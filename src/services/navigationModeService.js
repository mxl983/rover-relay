import fs from "fs/promises";
import path from "path";
import config from "../config.js";

const DEFAULT_STATE = { enabled: false, phase: "idle", updatedAt: null };

async function readStateFile() {
  const raw = await fs.readFile(config.navigation.modeFilePath, "utf8");
  return JSON.parse(raw);
}

export async function getNavigationMode() {
  try {
    const data = await readStateFile();
    return {
      enabled: data?.enabled === true,
      phase: typeof data?.phase === "string" ? data.phase : "idle",
      updatedAt: data?.updatedAt ?? null,
    };
  } catch {
    return { ...DEFAULT_STATE };
  }
}

export async function setNavigationMode(enabled) {
  const next = {
    enabled: Boolean(enabled),
    phase: enabled ? "waiting" : "idle",
    updatedAt: new Date().toISOString(),
  };
  const directory = path.dirname(config.navigation.modeFilePath);
  await fs.mkdir(directory, { recursive: true });
  const tmpPath = `${config.navigation.modeFilePath}.tmp`;
  await fs.writeFile(tmpPath, JSON.stringify(next, null, 2));
  await fs.rename(tmpPath, config.navigation.modeFilePath);
  return next;
}
