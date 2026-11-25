#!/usr/bin/env python3
"""Generate the note 58 manifests for tests/grate/asm/mfu_s58_read.in.

Usage: make_s58.py <out.json> <section>

The manifests are written to whatever path is given (the note 57 convention:
pass tests/grate/asm_characterize.s58_<section>.json).

Sections:
  cal       the store-code map below 2^-14, where note 57's readings live: the
            unit has to make the value, but the code path is the same one every
            read in these notes goes through
  reg       where selectors 4..7 read from: the template's own row 4..7 copy,
            rows 4..7 overwritten from the low uniforms, the four packet rows,
            and the clip w through the scaling vertex shader
  wsrc      the row-write source rows: row 2 varied for a dominant X and a
            dominant Y, row 0 varied for a dominant Z, plus the candidate rows
            that are not the source, two-word sequences that tell a live row 2
            from a latched one, and a non-rcp opcode control

Helpers, controls and defaults are imported from make_s57 so the two notes share
one set of read paths. Seed rows come from uniforms 5..8; uniforms 0..4 default
to 8.0, 0.125, 3.0, 0.75 and 1.0 and uniform 9 holds 0.25 for the scaled read
paths. Readback is `store rt1, r0, r1`: code(value) in bytes 1 and 3,
code = 0xF7 + 8*log2(value) for positive values, 00 for zero and negatives, FF
clips at 2.0.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_s57 as s57  # noqa: E402

FS = "tests/grate/asm/mfu_s58_read.in"

CONTROLS = s57.CONTROLS
DEFAULT_C = s57.DEFAULT_C
DEFAULT_U9 = s57.DEFAULT_U9
DEFAULT_IPL = s57.DEFAULT_IPL
NOP_BATCH = s57.NOP_BATCH
NOP_WORD = s57.NOP_WORD

read_plain = s57.read_plain
read_abs = s57.read_abs
read_neg = s57.read_neg
read_quarter = s57.read_quarter
read_quarter_abs = s57.read_quarter_abs
read_quarter_neg = s57.read_quarter_neg
read_twostep_abs = s57.read_twostep_abs

VS_SCALE = "tests/grate/asm/vs_scale_w.txt"


def variant(name, vec, sfu="nop r0", mul0="", mul1="", read=None, pre="",
            delay=0, late=0, delay_word=NOP_WORD, ipl=DEFAULT_IPL,
            c=DEFAULT_C, u9=DEFAULT_U9, extra="", vs=None, args=None):
    v0, v1, v2, v3 = vec
    pr0, pr1, mid = (read if read else read_plain(0))
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
                "@SFU@": sfu,
                "@MUL0@": mul0,
                "@MUL1@": mul1,
                "@IPL@": ipl,
                "@PR0@": pr0,
                "@PR1@": pr1,
                "@PRE@": pre,
                "@EXTRA@": extra,
                "@DELAY@": delay_word * delay,
                "@MID@": mid,
                "@LATE@": NOP_BATCH * late,
            }
        },
    }
    if vs:
        entry["vs"] = vs
    if args:
        entry["args"] = args
    return entry


def write_rows(dst):
    """One ALU batch writing row registers dst[0..3] from uniforms 0..3."""
    return (
        "EXEC\n"
        "\tALU:\n"
        "\t\tALU0:\tMAD  %s,  u0,  #1,  #0\n" % dst[0] +
        "\t\tALU1:\tMAD  %s,  u1,  #1,  #0\n" % dst[1] +
        "\t\tALU2:\tMAD  %s,  u2,  #1,  #0\n" % dst[2] +
        "\t\tALU3:\tMAD  %s,  u3,  #1,  #0\n" % dst[3] +
        ";\n"
    )


# --- 1. the store-code map below 2^-14 ------------------------------------


def section_cal():
    """Make known small values with the unit and read them back.

    The stores here are the same instruction every other section uses, so this
    calibrates the code map rather than the sfu. `rcp0` of a 65504 row is
    2^-16.0 (-1/65504 = 2^-15.999), the multiplier scales that further down,
    and `sq` squares it inside the next MFU word, which reaches 2^-32 without
    needing a constant the fp20 file cannot hold.
    """
    out = []
    big = ("65504.0", "1.0", "1.0", "1.0")
    out.append(variant("cal-anchor-rcp2", ("2.0", "1.0", "1.0", "1.0"),
                       sfu="rcp r0", read=read_plain(0)))
    out.append(variant("cal-anchor-rcp4", ("4.0", "1.0", "1.0", "1.0"),
                       sfu="rcp r0", read=read_plain(0)))
    # rcp of a saturated row: 2^-16
    out.append(variant("cal-rcp65504", big, sfu="rcp r0",
                       mul0="mul0: r3, sfu, #1", read=read_plain(3)))
    # the same value scaled by rows 2^-7 and 2^-10 through the multiplier
    for tag, scale in (("2^-7", "0.0078125"), ("2^-10", "0.0009765625")):
        vec = ("65504.0", scale, "1.0", "1.0")
        out.append(variant("cal-rcp65504-x%s" % tag, vec, sfu="rcp r0",
                           mul0="mul0: r3, sfu, r1", read=read_plain(3)))
    # two steps: rcp of 65504 (2^-16) squared to 2^-32 in the next word
    sq = (
        "EXEC\n"
        "\tMFU:\tsfu: nop r0\n"
        "\t\tmul0: r3, r3, r3\n"
        "\t\tipl: NOP, NOP, NOP, NOP\n"
        ";\n"
    )
    out.append(variant("cal-rcp65504-sq", big, sfu="rcp r0",
                       mul0="mul0: r3, sfu, #1", read=read_plain(3), delay=1,
                       delay_word=sq))
    # two more squarings: 2^-64 is far below the map's bottom if the map ends
    # at 2^-30.875, so this is the clamp-or-wrap anchor
    out.append(variant("cal-rcp65504-sq4", big, sfu="rcp r0",
                       mul0="mul0: r3, sfu, #1", read=read_plain(3), delay=2,
                       delay_word=sq))
    # the top of the map: a value just under and just over 2.0
    for tag, v in (("below", "1.96875"), ("at", "2.0"), ("above", "2.0625")):
        out.append(variant("cal-top-%s" % tag, (v, "1.0", "1.0", "1.0"),
                           sfu="nop r0", read=read_plain(0)))
    # zero and a negative row, for the sign convention
    out.append(variant("cal-neg", ("-1.5", "1.0", "1.0", "1.0"),
                       sfu="nop r0", read=read_quarter_neg(0)))
    return out, 3


# --- 2. where selectors 4..7 read from ------------------------------------

X4 = ("4.0", "2.0", "1.0", "0.5")
Y4 = ("2.0", "4.0", "1.0", "0.5")
W4 = ("2.0", "1.0", "4.0", "0.5")
ROWS4 = ("0.5", "1.0", "2.0", "4.0")
SHADOW_A = ("8.0", "0.125", "3.0", "0.75", "1.0")
SHADOW_B = ("0.25", "1.5", "6.0", "1.75", "1.0")


def sel_variants(tag, vec, pre="", vs=None, args=None, c=DEFAULT_C):
    out = []
    for n in (4, 5, 6, 7):
        for op in ("rcp", "rsq"):
            out.append(variant("%s-%s%d" % (tag, op, n), vec,
                               sfu="%s r%d" % (op, n),
                               mul0="mul0: r3, sfu, #1", read=read_plain(3),
                               pre=pre, vs=vs, args=args, c=c))
    return out


def section_reg():
    out = []
    # (1) the packet rows: the note 57 baseline and the three axes
    for tag, vec in (("x4", X4), ("y4", Y4), ("w4", W4)):
        out += sel_variants("reg-%s" % tag, vec)
    # (2) what the template's row 4..7 copy leaves there, read directly
    for n in (4, 5, 6, 7):
        out.append(variant("reg-copy-r%d" % n, X4, sfu="nop r0",
                           read=read_plain(n)))
    # (3) rows 4..7 overwritten after the copy, from uniforms 0..3
    pre_a = write_rows(("r4.lh", "r5.lh", "r6.lh", "r7.lh"))
    out += sel_variants("reg-shadow-a", X4, pre=pre_a, c=SHADOW_A)
    for n in (4, 5, 6, 7):
        out.append(variant("reg-shadow-a-r%d" % n, X4, sfu="nop r0",
                           read=read_plain(n), pre=pre_a, c=SHADOW_A))
    # (4) a second overwrite with the packet rows on different values
    pre_b = write_rows(("r4.lh", "r5.lh", "r6.lh", "r7.lh"))
    out += sel_variants("reg-shadow-b", ROWS4, pre=pre_b, c=SHADOW_B)
    for n in (4, 5, 6, 7):
        out.append(variant("reg-shadow-b-r%d" % n, ROWS4, sfu="nop r0",
                           read=read_plain(n), pre=pre_b, c=SHADOW_B))
    # (5) the clip w: 1/w changes and the rows do not
    for tag, scale in (("w1", "1.0"), ("w025", "0.25"), ("w4", "4.0")):
        args = ["--vs_uniform", '["clip_scale"]=(%s,%s,%s,%s)'
                % (scale, scale, scale, scale)]
        out += sel_variants("reg-%s" % tag, X4, vs=VS_SCALE, args=args)
    return out, 3


# --- 3. the row-write source rows -----------------------------------------

WSRC2 = ["0.125", "0.2578125", "0.5", "0.75", "1.0", "1.25", "1.5", "2.0",
         "3.0", "3.5", "6.0", "-0.75", "-1.5", "-3.0", "-6.0"]

WSRC0 = ["0.125", "0.5", "0.75", "1.0", "2.0", "3.0", "4.0", "6.0",
         "-0.75", "-2.0", "-6.0"]


def section_wsrc():
    out = []
    # (1) dominant X (row 0 = 8.0): vary row 2, read the written row 0
    for v in WSRC2:
        tag = v.replace(".", "p").replace("-", "n")
        out.append(variant("wsrc-d0-x%s-q" % tag, ("8.0", "1.0", v, "1.0"),
                           sfu="rcp r0", read=read_quarter(0)))
        out.append(variant("wsrc-d0-x%s-qn" % tag, ("8.0", "1.0", v, "1.0"),
                           sfu="rcp r0", read=read_quarter_neg(0)))
    # (2) dominant Y (row 1 = 8.0): vary row 2, read the written row 1
    for v in WSRC2:
        tag = v.replace(".", "p").replace("-", "n")
        out.append(variant("wsrc-d1-x%s-q" % tag, ("1.0", "8.0", v, "1.0"),
                           sfu="rcp r9", read=read_quarter(1)))
        out.append(variant("wsrc-d1-x%s-qn" % tag, ("1.0", "8.0", v, "1.0"),
                           sfu="rcp r9", read=read_quarter_neg(1)))
    # (3) dominant Z (row 2 = 8.0): vary row 0, read the written row 0
    for v in WSRC0:
        tag = v.replace(".", "p").replace("-", "n")
        out.append(variant("wsrc-d2-x%s-q" % tag, (v, "1.0", "8.0", "1.0"),
                           sfu="rcp r10", read=read_quarter(0)))
        out.append(variant("wsrc-d2-x%s-qn" % tag, (v, "1.0", "8.0", "1.0"),
                           sfu="rcp r10", read=read_quarter_neg(0)))
    # (4) the rows that are not the source: row 1 and row 3 move under a
    # dominant X with row 2 fixed at 2.0
    for tag, vec in (("r1a", ("8.0", "0.5", "2.0", "1.0")),
                     ("r1b", ("8.0", "3.0", "2.0", "1.0")),
                     ("r3a", ("8.0", "1.0", "2.0", "4.0")),
                     ("r3b", ("8.0", "1.0", "2.0", "0.5"))):
        out.append(variant("wsrc-nosrc-%s-q" % tag, vec, sfu="rcp r0",
                           read=read_quarter(0)))
        out.append(variant("wsrc-nosrc-%s-qn" % tag, vec, sfu="rcp r0",
                           read=read_quarter_neg(0)))
    # (5) the descriptor row: row 2 after the word, at two source values
    for tag, vec in (("p", ("8.0", "1.0", "2.0", "1.0")),
                     ("n", ("-8.0", "1.0", "2.0", "1.0")),
                     ("z", ("2.0", "1.0", "8.0", "1.0"))):
        out.append(variant("wsrc-desc-%s-q2" % tag, vec, sfu="rcp r10",
                           read=read_quarter(2)))
    # (6) independent readbacks of the written row
    for tag, vec, sfu in (("d0", ("8.0", "1.0", "2.0", "1.0"), "rcp r0"),
                          ("d1", ("1.0", "8.0", "2.0", "1.0"), "rcp r9"),
                          ("d2", ("2.0", "1.0", "8.0", "1.0"), "rcp r10")):
        n = {"d0": 0, "d1": 1, "d2": 0}[tag]
        out.append(variant("wsrc-mul-%s" % tag, vec, sfu=sfu,
                           mul0="mul0: r3, r%d, #1" % n, read=read_quarter(3)))
        out.append(variant("wsrc-mul-%s-qn" % tag, vec, sfu=sfu,
                           mul0="mul0: r3, r%d, #1" % n,
                           read=read_quarter_neg(3)))
        out.append(variant("wsrc-late-%s" % tag, vec, sfu=sfu,
                           read=read_twostep_abs(n), late=1))
    # (7) a live row 2 or a latched one: word 1 writes row 2 = its descriptor
    # (2.0 for a dominant row 1 with a positive sign) over a pre-word 0.25,
    # word 2 then negates a dominant row 2 of 2.0 into row 0
    two_word = s57.mfu_word("rcp r9")
    for v in ("0.25", "0.75"):
        tag = v.replace(".", "p")
        out.append(variant("wsrc-live-%s-q0" % tag,
                           ("0.5", "8.0", v, "1.0"), sfu="rcp r8",
                           read=read_quarter(0), pre=two_word))
        out.append(variant("wsrc-live-%s-q2" % tag,
                           ("0.5", "8.0", v, "1.0"), sfu="rcp r8",
                           read=read_quarter(2), pre=two_word))
    # (8) one non-rcp control per axis, one source value
    for tag, vec, sfu in (("d0", ("8.0", "1.0", "1.5", "1.0"), "rsq r0"),
                          ("d1", ("1.0", "8.0", "1.5", "1.0"), "rsq r9"),
                          ("d2", ("1.5", "1.0", "8.0", "1.0"), "rsq r10")):
        n = {"d0": 0, "d1": 1, "d2": 0}[tag]
        out.append(variant("wsrc-rsq-%s-q" % tag, vec, sfu=sfu,
                           read=read_quarter(n)))
        out.append(variant("wsrc-rsq-%s-qn" % tag, vec, sfu=sfu,
                           read=read_quarter_neg(n)))
    return out, 3


# --- 4. the transfer function of selectors 5..7 ---------------------------

# Row 1 under test, rows 0, 2, 3 held at 0.25 so the dominant index stays 1 for
# every value above 0.25, and at 0.0625 for the values below it. The grid puts
# four points within 1% below 2.0 and three above it.
SEL_GRID = ["0.5", "0.625", "0.75", "0.875", "1.0", "1.125", "1.25",
            "1.375", "1.5", "1.625", "1.75", "1.875", "1.9375",
            "1.96875", "1.984375", "1.9921875", "1.99609375", "2.0",
            "2.0009765625", "2.00390625", "2.0078125", "2.015625",
            "2.03125", "2.0625", "2.125", "2.25", "2.5", "3.0", "4.0",
            "6.0", "8.0", "-0.75", "-1.5", "-2.5", "-4.0"]

SEL_GRID_SMALL = ["0.0625", "0.1", "0.125", "0.15", "0.2", "0.25"]


def tag_of(v):
    return v.replace(".", "p").replace("-", "n")


def section_selmap():
    out = []
    # (1) row 1, the selector 5 grid, both opcodes
    for v in SEL_GRID:
        vec = ("0.25", v, "0.25", "0.25")
        for op in ("rcp", "rsq"):
            out.append(variant("selmap-r5-%s-%s" % (tag_of(v), op), vec,
                               sfu="%s r5" % op,
                               mul0="mul0: r3, sfu, #1", read=read_plain(3)))
    for v in SEL_GRID_SMALL:
        vec = ("0.0625", v, "0.0625", "0.0625")
        for op in ("rcp", "rsq"):
            out.append(variant("selmap-r5s-%s-%s" % (tag_of(v), op), vec,
                               sfu="%s r5" % op,
                               mul0="mul0: r3, sfu, #1", read=read_plain(3)))
    # (2) rows 2 and 3 at a coarser grid, to see whether 6 and 7 follow the
    # same function
    coarse = ["0.5", "1.0", "1.5", "1.875", "1.9921875", "2.0",
              "2.0078125", "2.5", "4.0", "8.0", "-1.5"]
    for k, n in ((2, 6), (3, 7)):
        for v in coarse:
            rows = ["0.25", "0.25", "0.25", "0.25"]
            rows[k] = v
            vec = tuple(rows)
            for op in ("rcp", "rsq"):
                out.append(variant("selmap-r%d-%s-%s" % (n, tag_of(v), op),
                                   vec, sfu="%s r%d" % (op, n),
                                   mul0="mul0: r3, sfu, #1",
                                   read=read_plain(3)))
    # (3) the boundary at the same row value, different dominant index: row 1 =
    # 1.9921875 with row 0 at 0.25 (d=1) and with row 0 at 4.0 (d=0)
    for tag, vec in (("d1", ("0.25", "1.9921875", "0.25", "0.25")),
                     ("d0", ("4.0", "1.9921875", "0.25", "0.25")),
                     ("d1-at", ("0.25", "2.0", "0.25", "0.25")),
                     ("d0-at", ("4.0", "2.0", "0.25", "0.25"))):
        for op in ("rcp", "rsq"):
            out.append(variant("selmap-dom-%s-%s" % (tag, op), vec,
                               sfu="%s r5" % op,
                               mul0="mul0: r3, sfu, #1", read=read_plain(3)))
    return out, 3


# --- 5. selectors 4..7 against the clip w --------------------------------


def section_selw():
    out = []
    for tag, vec in (("below", ("0.25", "1.0", "0.25", "0.25")),
                     ("above", ("0.25", "2.0", "0.25", "0.25"))):
        for wtag, scale in (("w1", "1.0"), ("w025", "0.25"),
                            ("w4", "4.0")):
            args = ["--vs_uniform", '["clip_scale"]=(%s,%s,%s,%s)'
                    % (scale, scale, scale, scale)]
            for n in (4, 5, 6, 7):
                for op in ("rcp", "rsq"):
                    out.append(variant("selw-%s-%s-%s%d" % (tag, wtag, op, n),
                                       vec, sfu="%s r%d" % (op, n),
                                       mul0="mul0: r3, sfu, #1",
                                       read=read_plain(3), vs=VS_SCALE,
                                       args=args))
    # the rows under the scaling shader, to show the seeds did not move
    for wtag, scale in (("w025", "0.25"), ("w4", "4.0")):
        args = ["--vs_uniform", '["clip_scale"]=(%s,%s,%s,%s)'
                % (scale, scale, scale, scale)]
        for n in range(4):
            out.append(variant("selw-rows-%s-r%d" % (wtag, n),
                               ("0.25", "2.0", "0.25", "0.25"),
                               sfu="nop r0", read=read_plain(n), vs=VS_SCALE,
                               args=args))
    return out, 3


# --- 6. the row-write sources, second round -------------------------------


def section_wsrc2():
    out = []
    # (1) dominant X through the bit-3 selector r8: vary row 2, read row 0
    for v in WSRC2:
        vec = ("8.0", "1.0", v, "1.0")
        out.append(variant("wsrc2-d0-x%s-q" % tag_of(v), vec, sfu="rcp r8",
                           read=read_quarter(0)))
        out.append(variant("wsrc2-d0-x%s-qn" % tag_of(v), vec, sfu="rcp r8",
                           read=read_quarter_neg(0)))
    # (2) the rows that are not the source, now through r8
    for tag, vec in (("r1a", ("8.0", "0.5", "2.0", "1.0")),
                     ("r1b", ("8.0", "3.0", "2.0", "1.0")),
                     ("r3a", ("8.0", "1.0", "2.0", "4.0")),
                     ("r3b", ("8.0", "1.0", "2.0", "0.5")),
                     ("r0a", ("6.0", "1.0", "2.0", "1.0"))):
        out.append(variant("wsrc2-nosrc-%s-q" % tag, vec, sfu="rcp r8",
                           read=read_quarter(0)))
        out.append(variant("wsrc2-nosrc-%s-qn" % tag, vec, sfu="rcp r8",
                           read=read_quarter_neg(0)))
    # (3) a live row 2 or a latched one. Word 1 is a bit-3 word with a dominant
    # row 0 and a negative sign, so it writes row 2 = descriptor 1.0 and leaves
    # row 1 at 6.0; word 2 then has a dominant row 1 and negates row 2 into it,
    # which is -1.0 for a live row 2 and -X for a latched one.
    word1 = s57.mfu_word("rcp r8")
    for v in ("0.25", "2.0"):
        out.append(variant("wsrc2-live-%s-q1" % tag_of(v),
                           ("-8.0", "6.0", v, "1.0"), sfu="rcp r9",
                           read=read_quarter(1), pre=word1))
        out.append(variant("wsrc2-live-%s-qn1" % tag_of(v),
                           ("-8.0", "6.0", v, "1.0"), sfu="rcp r9",
                           read=read_quarter_neg(1), pre=word1))
    # (4) the same word 2 with no preceding word, for the source value itself
    for v in ("0.25", "2.0"):
        out.append(variant("wsrc2-solo-%s-qn1" % tag_of(v),
                           ("-8.0", "6.0", v, "1.0"), sfu="rcp r9",
                           read=read_quarter_neg(1)))
    # (5) the d=2 rule with row 2 negative and row 0 varied, and a delayed read
    for v in ("0.5", "2.0", "4.0"):
        for s in ("8.0", "-8.0"):
            out.append(variant("wsrc2-d2-%s-s%s-late" % (tag_of(v),
                                                          tag_of(s)),
                               (v, "1.0", s, "1.0"), sfu="rcp r10",
                               read=read_twostep_abs(0), late=1))
    return out, 3


# --- 7. negative rows and the row-write reconfirmation --------------------

# Row 3 holds a 2^12 gain, which keeps a 2^-32 result inside the code map; the
# negating read turns a negative product positive so its magnitude is visible.
GAIN_VEC = ("0.0625", None, "0.0625", "4096.0")


def gvec(v):
    return (GAIN_VEC[0], v, GAIN_VEC[2], GAIN_VEC[3])


def section_tail():
    out = []
    # (1) the negative rows, through the gain and the negating read
    for v in ("-0.1", "-0.125", "-0.2", "-0.25", "-0.375", "-0.5",
              "-0.75", "-1.0", "-1.25", "-1.5", "-1.75", "-2.0",
              "-2.5", "-3.0", "-4.0", "-6.0"):
        for op in ("rcp", "rsq"):
            out.append(variant("tail-neg-%s-%s" % (tag_of(v), op), gvec(v),
                               sfu="%s r5" % op, mul0="mul0: r2, sfu, r3",
                               read=read_neg(2)))
    # (2) the positive controls at the same gain
    for v in ("0.5", "1.0", "1.25", "1.5", "1.75", "2.0", "3.0", "6.0"):
        for op in ("rcp", "rsq"):
            out.append(variant("tail-pos-%s-%s" % (tag_of(v), op), gvec(v),
                               sfu="%s r5" % op, mul0="mul0: r2, sfu, r3",
                               read=read_plain(2)))
    # (3) selectors 5 and 7 still write no row
    for sfu in ("rcp r5", "rsq r5", "rcp r7", "rsq r7"):
        tag = sfu.replace(" ", "-")
        for n in range(4):
            out.append(variant("tail-nowrite-%s-r%d" % (tag, n), X4,
                               sfu=sfu, mul0="mul0: r3, sfu, #1",
                               read=read_quarter(n)))
    return out, 3


SECTIONS = {
    "cal": (section_cal, "s58-cal"),
    "reg": (section_reg, "s58-reg"),
    "wsrc": (section_wsrc, "s58-wsrc"),
    "selmap": (section_selmap, "s58-selmap"),
    "selw": (section_selw, "s58-selw"),
    "wsrc2": (section_wsrc2, "s58-wsrc2"),
    "tail": (section_tail, "s58-tail"),
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
        "controls": CONTROLS,
        "variants": variants,
    }
    with open(out_path, "w") as fp:
        json.dump(manifest, fp, indent=2)
        fp.write("\n")
    print("%s: %d variants, %d runs" % (name, len(variants),
                                        len(variants) * repeat))


if __name__ == "__main__":
    main()
