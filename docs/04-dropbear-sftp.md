# 04 - Dropbear + SFTP as root

This is the end state of the run: a key-only Dropbear SSH server with SFTP,
running as root on the beamer, started automatically from the OTA payload.

Both binaries live in `/appconfigs` (jffs2, writable, **no** `noexec`), which
is the only persistent writable location on the device:

| file | size | notes |
|---|---:|---|
| `dropbear` | 375 020 B | 2026.94, static musl, armv7, `SFTPSERVER_PATH="/appconfigs/sftp-server"` |
| `sftp-server` | 404 668 B | OpenSSH 9.9p2, static, linked against zlib 1.3.2 + OpenSSL 3.5.9 |
| `dropbear_ed25519_host_key` | 83 B | generated on device with `dropbearkey -t ed25519` |
| `authorized_keys` | ~81 B per key | copied to `/home/root/.ssh/authorized_keys` |
| `home/root/.ssh/` | dir | bind-mounted over `/home` |

The partition is only **1.0 MB**, so do not keep the base64 temp files:
upload with per-chunk decode (`base64 -d >> file`), and delete
`dropbearkey` after the host key exists if space runs short.

## Building

Everything cross-compiles with zig 0.14.1 and no system packages:

```sh
ZIG=/path/to/zig/zig
CC="$ZIG cc -target arm-linux-musleabihf -mcpu=cortex_a7 -O2"

# dropbear (no sftp binary inside - only the path)
CFLAGS="-O2 -DSFTPSERVER_PATH=\"/appconfigs/sftp-server\"" \
  ./configure --host=arm-linux-musleabihf \
      --disable-zlib --disable-syslog --disable-lastlog \
      --disable-utmp --disable-utmpx --disable-wtmp --disable-wtmpx \
      --disable-pututline --disable-pututxline --enable-static
make PROGRAMS="dropbear dropbearkey" -j4

# sftp-server: zlib + openssl + openssh-portable
./configure --host=arm-linux-musleabihf \
    --with-zlib=$ZLIB --with-ssl-dir=$OPENSSL \
    --without-pam --without-shadow --without-selinux --without-audit \
    --disable-etc-default-login --without-openssl-header-check \
    --without-pie --with-ldflags="-static"
make sftp-server
```

Dropbear itself ships **no** `sftp-server`; `default_options.h` only contains
`SFTPSERVER_PATH "/usr/libexec/sftp-server"`. Since `/usr` is read-only
squashfs on the device, the path is overridden at compile time via
`-DSFTPSERVER_PATH` (the header is wrapped by `ifndef_wrapper.sh`, so `-D`
wins).

## Deploying

1. Upload both binaries to `/appconfigs` (base64 over the shell, per-chunk).
2. Generate the host key: `dropbearkey -t ed25519 -f /appconfigs/dropbear_ed25519_host_key`.
3. Put the public key into `/appconfigs/authorized_keys`.
4. `mount --bind /appconfigs/home /home` and place the file at
   `/home/root/.ssh/authorized_keys` (root's home comes from `/etc/passwd`
   and lives on the read-only rootfs).
5. Start:
   ```sh
   /appconfigs/dropbear -r /appconfigs/dropbear_ed25519_host_key \
       -p 22 -P /var/run/dropbear.pid -g -s
   ```
   `-g` and `-s` disable password authentication entirely.

## Autostart

There is **no writable boot hook**: `/`, `/config` and `/customer` are
squashfs read-only, and `/etc/profile`, `/etc/init.d/rcS` and
`/customer/demo.sh` are all on those filesystems. The only writable location
is `/appconfigs`.

The working approach is the OTA client itself. `/appconfigs/lollipop.conf`
contains `ota_host_cn` / `ota_host_en`; point them at the lure:

```ini
ota_host_cn=10.42.0.1:8000
ota_host_en=10.42.0.1:8000
```

On every boot the device then fetches `config.txt`, `sd203ota.bin.gz` and
`otaunpack` from that host (observed user agent
`stagefright/1.1 (Linux;Android 6.0)`), and the payload runs as root. The
payload must **block** at the end, otherwise `otasrv` reboots the device.

Caveat: this requires the device to boot on the hotspot with the lure
running. A truly standalone setup would need a modified rootfs flashed to
`/dev/mtdblock3` (destructive, not done here).
