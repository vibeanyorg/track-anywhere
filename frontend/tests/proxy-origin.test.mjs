import assert from "node:assert/strict";
import test from "node:test";

import {
  resolvePublicOrigin,
  rewriteAllUpstreamJsonUrls,
  rewriteUpstreamJsonUrls,
  rewriteUpstreamLocation
} from "../app/lib/proxy-origin.mjs";

const publicOrigin = "https://ledger.example.com";
const upstreamOrigin = "http://api:8000";

test("resolves the browser-facing origin from proxy request headers", () => {
  assert.equal(
    resolvePublicOrigin(
      { host: "127.0.0.1:13000" },
      "http://localhost:3000"
    ),
    "http://127.0.0.1:13000"
  );
  assert.equal(
    resolvePublicOrigin(
      {
        host: "next:3000",
        "x-forwarded-host": "ledger.example.com",
        "x-forwarded-proto": "https"
      },
      "http://next:3000"
    ),
    "https://ledger.example.com"
  );
});

test("configured public origin wins over untrusted forwarded headers", () => {
  assert.equal(
    resolvePublicOrigin(
      {
        host: "next:3000",
        "x-forwarded-host": "attacker.example",
        "x-forwarded-proto": "https"
      },
      "http://next:3000",
      "https://ledger.example.com"
    ),
    "https://ledger.example.com"
  );
  assert.throws(
    () => resolvePublicOrigin({}, "http://next:3000", "https://ledger.example.com/path"),
    /must be an HTTP\(S\) origin/
  );
});

test("rewrites only absolute upstream V2 URLs in JSON responses", () => {
  const body = JSON.stringify({
    verification_uri: `${upstreamOrigin}/api/v2/auth/device`,
    verification_uri_complete: `${upstreamOrigin}/api/v2/auth/device?user_code=ABCD`,
    redirect_uri: "http://127.0.0.1:49152/callback?code=one",
    internal_admin: `${upstreamOrigin}/admin`
  });

  assert.deepEqual(
    JSON.parse(rewriteUpstreamJsonUrls(body, publicOrigin, upstreamOrigin)),
    {
      verification_uri: `${publicOrigin}/api/v2/auth/device`,
      verification_uri_complete: `${publicOrigin}/api/v2/auth/device?user_code=ABCD`,
      redirect_uri: "http://127.0.0.1:49152/callback?code=one",
      internal_admin: `${upstreamOrigin}/admin`
    }
  );
});

test("rewrites every internal URL in public OAuth discovery metadata", () => {
  const body = JSON.stringify({
    issuer: `${upstreamOrigin}/`,
    authorization_endpoint: `${upstreamOrigin}/api/v2/oauth/authorize`,
    resource: `${upstreamOrigin}/mcp`,
    external_documentation: "https://modelcontextprotocol.io/specification"
  });

  assert.deepEqual(
    JSON.parse(rewriteAllUpstreamJsonUrls(body, publicOrigin, upstreamOrigin)),
    {
      issuer: `${publicOrigin}/`,
      authorization_endpoint: `${publicOrigin}/api/v2/oauth/authorize`,
      resource: `${publicOrigin}/mcp`,
      external_documentation: "https://modelcontextprotocol.io/specification"
    }
  );
});

test("rewrites an upstream V2 Location without changing external redirects", () => {
  const internal = rewriteUpstreamLocation(
    { location: `${upstreamOrigin}/api/v2/auth/device?user_code=ABCD` },
    publicOrigin,
    upstreamOrigin
  );
  const external = rewriteUpstreamLocation(
    { location: "http://127.0.0.1:49152/callback?code=one" },
    publicOrigin,
    upstreamOrigin
  );

  assert.equal(
    internal.get("location"),
    `${publicOrigin}/api/v2/auth/device?user_code=ABCD`
  );
  assert.equal(
    external.get("location"),
    "http://127.0.0.1:49152/callback?code=one"
  );
});
