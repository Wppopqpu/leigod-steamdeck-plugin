# Package audit — 2026-10-01

## Scope

Reviewed packaging, service configuration, helper scripts, install hooks,
diagnostics and regression tests on `fix/mobile-binding`. This is not a source
audit of the proprietary accelerator. Successful phone binding was reported for
the firewall fix plus numeric sslh logging before this cleanup release.

## Changes accepted for the cleanup release

- Restore portable defaults: no committed site-specific device address.
- Use dummy for automatic identity and macvlan only for a separately reserved
  static LAN address. Explicit broadcast plus `noprefixroute` avoids route takeover.
- Mark package-owned virtual links; preserve physical/unmarked links. Stop and
  uninstall clean only owned links, including failed-start leftovers. Legacy links
  require explicit adoption; there is no ownership inference from the name alone.
- Insert tagged, LAN-scoped nftables rules in the existing input chain. Cleanup
  preserves host policy and unrelated rules; no cross-table or base-chain jumps.
- Use `sslh -n` to prevent reverse DNS delaying initial WebSocket forwarding.
- Create the monitor lock directory in the launcher's mount namespace.
- Save binding state in a root-private directory, stop active writers before an
  upgrade snapshot, and do not restore obsolete bindings after explicit unbind.
- Align `pkgver` and `acc_version.ini` with the pinned artifact's embedded version
  `1.3.0.01`. The binary pin was already changed in the investigation worktree;
  this release does not replace it with an unverified new download.

## Package boundary

Runtime files are confined to `/opt/leigod`, `/etc/leigod`, the service/helper/module
directories under `/usr/lib`, and documentation/license directories under
`/usr/share`. Logs and binding files are generated at runtime, not copied from the
installed system. The binding snapshot directory is created only by install hooks.

No repository metadata, test/diagnostic scripts, raw captures, cached bytecode,
files destined for a developer home directory or nested package archives belong
in the installed payload. Standard `.BUILDINFO` metadata is retained and includes
the build directory and package environment; it is not a claim of anonymized builds.
The packaging license and its explicit proprietary-upstream scope notice are
installed; that notice does not grant rights to the vendor code.

## Verification

- `test-device.py`: automatic defaults; wildcard discovery with dummy identity;
  reserved static macvlan; broadcast/local/main route behavior; repeated setup;
  ownership checks; explicit adoption; cleanup even with invalid configuration.
- `test-firewall.py`: actual UDP/TCP traffic with policy drop; 6066/5588 allowed;
  backend 5589 still blocked; idempotent setup/cleanup; unrelated rules preserved.
- `test-sslh.py`: actual launcher and sslh; controlled reverse-DNS timeout;
  successful HTTP 101 with numeric logging; ordinary HTTP withheld from backend.
- `test-install.py`: private credential snapshot permissions; restoration;
  intentional unbind; stop/save/start ordering; inactive service stays inactive.
- `test-diagnostics.py`: metadata-only summaries, credential omission and verified
  logging acknowledgements.
- Package verification checks `.SRCINFO`, pinned source hashes, archive ownership,
  executable modes and the file boundary. Build success alone is not a networking test.

## Remaining limits

- Upstream binaries/data are closed source, delivered by an HTTP source and pinned
  by hash. The pin is not a vendor signature. Runtime vendor self-updates may change
  the installed binary independently of pacman.
- The service shares host networking and can alter routes/firewall state. Its mount,
  PID and device restrictions do not neutralize that authority.
- Backend 5589 and debug console 10001 may listen on all addresses. The package's
  two accept rules do not block those ports on a permissive host firewall.
- The integration supports `inet filter input`; other chains, multiple dropping
  base chains, firewall reloads and VPN/default-interface changes need operator care.
- Explicit static addresses require operator reservation; no claim of DHCP conflict
  detection is made. macvlan behavior on Wi-Fi is not assumed to work.
- A restart can change an old adopted physical-MAC-derived identity when the link is
  recreated. Current machine-id-derived identities are stable; older ones may rebind.
- The token's runtime persistence and acceleration cleanup remain vendor-controlled.
  Private reinstall backups are intentionally retained until restored or removed.
