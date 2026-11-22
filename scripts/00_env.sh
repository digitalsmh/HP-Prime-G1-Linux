#!/usr/bin/env bash
# 00_env.sh — shared settings for the HP Prime V3 Linux reconnect scripts (sourced, not run).
# 2026-11-19. Calculator: HP Prime G1 G8X92AA, V3 BOOT1 + LINUX.DAT, RNDIS gadget 04d8:1234.
HPP_IP=192.168.66.1                 # calculator (static, /etc/network/interfaces)
HPP_LAN_IP=192.168.123.165          # calculator USB-to-LAN IP (DHCP from AP)
HPP_USER=root
HPP_PASS=root                       # buildroot default; used only via a temp SSH_ASKPASS file
HPP_NM_CON=hpprime-rndis            # NetworkManager profile (no default route, no DNS)
HPP_USB_GADGET=04d8:1234            # V3 Linux RNDIS gadget
HPP_USB_RECOVERY=03f0:0441          # HP recovery mode (Reset+Symb) — scripts here never write to it
HPP_SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=60)

hpp_log() { printf '[%s] %s\n' "$(date +%T)" "$*"; }

# hpp_ssh CMD... : run a command on the calculator non-interactively (password via temp askpass).
hpp_ssh() {
    local a rc
    a=$(mktemp /tmp/hpp-askpass.XXXXXX)
    printf '#!/bin/sh\necho %s\n' "$HPP_PASS" > "$a"; chmod 700 "$a"
    SSH_ASKPASS="$a" SSH_ASKPASS_REQUIRE=force DISPLAY="${DISPLAY:-:0}" \
        ssh "${HPP_SSH_OPTS[@]}" "$HPP_USER@$HPP_IP" "$@"
    rc=$?; rm -f "$a"; return $rc
}

# hpp_scp SRC DST : scp with legacy protocol (-O; dropbear has no SFTP server).
hpp_scp() {
    local a rc
    a=$(mktemp /tmp/hpp-askpass.XXXXXX)
    printf '#!/bin/sh\necho %s\n' "$HPP_PASS" > "$a"; chmod 700 "$a"
    SSH_ASKPASS="$a" SSH_ASKPASS_REQUIRE=force DISPLAY="${DISPLAY:-:0}" \
        scp -O "${HPP_SSH_OPTS[@]}" "$@"
    rc=$?; rm -f "$a"; return $rc
}

# hpp_iface : current PC-side RNDIS interface name (changes every boot because the gadget MAC is random).
hpp_iface() {
    local d
    for d in /sys/class/net/*; do
        [ "$(readlink -f "$d/device/driver" 2>/dev/null | xargs -r basename)" = rndis_host ] || continue
        basename "$d"; return 0
    done
    return 1
}

# hpp_ssh_lan CMD... : run a command on the calculator via USB-Ethernet LAN.
hpp_ssh_lan() {
    local a rc
    a=$(mktemp /tmp/hpp-askpass.XXXXXX)
    printf '#!/bin/sh\necho %s\n' "$HPP_PASS" > "$a"; chmod 700 "$a"
    SSH_ASKPASS="$a" SSH_ASKPASS_REQUIRE=force DISPLAY="${DISPLAY:-:0}" \
        ssh "${HPP_SSH_OPTS[@]}" "$HPP_USER@$HPP_LAN_IP" "$@" 2> >(grep -v '^\*\*' >&2)
    rc=$?; rm -f "$a"; return $rc
}

