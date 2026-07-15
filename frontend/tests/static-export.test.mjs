import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";

import nextConfig from "../next.config.mjs";

test("the frontend is a static export with no server-side proxy routes", () => {
  assert.equal(nextConfig.output, "export");
  assert.equal(nextConfig.trailingSlash, true);

  for (const route of [
    "../app/.well-known/[...path]/route.ts",
    "../app/api/v2/[...path]/route.ts",
    "../app/mcp/route.ts"
  ]) {
    assert.equal(existsSync(new URL(route, import.meta.url)), false, route);
  }
});
