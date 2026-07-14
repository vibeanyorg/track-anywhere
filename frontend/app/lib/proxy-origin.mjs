export function normalizeSameSiteRequestHeaders(
  sourceHeaders,
  publicOrigin,
  upstreamOrigin
) {
  const headers = new Headers(sourceHeaders);
  const normalizedPublicOrigin = new URL(publicOrigin).origin;
  const normalizedUpstreamOrigin = new URL(upstreamOrigin).origin;
  const origin = headers.get("origin");
  if (origin && safeOrigin(origin) === normalizedPublicOrigin) {
    headers.set("origin", normalizedUpstreamOrigin);
  }

  const referer = headers.get("referer");
  if (referer) {
    const parsed = safeUrl(referer);
    if (parsed?.origin === normalizedPublicOrigin) {
      headers.set(
        "referer",
        `${normalizedUpstreamOrigin}${parsed.pathname}${parsed.search}${parsed.hash}`
      );
    }
  }
  return headers;
}

export function rewriteUpstreamLocation(
  sourceHeaders,
  publicOrigin,
  upstreamOrigin
) {
  const headers = new Headers(sourceHeaders);
  const location = headers.get("location");
  if (!location) return headers;
  const rewritten = rewriteApiUrl(location, publicOrigin, upstreamOrigin);
  if (rewritten !== location) headers.set("location", rewritten);
  return headers;
}

export function rewriteUpstreamJsonUrls(
  body,
  publicOrigin,
  upstreamOrigin
) {
  let parsed;
  try {
    parsed = JSON.parse(body);
  } catch {
    return body;
  }
  const rewritten = rewriteValue(parsed, publicOrigin, upstreamOrigin);
  return rewritten.changed ? JSON.stringify(rewritten.value) : body;
}

function rewriteValue(value, publicOrigin, upstreamOrigin) {
  if (typeof value === "string") {
    const rewritten = rewriteApiUrl(value, publicOrigin, upstreamOrigin);
    return { value: rewritten, changed: rewritten !== value };
  }
  if (Array.isArray(value)) {
    let changed = false;
    const items = value.map((item) => {
      const rewritten = rewriteValue(item, publicOrigin, upstreamOrigin);
      changed ||= rewritten.changed;
      return rewritten.value;
    });
    return { value: items, changed };
  }
  if (value !== null && typeof value === "object") {
    let changed = false;
    const entries = Object.entries(value).map(([key, item]) => {
      const rewritten = rewriteValue(item, publicOrigin, upstreamOrigin);
      changed ||= rewritten.changed;
      return [key, rewritten.value];
    });
    return { value: Object.fromEntries(entries), changed };
  }
  return { value, changed: false };
}

function rewriteApiUrl(value, publicOrigin, upstreamOrigin) {
  const parsed = safeUrl(value);
  if (!parsed) return value;
  const normalizedUpstreamOrigin = new URL(upstreamOrigin).origin;
  if (
    parsed.origin !== normalizedUpstreamOrigin ||
    (parsed.pathname !== "/api/v2" && !parsed.pathname.startsWith("/api/v2/"))
  ) {
    return value;
  }
  const normalizedPublicOrigin = new URL(publicOrigin).origin;
  return `${normalizedPublicOrigin}${parsed.pathname}${parsed.search}${parsed.hash}`;
}

function safeOrigin(value) {
  return safeUrl(value)?.origin ?? null;
}

function safeUrl(value) {
  try {
    return new URL(value);
  } catch {
    return null;
  }
}
