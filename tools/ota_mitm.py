#!/usr/bin/env python3
"""Userspace TCP responder for the SD203 beamer's OTA connection.

The device downloads its OTA files over plain HTTP from the host stored in
``ota_host_en`` / ``ota_host_cn``.  If this machine is the device's L2 next hop
(typically because it runs the access point), the device's packets arrive here
with the vendor IP as destination.  With ``net.ipv4.ip_forward=0`` the kernel
drops them, so we can answer at L2 with a spoofed source address instead of
using NAT or iptables (which are often unavailable in containers).

This script implements the smallest useful TCP endpoint for that job:

* SYN -> SYN-ACK with a random server ISN,
* one segment in flight (stop-and-wait), plain ACKs, no window scaling,
* the HTTP request is answered with a local file,
* FIN is only sent after the whole response has been transmitted,
* unacknowledged data and FINs are retransmitted,
* duplicate request data is ACKed again instead of being appended twice.

It is deliberately not a general TCP stack.  ``--root`` must contain the
files the device asks for by basename (``config.txt``, ``sd203ota.bin.gz``,
``otaunpack``); exact relative paths are served too when they exist.  Unknown
paths get a 404 with an empty body.

Requires scapy and root (AF_PACKET raw sockets)::

    sudo python3 tools/ota_mitm.py --iface wlan0 --client 10.42.0.31 \\
        --server 43.254.2.156 --root ./lure

The MAC of the local interface is read from
``/sys/class/net/<iface>/address``.  The client MAC defaults to the reference
device and can be overridden with ``--client-mac``.

Safety: this only answers the device's own OTA requests.  It does not forward
anything and does not contact the vendor.  Keep ``ip_forward=0`` for the whole
run, otherwise the kernel may race this responder and leak the device's
request to the real OTA host.
"""
import argparse
import os
import random
import re
import threading
import time

from scapy.all import Ether, IP, Raw, TCP, sendp, sniff

MAC_RE = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")

CFG = {}
FLOWS = {}
LOCK = threading.Lock()

RETRANSMIT_AFTER = 0.6
MAX_RETRIES = 10
CLEAN_AFTER = 120.0


