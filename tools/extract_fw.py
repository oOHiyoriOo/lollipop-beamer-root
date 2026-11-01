#!/usr/bin/env python3
"""Carve the OTA container of the SD203 Lollipop beamer.

The vendor container (`sd203ota.bin`) is a flat blob: a small script header
followed by U-Boot, a Linux uImage and two SquashFS images.  There is no
index we need - we just scan for the well-known magics and carve by the size
fields stored inside each image.

Usage:
    python3 extract_fw.py ./fw/sd203ota.bin.gz --out ./fw/extracted
    python3 extract_fw.py ./fw/sd203ota.bin --out ./fw/extracted --extract
"""
import argparse
import gzip
import os
import struct
import sys

UIMAGE_MAGIC = bytes.fromhex("27051956")
SQUASHFS_MAGIC = b"hsqs"


def load(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rb") as fh:
            return fh.read()
    with open(path, "rb") as fh:
        return fh.read()


def find_all(data, magic):
    out = []
    start = 0
    while True:
        i = data.find(magic, start)
        if i < 0:
            return out
        out.append(i)
        start = i + 1


def carve(data, outdir):
    os.makedirs(outdir, exist_ok=True)
    found = []

    for i, off in enumerate(find_all(data, UIMAGE_MAGIC)):
        size = struct.unpack_from(">I", data, off + 12)[0]
        name = data[off + 32:off + 64].split(b"\x00")[0].decode("latin-1", "replace")
        if size <= 0 or off + 64 + size > len(data):
            continue
        dest = os.path.join(outdir, "uimage%d.bin" % (i + 1))
        with open(dest, "wb") as fh:
            fh.write(data[off:off + 64 + size])
        found.append(("uImage", off, 64 + size, dest, name))

    for i, off in enumerate(find_all(data, SQUASHFS_MAGIC)):
        # superblock: bytes_used is a little-endian u64 at offset 40
        try:
            used = struct.unpack_from("<Q", data, off + 40)[0]
            block = struct.unpack_from("<I", data, off + 12)[0]
            inodes = struct.unpack_from("<I", data, off + 4)[0]
        except struct.error:
            continue
        if used <= 0 or used > len(data) - off:
            continue
        dest = os.path.join(outdir, "squashfs%d.bin" % (i + 1))
        with open(dest, "wb") as fh:
            fh.write(data[off:off + used])
        found.append(("SquashFS", off, used, dest,
                      "inodes=%d block=%d" % (inodes, block)))

    return found


def extract_squashfs(img, outdir):
    try:
        import PySquashfsImage  # noqa: F401
    except ImportError:
        print("  PySquashfsImage not installed - skipping extraction")
        print("  pip install PySquashfsImage")
        return False
    import subprocess
    dest = os.path.join(outdir, os.path.basename(img).replace(".bin", ""))
    cmd = [sys.executable, "-m", "PySquashfsImage", "extract",
           "-d", dest, "-p", "/", img]
    print("  %s" % " ".join(cmd))
    subprocess.run(cmd, check=False)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--out", default="extracted")
    ap.add_argument("--extract", action="store_true",
                    help="also extract SquashFS images (needs PySquashfsImage)")
    args = ap.parse_args()

    data = load(args.image)
    print("input: %s (%d bytes)" % (args.image, len(data)))
    found = carve(data, args.out)

    if not found:
        print("no images found")
        return 1

    print("\ncarved images")
    for kind, off, size, dest, note in found:
        print("  %-9s off=%-9d size=%-9d %-40s %s" %
              (kind, off, size, os.path.basename(dest), note))

    if args.extract:
        print("\nextraction")
        for kind, off, size, dest, note in found:
            if kind == "SquashFS":
                extract_squashfs(dest, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
