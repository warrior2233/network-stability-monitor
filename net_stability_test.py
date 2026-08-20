#!/usr/bin/env python3
"""
Lightweight network stability tester.

It repeatedly downloads test payloads with multiple HTTP connections and writes
interval throughput + latency samples to CSV. It is intended for practical home/
office troubleshooting: detect drops, stalls, packet-loss-like symptoms, and
large speed swings over time.

No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import random
import socket
import string
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


DEFAULT_URL_TEMPLATE = (
    "https://speed.cloudflare.com/__down?bytes={bytes}&cachebust={cachebust}"
)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def random_token(length: int = 12) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def mbps(byte_count: int, seconds: float) -> float:
    if seconds <= 0:
        return 0.0
    return byte_count * 8 / seconds / 1_000_000


def build_url(template: str, payload_bytes: int) -> str:
    if "{bytes}" in template or "{cachebust}" in template:
        return template.format(bytes=payload_bytes, cachebust=random_token())

    # If the user passes a plain URL, append a cache-busting query parameter so
    # repeated requests are less likely to be served from an intermediate cache.
    parsed = urllib.parse.urlparse(template)
    sep = "&" if parsed.query else "?"
    return f"{template}{sep}cachebust={random_token()}"


def make_opener(direct: bool) -> urllib.request.OpenerDirector:
    if direct:
        # Empty ProxyHandler means: do not use HTTP_PROXY/HTTPS_PROXY or the
        # Windows Internet Options proxy. This bypasses Clash only when Clash is
        # configured as an application/system HTTP proxy, not when TUN/global
        # route capture is enabled.
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def detected_proxy_settings() -> dict[str, str]:
    keys = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"]
    found: dict[str, str] = {}
    for key in keys:
        value = os.environ.get(key) or os.environ.get(key.lower())
        if value:
            found[key] = value
    try:
        system_proxies = urllib.request.getproxies()
        for key, value in system_proxies.items():
            found[f"system_{key}"] = value
    except Exception:
        pass
    return found


@dataclass
class SharedState:
    downloaded_bytes: int = 0
    request_count: int = 0
    errors: int = 0
    active_downloads: int = 0
    latency_samples_ms: list[float] = field(default_factory=list)
    latency_failures: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_bytes(self, n: int) -> None:
        with self.lock:
            self.downloaded_bytes += n

    def add_request(self) -> None:
        with self.lock:
            self.request_count += 1

    def add_error(self) -> None:
        with self.lock:
            self.errors += 1

    def add_latency(self, ms: Optional[float]) -> None:
        with self.lock:
            if ms is None:
                self.latency_failures += 1
            else:
                self.latency_samples_ms.append(ms)

    def set_active_delta(self, delta: int) -> None:
        with self.lock:
            self.active_downloads += delta

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "downloaded_bytes": self.downloaded_bytes,
                "request_count": self.request_count,
                "errors": self.errors,
                "active_downloads": self.active_downloads,
                "latency_samples_ms": list(self.latency_samples_ms),
                "latency_failures": self.latency_failures,
            }


def download_worker(
    worker_id: int,
    state: SharedState,
    stop: threading.Event,
    url_template: str,
    payload_bytes: int,
    timeout: float,
    read_size: int,
    direct: bool,
) -> None:
    headers = {
        "User-Agent": f"net-stability-test/1.0 worker-{worker_id}",
        "Cache-Control": "no-cache",
    }
    opener = make_opener(direct)

    while not stop.is_set():
        url = build_url(url_template, payload_bytes)
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            state.set_active_delta(1)
            with opener.open(req, timeout=timeout) as resp:
                while not stop.is_set():
                    chunk = resp.read(read_size)
                    if not chunk:
                        break
                    state.add_bytes(len(chunk))
            state.add_request()
        except Exception:
            state.add_error()
            # Avoid a tight retry loop during outages.
            stop.wait(0.5)
        finally:
            state.set_active_delta(-1)


def latency_worker(
    state: SharedState,
    stop: threading.Event,
    host: str,
    port: int,
    timeout: float,
    every: float,
) -> None:
    while not stop.is_set():
        start = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                elapsed_ms = (time.perf_counter() - start) * 1000
                state.add_latency(elapsed_ms)
        except OSError:
            state.add_latency(None)
        stop.wait(every)


def percentile(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * p))
    return ordered[max(0, min(idx, len(ordered) - 1))]


def infer_latency_host(url_template: str) -> str:
    sample_url = build_url(url_template, 1)
    parsed = urllib.parse.urlparse(sample_url)
    if not parsed.hostname:
        raise ValueError("Could not infer latency host from URL; pass --latency-host.")
    return parsed.hostname


def open_csv(path: str) -> tuple[object, csv.DictWriter]:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    f = open(path, "w", newline="", encoding="utf-8-sig")
    writer = csv.DictWriter(
        f,
        fieldnames=[
            "timestamp",
            "elapsed_sec",
            "interval_sec",
            "interval_mbps",
            "avg_mbps",
            "total_gb",
            "requests",
            "download_errors",
            "active_downloads",
            "latency_avg_ms",
            "latency_p95_ms",
            "latency_failures",
        ],
    )
    writer.writeheader()
    return f, writer


def run(args: argparse.Namespace) -> int:
    if args.connections < 1:
        raise ValueError("--connections must be >= 1")
    if args.duration <= 0:
        raise ValueError("--duration must be > 0")
    if args.interval <= 0:
        raise ValueError("--interval must be > 0")

    url_template = args.url
    latency_host = args.latency_host or infer_latency_host(url_template)

    state = SharedState()
    stop = threading.Event()
    threads: list[threading.Thread] = []

    for i in range(args.connections):
        t = threading.Thread(
            target=download_worker,
            args=(
                i + 1,
                state,
                stop,
                url_template,
                args.payload_bytes,
                args.timeout,
                args.read_size,
                args.direct,
            ),
            daemon=True,
        )
        threads.append(t)
        t.start()

    if not args.no_latency:
        t = threading.Thread(
            target=latency_worker,
            args=(
                state,
                stop,
                latency_host,
                args.latency_port,
                min(args.timeout, 5.0),
                args.latency_every,
            ),
            daemon=True,
        )
        threads.append(t)
        t.start()

    csv_file, writer = open_csv(args.csv)
    started = time.perf_counter()
    last_time = started
    last_bytes = 0
    last_snapshot = state.snapshot()

    print(f"Target URL: {url_template}")
    print(f"Mode: {'DIRECT/no application proxy' if args.direct else 'default system proxy behavior'}")
    proxies = detected_proxy_settings()
    if proxies:
        print("Detected proxy settings:")
        for key, value in proxies.items():
            print(f"  {key}={value}")
    else:
        print("Detected proxy settings: none")
    print(f"Duration: {args.duration}s | interval: {args.interval}s | connections: {args.connections}")
    print(f"Latency check: {'off' if args.no_latency else f'{latency_host}:{args.latency_port}'}")
    print(f"CSV: {os.path.abspath(args.csv)}")
    print("Press Ctrl+C to stop early.\n")

    try:
        while True:
            elapsed = time.perf_counter() - started
            if elapsed >= args.duration:
                break

            time.sleep(min(args.interval, max(0.1, args.duration - elapsed)))
            current_time = time.perf_counter()
            snap = state.snapshot()

            interval_sec = current_time - last_time
            interval_bytes = snap["downloaded_bytes"] - last_bytes
            interval_mbps = mbps(interval_bytes, interval_sec)
            avg_mbps = mbps(snap["downloaded_bytes"], current_time - started)
            total_gb = snap["downloaded_bytes"] / 1_000_000_000

            prev_latency_count = len(last_snapshot["latency_samples_ms"])
            interval_latencies = snap["latency_samples_ms"][prev_latency_count:]
            latency_avg = (
                sum(interval_latencies) / len(interval_latencies)
                if interval_latencies
                else None
            )
            latency_p95 = percentile(interval_latencies, 0.95)
            latency_failures = snap["latency_failures"] - last_snapshot["latency_failures"]

            row = {
                "timestamp": now_iso(),
                "elapsed_sec": round(current_time - started, 2),
                "interval_sec": round(interval_sec, 2),
                "interval_mbps": round(interval_mbps, 2),
                "avg_mbps": round(avg_mbps, 2),
                "total_gb": round(total_gb, 4),
                "requests": snap["request_count"],
                "download_errors": snap["errors"],
                "active_downloads": snap["active_downloads"],
                "latency_avg_ms": round(latency_avg, 2) if latency_avg is not None else "",
                "latency_p95_ms": round(latency_p95, 2) if latency_p95 is not None else "",
                "latency_failures": latency_failures,
            }
            writer.writerow(row)
            csv_file.flush()

            lat_text = (
                "lat n/a"
                if latency_avg is None
                else f"lat avg {latency_avg:.1f} ms p95 {latency_p95:.1f} ms"
            )
            print(
                f"{row['elapsed_sec']:>7}s | "
                f"{interval_mbps:>8.2f} Mbps interval | "
                f"{avg_mbps:>8.2f} Mbps avg | "
                f"{total_gb:>7.3f} GB | "
                f"errors {snap['errors']} | {lat_text} | lat_fail {latency_failures}"
            )

            last_time = current_time
            last_bytes = snap["downloaded_bytes"]
            last_snapshot = snap
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=2)
        csv_file.close()

    final = state.snapshot()
    total_sec = max(0.001, time.perf_counter() - started)
    all_latencies = final["latency_samples_ms"]
    print("\nSummary")
    print(f"  Total downloaded: {final['downloaded_bytes'] / 1_000_000_000:.3f} GB")
    print(f"  Average speed:    {mbps(final['downloaded_bytes'], total_sec):.2f} Mbps")
    print(f"  HTTP requests:    {final['request_count']}")
    print(f"  Download errors:  {final['errors']}")
    if all_latencies:
        print(f"  Latency avg:      {sum(all_latencies)/len(all_latencies):.2f} ms")
        print(f"  Latency p95:      {percentile(all_latencies, 0.95):.2f} ms")
    print(f"  Latency failures: {final['latency_failures']}")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sustained HTTP download + TCP latency network stability tester."
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL_TEMPLATE,
        help=(
            "Download URL or template. Templates may contain {bytes} and "
            "{cachebust}. Default uses Cloudflare's speed test endpoint."
        ),
    )
    parser.add_argument("--duration", type=int, default=60, help="Test duration in seconds.")
    parser.add_argument("--interval", type=float, default=5, help="Report interval in seconds.")
    parser.add_argument("--connections", type=int, default=4, help="Parallel download connections.")
    parser.add_argument(
        "--payload-bytes",
        type=int,
        default=100_000_000,
        help="Bytes requested per HTTP download when the URL template supports {bytes}.",
    )
    parser.add_argument(
        "--read-size",
        type=int,
        default=256 * 1024,
        help="Socket read size in bytes.",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP/socket timeout seconds.")
    parser.add_argument("--csv", default="network_stability_log.csv", help="CSV output path.")
    parser.add_argument(
        "--direct",
        action="store_true",
        help=(
            "Bypass Python/Windows HTTP proxy settings. This does not bypass "
            "Clash TUN/global route capture; disable TUN or add DIRECT rules "
            "in Clash for that case."
        ),
    )
    parser.add_argument("--no-latency", action="store_true", help="Disable TCP latency checks.")
    parser.add_argument("--latency-host", default=None, help="Host for TCP latency checks.")
    parser.add_argument("--latency-port", type=int, default=443, help="Port for TCP latency checks.")
    parser.add_argument("--latency-every", type=float, default=1.0, help="Latency check interval.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(run(parse_args(sys.argv[1:])))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
