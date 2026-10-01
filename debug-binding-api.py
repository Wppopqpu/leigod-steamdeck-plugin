#!/usr/bin/env python3
"""Capture a bounded binding attempt; print metadata, never raw credentials."""

import argparse
from collections import Counter
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time


def set_log_level(level):
    with socket.create_connection(("127.0.0.1", 10001), timeout=3) as connection:
        connection.settimeout(3)
        # Read the server banner before issuing a command, then require its ack.
        connection.recv(8192)
        connection.sendall(f"set_log_level {level}\n".encode())
        expected = f"Log level set to {level}".encode()
        response = b""
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and len(response) < 16384:
            connection.settimeout(max(0.01, deadline - time.monotonic()))
            part = connection.recv(4096)
            if not part:
                break
            response += part
            if expected in response:
                return
        raise RuntimeError("Debug server did not acknowledge the log-level change")


def summarize_traffic(path):
    """Print TCP metadata and HTTP status only; omit URLs, headers and bodies.

    This is a packet-level view, not a TCP reassembler. HTTP status lines split
    between packets may not be recognized; duplicate interface captures remain.
    """
    header = re.compile(
        r"^\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2}\.\d+) .*?"
        r"IP ([0-9.]+) > ([0-9.]+): Flags \[([A-Za-z.*]+)\].*?length (\d+)"
    )
    packets = []
    current = None
    for line in path.read_text(errors="replace").splitlines():
        match = header.search(line)
        if match:
            current = [match.groups(), []]
            packets.append(current)
        elif current is not None:
            current[1].append(line)
    print("\n=== TCP / HTTP timeline (metadata only) ===")
    if not packets:
        print("No TCP metadata captured; check the capture error log.")
    shown = 0
    for (timestamp, source, destination, flags, size), lines in packets:
        payload = "\n".join(lines)
        statuses = re.findall(r"HTTP/1\.[01] ([1-5][0-9]{2})\b", payload)
        request = bool(re.search(r"\bGET [^\r\n]* HTTP/1\.[01]", payload))
        if size == "0" and not any(flag in flags for flag in "SRF"):
            continue
        if shown >= 80:
            print("Further packets omitted from the summary.")
            break
        suffix = " HTTP_GET" if request else ""
        if statuses:
            suffix += " HTTP_STATUS=" + ",".join(statuses)
        print(f"{timestamp} {source} -> {destination} flags={flags} tcp_bytes={size}{suffix}")
        shown += 1
    print("Packet observations can repeat across interfaces; absence of an HTTP status is inconclusive.")


def summarize(path):
    methods = Counter()
    events = Counter()
    codes = Counter()
    total = 0
    for line in path.read_text(errors="replace").splitlines():
        total += 1
        for method in re.findall(
            r"\b(?:getRouterInfo|getDeviceList|getNetworkState|GETROUTERINFO|"
            r"GETDEVICELIST|GETNETWORKSTATE|bindRouter|unbindRouter)\b", line
        ):
            methods[method] += 1
        # Log source/function names identify failures without revealing bodies.
        header = re.search(
            r"\b(DEBUG|INFO|WARN|ERROR)\s+\[([A-Za-z0-9_.-]+)\]"
            r"\s+\[([A-Za-z0-9_:~.-]+)\]", line
        )
        if header:
            events[" ".join(header.groups())] += 1
        for field, number in re.findall(
            r'''\b(retCode|error_code|status_code|http_code)\b["']?\s*[:=]\s*["']?(-?\d{1,6})\b''',
            line,
        ):
            codes[f"{field}={number}"] += 1

    print(f"\nCaptured log lines: {total}")
    for title, counts in (("API names", methods), ("Status codes", codes),
                          ("Log sources (no message bodies)", events)):
        print(f"\n=== {title} ===")
        if not counts:
            print("Not observed; this alone does not prove absence of traffic.")
        for item, count in counts.most_common(60):
            print(f"{count:4d}  {item}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=45)
    parser.add_argument("--analyze", type=Path, help="Summarize a saved log only")
    args = parser.parse_args()
    if args.analyze:
        summarize(args.analyze)
        traffic = args.analyze.with_name("traffic.log")
        if traffic.exists():
            summarize_traffic(traffic)
        return
    if not 10 <= args.seconds <= 300:
        parser.error("--seconds must be between 10 and 300")
    for program in ("sudo", "timeout", "tail", "tcpdump"):
        if not shutil.which(program):
            parser.error(f"Required program not found: {program}")

    subprocess.run(["sudo", "-v"], check=True)
    directory = Path(tempfile.mkdtemp(prefix="leigod-api-debug."))
    log = directory / "web-api.log"
    errors = directory / "capture-errors.log"
    traffic = directory / "traffic.log"
    processes = []
    debug_requested = False

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    old_term = signal.signal(signal.SIGTERM, interrupt)
    try:
        with log.open("wb") as output, errors.open("wb") as stderr, traffic.open("wb") as packets:
            processes.append(subprocess.Popen([
                "sudo", "-n", "timeout", "--signal=INT", "--kill-after=3s",
                f"{args.seconds + 15}s", "tail", "-n", "0", "-F",
                "/opt/leigod/log/web_api.log",
            ], stdout=output, stderr=stderr))
            processes.append(subprocess.Popen([
                "sudo", "-n", "timeout", "--signal=INT", "--kill-after=3s",
                f"{args.seconds + 15}s", "tcpdump", "-p", "-i", "any", "-nn",
                "-tttt", "-l", "-s", "512", "-A",
                "tcp and (port 5588 or port 5589)",
            ], stdout=packets, stderr=stderr))
            time.sleep(0.5)
            if any(process.poll() is not None for process in processes):
                raise RuntimeError(f"Log capture failed; see {errors}")
            debug_requested = True
            set_log_level("debug")
            print("Debug level acknowledged by the application.", flush=True)
            print(f"Capture ready: {args.seconds} seconds. Retry binding on the phone now.", flush=True)
            print(f"Private raw log: {log}", flush=True)
            time.sleep(args.seconds)
    except KeyboardInterrupt:
        print("\nStopping capture.")
    finally:
        if debug_requested:
            try:
                set_log_level("info")
                print("Application log level restored to INFO.", flush=True)
            except (OSError, RuntimeError) as exc:
                print(f"Could not restore INFO: {exc}. Restart leigod to restore its startup log level.")
        for process in processes:
            if process.poll() is None:
                try:
                    process.send_signal(signal.SIGINT)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                print("Capture is still exiting; its timeout will stop it shortly.")
        signal.signal(signal.SIGTERM, old_term)
    summarize(log)
    summarize_traffic(traffic)
    print("\nShare the summary above; raw logs may contain account credentials.")


if __name__ == "__main__":
    main()
