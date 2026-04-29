import { useState } from "react";
import mqtt from "mqtt";
import PropTypes from "prop-types";
import { MQTT_HOST } from "../constants";

const DASHBOARD_BASE_PATH = import.meta.env.BASE_URL || "/";

function isDashboardPath(pathname) {
  const p = pathname || "/";
  const baseNoTrailingSlash =
    DASHBOARD_BASE_PATH.endsWith("/") && DASHBOARD_BASE_PATH.length > 1
      ? DASHBOARD_BASE_PATH.slice(0, -1)
      : DASHBOARD_BASE_PATH;
  if (baseNoTrailingSlash === "/") return p === "/" || p.startsWith("/rover");
  return p === baseNoTrailingSlash || p.startsWith(`${baseNoTrailingSlash}/`);
}

export const LoginOverlay = ({ onLoginSuccess }) => {
  const [formData, setFormData] = useState({ username: "", password: "" });
  const [status, setStatus] = useState("IDLE"); // IDLE, CONNECTING, ERROR
  const [errorMsg, setErrorMsg] = useState("");

  const verifyAndConnect = (e) => {
    e.preventDefault();
    setStatus("CONNECTING");

    const options = {
      keepalive: 60,
      clientId: `web_operator_${Math.random().toString(16).substring(2, 5)}`,
      protocolId: "MQTT",
      protocolVersion: 4,
      clean: true,
      reconnectPeriod: 1000,
      connectTimeout: 30 * 1000,
      username: formData.username,
      password: formData.password,
    };

    const client = mqtt.connect(MQTT_HOST, options);

    const isDashboardUrlHit = () => {
      if (typeof window === "undefined") return false;
      return isDashboardPath(window.location?.pathname);
    };
    const canWakeNow = () =>
      typeof document !== "undefined" &&
      document.visibilityState === "visible" &&
      !document.hidden &&
      isDashboardUrlHit();

    client.on("connect", () => {
      console.log("🔓 AUTH_VALIDATED");

      // As soon as MQTT auth succeeds, transition UI and let the main app manage MQTT.
      onLoginSuccess(client, formData);

      // Fire-and-forget boot signals only from an active visible dashboard tab.
      if (canWakeNow()) {
        const topic1 = "rover/power/pi";
        const topic2 = "rover/power/aux";
        const payload = "On";
        const pubOptions = { qos: 1 };
        client.publish(topic1, payload, pubOptions);
        client.publish(topic2, payload, pubOptions);
      }

      // Close this temporary client shortly after; App will create its own via useMqtt.
      setTimeout(() => client.end(), 500);
    });

    client.on("error", (err) => {
      console.error("🚫 AUTH_FAILED:", err);
      setStatus("ERROR");
      setErrorMsg("ACCESS_DENIED: CREDENTIALS_REJECTED");
      client.end();
    });
  };

  return (
    <div style={styles.overlay}>
      <form onSubmit={verifyAndConnect} style={styles.terminal}>
        <div style={styles.header}>
          <div style={styles.glitch}>SYSTEM_ACCESS_REQUIRED</div>
          <div style={styles.subHeader}>HIVEMQ_CLOUD_GATEWAY</div>
        </div>

        <div style={styles.inputGroup}>
          <label style={styles.label}>OPERATOR_ID</label>
          <input
            type="text"
            className="hud-input"
            value={formData.username}
            onChange={(e) =>
              setFormData({ ...formData, username: e.target.value })
            }
            placeholder="jjrover"
            disabled={status === "CONNECTING"}
          />
        </div>

        <div style={styles.inputGroup}>
          <label style={styles.label}>ACCESS_KEY</label>
          <input
            type="password"
            className="hud-input"
            value={formData.password}
            onChange={(e) =>
              setFormData({ ...formData, password: e.target.value })
            }
            placeholder="••••••••"
            disabled={status === "CONNECTING"}
          />
        </div>

        {status === "ERROR" && <div style={styles.error}>{errorMsg}</div>}

        <button
          type="submit"
          style={{
            ...styles.button,
            opacity: status === "CONNECTING" ? 0.5 : 1,
          }}
          disabled={status === "CONNECTING"}
        >
          {status === "CONNECTING"
            ? "VERIFYING_UPLINK..."
            : "INITIALIZE_UPLINK"}
        </button>
      </form>

      <style>{`
        .hud-input {
          background: rgba(0, 242, 255, 0.05);
          border: 1px solid #00f2ff33;
          padding: 10px;
          color: #fff;
          fontSize: 12px;
          font-family: monospace;
          outline: none;
        }
        .hud-input:focus {
          border-color: #00f2ff;
          box-shadow: 0 0 10px rgba(0, 242, 255, 0.2);
        }
      `}</style>
    </div>
  );
};

const styles = {
  overlay: {
    position: "fixed",
    inset: 0,
    backgroundColor: "#000",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
    fontFamily: "monospace",
  },
  terminal: {
    width: "320px",
    padding: "30px",
    border: "1px solid #00f2ff",
    background: "#050505",
    display: "flex",
    flexDirection: "column",
    gap: "20px",
  },
  header: {
    marginBottom: "10px",
    borderBottom: "1px solid #00f2ff44",
    paddingBottom: "10px",
  },
  glitch: {
    color: "#00f2ff",
    fontSize: "14px",
    fontWeight: "bold",
    letterSpacing: "2px",
  },
  subHeader: { color: "#666", fontSize: "9px", marginTop: "4px" },
  inputGroup: { display: "flex", flexDirection: "column", gap: "8px" },
  label: { color: "#00f2ff", fontSize: "10px", letterSpacing: "1px" },
  button: {
    width: "100%",
    background: "#00f2ff",
    color: "#000",
    border: "none",
    padding: "12px",
    fontSize: "12px",
    fontWeight: "bold",
    cursor: "pointer",
    fontFamily: "monospace",
  },
  error: {
    color: "#ff4444",
    fontSize: "10px",
    textAlign: "center",
    border: "1px solid #ff444433",
    padding: "5px",
  },
};

LoginOverlay.propTypes = {
  onLoginSuccess: PropTypes.func.isRequired,
};
