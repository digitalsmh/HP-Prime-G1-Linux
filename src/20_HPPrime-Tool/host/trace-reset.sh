#!/usr/bin/env bash
set -euo pipefail
umask 077

script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
trace_source="$script_dir/trace-reset.bt"
output_root="$script_dir/../../../10_Backup/20261116"
action=${1:-help}
trace_tee=(tee --output-error=warn-nopipe)

assess_guard_trace() {
    awk '
        /^[0-9]+ DEVICE_FLAGS / {addresses[$3]=1}
        /^[0-9]+ ATA_QUEUE / && / gate=1$/ {gates++}
        /^[0-9]+ ATA_QUEUE / && / gate=0$/ {failures++}
        /^[0-9]+ ATA_DONE result=0x2$/ {rejections++}
        /^[0-9]+ ATA_DONE / && !/ result=0x2$/ {failures++}
        /^[0-9]+ SCSI / {
            reads[$3] = index($0, "cdb=\\x28") > 0
            if (index($0, "cdb=\\xa1") || index($0, "cdb=\\x85")) leaks++
            if (index($0, "cdb=\\x0a") || index($0, "cdb=\\x2a") || index($0, "cdb=\\xaa") || index($0, "cdb=\\x8a")) writes++
        }
        /^[0-9]+ TRANSPORT_RETURN / {
            if ($NF != "result=0") failures++
            else if (reads[$3]) good_reads++
            delete reads[$3]
        }
        /^[0-9]+ (RESET_ENTER|DISCONNECT|SUBMIT_ERROR) / {failures++}
        /[Ll]ost|[Dd]ropped|ERROR|^[[:space:]]*@/ {failures++}
        /^TRACE_STOP: 20-second target window ended;/ {window++}
        /^TRACE_SUMMARY events=[1-9][0-9]* resets=0;/ {summary++}
        END {
            for (address in addresses) devices++
            printf "GUARD_ASSESSMENT gates=%d rejections=%d good_reads=%d ata_usb=%d writes=%d failures=%d addresses=%d full_window=%d summary=%d\n", gates, rejections, good_reads, leaks, writes, failures, devices, window, summary
            if (gates < 1 || rejections != gates || good_reads < 1 || leaks || writes || failures || devices != 1 || window != 1 || summary != 1) exit 3
        }
    '
}

assessment_self_test() {
    local baseline changed replacement
    baseline='1 DEVICE_FLAGS addr=114 flags=0x2000000
2 ATA_QUEUE pid=1 comm=test addr=114 opcode=0xa1 gate=1
3 ATA_DONE result=0x2
4 SCSI pid=1 comm=test addr=114 cdb=\x28\x00
5 TRANSPORT_RETURN pid=1 comm=test result=0
TRACE_STOP: 20-second target window ended; disconnect USB now.
TRACE_SUMMARY events=2 resets=0; no payload or serial captured.'
    printf '%s\n' "$baseline" | assess_guard_trace >/dev/null || fail 'Positive guard assessment failed.'
    for replacement in ATA_QUEUE ATA_DONE SCSI '20-second' TRACE_SUMMARY; do
        changed=${baseline/"$replacement"/OMITTED}
        if printf '%s\n' "$changed" | assess_guard_trace >/dev/null; then
            fail "Missing evidence was accepted: $replacement"
        fi
    done
    for changed in '6 RESET_ENTER pid=1' '6 DISCONNECT addr=114' 'Lost 1 events' '6 SCSI pid=1 cdb=\xa1' '6 SCSI pid=1 cdb=\x85' '6 DEVICE_FLAGS addr=115 flags=0x2000000' '6 TRANSPORT_RETURN pid=1 result=3' '6 ATA_QUEUE pid=1 gate=0' '6 ATA_QUEUE pid=1 gate=1' '6 ATA_DONE result=0x10000' '6 SCSI pid=1 cdb=\x0a' '6 SCSI pid=1 cdb=\x2a' '6 SCSI pid=1 cdb=\xaa' '6 SCSI pid=1 cdb=\x8a'; do
        if printf '%s\n%s\n' "$baseline" "$changed" | assess_guard_trace >/dev/null; then
            fail "Negative guard assessment was accepted: $changed"
        fi
    done
    if printf '' | assess_guard_trace >/dev/null; then
        fail 'Empty evidence was accepted.'
    fi
    printf 'ASSESSMENT_SELF_TEST_OK: 21 offline cases; no probes attached.\n'
}

