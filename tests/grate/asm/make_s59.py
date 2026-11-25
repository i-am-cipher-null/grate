#!/usr/bin/env python3
"""Generate the note 59 manifests for tests/grate/asm/mfu_s59_read.in.

Usage: make_s59.py <out.json> <section>

Sections:
  wsel      selector 4 against the interpolated perspective denominator: the
            ALU-visible r4 and the sfu result of `rcp r4` in one word, at three
            sample points and four clip-w values, plus a repeated cell
  fams      the significand transform of selector 5: matched-significand
            families across exponents, powers of two, the last value below 2.0
            and the first above it, and the negative rows
  posdep    the same rows, four sample positions, selectors 5, 6 and 7: does
            the projection position alone move them
  ipl       rows filled by the interpolator from a scalar varying, read back
            through the ALU and through selectors 5..7 at several positions and
            two value sets, so the transform can be checked on a second input
            path

The perspective arms use `vs_persp_col.txt`, which premultiplies the clip
position by a per-vertex w, so the projected triangle and the sample points do
not move when w changes and only the interpolation weights do. With w = 1 at
every vertex except the blue one (w = 1 + wdelta), and the screen-space weights

    q1 (0.75, 0.45)    lambda = 0.125, 0.725, 0.15
    q2 (0.35, -0.35)   lambda = 0.325, 0.325, 0.35
    q3 (-0.6, -0.8)    lambda = 0.8,   0.1,   0.1

the perspective denominator D = sum lambda_i / W_i takes the values

    w = 1   D = 1.0 everywhere
    w = 2   0.6375, 0.8375, 0.95
    w = 4   0.456,  0.756,  0.925
    w = 8   0.365625, 0.715625, 0.9125

so 65536 D spans 0.366 to 1.0 and the two selector-4 readings can be compared
against it at a resolution of one code step (0.125 octave).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_s57 as s57  # noqa: E402

FS = "tests/grate/asm/mfu_s59_read.in"
VS_PERSP = "tests/grate/asm/vs_persp_col.txt"
VS_POS = "tests/grate/asm/vs_persp_pos.txt"
VS_OFFSET = "tests/grate/asm/vs_offset.txt"

NOP = "NOP"

# read scales: rows are large, sfu captures are tiny
ROW_A = "0.000030517578125"   # 2^-15
ROW_B = "0.001953125"        # 2^-9
SFU_A = "4096.0"             # 2^12
SFU_B = "16.0"               # 2^4

C_ROWS = ("1.0", "1.0", "1.0", "1.0", "1.0")


def variant(name, vec=("1.0", "1.0", "1.0", "1.0"), sfu="nop r0", mul0="",
            mul1="", read=None, pre="", ipl="NOP, NOP, NOP, NOP",
            c=C_ROWS, u9=ROW_A, seed=True, vs=None, args=None, delay=0,
            delay_word=None, mid="", late=0):
    v0, v1, v2, v3 = vec
    pr0, pr1 = (read if read else ("NOP", "NOP"))
    if seed:
        slots = ["MAD  r%d.lh,  u%d,  #1,  #0" % (n, 5 + n) for n in range(4)]
    else:
        slots = [NOP] * 4
    entry = {
        "name": name,
        "fs": FS,
        "replace": {
            "fs": {
                "@C0@": c[0],
                "@C1@": c[1],
                "@C2@": c[2],
                "@C3@": c[3],
                "@C4@": c[4],
                "@V0@": v0,
                "@V1@": v1,
                "@V2@": v2,
                "@V3@": v3,
                "@U9@": u9,
                "@S0@": slots[0],
                "@S1@": slots[1],
                "@S2@": slots[2],
                "@S3@": slots[3],
                "@PRE@": pre,
                "@SFU@": sfu,
                "@MUL0@": mul0,
                "@MUL1@": mul1,
                "@IPL@": ipl,
                "@EXTRA@": "",
                "@DELAY@": (delay_word or s57.NOP_WORD) * delay,
                "@PR0@": pr0,
                "@PR1@": pr1,
                "@MID@": mid,
                "@LATE@": s57.NOP_BATCH * late,
            }
        },
    }
    if vs:
        entry["vs"] = vs
    if args:
        entry["args"] = args
    return entry


def read_scaled(n):
    return ("MAD r0.lh, r%d, u9, #0" % n, "MAD r1.lh, r%d, u4, #0" % n)


def persp_args(x, y, w, ar=1.0, ag=0.5, ab=0.25):
    return [
        "--vs_uniform", '["offset"]=(%s,%s,0.0,0.0)' % (round(-(1.0 + x), 6),
                                                       round(-(1.0 + y), 6)),
        "--vs_uniform", '["wdelta"]=(%s,0.0,0.0,0.0)' % (w - 1),
        "--vs_uniform", '["ar"]=(%s,0.0,0.0,0.0)' % ar,
        "--vs_uniform", '["ag"]=(%s,0.0,0.0,0.0)' % ag,
        "--vs_uniform", '["ab"]=(%s,0.0,0.0,0.0)' % ab,
    ]


def offset_args(dx, dy):
    return ["--vs_uniform", '["offset"]=(%s,%s,0.0,0.0)' % (dx, dy)]


POINTS = (("q1", 0.75, 0.45), ("q2", 0.35, -0.35), ("q3", -0.6, -0.8))

IPL_WORD = s57.mfu_word("rcp r4", "mul0: bar, sfu, bar0",
                        "mul1: bar, sfu, bar1",
                        "t0.fp20, t0.fp20, t0.fp20, t0.fp20")


# --- 1. selector 4 against the interpolated reciprocal w ------------------


def section_wsel():
    out = []
    # lane 1: 2 D (r4 times 2^-15); lane 2: 0.5 / D (rcp(r4) times 2^15), so
    # the product of the two lanes is 1.0 for every cell
    read = ("MAD r0.lh, r4, u9, #0", "MAD r1.lh, r3, u4, #0")
    c = ("1.0", "1.0", "1.0", "1.0", "32768.0")
    for label, x, y in POINTS:
        for w in (1, 2, 4, 8):
            out.append(variant("wsel-%s-w%d" % (label, w), sfu="rcp r4",
                               mul0="mul0: r3, sfu, #1", read=read, c=c,
                               u9=ROW_A, vs=VS_PERSP,
                               args=persp_args(x, y, w)))
    # repeated cells: the same program twice more, to see the repeat spread
    out.append(variant("wsel-q1-w4-again", sfu="rcp r4",
                       mul0="mul0: r3, sfu, #1", read=read, c=c, u9=ROW_A,
                       vs=VS_PERSP, args=persp_args(0.75, 0.45, 4)))
    out.append(variant("wsel-q3-w4-again", sfu="rcp r4",
                       mul0="mul0: r3, sfu, #1", read=read, c=c, u9=ROW_A,
                       vs=VS_PERSP, args=persp_args(-0.6, -0.8, 4)))
    # the sample points themselves, read back through the interpolator
    for label, x, y in POINTS:
        out.append(variant("wsel-%s-pos" % label, sfu="nop r0", pre=IPL_WORD,
                           read=read_scaled(0), u9=ROW_A, seed=False,
                           vs=VS_POS, args=offset_args(
                               round(-(1.0 + x), 6), round(-(1.0 + y), 6))))
    return out, 3


# --- 2. the significand transform ----------------------------------------

# Each family shares one significand across exponents far apart, so a transform
# that keeps the significand predicts one code for the whole family, while one
# that keeps the value predicts a different code for each member.
FAMILIES = [
    ("m1p5-low", [0.09375, 0.1875, 0.375, 0.75, 1.5]),
    ("m1p5-high", [3.0, 6.0, 12.0, 24.0, 48.0]),
    ("m1p25-low", [0.15625, 0.3125, 0.625, 1.25]),
    ("m1p25-high", [2.5, 5.0, 10.0, 20.0, 40.0]),
    ("m1p75-low", [1.75]),
    ("m1p75-high", [3.5, 7.0, 14.0, 28.0]),
    ("m1-low", [0.0625, 0.125, 0.25, 0.5, 1.0]),
    ("m1-high", [2.0, 4.0, 8.0, 16.0, 32.0]),
    ("edge", [1.9990234375, 2.0, 2.0009765625]),
]

NEG_GAIN = [("-2.0", -2.0), ("-3.0", -3.0), ("-4.0", -4.0), ("-6.0", -6.0),
            ("-8.0", -8.0), ("-0.5", -0.5), ("-1.0", -1.0), ("-1.5", -1.5)]


def tag(v):
    return ("%g" % v).replace(".", "p").replace("-", "n")


def section_fams():
    out = []
    for fam, values in FAMILIES:
        for v in values:
            out.append(variant("fams-%s-%s-rsq" % (fam, tag(v)),
                               vec=("0.0625", "%r" % v, "0.0625", "0.0625"),
                               sfu="rsq r5", mul0="mul0: r3, sfu, #1",
                               read=read_scaled(3), u9=SFU_A,
                               c=("1.0", "1.0", "1.0", "1.0", SFU_B)))
    # rcp on the same families: the upper branch is readable, the lower one is
    # the underflow the note 58 calibration pinned
    for fam in ("m1p5-low", "m1p5-high", "m1-high", "edge"):
        for v in dict(FAMILIES)[fam]:
            out.append(variant("fams-%s-%s-rcp" % (fam, tag(v)),
                               vec=("0.0625", "%r" % v, "0.0625", "0.0625"),
                               sfu="rcp r5", mul0="mul0: r3, sfu, #1",
                               read=read_scaled(3), u9=SFU_A,
                               c=("1.0", "1.0", "1.0", "1.0", SFU_B)))
    # negatives: row 3 carries a 2^12 gain so the upper branch's 2^-16 lands in
    # the code map; the negating lane turns the negative product positive
    read_neg = ("MAD r0.lh, -r2, #1, #0", "MAD r1.lh, r2, #1, #0")
    for label, v in NEG_GAIN:
        out.append(variant("fams-neg-%s-rcp" % tag(v),
                           vec=("0.0625", "%r" % v, "0.0625", "4096.0"),
                           sfu="rcp r5", mul0="mul0: r2, sfu, r3",
                           read=read_neg))
    return out, 3


# --- 3. does the sample position move selectors 5..7? --------------------

POS_VEC = ("0.0625", "1.0", "3.0", "0.75")
POS_OFFSETS = (("p0", 0.0, 0.0), ("p1", -0.5, -0.4), ("p2", -0.25, -0.2),
               ("p3", -0.75, -0.6))


def section_posdep():
    out = []
    for label, dx, dy in POS_OFFSETS:
        for n in (5, 6, 7):
            row = n - 4
            for op in ("rcp", "rsq"):
                out.append(variant("posdep-%s-%s%d" % (label, op, n),
                                   vec=POS_VEC, sfu="%s r%d" % (op, n),
                                   mul0="mul0: r3, sfu, #1",
                                   read=read_scaled(3), u9=SFU_A,
                                   c=("1.0", "1.0", "1.0", "1.0", SFU_B),
                                   vs=VS_OFFSET, args=offset_args(dx, dy)))
    return out, 3


# --- 4. interpolator-filled rows -----------------------------------------

SETS = (("a", 1.0, 0.5, 0.25), ("b", 4.0, 2.0, 1.0))


def section_ipl():
    out = []
    for label, x, y in POINTS:
        for setname, ar, ag, ab in SETS:
            for w in (1, 4):
                base = persp_args(x, y, w, ar, ag, ab)
                lname = "ipl-%s-%s-w%d" % (label, setname, w)
                # the rows the interpolator wrote, read through the ALU
                out.append(variant(lname + "-row1", sfu="nop r0", pre=IPL_WORD,
                                   read=read_scaled(1), u9=ROW_A, seed=False,
                                   vs=VS_PERSP, args=base))
                # and the same rows seen by selector 5
                out.append(variant(lname + "-sel5", sfu="rsq r5",
                                   mul0="mul0: r3, sfu, #1", pre=IPL_WORD,
                                   read=read_scaled(3), u9=SFU_A,
                                   c=("1.0", "1.0", "1.0", "1.0", SFU_B),
                                   seed=False, vs=VS_PERSP, args=base))
    return out, 3


# --- 5. selector 4 against the multiplier's own view of the pipeline value --


def section_wsel2():
    """Row 4 as the ALU sees it is not the pipeline reciprocal; the multiplier
    source slot 4 is. One word carries both views: lane 1 is src4 scaled by the
    row-0 constant 2^-15 (so it reads 2 D), lane 2 is rcp of selector 4 scaled by
    2^15 (so it reads 0.5/D). Their product is 1.0 exactly if the two paths carry
    one value, and D comes from the geometry alone."""
    out = []
    vec = ("0.000030517578125", "1.0", "1.0", "1.0")
    c = ("1.0", "1.0", "1.0", "1.0", "32768.0")
    read = ("MAD r0.lh, r2, #1, #0", "MAD r1.lh, r3, u4, #0")
    for label, x, y in POINTS:
        for w in (1, 2, 4, 8):
            out.append(variant("wsel2-%s-w%d" % (label, w), vec=vec,
                               sfu="rcp r4", mul0="mul0: r2, src4, r0",
                               mul1="mul1: r3, sfu, #1", read=read, c=c,
                               u9=ROW_A, vs=VS_PERSP,
                               args=persp_args(x, y, w)))
    # There is no row 4 to control against: the assembler rejects r4 both as an
    # ALU write target and as a multiplier source ("Invalid row register number,
    # r3 max"), so the multiplier's src4 slot and the ALU's r4 read are the only
    # two independent handles on the pipeline value.
    # A known-value control lane: row 1 is the seed 1.0 and row 0 is 2^-15, so
    # lane 2 must read 2^-15 in every cell while lane 1 moves with D.
    c4 = ("1.0", "1.0", "1.0", "1.0", "32768.0")
    read4 = ("MAD r0.lh, r2, #1, #0", "MAD r1.lh, r3, #1, #0")
    for label, x, y, w in (("q1", 0.75, 0.45, 1), ("q1", 0.75, 0.45, 4),
                           ("q2", 0.35, -0.35, 4), ("q3", -0.6, -0.8, 4)):
        out.append(variant("wsel2-%s-w%d-ctl" % (label, w), vec=vec,
                           sfu="rcp r4", mul0="mul0: r2, src4, r0",
                           mul1="mul1: r3, r1, r0", read=read4, c=c4,
                           u9=ROW_A, vs=VS_PERSP,
                           args=persp_args(x, y, w)))
    return out, 3


# --- 6. interpolator rows: the row and the selector result in one word ----


def section_ipl2():
    out = []
    # lane 1 reads row 1 scaled by u4 = 0.25 (so multiply by 4), lane 2 reads the
    # rsq of selector 5 scaled by 2^12; the same fragment supplies both, so the
    # transfer function can be checked without comparing two runs.
    read = ("MAD r0.lh, r1, u4, #0", "MAD r1.lh, r3, u9, #0")
    c = ("1.0", "1.0", "1.0", "1.0", "0.25")
    for label, x, y in POINTS:
        for setname, ar, ag, ab in SETS:
            for w in (1, 4):
                out.append(variant(
                    "ipl2-%s-%s-w%d" % (label, setname, w),
                    sfu="rsq r5", mul0="mul0: r3, sfu, #1",
                    pre=IPL_WORD, read=read, c=c, u9=SFU_A, seed=False,
                    vs=VS_PERSP, args=persp_args(x, y, w, ar, ag, ab)))
    return out, 3


# --- 7. is the interpolator-path mismatch a timing effect? ---------------


def section_ipl3():
    """Same fragment, same row, one and two intervening MFU words: a value that
    drifts with the delay is a pipeline-latch effect, a value that repeats is a
    stable but different operand."""
    out = []
    read = ("MAD r0.lh, r1, u4, #0", "MAD r1.lh, r3, u9, #0")
    c = ("1.0", "1.0", "1.0", "1.0", "0.25")
    cells = (("q1", 0.75, 0.45, 1, "a", 1.0, 0.5, 0.25),
             ("q1", 0.75, 0.45, 1, "b", 4.0, 2.0, 1.0),
             ("q2", 0.35, -0.35, 4, "a", 1.0, 0.5, 0.25))
    for label, x, y, w, setname, ar, ag, ab in cells:
        for delay in (1, 2):
            out.append(variant(
                "ipl3-%s-%s-w%d-d%d" % (label, setname, w, delay),
                sfu="rsq r5", mul0="mul0: r3, sfu, #1",
                pre=IPL_WORD, read=read, c=c, u9=SFU_A, seed=False, delay=delay,
                vs=VS_PERSP, args=persp_args(x, y, w, ar, ag, ab)))
    return out, 3


# --- 8. are the four interpolated components equal? ----------------------


def section_ipl4():
    """Two lanes on rows 0 and 1 of the same interpolated fragment: if the
    interpolator replicated the scalar into all four rows, the lanes agree."""
    out = []
    read = ("MAD r0.lh, r0, u4, #0", "MAD r1.lh, r1, u4, #0")
    c = ("1.0", "1.0", "1.0", "1.0", "0.25")
    for label, x, y, w, setname, ar, ag, ab in (
            ("q1", 0.75, 0.45, 1, "a", 1.0, 0.5, 0.25),
            ("q1", 0.75, 0.45, 1, "b", 4.0, 2.0, 1.0),
            ("q2", 0.35, -0.35, 4, "a", 1.0, 0.5, 0.25)):
        out.append(variant("ipl4-%s-%s-w%d-rows01" % (label, setname, w),
                           sfu="nop r0", pre=IPL_WORD, read=read, c=c,
                           u9=SFU_A, seed=False, vs=VS_PERSP,
                           args=persp_args(x, y, w, ar, ag, ab)))
    return out, 3


SECTIONS = {
    "wsel": (section_wsel, "s59-wsel"),
    "fams": (section_fams, "s59-fams"),
    "posdep": (section_posdep, "s59-posdep"),
    "ipl": (section_ipl, "s59-ipl"),
    "wsel2": (section_wsel2, "s59-wsel2"),
    "ipl2": (section_ipl2, "s59-ipl2"),
    "ipl3": (section_ipl3, "s59-ipl3"),
    "ipl4": (section_ipl4, "s59-ipl4"),
}


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    out_path, section = sys.argv[1], sys.argv[2]
    if section not in SECTIONS:
        sys.exit("unknown section %r" % section)
    make, name = SECTIONS[section]
    variants, repeat = make()
    manifest = {
        "name": name,
        "repeat": repeat,
        "timeout": 20,
        "program": {"vs": s57.VS, "fs": FS, "lnk": s57.LNK},
        "controls": s57.CONTROLS,
        "variants": variants,
    }
    with open(out_path, "w") as fp:
        json.dump(manifest, fp, indent=2)
        fp.write("\n")
    print("%s: %d variants, %d runs" % (name, len(variants),
                                        len(variants) * repeat))


if __name__ == "__main__":
    main()
