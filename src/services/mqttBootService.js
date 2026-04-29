import mqtt from "mqtt";
import config from "../config.js";
import {
  getLatestTelemetryEvent,
  recordTelemetryEvent,
} from "./telemetryService.js";

class MqttBootService {
  constructor() {
    this.client = null;
    this.connected = false;
    this.lastSignalAtMs = 0;
  }

  start() {
    if (!config.mqttBoot.enabled) return;
    if (!config.mqttBoot.username || !config.mqttBoot.password) {
      console.warn("[MQTT-BOOT] enabled but credentials missing; skipping connect");
      return;
    }
    if (this.client) return;

    this.client = mqtt.connect(config.mqttBoot.url, {
      username: config.mqttBoot.username,
      password: config.mqttBoot.password,
      keepalive: 60,
      reconnectPeriod: 1000,
      connectTimeout: 20_000,
      protocolId: "MQTT",
      protocolVersion: 4,
      clean: true,
      clientId: `relay_boot_${Math.random().toString(16).slice(2, 8)}`,
    });

    this.client.on("connect", () => {
      this.connected = true;
      console.log("[MQTT-BOOT] connected");
      this.client?.subscribe(config.mqttBoot.bootTopic, { qos: 1 }, (err) => {
        if (err) {
          console.error("[MQTT-BOOT] subscribe failed:", err.message);
          return;
        }
        console.log(`[MQTT-BOOT] subscribed ${config.mqttBoot.bootTopic}`);
      });
    });

    this.client.on("message", (topic, payloadBuf) => {
      const payload = String(payloadBuf || "").trim();
      if (topic !== config.mqttBoot.bootTopic) return;
      const lowered = payload.toLowerCase();
      const onPrefix = config.mqttBoot.bootPayloadPrefix.toLowerCase();
      const isOn = lowered.startsWith(onPrefix);
      const isOff = lowered.startsWith("off");
      if (!isOn && !isOff) return;
      const now = Date.now();
      // Ignore accidental duplicates in rapid succession.
      if (now - this.lastSignalAtMs < 1500) return;
      this.lastSignalAtMs = now;

      if (isOn) {
        const lastOn = getLatestTelemetryEvent("mqtt_power_on");
        const lastOff = getLatestTelemetryEvent("mqtt_power_off");
        const latestPowerEvent =
          lastOn && lastOff ? (lastOn.id > lastOff.id ? lastOn : lastOff) : lastOn || lastOff;
        if (latestPowerEvent?.event === "mqtt_power_on") {
          console.log("[MQTT-BOOT] ignoring mqtt_power_on; last power event is already ON");
          return;
        }
        recordTelemetryEvent("mqtt_power_on");
        console.log(`[MQTT-BOOT] wake signal logged to telemetry topic=${topic} payload=${payload}`);
        return;
      }

      recordTelemetryEvent("mqtt_power_off");
      console.log(`[MQTT-BOOT] off signal logged to telemetry topic=${topic} payload=${payload}`);
    });

    this.client.on("reconnect", () => {
      this.connected = false;
      console.log("[MQTT-BOOT] reconnecting...");
    });
    this.client.on("close", () => {
      this.connected = false;
      console.log("[MQTT-BOOT] connection closed");
    });
    this.client.on("error", (err) => {
      this.connected = false;
      console.error("[MQTT-BOOT] error:", err.message);
    });
  }

  stop() {
    if (!this.client) return;
    this.client.end(true);
    this.client = null;
    this.connected = false;
  }
}

export const mqttBootService = new MqttBootService();
