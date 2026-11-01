# Inventory — stock firmware

Source: raw partition dumps from the live device (`ssh`/`sftp`), not the OTA
package. The OTA package and the installed firmware share the version string
`V2.1.0.2-R` but are **not byte-identical** (e.g. `/customer`:
`inodes=328, bytes_used=8182916` on the device vs `inodes=329,
bytes_used=8182808` in the OTA container). The live dumps are authoritative.

## Partitions (real, from `/proc/mtd`)

| mtd | name | size | mount | fs |
|---|---|---:|---|---|
| mtd0 | BOOT | 384 KB | — | raw (u-boot) |
| mtd1 | KERNEL | 2.1 MB | — | uImage |
| mtd2 | LOGO | 128 KB | — | raw |
| mtd3 | rootfs | **1.5 MB** | `/` | squashfs 4.0, xz, 215 inodes, `bytes_used=1549414` |
| mtd4 | miservice | 3.2 MB | `/config` | squashfs, xz, 129 inodes |
| mtd5 | customer | 8.0 MB | `/customer` | squashfs, xz, 328 inodes |
| mtd6 | appconfigs | 1.0 MB | `/appconfigs` | jffs2, rw |

Backup (md5): `mtd0 aee67cdf…`, `mtd1 45f9c1f0…`, `mtd2 416e9e1d…`,
`mtd3 a8447000…`, `mtd4 ccc60cb2…`, `mtd5 8cc8835b…`, `mtd6 a893206d…`

## Boot chain (as built)

```
kernel cmdline: console=ttyS0,115200 root=/dev/mtdblock3 rootfstype=squashfs
                ro init=/linuxrc
/linuxrc -> busybox init -> /etc/inittab:
    ::sysinit:/etc/init.d/rcS
    ::sysinit:-/bin/sh          <- login shell on console, reads /etc/profile

/etc/init.d/rcS:
    mount -a                    (proc, sysfs, tmpfs /tmp, tmpfs /var, debugfs)
    mdev -s
    telnetd -l sh               <- root shell, port 23 (applet missing -> no-op)

/etc/profile:                   <- the real boot orchestrator
    PATH / LD_LIBRARY_PATH
    mount -t tmpfs mdev /dev ; mdev -s
    mount squashfs /dev/mtdblock4 /config
    mount squashfs /dev/mtdblock5 /customer
    mount jffs2 mtd:appconfigs /appconfigs
    busybox telnetd&            <- second root shell attempt (applet missing)
    [ -e /appconfigs/pq.ini ]   || reboot
    [ -e /config/sd20x_dongle ] || reboot
    /customer/demo.sh           || reboot

/customer/demo.sh:
    ~20x insmod (mmc/usb/vfat/ntfs -> mhal -> mi_common/mi_sys/mi_ao/mi_gfx/
                 mi_divp/mi_vdec/mi_hdmi/mi_disp -> fbdev), mknod /dev/mi_*
    gpio12, /var/run/*, tmpfs /mnt, /mnt/usb, /mnt/sd
    cp /customer/otasrv /tmp; chmod 777; ./otasrv &
    cd /customer; ./sd20x_manage &
```

Boa (port 80) is **not** started by any rootfs script — it comes from
`sd20x_manage` or `otasrv` (not yet pinned down).

If `/etc/profile` never runs, the box is dead (no `/config`, `/customer`,
`/appconfigs` mounts, no modules, no UI) but it does not hang.

## Rootfs contents

29 regular files, 164 symlinks, ~4.2 MB extracted, compressed to 1.5 MB.

- `bin/busybox` — the only utility binary (glibc 2.28 dynamic)
- `bin/WifiTest_iw` — vendor RF/WiFi test tool
- `etc/fw_printenv` (+ `fw_setenv` symlink is **missing**) reading
  `/dev/mtd0 0x5F000 0x1000 0x1000`
- glibc 2.28: `ld`, `libc`, `libm`, `libpthread`, `libdl`, `librt`, `libcrypt`,
  `libresolv`, `libgcc_s`, `libstdc++.so.6.0.25`
- `lib/libcam_os_wrapper.so`, `lib/libcam_fs_wrapper.so` (vendor wrappers)
- `lib/libpcprofile.so` (leftover), `lib/newlink.txt` (0 bytes),
  `lib/modules/4.9.84` (broken symlink)

### Busybox applets — present

`ash sh mount umount mdev insmod lsmod ifconfig udhcpc ip route init reboot
poweroff halt devmem makedevs md5sum base64 gunzip awk grep sed find ps kill
cp mv ln rm mkdir ls cat vi sleep ln stat df du free top cut sort head tail
xargs timeout nohup inetd udhcpd ntpd nslookup ...`

### Busybox applets — **missing**

`telnetd`, `httpd`, `wget`/`tftp`/`ftpget`, `nc`, `su`, `login`, `getty`,
`passwd`/`adduser`, `crond`, `gzip`, `tar`, `chroot`, `modprobe`, `syslogd`,
`sshd`/`dropbear`, `hexdump`, `strace`.

`inetd` exists but `/etc/inetd.conf` does not — dead applet unless we create
one (which is exactly what payload v5 does).

## Vendor backdoors / debug paths

| # | finding | evidence |
|---|---|---|
| B1 | `telnetd -l sh` in `/etc/init.d/rcS` — root shell without login (applet missing in this build, so currently inert) | rcS:12 |
| B2 | `busybox telnetd&` in `/etc/profile` — same intent | profile:21 |
| B3 | fixed root DES hash `qdGt8T0XjiWXI` in `/etc/passwd` | passwd:1 |
| B4 | `fw_printenv` + `fw_env.config` → U-Boot env readable | etc/ |
| B5 | `bin/WifiTest_iw` — vendor RF test tool | file |
| B6 | `ulimit -c unlimited` in profile (core dumps) | profile:5 |
| B7 | `/sbin/devmem` + `makedevs` — raw memory access as root | bin list |
| B8 | `otasrv` copied to `/tmp` and `chmod 777` | demo.sh:97-100 |
| B9 | console root shell without login (`::sysinit:-/bin/sh`) | inittab:2 |

No SSH keys, no certificates (`*.pem/crt/key` = 0 hits), no `/etc/inetd.conf`,
no extra init scripts.

## Keep / drop for a custom image

**Keep:** busybox + the mount/insmod/mdev/network applets, glibc 2.28 set,
`libstdc++`, `libcam_*_wrapper.so`, `/etc/{inittab,rcS,profile,fstab,mdev.conf,
passwd,group,resolv.conf}`, the mount-point skeleton.

**Drop:** the two telnetd lines, `WifiTest_iw` (if nothing calls it),
`libpcprofile.so`, `/etc/passwd-`, `udhcpc2.script`, pre-populated `/dev`,
`/vendor`, `/home`, and the unused applets (VT family, beep, dc, nuke,
uuencode/uudecode, bootchartd, lsscsi).

**Add:** dropbear + sftp-server (static, key-only), started from `/etc/profile`
(replacing the telnetd lines), and a fixed root hash or none at all.

## Constraints

- rootfs partition is 1.5 MB; the rebuilt squashfs must fit
  (original: xz, block 128 KiB, 215 inodes, `bytes_used=1549414`).
- `/appconfigs/pq.ini` and `/config/sd20x_dongle` must exist or the boot loops.
- `/tmp` must stay tmpfs 0777 (`otasrv` is `chmod 777`).
- `/customer/lib` ships no libc — the rootfs glibc is needed by every vendor
  binary.
