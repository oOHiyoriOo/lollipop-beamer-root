# 01 - Device and firmware overview

This document describes the target device and the parts of its firmware that
were recovered. It is the context for the two audit documents: the web UI
(`02-boa-audit.md`) and the OTA root path (`03-ota-root.md`).

## Identity

| Property | Value |
|---|---|
| SoC | SigmaStar SD203 |
| Model string | `sd203_6710_yro8__SSW105AT` |
| Firmware | `V2.1.0.2-R` |
| Firmware config fields | `suffix_version=V2`, `major_version=1`, `middle_version=0`, `minor_version=2`, `small_version=R` |
| Web UI | `Boa/0.94.13` |
| Reference device | `10.42.0.31`, MAC `d0:7c:b2:17:f7:46` |
| Reference lab host | `10.42.0.1` |

The model string and version come from the device's own OTA configuration.
The retail product name is not asserted here; the web UI identifies itself as
`Lollipop`.

## Firmware container

The vendor OTA package is a gzip file (`sd203ota.bin.gz`). After
decompression, `sd203ota.bin` is a flat bundle: a script header followed by
bootloader, kernel and two SquashFS images. It is not a full flash dump.

| Region | Offset in `sd203ota.bin` | Size | Notes |
|---|---:|---:|---|
| U-Boot | 198206 | 186412 | bootloader |
| Linux uImage | 390994 | 2134780 | kernel |
| SquashFS 1 | 2558586 | 2896618 | 130 inodes, xz, mounted as `/config` |
| SquashFS 2 | 5458830 | 8182808 | 329 inodes, xz, mounted as `/customer` |

Offsets are byte offsets in the decompressed container. `tools/extract_fw.py`
uses the magic values and the size fields inside each image to carve these
regions.

The root filesystem was not recovered from this bundle, so its exact content
and the on-device MTD partition layout remain unknown. Anything that depends
on a file outside `/config` and `/customer` has to be verified on the device.

## Runtime layout

The recovered `/customer` image contains the complete web UI: the Boa binary,
`boa.conf`, `mime.types`, the 24 CGI programs, `otasrv`, `libsd20x_msrv.so`
and the other shared libraries. The live webroot was compared byte for byte
with this image:

| File | Live size | Firmware size | Result |
|---|---:|---:|---|
| `boa.conf` | 352 | 352 | identical |
| `mime.types` | 22275 | 22275 | identical |
| `css/main.css` | 7495 | 7495 | identical |
| `index.html` | 158 | 158 | identical |

That makes the static firmware analysis directly applicable to the running
device for these files.

The web server configuration is small:

```text
Port 80
User 0
Group 0
DocumentRoot /customer/res/boa
ScriptAlias /cgi-bin/ /customer/res/boa/cgi-bin/
DirectoryIndex index.html
MimeTypes /customer/res/boa/mime.types
```

There is no `DirectoryMaker` and no `Alias` in this configuration. The
`DocumentRoot` is the vendor webroot, not the root filesystem.

Boa is the only service this project needed to talk to. The OTA client is
`otasrv` together with `libsd20x_msrv.so`; it is started on demand and is
described in `03-ota-root.md`.

## Root filesystem constraints

The root filesystem is minimal. In the tested build `busybox` existed, but
`telnetd`, `nc`, `httpd` and `wget` were not usable. Payloads therefore cannot
assume those tools. The vendor Boa binary is present and is the reliable way
to expose files after a payload has run.

## Trust model

The web UI was audited for request-controlled shell sinks and path traversal;
no such sink was found (see `02-boa-audit.md`). The OTA client is different:
it downloads three files over plain HTTP, checks only MD5 values supplied by
the same server, and executes the downloaded `otaunpack` as root. That is the
root path documented in `03-ota-root.md`.

## Unknowns

- The MTD partition layout and the root filesystem image were not recovered.
- The exact set of vendor daemons and their privileges beyond Boa is unknown.
- The process credentials of the CGI children are inferred from `boa.conf`
  (`User 0`, `Group 0`), not observed directly.
