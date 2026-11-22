#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
rule_name=59-hp-prime-prepatch-no-probe.rules
source_rule="$script_dir/$rule_name"
target_rule="/run/udev/rules.d/$rule_name"
action=${1:-status}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

verify_source() {
    [[ -f "$source_rule" && ! -L "$source_rule" ]] || fail 'Rule source is missing or is a symlink.'
    udevadm verify "$source_rule"
}

require_disconnected() {
    local usb_path vendor product
    for usb_path in /sys/bus/usb/devices/*; do
        [[ -f "$usb_path/idVendor" ]] || continue
        read -r vendor < "$usb_path/idVendor" || fail 'USB state changed during inspection.'
        [[ "$vendor" == 03f0 ]] || continue
        read -r product < "$usb_path/idProduct" || fail 'USB state changed during inspection.'
        [[ "$product" != 0441 ]] || fail 'Disconnect HP Prime USB while keeping the v16 screen, then retry.'
    done
}

require_matching_target() {
    [[ -f "$target_rule" && ! -L "$target_rule" ]] || fail 'Installed path is not a regular file; refusing to change it.'
    cmp -s -- "$source_rule" "$target_rule" || fail 'Installed rule differs from source; refusing to overwrite or remove it.'
}

[[ $# -le 1 ]] || fail 'Usage: host-probe-control.sh [verify|status|install|remove]'
case "$action" in
    verify)
        verify_source
        exit 0
        ;;
    status)
        if [[ -e "$target_rule" || -L "$target_rule" ]]; then
            require_matching_target
            printf 'FILE_PRESENT: %s (matches source; reload and device behavior not verified)\n' "$target_rule"
        else
            printf 'NOT_INSTALLED: %s\n' "$target_rule"
        fi
        exit 0
        ;;
    install|remove) ;;
    *) fail 'Usage: host-probe-control.sh [verify|status|install|remove]' ;;
esac

[[ "$EUID" -eq 0 ]] || fail 'Administrator access required. Run this script explicitly with sudo.'
require_disconnected
verify_source

if [[ "$action" == install ]]; then
    for rules_dir in /etc/udev/rules.d /usr/local/lib/udev/rules.d /usr/lib/udev/rules.d; do
        [[ ! -e "$rules_dir/$rule_name" && ! -L "$rules_dir/$rule_name" ]] || fail "Conflicting rule path: $rules_dir/$rule_name"
    done
    [[ -d /run/udev/rules.d && ! -L /run/udev/rules.d ]] || fail 'Expected /run/udev/rules.d directory is unavailable.'
    if [[ -e "$target_rule" || -L "$target_rule" ]]; then
        require_matching_target
    else
        temporary_rule=$(mktemp /run/udev/rules.d/.hp-prime-prepatch.XXXXXX)
        trap 'rm -f -- "$temporary_rule"' EXIT
        install -m 0644 -o 0 -g 0 -T -- "$source_rule" "$temporary_rule"
        ln -T -- "$temporary_rule" "$target_rule"
        rm -- "$temporary_rule"
        trap - EXIT
    fi
    require_matching_target
    udevadm control --reload-rules || fail 'Reload failed; the rule file remains installed. Keep USB disconnected and run remove to roll back.'
    printf 'INSTALLED_AND_RELOADED: %s\n' "$target_rule"
    printf 'Keep USB disconnected until the next controlled check. No device event was triggered.\n'
else
    if [[ -e "$target_rule" || -L "$target_rule" ]]; then
        require_matching_target
        rm -- "$target_rule"
    fi
    udevadm control --reload-rules || fail 'Rule file is absent but reload failed. Keep USB disconnected and rerun remove.'
    printf 'REMOVED_AND_RELOADED: %s\n' "$target_rule"
fi
