import Database from "better-sqlite3";
import fs from "fs";
import path from "path";
import config from "../config.js";

let db = null;

/** Same telemetry row shape as github.com/mxl983/rover server telemetryService. */
export function openDb() {
  if (db) return db;
  const dir = path.dirname(config.telemetry.dbPath);
  try {
    fs.mkdirSync(dir, { recursive: true });
  } catch (e) {
    if (e.code !== "EEXIST") throw e;
  }
  db = new Database(config.telemetry.dbPath);
  db.pragma("journal_mode = WAL");
  db.pragma("synchronous = NORMAL");
  db.exec(`
    CREATE TABLE IF NOT EXISTS telemetry (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      event TEXT,
      voltage REAL,
      battery_pct REAL,
      distance REAL,
      pan REAL,
      tilt REAL,
      cpu_temp TEXT,
      cpu_load INTEGER,
      wifi_signal INTEGER,
      usb_power INTEGER,
      charging INTEGER,
      session_id TEXT,
      session_active INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_telemetry_created_at ON telemetry(created_at);

    CREATE TABLE IF NOT EXISTS client_connections (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      event TEXT NOT NULL,
      client_ip TEXT,
      user_agent TEXT,
      device_info TEXT,
      location_info TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_client_connections_created_at ON client_connections(created_at);

    CREATE TABLE IF NOT EXISTS rover_heartbeat (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      phase TEXT,
      boot_started_at TEXT,
      battery_pct REAL,
      video_on INTEGER,
      voltage REAL,
      raw_health TEXT,
      charging INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_rover_heartbeat_created_at ON rover_heartbeat(created_at);

    CREATE TABLE IF NOT EXISTS mqtt_boot_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      topic TEXT NOT NULL,
      payload TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_mqtt_boot_events_created_at ON mqtt_boot_events(created_at);
  `);
  migrateRelaySchema(db);
  return db;
}

/** v2: charging + purge. v3: session columns. v4: purge + session_id UUID (TEXT). */
function migrateRelaySchema(db) {
  let ver = db.pragma("user_version", { simple: true });

  if (ver < 2) {
    const telHasCharging = db.prepare("PRAGMA table_info(telemetry)").all().some((c) => c.name === "charging");
    const hbHasCharging = db.prepare("PRAGMA table_info(rover_heartbeat)").all().some((c) => c.name === "charging");

    if (!telHasCharging) {
      try {
        db.exec("ALTER TABLE telemetry ADD COLUMN charging INTEGER");
      } catch (e) {
        console.warn("[relay-db] telemetry.charging migrate:", e.message);
      }
    }
    if (!hbHasCharging) {
      try {
        db.exec("ALTER TABLE rover_heartbeat ADD COLUMN charging INTEGER");
      } catch (e) {
        console.warn("[relay-db] rover_heartbeat.charging migrate:", e.message);
      }
    }

    console.log("[relay-db] schema v2: purging telemetry & rover_heartbeat (charging migration)");
    db.exec("DELETE FROM telemetry; DELETE FROM rover_heartbeat;");
    db.pragma("user_version = 2");
    ver = 2;
  }

  if (ver < 3) {
    const cols = db.prepare("PRAGMA table_info(telemetry)").all();
    if (!cols.some((c) => c.name === "session_id")) {
      try {
        db.exec("ALTER TABLE telemetry ADD COLUMN session_id INTEGER");
      } catch (e) {
        console.warn("[relay-db] telemetry.session_id migrate:", e.message);
      }
    }
    if (!cols.some((c) => c.name === "session_active")) {
      try {
        db.exec("ALTER TABLE telemetry ADD COLUMN session_active INTEGER");
      } catch (e) {
        console.warn("[relay-db] telemetry.session_active migrate:", e.message);
      }
    }
    db.pragma("user_version = 3");
    ver = 3;
  }

  if (ver < 4) {
    console.log("[relay-db] schema v4: purge telemetry & rover_heartbeat; session_id UUID (TEXT)");
    db.exec("DELETE FROM telemetry; DELETE FROM rover_heartbeat;");
    const cols = db.prepare("PRAGMA table_info(telemetry)").all();
    const sid = cols.find((c) => c.name === "session_id");
    if (sid) {
      try {
        db.exec("ALTER TABLE telemetry DROP COLUMN session_id");
      } catch (e) {
        console.warn("[relay-db] telemetry DROP session_id:", e.message);
      }
    }
    if (!db.prepare("PRAGMA table_info(telemetry)").all().some((c) => c.name === "session_id")) {
      try {
        db.exec("ALTER TABLE telemetry ADD COLUMN session_id TEXT");
      } catch (e) {
        console.warn("[relay-db] telemetry ADD session_id TEXT:", e.message);
      }
    }
    db.pragma("user_version = 4");
  }
}

export function getDb() {
  return openDb();
}

export function closeDb() {
  if (db) {
    db.close();
    db = null;
  }
}
