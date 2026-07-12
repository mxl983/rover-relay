/** Toggle document fullscreen (standard + webkit). */
export function toggleDocumentFullscreen() {
  if (typeof document === "undefined") return;
  const active = document.fullscreenElement || document.webkitFullscreenElement;
  if (!active) {
    const el = document.documentElement;
    const requestFs = el.requestFullscreen || el.webkitRequestFullscreen;
    if (requestFs) {
      Promise.resolve(requestFs.call(el)).catch(() => {});
    }
    return;
  }
  const exitFs = document.exitFullscreen || document.webkitExitFullscreen;
  if (exitFs) {
    Promise.resolve(exitFs.call(document)).catch(() => {});
  }
}
