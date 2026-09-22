# Rover dashboard

Mission-control UI that runs on **another Tailscale device** and talks to this Pi by **FQDN**.

| Concern | Backend | URL (default) |
|---------|---------|-----------------|
| Video / mic / talk | pi-server MediaMTX WebRTC | `https://<VITE_PI_FQDN>:8889/{cam,mic,talk}/…` |
| Drive / gimbal / status / photo | MentorPi `web_car` → ROS | `http://<VITE_PI_FQDN>:5000/api/…` |

Set the MagicDNS name once:

```bash
# control-dashboard/.env.local
VITE_PI_FQDN=raspberrypi.tail9d0237.ts.net
```

## Run (on your laptop / other device)

```bash
cd control-dashboard
npm install
npm run dev
```

Requires Tailscale reachability to the Pi FQDN, MediaMTX HTTPS certs for WebRTC, and `web_car.service` for drive.
