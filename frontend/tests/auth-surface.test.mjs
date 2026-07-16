import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

test("signed-out home offers account links without asking for an API key", () => {
  const header = source("../app/components/auth-header.tsx");
  const home = source("../app/page.tsx");

  assert.match(header, /accountUrl\("login"\)/);
  assert.match(header, /accountUrl\("signup"\)/);
  assert.match(header, />\s*Sign in\s*</);
  assert.match(header, />\s*Create account\s*</);
  assert.doesNotMatch(header, /auth\/session\/api-key/);
  assert.match(home, /accountUrl\("login"\)/);
});

test("home explains how to connect the read-only ChatGPT app", () => {
  const home = source("../app/page.tsx");

  assert.match(home, /https:\/\/ledger\.ttsseed\.com\/mcp/);
  assert.match(home, /Security and login/);
  assert.match(home, /Developer mode/);
  assert.match(home, /Settings → Plugins/);
  assert.match(home, /https:\/\/chatgpt\.com\/plugins/);
  assert.match(home, /OAuth/);
  assert.match(home, /ledger:read/);
  assert.match(home, /read-only/);
  assert.match(home, /Never paste a setup key or API key into ChatGPT/);
});

test("ChatGPT step numbers use an accessible foreground color", () => {
  const styles = source("../app/globals.css");

  assert.match(
    styles,
    /\.chatgpt-step-number\s*{[^}]*color:\s*var\(--moss-deep\)/s
  );
});

test("account pages expose password signup and login contracts", () => {
  const form = source("../app/auth/auth-form.tsx");

  assert.match(form, /\/api\/v2\/auth\/signup/);
  assert.match(form, /\/api\/v2\/auth\/session\/password/);
  assert.match(form, /name="display_name"/);
  assert.match(form, /name="email"/);
  assert.match(form, /name="password"/);
  assert.match(form, /name="confirm_password"/);
  assert.match(form, /name="setup_key"/);
  assert.match(form, /Passwords do not match/);
  assert.match(form, /minLength=\{12\}/);
  assert.match(form, /maxLength=\{128\}/);
  assert.match(form, /role="alert"/);
});

test("API key login remains an explicitly secondary compatibility option", () => {
  const form = source("../app/auth/auth-form.tsx");

  assert.match(form, /<details[^>]*className="api-key-login"/);
  assert.match(form, /Use an API key instead/);
  assert.match(form, /\/api\/v2\/auth\/session\/api-key/);
});

test("OAuth and device prompts send signed-out users to normal sign in", () => {
  const callback = source("../app/auth/callback/cli-callback.tsx");
  const device = source("../app/auth/device/device-authorization.tsx");

  assert.match(callback, />\s*Sign in to continue\s*</);
  assert.match(device, />\s*Sign in to continue\s*</);
  assert.doesNotMatch(callback, /Sign in with an API key/);
  assert.doesNotMatch(device, /Sign in with an API key/);
});
