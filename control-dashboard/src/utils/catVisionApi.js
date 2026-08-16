/**
 * Cat vision API (detect + posture).
 *
 * Paths depend on whether we talk to vision_server directly or via the relay
 * HTTPS proxy (required for github.io — browsers block http://…:8010 mixed content).
 */
import { apiFetch, apiPostJson } from "../api/client.js";
import { VISION_HTTP_BASE } from "../config.js";

/** @param {string} base */
export function visionEndpoints(base) {
  const root = String(base || "").replace(/\/$/, "");
  const viaRelay = /\/api\/vision$/i.test(root);
  if (viaRelay) {
    return {
      cat: `${root}/cat`,
      ws: `${root.replace(/^http/i, "ws")}/ws`,
    };
  }
  return {
    cat: `${root}/api/cat`,
    ws: `${root.replace(/^http/i, "ws")}/ws/vision`,
  };
}

const ENDPOINTS = visionEndpoints(VISION_HTTP_BASE);

export function getCatVisionWsUrl() {
  return ENDPOINTS.ws;
}

export async function fetchCatVisionStatus() {
  const res = await apiFetch(ENDPOINTS.cat, { timeout: 4000, retries: 1 });
  if (!res.ok) {
    throw new Error(`cat vision status failed (${res.status})`);
  }
  return res.json();
}

export async function postCatVisionEnabled(enabled) {
  return apiPostJson(
    ENDPOINTS.cat,
    { enabled: Boolean(enabled) },
    { timeout: 4000, retries: 1 },
  );
}

export function readCatVisionEnabled(payload) {
  if (payload && typeof payload.enabled === "boolean") return payload.enabled;
  return null;
}
