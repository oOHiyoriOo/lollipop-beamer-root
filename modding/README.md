# Modding: building a custom image

Goal: replace the vendor firmware with a minimal, controlled image that keeps
what is needed (video, WiFi, web UI) and drops what is not, with a root shell
that starts on its own.

Status: **inventory phase**. Nothing has been flashed.

---

## What we know about the device

| partition | device | fs | mount | writable |
|---|---|---|---|---|
| U-Boot | `/dev/mtd0` | raw | — | only via `fw_setenv` |
| kernel | `/dev/mtd2` (uImage) | raw | — | flash only |
| rootfs | `/dev/mtdblock3` | squashfs | `/` | no |
| config | `/dev/mtdblock4` | squashfs | `/config` | no |
| customer | `/dev/mtdblock5` | squashfs | `/customer` | no |
| appconfigs | `mtd:appconfigs` | jffs2 | `/appconfigs` | **yes, 1.0 MB** |

Boot chain (observed live):

```
U-Boot -> kernel (console=ttyS0,115200 root=/dev/mtdblock3 rootfstype=squashfs
                  ro init=/linuxrc)
  /linuxrc = busybox init
    /etc/inittab:  ::sysinit:/etc/init.d/rcS
                   ::sysinit:-/bin/sh        <- login shell, reads /etc/profile
    /etc/init.d/rcS: mount -a, mdev, telnetd  (applet missing -> fails)
    /etc/profile:    mounts /config, /customer, /appconfigs; starts
                     /customer/demo.sh
    /customer/demo.sh: insmod, /tmp/otasrv &, /customer/sd20x_manage &
```

`/etc/profile` is also re-executed on every login shell — the reason a plain
`ssh root@...` replays the whole boot sequence (and resets WiFi). Use
`ssh -T` for automation.

## Known services

| process | path | role |
|---|---|---|
| `sd20x_manage` | `/customer/sd20x_manage` | main daemon: UI, WiFi state machine, BT, OTA client (`libsd20x_msrv.so`) |
| `otasrv` | copied to `/tmp/otasrv` by `demo.sh` | IPC command server; runs `otaunpack` on `IPC_OTA_START` |
| `boa` | `/customer/boa` | web UI on :80, `DocumentRoot /customer/res/boa`, runs as `User 0` |
| `wpa_supplicant` | `/config/wifi/wpa_supplicant` | WiFi |
| `udhcpc` | busybox | DHCP client via `/etc/init.d/udhcpc.script` |
| `inetd` | busybox | available, not enabled by the vendor |
| `telnetd` | — | referenced by boot scripts but **not compiled into busybox** |

## Inventory (to be filled)

- [ ] rootfs `/` — `/etc`, `/bin`, `/sbin`, `/usr`, `/lib` dump + file list
- [ ] `/config` — panel/LCM tuning, WiFi blobs, modules
- [ ] `/customer` — UI assets, CGIs, libs
- [ ] `/appconfigs` — runtime config (small, jffs2)
- [ ] kernel + U-Boot images (uImage headers, cmdline, env)
- [ ] vendor test / debug / backdoor paths
- [ ] what is needed for: video playback, HDMI, WiFi STA/AP, AirPlay/Miracast, DLNA, web UI

## Open questions

1. **Watchdog**: the device resets roughly every 5 minutes while our payload
   blocks. Likely `/dev/watchdog` opened by the OTA client. v7 of the payload
   feeds it; needs verification.
2. **Recovery**: the vendor USB update path (`usbautoupgrade`, `UpgradeImage`,
   `RY_O8__SSW105AT.bin` in `/appconfigs` / U-Boot env) is the only known
   recovery if a flashed rootfs does not boot. **Verify this before flashing
   anything.** If U-Boot itself is untouched, a bad rootfs should still be
   recoverable through it.
3. **Size**: the rootfs partition is small; a rebuilt image must fit the same
   mtd size. Determine the exact size and the squashfs parameters
   (block size, compression, inode count) from the original image.
4. **Signatures**: `otaunpack` verifies CRC32 per image and the OTA config
   carries MD5 only. Nothing indicates a signature check, but this must be
   confirmed before relying on it.

## Plan

1. Dump every mtd partition over SSH/SFTP (raw images).
2. Extract and inventory the rootfs.
3. Decide what to keep (video/HDMI/WiFi/UI) and what to drop.
4. Add: dropbear + sftp-server, static, key-only; a boot hook that starts it.
5. Rebuild the rootfs squashfs with the same parameters, repack the vendor
   container with correct CRC32/MD5, flash.
6. Keep the stock images for recovery.
