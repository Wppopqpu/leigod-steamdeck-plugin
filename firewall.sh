#!/bin/bash

set -eu
set -o pipefail

# Integrate with the host's existing input chain. An accept in a separate
# base chain cannot override a later drop; jumps cannot cross tables.
FILTER_CHAIN=(inet filter input)
RULES=""

read_rules() {
    local tables chains
    # Distinguish an absent chain from a failed/unauthorized netlink query.
    tables=$(/usr/bin/nft list tables) || return 1
    if ! grep -qx 'table inet filter' <<<"$tables"; then
        return 2
    fi
    chains=$(/usr/bin/nft list chains inet filter) || return 1
    if ! grep -qE '^[[:space:]]*chain input \{' <<<"$chains"; then
        return 2
    fi
    RULES=$(/usr/bin/nft -a list chain "${FILTER_CHAIN[@]}")
}

delete_commands() {
    # Only delete rules carrying our exact ownership markers.
    awk '/comment "leigod-steamdeck-plugin:(discovery|control)"/ {
        for (i = 1; i < NF; i++)
            if ($i == "handle" && $(i + 1) ~ /^[0-9]+$/)
                print "delete rule inet filter input handle " $(i + 1)
    }' <<<"$RULES"
}

setup() {
    local status route iface source cidr parent=auto key value
    if read_rules; then
        :
    else
        status=$?
        if [ "$status" -eq 2 ]; then
            printf 'leigod: inet filter input is absent; configure UDP 6066 and TCP 5588 in your firewall if needed.\n' >&2
            return 0
        fi
        return "$status"
    fi

    if [ -r /etc/leigod/device.conf ]; then
        while IFS='=' read -r key value || [ -n "$key" ]; do
            key=${key//[[:space:]]/}
            value=${value%%#*}
            value=${value//[[:space:]]/}
            [ "$key" != DEVICE_PARENT ] || parent=$value
        done < /etc/leigod/device.conf
    fi
    if [ "$parent" = auto ]; then
        route=$(/usr/bin/ip -4 route get 1.1.1.1) || return 1
        iface=$(awk '{for(i=1;i<NF;i++) if($i=="dev") {print $(i+1); exit}}' <<<"$route")
        source=$(awk '{for(i=1;i<NF;i++) if($i=="src") {print $(i+1); exit}}' <<<"$route")
    else
        iface=$parent
        source=''
    fi
    if ! [[ "$iface" =~ ^[a-zA-Z0-9_.:-]+$ ]]; then
        printf 'leigod: cannot determine the LAN interface\n' >&2
        return 1
    fi
    cidr=$(/usr/bin/ip -o -4 addr show dev "$iface" scope global |
        awk -v source="$source" '{split($4,a,"/"); if(source=="" || a[1]==source) {print $4; exit}}')
    if ! [[ "$cidr" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ ]]; then
        printf 'leigod: cannot determine the LAN subnet\n' >&2
        return 1
    fi

    # Replace our two rules atomically; preserve all host rules and policies.
    {
        delete_commands
        printf 'insert rule inet filter input iifname { "%s", "wlan0" } ip saddr %s udp dport 6066 counter accept comment "leigod-steamdeck-plugin:discovery"\n' "$iface" "$cidr"
        printf 'insert rule inet filter input iifname { "%s", "wlan0" } ip saddr %s tcp dport 5588 counter accept comment "leigod-steamdeck-plugin:control"\n' "$iface" "$cidr"
    } | /usr/bin/nft -f -
}

cleanup() {
    local status commands
    if read_rules; then
        commands=$(delete_commands)
        if [ -n "$commands" ]; then
            /usr/bin/nft -f - <<<"$commands"
        fi
    else
        status=$?
        [ "$status" -eq 2 ] || return "$status"
    fi
}

case "${1:-setup}" in
    setup) setup ;;
    cleanup) cleanup ;;
    *)
        printf 'usage: %s {setup|cleanup}\n' "$0" >&2
        exit 2
        ;;
esac
