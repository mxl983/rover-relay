import Database from "better-sqlite3";
import fs from "fs";
import path from "path";
import config from "../config.js";

let db = null;

function ensureColumn(database, tableName, columnName, ddl) {
  const cols = database.prepare(`PRAGMA table_info(${tableName})`).all();
  const exists = cols.some((c) => c.name === columnName);
  if (!exists) database.exec(`ALTER TABLE ${tableName} ADD COLUMN ${ddl}`);
}

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

    CREATE TABLE IF NOT EXISTS experiment_voltage_samples (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      session_id TEXT,
      label_charging INTEGER NOT NULL,
      voltage REAL NOT NULL,
      telemetry_voltage REAL,
      voltage_1dp REAL,
      adc_mv_avg REAL,
      adc_raw_min INTEGER,
      adc_raw_max INTEGER,
      source TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_experiment_voltage_created_at ON experiment_voltage_samples(created_at);
    CREATE INDEX IF NOT EXISTS idx_experiment_voltage_label ON experiment_voltage_samples(label_charging);
    CREATE INDEX IF NOT EXISTS idx_experiment_voltage_session ON experiment_voltage_samples(session_id);

    CREATE TABLE IF NOT EXISTS runtime_voltage_samples (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      esp_voltage REAL NOT NULL,
      telemetry_voltage REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_runtime_voltage_created_at ON runtime_voltage_samples(created_at);

    CREATE TABLE IF NOT EXISTS experiment_collection_state (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      enabled INTEGER NOT NULL DEFAULT 0,
      session_id TEXT,
      saved_count INTEGER NOT NULL DEFAULT 0,
      dropped_count INTEGER NOT NULL DEFAULT 0,
      last_error TEXT,
      last_sample_at TEXT,
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    INSERT OR IGNORE INTO experiment_collection_state (id) VALUES (1);
  `);
  // Lightweight migration for existing DBs created before telemetry_voltage existed.
  ensureColumn(db, "experiment_voltage_samples", "telemetry_voltage", "telemetry_voltage REAL");
  ensureColumn(
    db,
    "experiment_collection_state",
    "forced_label_charging",
    "forced_label_charging INTEGER",
  );
  ensureColumn(db, "experiment_collection_state", "label_mode", "label_mode TEXT");
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
