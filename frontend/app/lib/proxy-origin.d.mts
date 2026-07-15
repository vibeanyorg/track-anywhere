export function resolvePublicOrigin(
  sourceHeaders: HeadersInit,
  fallbackOrigin: string,
  configuredOrigin?: string
): string;

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

export function rewriteAllUpstreamJsonUrls(
  body: string,
  publicOrigin: string,
  upstreamOrigin: string
): string;
