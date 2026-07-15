import assert from "node:assert/strict";
import test from "node:test";

import {
  copyProxyRequestHeaders,
  copyProxyResponseHeaders
} from "../app/lib/proxy-headers.mjs";

test("drops request hop-by-hop headers, including Connection extensions", () => {
  const copied = copyProxyRequestHeaders({
    authorization: "Bearer oauth-token",
    connection: "keep-alive, x-private-hop",
    "content-length": "99",
    host: "ledger.example.com",
    "mcp-protocol-version": "2025-11-25",
    origin: "https://ledger.example.com",
    "proxy-connection": "keep-alive",
    referer: "https://ledger.example.com/auth/callback?code=one",
    "x-private-hop": "secret"
  });

  assert.equal(copied.get("authorization"), "Bearer oauth-token");
  assert.equal(copied.get("mcp-protocol-version"), "2025-11-25");
  assert.equal(copied.get("origin"), "https://ledger.example.com");
  assert.equal(
    copied.get("referer"),
    "https://ledger.example.com/auth/callback?code=one"
  );
  assert.equal(copied.get("connection"), null);
  assert.equal(copied.get("content-length"), null);
  assert.equal(copied.get("host"), null);
  assert.equal(copied.get("proxy-connection"), null);
  assert.equal(copied.get("x-private-hop"), null);
});

test("preserves MCP and OAuth response headers while dropping transport metadata", () => {
  const copied = copyProxyResponseHeaders({
    connection: "x-private-hop",
    "content-encoding": "gzip",
    "content-length": "123",
    "mcp-session-id": "session-123",
    "set-cookie": "session=private",
    "www-authenticate": 'Bearer resource_metadata="https://ledger.example.com/.well-known/oauth-protected-resource/mcp"',
    "x-private-hop": "secret"
  });

  assert.equal(copied.get("mcp-session-id"), "session-123");
  assert.match(copied.get("www-authenticate") ?? "", /resource_metadata=/);
  assert.equal(copied.get("connection"), null);
  assert.equal(copied.get("content-encoding"), null);
  assert.equal(copied.get("content-length"), null);
  assert.equal(copied.get("set-cookie"), null);
  assert.equal(copied.get("x-private-hop"), null);
});
