const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "proxy-connection",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade"
]);

export function copyProxyRequestHeaders(sourceHeaders) {
  return copyEndToEndHeaders(sourceHeaders, {
    drop: new Set(["content-length", "host"])
  });
}

export function copyProxyResponseHeaders(sourceHeaders) {
  return copyEndToEndHeaders(sourceHeaders, {
    // fetch transparently decodes upstream bodies. Forwarding the original
    // representation headers would make the browser decode an already decoded
    // stream or wait for a stale byte count.
    drop: new Set(["content-encoding", "content-length", "set-cookie"])
  });
}

function copyEndToEndHeaders(sourceHeaders, { drop }) {
  const source = new Headers(sourceHeaders);
  const connectionHeaders = new Set(
    (source.get("connection") ?? "")
      .split(",")
      .map((name) => name.trim().toLowerCase())
      .filter(Boolean)
  );
  const copied = new Headers();

  source.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (
      HOP_BY_HOP.has(lower) ||
      connectionHeaders.has(lower) ||
      drop.has(lower)
    ) {
      return;
    }
    copied.append(key, value);
  });
  return copied;
}
