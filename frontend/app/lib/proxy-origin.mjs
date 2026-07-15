export function resolvePublicOrigin(
  sourceHeaders,
  fallbackOrigin,
  configuredOrigin
) {
  if (configuredOrigin !== undefined) {
    const rawConfiguredOrigin = configuredOrigin.trim();
    const configured = safeUrl(rawConfiguredOrigin);
    if (
      !configured ||
      !["http:", "https:"].includes(configured.protocol) ||
      configured.username ||
      configured.password ||
      configured.pathname !== "/" ||
      configured.search ||
      configured.hash
    ) {
      throw new Error("TRACK_ANYWHERE_PUBLIC_BASE_URL must be an HTTP(S) origin");
    }
    return configured.origin;
  }
  const headers = new Headers(sourceHeaders);
  const fallback = new URL(fallbackOrigin);
  const forwardedHost = firstHeaderValue(headers.get("x-forwarded-host"));
  const host = forwardedHost ?? firstHeaderValue(headers.get("host"));
  const forwardedProto = firstHeaderValue(headers.get("x-forwarded-proto"));
  const protocol = forwardedProto ?? fallback.protocol.replace(":", "");

  if (!host || (protocol !== "http" && protocol !== "https")) {
    return fallback.origin;
  }
  try {
    const resolved = new URL(`${protocol}://${host}`);
    if (
      resolved.username ||
      resolved.password ||
      resolved.pathname !== "/" ||
      resolved.search ||
      resolved.hash
    ) {
      return fallback.origin;
    }
    return resolved.origin;
  } catch {
    return fallback.origin;
  }
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
  return rewriteJsonUrls(body, (value) =>
    rewriteApiUrl(value, publicOrigin, upstreamOrigin)
  );
}

export function rewriteAllUpstreamJsonUrls(
  body,
  publicOrigin,
  upstreamOrigin
) {
  return rewriteJsonUrls(body, (value) =>
    rewriteUpstreamUrl(value, publicOrigin, upstreamOrigin)
  );
}

function rewriteJsonUrls(body, rewriteUrl) {
  let parsed;
  try {
    parsed = JSON.parse(body);
  } catch {
    return body;
  }
  const rewritten = rewriteValue(parsed, rewriteUrl);
  return rewritten.changed ? JSON.stringify(rewritten.value) : body;
}

function rewriteValue(value, rewriteUrl) {
  if (typeof value === "string") {
    const rewritten = rewriteUrl(value);
    return { value: rewritten, changed: rewritten !== value };
  }
  if (Array.isArray(value)) {
    let changed = false;
    const items = value.map((item) => {
      const rewritten = rewriteValue(item, rewriteUrl);
      changed ||= rewritten.changed;
      return rewritten.value;
    });
    return { value: items, changed };
  }
  if (value !== null && typeof value === "object") {
    let changed = false;
    const entries = Object.entries(value).map(([key, item]) => {
      const rewritten = rewriteValue(item, rewriteUrl);
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

function rewriteUpstreamUrl(value, publicOrigin, upstreamOrigin) {
  const parsed = safeUrl(value);
  if (!parsed || parsed.origin !== new URL(upstreamOrigin).origin) return value;
  const normalizedPublicOrigin = new URL(publicOrigin).origin;
  return `${normalizedPublicOrigin}${parsed.pathname}${parsed.search}${parsed.hash}`;
}

function safeUrl(value) {
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

function firstHeaderValue(value) {
  const first = value?.split(",", 1)[0]?.trim();
  return first || null;
}
