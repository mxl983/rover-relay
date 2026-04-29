import config from "../config.js";

export function requireToken(req, res, next) {
  const expected = config.auth.token;
  if (!expected) return next();
  const h = req.headers.authorization || "";
  const token = h.startsWith("Bearer ") ? h.slice(7).trim() : "";
  if (token !== expected) {
    if (config.logging.requestEnabled) {
      console.warn(
        `[AUTH] Unauthorized ${req.method} ${req.originalUrl} from ${req.socket.remoteAddress || "unknown"}`,
      );
    }
    return res.status(401).json({ success: false, error: "Unauthorized" });
  }
  next();
}

export function optionalStreamAuth(req, res, next) {
  if (!config.backupCam.streamAuth) return next();
  return requireToken(req, res, next);
}
