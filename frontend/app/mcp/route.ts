import { NextRequest } from "next/server";
import { proxyBackend } from "../lib/backend-proxy";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  return proxyMcp(request);
}

export async function POST(request: NextRequest) {
  return proxyMcp(request);
}

export async function DELETE(request: NextRequest) {
  return proxyMcp(request);
}

export async function HEAD(request: NextRequest) {
  return proxyMcp(request);
}

export async function OPTIONS(request: NextRequest) {
  return proxyMcp(request);
}

function proxyMcp(request: NextRequest) {
  return proxyBackend(request, "/mcp");
}
