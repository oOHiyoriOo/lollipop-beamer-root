#!/usr/bin/env python3
"""Download and verify the OTA package for the SD203 Lollipop beamer.

The device's own OTA client fetches exactly these three files over plain
HTTP from a host stored in its configuration (`ota_host_en`):

    <base>/config.txt
    <base>/sd203ota.bin.gz
    <base>/otaunpack

This tool mirrors that, verifies the MD5 values advertised in config.txt and
prints a summary.  It does not talk to the device.

Usage:
    python3 fw_download.py --list
    python3 fw_download.py --out ./fw
    python3 fw_download.py --model sd203_6710_yro8__SSW105AT_1 --out ./fw
"""
import argparse
import hashlib
import os
import re
import sys
import urllib.request

DEFAULT_HOST = "http://43.254.2.156:8000"
DEFAULT_MODEL = "sd203_6710_yro8__SSW105AT_1"
FILES = ("config.txt", "otaunpack", "sd203ota.bin.gz")


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "fw-download/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def list_models(host):
    """Parse the IIS directory listing and return model directories."""
    out = []
    for index in ("/ota/", "/ota/rgl/"):
        try:
            html = fetch(host + index).decode("latin-1", "replace")
        except Exception as exc:
            print("  %-10s %s" % (index, exc))
            continue
        links = re.findall(r'HREF="([^"]+)"', html, re.I)
        dirs = sorted({l for l in links if l.endswith("/") and l != "/ota/"})
        print("%s" % index)
        for d in dirs:
            name = d.rstrip("/").rsplit("/", 1)[-1]
            if name:
                out.append((index, name))
                print("  %s" % d)
    return out


def parse_config(text):
    cfg = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, val = line.split("=", 1)
        cfg[key.strip()] = val.strip()
    return cfg


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", default="fw")
    ap.add_argument("--list", action="store_true", help="list available model directories")
    ap.add_argument("--skip-verify", action="store_true")
    args = ap.parse_args()

    base = args.host.rstrip("/")
    if args.list:
        list_models(base)
        return 0

    url_base = "%s/ota/rgl/%s" % (base, args.model)
    os.makedirs(args.out, exist_ok=True)
    print("source: %s" % url_base)

    paths = {}
    for name in FILES:
        url = "%s/%s" % (url_base, name)
        dest = os.path.join(args.out, name)
        if os.path.exists(dest):
            print("  %-16s already present (%d bytes)" % (name, os.path.getsize(dest)))
        else:
            print("  %-16s downloading ..." % name, end="", flush=True)
            data = fetch(url)
            with open(dest, "wb") as fh:
                fh.write(data)
            print(" %d bytes" % len(data))
        paths[name] = dest

    print("\nlocal files")
    for name in FILES:
        p = paths[name]
        print("  %-16s %10d  md5=%s" % (name, os.path.getsize(p), md5(p)))

    verify = not args.skip_verify
    if verify and os.path.exists(paths["config.txt"]):
        with open(paths["config.txt"], "r", encoding="utf-8", errors="replace") as fh:
            cfg = parse_config(fh.read())
        print("\nconfig.txt")
        for key in ("suffix_version", "major_version", "middle_version",
                    "minor_version", "small_version", "release_date",
                    "force_update", "MD5", "MD5_OTA"):
            if key in cfg:
                print("  %-16s %s" % (key, cfg[key]))
        print("\nverification")
        ok = True
        pairs = (("sd203ota.bin.gz", "MD5"), ("otaunpack", "MD5_OTA"))
        for name, key in pairs:
            want = cfg.get(key)
            if not want:
                print("  %-16s no %s in config" % (name, key))
                ok = False
                continue
            got = md5(paths[name])
            status = "OK" if got.lower() == want.lower() else "MISMATCH"
            if status != "OK":
                ok = False
            print("  %-16s %s (expected %s)" % (name, status, want))
        if not ok:
            print("\nwarning: checksum mismatch - the package may have changed")
            return 1
        print("\nversion: %s%s.%s.%s-%s" % (
            cfg.get("suffix_version", ""),
            cfg.get("major_version", "?"), cfg.get("middle_version", "?"),
            cfg.get("minor_version", "?"), cfg.get("small_version", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
