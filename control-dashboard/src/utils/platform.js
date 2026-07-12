/**
 * Detect SteamOS / Linux targets where Chromium gates the Gamepad API until
 * the tab is focused and the user presses a controller button.
 */
export function isSteamOS() {
  if (typeof navigator === "undefined") return false;

  const ua = navigator.userAgent ?? "";
  const uaLower = ua.toLowerCase();

  if (
    uaLower.includes("steamos") ||
    uaLower.includes("steam deck") ||
    uaLower.includes("valve steam")
  ) {
    return true;
  }

  const platform = navigator.platform ?? "";
  if (/linux/i.test(ua) || /linux/i.test(platform)) {
    return true;
  }

  if (navigator.userAgentData?.platform === "Linux") {
    return true;
  }

  return false;
}
