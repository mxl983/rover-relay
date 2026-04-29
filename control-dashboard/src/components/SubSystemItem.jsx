import React from "react";

/**
 * SubsystemItem Component
 * @param {string} label - The name of the subsystem (e.g., "CAM_UNIT")
 * @param {string} dotColor - The class name for the dot color ('red', 'green', or 'yellow')
 */
export const SubsystemItem = ({ label, dotColor }) => {
  return (
    <div className="subsystem-item">
      {/* Dynamic class for the status dot */}
      <span className={`dot ${dotColor}`}></span>
      <div
        style={{
          fontSize: "10px",
          width: "100%",
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        {label}
      </div>
    </div>
  );
};

export default SubsystemItem;
