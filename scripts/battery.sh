#!/usr/bin/env bash
# battery.sh — Check HP Prime G1 Battery & Power status
# Works both inside calculator (/usr/bin/battery.sh) and from host PC (remote via SSH).
set -euo pipefail

# Check if running locally on calculator or on PC
if [ -f /etc/init.d/rcS ] && uname -m | grep -q "arm"; then
    IS_CALCULATOR=1
else
    IS_CALCULATOR=0
fi

if [ "$IS_CALCULATOR" -eq 1 ]; then
    # Running inside calculator
    if [ -x /usr/bin/hpp-batt ]; then
        exec /usr/bin/hpp-batt "$@"
    elif [ -x "$(dirname "$0")/hpp-batt" ]; then
        exec "$(dirname "$0")/hpp-batt" "$@"
    fi

    # Fallback to devmem
    if ! which devmem >/dev/null 2>&1; then
        echo "Error: devmem or hpp-batt not found." >&2
        exit 1
    fi

    # Setup ADC: ADCCON=0x4580, ADCMUX=0 (AIN0), ADCDLY=1500, ADCTSC=0xd3
    devmem 0x58000000 32 0x4580 >/dev/null
    devmem 0x58000018 32 0 >/dev/null
    devmem 0x58000008 32 1500 >/dev/null
    devmem 0x58000004 32 0xd3 >/dev/null

    # Sampling 5 times
    SUM=0
    COUNT=5
    for i in $(seq 1 $COUNT); do
        devmem 0x58000000 32 0x4581 >/dev/null
        usleep 5000
        VAL=$(devmem 0x5800000c 32)
        RAW=$(( VAL & 0xfff ))
        SUM=$(( SUM + RAW ))
    done
    AVG_RAW=$(( SUM / COUNT ))

    # GPFDAT Bit 3 check (Charging / External VBUS)
    GPFDAT=$(devmem 0x56000054 32)
    CHG=$(( (GPFDAT >> 3) & 1 ))

    # Calculate Voltage: RAW * 0.004556 V
    # In integer arithmetic (mV): RAW * 4556 / 1000
    MV=$(( AVG_RAW * 4556 / 1000 ))
    V_INT=$(( MV / 1000 ))
    V_FRAC=$(( (MV % 1000) / 10 ))

    # Calculate Percent: (MV - 3400) * 100 / (4150 - 3400)
    if [ "$MV" -le 3400 ]; then
        PCT=0
    elif [ "$MV" -ge 4150 ]; then
        PCT=100
    else
        PCT=$(( (MV - 3400) * 100 / 750 ))
    fi

    # Build gauge bar
    BAR_LEN=20
    FILLED=$(( (PCT * BAR_LEN) / 100 ))
    BAR=""
    for i in $(seq 1 $BAR_LEN); do
        if [ "$i" -le "$FILLED" ]; then
            BAR="${BAR}="
        else
            BAR="${BAR} "
        fi
    done

    STATUS="방전 중 (Discharging / Battery)"
    if [ "$CHG" -eq 1 ]; then
        STATUS="충전 중 (Charging / USB)"
    fi

    echo "===================================================="
    echo "           HP Prime G1 배터리 및 전원 상태          "
    echo "===================================================="
    printf "  배터리 전압 : %d.%02d V  (ADC RAW: %d)\n" "$V_INT" "$V_FRAC" "$AVG_RAW"
    printf "  배터리 잔량 : %3d%%  [%s]\n" "$PCT" "$BAR"
    echo "  전원 상태   : $STATUS"
    if [ "$PCT" -le 15 ] && [ "$CHG" -eq 0 ]; then
        echo "  경고        : [!] 배터리 잔량이 부족합니다. 충전기를 연결하십시오."
    else
        echo "  동작 상태   : 정상 (Good)"
    fi
    echo "===================================================="

else
    # Running on PC: remote query via SSH
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    source "$SCRIPT_DIR/00_env.sh"

    TARGET_IP="${1:-$HPP_LAN_IP}"

    echo "Connecting to HP Prime G1 at $TARGET_IP to read battery status..."
    hpp_ssh_lan "
        if [ -x /usr/bin/hpp-batt ]; then
            /usr/bin/hpp-batt
        elif [ -x /tmp/hpp-batt ]; then
            /tmp/hpp-batt
        else
            # Remote devmem execution
            devmem 0x58000000 32 0x4580 >/dev/null
            devmem 0x58000018 32 0 >/dev/null
            devmem 0x58000008 32 1500 >/dev/null
            devmem 0x58000004 32 0xd3 >/dev/null
            SUM=0
            for i in 1 2 3 4 5; do
                devmem 0x58000000 32 0x4581 >/dev/null
                usleep 5000
                VAL=\$(devmem 0x5800000c 32)
                RAW=\$(( VAL & 0xfff ))
                SUM=\$(( SUM + RAW ))
            done
            AVG_RAW=\$(( SUM / 5 ))
            GPFDAT=\$(devmem 0x56000054 32)
            CHG=\$(( (GPFDAT >> 3) & 1 ))
            MV=\$(( AVG_RAW * 4556 / 1000 ))
            V_INT=\$(( MV / 1000 ))
            V_FRAC=\$(( (MV % 1000) / 10 ))
            if [ \"\$MV\" -le 3400 ]; then PCT=0;
            elif [ \"\$MV\" -ge 4150 ]; then PCT=100;
            else PCT=\$(( (MV - 3400) * 100 / 750 )); fi
            BAR_LEN=20
            FILLED=\$(( (PCT * BAR_LEN) / 100 ))
            BAR=\"\"
            for i in \$(seq 1 \$BAR_LEN); do
                if [ \"\$i\" -le \"\$FILLED\" ]; then BAR=\"\${BAR}=\"; else BAR=\"\${BAR} \"; fi
            done
            STATUS=\"방전 중 (Discharging / Battery)\"
            if [ \"\$CHG\" -eq 1 ]; then STATUS=\"충전 중 (Charging / USB)\"; fi
            echo '===================================================='
            echo '           HP Prime G1 배터리 및 전원 상태          '
            echo '===================================================='
            printf '  배터리 전압 : %d.%02d V  (ADC RAW: %d)\n' \"\$V_INT\" \"\$V_FRAC\" \"\$AVG_RAW\"
            printf '  배터리 잔량 : %3d%%  [%s]\n' \"\$PCT\" \"\$BAR\"
            echo \"  전원 상태   : \$STATUS\"
            if [ \"\$PCT\" -le 15 ] && [ \"\$CHG\" -eq 0 ]; then
                echo '  경고        : [!] 배터리 잔량이 부족합니다. 충전기를 연결하십시오.'
            else
                echo '  동작 상태   : 정상 (Good)'
            fi
            echo '===================================================='
        fi
    "
fi

