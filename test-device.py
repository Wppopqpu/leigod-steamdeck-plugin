#!/usr/bin/env python3
"""Test owned virtual interfaces in disposable mount/network namespaces."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


def run(*args, check=True, data=None):
    result = subprocess.run(args, input=data, text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(f"{args}: {result.stderr}")
    return result


def main():
    if len(sys.argv) == 1:
        run("unshare", "--user", "--map-root-user", "--net", "--mount",
            sys.executable, str(Path(__file__).resolve()), "--isolated",
            os.readlink("/proc/self/ns/net"), os.readlink("/proc/self/ns/mnt"))
        print("PASS: virtual device defaults, ownership, migration, routing and cleanup")
        return
    if len(sys.argv) != 4 or sys.argv[1] != "--isolated":
        raise RuntimeError("Run without arguments")
    assert os.readlink("/proc/self/ns/net") != sys.argv[2]
    assert os.readlink("/proc/self/ns/mnt") != sys.argv[3]
    run("mount", "--make-rprivate", "/")
    run("mount", "-t", "tmpfs", "tmpfs", "/etc")
    run("mount", "-t", "sysfs", "sysfs", "/sys")
    Path("/etc/leigod").mkdir()
    Path("/etc/machine-id").write_text("0123456789abcdef0123456789abcdef\n")
    config = Path("/etc/leigod/device.conf")
    project = Path(__file__).resolve().parent
    script = str(project / "ensure-wlan0.sh")
    config.write_text((project / "device.conf").read_text())
    run("ip", "link", "set", "lo", "up")
    peer = subprocess.Popen(["unshare", "--net", "sleep", "120"])
    try:
        for _ in range(100):
            if os.readlink(f"/proc/{peer.pid}/ns/net") != os.readlink("/proc/self/ns/net"):
                break
            time.sleep(0.02)
        else:
            raise RuntimeError("Peer namespace not ready")
        client = ("nsenter", "-t", str(peer.pid), "-n")
        run("ip", "link", "add", "lan0", "type", "veth", "peer", "name", "phone")
        run("ip", "link", "set", "phone", "netns", str(peer.pid))
        run("ip", "addr", "add", "192.0.2.10/24", "brd", "+", "dev", "lan0")
        run("ip", "link", "set", "lan0", "up")
        run("ip", "route", "add", "default", "via", "192.0.2.1")
        run(*client, "ip", "addr", "add", "192.0.2.20/24", "brd", "+", "dev", "phone")
        run(*client, "ip", "link", "set", "phone", "up")

        def info():
            return json.loads(run("ip", "-j", "-d", "addr", "show", "dev", "wlan0").stdout)[0]

        def setup():
            run("bash", script, "setup")

        def cleanup():
            run("bash", script, "cleanup")

        setup()
        initial = info()
        assert initial["linkinfo"]["info_kind"] == "dummy"
        assert initial["ifalias"] == "leigod-steamdeck-plugin"
        assert any(a.get("local") == "192.0.2.10" and a["prefixlen"] == 32 for a in initial["addr_info"])
        main_routes = run("ip", "-4", "route", "show").stdout
        assert "dev wlan0" not in main_routes
        setup()
        assert info()["ifindex"] == initial["ifindex"]
        assert info()["address"] == initial["address"]

        # A wildcard discovery socket receives LAN broadcasts with a dummy
        # identity. macvlan is not required to cure an INPUT firewall drop.
        run("nft", "-f", "-", data='''
add table inet filter
add chain inet filter input { type filter hook input priority 0; policy drop; }
add rule inet filter input iifname "lo" accept
''')
        run("bash", str(project / "firewall.sh"), "setup")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.bind(("0.0.0.0", 6066))
            server.settimeout(2)
            run(*client, "python3", "-c", '''
import socket
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.sendto(b"discovery", ("192.0.2.255", 6066))
''')
            assert server.recvfrom(100)[0] == b"discovery"
        cleanup()
        cleanup()
        assert run("ip", "link", "show", "wlan0", check=False).returncode != 0
        setup()
        assert info()["address"] == initial["address"]

        # A static address uses a macvlan; explicit broadcast and noprefixroute
        # retain local broadcast delivery without hijacking LAN routes.
        config.write_text("DEVICE_IP=192.0.2.240\nDEVICE_PREFIX=auto\nDEVICE_PARENT=lan0\n")
        setup()
        assert info()["linkinfo"]["info_kind"] == "macvlan"
        assert "broadcast 192.0.2.255 dev wlan0" in run("ip", "-4", "route", "show", "table", "local").stdout
        assert run("ip", "-4", "route", "show").stdout == main_routes
        assert "dev lan0" in run("ip", "route", "get", "192.0.2.20").stdout
        index = info()["ifindex"]
        config.write_text("DEVICE_IP=not-an-ip\n")
        assert run("bash", script, "setup", check=False).returncode != 0
        assert info()["ifindex"] == index
        cleanup()  # Cleanup must work even with invalid configuration.

        # Do not delete or readdress an administrator-owned virtual interface.
        run("ip", "link", "add", "wlan0", "type", "dummy")
        run("ip", "addr", "add", "198.51.100.2/32", "dev", "wlan0")
        unmanaged = info()
        setup()
        cleanup()
        assert info() == unmanaged
        run("bash", script, "adopt")
        assert info()["address"] == unmanaged["address"]
        cleanup()
        assert run("ip", "link", "show", "wlan0", check=False).returncode != 0

        # Even our alias on a physical-like veth must never authorize deletion.
        run("ip", "link", "add", "wlan0", "type", "veth", "peer", "name", "other")
        run("ip", "link", "set", "wlan0", "alias", "leigod-steamdeck-plugin")
        cleanup()
        assert run("ip", "link", "show", "wlan0").returncode == 0
        assert run("bash", script, "adopt", check=False).returncode != 0
    finally:
        peer.terminate()
        peer.wait(timeout=5)


if __name__ == "__main__":
    main()
