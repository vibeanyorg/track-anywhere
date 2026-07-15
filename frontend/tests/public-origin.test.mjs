import assert from "node:assert/strict";
import test from "node:test";

import { oauthRedirectUri } from "../app/lib/public-origin.mjs";

test("OAuth callback follows the browser origin instead of a fixed dev port", () => {
  assert.equal(
    oauthRedirectUri("https://ledger.example.com/"),
    "https://ledger.example.com/auth/callback"
  );
  assert.equal(
    oauthRedirectUri("http://127.0.0.1:13000"),
    "http://127.0.0.1:13000/auth/callback"
  );
});
