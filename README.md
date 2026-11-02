# Bravia Remote

A phone-friendly web remote for older, non-Android Sony BRAVIA TVs (2013–2015 models such as the KDL-70W840B), whose built-in app store has been shut down. It runs as a tiny Docker container (pure Python, no dependencies) and talks to the TV over its built-in IP control API.

- D-pad, Home, Back, Options, Info, volume and channel rockers, media keys, number pad
- Power off, and power on using wake-on-LAN
- Input picker that shows your HDMI port names
- Live status: power state, current input, volume
- PIN pairing with automatic cookie renewal (pre-shared key also supported)

## Install on Unraid

1. **Docker** tab → **Add Container** → paste this into **Template**:
   ```
   https://raw.githubusercontent.com/GrimIock-v1/bravia-remote/main/unraid/bravia-remote.xml
   ```
   Or save `unraid/bravia-remote.xml` to `/boot/config/plugins/dockerMan/templates-user/` and pick it from the template list.
2. Check the TV IP and MAC address, then click **Apply**.
3. Open `http://<unraid-ip>:8787` on your phone and add it to your home screen.
4. Tap **Show PIN on TV**, then enter the 4-digit code the TV displays.

The template uses **host networking** so that wake-on-LAN broadcasts can reach the TV.

## Docker / Compose

```yaml
services:
  bravia-remote:
    image: ghcr.io/grimiock-v1/bravia-remote:latest
    network_mode: host
    restart: unless-stopped
    environment:
      TV_IP: 10.0.0.61
      TV_MAC: "00:71:cc:4b:a0:63"
      PORT: 8787
    volumes:
      - ./config:/config
```

| Variable | Default | Purpose |
|---|---|---|
| `TV_IP` | `10.0.0.61` | TV address (give it a DHCP reservation) |
| `TV_MAC` | `00:71:cc:4b:a0:63` | For wake-on-LAN |
| `PORT` | `8787` | Web port |
| `PUID` / `PGID` | `99` / `100` | Owner of files in `/config` |

## TV settings

If no PIN appears or power-on doesn't work, check these on the TV:
- **Home Network Setup → Remote device / Renderer**: turn on remote control by network devices.
- **Remote Start**: turn it **on** so the TV keeps listening on the network while in standby.
- If you set **IP Control → Authentication** to a pre-shared key, choose "Use a pre-shared key instead" in the pairing sheet.

## Run without Docker

```
python3 app/tv_remote.py --tv 10.0.0.61
```

## Development

`python3 tests/test_remote.py` runs the app against a simulated BRAVIA. On every push, CI runs those tests, builds the image, starts the real container the way Unraid would (host network, root-owned appdata) and runs the same suite against it. Only then does it publish `amd64` and `arm64` images to GHCR.

Anyone on your LAN who can open the page can control the TV, so don't expose the port to the internet.
