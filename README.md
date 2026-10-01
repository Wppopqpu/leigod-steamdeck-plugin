# leigod-steamdeck-plugin

Arch Linux packaging of Leigod's proprietary SteamDeck accelerator plugin for
x86_64 Linux. A hardened systemd service supplies the filesystem paths and device
identity expected by the plugin. The phone application discovers and controls it
on the LAN. This is a community packaging project, not an official Linux client.

## Build and start

```bash
makepkg -si
sudo systemctl enable --now leigod
```

The kernel must provide `tun`, `dummy`, and (for static-address mode) `macvlan`.
The package supplies a modules-load configuration. If the modules are not already
available on a first installation, load them on the host or reboot before starting
the service. The systemd version must support the settings in `leigod.service`,
including `PrivatePIDs`; check with `systemd-analyze verify leigod.service`.

In the Leigod phone app, choose hardware acceleration, then SteamDeck. Connect the
phone to the same LAN and complete the binding flow. The packaging does not supply
an account, subscription, or bypass vendor authentication.

```bash
systemctl status leigod --no-pager -l
journalctl -u leigod --no-pager
```

Application logs are in `/opt/leigod/log`. They can contain account credentials;
do not post raw logs or captures publicly. Files installed under
`/usr/share/doc/leigod-steamdeck-plugin/` explain configuration and troubleshooting.

## Network paths

| Endpoint | Purpose |
|---|---|
| UDP 6066 | LAN discovery |
| TCP 5588 | Phone WebSocket control through sslh |
| TCP 5589 | Vendor backend; sslh forwards to its loopback address |
| TCP 10001 | Vendor debugging console |
| `wlan0` | Physical or package-owned virtual identity used by the vendor |
| `tun_PC` | Vendor acceleration interface during a session |

`run.sh` uses `sslh -n` so reverse DNS for connection logs cannot stall the initial
WebSocket request. The regex probe rejects ordinary HTTP that lacks a WebSocket
upgrade header, but is not a complete protocol validator. The backend and debug
console can listen on all addresses: the proxy does **not** make them loopback-only.
Keep ports 5589 and 10001 blocked from untrusted networks with the host firewall.

## Device configuration

`/etc/leigod/device.conf` is a pacman backup file. Local edits are retained; inspect
`.pacnew` files after upgrades. The package defaults contain no site-specific IP.

| Setting | Default | Behavior |
|---|---|---|
| `DEVICE_IP` | `auto` | Identity-only dummy with the selected LAN's existing address |
| `DEVICE_PREFIX` | `auto` | `/32` for automatic IP; parent prefix for static IP |
| `DEVICE_PARENT` | `auto` | Interface selected by the IPv4 route to 1.1.1.1 |

The route lookup sends no packet. On VPN or multi-interface hosts, explicitly set
`DEVICE_PARENT` to the LAN interface; the firewall helper uses the same setting.
`DEVICE_IP=none` creates an identity without a global IPv4 address and may leave
the vendor's device list empty.

A static `DEVICE_IP` selects a macvlan on `DEVICE_PARENT`. Reserve an unused address
on your LAN outside the dynamic DHCP pool; do not reuse the parent address.
Static mode is intended for wired Ethernet; Wi-Fi and VPN interfaces may not
support macvlan. Automatic mode does not scan for or allocate a free LAN address.

Only interfaces with alias `leigod-steamdeck-plugin` and type dummy/macvlan are
managed. A physical or unmarked `wlan0` is never automatically deleted, readdressed,
or claimed. A newly created identity uses a stable machine-id-derived MAC.
No competing main-table subnet route is installed. Static mode explicitly sets
the broadcast address to retain a local broadcast route with `noprefixroute`.

### Migrating the troubleshooting branch

Earlier experimental packages created an unmarked virtual `wlan0`. It remains
unmanaged after upgrade. If you verified that it belongs to this package, opt in:

