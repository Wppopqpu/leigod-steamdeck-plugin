# Troubleshooting

## Confirmed failures from the binding investigation

### Discovery reaches packet capture, but not the UDP socket

The host's `inet filter input` chain had policy `drop` and no allow rules for
UDP 6066 or TCP 5588. Packet capture showed the phone's discovery broadcasts,
while the vendor thread remained blocked in `recvfrom`. Observing a frame in
tcpdump does **not** prove delivery through the input firewall to an application.

Inspect the actual host rules and counters. An ACCEPT in an independent base
chain (or an iptables table) does not override a later nftables drop. The current
helper inserts scoped rules directly in the existing input chain, without jumps
to other tables. See the firewall section in README for its supported layout.

### WebSocket handshake stalls after discovery succeeds

The phone sent a WebSocket upgrade request to TCP 5588. sslh immediately connected
to TCP 5589, but forwarded the request roughly ten seconds later. The backend
closed after about five seconds; the late request received a reset.

In sslh-fork 2.3.1, `start_shoveler()` calls `get_connection_desc()` before
`flush_deferred()`. `sprintaddr()` uses `getnameinfo()` for log addresses. Reverse
DNS can therefore block forwarding before the first request is delivered.

`run.sh` now launches sslh with `-n`. The command-line option also works when pacman
preserves an older sslh configuration. An isolated unresponsive-DNS test reproduces
the old ten-second stall; numeric logging obtains HTTP 101 in approximately 1 ms
in the same test. The phone user confirmed successful binding after this fix.

## What the investigation did not establish

The initial claims that dummy interfaces, /32 prefixes or shared identity addresses
were the original cause were not proven. Fixing the host input policy and the sslh
DNS stall established the two actual blockers. A new regression test verifies LAN
discovery delivery to a wildcard UDP socket while the identity interface is dummy.

Experimental /24 changes did introduce a competing main-table route via the dummy;
that regression was observed and corrected. `noprefixroute` need not suppress local
broadcast routing: explicitly providing `broadcast +` preserves the local route.
Static-address macvlan remains an optional tested configuration, not a universal
requirement for discovery. Do not migrate working physical interfaces blindly.

## Evidence-driven diagnostic order

1. Check `systemctl status leigod --no-pager -l` and recent journal entries.
2. Confirm the current owner of UDP 6066 with `sudo ss -uapn`. File descriptor
   numbers change across process restarts; do not hardcode one.
3. Compare discovery capture with the input chain's rules/counters. A blocked
   `recvfrom` alone cannot distinguish firewall drops from missing packets.
4. Check a completed receive **from the phone** and a successful send to its port.
   Cloud heartbeats and ICMP probes are unrelated; resumed strace calls matter.
5. Inspect phone -> 5588 -> loopback 5589 timing, HTTP 101 and close/reset events.
   A successful local handshake does not prove a LAN handshake works: local names
   may resolve immediately while phone reverse DNS stalls.
6. Only after handshake success, investigate application commands and vendor
   authentication. Do not print or replace credentials to "fix" discovery.

`debug-binding-api.py` captures a bounded attempt and prints metadata without HTTP
URLs, headers or bodies. Its packet summary is not TCP reassembly; duplicated
interface captures and split headers limit negative conclusions. Keep raw logs
private. The legacy shell entry point delegates to the new diagnostic.

## Lifecycle and acceleration checks

- **Unmanaged wlan0:** Setup preserves it. For an old package-created dummy/macvlan,
  explicitly adopt it before expecting automatic cleanup. Never adopt a physical
  interface. Stop/start on an owned virtual interface removes and recreates it.
- **`/var/run/acc_daemon.lock` missing:** Root tmpfs views are separate for systemd
  commands. The launcher creates `/var/run` in its own view before executing the
  monitor. An `ExecStartPre=mkdir` does not persist it into that view.
- **TUN creation failure:** Verify the host module/device, `ipset`, and the service's
  `/dev/net/tun` bind and device allowance. A missing node is not fixed by only
  adding a cgroup device permission.
- **Discovery works, binding lost after restart:** Check persistence without
  exposing token contents. A bound UI or discovery flag is not sufficient proof.
- **Host firewall reload:** Restart Leigod to recreate tagged rules. A different
  chain layout or additional dropping base chain requires host-specific integration.
- **Uninstall leftovers:** Examine ownership alias and exact nftables comment tags.
  Do not globally flush mangle, delete arbitrary wlan0, or delete other firewall tables.

Historical migration notes remain in the source repository's `MIGRATION.md`.
They are observations of earlier experiments, not a specification of current behavior.
