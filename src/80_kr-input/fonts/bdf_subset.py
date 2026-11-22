#!/usr/bin/env python3
"""
bdf_subset.py — HP Prime G1 X11 Core Bitmap Font (BDF -> PCF) Subsetter
Filters BDF by Unicode range + KS X 1001 + exact DWIDTH (half vs full width),
normalizes XLFD properties (SPACING="C", PIXEL_SIZE, AVERAGE_WIDTH, FONTBOUNDINGBOX),
and outputs clean charcell BDFs for xterm (-fn / -fw) and hpp-vkbd.
"""
import argparse


def build_ksx1001_hangul_set():
    s = set()
    for hi in range(0xB0, 0xC9):
        for lo in range(0xA1, 0xFF):
            try:
                s.add(ord(bytes([hi, lo]).decode("euc_kr")))
            except UnicodeDecodeError:
                pass
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="Input BDF file")
    ap.add_argument("dst", help="Output BDF file")
    ap.add_argument("--ranges", default="", help="Comma-separated hex ranges, e.g. 0020-007E,00A0-00FF")
    ap.add_argument("--ksx1001", action="store_true", help="Include KS X 1001 2,350 Hangul syllables")
    ap.add_argument("--dwidth", type=int, required=True, help="Required glyph DWIDTH_x (e.g. 6 for half, 12 for full)")
    ap.add_argument("--pixel-size", type=int, required=True, help="Normalized PIXEL_SIZE (e.g. 12 or 16)")
    ap.add_argument("--ascent", type=int, required=True, help="FONT_ASCENT")
    ap.add_argument("--descent", type=int, required=True, help="FONT_DESCENT")
    ap.add_argument("--family", required=True, help="XLFD Family name (e.g. GalmuriMono11)")
    args = ap.parse_args()

    keep = set()
    for r in filter(None, args.ranges.split(",")):
        lo, _, hi = r.partition("-")
        keep.update(range(int(lo, 16), int(hi or lo, 16) + 1))
    if args.ksx1001:
        keep.update(build_ksx1001_hangul_set())

    lines = open(args.src, encoding="latin-1").read().splitlines()
    i0 = next(i for i, l in enumerate(lines) if l.startswith("STARTCHAR"))
    i1 = max(i for i, l in enumerate(lines) if l.startswith("ENDFONT"))
    body = lines[i0:i1]

    blocks, cur = [], []
    for l in body:
        cur.append(l)
        if l.startswith("ENDCHAR"):
            blocks.append(cur)
            cur = []

    out = []
    for b in blocks:
        enc = None
        dw = None
        for l in b:
            if l.startswith("ENCODING "):
                enc = int(l.split()[1])
            elif l.startswith("DWIDTH "):
                dw = int(l.split()[1])
        if enc in keep and dw == args.dwidth:
            out.append(b)

    avg_width = args.dwidth * 10
    pt_size = args.pixel_size * 10
    xlfd = f"-galmuri-{args.family.lower()}-medium-r-normal--{args.pixel_size}-{pt_size}-75-75-c-{avg_width}-iso10646-1"

    header = [
        "STARTFONT 2.1",
        f"FONT {xlfd}",
        f"SIZE {args.pixel_size} 75 75",
        f"FONTBOUNDINGBOX {args.dwidth} {args.pixel_size} 0 -{args.descent}",
        "STARTPROPERTIES 14",
        'FOUNDRY "galmuri"',
        f'FAMILY_NAME "{args.family}"',
        'WEIGHT_NAME "medium"',
        'SLANT "r"',
        'SETWIDTH_NAME "normal"',
        'ADD_STYLE_NAME ""',
        f"PIXEL_SIZE {args.pixel_size}",
        f"POINT_SIZE {pt_size}",
        "RESOLUTION_X 75",
        "RESOLUTION_Y 75",
        'SPACING "C"',
        f"AVERAGE_WIDTH {avg_width}",
        'CHARSET_REGISTRY "iso10646"',
        'CHARSET_ENCODING "1"',
        f"FONT_ASCENT {args.ascent}",
        f"FONT_DESCENT {args.descent}",
        "ENDPROPERTIES",
        f"CHARS {len(out)}",
    ]
    # Fix STARTPROPERTIES count (16 properties)
    header[4] = "STARTPROPERTIES 16"

    with open(args.dst, "w", encoding="latin-1") as f:
        for l in header:
            f.write(l + "\n")
        for b in out:
            for l in b:
                f.write(l + "\n")
        f.write("ENDFONT\n")

    print(f"[{args.dst}] kept {len(out)} glyphs (DWIDTH={args.dwidth}, XLFD={xlfd})")


if __name__ == "__main__":
    main()

