import { NextRequest } from "next/server";
import { encodeProxyPath, proxyBackend } from "../../lib/backend-proxy";

export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export async function HEAD(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export async function OPTIONS(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

async function proxy(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  let pathname: string;
  try {
    pathname = encodeProxyPath("/.well-known", path);
  } catch {
    return new Response("Not found", { status: 404 });
  }
  return proxyBackend(request, pathname, {
    rewriteJsonUrls: "all-upstream",
    streamResponse: true
  });
}
