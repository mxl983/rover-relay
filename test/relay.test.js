import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import request from "supertest";
import { mkdirSync, rmSync } from "fs";
import { join } from "path";
import { tmpdir } from "os";

let dir;
let app;
let closeTelemetry;
let closeDb;

beforeEach(async () => {
  vi.resetModules();
  dir = join(tmpdir(), `relay-test-${Date.now()}`);
  mkdirSync(dir, { recursive: true });
  process.env.TELEMETRY_DB_PATH = join(dir, "t.db");
  process.env.ROVER_API_TOKEN = "testsecret";
  process.env.CORS_ORIGINS = "http://localhost:5173";
  process.env.ROVER_HEARTBEAT_STALE_MS = "60000";
  process.env.ROVER_BOOT_TOTAL_MS = "50000";
  process.env.CHARGING_LED_WEBCAM_STUB = "idle";
  /** Fail backup-cam environment fetch immediately (tests don’t hit real LAN hardware). */
  process.env.BACKUP_CAM_REALTIME_URL = "http://127.0.0.1:1/realtime";
  Reflect.deleteProperty(process.env, "NODE_ENV");
  Reflect.deleteProperty(process.env, "TELEMETRY_ENABLED");

  const telemetry = await import("../src/services/telemetryService.js");
  const dbmod = await import("../src/services/db.js");
  closeTelemetry = telemetry.closeTelemetry;
  closeDb = dbmod.closeDb;
  telemetry.initTelemetry();

  const { createApp } = await import("../src/app.js");
  app = createApp();
});

afterEach(() => {
  Reflect.deleteProperty(process.env, "CHARGING_LED_WEBCAM_STUB");
  Reflect.deleteProperty(process.env, "BACKUP_CAM_REALTIME_URL");
  closeTelemetry?.();
  closeDb?.();
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {
    /* ignore */
  }
});

describe("telemetry ingest", () => {
  it("serves dashboard page", async () => {
    const res = await request(app).get("/dashboard");
    expect(res.status).toBe(200);
    expect(res.text).toContain("Rover Telemetry Dashboard");
  });

  it("rejects missing token when configured", async () => {
    const res = await request(app).post("/api/telemetry/ingest").send({ health: { battery: 50 } });
    expect(res.status).toBe(401);
  });

  it("records and lists telemetry", async () => {
    const health = {
      battery: 88,
      voltage: 12.4,
      distance: 10,
      pan: 0,
      tilt: 0,
      cpuTemp: "42",
      cpuLoad: 20,
      wifiSignal: -55,
      usbPower: "on",
    };
    const post = await request(app)
      .post("/api/telemetry/ingest")
      .set("Authorization", "Bearer testsecret")
      .send({ health, event: "relay_test" });
    expect(post.status).toBe(200);

    const get = await request(app).get("/api/telemetry?limit=5");
    expect(get.status).toBe(200);
    expect(get.body.success).toBe(true);
    expect(get.body.telemetry.length).toBe(1);
    expect(get.body.telemetry[0].battery_pct).toBe(88);
    expect(get.body.telemetry[0].event).toBe("relay_test");
  });
});

describe("rover pulse", () => {
  it("writes telemetry and heartbeat together", async () => {
    const res = await request(app)
      .post("/api/rover/pulse")
      .set("Authorization", "Bearer testsecret")
      .send({
        health: { battery: 77, videoOn: true },
        event: "pulse_test",
        phase: "ready",
      });
    expect(res.status).toBe(200);
    const tel = await request(app).get("/api/telemetry?limit=2");
    expect(tel.body.telemetry[0].event).toBe("pulse_test");
    const st = await request(app).get("/api/rover/state");
    expect(st.body.rover.online).toBe(true);
  });
});

describe("rover state", () => {
  it("tracks boot progress and online", async () => {
    const bootStart = new Date(Date.now() - 25_000).toISOString();
    await request(app)
      .post("/api/rover/heartbeat")
      .set("Authorization", "Bearer testsecret")
      .send({
        phase: "booting",
        bootStartedAt: bootStart,
        health: { battery: 90, videoOn: true },
      });

    const st = await request(app).get("/api/rover/state");
    expect(st.status).toBe(200);
    expect(st.body.rover.online).toBe(true);
    expect(st.body.rover.booting).toBe(true);
    expect(st.body.rover.bootProgressPct).toBeGreaterThan(40);
    expect(st.body.rover.bootProgressPct).toBeLessThan(60);

    await request(app)
      .post("/api/rover/heartbeat")
      .set("Authorization", "Bearer testsecret")
      .send({
        phase: "ready",
        health: { battery: 89, videoOn: true },
      });

    const st2 = await request(app).get("/api/rover/state");
    expect(st2.body.rover.booting).toBe(false);
    expect(st2.body.rover.bootProgressPct).toBe(100);
    expect(st2.body.rover.lastBootAt).toBeTruthy();
  });
});

describe("charging (charger LED webcam)", () => {
  it("GET /api/rover/charging matches rover state (stub)", async () => {
    const res = await request(app).get("/api/rover/charging");
    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.charging.detectionSource).toBe("webcam");
    expect(res.body.charging.reason).toBe("webcam_stub_idle");
    expect(res.body.charging.isCharging).toBe(false);
    const st = await request(app).get("/api/rover/state");
    expect(st.body.rover.charging.detectionSource).toBe("webcam");
    expect(st.body.rover.charging.reason).toBe("webcam_stub_idle");
  });
});

describe("rover environment endpoint", () => {
  it("returns realtime temperature and pressure via relay", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: true,
        sensor: "BMP280",
        temperature_c: 22.87,
        pressure_hpa: 1000.44,
        i2c_addr: "0x76",
      }),
    });
    const res = await request(app).get("/api/rover/environment");
    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.environment.temperatureC).toBe(22.87);
    expect(res.body.environment.pressureHpa).toBe(1000.44);
    expect(res.body.environment.sensor).toBe("BMP280");
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    fetchSpy.mockRestore();
  });
});

describe("CORS origins parsing", () => {
  it("normalizes GitHub Pages site URL to origin (path is not sent by browsers)", async () => {
    vi.resetModules();
    process.env.CORS_ORIGINS =
      "https://mxl983.github.io/rover-relay/,http://localhost:5173";
    const { default: cfg } = await import("../src/config.js");
    expect(cfg.cors.origins).toContain("https://mxl983.github.io");
    expect(cfg.cors.origins).toContain("http://localhost:5173");
    expect(cfg.cors.origins.length).toBe(2);
  });
});
