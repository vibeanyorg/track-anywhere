export type ErrorPayload = {
  detail?: unknown;
};

export async function readJson<T>(response: Response): Promise<T> {
  const text = await response.text();
  if (!text) return {} as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    return { detail: text } as T;
  }
}

export function responseError(payload: unknown, fallback = "Request failed") {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as ErrorPayload).detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
  }
  return fallback;
}
