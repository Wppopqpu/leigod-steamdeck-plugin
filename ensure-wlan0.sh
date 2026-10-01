#!/bin/bash
# Manage only virtual interfaces carrying this package's ownership marker.
set -euo pipefail

OWNER=leigod-steamdeck-plugin
CONFIG=/etc/leigod/device.conf

exists() { ip link show dev wlan0 >/dev/null 2>&1; }
kind() { ip -d link show dev wlan0 | awk '$1 == "dummy" || $1 == "macvlan" {print $1; exit}'; }
owned() {
    [ "$(cat /sys/class/net/wlan0/ifalias 2>/dev/null)" = "$OWNER" ] &&
        [[ "$(kind)" =~ ^(dummy|macvlan)$ ]]
}
fail() { printf 'leigod: %s\n' "$*" >&2; return 1; }

cleanup() {
    if exists && owned; then
        ip link delete dev wlan0
    fi
}

adopt() {
    exists || { fail 'no wlan0 to adopt'; return 1; }
    [[ "$(kind)" =~ ^(dummy|macvlan)$ ]] || {
        fail 'only a dummy or macvlan may be explicitly adopted'; return 1;
    }
    local alias
    alias=$(cat /sys/class/net/wlan0/ifalias)
    [ -z "$alias" ] || [ "$alias" = "$OWNER" ] || {
        fail 'wlan0 already has another ownership alias'; return 1;
    }
    # Explicit operator action only: preserve the existing MAC and addresses.
    ip link set dev wlan0 alias "$OWNER"
}

setup() {
    if exists && ! owned; then
        printf 'leigod: preserving unmanaged wlan0 (including its MAC and addresses).\n' >&2
        return 0
    fi

    local DEVICE_IP=auto DEVICE_PREFIX=auto DEVICE_PARENT=auto
    local key value route='' source='' parent='' cidr='' type prefix address mac
    if [ -r "$CONFIG" ]; then
        # Parse data, never execute configuration as shell code.
        while IFS='=' read -r key value || [ -n "$key" ]; do
            key=${key//[[:space:]]/}
            value=${value%%#*}
            value=${value//[[:space:]]/}
            case "$key" in
                DEVICE_IP) DEVICE_IP=$value ;;
                DEVICE_PREFIX) DEVICE_PREFIX=$value ;;
                DEVICE_PARENT) DEVICE_PARENT=$value ;;
            esac
        done < "$CONFIG"
    fi

    case "$DEVICE_IP" in
        auto|none) type=dummy ;;
        *)
            type=macvlan
            awk -v ip="$DEVICE_IP" 'BEGIN {
                if (split(ip,a,".") != 4) exit 1;
                for (i=1;i<=4;i++) if (a[i] !~ /^[0-9]+$/ || a[i] > 255) exit 1;
            }' || { fail 'DEVICE_IP must be auto, none, or an IPv4 address'; return 1; }
            ;;
    esac

    if [ "$DEVICE_IP" != none ]; then
        if [ "$DEVICE_PARENT" = auto ]; then
            route=$(ip -4 route get 1.1.1.1) || { fail 'no IPv4 route; set DEVICE_PARENT for your LAN'; return 1; }
            parent=$(awk '{for(i=1;i<NF;i++) if($i=="dev") {print $(i+1); exit}}' <<<"$route")
            source=$(awk '{for(i=1;i<NF;i++) if($i=="src") {print $(i+1); exit}}' <<<"$route")
        else
            parent=$DEVICE_PARENT
        fi
        [[ "$parent" =~ ^[a-zA-Z0-9_.:-]+$ ]] && [ "$parent" != wlan0 ] || {
            fail 'DEVICE_PARENT must select a LAN interface other than wlan0'; return 1;
        }
        cidr=$(ip -o -4 addr show dev "$parent" scope global |
            awk -v source="$source" '{split($4,a,"/"); if(source=="" || a[1]==source) {print $4; exit}}')
        [ -n "$cidr" ] || { fail "no global IPv4 address on $parent"; return 1; }
        if [ "$DEVICE_IP" = auto ]; then
            address=${cidr%/*}
            # Identity-only dummy aliases share the real host address safely
            # without creating another LAN endpoint or connected subnet route.
            prefix=32
        else
            address=$DEVICE_IP
            [ "$address" != "${cidr%/*}" ] || {
                fail 'a macvlan requires a separate, reserved LAN address'; return 1;
            }
            prefix=${cidr#*/}
        fi
        [ "$DEVICE_PREFIX" = auto ] || prefix=$DEVICE_PREFIX
        [[ "$prefix" =~ ^[0-9]{1,2}$ ]] && ((10#$prefix >= 1 && 10#$prefix <= 32)) || {
            fail 'DEVICE_PREFIX must be auto or a prefix from 1 to 32'; return 1;
        }
    fi

    # Preserve an existing owned identity, including explicitly adopted legacy
    # interfaces. A machine-id-derived MAC is used only for a new identity.
    if exists; then
        mac=$(cat /sys/class/net/wlan0/address)
    else
        [ -s /etc/machine-id ] || { fail 'machine-id is required for a stable identity'; return 1; }
        mac="02:$(md5sum /etc/machine-id | cut -c1-10 | sed 's/\(..\)/&:/g;s/:$//')"
    fi
    if exists; then
        if [ "$(kind)" != "$type" ] ||
            { [ "$type" = macvlan ] && [ "$(cat /sys/class/net/wlan0/iflink)" != "$(cat "/sys/class/net/$parent/ifindex")" ]; }; then
            cleanup
        fi
    fi
    if ! exists; then
        if [ "$type" = macvlan ]; then
            ip link add link "$parent" name wlan0 address "$mac" type macvlan mode bridge
        else
            ip link add name wlan0 address "$mac" type dummy
        fi
        # Set the alias separately: some iproute2 versions ignore alias on add.
        ip link set dev wlan0 alias "$OWNER" || { ip link delete dev wlan0; return 1; }
    fi
    ip link set dev wlan0 up
    if [ "$DEVICE_IP" = none ]; then
        ip -4 addr flush dev wlan0 scope global
        return 0
    fi
    # Validate configuration above before replacing addresses on an owned link.
    # Explicit broadcast + creates the local broadcast route even with
    # noprefixroute; no temporary competing main-table route is introduced.
    local current
    current=$(ip -o -4 addr show dev wlan0 scope global | awk '{print $4}')
    if [ "$current" != "$address/$prefix" ]; then
        ip -4 addr flush dev wlan0 scope global
    fi
    ip -4 addr replace "$address/$prefix" broadcast + dev wlan0 noprefixroute
}

case "${1:-setup}" in
    setup) setup ;;
    cleanup) cleanup ;;
    adopt) adopt ;;
    *) printf 'Usage: %s {setup|cleanup|adopt}\n' "$0" >&2; exit 2 ;;
esac
