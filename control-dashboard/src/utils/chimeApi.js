/**
 * Ask the rover to play its system chime once (best-effort; never throws).
 * No-op on the MentorPi stack — /api/system/chime is not served (avoids 404 spam).
 */
export async function playRoverChime() {
  return;
}