logging_self_test() (
    local temporary statuses
    temporary=$(mktemp -d)
    trap 'rm -rf -- "$temporary"' EXIT
    {
        printf '%1048576s\n' ''
        printf 'TRACE_END_TEST\n'
    } > "$temporary/expected"
    if cat "$temporary/expected" | tee "$temporary/old" | head -c 0; then
        fail 'Closed display pipe did not reproduce the old failure.'
    else
        statuses=("${PIPESTATUS[@]}")
    fi
    [[ "${statuses[1]}" -eq 141 ]] || fail 'Old tee did not report SIGPIPE.'
    if cat "$temporary/expected" | "${trace_tee[@]}" "$temporary/actual" | head -c 0; then
        statuses=("${PIPESTATUS[@]}")
    else
        fail 'Closed display pipe interrupted capture.'
    fi
    [[ "${statuses[*]}" == '0 0 0' ]] || fail 'Capture pipeline failed.'
    cmp -- "$temporary/expected" "$temporary/actual" || fail 'Capture was truncated.'
    if cat "$temporary/expected" | "${trace_tee[@]}" /dev/full >/dev/null 2> "$temporary/error"; then
        fail 'Storage failure was accepted.'
    else
        statuses=("${PIPESTATUS[@]}")
    fi
    [[ "${statuses[1]}" -ne 0 && -s "$temporary/error" ]] || fail 'Storage failure was not diagnosed.'
    printf 'LOGGING_SELF_TEST_OK: old SIGPIPE reproduced; closed display preserves all bytes; storage failure rejected.\n'
)

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

