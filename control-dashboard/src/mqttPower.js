/**
 * HiveMQ power commands for esp-web-arduino (GPIO main circuit).
 *
 * Publish to rover/web/cmd:
 *   ON GPIO13
 *   OFF GPIO13
 *   OFF GPIO13 DELAY 15
 * Subscribe rover/web/status for replies (PONG, etc.).
 */

export const MQTT_CMD_TOPIC =
  import.meta.env.VITE_MQTT_CMD_TOPIC || "rover/web/cmd";

export const MQTT_STATUS_TOPIC =
  import.meta.env.VITE_MQTT_STATUS_TOPIC || "rover/web/status";

/** Main power relay GPIO on the ESP. */
export const MQTT_POWER_GPIO = Number(import.meta.env.VITE_MQTT_POWER_GPIO) || 13;

/**
 * Seconds to wait after graceful Pi shutdown before cutting the circuit.
 * Matches esp-web-arduino: OFF GPIO13 DELAY <seconds>
 */
export const MQTT_POWER_OFF_DELAY_SEC =
  Number(import.meta.env.VITE_MQTT_POWER_OFF_DELAY_SEC) || 15;

/** Assumed rover cold-boot duration used by the loading-screen progress bar. */
export const ROVER_BOOT_DURATION_MS = 50_000;

const LAST_POWER_ON_AT_KEY = "rover:lastPowerOnAt";

/** Persist when a wake (ON) command was last published. */
export function recordPowerOnSent(at = Date.now()) {
  const ts = Number(at);
  if (!Number.isFinite(ts) || ts <= 0) return null;
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(LAST_POWER_ON_AT_KEY, String(ts));
    }
  } catch {
    /* ignore quota / private mode */
  }
  return ts;
}

/** @returns {number | null} epoch ms of last publishPowerOn, if known */
export function getLastPowerOnAt() {
  try {
    if (typeof localStorage === "undefined") return null;
    const v = Number(localStorage.getItem(LAST_POWER_ON_AT_KEY));
    return Number.isFinite(v) && v > 0 ? v : null;
  } catch {
    return null;
  }
}

/**
 * Boot progress from last MQTT ON → now, assuming ROVER_BOOT_DURATION_MS.
 * Caps at 99 until the stream is actually ready.
 * @returns {number | null}
 */
export function getBootProgressPercent(
  now = Date.now(),
  bootMs = ROVER_BOOT_DURATION_MS,
) {
  const at = getLastPowerOnAt();
  if (at == null) return null;
  const duration = Math.max(1, Number(bootMs) || ROVER_BOOT_DURATION_MS);
  const elapsed = Math.max(0, now - at);
  return Math.min(99, Math.max(0, Math.round((elapsed / duration) * 100)));
}

export function powerOnPayload(gpio = MQTT_POWER_GPIO) {
  return `ON GPIO${gpio}`;
}

export function powerOffPayload(gpio = MQTT_POWER_GPIO) {
  return `OFF GPIO${gpio}`;
}

export function powerOffDelayedPayload(
  delaySec = MQTT_POWER_OFF_DELAY_SEC,
  gpio = MQTT_POWER_GPIO,
) {
  const sec = Math.max(0, Math.round(Number(delaySec) || 0));
  return `OFF GPIO${gpio} DELAY ${sec}`;
}

/** Publish wake (circuit ON). No-op if client missing. */
export function publishPowerOn(client, opts = { qos: 1 }) {
  if (!client?.publish) return false;
  client.publish(MQTT_CMD_TOPIC, powerOnPayload(), opts);
  recordPowerOnSent();
  return true;
}

/** Immediate circuit cut. */
export function publishPowerOff(client, opts = { qos: 1 }) {
  if (!client?.publish) return false;
  client.publish(MQTT_CMD_TOPIC, powerOffPayload(), opts);
  return true;
}

/** Cut after delay so the Pi can shut down cleanly first. */
export function publishPowerOffDelayed(
  client,
  delaySec = MQTT_POWER_OFF_DELAY_SEC,
  opts = { qos: 1 },
) {
  if (!client?.publish) return false;
  client.publish(MQTT_CMD_TOPIC, powerOffDelayedPayload(delaySec), opts);
  return true;
}
