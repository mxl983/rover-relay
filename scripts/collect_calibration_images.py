#!/usr/bin/env python3
"""Collect and verify checkerboard images for camera calibration.

This is Part 1 only: it captures verified images. It intentionally does not
call cv2.calibrateCamera() or calculate a camera matrix.

Before running this script on a Raspberry Pi Camera Module 3, disable
autofocus and lock the lens position. For example, inspect the camera controls
with:

    v4l2-ctl --device=/dev/video0 --list-ctrls

Then use the appropriate control exposed by your camera driver, commonly:

    v4l2-ctl --device=/dev/video0 --set-ctrl=focus_auto=0
    v4l2-ctl --device=/dev/video0 --set-ctrl=focus_absolute=<fixed_value>

The exact control names/range can differ by libcamera/V4L2 setup. Confirm
focus_auto is disabled before collecting images; autofocus movement invalidates
the calibration.

The checkerboard should be displayed flat on the monitor without browser/UI
scaling. Move it through the image, including the corners and different
distances/tilts, and collect approximately 20-30 successful frames.
"""

from __future__ import annotations

from pathlib import Path

try:
    import cv2
except ModuleNotFoundError as exc:
    raise SystemExit(
        "OpenCV is required. On Raspberry Pi OS, install it with:\n"
        "  sudo apt update && sudo apt install -y python3-opencv v4l-utils\n"
        "Then run this script again with the same python3 interpreter."
    ) from exc


# Number of INTERNAL checkerboard corners: (columns, rows).
# Change this if the printed/displayed checkerboard is different.
CHESSBOARD_SIZE = (7, 10)

CAMERA_INDEX = 0
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
OUTPUT_DIR = Path("calibration_images")
WINDOW_NAME = "Camera Calibration - press 's' to save, 'q' to quit"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Camera calibration image collector")
    print(f"Checkerboard internal corners: {CHESSBOARD_SIZE[0]} x {CHESSBOARD_SIZE[1]}")
    print(f"Saving verified frames to: {OUTPUT_DIR.resolve()}")
    print("Ensure Pi Camera 3 autofocus is disabled and locked before continuing.")
    print("Press 's' only when corners are visible; press 'q' to quit.")

    # CAP_V4L2 is useful for a V4L2 device on Linux/Raspberry Pi. If the
    # camera backend does not support it, cv2.VideoCapture(CAMERA_INDEX) is
    # the fallback.
    camera = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
    if not camera.isOpened():
        camera.release()
        camera = cv2.VideoCapture(CAMERA_INDEX)

    if not camera.isOpened():
        raise RuntimeError(
            f"Could not open camera index {CAMERA_INDEX}. "
            "Check the device and camera permissions."
        )

    camera.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

    actual_width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"Requested resolution: {FRAME_WIDTH}x{FRAME_HEIGHT}")
    print(f"Camera resolution:   {actual_width}x{actual_height}")

    saved_count = 0
    termination_criteria = (
        cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
        30,
        0.001,
    )
    find_flags = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE

    try:
        while True:
            success, frame = camera.read()
            if not success or frame is None:
                print("Warning: failed to read a frame.", flush=True)
                continue

            # Preserve this original frame. The saved image must not contain
            # the diagnostic corner overlay.
            original_frame = frame.copy()
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            found, corners = cv2.findChessboardCorners(
                gray,
                CHESSBOARD_SIZE,
                find_flags,
            )

            refined_corners = None
            if found and corners is not None:
                refined_corners = cv2.cornerSubPix(
                    gray,
                    corners,
                    winSize=(11, 11),
                    zeroZone=(-1, -1),
                    criteria=termination_criteria,
                )

            display_frame = frame.copy()
            if refined_corners is not None:
                cv2.drawChessboardCorners(
                    display_frame,
                    CHESSBOARD_SIZE,
                    refined_corners,
                    found,
                )
                cv2.putText(
                    display_frame,
                    "Corners found - press 's' to save",
                    (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.9,
                    (0, 220, 0),
                    2,
                    cv2.LINE_AA,
                )
            else:
                cv2.putText(
                    display_frame,
                    "Corners not found",
                    (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.9,
                    (0, 0, 255),
                    2,
                    cv2.LINE_AA,
                )

            cv2.putText(
                display_frame,
                f"Saved: {saved_count} (target 20-30)",
                (20, 78),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (255, 220, 0),
                2,
                cv2.LINE_AA,
            )
            cv2.imshow(WINDOW_NAME, display_frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s") and refined_corners is not None:
                output_path = OUTPUT_DIR / f"calibration_{saved_count + 1:04d}.png"
                if cv2.imwrite(str(output_path), original_frame):
                    saved_count += 1
                    print(
                        f"Captured frame {saved_count}: {output_path}",
                        flush=True,
                    )
                else:
                    print(f"Warning: failed to save {output_path}", flush=True)
    finally:
        camera.release()
        cv2.destroyAllWindows()
        print(f"Finished. Successfully captured {saved_count} frame(s).")


if __name__ == "__main__":
    main()
