import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: process.env.VITE_BASE_PATH || "/",
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.js"],
    include: ["src/**/*.test.{js,jsx}"],
    env: {
      VITE_PI_SERVER_IP: "test.rover.local",
      VITE_MQTT_HOST: "wss://mqtt.test/mqtt",
      VITE_ROVER_STATE_URL: "https://relay.test:8787/api/rover/state",
    },
  },
});
