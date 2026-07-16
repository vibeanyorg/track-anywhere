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

export type AuthorizationAction = "approve" | "deny";

export type AuthorizationResponsePayload = AuthorizationPayload & {
  action: AuthorizationAction;
  approved_scopes?: string[];
};

export function parseAuthorizationRequest(searchParams: {
  get(name: string): string | null;
}): AuthorizationRequest;

export function validateAuthorizationRedirect(
  candidate: string,
  requestedRedirectUri: string
): string;

export function normalizeDeviceCode(value: string): string;

export function defaultApprovedScopes(
  requestedScopes: string[],
  availableScopes: string[]
): string[];

export function canApproveScope(
  requestedScopes: string[],
  availableScopes: string[],
  scope: string
): boolean;

export function requiredApprovalScopes(resource: string): string[];

export function canBrowserSessionApproveOAuth(
  bookId: string | null | undefined
): boolean;

export function updateApprovedScopes(
  requestedScopes: string[],
  availableScopes: string[],
  approvedScopes: string[],
  scope: string,
  selected: boolean,
  resource?: string
): string[];

export function scopeSelectionStatus(
  previousScopes: string[],
  nextScopes: string[],
  scope: string,
  selected: boolean
): string;

export function canSubmitAuthorizationApproval(
  payload: AuthorizationPayload,
  approvedScopes: string[],
  bookId: string | null | undefined
): boolean;

export function buildAuthorizationResponsePayload(
  payload: AuthorizationPayload,
  action: AuthorizationAction,
  approvedScopes: string[]
): AuthorizationResponsePayload;
