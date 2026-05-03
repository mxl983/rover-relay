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
      usb_power INTEGER
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
      raw_health TEXT
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
  return db;
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
