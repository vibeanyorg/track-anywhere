import { NextRequest, NextResponse } from "next/server";
import {
  normalizeSameSiteRequestHeaders,
  rewriteUpstreamJsonUrls,
  rewriteUpstreamLocation
} from "../../../lib/proxy-origin.mjs";

const HOP_BY_HOP = new Set([
  "connection",
  "content-encoding",
  "content-length",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade"
]);

export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}
export async function POST(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}
export async function PUT(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}
export async function PATCH(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}
export async function DELETE(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}
export async function HEAD(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

async function proxy(request: NextRequest, context: RouteContext) {
  const backendUrl = process.env.TRACK_ANYWHERE_BACKEND_URL ?? "http://127.0.0.1:8000";
  const { path } = await context.params;
  const target = new URL(`/api/v2/${path.map(encodeURIComponent).join("/")}`, backendUrl);
  target.search = request.nextUrl.search;

  const forwardedHeaders = new Headers();
  request.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (HOP_BY_HOP.has(lower) || lower === "host") return;
    forwardedHeaders.set(key, value);
  });
  const headers = normalizeSameSiteRequestHeaders(
    forwardedHeaders,
    request.nextUrl.origin,
    target.origin
  );
  headers.set("x-forwarded-host", request.headers.get("host") ?? "");
  headers.set("x-forwarded-proto", request.nextUrl.protocol.replace(":", ""));

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
      redirect: "manual"
    });
  } catch {
    return NextResponse.json({ detail: "Can't reach the server." }, { status: 502 });
  }

  let responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (HOP_BY_HOP.has(lower) || lower === "set-cookie") return;
    responseHeaders.set(key, value);
  });
  responseHeaders = rewriteUpstreamLocation(
    responseHeaders,
    request.nextUrl.origin,
    target.origin
  );

  let body: ArrayBuffer | string | null = null;
  if (upstream.status !== 204 && upstream.status !== 304) {
    const rawBody = await upstream.arrayBuffer();
    const contentType = upstream.headers.get("content-type") ?? "";
    body = contentType.toLowerCase().includes("application/json")
      ? rewriteUpstreamJsonUrls(
          new TextDecoder().decode(rawBody),
          request.nextUrl.origin,
          target.origin
        )
      : rawBody;
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

function readSetCookies(headers: Headers): string[] {
  const withGetter = headers as Headers & { getSetCookie?: () => string[] };
  if (typeof withGetter.getSetCookie === "function") {
    return withGetter.getSetCookie();
  }
  const single = headers.get("set-cookie");
  return single ? [single] : [];
}
