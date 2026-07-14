export function normalizeSameSiteRequestHeaders(
  sourceHeaders: HeadersInit,
  publicOrigin: string,
  upstreamOrigin: string
): Headers;

export function rewriteUpstreamLocation(
  sourceHeaders: HeadersInit,
  publicOrigin: string,
  upstreamOrigin: string
): Headers;

export function rewriteUpstreamJsonUrls(
  body: string,
  publicOrigin: string,
  upstreamOrigin: string
): string;
