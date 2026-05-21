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
  process.env.ROVER_LATITUDE = "49.0";
  process.env.ROVER_LONGITUDE = "-123.0";
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

afterEach(async () => {
  Reflect.deleteProperty(process.env, "CHARGING_LED_WEBCAM_STUB");
  Reflect.deleteProperty(process.env, "BACKUP_CAM_REALTIME_URL");
  Reflect.deleteProperty(process.env, "ROVER_LATITUDE");
  Reflect.deleteProperty(process.env, "ROVER_LONGITUDE");
  try {
    const { resetClientLocationForTests } = await import("../src/services/clientRoverDistanceService.js");
    resetClientLocationForTests();
  } catch {
    /* ignore */
  }
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
    expect(res.text).toContain("Latest sessions · usable battery % vs time since charge");
  });

  it("rejects missing token when configured", async () => {
    const res = await request(app).post("/api/telemetry/ingest").send({ health: { battery: 50 } });
    expect(res.status).toBe(401);
  });

  it("returns dashboard charts json", async () => {
    const res = await request(app).get("/api/telemetry/charts");
    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.activeSessionsPerDay?.valuesActiveSec).toBeTruthy();
    expect(res.body.activeSessionsPerDay?.valuesIdleSec).toBeTruthy();
    expect(res.body.batteryTimePerBand?.labels?.length).toBe(10);
  });

  it("returns latest session battery series with elapsed time since last charge", async () => {
    const sessionId = "f7bc89c8-3313-4c73-859c-c695fb79cb52";
    const health = { battery: 95, voltage: 12.4, cpuLoad: 10, wifiSignal: -50, usbPower: "on" };
    const { getDb } = await import("../src/services/db.js");
    const db = getDb();
    db.prepare(
      `INSERT INTO telemetry (event, charging, created_at) VALUES ('charging_end', 0, datetime('now', '-2 hours'))`,
    ).run();
    await request(app)
      .post("/api/telemetry/ingest")
      .set("Authorization", "Bearer testsecret")
      .send({ health, event: "mqtt_power_on" });
    db.prepare(
      "UPDATE telemetry SET session_id = ?, battery_pct = ? WHERE id = (SELECT MAX(id) FROM telemetry)",
    ).run(sessionId, 95);
    await request(app)
      .post("/api/telemetry/ingest")
      .set("Authorization", "Bearer testsecret")
      .send({ health: { ...health, battery: 88 }, event: "health_report" });
    db.prepare(
      "UPDATE telemetry SET session_id = ? WHERE id = (SELECT MAX(id) FROM telemetry)",
    ).run(sessionId);
    await request(app)
      .post("/api/telemetry/ingest")
      .set("Authorization", "Bearer testsecret")
      .send({ health: { ...health, battery: 80 }, event: "health_report" });
    db.prepare(
      "UPDATE telemetry SET session_id = ? WHERE id = (SELECT MAX(id) FROM telemetry)",
    ).run(sessionId);

    const res = await request(app).get("/api/telemetry/sessions/latest?limit=3");
    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    const match = (res.body.sessions || []).find((s) => s.sessionId === sessionId);
    expect(match).toBeTruthy();
    expect(match.series.batteryPct.length).toBeGreaterThanOrEqual(2);
    expect(match.series.batteryPct[0]).toBeGreaterThan(match.series.batteryPct.at(-1));
    expect(match.series.elapsedMin?.length).toBe(match.series.batteryPct.length);
    expect(match.series.elapsedMin.every((m) => m >= 0)).toBe(true);
    expect(res.body.lastCharging?.event).toBe("charging_end");
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
    expect(get.body.telemetry[0].battery_pct).toBe(76);
    expect(get.body.telemetry[0].event).toBe("relay_test");
    expect(typeof get.body.telemetry[0].session_id).toBe("string");
    expect(get.body.telemetry[0].session_id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
    expect(get.body.telemetry[0].session_active).toBe(1);

    const afterOff = await request(app)
      .post("/api/telemetry/ingest")
      .set("Authorization", "Bearer testsecret")
      .send({ health, event: "mqtt_power_off" });
    expect(afterOff.status).toBe(200);
    const get2 = await request(app).get("/api/telemetry?limit=5");
    const offRow = get2.body.telemetry.find((r) => r.event === "mqtt_power_off");
    expect(offRow?.session_id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
    expect(offRow?.session_id).not.toBe(get.body.telemetry[0].session_id);
    expect(offRow?.session_active).toBe(0);
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

describe("client distance", () => {
  it("computes distance from client coords to rover", async () => {
    const res = await request(app).get(
      "/api/rover/client-distance?latitude=49.19&longitude=-123.12",
    );
    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.distanceMeters).toBeGreaterThan(0);
    expect(res.body.rover).toBeUndefined();
    expect(res.body.latitude).toBeUndefined();
  });

  it("stores client location on POST and exposes distance via state", async () => {
    const post = await request(app)
      .post("/api/rover/client-distance")
      .send({ latitude: 49.19, longitude: -123.12, accuracy: 12 });
    expect(post.status).toBe(200);
    expect(post.body.distanceMeters).toBeGreaterThan(0);
    expect(post.body.rover).toBeUndefined();

    const st = await request(app).get("/api/rover/state");
    expect(st.body.rover.clientLocation?.distanceMeters).toBe(post.body.distanceMeters);
    expect(st.body.rover.clientLocation?.latitude).toBeUndefined();
    expect(st.body.rover.clientLocation?.rover).toBeUndefined();
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
