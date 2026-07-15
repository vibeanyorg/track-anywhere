export const DEFAULT_OAUTH_SCOPE: string;

export type AuthorizationPayload = {
  response_type: "code";
  client_id: string;
  redirect_uri: string;
  resource: string;
  scope: string;
  state?: string;
  code_challenge: string;
  code_challenge_method: "S256";
};

export type AuthorizationRequest = {
  payload: AuthorizationPayload;
  clientId: string;
  resource: string;
  redirectHost: string;
  scopes: string[];
};

export function parseAuthorizationRequest(searchParams: {
  get(name: string): string | null;
}): AuthorizationRequest;

export function validateAuthorizationRedirect(
  candidate: string,
  requestedRedirectUri: string
): string;

export function normalizeDeviceCode(value: string): string;
