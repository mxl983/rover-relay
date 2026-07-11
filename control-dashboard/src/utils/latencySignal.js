export const HIGH_LATENCY_MS = 300;

export function isHighLatency(latencyMs) {
  const val = Number(latencyMs);
  return Number.isFinite(val) && val > HIGH_LATENCY_MS;
}
