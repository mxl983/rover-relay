/**
 * Shared API client: timeout, retries, and consistent error handling.
 */

const DEFAULT_TIMEOUT_MS = 15_000;
const DEFAULT_RETRIES = 1;

/**
 * @param {string} url
 * @param {RequestInit & { timeout?: number; retries?: number }} [options]
 * @returns {Promise<Response>}
 */
export async function apiFetch(url, options = {}) {
  const { timeout = DEFAULT_TIMEOUT_MS, retries = DEFAULT_RETRIES, ...fetchOptions } = options;
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeout);

  let lastError;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const res = await fetch(url, {
        ...fetchOptions,
        signal: controller.signal,
      });
      clearTimeout(id);
      return res;
    } catch (err) {
      lastError = err;
      if (attempt === retries || err.name === "AbortError") break;
    }
  }
  clearTimeout(id);
  throw lastError;
}

/**
 * POST JSON and parse JSON response. Throws on non-ok or network error.
 * @param {string} url
 * @param {object} body
 * @param {{ timeout?: number; retries?: number; headers?: Record<string, string> }} [options]
 * @returns {Promise<unknown>}
 */
export async function apiPostJson(url, body, options = {}) {
  const { headers = {}, ...rest } = options;
  const res = await apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
    ...rest,
  });
  const text = await res.text();
  if (!res.ok) {
    const err = new Error(`API error ${res.status}: ${text || res.statusText}`);
    err.status = res.status;
    err.body = text;
    throw err;
  }
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/**
 * POST with no body, parse JSON response.
 * @param {string} url
 * @param {{ timeout?: number; retries?: number }} [options]
 * @returns {Promise<unknown>}
 */
export async function apiPost(url, options = {}) {
  const res = await apiFetch(url, { method: "POST", ...options });
  const text = await res.text();
  if (!res.ok) {
    const err = new Error(`API error ${res.status}: ${text || res.statusText}`);
    err.status = res.status;
    err.body = text;
    throw err;
  }
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
