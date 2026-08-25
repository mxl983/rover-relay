"""Hardware-free SLAM + navigation test bed (internal tooling only)."""

from .engine import (
    GRID_RESOLUTION_M,
    ROVER_SIZE_M,
    SCENARIOS,
    SlamNavSimulation,
    cast_ray,
    distance,
    plan_grid_path,
    run_regressions,
    wrap_angle,
)
from .harness import (
    drive,
    pose_error,
    run_apartment_drift_benchmark,
    summarize_errors,
)

__all__ = [
    "GRID_RESOLUTION_M",
    "ROVER_SIZE_M",
    "SCENARIOS",
    "SlamNavSimulation",
    "cast_ray",
    "distance",
    "drive",
    "plan_grid_path",
    "pose_error",
    "run_apartment_drift_benchmark",
    "run_regressions",
    "summarize_errors",
    "wrap_angle",
]
