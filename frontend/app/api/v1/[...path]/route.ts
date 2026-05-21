import { NextRequest, NextResponse } from "next/server";

const backendUrl = process.env.TRACK_ANYWHERE_BACKEND_URL ?? "http://127.0.0.1:8000";
const hopByHopHeaders = new Set([
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

type RouteContext = {
  params: Promise<{ path: string[] }> | { path: string[] };
};

async function proxy(request: NextRequest, context: RouteContext) {
  const params = await Promise.resolve(context.params);
  const target = new URL(`/api/v1/${params.path.map(encodeURIComponent).join("/")}`, backendUrl);
  target.search = request.nextUrl.search;

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers: requestHeaders(request),
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
      redirect: "manual"
    });
  } catch {
    return NextResponse.json({ detail: "Backend is not reachable" }, { status: 502 });
  }

  const response = new NextResponse(await responseBody(upstream), {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders(upstream)
  });
  for (const cookie of setCookieHeaders(upstream.headers)) {
    response.headers.append("set-cookie", cookie);
  }
  return response;
}

function requestHeaders(request: NextRequest) {
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    const lowerKey = key.toLowerCase();
    if (hopByHopHeaders.has(lowerKey) || lowerKey === "host") return;
    headers.set(key, value);
  });
  headers.set("x-forwarded-host", request.headers.get("host") ?? "");
  headers.set("x-forwarded-proto", request.nextUrl.protocol.replace(":", ""));
  return headers;
}

function responseHeaders(upstream: Response) {
  const headers = new Headers();
  upstream.headers.forEach((value, key) => {
    const lowerKey = key.toLowerCase();
    if (hopByHopHeaders.has(lowerKey) || lowerKey === "set-cookie") return;
    headers.set(key, value);
  });
  return headers;
}

async function responseBody(upstream: Response) {
  if (upstream.status === 204 || upstream.status === 304) return null;
  return await upstream.arrayBuffer();
}

function setCookieHeaders(headers: Headers) {
  const headersWithCookies = headers as Headers & { getSetCookie?: () => string[] };
  if (headersWithCookies.getSetCookie) return headersWithCookies.getSetCookie();
  const cookie = headers.get("set-cookie");
  return cookie ? [cookie] : [];
}
