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

function sqlDate(ms) {
  return new Date(ms).toISOString().slice(0, 19).replace("T", " ");
}

describe("charging detection", () => {
  it("GET /api/rover/charging matches rover state", async () => {
    const res = await request(app).get("/api/rover/charging");
    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.charging).toBeDefined();
    expect(res.body.charging.isCharging === false || res.body.charging.isCharging === null).toBe(true);
  });

  it("detects slow consistent voltage rise", async () => {
    const { getDb } = await import("../src/services/db.js");
    const db = getDb();
    const t0 = Date.now() - 20 * 60_000;
    for (let i = 0; i < 10; i += 1) {
      const voltage = 10.95 + i * 0.015;
      db.prepare(
        `INSERT INTO telemetry (created_at, event, battery_pct, voltage, usb_power)
         VALUES (?, 'chg', 60.0, ?, 0)`,
      ).run(sqlDate(t0 + i * 60_000), voltage);
    }
    await request(app)
      .post("/api/rover/pulse")
      .set("Authorization", "Bearer testsecret")
      .send({
        health: { battery: 60, voltage: 11.08, videoOn: false },
        phase: "ready",
      });

    const st = await request(app).get("/api/rover/state");
    expect(st.body.rover.charging.isCharging).toBe(true);
    expect(st.body.rover.charging.goodRateCount).toBeGreaterThanOrEqual(2);
  });

  it("detects immediate plug-in voltage bump", async () => {
    const { getDb } = await import("../src/services/db.js");
    const db = getDb();
    const t0 = Date.now() - 90_000;
    const seq = [10.8, 11.0, 11.0, 11.1];
    for (let i = 0; i < seq.length; i += 1) {
      db.prepare(
        `INSERT INTO telemetry (created_at, event, battery_pct, voltage, usb_power)
         VALUES (?, 'plug', 62, ?, 0)`,
      ).run(sqlDate(t0 + i * 10_000), seq[i]);
    }
    await request(app)
      .post("/api/rover/pulse")
      .set("Authorization", "Bearer testsecret")
      .send({
        health: { battery: 62, voltage: 11.1, videoOn: false },
        phase: "ready",
      });

    const st = await request(app).get("/api/rover/state");
    expect(st.body.rover.charging.isCharging).toBe(true);
  });

  it("ignores startup 0V before evaluating charging", async () => {
    const { getDb } = await import("../src/services/db.js");
    const db = getDb();
    const t0 = Date.now() - 5 * 60_000;
    db.prepare(
      `INSERT INTO telemetry (created_at, event, battery_pct, voltage, usb_power)
       VALUES (?, 'bootish', 70, 0.0, 0)`,
    ).run(sqlDate(t0));
    db.prepare(
      `INSERT INTO telemetry (created_at, event, battery_pct, voltage, usb_power)
       VALUES (?, 'bootish', 70, 0.0, 0)`,
    ).run(sqlDate(t0 + 60_000));
    db.prepare(
      `INSERT INTO telemetry (created_at, event, battery_pct, voltage, usb_power)
       VALUES (?, 'bootish', 70, 10.9, 0)`,
    ).run(sqlDate(t0 + 120_000));
    db.prepare(
      `INSERT INTO telemetry (created_at, event, battery_pct, voltage, usb_power)
       VALUES (?, 'bootish', 70, 11.1, 0)`,
    ).run(sqlDate(t0 + 130_000));
    db.prepare(
      `INSERT INTO telemetry (created_at, event, battery_pct, voltage, usb_power)
       VALUES (?, 'bootish', 70, 11.12, 0)`,
    ).run(sqlDate(t0 + 190_000));
    db.prepare(
      `INSERT INTO telemetry (created_at, event, battery_pct, voltage, usb_power)
       VALUES (?, 'bootish', 70, 11.14, 0)`,
    ).run(sqlDate(t0 + 250_000));
    await request(app)
      .post("/api/rover/pulse")
      .set("Authorization", "Bearer testsecret")
      .send({
        health: { battery: 70, voltage: 11.14, videoOn: false },
        phase: "ready",
      });
    const st = await request(app).get("/api/rover/state");
    expect(st.body.rover.charging.isCharging).toBe(true);
  });

  it("clears charging after sustained downward slope", async () => {
    const { getDb } = await import("../src/services/db.js");
    const db = getDb();
    const t0 = Date.now() - 25 * 60_000;
    for (let i = 0; i < 8; i += 1) {
      const voltage = 10.95 + i * 0.02;
      db.prepare(
        `INSERT INTO telemetry (created_at, event, battery_pct, voltage, usb_power)
         VALUES (?, 'up', 72, ?, 0)`,
      ).run(sqlDate(t0 + i * 60_000), voltage);
    }
    for (let j = 1; j <= 3; j += 1) {
      const voltage = 10.95 + 7 * 0.02 - j * 0.06;
      db.prepare(
        `INSERT INTO telemetry (created_at, event, battery_pct, voltage, usb_power)
         VALUES (?, 'down', 72, ?, 0)`,
      ).run(sqlDate(t0 + (8 + j - 1) * 60_000), voltage);
    }
    await request(app)
      .post("/api/rover/pulse")
      .set("Authorization", "Bearer testsecret")
      .send({
        health: { battery: 72, voltage: 10.91, videoOn: false },
        phase: "ready",
      });
    const st = await request(app).get("/api/rover/state");
    expect(st.body.rover.charging.isCharging).toBe(false);
  });

  it("detects immediate unplug voltage drop", async () => {
    const { getDb } = await import("../src/services/db.js");
    const db = getDb();
    const t0 = Date.now() - 90_000;
    const seq = [11.1, 10.9, 10.9, 10.8];
    for (let i = 0; i < seq.length; i += 1) {
      db.prepare(
        `INSERT INTO telemetry (created_at, event, battery_pct, voltage, usb_power)
         VALUES (?, 'unplug', 62, ?, 0)`,
      ).run(sqlDate(t0 + i * 10_000), seq[i]);
    }
    await request(app)
      .post("/api/rover/pulse")
      .set("Authorization", "Bearer testsecret")
      .send({
        health: { battery: 62, voltage: 10.8, videoOn: false },
        phase: "ready",
      });

    const st = await request(app).get("/api/rover/state");
    expect(st.body.rover.charging.isCharging).toBe(false);
    expect(st.body.rover.charging.reason).toBe("voltage_drop_after_unplug");
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

describe("voltage experiments", () => {
  it("records labeled ESP voltage samples and returns analysis", async () => {
    for (let i = 0; i < 25; i += 1) {
      await request(app).post("/api/telemetry/experiments/voltage-sample").send({
        sessionId: "s-chg",
        labelCharging: true,
        voltage: 4.2 + i * 0.01,
        telemetryVoltage: 10.9 + i * 0.005,
        voltage1dp: 4.2,
        adcMvAvg: 900 + i,
      });
    }
    for (let i = 0; i < 25; i += 1) {
      await request(app).post("/api/telemetry/experiments/voltage-sample").send({
        sessionId: "s-dis",
        labelCharging: false,
        voltage: 5.1 + i * 0.01,
        telemetryVoltage: 11.4 + i * 0.005,
        voltage1dp: 5.1,
        adcMvAvg: 1100 + i,
      });
    }

    const samplesRes = await request(app).get("/api/telemetry/experiments/voltage-samples?limit=100");
    expect(samplesRes.status).toBe(200);
    expect(samplesRes.body.success).toBe(true);
    expect(samplesRes.body.samples.length).toBeGreaterThanOrEqual(50);

    const analysisRes = await request(app).get("/api/telemetry/experiments/analysis");
    expect(analysisRes.status).toBe(200);
    expect(analysisRes.body.success).toBe(true);
    expect(analysisRes.body.analysis.ok).toBe(true);
    expect(analysisRes.body.analysis.model.type).toBe("logistic_temporal_v1");
    expect(analysisRes.body.analysis.model.validation.accuracy).toBeGreaterThan(70);
  });

  it("clears experiment dataset", async () => {
    await request(app).post("/api/telemetry/experiments/voltage-sample").send({
      sessionId: "clear-case",
      labelCharging: true,
      voltage: 4.3,
      telemetryVoltage: 11.0,
    });

    const clearRes = await request(app).delete("/api/telemetry/experiments/voltage-samples");
    expect(clearRes.status).toBe(200);
    expect(clearRes.body.success).toBe(true);
    expect(clearRes.body.deleted).toBeGreaterThanOrEqual(1);

    const samplesRes = await request(app).get("/api/telemetry/experiments/voltage-samples?limit=10");
    expect(samplesRes.body.success).toBe(true);
    expect(samplesRes.body.samples.length).toBe(0);
  });
});
