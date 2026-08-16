import { getAllowedCaptureOrigin } from "../config";

/**
 * Validates that a capture URL is same-origin as the API to avoid open-redirect.
 * @param {string} url
 * @returns {boolean}
 */
export function isAllowedCaptureUrl(url) {
  if (!url || typeof url !== "string") return false;
  try {
    const parsed = new URL(url);
    const allowed = getAllowedCaptureOrigin();
    if (!allowed) return false;
    return parsed.origin === allowed;
  } catch {
    return false;
  }
}
