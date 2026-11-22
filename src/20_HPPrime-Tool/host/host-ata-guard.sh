#!/usr/bin/env bash
set -euo pipefail
umask 077

script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
output_root="$script_dir/../../../10_Backup/20261116"
parameter=/sys/module/usb_storage/parameters/quirks
state_dir=/run/hp-prime-ata-guard
action=${1:-status}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

merge_quirks() {
    local original=$1 entry vendor product flags found=0 merged=''
    local -a entries=()
    [[ -z "$original" || "$original" =~ ^[[:xdigit:]]{1,4}:[[:xdigit:]]{1,4}:[a-zA-Z]*(,[[:xdigit:]]{1,4}:[[:xdigit:]]{1,4}:[a-zA-Z]*)*$ ]] || return 1
    IFS=, read -r -a entries <<< "$original"
    for entry in "${entries[@]}"; do
        IFS=: read -r vendor product flags <<< "$entry"
        if ((16#$vendor == 0x03f0 && 16#$product == 0x0441)); then
            ((found == 0)) || return 1
            found=1
            if [[ "${flags,,}" != *t* ]]; then
                entry="${entry}t"
            fi
        fi
        merged+="${merged:+,}$entry"
    done
    if ((found == 0)); then
        merged+="${merged:+,}03f0:0441:t"
    fi
    ((${#merged} < 128)) || return 1
    printf '%s\n' "$merged"
}

archive_restored_state() {
    local source=$1 archive
    [[ -d "$source" && ! -L "$source" && -f "$source/restored" && ! -L "$source/restored" ]] || return 1
    archive=$(mktemp -d "${source}.restored-XXXXXX") || return 1
    mv -T -- "$source" "$archive/state" || return 1
    printf '%s\n' "$archive/state"
}

retry_self_test() (
    local temporary archive name
    temporary=$(mktemp -d)
    trap 'rm -rf -- "$temporary"' EXIT
    mkdir -- "$temporary/state"
    if archive_restored_state "$temporary/state" >/dev/null; then
        fail 'Unrestored state was archived.'
    fi
    for name in original expected kernel boot-id audit-dir restored; do
        printf '%s\n' "$name" > "$temporary/state/$name"
    done
    cp -a -- "$temporary/state" "$temporary/reference"
    archive=$(archive_restored_state "$temporary/state") || fail 'Restored state was not archived.'
    [[ ! -e "$temporary/state" && -d "$archive" ]] || fail 'Archive move failed.'
    for name in original expected kernel boot-id audit-dir restored; do
        cmp -- "$temporary/reference/$name" "$archive/$name" || fail 'Prior restore record changed.'
    done
    if archive_restored_state "$temporary/state" >/dev/null; then
        fail 'Missing state was accepted for another retry.'
    fi
    printf 'RETRY_SELF_TEST_OK: unrestored/missing state rejected; all prior records preserved.\n'
)

self_test() {
    local actual sample
    actual=$(merge_quirks '')
    [[ "$actual" == 03f0:0441:t ]] || fail 'Empty baseline test failed.'
    actual=$(merge_quirks '1234:abcd:u')
    [[ "$actual" == 1234:abcd:u,03f0:0441:t ]] || fail 'Unrelated quirk preservation failed.'
    actual=$(merge_quirks '03F0:0441:rs,1234:abcd:u')
    [[ "$actual" == 03F0:0441:rst,1234:abcd:u ]] || fail 'Target quirk merge failed.'
    actual=$(merge_quirks '3f0:441:T')
    [[ "$actual" == 3f0:441:T ]] || fail 'Existing quirk detection failed.'
    actual=$(merge_quirks '03f0:0441:')
    [[ "$actual" == 03f0:0441:t ]] || fail 'Empty target flags test failed.'
    for sample in '03f0:0441:r,3f0:441:s' 'bad' '1234:5678:u,' '1234:5678:u,,03f0:0441:t'; do
        if merge_quirks "$sample" >/dev/null; then
            fail 'Invalid or duplicate input was accepted.'
        fi
    done
    sample=$(printf '%0128d' 0)
    if merge_quirks "1234:5678:${sample//0/u}" >/dev/null; then
        fail 'Oversized parameter accepted.'
    fi
    printf 'SELF_TEST_OK: 10 offline cases; no device or system writes.\n'
}

require_disconnected() {
    local usb_path vendor product
    for usb_path in /sys/bus/usb/devices/*; do
        [[ -f "$usb_path/idVendor" ]] || continue
        read -r vendor < "$usb_path/idVendor" || fail 'USB state changed during inspection.'
        [[ "$vendor" == 03f0 ]] || continue
        read -r product < "$usb_path/idProduct" || fail 'USB state changed during inspection.'
        [[ "$product" != 0441 ]] || fail 'Disconnect HP Prime USB; keep the v16 screen.'
    done
}

require_state() {
    local name
    [[ -d "$state_dir" && ! -L "$state_dir" ]] || fail 'Trusted restore state is unavailable.'
    [[ $(stat -c '%u:%a' "$state_dir") == 0:700 ]] || fail 'Unexpected state ownership or permissions.'
    for name in original expected kernel boot-id audit-dir; do
        [[ -f "$state_dir/$name" && ! -L "$state_dir/$name" ]] || fail "Missing restore record: $name"
        [[ $(stat -c '%u:%a' "$state_dir/$name") == 0:600 ]] || fail "Untrusted restore record: $name"
    done
    [[ $(< "$state_dir/kernel") == "$(uname -r)" ]] || fail 'Kernel changed; refusing stale restore.'
    [[ $(< "$state_dir/boot-id") == "$(< /proc/sys/kernel/random/boot_id)" ]] || fail 'Boot changed; refusing stale restore.'
}

[[ $# -le 1 ]] || fail 'Usage: host-ata-guard.sh [status|self-test|apply|retry|verify|restore]'
case "$action" in
    self-test) self_test; retry_self_test; exit 0 ;;
    status)
        [[ -r "$parameter" ]] || fail 'usb_storage is not loaded or quirks is unreadable.'
        printf 'KERNEL=%s\nQUIRKS=%s\n' "$(uname -r)" "$(< "$parameter")"
        if [[ -e "$state_dir" || -L "$state_dir" ]]; then
            printf 'STATE_PRESENT: %s (does not prove quirk applied to a device)\n' "$state_dir"
        else
            printf 'NO_GUARD_STATE\n'
        fi
        require_disconnected
        printf 'RECOVERY_USB_DISCONNECTED\n'
        exit 0
        ;;
    apply|retry|verify|restore) ;;
    *) fail 'Usage: host-ata-guard.sh [status|self-test|apply|retry|verify|restore]' ;;
esac

[[ "$EUID" -eq 0 ]] || fail 'Run this script explicitly with sudo. No setting was changed.'
[[ -r "$parameter" && -w "$parameter" ]] || fail 'Loaded usb_storage quirks parameter is unavailable.'
require_disconnected

if [[ "$action" == verify ]]; then
    require_state
    [[ ! -e "$state_dir/restored" ]] || fail 'This guard session has already been restored.'
    [[ $(< "$parameter") == "$(< "$state_dir/expected")" ]] || fail 'Quirk no longer matches the prepared value.'
    printf 'GUARD_PARAMETER_VERIFIED: device behavior still requires observation.\n'
    exit 0
fi

previous_state=''
if [[ "$action" == apply || "$action" == retry ]]; then
    [[ $(uname -r) == 7.0.0-43-generic ]] || fail 'This kernel has not been reviewed.'
    [[ $(< /sys/module/usb_storage/srcversion) == 0081FFDEABA0DB0F46ADDB0 ]] || fail 'Loaded module changed since review.'
    [[ -d "$output_root" ]] || fail 'Dated backup directory is missing.'
    if [[ "$action" == retry ]]; then
        require_state
        exec {retry_lock_fd}< "$state_dir"
        flock -n "$retry_lock_fd" || fail 'Another guard operation is active.'
        require_state
        [[ -f "$state_dir/restored" && ! -L "$state_dir/restored" ]] || fail 'Restore the prior session before retrying.'
        [[ $(stat -c '%u:%a' "$state_dir/restored") == 0:600 ]] || fail 'Untrusted restored marker.'
        [[ $(< "$parameter") == "$(< "$state_dir/original")" ]] || fail 'Current quirks differ from restored baseline; refusing retry.'
        require_disconnected
        previous_state=$(archive_restored_state "$state_dir") || fail 'Prior restore records could not be archived; no setting was changed.'
        printf 'PREVIOUS_STATE_PRESERVED: %s\n' "$previous_state"
    fi
    [[ ! -e "$state_dir" && ! -L "$state_dir" ]] || fail 'A prior guard session exists; inspect or restore it instead of applying again.'
    original=$(< "$parameter")
    expected=$(merge_quirks "$original") || fail 'Malformed, duplicate target, or oversized quirks; refusing to modify.'
    [[ "$expected" != "$original" ]] || fail 'ATA guard is already configured; no new trial was prepared.'
    mkdir -m 0700 -- "$state_dir"
    exec {lock_fd}< "$state_dir"
    flock -n "$lock_fd" || fail 'Another guard operation is active.'
    audit_dir=$(mktemp -d "$output_root/ata-guard-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
    printf '%s\n' "$original" > "$state_dir/original"
    printf '%s\n' "$expected" > "$state_dir/expected"
    uname -r > "$state_dir/kernel"
    cp -- /proc/sys/kernel/random/boot_id "$state_dir/boot-id"
    chmod 0600 "$state_dir/boot-id"
    printf '%s\n' "$audit_dir" > "$state_dir/audit-dir"
    install -m 0600 -- "$state_dir/original" "$audit_dir/original-quirks.txt"
    install -m 0600 -- "$state_dir/expected" "$audit_dir/expected-quirks.txt"
    {
        printf 'UTC_PREPARED=%s\nKERNEL=%s\n' "$(date -u +%FT%TZ)" "$(uname -r)"
        sha256sum -- "$script_dir/host-ata-guard.sh"
        printf 'TARGET=03f0:0441 ADDED_FLAG=t STATE=%s\n' "$state_dir"
        printf 'PREVIOUS_STATE=%s\n' "$previous_state"
    } > "$audit_dir/session.log"
    sync -f "$audit_dir/original-quirks.txt"
    sync -f "$audit_dir/session.log"
    require_state
    require_disconnected
    [[ $(< "$parameter") == "$original" ]] || fail 'Quirks changed during preparation; no overwrite performed.'
    printf '%s\n' "$expected" > "$parameter" || fail "Apply failed; keep USB disconnected. Restore state: $state_dir"
    [[ $(< "$parameter") == "$expected" ]] || fail "Readback mismatch; keep USB disconnected. Restore state: $state_dir"
    printf 'UTC_APPLIED=%s READBACK_MATCH=yes\n' "$(date -u +%FT%TZ)" >> "$audit_dir/session.log"
    if [[ ${SUDO_UID:-} =~ ^[0-9]+$ && ${SUDO_GID:-} =~ ^[0-9]+$ && ${SUDO_UID} != 0 ]]; then
        chown -- "$SUDO_UID:$SUDO_GID" "$audit_dir/original-quirks.txt" "$audit_dir/expected-quirks.txt" "$audit_dir/session.log" "$audit_dir"
    fi
    printf 'APPLIED_AND_READBACK_VERIFIED: %s\nLOG_DIRECTORY: %s\n' "$expected" "$audit_dir"
    printf 'Keep USB disconnected until the guarded trace is ready. No device command was sent.\n'
else
    require_state
    exec {lock_fd}< "$state_dir"
    flock -n "$lock_fd" || fail 'Another guard operation is active.'
    original=$(< "$state_dir/original")
    expected=$(< "$state_dir/expected")
    current=$(< "$parameter")
    [[ "$current" == "$expected" || "$current" == "$original" ]] || fail 'Current quirks conflict with saved values; refusing to overwrite another change.'
    require_disconnected
    printf '%s\n' "$original" > "$parameter" || fail 'Restore write failed; keep USB disconnected.'
    [[ $(< "$parameter") == "$original" ]] || fail 'Restore readback mismatch; keep USB disconnected.'
    printf 'UTC_RESTORED=%s\n' "$(date -u +%FT%TZ)" > "$state_dir/restored"
    printf 'RESTORED_AND_READBACK_VERIFIED: original quirks restored; state retained at %s\n' "$state_dir"
fi
