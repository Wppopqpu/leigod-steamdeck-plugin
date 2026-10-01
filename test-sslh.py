#!/usr/bin/env python3
"""Reproduce reverse-DNS stalls using real sslh in disposable namespaces.

Run: python3 test-sslh.py
Only private mount/network namespaces receive resolver and hosts fixtures.
"""

import base64
import hashlib
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time


def run(*args):
    subprocess.run(args, check=True, capture_output=True, text=True)


def main():
    if len(sys.argv) == 1:
        subprocess.run([
            "unshare", "--user", "--map-root-user", "--net", "--mount",
            sys.executable, str(Path(__file__).resolve()), "--isolated",
            os.readlink("/proc/self/ns/net"), os.readlink("/proc/self/ns/mnt"),
        ], check=True)
        return
    if len(sys.argv) != 4 or sys.argv[1] != "--isolated":
        raise RuntimeError("Run without arguments")
    if (os.readlink("/proc/self/ns/net") == sys.argv[2]
            or os.readlink("/proc/self/ns/mnt") == sys.argv[3]):
        raise RuntimeError("Refusing to alter host namespaces")

    run("mount", "--make-rprivate", "/")
    # The launcher includes a /tmp log-size watcher; it must not see host logs.
    run("mount", "-t", "tmpfs", "tmpfs", "/tmp")
    run("ip", "link", "set", "lo", "up")
    project = Path(__file__).resolve().parent
    dns_queries = []
    stop = threading.Event()
    dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dns.bind(("127.0.0.1", 53))
    dns.settimeout(0.1)

    def blackhole_dns():
        while not stop.is_set():
            try:
                packet, _ = dns.recvfrom(4096)
                dns_queries.append(packet)
            except socket.timeout:
                pass

    dns_thread = threading.Thread(target=blackhole_dns, daemon=True)
    dns_thread.start()
    with tempfile.TemporaryDirectory(prefix="leigod-sslh-test.") as temp:
        directory = Path(temp)
        fixtures = {
            "/etc/nsswitch.conf": "hosts: files dns\n",
            "/etc/hosts": "127.0.0.1 localhost\n0.0.0.0 wildcard\n",
            "/etc/resolv.conf": "nameserver 127.0.0.1\noptions timeout:5 attempts:2\n",
        }
        for target, text in fixtures.items():
            fixture = directory / Path(target).name
            fixture.write_text(text)
            run("mount", "--bind", str(fixture), target)

        config = directory / "sslh.cfg"
        config.write_text((project / "sslh-guard.cfg").read_text())
        launcher = (project / "run.sh").read_text()
        # Test the actual launcher unchanged as the fixed case. A synthetic
        # numeric variant would still pass if someone removed -n from run.sh.
        if "/usr/bin/sslh -n -f" not in launcher:
            raise RuntimeError("run.sh must enable numeric sslh logging")
        legacy = launcher.replace("/usr/bin/sslh -n -f", "/usr/bin/sslh -f")
        fixed = launcher
        if legacy == fixed:
            raise RuntimeError("Could not identify the sslh launcher command")

        key = "dGhlIHNhbXBsZSBub25jZQ=="
        request = (
            "GET / HTTP/1.1\r\nHost: 127.0.0.1:5588\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode()
        accept = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
        ).digest()).decode()
        response = (
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode()

        def attempt(name, script_text, payload):
            script = directory / f"{name}.sh"
            script.write_text(script_text)
            result = {"received": b"", "accepted": False, "timed_out": False}
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 5589))
            server.listen(1)
            server.settimeout(4)

            def backend():
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    return
                with connection:
                    result["accepted"] = True
                    connection.settimeout(5)
                    try:
                        while b"\r\n\r\n" not in result["received"]:
                            part = connection.recv(4096)
                            if not part:
                                break
                            result["received"] += part
                        if result["received"]:
                            connection.sendall(response)
                    except socket.timeout:
                        result["timed_out"] = True

            thread = threading.Thread(target=backend, daemon=True)
            thread.start()
            env = dict(os.environ, LEIGOD_SSLH_CONF=str(config))
            with (directory / f"{name}.log").open("wb") as output:
                process = subprocess.Popen(
                    ["bash", str(script), "/usr/bin/sleep", "30"], env=env,
                    stdout=output, stderr=output, start_new_session=True,
                )
                try:
                    # Retry only refused connections; never send empty probes.
                    deadline = time.monotonic() + 3
                    while True:
                        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        client.bind(("127.0.0.2", 0))
                        client.settimeout(13)
                        try:
                            client.connect(("127.0.0.1", 5588))
                            break
                        except ConnectionRefusedError:
                            client.close()
                            if process.poll() is not None or time.monotonic() >= deadline:
                                raise RuntimeError(f"sslh did not start in {name}")
                            time.sleep(0.05)
                    with client:
                        start = time.monotonic()
                        client.sendall(payload)
                        data = b""
                        try:
                            while b"\r\n\r\n" not in data:
                                part = client.recv(4096)
                                if not part:
                                    break
                                data += part
                        except ConnectionResetError:
                            pass
                        result["elapsed"] = time.monotonic() - start
                        result["response"] = data
                    thread.join(timeout=6)
                    if thread.is_alive():
                        raise RuntimeError("Backend test thread did not finish")
                finally:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                    server.close()
            return result

        before = attempt("legacy", legacy, request)
        assert dns_queries, "Legacy control did not attempt DNS resolution"
        assert before["accepted"] and before["timed_out"]
        assert not before["received"] and not before["response"]
        print(f"REPRODUCED: legacy sslh delays {before['elapsed']:.3f}s; backend expires before receiving the handshake")

        queries_before = len(dns_queries)
        after = attempt("numeric", fixed, request)
        assert after["received"] == request
        assert after["response"] == response
        assert after["elapsed"] < 2
        assert len(dns_queries) == queries_before, "Numeric mode still attempted DNS"
        print(f"PASS: numeric sslh returns HTTP 101 in {after['elapsed']:.3f}s, with no DNS query")

        plain = attempt("plain-http", fixed, b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert not plain["accepted"] and not plain["received"] and not plain["response"]
        print("PASS: ordinary HTTP still does not reach the backend")
    stop.set()
    dns_thread.join(timeout=1)
    dns.close()


if __name__ == "__main__":
    main()
