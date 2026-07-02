import { apiFetch, apiPostJson } from "../api/client.js";
import { RELAY_BASE_URL } from "../config.js";

const NAVIGATION_ENDPOINT = `${
  import.meta.env.VITE_NAVIGATION_URL ||
  `${RELAY_BASE_URL.replace(/\/$/, "")}/api/system/navigation`
}`;

/** POST — enable or disable autonomous roam mode (relay-hosted). */
export async function postNavigation(enabled) {
  return apiPostJson(NAVIGATION_ENDPOINT, { enabled });
}

/** GET — { success, enabled, phase }. */
export async function fetchNavigationStatus() {
  const res = await apiFetch(NAVIGATION_ENDPOINT, {
    timeout: 2500,
    retries: 0,
  });
  if (!res.ok) {
    throw new Error(`navigation ${res.status}`);
  }
  return res.json();
}

/** @param {unknown} response */
export function readNavigationEnabled(response) {
  return typeof response?.enabled === "boolean" ? response.enabled : null;
}
