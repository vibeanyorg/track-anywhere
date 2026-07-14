import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeSameSiteRequestHeaders,
  rewriteUpstreamJsonUrls,
  rewriteUpstreamLocation
} from "../app/lib/proxy-origin.mjs";

const publicOrigin = "https://ledger.example.com";
const upstreamOrigin = "http://api:8000";

test("normalizes only same-site Origin and Referer for the trusted upstream", () => {
  const normalized = normalizeSameSiteRequestHeaders(
    {
      origin: publicOrigin,
      referer: `${publicOrigin}/auth/callback?code=one`,
      authorization: "Bearer token"
    },
    publicOrigin,
    upstreamOrigin
  );

  assert.equal(normalized.get("origin"), upstreamOrigin);
  assert.equal(
    normalized.get("referer"),
    `${upstreamOrigin}/auth/callback?code=one`
  );
  assert.equal(normalized.get("authorization"), "Bearer token");
});

test("does not normalize a foreign request origin", () => {
  const normalized = normalizeSameSiteRequestHeaders(
    {
      origin: "https://attacker.example",
      referer: "https://attacker.example/submit"
    },
    publicOrigin,
    upstreamOrigin
  );

  assert.equal(normalized.get("origin"), "https://attacker.example");
  assert.equal(normalized.get("referer"), "https://attacker.example/submit");
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
