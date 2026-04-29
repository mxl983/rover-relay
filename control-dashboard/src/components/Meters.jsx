import React from "react";
import { RoverSchematic } from "./RoverSchematic";

export const Meters = ({ stats, compact }) => {
  return (
    <div className="meter-container">
      <RoverSchematic
        pan={stats.pan}
        battery={stats.battery}
        cpuTemp={stats.cpuTemp}
        latencyMs={stats.latency}
        isCharging={stats.isCharging}
      />
      {compact ? null : (
        <>
          {" "}
          <div className="meter-row">
            <div className="stat">
              BAT <span>{stats.battery || "-"}%</span>
            </div>
            <div className="stat">
              VOL <span>{stats.voltage || "-"}V</span>
            </div>
            <div className="stat">
              DLAY <span id="lat">{stats.latency || "-"}ms</span>
            </div>
          </div>
          <div className="meter-row">
            <div className="stat">
              DIST{" "}
              <span>{((stats?.distance || 0) / 1000).toFixed(1) || "-"}M</span>
            </div>
            <div className="stat">
              TEMP <span>{stats.cpuTemp || "-"}°C</span>
            </div>
            <div className="stat">
              CPU <span id="lat">{stats.cpuLoad || "-"}%</span>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default Meters;
