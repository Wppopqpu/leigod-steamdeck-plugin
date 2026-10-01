#!/usr/bin/env python3
"""Run with: python3 test-firewall.py.

Exercise the real nftables rules in isolated network namespaces, never on
the host firewall. No third-party Python dependencies are required.
"""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys


def run(*args, data=None):
    return subprocess.run(args, input=data, text=True, capture_output=True,
                          check=True).stdout


def nft_rules():
    return json.loads(run("nft", "-j", "list", "chain", "inet", "filter", "input"))


def main():
    if len(sys.argv) == 1:
        subprocess.run(["unshare", "--user", "--map-root-user", "--net", "--mount",
                        sys.executable, str(Path(__file__).resolve()), "--isolated",
                        os.readlink("/proc/self/ns/net"), os.readlink("/proc/self/ns/mnt")], check=True)
        return
    if len(sys.argv) != 4 or sys.argv[1] != "--isolated":
        raise RuntimeError("Run this script without arguments")
    if os.readlink("/proc/self/ns/net") == sys.argv[2]:
        raise RuntimeError("Refusing to test in the host network namespace")
    if os.readlink("/proc/self/ns/mnt") == sys.argv[3]:
        raise RuntimeError("Refusing to change host configuration")
    run("mount", "--make-rprivate", "/")
    run("mount", "-t", "tmpfs", "tmpfs", "/etc")
    Path("/etc/leigod").mkdir()
    Path("/etc/leigod/device.conf").write_text("DEVICE_PARENT=auto\n")
    if run("nft", "list", "ruleset").strip():
        raise RuntimeError("Tests require an empty, disposable network namespace")

    script = str(Path(__file__).resolve().with_name("firewall.sh"))
    run("ip", "link", "set", "lo", "up")
    peer = subprocess.Popen(["unshare", "--net", "sleep", "120"])
    try:
        # Wait for unshare to enter the peer namespace before moving the link.
        import time
        for _ in range(100):
            if peer.poll() is not None:
                raise RuntimeError("Peer namespace creation failed")
            if os.readlink(f"/proc/{peer.pid}/ns/net") != os.readlink("/proc/self/ns/net"):
                break
            time.sleep(0.02)
        else:
            raise RuntimeError("Peer namespace creation timed out")

        prefix = ("nsenter", "-t", str(peer.pid), "-n")
        run("ip", "link", "add", "enp2s0", "type", "veth", "peer", "name", "phone")
        run("ip", "link", "set", "phone", "netns", str(peer.pid))
        run("ip", "addr", "add", "192.168.9.239/24", "brd", "+", "dev", "enp2s0")
        run("ip", "link", "set", "enp2s0", "up")
        run("ip", "route", "add", "default", "via", "192.168.9.1")
        run(*prefix, "ip", "addr", "add", "192.168.9.101/24", "brd", "+", "dev", "phone")
        run(*prefix, "ip", "link", "set", "phone", "up")
        run("nft", "-f", "-", data='''
add table inet filter
add chain inet filter input { type filter hook input priority 0; policy drop; }
add rule inet filter input iifname "lo" accept
add rule inet filter input tcp dport 22 accept comment "host-owned"
add table inet unrelated
add chain inet unrelated keep
add rule inet unrelated keep counter comment "leave-me-alone"
''')
        original = nft_rules()
        unrelated = run("nft", "-s", "list", "table", "inet", "unrelated")

        def receives_broadcast():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
                listener.bind(("0.0.0.0", 6066))
                listener.settimeout(0.5)
                run(*prefix, "python3", "-c", '''
import socket
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.sendto(b"<NBOX> HELLO any", ("192.168.9.255", 6066))
''')
                try:
                    payload, remote = listener.recvfrom(1024)
                    return payload == b"<NBOX> HELLO any" and remote[0] == "192.168.9.101"
                except socket.timeout:
                    return False

        def tcp_connects(port):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("0.0.0.0", port))
                listener.listen(1)
                result = run(*prefix, "python3", "-c", f'''
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.settimeout(0.5)
    print(s.connect_ex(("192.168.9.239", {port})) == 0)
''')
                return result.strip() == "True"

        assert not receives_broadcast(), "Baseline must drop discovery"
        assert not tcp_connects(5588), "Baseline must drop control connections"
        run("bash", script, "setup")
        assert receives_broadcast(), "Setup must allow LAN discovery"
        assert tcp_connects(5588), "Setup must allow LAN control connections"
        assert not tcp_connects(5589), "Setup must not expose the backend port"
        print("PASS: UDP broadcast and TCP 5588 allowed; TCP 5589 still blocked")

        run("bash", script, "setup")
        rules = [x["rule"] for x in nft_rules()["nftables"] if "rule" in x]
        owned = [r for r in rules if r.get("comment", "").startswith("leigod-steamdeck-plugin:")]
        assert len(owned) == 2, "Repeated setup must leave exactly two owned rules"
        assert receives_broadcast()
        run("bash", script, "cleanup")
        run("bash", script, "cleanup")
        # Metadata may differ; compare the actual chain/rule objects.
        objects = lambda doc: [x for x in doc["nftables"] if "metainfo" not in x]
        assert objects(nft_rules()) == objects(original), "Cleanup changed host rules"
        assert run("nft", "-s", "list", "table", "inet", "unrelated") == unrelated
        assert not receives_broadcast(), "Cleanup must restore the baseline policy"
        print("PASS: repeated setup/cleanup preserve host rules and unrelated tables")

        run("nft", "delete", "table", "inet", "filter")
        run("bash", script, "setup")
        run("bash", script, "cleanup")
        assert "table inet filter" not in run("nft", "list", "tables")
        print("PASS: absent host chain is reported without creating a new firewall")
    finally:
        peer.terminate()
        peer.wait(timeout=5)


if __name__ == "__main__":
    main()