def log(msg):
    """Write one timestamped line to stdout and, if configured, to --log."""
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    path = CFG.get("log")
    if path:
        with open(path, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(line + "\n")
    print(line, flush=True)


def read_mac(iface):
    """Return the MAC address of a local interface from sysfs."""
    path = "/sys/class/net/%s/address" % iface
    try:
        with open(path, "r", encoding="ascii") as fh:
            mac = fh.read().strip().lower()
    except OSError as exc:
        raise SystemExit("cannot read %s: %s" % (path, exc))
    if not MAC_RE.match(mac):
        raise SystemExit("unexpected MAC address in %s: %r" % (path, mac))
    return mac


def normalise_mac(value):
    """Lowercase and validate a MAC address from the command line."""
    mac = value.strip().lower()
    if not MAC_RE.match(mac):
        raise SystemExit("invalid MAC address: %r" % value)
    return mac


def send_tcp(dport, seq, ack, flags, payload=b"", win=64240):
    """Send one Ethernet/IP/TCP frame to the device."""
    pkt = (Ether(src=CFG["our_mac"], dst=CFG["client_mac"]) /
           IP(src=CFG["server"], dst=CFG["client"]) /
           TCP(sport=CFG["port"], dport=dport, seq=seq, ack=ack, flags=flags,
               window=win) /
           Raw(load=payload))
    sendp(pkt, iface=CFG["iface"], verbose=False)


def resolve(path):
    """Map a request path to bytes below --root, or return None."""
    root = CFG["root"]
    rel = path.split("?", 1)[0].lstrip("/")
    candidates = []
    if rel:
        candidates.append(os.path.realpath(os.path.join(root, rel)))
    base = os.path.basename(rel.rstrip("/"))
    if base:
        candidates.append(os.path.realpath(os.path.join(root, base)))
    for full in candidates:
        try:
            if os.path.commonpath([root, full]) != root:
                continue
        except ValueError:
            continue
        if os.path.isfile(full):
            with open(full, "rb") as fh:
                return fh.read()
    return None


def http_response(path):
    """Build a complete HTTP/1.0 response for one request path."""
    body = resolve(path)
    if body is None:
        log("404 %s" % path)
        return (b"HTTP/1.0 404 Not Found\r\n"
                b"Content-Length: 0\r\n"
                b"Connection: close\r\n\r\n")
    log("200 %s (%d bytes)" % (path, len(body)))
    head = ("HTTP/1.0 200 OK\r\n"
            "Content-Type: application/octet-stream\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n\r\n" % len(body)).encode("ascii")
    return head + body


def pump(port):
    """Send the next segment if the previous one was acknowledged."""
    f = FLOWS.get(port)
    if not f or f["state"] != "est":
        return
    if f["acked"] < f["s_next"]:
        return  # still waiting for the ACK of the last segment
    if f["off"] < len(f["resp"]):
        chunk = f["resp"][f["off"]:f["off"] + CFG["mss"]]
        send_tcp(port, f["s_next"], f["c_next"], "PA", chunk)
        f["last"] = (f["s_next"], f["c_next"], "PA", chunk)
        f["last_t"] = time.time()
        f["s_next"] += len(chunk)
        f["off"] += len(chunk)
        log("-> data %d bytes (off=%d/%d)" % (len(chunk), f["off"], len(f["resp"])))
    elif f["resp"] and not f["fin"]:
        send_tcp(port, f["s_next"], f["c_next"], "FA")
        f["last"] = (f["s_next"], f["c_next"], "FA", b"")
        f["last_t"] = time.time()
        f["fin"] = True
        f["s_next"] += 1
        log("-> FIN")


def handler(pkt):
    """Handle one captured frame from the device."""
    if IP not in pkt or TCP not in pkt:
        return
    ip, tcp = pkt[IP], pkt[TCP]
    if ip.src != CFG["client"] or ip.dst != CFG["server"]:
        return
    if int(tcp.dport) != CFG["port"]:
        return

    port = int(tcp.sport)
    fl = int(tcp.flags)
    with LOCK:
        if fl & 0x02 and not fl & 0x10:  # SYN without ACK
            f = FLOWS.get(port)
            if f and f["c_isn"] == int(tcp.seq) and not f["req"] and not f["resp"]:
                send_tcp(port, f["s_isn"], f["c_next"], "SA")
                log("SYN retransmit from %s:%d -> SYN-ACK" % (CFG["client"], port))
                return
            s_isn = random.randint(1, 0x7FFFFFFF)
            f = {
                "c_isn": int(tcp.seq),
                "s_isn": s_isn,
                "c_next": int(tcp.seq) + 1,
                "s_next": s_isn + 1,
                "acked": s_isn + 1,
                "req": b"",
                "resp": b"",
                "off": 0,
                "state": "est",
                "fin": False,
                "last": None,
                "last_t": 0.0,
                "retries": 0,
            }
            FLOWS[port] = f
            send_tcp(port, s_isn, f["c_next"], "SA")
            log("SYN from %s:%d -> SYN-ACK" % (CFG["client"], port))
            return

        f = FLOWS.get(port)
        if not f:
            return

        if fl & 0x01:  # FIN from the device
            f["c_next"] = int(tcp.seq) + 1
            if f["resp"] and f["off"] >= len(f["resp"]) and not f["fin"]:
                send_tcp(port, f["s_next"], f["c_next"], "FA")
                f["fin"] = True
                f["s_next"] += 1
                log("client FIN -> our FIN")
            else:
                send_tcp(port, f["s_next"], f["c_next"], "A")
                log("client FIN (ACK only, off=%d/%d)" % (f["off"], len(f["resp"])))
                if f["resp"] and f["off"] < len(f["resp"]):
                    pump(port)
            f["state"] = "closed" if f["fin"] else f["state"]
            return

        ack = int(tcp.ack)
        if ack > f["acked"]:
            f["acked"] = ack
            f["retries"] = 0

        payload = bytes(pkt[Raw].load) if Raw in pkt else b""
        if payload:
            seq = int(tcp.seq)
            if seq == f["c_next"]:
                f["req"] += payload
                f["c_next"] += len(payload)
                send_tcp(port, f["s_next"], f["c_next"], "A")
                log("<- %d bytes (request=%d)" % (len(payload), len(f["req"])))
            elif seq < f["c_next"]:
                send_tcp(port, f["s_next"], f["c_next"], "A")
                log("<- duplicate %d bytes, ACK again" % len(payload))
            else:
                log("<- out-of-order seq=%d expected=%d, ignored" % (seq, f["c_next"]))

            if b"\r\n\r\n" in f["req"] and not f["resp"]:
                line = f["req"].split(b"\r\n", 1)[0].decode("latin1", "replace")
                parts = line.split(" ")
                path = parts[1] if len(parts) >= 2 else "/"
                log("HTTP %s" % line)
                f["resp"] = http_response(path)

        pump(port)


def reaper():
    """Retransmit unacknowledged segments and drop old closed flows."""
    while True:
        time.sleep(0.4)
        with LOCK:
            now = time.time()
            for port, f in list(FLOWS.items()):
                if f["state"] == "closed" and now - f["last_t"] > CLEAN_AFTER:
                    del FLOWS[port]
                    continue
                if not f["last"] or f["state"] != "est":
                    continue
                if now - f["last_t"] <= RETRANSMIT_AFTER:
                    continue
                if f["acked"] >= f["s_next"]:
                    continue
                if f["retries"] >= MAX_RETRIES:
                    log("giving up on port %d after %d retransmits" % (port, MAX_RETRIES))
                    f["last"] = None
                    continue
                seq, ack, flags, pl = f["last"]
                send_tcp(port, seq, ack, flags, pl)
                f["retries"] += 1
                f["last_t"] = now
                log("retransmit %s #%d on port %d" % (flags, f["retries"], port))


def ip_forward_state():
    """Return the current ip_forward value, or None if it cannot be read."""
    try:
        with open("/proc/sys/net/ipv4/ip_forward", "r", encoding="ascii") as fh:
            return fh.read().strip()
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iface", required=True,
                    help="interface that sees the device (for example wlan0)")
    ap.add_argument("--client", default="10.42.0.31",
                    help="device IP (default: 10.42.0.31, the reference unit)")
    ap.add_argument("--client-mac", default="d0:7c:b2:17:f7:46",
                    help="device MAC (default: MAC of the reference unit)")
    ap.add_argument("--server", default="43.254.2.156",
                    help="spoofed OTA server IP (default: the vendor host)")
    ap.add_argument("--port", type=int, default=8000,
                    help="OTA server TCP port (default: 8000)")
    ap.add_argument("--root", default="lure",
                    help="directory with config.txt, sd203ota.bin.gz, otaunpack")
    ap.add_argument("--mss", type=int, default=1200,
                    help="TCP payload size per segment (default: 1200)")
    ap.add_argument("--log", default=None,
                    help="log file (default: <root>/mitm.log)")
    args = ap.parse_args()

    if not 1 <= args.port <= 65535:
        ap.error("--port must be between 1 and 65535")
    if not 64 <= args.mss <= 1460:
        ap.error("--mss must be between 64 and 1460")

    root = os.path.realpath(os.path.abspath(args.root))
    if not os.path.isdir(root):
        ap.error("--root is not a directory: %s" % root)

    log_path = os.path.abspath(args.log) if args.log else os.path.join(root, "mitm.log")
    log_dir = os.path.dirname(log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    CFG.update({
        "iface": args.iface,
        "our_mac": read_mac(args.iface),
        "client": args.client,
        "client_mac": normalise_mac(args.client_mac),
        "server": args.server,
        "port": args.port,
        "root": root,
        "mss": args.mss,
        "log": log_path,
    })

    if not os.path.isfile(os.path.join(root, "config.txt")):
        log("warning: %s/config.txt is missing, the device will receive 404" % root)

    fwd = ip_forward_state()
    if fwd not in (None, "0"):
        log("WARNING: ip_forward=%s; the kernel may forward OTA packets to the "
            "real vendor and race this responder. Set net.ipv4.ip_forward=0." % fwd)

    log("MITM up: iface=%s client=%s (%s) server=%s:%d root=%s mss=%d" % (
        CFG["iface"], CFG["client"], CFG["client_mac"], CFG["server"],
        CFG["port"], root, CFG["mss"]))

    threading.Thread(target=reaper, daemon=True).start()
    try:
        sniff(iface=args.iface, prn=handler, store=False)
    except PermissionError:
        raise SystemExit("raw sockets need root; run this with sudo")
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
