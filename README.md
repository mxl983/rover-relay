# Mango Rover

**A home rover, vibe-coded end to end.**

We had a three-week trip coming up. Our cat, **Mango**, would be home alone for five days before someone could pick her up, and we wanted a way to keep an eye on her. So we tried it as a weekend **vibe-coding** project: how far can you get if you just build? Farther than we expected. The result is impressive, and still a little surprising. A full rover you can steer from across the city or the other side of the planet.

- **Mission HUD** over live video: battery, temps, latency, Wi‑Fi, distance, charging, and more
- **Drive your way**: keyboard, on-screen sticks, or Xbox (USB / Bluetooth)
- **Pan-tilt gimbal** look, night vision, focus, resolution, and hi-res still capture
- **Two-way audio**: hear the rover, talk back
- **Backup camera**: ESP32 MJPEG through the relay
- **LiDAR minimap**
- **Drive assist**: obstacle-aware braking with a live collision HUD
- **Headlight, laser, treat feeder, meow**: one-tap from the cockpit
- **Quiet / Sport**, power-saving idle shutdown, hard power On/Off
- **Tailscale HTTPS relay**: telemetry, WebRTC video/audio, long-haul reach
- **Client distance**: how far you are from the rover site, in the HUD
- **Remote docking**: manual drive onto the charger from abroad so it can run for months before the pack itself wears out
- Same-city drive under ideal Wi‑Fi: **sub‑20 ms** latency; long-haul proven at **~380 ms** from the other side of the planet

<p align="center">
  <img src="assets/rover-front.png" alt="Mango Mate rover, front view on a tiled floor" width="520" />
</p>

---

## Built on the floor. Wired by hand.

Stacked aluminum decks, brass standoffs, and a Pi in the middle. Every deck is part of the product.

- **Aluminum chassis** with stacked decks and brass standoffs
- **Raspberry Pi** onboard: drive, camera, voice, sensors, WebSocket control
- **Pan-tilt camera gimbal** up front
- **LiDAR** scan path + ROS 2 stack over the tailnet
- **ESP32 edge cam**: backup stream plus ambient temp / pressure
- **Headlight** (USB power), **laser pointer**, **pet feeder**, rover **meow**
- **IMU** on the link (yaw hints for mapping)
- Telemetry offloaded to the relay (less SD wear on the Pi)

<p align="center">
  <img src="assets/rover-angle.png" alt="Mango Mate rover, three-quarter view showing camera gimbal and chassis" width="480" />
</p>

---

## A cockpit that feels present

Same HUD, controlled from the other side of the planet. **~10,000 km** out, **~380 ms** on the link, nose-to-nose with the house cat over live video. Drive, look, check in, and **dock for a charge** from abroad so service cycles stretch for months before the battery itself degrades.

<p align="center">
  <img src="assets/cockpit.png" alt="Mango Mate cockpit, close-up of a sleeping cat over live video at ~380 ms latency from the other side of the planet" width="720" />
</p>

---

## Out of the country. Still on duty.

Controlled from abroad. Roll into the living room, drop treats, and watch from the rover’s eye.

<p align="center">
  <img src="assets/operation.gif" alt="Mango rover in the living room, remote treat drop and watch while controlled from out of the country" width="720" />
</p>

---

## One system, three layers

| Layer | What it does |
| --- | --- |
| **Onboard** | Raspberry Pi: drive, camera, voice, sensors |
| **Edge cam** | ESP32: backup stream and environment sense |
| **Relay** | Tailscale HTTPS hub: telemetry, distance, long-haul reach |

Repos: [mxl983/rover](https://github.com/mxl983/rover) (onboard) · [relay setup docs](docs/RELAY.md)
