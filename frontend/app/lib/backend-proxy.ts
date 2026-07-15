import { NextRequest, NextResponse } from "next/server";
import { copyProxyRequestHeaders, copyProxyResponseHeaders } from "./proxy-headers.mjs";
import {
  resolvePublicOrigin,
  rewriteAllUpstreamJsonUrls,
  rewriteUpstreamJsonUrls,
  rewriteUpstreamLocation
} from "./proxy-origin.mjs";

type ProxyOptions = {
  rewriteJsonUrls?: "public-api" | "all-upstream";
  streamResponse?: boolean;
};

type NodeRequestInit = RequestInit & { duplex?: "half" };

export async function proxyBackend(
  request: NextRequest,
  pathname: string,
  options: ProxyOptions = {}
) {
  const backendUrl = process.env.TRACK_ANYWHERE_BACKEND_URL ?? "http://127.0.0.1:8000";
  const target = new URL(pathname, backendUrl);
  target.search = request.nextUrl.search;
  const publicOrigin = resolvePublicOrigin(
    request.headers,
    request.nextUrl.origin,
    process.env.TRACK_ANYWHERE_PUBLIC_BASE_URL
  );

  const headers = copyProxyRequestHeaders(request.headers);
  const publicUrl = new URL(publicOrigin);
  headers.set("x-forwarded-host", publicUrl.host);
  headers.set("x-forwarded-proto", publicUrl.protocol.replace(":", ""));

  const hasRequestBody = request.method !== "GET" && request.method !== "HEAD";
  const init: NodeRequestInit = {
    method: request.method,
    headers,
    body: hasRequestBody ? request.body : undefined,
    redirect: "manual",
    signal: request.signal
  };
  if (hasRequestBody && request.body !== null) init.duplex = "half";

  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch {
    return NextResponse.json({ detail: "Can't reach the server." }, { status: 502 });
  }

  let responseHeaders = copyProxyResponseHeaders(upstream.headers);
  responseHeaders = rewriteUpstreamLocation(responseHeaders, publicOrigin, target.origin);

  const hasResponseBody =
    request.method !== "HEAD" && upstream.status !== 204 && upstream.status !== 304;
  let body: BodyInit | null = null;
  if (hasResponseBody && options.rewriteJsonUrls) {
    const contentType = upstream.headers.get("content-type") ?? "";
    if (contentType.toLowerCase().includes("application/json")) {
      const rawBody = await upstream.text();
      body = options.rewriteJsonUrls === "all-upstream"
        ? rewriteAllUpstreamJsonUrls(rawBody, publicOrigin, target.origin)
        : rewriteUpstreamJsonUrls(rawBody, publicOrigin, target.origin);
    } else {
      body = options.streamResponse === false
        ? await upstream.arrayBuffer()
        : upstream.body;
    }
  } else if (hasResponseBody) {
    body = options.streamResponse === false
      ? await upstream.arrayBuffer()
      : upstream.body;
  }

  const response = new NextResponse(body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders
  });
  for (const cookie of readSetCookies(upstream.headers)) {
    response.headers.append("set-cookie", cookie);
  }
  return response;
}

export function encodeProxyPath(prefix: string, path: string[]) {
  if (path.some((segment) => segment === "." || segment === "..")) {
    throw new Error("Unsafe proxy path segment");
  }
  return `${prefix}/${path.map(encodeURIComponent).join("/")}`;
}

function readSetCookies(headers: Headers): string[] {
  const withGetter = headers as Headers & { getSetCookie?: () => string[] };
  if (typeof withGetter.getSetCookie === "function") {
    return withGetter.getSetCookie();
  }
  const single = headers.get("set-cookie");
  return single ? [single] : [];
}
