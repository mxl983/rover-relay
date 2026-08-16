/**
 * Ensures we only show "charging: yes" when confidence is medium/high.
 * Used by the LED webcam charging detector.
 */
export function enforceConservativeChargingDecision(result) {
  const confidence = result?.confidence || "low";
  const isStrongEnough = confidence === "medium" || confidence === "high";
  const shouldBeYes = result?.isCharging === true && isStrongEnough;
  if (shouldBeYes) return result;
  return {
    ...result,
    isCharging: false,
    reason: result?.reason || "conservative_gate_not_confident_yes",
  };
}