require_disconnected() {
    local usb_path vendor product
    for usb_path in /sys/bus/usb/devices/*; do
        [[ -f "$usb_path/idVendor" ]] || continue
        read -r vendor < "$usb_path/idVendor" || fail 'USB state changed during inspection.'
        [[ "$vendor" == 03f0 ]] || continue
        read -r product < "$usb_path/idProduct" || fail 'USB state changed during inspection.'
        [[ "$product" != 0441 ]] || fail 'Keep HP Prime USB disconnected before starting this tool.'
    done
}

if [[ "$action" == analyze-guard ]]; then
    [[ $# -eq 2 && -f "$2" ]] || fail 'Usage: trace-reset.sh analyze-guard TRACE_LOG'
    assess_guard_trace < "$2"
    exit $?
fi
[[ $# -le 1 ]] || fail 'Usage: trace-reset.sh [check|capture|guard-check|guard-capture|self-test]'
case "$action" in
    help)
        printf 'Usage: trace-reset.sh [check|capture|guard-check|guard-capture|self-test|analyze-guard TRACE_LOG]\n'
        printf 'Administrator execution required; start with HP Prime USB disconnected.\n'
        exit 0
        ;;
    self-test) assessment_self_test; logging_self_test; exit 0 ;;
    check|capture|guard-check|guard-capture) ;;
    *) fail 'Unknown trace action.' ;;
esac

[[ "$EUID" -eq 0 ]] || fail 'Run this script explicitly with sudo. No tracing was started.'
[[ $(uname -m) == x86_64 ]] || fail 'This diagnostic is prepared for the current x86_64 host only.'
[[ -f "$trace_source" && ! -L "$trace_source" ]] || fail 'Trace source is missing or is a symlink.'
[[ -d "$output_root" ]] || fail 'Expected dated backup directory is missing.'
[[ -r /sys/kernel/btf/vmlinux && -r /sys/kernel/btf/usb_storage ]] || fail 'Required kernel/module BTF is unavailable; no modules will be loaded.'
command -v bpftrace >/dev/null || fail 'bpftrace is unavailable.'
command -v timeout >/dev/null || fail 'timeout is unavailable.'
require_disconnected
require_probe_rule() {
    local status
    status=$(/bin/bash "$script_dir/host-probe-control.sh" status) || fail 'udev rule status check failed; do not connect USB.'
    [[ "$status" == FILE_PRESENT:* ]] || fail 'udev no-probe/read-only rule is not installed; do not connect USB.'
}
if [[ "$action" == guard-* ]]; then
    /bin/bash "$script_dir/host-ata-guard.sh" verify
    require_probe_rule
fi

output_dir=$(mktemp -d "$output_root/usb-trace-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
finish() {
    local result=$?
    trap - EXIT
    if [[ ${SUDO_UID:-} =~ ^[0-9]+$ && ${SUDO_GID:-} =~ ^[0-9]+$ && ${SUDO_UID} != 0 ]]; then
        chown -- "$SUDO_UID:$SUDO_GID" "$output_dir/"*.log "$output_dir" || true
    fi
    printf 'LOG_DIRECTORY: %s\n' "$output_dir"
    exit "$result"
}
trap finish EXIT

{
    printf 'UTC_START=%s\n' "$(date -u +%FT%TZ)"
    printf 'KERNEL=%s\n' "$(uname -r)"
    bpftrace --version
    sha256sum -- "$trace_source" "$script_dir/trace-reset.sh"
    printf 'TARGET=03f0:0441 PORT=3-10.2 MODE=%s\n' "$action"
} > "$output_dir/session.log"

printf 'PREFLIGHT: keep USB disconnected; validating and attaching/detaching probes.\n'
if ! timeout --signal=INT --kill-after=5s 30s bpftrace --dry-run "$trace_source" > "$output_dir/preflight.log" 2>&1; then
    cat -- "$output_dir/preflight.log"
    fail 'Probe validation failed. Do not connect USB; send the log directory for analysis.'
fi
printf 'PREFLIGHT_OK: probes validated and detached.\n'
[[ "$action" == capture || "$action" == guard-capture ]] || exit 0
require_disconnected
if [[ "$action" == guard-capture ]]; then
    /bin/bash "$script_dir/host-ata-guard.sh" verify
    require_probe_rule
fi

printf 'Wait for TRACE_READY below before connecting USB once.\n'
set +e
timeout --signal=INT --kill-after=5s 95s bpftrace -B line "$trace_source" 2>&1 | "${trace_tee[@]}" "$output_dir/trace.log" 2> "$output_dir/tee.log"
pipeline_status=("${PIPESTATUS[@]}")
trace_result=${pipeline_status[0]}
set -e
printf 'UTC_END=%s TRACE_EXIT=%s TEE_EXIT=%s\n' "$(date -u +%FT%TZ)" "$trace_result" "${pipeline_status[1]}" >> "$output_dir/session.log"
printf 'Disconnect HP Prime USB now; keep the v16 screen. Do not reconnect.\n'
[[ "${pipeline_status[1]}" -eq 0 ]] || fail 'Trace log storage failed; retained data may be incomplete.'
[[ "$trace_result" -eq 0 ]] || fail "Trace exited with status $trace_result; inspect retained logs."
if [[ "$action" == guard-capture ]]; then
    if assess_guard_trace < "$output_dir/trace.log"; then
        printf 'GUARDED_WINDOW_OK: observed ATA rejection and successful reads without reset/disconnect in this window only.\n'
    else
        printf 'GUARDED_WINDOW_NOT_PROVEN: inspect logs; do not reconnect or retry.\n'
        exit 3
    fi
    printf 'Restore the original quirks AFTER disconnecting USB. No backup was attempted.\n'
    exit 0
fi
if ! grep -Eq '^TRACE_SUMMARY events=[1-9][0-9]* resets=' "$output_dir/trace.log"; then
    fail 'No target events captured. An empty capture is not a successful diagnosis.'
fi
if ! grep -Eq '^[0-9]+ RESET_ENTER ' "$output_dir/trace.log"; then
    printf 'PARTIAL_CAPTURE: target traffic exists but no usb_reset_device call was captured.\n'
    exit 3
fi
printf 'CAPTURE_SAVED: target reset recorded; cause still requires log analysis.\n'
