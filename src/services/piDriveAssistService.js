import config from "../config.js";

function piBaseUrl() {
  const raw =
    process.env.NAV_PI_BASE_URL ||
    process.env.ROVER_PI_BASE_URL ||
    "https://rover.tail9d0237.ts.net:3000";
  return raw.replace(/\/$/, "");
}

/** Turn Pi drive assist on/off (best-effort; navigation must not fight assist maneuvers). */
export async function setPiDriveAssistEnabled(enabled) {
  const url = `${piBaseUrl()}/api/system/drive-assist`;
  const headers = { "Content-Type": "application/json" };
  if (config.auth?.token) {
    headers.Authorization = `Bearer ${config.auth.token}`;
  }

  try {
    const response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({ enabled: Boolean(enabled) }),
      signal: AbortSignal.timeout(2500),
    });
    if (!response.ok) {
      return { ok: false, status: response.status };
    }
    const body = await response.json();
    return { ok: true, enabled: body?.enabled === true };
  } catch (err) {
    return { ok: false, reason: err?.message ?? "pi_unreachable" };
  }
}