```bash
ip -d link show wlan0
sudo /usr/lib/leigod/ensure-wlan0.sh adopt
sudo systemctl restart leigod
```

Adoption preserves its current MAC and addresses; subsequent setup applies your
configuration. After removal, a new identity is derived from machine-id. Legacy
interfaces that cloned a physical MAC may therefore need one rebind. Keep your
working static configuration in `/etc/leigod/device.conf`; rebuilding the package
with default settings is not a reason to replace that local configuration.

## Firewall integration

The vendor engine requires `iptables`/`ipset` for acceleration. The wrapper uses
`nftables` for the host's input policy; these are separate responsibilities.

If `inet filter input` exists, `firewall.sh` atomically installs two tagged accept
rules for UDP 6066 and TCP 5588, scoped to the selected LAN's IPv4 subnet and to its
interface or `wlan0`. Stop/removal deletes only rules with the package's exact
tags. It never flushes a host table or deletes rules based on a broad name match.

Other firewall layouts are reported for manual integration. Multiple input base
chains can still drop a packet after an accept in this chain. No package can
override every host policy safely by adding a separate accept chain. Reloading
the host ruleset may remove temporary rules: restart Leigod afterwards. SSH rules,
the host's default policy, and other tables remain unchanged.

## Stop, upgrade and uninstall

```bash
sudo systemctl stop leigod
sudo pacman -R leigod-steamdeck-plugin
```

Stopping removes package-tagged firewall rules and package-owned virtual `wlan0`,
including after a failed start. Uninstall also retries cleanup before removing
the helper scripts. Physical/unowned interfaces are preserved. Check any cleanup
warnings and remaining vendor routes, iptables/ipset entries yourself; the package
does not globally flush acceleration tables or promise that reboot clears policies
restored by other services.

Upgrade stops an active service before saving the binding file and starts it again
after restoration. Already inactive services stay inactive. A private backup under
`/var/lib/leigod-package-state` (directory 0700, file 0600) supports reinstall; it may
remain after uninstall. Remove it explicitly if you want to forget that binding.
The old shared `/tmp/leigod-bind-state.ini` is not imported or trusted.

Binding persistence is controlled by the closed-source engine. Earlier testing
observed writes during acceleration start/stop, so a discovery reply or UI binding
alone is not proof that a token has been saved to disk.

## Isolation and limits

The service has private PID/mount/device views, a root tmpfs and an explicit path
allowlist. It still shares the host network and has `CAP_NET_ADMIN`/`CAP_NET_RAW`:
the engine can modify host networking. Configuration, logs and binding state under
`/opt/leigod` remain writable. These controls are not a guarantee against malicious
behavior by privileged proprietary code.

The vendor updater can replace files under `/opt/leigod`; the build-time pin does
not attest later runtime updates. Downloads currently use HTTP with pinned SHA-256;
checksums provide reproducibility, not independent publisher authentication.
See `AUDIT.md` for package contents, limitations and verification scope.

## Tests and diagnostics

```bash
python3 test-device.py
python3 test-firewall.py
python3 test-sslh.py
python3 test-install.py
python3 test-diagnostics.py
```

Network tests create disposable user/network/mount namespaces and do not change
the host firewall or resolver configuration. They require unprivileged user
namespaces and the relevant kernel modules. Python and test tools are development
requirements, not runtime dependencies of the package.

```bash
python3 debug-binding-api.py
```

The diagnostic requires sudo, tcpdump and a running service. It verifies the DEBUG
acknowledgement, records a bounded attempt, returns the application to INFO and
prints metadata only. Raw files stay in a private temporary directory and should
be deleted when no longer needed. `debug-mobile-binding.sh` is a compatibility
entry point; the old hardcoded phone-IP/fd classifier is no longer used.

The tests and diagnostics stay in the repository and are not installed by the
runtime package. Read `TROUBLESHOOTING.md` before changing network settings.
