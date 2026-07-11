import { apiPost } from "../api/client.js";
import { PI_SYSTEM_ENDPOINT } from "../config.js";

const CHIME_ENDPOINT = `${PI_SYSTEM_ENDPOINT}/chime`;

/**
 * Ask the rover to play its system chime once (best-effort; never throws).
 * Used as audible feedback when a dashboard setting changes.
 */
export async function playRoverChime() {
  try {
    await apiPost(CHIME_ENDPOINT, { timeout: 2500, retries: 0 });
  } catch {
    /* ignore — setting change should not fail if chime is unreachable */
  }
}
