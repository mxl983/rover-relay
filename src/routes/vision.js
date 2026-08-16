import { Router } from "express";
import config from "../config.js";
import { error } from "../utils/apiResponse.js";

const router = Router();

function upstreamBase() {
  return (config.vision?.upstreamUrl || "http://host.docker.internal:8010").replace(
    /\/$/,
    "",
  );
}

/**
 * GET/POST /api/vision/cat → vision_server /api/cat
 * Keeps github.io (HTTPS) off mixed-content http://…:8010.
 */
router.all("/cat", async (req, res) => {
  const url = `${upstreamBase()}/api/cat`;
  try {
    const headers = { Accept: "application/json" };
    if (req.headers["content-type"]) {
      headers["Content-Type"] = req.headers["content-type"];
    }
    const init = {
      method: req.method,
      headers,
      signal: AbortSignal.timeout(config.vision?.timeoutMs || 4000),
    };
    if (req.method !== "GET" && req.method !== "HEAD") {
      init.body = JSON.stringify(req.body ?? {});
      headers["Content-Type"] = "application/json";
    }
    const upstream = await fetch(url, init);
    const text = await upstream.text();
    res.status(upstream.status);
    res.setHeader("Cache-Control", "no-store");
    const ct = upstream.headers.get("content-type");
    if (ct) res.setHeader("Content-Type", ct);
    return res.send(text);
  } catch (e) {
    return error(
      res,
      config.env === "production"
        ? "Vision service unavailable"
        : e instanceof Error
          ? e.message
          : String(e),
      502,
    );
  }
});

router.get("/health", async (req, res) => {
  const url = `${upstreamBase()}/health`;
  try {
    const upstream = await fetch(url, {
      signal: AbortSignal.timeout(config.vision?.timeoutMs || 4000),
    });
    const text = await upstream.text();
    res.status(upstream.status);
    res.setHeader("Cache-Control", "no-store");
    const ct = upstream.headers.get("content-type");
    if (ct) res.setHeader("Content-Type", ct);
    return res.send(text);
  } catch (e) {
    return error(
      res,
      config.env === "production"
        ? "Vision service unavailable"
        : e instanceof Error
          ? e.message
          : String(e),
      502,
    );
  }
});

export default router;
