#!/bin/sh
# UNVERIFIED TEMPLATE - this payload has NOT been run on the device.
#
# Goal: start a statically linked Dropbear SSH server as root, key-only, with
# SFTP, on port 22, with its PID file in /tmp.
#
# What is missing / unknown:
#   * The dropbear and sftp-server binaries are NOT included in this repo.
#     Cross-compile static ARM binaries (musl) or obtain a matching build.
#   * The rootfs may be read-only; /root and /etc may not be writable.
#   * Dropbear's SFTP subsystem path is a build-time constant.  If the binary
#     was built with an external sftp-server, the server must be placed at the
#     compiled-in path (unknown here).  A build with the built-in SFTP server
#     needs no separate file.
#   * Host key generation needs dropbearkey (or a matching multicall symlink).
#   * The OTA client only downloads `otaunpack`; it does not deliver extra
#     files.  Embed the binaries and your public key in this script (base64
#     or here-doc) if you need a single-file payload.
#   * The vendor client calls waitpid() on this script and reboots afterwards.
#     The final sleep only keeps the SSH window open for HOLD seconds; for
#     persistence you would need to stop otasrv or install an init script,
#     which is out of scope and untested.
#
# Every path below is a placeholder.  Adjust, then test on hardware you own.

# UNVERIFIED: binary locations and names.
DROPBEAR=${DROPBEAR:-/tmp/dropbear}
DROPBEARKEY=${DROPBEARKEY:-/tmp/dropbearkey}
SFTP_SERVER=${SFTP_SERVER:-/tmp/sftp-server}

# UNVERIFIED: host key and authorized_keys location on this rootfs.
HOSTKEY=${HOSTKEY:-/tmp/dropbear_host_key}
AUTH_DIR=${AUTH_DIR:-/root/.ssh}
AUTH_SRC=${AUTH_SRC:-/tmp/authorized_keys}
AUTH_KEYS=$AUTH_DIR/authorized_keys
PIDFILE=${PIDFILE:-/tmp/dropbear.pid}
PORT=${PORT:-22}
HOLD=${HOLD:-540}

LOG=/tmp/dropbear_payload.log
: > "$LOG" 2>/dev/null
exec >>"$LOG" 2>&1

echo "=== DROPBEAR START (UNVERIFIED) ==="

if [ ! -x "$DROPBEAR" ]; then
  echo "missing executable: $DROPBEAR"
  exit 1
fi
if [ ! -x "$DROPBEARKEY" ]; then
  echo "missing executable: $DROPBEARKEY"
  exit 1
fi

# Host key: generate once if it does not exist yet.
if [ ! -f "$HOSTKEY" ]; then
  echo "generating host key in $HOSTKEY"
  "$DROPBEARKEY" -t ed25519 -f "$HOSTKEY" || {
    echo "dropbearkey failed"
    exit 1
  }
fi
chmod 600 "$HOSTKEY" 2>/dev/null

# authorized_keys: copy the public key into the place Dropbear reads for root.
if [ -f "$AUTH_SRC" ]; then
  mkdir -p "$AUTH_DIR" 2>/dev/null
  cp "$AUTH_SRC" "$AUTH_KEYS" 2>/dev/null
  chmod 700 "$AUTH_DIR" 2>/dev/null
  chmod 600 "$AUTH_KEYS" 2>/dev/null
fi
if [ ! -f "$AUTH_KEYS" ]; then
  echo "missing $AUTH_KEYS - key-only login would fail"
  exit 1
fi

# -g: disable password logins for root.
# -s: disable password logins entirely (key-only).
# -r: host key, -p: port, -P: PID file.
# UNVERIFIED: whether this build accepts these flags and the sftp path.
"$DROPBEAR" -g -s -r "$HOSTKEY" -p "$PORT" -P "$PIDFILE"
sleep 2

if [ -s "$PIDFILE" ]; then
  echo "dropbear pid: $(cat "$PIDFILE")"
else
  echo "dropbear did not write $PIDFILE"
fi

# The SFTP subsystem is selected by the client's `subsystem sftp` request.
# Dropbear spawns it from a build-time path; there is no portable runtime
# option to point it at $SFTP_SERVER.  UNVERIFIED: place the binary at the
# path your build expects, or use a build with the built-in server.
echo "sftp-server placeholder: $SFTP_SERVER"

echo "=== DROPBEAR HOLD ${HOLD}s ==="
sleep "$HOLD"

# Do not block forever: otasrv is waiting in waitpid().
echo "=== DROPBEAR END ==="
exit 0
