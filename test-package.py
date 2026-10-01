#!/usr/bin/env python3
"""Audit a built .pkg.tar.zst without extracting files onto the filesystem."""
import hashlib
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 test-package.py <package.pkg.tar.zst>")
    project = Path(__file__).resolve().parent
    mapping = {
        "etc/leigod/device.conf": "device.conf",
        "etc/leigod/sslh-guard.cfg": "sslh-guard.cfg",
        "opt/leigod/acc-gw.router.amd64": "acc-gw.router.amd64",
        "opt/leigod/acc_upgrade_monitor": "acc-gw.router.amd64",
        "opt/leigod/config/accelerator.ini": "accelerator.ini",
        "opt/leigod/config/acc_version.ini": "acc_version.ini",
        "opt/leigod/config/ipdatacloud_country.xdb": "ipdatacloud_country.xdb",
        "opt/leigod/fake_os_release": "fake_os_release",
        "opt/leigod/fake_product_name": "fake_product_name",
        "opt/leigod/fake_version": "fake_version",
        "usr/lib/leigod/ensure-wlan0.sh": "ensure-wlan0.sh",
        "usr/lib/leigod/firewall.sh": "firewall.sh",
        "usr/lib/leigod/run.sh": "run.sh",
        "usr/lib/modules-load.d/leigod.conf": "leigod.conf",
        "usr/lib/systemd/system/leigod.service": "leigod.service",
        "usr/share/leigod/mirror.conf.example": "mirror.conf.example",
        "usr/share/licenses/leigod-steamdeck-plugin/LICENSE": "LICENSE",
        ".INSTALL": "leigod-plugin.install",
    }
    for name in ("README.md", "TROUBLESHOOTING.md", "AUDIT.md"):
        mapping[f"usr/share/doc/leigod-steamdeck-plugin/{name}"] = name
    monitor = "opt/leigod/steamdeck_acc_monitor.sh"
    metadata = {".PKGINFO", ".BUILDINFO", ".MTREE"}
    expected = set(mapping) | metadata | {monitor}
    directories = {"opt/leigod/log"}
    for name in expected:
        directories.update(str(parent) for parent in PurePosixPath(name).parents if str(parent) != ".")
    executable = {monitor, "opt/leigod/acc-gw.router.amd64", "opt/leigod/acc_upgrade_monitor",
                  "usr/lib/leigod/ensure-wlan0.sh", "usr/lib/leigod/firewall.sh", "usr/lib/leigod/run.sh"}
    srcinfo = (project / ".SRCINFO").read_text()
    sources = re.findall(r"^\s*source = (.+)$", srcinfo, re.M)
    hashes = re.findall(r"^\s*sha256sums = (.+)$", srcinfo, re.M)
    assert len(sources) == len(hashes)
    pins = {source.rsplit("/", 1)[-1]: digest for source, digest in zip(sources, hashes)}
    seen = set()
    with subprocess.Popen(["zstd", "-dc", "--", sys.argv[1]], stdout=subprocess.PIPE) as process:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                name = member.name.rstrip("/")
                assert member.uid == 0 and member.gid == 0, f"Non-root owner: {name}"
                if member.isdir():
                    assert name in directories and member.mode == 0o755, f"Unexpected directory: {name}"
                    continue
                assert member.isfile() and name in expected and name not in seen, f"Unexpected file: {name}"
                seen.add(name)
                assert member.mode == (0o755 if name in executable else 0o644), f"Wrong mode: {name}"
                data = archive.extractfile(member).read()
                if name in mapping:
                    source = mapping[name]
                    assert data == (project / source).read_bytes(), f"Payload differs: {name}"
                    if source in pins:
                        assert hashlib.sha256(data).hexdigest() == pins[source], f"Hash mismatch: {name}"
                elif name == monitor:
                    assert b"-p 5589" in data and b"-p 5588" not in data
                    assert b"trap 'rm -f" in data
                elif name == ".PKGINFO":
                    version = re.search(r"^\s*pkgver = (.+)$", srcinfo, re.M)[1]
                    release = re.search(r"^\s*pkgrel = (.+)$", srcinfo, re.M)[1]
                    assert f"pkgver = {version}-{release}\n".encode() in data
        # Drain tar padding so zstd can finish without a pipe error.
        process.stdout.read()
        assert process.wait() == 0
    assert seen == expected, f"Missing files: {expected - seen}"
    assert re.search(r"^DEVICE_IP=auto$", (project / "device.conf").read_text(), re.M)
    assert not re.search(r"^token\s*=\s*\S+", (project / "accelerator.ini").read_text(), re.M)
    print(f"PASS: {len(seen)} expected files; pinned bytes, root ownership, modes, version and portable defaults")
    print("PASS: no developer paths, diagnostics, tests, bytecode, captures or runtime credentials packaged")


if __name__ == "__main__":
    main()
