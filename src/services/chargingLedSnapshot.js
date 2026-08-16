/** Last LED inference written by chargingTelemetryLogger (0 = not charging, 1 = charging, null = unknown). */
let cachedCharging = null;

export function setChargingLedSnapshot(isCharging) {
  if (isCharging === true) cachedCharging = 1;
  else if (isCharging === false) cachedCharging = 0;
  else cachedCharging = null;
}

/** For stamping telemetry / heartbeat rows on the relay (may be null before first poll). */
export function getChargingLedSnapshot() {
  return cachedCharging;
}
