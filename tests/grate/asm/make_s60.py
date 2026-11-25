#!/usr/bin/env python3
"""Generate the note 60 manifests for tests/grate/asm/mfu_s59_read.in.

Usage: make_s60.py <out.json> <section>

Sections:
  hit      find a sample point with a large blue-vertex weight by putting a big
            clip w on that one vertex and reading selector 4 (a low D means the
            sample sits near the weighted corner)
  prec     selector 4 against 65536 * D at one fixed sample: w swept on each of
            the three vertices in turn, plus uniform-w controls, so the weights
            are fitted from the data instead of taken from a table
  wstate   selectors 5..7 with ALU-seeded rows held fixed, at uniform clip w and
            at a per-vertex clip w: does the clip-w state move them, and does it
            move the significand, the exponent bit, or both
  pth      the perspective-interpolator discrepancy: direct interpolator row,
            interpolator row plus an ALU identity rewrite, and an ALU-seeded
            equivalent of the measured interpolated value, at w = 1 and w = 4

Geometry: the harness renders a two-triangle quad at clip corners
(-1, 1), (-1, -1), (1, 1), (1, -1) with vertex colours red, green, blue, red,
and reads back the framebuffer's first pixel.  `vs_persp_col.txt` puts w on the
blue vertex (v2) and premultiplies the clip position, so the projected quad does
not move; `vs_persp_wgreen.txt` and `vs_persp_wred.txt` do the same for the green
vertex (v1) and the red ones (v0 and v3).  `vs_scale_w.txt` scales the whole clip
position, which leaves the projection fixed and makes w uniform, i.e. D = 1/w
with no unknown weight.

The sample used throughout is the note 55/59 q1 offset (-1.75, -1.45), whose
weights note 55 fitted as (green 0.125, blue 0.725, red 0.15); the `hit` section
re-derives them from the selector-4 curve so note 60 does not depend on the fit.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_s59 as s59  # noqa: E402

FS = s59.FS
VS_PERSP = s59.VS_PERSP
VS_SCALE = "tests/grate/asm/vs_scale_w.txt"
VS_WGREEN = "tests/grate/asm/vs_persp_wgreen.txt"
VS_WRED = "tests/grate/asm/vs_persp_wred.txt"

ROW_A = s59.ROW_A          # 2^-15, the row readback lane
SFU_A = s59.SFU_A          # 2^12, the sfu capture lane for the low branch
SFU_B = s59.SFU_B          # 2^4, the sfu capture lane for the high branch

Q1 = (0.75, 0.45)          # note 55's first sample point


def tag(v):
    return ("%g" % v).replace(".", "p").replace("-", "n")


def scale_args(w):
    return ["--vs_uniform", '["clip_scale"]=(%s,%s,%s,%s)' % (w, w, w, w)]


def s4_cell(name, vs, w, x=Q1[0], y=Q1[1], sfu="rcp r4"):
    """Selector 4: lane 1 = row 1 * 2^-15 (rows unchanged), lane 2 = the
    2^-16/D capture scaled by 0.5, i.e. 2^-17/D, which is inside the store map
    for every D from 0.15 to 3.3 the sweeps reach."""
    read = ("MAD r0.lh, r1, u9, #0", "MAD r1.lh, r3, u4, #0")
    c = ("1.0", "1.0", "1.0", "1.0", "0.5")
    if vs is VS_SCALE:
        args = scale_args(w)
        vs_path = VS_SCALE
    else:
        args = s59.persp_args(x, y, w)
        vs_path = vs
    return s59.variant(name, vec=("1.0", "1.0", "3.0", "0.75"), sfu=sfu,
                       mul0="mul0: r3, sfu, #1", read=read, c=c, u9=ROW_A,
                       vs=vs_path, args=args)


# --- 1. a sample point near the weighted corner ---------------------------


def section_hit():
    out = []
    for dx, dy in ((-1.75, -1.45), (-2.0, -1.6), (-2.1, -1.75), (-1.9, -1.7),
                   (-2.2, -1.9)):
        out.append(s4_cell("hit-blue-w16-%s_%s" % (tag(dx), tag(dy)),
                           VS_PERSP, 16.0, *(-(1.0 + dx), -(1.0 + dy))))
    return out, 1


# --- 2. selector 4 with the weights fitted from the sweep -----------------


def section_prec():
    """One sample, one word shape, only w moves.  Lane 2 = 0.5/D * 2^-2.

    With w on one vertex and the other two at 1, D = (1 - b) + b/w, so a sweep
    of w determines b and, through b, the weight itself; the three sweeps have
    to give three weights that sum to 1.  The uniform arm has no unknown at all
    (D = 1/w) and is the exact-prediction control.
    """
    out = []
    # uniform w: D = 1/w with no unknown, so these cells are predictions, not
    # fits (w = 0.5 puts the operand at 2^17, the top of the field's range)
    for w in (0.5, 1.0, 2.0, 4.0, 8.0):
        out.append(s4_cell("prec-uni-w%s" % tag(w), VS_SCALE, w))
    # w on the blue vertex at the sample `hit` found near that corner: D falls
    # from 1.0 to about 0.16, three octaves of the reciprocal
    for w in (1.0, 2.0, 4.0, 8.0, 16.0, 32.0):
        out.append(s4_cell("prec-blue-w%s" % tag(w), VS_PERSP, w, 0.9, 0.7))
    # the same blue sweep at the note 55/59 sample point, so the three weights
    # fitted from three sweeps are fitted at one and the same sample
    for w in (2.0, 4.0, 8.0, 16.0):
        out.append(s4_cell("prec-blueq1-w%s" % tag(w), VS_PERSP, w))
    # w on a small-weight vertex has to go below 1 to move D at all: those
    # sweeps run 1.0 .. about 3.3 and fix the small weights
    for label, vs in (("green", VS_WGREEN), ("red", VS_WRED)):
        for w in (1.0, 0.5, 0.25, 0.125, 0.0625):
            out.append(s4_cell("prec-%s-w%s" % (label, tag(w)), vs, w))
    # repeats, and the same program at the other sample point
    out.append(s4_cell("prec-blue-w8-again", VS_PERSP, 8.0, 0.9, 0.7))
    out.append(s4_cell("prec-uni-w4-again", VS_SCALE, 4.0))
    out.append(s4_cell("prec-blue-w8-q2", VS_PERSP, 8.0, 0.35, -0.35))
    return out, 3


# --- 3. clip-w state with the rows held fixed -----------------------------


def wstate_cell(name, sel, rows, vs, w, scale, x=Q1[0], y=Q1[1],
                op="rsq"):
    """Lane 1 reads row 1 back through the ALU (rows unchanged), lane 2 is the
    selector result captured through the explicit `sfu` multiplier token."""
    # lane 1 always reads row 1, which no cell probes, so the control lane is
    # the same everywhere; lane 2 is the capture in r3
    read = ("MAD r0.lh, r1, u9, #0", "MAD r1.lh, r3, u4, #0")
    c = ("1.0", "1.0", "1.0", "1.0", scale)
    if vs is VS_SCALE:
        vs_path, args = VS_SCALE, scale_args(w)
    else:
        vs_path, args = vs, s59.persp_args(x, y, w)
    vec = ("1.0", "%r" % rows[0], "%r" % rows[1], "%r" % rows[2])
    return s59.variant(name, vec=vec, sfu="%s r%d" % (op, sel + 4),
                       mul0="mul0: r3, sfu, #1", read=read, c=c,
                       u9=ROW_A, vs=vs_path, args=args)


def rows_cell(name, vs, w, x=Q1[0], y=Q1[1]):
    """No sfu, no multipliers: rows 2 and 3 read back in the two lanes, so the
    row-integrity claim does not depend on the capture path."""
    read = ("MAD r0.lh, r2, u9, #0", "MAD r1.lh, r3, u9, #0")
    c = ("1.0", "1.0", "1.0", "1.0", "1.0")
    if vs is VS_SCALE:
        vs_path, args = VS_SCALE, scale_args(w)
    else:
        vs_path, args = vs, s59.persp_args(x, y, w)
    return s59.variant(name, vec=("1.0", "1.0", "3.0", "0.75"), sfu="nop r0",
                       read=read, c=c, u9=ROW_A, vs=vs_path, args=args)


ROWS = (1.0, 3.0, 0.75)          # rows 1, 2, 3 -> selectors 5, 6, 7
SEL_SCALE = {1: SFU_A, 2: SFU_B, 3: SFU_A}   # low branch -> 2^12, high -> 2^4


def section_wstate():
    out = []
    # uniform clip w: D = 1/w exactly, no unknown weight in the prediction
    for w in (0.25, 0.5, 1.0, 2.0, 4.0):
        for sel in (1, 2, 3):
            out.append(wstate_cell("wstate-uni-w%s-s%d" % (tag(w), sel + 4),
                                   sel, ROWS, VS_SCALE, w, SEL_SCALE[sel]))
    # one opcode the other way round, where the branch allows it
    out.append(wstate_cell("wstate-uni-w1-s5-rcp", 1, ROWS, VS_SCALE, 1.0,
                           SFU_A, op="rcp"))
    # per-vertex clip w, same sample, fitted weight from `prec`
    for label, vs in (("blue", VS_PERSP), ("green", VS_WGREEN)):
        for w in (2.0, 4.0, 16.0):
            for sel in (1, 2, 3):
                out.append(wstate_cell(
                    "wstate-%s-w%s-s%d" % (label, tag(w), sel + 4), sel, ROWS,
                    vs, w, SEL_SCALE[sel]))
    # does the clip-w state touch the significand or the exponent bit?  same
    # significand, one octave apart, at the same w
    for rows, name in (((1.0, 3.0, 0.75), "samed"),
                       ((1.0, 6.0, 1.5), "double")):
        for sel in (2, 3):
            out.append(wstate_cell("wstate-sig-%s-w4-s%d" % (name, sel + 4),
                                   sel, rows, VS_PERSP, 4.0, SEL_SCALE[sel]))
    out.append(wstate_cell("wstate-uni-w4-s6-again", 2, ROWS, VS_SCALE, 4.0,
                           SFU_B))
    # row integrity in the same geometries, read without any sfu or multiplier
    for w in (0.25, 1.0, 4.0):
        out.append(rows_cell("wstate-rows-uni-w%s" % tag(w), VS_SCALE, w))
    for w in (2.0, 16.0):
        out.append(rows_cell("wstate-rows-blue-w%s" % tag(w), VS_PERSP, w))
    out.append(rows_cell("wstate-rows-green-w4", VS_WGREEN, 4.0))
    return out, 2


# --- 4. the perspective-interpolator discrepancy, path by path ------------


def ipl_word(t0="t0.fp20"):
    return s59.s57.mfu_word("rcp r4", "mul0: bar, sfu, bar0",
                            "mul1: bar, sfu, bar1",
                            "%s, %s, %s, %s" % (t0, t0, t0, t0))


def rewrite_block(row):
    return ("EXEC\n\tALU:\n\t\tALU0:\tMAD r%d.lh, r%d, u0, #0\n"
            "\t\tALU1:\tNOP\n\t\tALU2:\tNOP\n\t\tALU3:\tNOP\n;\n"
            % (row, row))


def pth_cell(name, sel, pre, seed, vec, sfu_scale, vs, x, y, w, rows=None,
             ar=1.0, ag=0.5, ab=0.25):
    read = ("MAD r0.lh, r1, u9, #0", "MAD r1.lh, r3, u4, #0")
    c = ("1.0", "1.0", "1.0", "1.0", sfu_scale)
    return s59.variant(name, vec=vec, sfu="rsq r%d" % sel, pre=pre,
                       mul0="mul0: r3, sfu, #1", read=read, c=c, u9=ROW_A,
                       seed=seed, vs=vs,
                       args=s59.persp_args(x, y, w, ar, ag, ab))


SETS = {"a": (1.0, 0.5, 0.25), "b": (4.0, 2.0, 1.0)}
# ALU-representable fp20 stand-ins for the interpolated values measured in note
# 59 lane 1 (v = 4 x the stored 0.25-scaled row read), rounded to 2^-10
ALUSEED = {"q1-a-w4": 0.5458984375, "q1-a-w1": 0.3544921875,
           "q2-a-w4": 0.6484375, "q2-a-w1": 0.5458984375}


def section_pth():
    out = []
    cells = (("q1", 0.75, 0.45, "a", 4.0), ("q1", 0.75, 0.45, "a", 1.0),
             ("q2", 0.35, -0.35, "a", 4.0), ("q2", 0.35, -0.35, "a", 1.0))
    for label, x, y, setname, w in cells:
        ar, ag, ab = SETS[setname]
        key = "%s-%s-w%d" % (label, setname, w)
        # path 1: the interpolator writes the row, the sfu runs in a later word
        out.append(pth_cell("pth-%s-ipl" % key, 5, ipl_word(), False,
                            ("1.0", "1.0", "1.0", "1.0"), SFU_A, VS_PERSP, x,
                            y, w, ar=ar, ag=ag, ab=ab))
        # path 2: same, with an ALU identity rewrite of row 1 in between
        out.append(pth_cell("pth-%s-rewrite" % key, 5,
                            ipl_word() + rewrite_block(1), False,
                            ("1.0", "1.0", "1.0", "1.0"), SFU_A, VS_PERSP, x,
                            y, w, ar=ar, ag=ag, ab=ab))
        # path 3: the ALU seeds the row with the equivalent constant
        out.append(pth_cell("pth-%s-aluseed" % key, 5, "", True,
                            ("1.0", "%.10g" % ALUSEED[key], "1.0", "1.0"),
                            SFU_A, VS_PERSP, x, y, w, ar=ar, ag=ag, ab=ab))
    # a second value for the affected selector, and the affine control
    for label, x, y, setname, w in (("q1", 0.75, 0.45, "b", 4.0),
                                    ("q1", 0.75, 0.45, "b", 1.0)):
        ar, ag, ab = SETS[setname]
        key = "%s-%s-w%d" % (label, setname, w)
        for sel, pre_name, pre in ((5, "ipl", ipl_word()),
                                   (5, "rewrite", ipl_word() + rewrite_block(1)),
                                   (6, "ipl", ipl_word()),
                                   (6, "rewrite", ipl_word() + rewrite_block(1))):
            out.append(pth_cell("pth-%s-%s-s%d" % (key, pre_name, sel + 4), sel,
                                pre, False, ("1.0", "1.0", "1.0", "1.0"),
                                SFU_A, VS_PERSP, x, y, w, ar=ar, ag=ag, ab=ab))
    # affine control: same value as the w = 4 cell, but interpolated with every
    # vertex w = 1, so the row is not perspective corrected.  The vertex values
    # are scaled by measured w=4 / w=1 so the two rows hold the same number.
    for label, x, y, setname, gain in (("q1", 0.75, 0.45, "a", 1.54),
                                       ("q2", 0.35, -0.35, "a", 1.19)):
        ar, ag, ab = SETS[setname]
        out.append(pth_cell("pth-affine-%s-a-s5" % label, 5, ipl_word(),
                            False, ("1.0", "1.0", "1.0", "1.0"), SFU_A,
                            VS_PERSP, x, y, 1.0, ar=ar * gain,
                            ag=ag * gain, ab=ab * gain))
    return out, 2


def section_pth2():
    """What value does the sfu see when the row comes from the interpolator?

    The row readback gives the ALU's view; the selector code gives the sfu's,
    through a monotone transform.  Sweeping w over the same sample moves the
    perspective-corrected value while the affine one stays put, so the two
    candidates predict different codes.
    """
    out = []
    for label, x, y in (("q1", 0.75, 0.45), ("q2", 0.35, -0.35)):
        ar, ag, ab = SETS["a"]
        for w in (2.0, 4.0, 8.0):
            key = "%s-a-w%d" % (label, w)
            out.append(pth_cell("pth2-%s-ipl" % key, 5, ipl_word(), False,
                                ("1.0", "1.0", "1.0", "1.0"), SFU_A,
                                VS_PERSP, x, y, w, ar=ar, ag=ag, ab=ab))
            out.append(pth_cell("pth2-%s-rewrite" % key, 5,
                                ipl_word() + rewrite_block(1), False,
                                ("1.0", "1.0", "1.0", "1.0"), SFU_A,
                                VS_PERSP, x, y, w, ar=ar, ag=ag, ab=ab))
    # the affine reference at the same rows (w = 1): value and selector together
    for label, x, y in (("q1", 0.75, 0.45), ("q2", 0.35, -0.35)):
        ar, ag, ab = SETS["a"]
        out.append(pth_cell("pth2-%s-a-w1-ipl" % label, 5, ipl_word(), False,
                            ("1.0", "1.0", "1.0", "1.0"), SFU_A, VS_PERSP, x,
                            y, 1.0, ar=ar, ag=ag, ab=ab))
    out.append(pth_cell("pth2-q1-a-w4-again", 5, ipl_word(), False,
                        ("1.0", "1.0", "1.0", "1.0"), SFU_A, VS_PERSP, 0.75,
                        0.45, 4.0, ar=1.0, ag=0.5, ab=0.25))
    return out, 3


def section_pth3():
    """The constant-varying control: every corner of the varying holds the same
    number, so the perspective-corrected row is that number for every clip w
    (A * sum lambda/W / D = A).  If the selector code still moves with w, the
    sfu is not reading the corrected row; if it does not, the w dependence seen
    with a varying-varying varying comes through the value, not the sfu path."""
    out = []
    for label, x, y in (("q1", 0.75, 0.45), ("q2", 0.35, -0.35),
                        ("q3", -0.6, -0.8)):
        for w in (1.0, 2.0, 4.0, 8.0):
            out.append(pth_cell("pth3-%s-c-w%s-ipl" % (label, tag(w)), 5,
                                ipl_word(), False,
                                ("1.0", "1.0", "1.0", "1.0"), SFU_A,
                                VS_PERSP, x, y, w, ar=0.5, ag=0.5, ab=0.5))
    out.append(pth_cell("pth3-q1-c-w4-rewrite", 5,
                        ipl_word() + rewrite_block(1), False,
                        ("1.0", "1.0", "1.0", "1.0"), SFU_A, VS_PERSP, 0.75,
                        0.45, 4.0, ar=0.5, ag=0.5, ab=0.5))
    out.append(pth_cell("pth3-q1-c-w4-again", 5, ipl_word(), False,
                        ("1.0", "1.0", "1.0", "1.0"), SFU_A, VS_PERSP, 0.75,
                        0.45, 4.0, ar=0.5, ag=0.5, ab=0.5))
    return out, 3


SECTIONS = {
    "hit": (section_hit, "s60-hit"),
    "pth2": (section_pth2, "s60-pth2"),
    "pth3": (section_pth3, "s60-pth3"),
    "prec": (section_prec, "s60-prec"),
    "wstate": (section_wstate, "s60-wstate"),
    "pth": (section_pth, "s60-pth"),
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
        "program": {"vs": s59.s57.VS, "fs": FS, "lnk": s59.s57.LNK},
        "controls": s59.s57.CONTROLS,
        "variants": variants,
    }
    with open(out_path, "w") as fp:
        json.dump(manifest, fp, indent=2)
        fp.write("\n")
    print("%s: %d variants, %d runs" % (name, len(variants),
                                        len(variants) * repeat))


if __name__ == "__main__":
    main()
