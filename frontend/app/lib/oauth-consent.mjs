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

export function defaultApprovedScopes(requestedScopes, availableScopes) {
  const available = new Set(normalizeScopeList(availableScopes));
  return normalizeScopeList(requestedScopes).filter(
    (scope) => scope.endsWith(":read") && available.has(scope)
  );
}

export function canApproveScope(requestedScopes, availableScopes, scope) {
  const requested = new Set(normalizeScopeList(requestedScopes));
  const available = new Set(normalizeScopeList(availableScopes));
  if (!requested.has(scope) || !available.has(scope)) return false;
  if (!scope.endsWith(":write")) return true;
  const matchingRead = matchingReadScope(scope);
  return requested.has(matchingRead) && available.has(matchingRead);
}

export function requiredApprovalScopes(resource) {
  let parsed;
  try {
    parsed = new URL(resource);
  } catch {
    return [];
  }
  return parsed.pathname.replace(/\/+$/, "") === "/mcp" ? ["ledger:read"] : [];
}

export function canBrowserSessionApproveOAuth(bookId) {
  return bookId === null;
}

export function updateApprovedScopes(
  requestedScopes,
  availableScopes,
  approvedScopes,
  scope,
  selected,
  resource = ""
) {
  const requested = normalizeScopeList(requestedScopes);
  const available = new Set(normalizeScopeList(availableScopes));
  const approved = new Set(normalizeScopeList(approvedScopes));
  if (selected) {
    if (!canApproveScope(requested, availableScopes, scope)) {
      throw new Error("This permission is not available to approve.");
    }
    approved.add(scope);
    if (scope.endsWith(":write")) approved.add(matchingReadScope(scope));
  } else {
    approved.delete(scope);
    if (scope.endsWith(":read")) {
      approved.delete(matchingWriteScope(scope));
    }
  }
  for (const requiredScope of requiredApprovalScopes(resource)) {
    if (requested.includes(requiredScope) && available.has(requiredScope)) {
      approved.add(requiredScope);
    }
  }
  return requested.filter((item) => approved.has(item));
}

export function scopeSelectionStatus(
  previousScopes,
  nextScopes,
  scope,
  selected
) {
  const previous = new Set(normalizeScopeList(previousScopes));
  const next = new Set(normalizeScopeList(nextScopes));
  const automaticallyAdded = [...next].filter(
    (item) => item !== scope && !previous.has(item)
  );
  const automaticallyRemoved = [...previous].filter(
    (item) => item !== scope && !next.has(item)
  );

  if (automaticallyAdded.length) {
    return `Selected ${scope}. ${automaticallyAdded.join(", ")} was also selected because write access requires matching read access.`;
  }
  if (automaticallyRemoved.length) {
    return `Cleared ${scope}. ${automaticallyRemoved.join(", ")} was also cleared because write access requires matching read access.`;
  }
  if (!selected && next.has(scope)) {
    return `${scope} is required for this connection and remains selected.`;
  }
  return `${scope} ${selected ? "selected" : "cleared"}.`;
}

export function canSubmitAuthorizationApproval(payload, approvedScopes, bookId) {
  if (!canBrowserSessionApproveOAuth(bookId)) return false;
  try {
    buildAuthorizationResponsePayload(payload, "approve", approvedScopes);
    return true;
  } catch {
    return false;
  }
}

export function buildAuthorizationResponsePayload(payload, action, approvedScopes) {
  if (action === "deny") return { ...payload, action };
  if (action !== "approve") {
    throw new Error("The authorization response action is invalid.");
  }

  const requested = normalizeScope(payload.scope).split(" ");
  const requestedSet = new Set(requested);
  const approvedSet = new Set(normalizeScopeList(approvedScopes));
  if (!approvedSet.size) {
    throw new Error("Select at least one permission before allowing access.");
  }
  if ([...approvedSet].some((scope) => !requestedSet.has(scope))) {
    throw new Error("Only permissions requested by this app can be approved.");
  }
  for (const scope of approvedSet) {
    if (scope.endsWith(":write") && !approvedSet.has(matchingReadScope(scope))) {
      throw new Error("Write access requires its matching read permission.");
    }
  }
  for (const requiredScope of requiredApprovalScopes(payload.resource)) {
    if (!approvedSet.has(requiredScope)) {
      throw new Error(`${requiredScope} is required for this MCP connection.`);
    }
  }

  return {
    ...payload,
    action,
    approved_scopes: requested.filter((scope) => approvedSet.has(scope))
  };
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

function normalizeScopeList(values) {
  if (!Array.isArray(values)) return [];
  return [...new Set(values.map((value) => String(value).trim()).filter(Boolean))];
}

function matchingReadScope(scope) {
  return scope.replace(/:write$/, ":read");
}

function matchingWriteScope(scope) {
  return scope.replace(/:read$/, ":write");
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
