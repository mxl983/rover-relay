/**
 * Cat vision box API (live detect + posture on vision_server).
 */
import { apiFetch, apiPostJson } from "../api/client.js";
import { VISION_HTTP_BASE } from "../config.js";

const CAT_ENDPOINT = `${VISION_HTTP_BASE}/api/cat`;

export function getCatVisionWsUrl() {
  return `${VISION_HTTP_BASE.replace(/^http/i, "ws")}/ws/vision`;
}

export async function fetchCatVisionStatus() {
  const res = await apiFetch(CAT_ENDPOINT, { timeout: 2500, retries: 0 });
  if (!res.ok) {
    throw new Error(`cat vision status failed (${res.status})`);
  }
  return res.json();
}

export async function postCatVisionEnabled(enabled) {
  return apiPostJson(CAT_ENDPOINT, { enabled: Boolean(enabled) }, { timeout: 2500, retries: 0 });
}

export function readCatVisionEnabled(payload) {
  if (payload && typeof payload.enabled === "boolean") return payload.enabled;
  return null;
}
