#!/usr/bin/env bash
# ssh_lan.sh — SSH into HP Prime G1 via USB Ethernet adapter on local LAN.
# Usage: ./ssh_lan.sh [IP_ADDRESS]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/00_env.sh"

TARGET_IP="${1:-$HPP_LAN_IP}"

echo "========================================================"
echo " Connecting to HP Prime G1 via LAN: $TARGET_IP"
echo " Default root password is: root"
echo "========================================================"

exec ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$HPP_USER@$TARGET_IP"

