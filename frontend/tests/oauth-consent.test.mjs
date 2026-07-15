import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeDeviceCode,
  parseAuthorizationRequest,
  validateAuthorizationRedirect
} from "../app/lib/oauth-consent.mjs";

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
