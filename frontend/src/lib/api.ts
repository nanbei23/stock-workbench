export async function requestJson<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, options);
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const detail = payload?.detail || payload?.message || response.statusText;
    throw new Error(Array.isArray(detail) ? detail.map((item) => item.msg || String(item)).join("; ") : String(detail));
  }

  return payload as T;
}

export function getJson<T>(url: string): Promise<T> {
  return requestJson<T>(url);
}

export function postJson<TResponse, TPayload extends object = Record<string, never>>(
  url: string,
  payload?: TPayload
): Promise<TResponse> {
  return requestJson<TResponse>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload ?? {})
  });
}
