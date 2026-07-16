import assert from "node:assert/strict";
import test from "node:test";

import * as oauthConsent from "../app/lib/oauth-consent.mjs";

const {
  normalizeDeviceCode,
  parseAuthorizationRequest,
  validateAuthorizationRedirect
} = oauthConsent;

const challenge = "A".repeat(43);

function authorizationParams(overrides = {}) {
  return new URLSearchParams({
    response_type: "code",
    client_id: "chatgpt-client",
    redirect_uri: "https://chatgpt.com/connector/oauth/callback",
    resource: "https://ledger.example.com/mcp",
    scope: "ledger:read book:read ledger:read",
    state: "state-one",
    code_challenge: challenge,
    code_challenge_method: "S256",
    ...overrides
  });
}

test("parses and preserves the OAuth response type and resource", () => {
  const request = parseAuthorizationRequest(authorizationParams());

  assert.deepEqual(request.payload, {
    response_type: "code",
    client_id: "chatgpt-client",
    redirect_uri: "https://chatgpt.com/connector/oauth/callback",
    resource: "https://ledger.example.com/mcp",
    scope: "ledger:read book:read",
    state: "state-one",
    code_challenge: challenge,
    code_challenge_method: "S256"
  });
  assert.equal(request.redirectHost, "chatgpt.com");
  assert.deepEqual(request.scopes, ["ledger:read", "book:read"]);
});

test("accepts loopback callbacks for native CLI clients", () => {
  const request = parseAuthorizationRequest(
    authorizationParams({
      redirect_uri: "http://127.0.0.1:49152/callback",
      resource: "http://127.0.0.1:13000/api/v2",
      state: ""
    })
  );

  assert.equal(request.redirectHost, "127.0.0.1:49152");
  assert.equal(request.payload.state, undefined);
});

test("rejects incomplete, insecure, and non-S256 authorization requests", () => {
  assert.throws(
    () => parseAuthorizationRequest(authorizationParams({ resource: "" })),
    /incomplete/
  );
  assert.throws(
    () =>
      parseAuthorizationRequest(
        authorizationParams({ redirect_uri: "http://attacker.example/callback" })
      ),
    /insecure redirect/
  );
  assert.throws(
    () =>
      parseAuthorizationRequest(
        authorizationParams({ code_challenge_method: "plain" })
      ),
    /S256/
  );
});

test("allows only the requested callback target returned by the server", () => {
  const requested = "https://chatgpt.com/connector/oauth/callback?tenant=one";
  const target = validateAuthorizationRedirect(
    `${requested}&code=issued&state=state-one`,
    requested
  );

  assert.equal(
    target,
    "https://chatgpt.com/connector/oauth/callback?tenant=one&code=issued&state=state-one"
  );
  assert.throws(
    () =>
      validateAuthorizationRedirect(
        "https://attacker.example/connector/oauth/callback?code=issued",
        requested
      ),
    /unexpected redirect/
  );
  assert.throws(
    () =>
      validateAuthorizationRedirect(
        "https://chatgpt.com/connector/oauth/callback?code=issued",
        requested
      ),
    /changed the registered redirect/
  );
});

test("accepts access-denied redirects and normalizes device codes", () => {
  assert.equal(
    validateAuthorizationRedirect(
      "http://localhost:41234/callback?error=access_denied&state=one",
      "http://localhost:41234/callback"
    ),
    "http://localhost:41234/callback?error=access_denied&state=one"
  );
  assert.equal(normalizeDeviceCode(" abcd - efgh "), "ABCD-EFGH");
});

test("defaults consent to available read scopes while keeping writes opt-in", () => {
  const requested = ["book:read", "book:write", "ledger:read", "ledger:write"];
  const available = ["book:read", "ledger:read", "ledger:write"];

  assert.deepEqual(oauthConsent.defaultApprovedScopes(requested, available), [
    "book:read",
    "ledger:read"
  ]);
  assert.equal(oauthConsent.canApproveScope(requested, available, "book:write"), false);
  assert.equal(oauthConsent.canApproveScope(requested, available, "ledger:write"), true);
});

test("write selection retains its matching read permission", () => {
  const requested = ["ledger:read", "ledger:write"];
  const available = ["ledger:read", "ledger:write"];

  const withWrite = oauthConsent.updateApprovedScopes(
    requested,
    available,
    ["ledger:read"],
    "ledger:write",
    true
  );
  assert.deepEqual(withWrite, ["ledger:read", "ledger:write"]);

  assert.deepEqual(
    oauthConsent.updateApprovedScopes(
      requested,
      available,
      withWrite,
      "ledger:read",
      false
    ),
    []
  );
});

