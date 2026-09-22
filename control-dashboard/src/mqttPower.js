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
