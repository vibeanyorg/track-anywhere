export function oauthRedirectUri(publicOrigin) {
  return new URL("/auth/callback", publicOrigin).toString();
}
