/** Stable per-browser presence id + short platform label for the Pi viewers API. */

const CLIENT_ID_KEY = "rover-dashboard-presence-id";

export function getOrCreatePresenceClientId() {
  if (typeof window === "undefined") return "server";
  try {
    const existing = window.localStorage.getItem(CLIENT_ID_KEY);
    if (existing && existing.length >= 8) return existing.slice(0, 64);
    const id =
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    window.localStorage.setItem(CLIENT_ID_KEY, id);
    return id;
  } catch {
    return `ephemeral-${Date.now().toString(36)}`;
  }
}

/**
 * Short human label for the HUD (iPhone, Mac, …).
 * @param {string} [ua]
 */
export function getPresenceClientLabel(ua = typeof navigator !== "undefined" ? navigator.userAgent : "") {
  const s = String(ua || "");
  if (/iPhone/i.test(s)) return "iPhone";
  if (/iPad/i.test(s) || (/Macintosh/i.test(s) && typeof navigator !== "undefined" && navigator.maxTouchPoints > 1)) {
    return "iPad";
  }
  if (/Android/i.test(s)) return "Android";
  if (/Macintosh|Mac OS X/i.test(s)) return "Mac";
  if (/Windows/i.test(s)) return "Windows";
  if (/CrOS/i.test(s)) return "ChromeOS";
  if (/Linux/i.test(s)) return "Linux";
  return "Browser";
}

/** Body for POST /api/system/activity heartbeats. */
export function presenceActivityBody(extra = {}) {
  return {
    clientId: getOrCreatePresenceClientId(),
    label: getPresenceClientLabel(),
    ...extra,
  };
}

/**
 * @param {unknown} presence
 * @returns {{ count: number, clients: { id: string, label: string }[] } | null}
 */
export function readPresenceSnapshot(presence) {
  if (!presence || typeof presence !== "object") return null;
  const clients = Array.isArray(presence.clients)
    ? presence.clients
        .map((c) => ({
          id: String(c?.id ?? ""),
          label: String(c?.label ?? "Browser").slice(0, 32),
        }))
        .filter((c) => c.id)
    : [];
  const count = Number.isFinite(Number(presence.count))
    ? Math.max(0, Math.round(Number(presence.count)))
    : clients.length;
  return { count, clients };
}