test("MCP consent keeps ledger read selected and rejects grants without it", () => {
  const request = parseAuthorizationRequest(
    authorizationParams({ scope: "book:read ledger:read ledger:write" })
  );
  const requested = request.scopes;
  const available = [...requested];

  assert.deepEqual(
    oauthConsent.requiredApprovalScopes(request.resource),
    ["ledger:read"]
  );
  assert.deepEqual(
    oauthConsent.updateApprovedScopes(
      requested,
      available,
      requested,
      "ledger:read",
      false,
      request.resource
    ),
    ["book:read", "ledger:read"]
  );
  assert.throws(
    () =>
      oauthConsent.buildAuthorizationResponsePayload(
        request.payload,
        "approve",
        ["book:read"]
      ),
    /ledger:read.*required/i
  );
  assert.equal(
    oauthConsent.canSubmitAuthorizationApproval(
      request.payload,
      ["book:read"],
      null
    ),
    false
  );
});

test("API consent can grant a flexible nonempty subset", () => {
  const request = parseAuthorizationRequest(
    authorizationParams({
      resource: "https://ledger.example.com/api/v2",
      scope: "book:read ledger:read"
    })
  );
  const selected = oauthConsent.updateApprovedScopes(
    request.scopes,
    request.scopes,
    request.scopes,
    "ledger:read",
    false,
    request.resource
  );

  assert.deepEqual(oauthConsent.requiredApprovalScopes(request.resource), []);
  assert.deepEqual(selected, ["book:read"]);
  assert.deepEqual(
    oauthConsent.buildAuthorizationResponsePayload(
      request.payload,
      "approve",
      selected
    ).approved_scopes,
    ["book:read"]
  );
  assert.equal(
    oauthConsent.canSubmitAuthorizationApproval(request.payload, selected, null),
    true
  );
});

test("book-bound browser sessions cannot approve OAuth but owner sessions can", () => {
  const request = parseAuthorizationRequest(
    authorizationParams({ scope: "ledger:read ledger:write" })
  );

  assert.equal(oauthConsent.canBrowserSessionApproveOAuth(null), true);
  assert.equal(oauthConsent.canBrowserSessionApproveOAuth("book-one"), false);
  assert.equal(oauthConsent.canBrowserSessionApproveOAuth(undefined), false);
  assert.equal(
    oauthConsent.canSubmitAuthorizationApproval(
      request.payload,
      ["ledger:read"],
      "book-one"
    ),
    false
  );
});

test("scope selection status announces automatic read and write dependency changes", () => {
  const requested = ["ledger:read", "ledger:write"];
  const withWrite = oauthConsent.updateApprovedScopes(
    requested,
    requested,
    [],
    "ledger:write",
    true,
    "https://ledger.example.com/api/v2"
  );

  assert.deepEqual(withWrite, ["ledger:read", "ledger:write"]);
  assert.match(
    oauthConsent.scopeSelectionStatus([], withWrite, "ledger:write", true),
    /ledger:read.*also selected/i
  );

  const withoutRead = oauthConsent.updateApprovedScopes(
    requested,
    requested,
    withWrite,
    "ledger:read",
    false,
    "https://ledger.example.com/api/v2"
  );
  assert.deepEqual(withoutRead, []);
  assert.match(
    oauthConsent.scopeSelectionStatus(
      withWrite,
      withoutRead,
      "ledger:read",
      false
    ),
    /ledger:write.*also cleared/i
  );
});

test("approval payload preserves requested scope and sends only approved scopes", () => {
  const request = parseAuthorizationRequest(
    authorizationParams({ scope: "ledger:read ledger:write" })
  );

  assert.deepEqual(
    oauthConsent.buildAuthorizationResponsePayload(
      request.payload,
      "approve",
      ["ledger:read", "ledger:write", "ledger:read"]
    ),
    {
      ...request.payload,
      action: "approve",
      approved_scopes: ["ledger:read", "ledger:write"]
    }
  );
  assert.equal(request.payload.scope, "ledger:read ledger:write");
});

test("approval payload rejects empty, unrequested, and write-only selections", () => {
  const request = parseAuthorizationRequest(
    authorizationParams({ scope: "ledger:read ledger:write" })
  );

  assert.throws(
    () => oauthConsent.buildAuthorizationResponsePayload(request.payload, "approve", []),
    /at least one permission/i
  );
  assert.throws(
    () =>
      oauthConsent.buildAuthorizationResponsePayload(request.payload, "approve", [
        "book:read"
      ]),
    /requested/i
  );
  assert.throws(
    () =>
      oauthConsent.buildAuthorizationResponsePayload(request.payload, "approve", [
        "ledger:write"
      ]),
    /matching read/i
  );
  assert.deepEqual(
    oauthConsent.buildAuthorizationResponsePayload(request.payload, "deny", []),
    { ...request.payload, action: "deny" }
  );
});
