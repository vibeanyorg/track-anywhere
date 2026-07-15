export const DEFAULT_OAUTH_SCOPE = "book:read ledger:read";

export function parseAuthorizationRequest(searchParams) {
  const responseType = searchParams.get("response_type") || "code";
  const clientId = required(searchParams, "client_id");
  const redirectUri = validateRedirectUri(required(searchParams, "redirect_uri"));
  const resource = validateResourceUri(required(searchParams, "resource"));
  const codeChallenge = required(searchParams, "code_challenge");
  const codeChallengeMethod = searchParams.get("code_challenge_method") || "S256";
  const scope = normalizeScope(searchParams.get("scope") || DEFAULT_OAUTH_SCOPE);
  const state = searchParams.get("state") || undefined;

  if (responseType !== "code") {
    throw new Error("This app requested an unsupported OAuth response type.");
  }
  if (codeChallengeMethod !== "S256") {
    throw new Error("This app must use PKCE with the S256 method.");
  }
  if (!/^[A-Za-z0-9._~-]{43,128}$/.test(codeChallenge)) {
    throw new Error("This app sent an invalid PKCE challenge.");
  }

  return {
    payload: {
      response_type: responseType,
      client_id: clientId,
      redirect_uri: redirectUri,
      resource,
      scope,
      ...(state ? { state } : {}),
      code_challenge: codeChallenge,
      code_challenge_method: codeChallengeMethod
    },
    clientId,
    resource,
    redirectHost: new URL(redirectUri).host,
    scopes: scope.split(" ")
  };
}

export function validateAuthorizationRedirect(candidate, requestedRedirectUri) {
  const expected = new URL(validateRedirectUri(requestedRedirectUri));
  const target = new URL(validateRedirectUri(candidate));

  if (target.origin !== expected.origin || target.pathname !== expected.pathname) {
    throw new Error("The authorization server returned an unexpected redirect.");
  }
  for (const [key, value] of expected.searchParams.entries()) {
    if (!target.searchParams.getAll(key).includes(value)) {
      throw new Error("The authorization server changed the registered redirect.");
    }
  }
  if (!target.searchParams.has("code") && !target.searchParams.has("error")) {
    throw new Error("The authorization server returned an incomplete redirect.");
  }
  return target.toString();
}

export function normalizeDeviceCode(value) {
  return value.trim().toUpperCase().replace(/\s+/g, "");
}

function required(searchParams, name) {
  const value = searchParams.get(name)?.trim();
  if (!value) {
    throw new Error("This authorization link is incomplete. Start again from the app.");
  }
  return value;
}

function normalizeScope(value) {
  const scopes = [...new Set(value.trim().split(/\s+/).filter(Boolean))];
  if (!scopes.length) {
    throw new Error("This app did not request any OAuth scopes.");
  }
  return scopes.join(" ");
}

function validateResourceUri(value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("This app requested an invalid resource.");
  }
  if (parsed.protocol !== "https:" && !isLoopbackHttp(parsed)) {
    throw new Error("This app requested an insecure resource.");
  }
  if (parsed.username || parsed.password || parsed.hash) {
    throw new Error("This app requested an invalid resource.");
  }
  return parsed.toString().replace(/\/$/, "");
}

function validateRedirectUri(value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("This app requested an invalid redirect.");
  }
  if (parsed.protocol !== "https:" && !isLoopbackHttp(parsed)) {
    throw new Error("This app requested an insecure redirect.");
  }
  if (parsed.username || parsed.password || parsed.hash) {
    throw new Error("This app requested an invalid redirect.");
  }
  return parsed.toString();
}

function isLoopbackHttp(url) {
  return (
    url.protocol === "http:" &&
    ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
  );
}
