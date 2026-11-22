#!/usr/bin/env bash
set -euo pipefail

HOST="prime"
SOURCE="auto"
FORMAT="auto"
DELAY=0
OUT_FILE=""

usage() {
  cat <<'EOF'
Usage: capture_from_pc.sh [OPTIONS] [OUTPUT_FILE]
Stream live HP Prime G1 LCD screen via SSH without writing to calculator JFFS2 flash.

Options:
  --host <ssh_host>         SSH host alias or IP (default: prime)
  -s, --source <src>        auto | hw | x11 | fb (default: auto)
  -f, --format <fmt>        auto | png | bmp | ppm (default: auto)
  -d, --delay <sec>         Delay seconds on calculator before capture (default: 0)
  -o, --output <file>       Output file on host PC (default: ./prime_lcd_YYYYMMDD_HHMMSS.png)
  -h, --help                Show this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host)    HOST="$2"; shift 2 ;;
    -s|--source) SOURCE="$2"; shift 2 ;;
    -f|--format) FORMAT="$2"; shift 2 ;;
    -d|--delay)  DELAY="$2"; shift 2 ;;
    -o|--output) OUT_FILE="$2"; shift 2 ;;
    -h|--help)   usage; exit 0 ;;
    -*)          echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    *)
      if [ -z "$OUT_FILE" ]; then
        OUT_FILE="$1"
        shift
      else
        echo "Unexpected argument: $1" >&2; exit 2
      fi
      ;;
  esac
done

if [ -z "$OUT_FILE" ]; then
  EXT="png"
  [ "$FORMAT" = "bmp" ] && EXT="bmp"
  [ "$FORMAT" = "ppm" ] && EXT="ppm"
  OUT_FILE="prime_lcd_$(date +%Y%m%d_%H%M%S).${EXT}"
elif [ "$FORMAT" = "auto" ]; then
  case "${OUT_FILE,,}" in
    *.bmp) FORMAT="bmp" ;;
    *.ppm) FORMAT="ppm" ;;
    *.png) FORMAT="png" ;;
  esac
fi

TMP_FILE="${OUT_FILE}.tmp.$$"
trap 'rm -f "$TMP_FILE"' EXIT

ssh "$HOST" "/usr/bin/lcd-capture --source '$SOURCE' --format '$FORMAT' -d '$DELAY' -o -" > "$TMP_FILE"
mv "$TMP_FILE" "$OUT_FILE"
trap - EXIT
ls -lh "$OUT_FILE"

