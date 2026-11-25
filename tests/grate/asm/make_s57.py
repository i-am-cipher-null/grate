#!/usr/bin/env python3
"""Generate the note 57 manifests for tests/grate/asm/mfu_s57_read.in.

Usage: make_s57.py <out.json> <section>

The manifests are written to whatever path is given (the note 56 generator
convention: pass tests/grate/asm_characterize.s57_<section>.json).

Sections:
  zrule     dominant-Z row-0 rule: the sign and magnitude of the pre-word row 0
            against both signs of a dominant Z, through the calibrated positive,
            negative, absolute and negated read paths and through the
            multiplier's copy of the written row 0
  ztie      d=0 and d=1 controls, the highest-index tie rule, the three-way tie
            and the all-zero vector
  zsweep    more dominant-Z magnitudes, including reads after a later batch
            boundary and a row 0 that is small next to the dominant Z
  post      the row registers after one sfu word with no multiplier at all:
            every selector 0..15, read through the scaled and negated paths
  cross     what each multiplier source (r0..r3, sfu, #1) receives inside a
            word with selectors 8..15 and with ordinary rcp, rsq, sqrt, lg2
            and frc controls, in mul0 and mul1
  cpos      multiplier destination and source-position boundaries
  internal  sfu operands 4..7 against rows, constants, ipl state, preceding
            sfu words and delays, in five opcodes

Seed rows come from uniforms 5..8; uniforms 0..4 default to 8.0, 0.125, 3.0,
0.75 and 1.0 and uniform 9 holds 0.25 for the scaled read paths, so a value
read from a row is never the value of the uniform with the same number. The
constants and the ipl operands are placeholders in this template, so a variant
can change them without touching the seeds.

Readback is `store rt1, r0, r1`: code(value) in byte 1 and 3, code = 0xF7 +
8*log2(value) for positive values, 00 for zero and negatives, FF clips at 2.0,
78 is the floor (about 2^-15.875). The plain read clips a 4.0 seed and cannot
see a negative, so the scaled (times 0.25) and negating paths carry most of the
resolution: `quarter` shows +value/4 and `quarter-neg` shows -value/4, so a cell
whose two lanes are both 00 is zero, a `quarter` hit is positive and a
`quarter-neg` hit is negative.
"""

import json
import sys

FS = "tests/grate/asm/mfu_s57_read.in"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

CONTROLS = [
    {
        "name": "immediate-operand",
        "fs": "tests/grate/asm/fs_alu_immediate_control.txt",
        "expected": "0x000000FF",
    },
    {
        "name": "varying-pipeline",
        "fs": "tests/grate/asm/fs_vs_tests.txt",
        "expected": "0xFF01FF00",
    },
]

DEFAULT_C = ("8.0", "0.125", "3.0", "0.75", "1.0")
DEFAULT_U9 = "0.25"
DEFAULT_IPL = "NOP, NOP, NOP, NOP"

NOP_BATCH = (
    "EXEC\n"
    "\tALU:\n"
    "\t\tALU0:\tNOP\n"
    "\t\tALU1:\tNOP\n"
    "\t\tALU2:\tNOP\n"
    "\t\tALU3:\tNOP\n"
    ";\n"
)

NOP_WORD = (
    "EXEC\n"
    "\tMFU:\tsfu: nop r0\n"
    "\t\tipl:  NOP, NOP, NOP, NOP\n"
    ";\n"
)

TWO_STEP_MID = (
    "EXEC\n"
    "\tALU:\n"
    "\t\tALU0:\tMAD r0.lh, r8, #1, #0\n"
    "\t\tALU1:\tMAD r1.lh, r8, #1, #0\n"
    "\t\tALU2:\tNOP\n"
    "\t\tALU3:\tNOP\n"
    ";\n"
)


def mfu_word(sfu, mul0="", mul1="", ipl=DEFAULT_IPL):
    text = "EXEC\n\tMFU:\tsfu: %s\n" % sfu
    if mul0:
        text += "\t\t%s\n" % mul0
    if mul1:
        text += "\t\t%s\n" % mul1
    text += "\t\tipl: %s\n;\n" % ipl
    return text


def read_plain(n):
    return ("MAD r0.lh, r%d, #1, #0" % n, "MAD r1.lh, r%d, #1, #0" % n, "")


def read_abs(n):
    return ("MAD r0.lh, abs(r%d), #1, #0" % n,
            "MAD r1.lh, abs(r%d), #1, #0" % n, "")


def read_neg(n):
    return ("MAD r0.lh, -r%d, #1, #0" % n, "MAD r1.lh, -r%d, #1, #0" % n, "")


def read_quarter(n):
    return ("MAD r0.lh, r%d, u9, #0" % n, "MAD r1.lh, r%d, u9, #0" % n, "")


def read_quarter_abs(n):
    return ("MAD r0.lh, abs(r%d), u9, #0" % n,
            "MAD r1.lh, abs(r%d), u9, #0" % n, "")


def read_quarter_neg(n):
    return ("MAD r0.lh, -r%d, u9, #0" % n,
            "MAD r1.lh, -r%d, u9, #0" % n, "")


def read_mulconst(n):
    return ("MAD r0.lh, r%d, u4, #0" % n, "MAD r1.lh, r%d, u4, #0" % n, "")


def read_twostep_abs(n):
    return ("MAD r8.lh, abs(r%d), #1, #0" % n, "NOP", TWO_STEP_MID)


READS = {
    "plain": read_plain,
    "abs": read_abs,
    "neg": read_neg,
    "quarter": read_quarter,
    "quarter-abs": read_quarter_abs,
    "quarter-neg": read_quarter_neg,
    "mulconst": read_mulconst,
    "twostep-abs": read_twostep_abs,
}


def variant(name, vec, sfu="rcp r10", mul0="", mul1="", read=None, delay=0,
            late=0, delay_word=NOP_WORD, ipl=DEFAULT_IPL, c=DEFAULT_C,
            u9=DEFAULT_U9, extra=""):
    v0, v1, v2, v3 = vec
    pr0, pr1, mid = (read if read else read_plain(0))
    return {
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
                "@EXTRA@": extra,
                "@DELAY@": delay_word * delay,
                "@MID@": mid,
                "@LATE@": NOP_BATCH * late,
            }
        },
    }


# --- 1. the dominant-Z row-0 rule -----------------------------------------

# Cells are (r0, r1, r2, r3). The z sign is the sign of the dominant row 2 and
# the "old" part names the sign and magnitude of the pre-word row 0.
ZCELLS = [
    ("zp-oldp2", ("2.0", "0.5", "4.0", "1.0")),
    ("zp-oldn2", ("-2.0", "0.5", "4.0", "1.0")),
    ("zn-oldp2", ("2.0", "0.5", "-4.0", "1.0")),
    ("zn-oldn2", ("-2.0", "0.5", "-4.0", "1.0")),
    ("zp-oldp075", ("0.75", "0.5", "8.0", "1.0")),
    ("zp-oldn075", ("-0.75", "0.5", "8.0", "1.0")),
    ("zn-oldp075", ("0.75", "0.5", "-8.0", "1.0")),
    ("zn-oldn075", ("-0.75", "0.5", "-8.0", "1.0")),
    ("zp-old0", ("0.0", "0.5", "4.0", "1.0")),
    ("zn-old0", ("0.0", "0.5", "-4.0", "1.0")),
    ("t2-sp-oldp", ("4.0", "0.5", "4.0", "1.0")),
    ("t2-sp-oldn", ("-4.0", "0.5", "4.0", "1.0")),
    ("t2-sm-oldp", ("4.0", "0.5", "-4.0", "1.0")),
    ("t2-sm-oldn", ("-4.0", "0.5", "-4.0", "1.0")),
    ("zp-r1neg", ("-2.0", "-0.5", "4.0", "1.0")),
    ("zero", ("0.0", "0.0", "0.0", "1.0")),
]

# writes rows 8..11 from the low uniforms (8.0, 0.125, 3.0, 0.75), to tell a row
# 8..15 read from the untouched register file
EXTRA_HIGH = (
    "EXEC\n"
    "\tALU:\n"
    "\t\tALU0:\tMAD r8.lh, u0, #1, #0\n"
    "\t\tALU1:\tMAD r9.lh, u1, #1, #0\n"
    "\t\tALU2:\tMAD r10.lh, u2, #1, #0\n"
    "\t\tALU3:\tMAD r11.lh, u3, #1, #0\n"
    ";\n"
)

ZREADS = ["quarter0", "quarter-neg0", "plain0", "abs0", "quarter1",
          "quarter2"]


def zrule_reads(path, n=0):
    if path == "quarter0":
        return read_quarter(0)
    if path == "quarter-neg0":
        return read_quarter_neg(0)
    if path == "plain0":
        return read_plain(0)
    if path == "abs0":
        return read_abs(0)
    if path == "quarter1":
        return read_quarter(1)
    if path == "quarter2":
        return read_quarter(2)
    if path == "quarter3":
        return read_quarter(3)
    raise KeyError(path)


def section_zrule():
    out = []
    for cell, vec in ZCELLS:
        for path in ZREADS:
            out.append(variant("zrule-%s-%s" % (cell, path), vec,
                               sfu="rcp r10", read=zrule_reads(path)))
        # the multiplier's own copy of the written row 0 (note 56.5: under
        # selector 10 the r0 source is the written row)
        out.append(variant("zrule-%s-mulread" % cell, vec, sfu="rcp r10",
                           mul0="mul0: r3, r0, #1", read=read_plain(3)))
    # a later batch boundary and the negated multiplier copy for the four
    # discriminating cells
    for cell in ("zp-oldp2", "zp-oldn2", "zn-oldp2", "zn-oldn2"):
        vec = dict(ZCELLS)[cell]
        out.append(variant("zrule-%s-lateabs" % cell, vec, sfu="rcp r10",
                           read=read_twostep_abs(0), late=1))
        out.append(variant("zrule-%s-mulread-qn" % cell, vec, sfu="rcp r10",
                           mul0="mul0: r3, r0, #1", read=read_quarter_neg(3)))
    # shadow row 3 and a second zero cell with a different read set
    for cell in ("zp-oldp2", "t2-sp-oldp"):
        out.append(variant("zrule-%s-quarter3" % cell, dict(ZCELLS)[cell],
                           sfu="rcp r10", read=read_quarter(3)))
    out.append(variant("zrule-zero-b-quarter2", dict(ZCELLS)["zero"],
                       sfu="rcp r10", read=read_quarter(2)))
    out.append(variant("zrule-zero-b-mulread", dict(ZCELLS)["zero"],
                       sfu="rcp r10", mul0="mul0: r3, r0, #1",
                       read=read_plain(3)))
    return out, 3


# --- 2. the d=0 / d=1 controls and the ties -------------------------------

TCELLS = [
    ("d0-p", ("4.0", "0.5", "2.0", "1.0")),
    ("d0-n", ("-4.0", "0.5", "2.0", "1.0")),
    ("d0-r2neg", ("4.0", "0.5", "-2.0", "1.0")),
    ("d1-p", ("2.0", "4.0", "0.5", "1.0")),
    ("d1-n", ("2.0", "-4.0", "0.5", "1.0")),
    ("t01-oldp", ("4.0", "-4.0", "0.5", "1.0")),
    ("t01-oldn", ("-4.0", "-4.0", "0.5", "1.0")),
    ("t12", ("0.5", "4.0", "-4.0", "1.0")),
    ("t3", ("-4.0", "-4.0", "-4.0", "1.0")),
]

TREADS = ["quarter0", "quarter-neg0", "quarter1", "quarter-neg1",
          "quarter2"]


def section_ztie():
    out = []
    for cell, vec in TCELLS:
        for path in TREADS:
            n = 1 if path.endswith("1") else (2 if path.endswith("2") else 0)
            read = read_quarter_neg(n) if "neg" in path else read_quarter(n)
            out.append(variant("ztie-%s-%s" % (cell, path), vec,
                               sfu="rcp r10", read=read))
    out.append(variant("ztie-d0-p-quarter3", dict(TCELLS)["d0-p"],
                       sfu="rcp r10", read=read_quarter(3)))
    out.append(variant("ztie-d0-p-lateabs", dict(TCELLS)["d0-p"],
                       sfu="rcp r10", read=read_twostep_abs(0), late=1))
    return out, 3


# --- 3. magnitudes, small row 0 and the zero vector -----------------------

SCELLS = [
    ("zp-oldp05", ("0.5", "0.5", "4.0", "1.0")),
    ("zp-oldn05", ("-0.5", "0.5", "4.0", "1.0")),
    ("zn-oldp05", ("0.5", "0.5", "-4.0", "1.0")),
    ("zn-oldn05", ("-0.5", "0.5", "-4.0", "1.0")),
    ("zp-oldp4", ("4.0", "0.5", "8.0", "1.0")),
    ("zp-oldn4", ("-4.0", "0.5", "8.0", "1.0")),
    ("zn-oldp4", ("4.0", "0.5", "-8.0", "1.0")),
    ("zn-oldn4", ("-4.0", "0.5", "-8.0", "1.0")),
    ("zp-oldp61", ("0.015625", "0.5", "4.0", "1.0")),
    ("zp-oldn61", ("-0.015625", "0.5", "4.0", "1.0")),
    ("zero-c", ("0.0", "0.0", "0.0", "1.0")),
]


def section_zsweep():
    out = []
    for cell, vec in SCELLS:
        for path in ("quarter0", "quarter-neg0", "quarter2"):
            out.append(variant("zsweep-%s-%s" % (cell, path), vec,
                               sfu="rcp r10", read=zrule_reads(path)))
        out.append(variant("zsweep-%s-lateabs" % cell, vec, sfu="rcp r10",
                           read=read_twostep_abs(0), late=1))
    return out, 3


# --- 4. the rows after one sfu word, without any multiplier ---------------

POST_SFUS = (["nop r0", "rcp r0", "rcp r1", "rcp r2", "rcp r3"]
             + ["rcp r%d" % i for i in range(4, 16)])

POST_VEC = ("4.0", "2.0", "1.0", "0.5")


def section_post():
    out = []
    for sfu in POST_SFUS:
        tag = sfu.replace(" ", "-")
        for n in (0, 1, 2):
            out.append(variant("post-%s-q%d" % (tag, n), POST_VEC, sfu=sfu,
                               read=read_quarter(n)))
            out.append(variant("post-%s-qn%d" % (tag, n), POST_VEC, sfu=sfu,
                               read=read_quarter_neg(n)))
        if 4 <= int(sfu.split()[-1][1:]) <= 7:
            out.append(variant("post-%s-q3" % tag, POST_VEC, sfu=sfu,
                               read=read_quarter(3)))
    return out, 3


# --- 5. the multiplier sources inside a normalization word -----------------

XP_VEC = ("2.0", "0.5", "1.0", "0.25")
XN_VEC = ("-2.0", "0.5", "1.0", "0.25")

CROSS_SFUS = (["nop r0", "rcp r0", "rcp r1", "rcp r2", "rcp r3"]
              + ["rcp r%d" % i for i in range(8, 16)]
              + ["rcp r16", "rcp r50"]
              + ["rsq r8", "rsq r10", "sqrt r10", "lg2 r10", "frc r10"])

SRCS = ("r0", "r1", "r2", "r3")


def cross_name(sfu, unit, src, vec):
    return "cross-%s-%s-%s-%s" % (vec, sfu.replace(" ", "-"), unit, src)


def section_cross():
    out = []
    for sfu in CROSS_SFUS:
        for src in SRCS:
            for unit in ("mul0", "mul1"):
                mul = "%s: r3, %s, #1" % (unit, src)
                out.append(variant(cross_name(sfu, unit, src, "xp"), XP_VEC,
                                   sfu=sfu,
                                   mul0=mul if unit == "mul0" else "",
                                   mul1=mul if unit == "mul1" else "",
                                   read=read_plain(3)))
        for unit in ("mul0", "mul1"):
            mul = "%s: r3, sfu, #1" % unit
            out.append(variant(cross_name(sfu, unit, "sfu", "xp"), XP_VEC,
                               sfu=sfu,
                               mul0=mul if unit == "mul0" else "",
                               mul1=mul if unit == "mul1" else "",
                               read=read_plain(3)))
    # the #1 source and the second operand position
    for sfu in ("rcp r8", "rcp r10"):
        for unit in ("mul0", "mul1"):
            mul = "%s: r3, #1, #1" % unit
            out.append(variant(cross_name(sfu, unit, "num1", "xp"), XP_VEC,
                               sfu=sfu,
                               mul0=mul if unit == "mul0" else "",
                               mul1=mul if unit == "mul1" else "",
                               read=read_plain(3)))
        for src in ("r0", "r2"):
            for unit in ("mul0", "mul1"):
                mul = "%s: r3, #1, %s" % (unit, src)
                out.append(variant(cross_name(sfu, unit, src + "-pos2", "xp"),
                                   XP_VEC, sfu=sfu,
                                   mul0=mul if unit == "mul0" else "",
                                   mul1=mul if unit == "mul1" else "",
                                   read=read_plain(3)))
    # the negative dominant component, for three selectors
    for sfu in ("rcp r0", "rcp r8", "rcp r10"):
        for src in SRCS:
            for unit in ("mul0", "mul1"):
                mul = "%s: r3, %s, #1" % (unit, src)
                out.append(variant(cross_name(sfu, unit, src, "xn"), XN_VEC,
                                   sfu=sfu,
                                   mul0=mul if unit == "mul0" else "",
                                   mul1=mul if unit == "mul1" else "",
                                   read=read_plain(3)))
    return out, 3


# --- 6. destination and source-position boundaries ------------------------

def section_cpos():
    out = []
    for sfu in ("rcp r8", "rcp r10"):
        tag = sfu.replace(" ", "-")
        for src in ("r0", "r2"):
            for dest in (0, 1, 3):
                out.append(variant("cpos-%s-src%s-dst%d-q" % (tag, src, dest),
                                   XP_VEC, sfu=sfu,
                                   mul0="mul0: r%d, %s, #1" % (dest, src),
                                   read=read_quarter(dest)))
                out.append(variant("cpos-%s-src%s-dst%d-qn" % (tag, src, dest),
                                   XP_VEC, sfu=sfu,
                                   mul0="mul0: r%d, %s, #1" % (dest, src),
                                   read=read_quarter_neg(dest)))
    return out, 3


# --- 7. the internal sfu operands 4..7 ------------------------------------

AXES = [
    ("X4", ("4.0", "2.0", "1.0", "0.5")),
    ("Y4", ("2.0", "4.0", "1.0", "0.5")),
    ("Z4", ("2.0", "1.0", "4.0", "0.5")),
]

OPS = ("rcp", "rsq", "sqrt", "lg2", "frc")


def ones_except(k, value):
    rows = ["1.0"] * 4
    if k < 4:
        rows[k] = value
    return tuple(rows)


def section_internal():
    out = []
    # (1) opcode and axis coverage
    for op in OPS:
        for n in range(4, 8):
            for axis, vec in AXES:
                out.append(variant("int-%s%d-%s" % (op, n, axis), vec,
                                   sfu="%s r%d" % (op, n),
                                   mul0="mul0: r3, sfu, #1",
                                   read=read_plain(3)))
    # (2) capture path and destination
    for n in range(4, 8):
        out.append(variant("int-rcp%d-Z4-mul1" % n, dict(AXES)["Z4"],
                           sfu="rcp r%d" % n, mul1="mul1: r3, sfu, #1",
                           read=read_plain(3)))
        out.append(variant("int-rcp%d-Z4-mul0r0" % n, dict(AXES)["Z4"],
                           sfu="rcp r%d" % n, mul0="mul0: r0, sfu, #1",
                           read=read_quarter(0)))
    # (3) one row at a time, for the selectors that looked axis-dependent. Rows
    # 0..3 come from the seed vector; rows 8..11 get a second value from the
    # extra batch, so a dependence on the high half of the register file would
    # show as a different code.
    for n in (4, 5, 6, 7):
        for k in range(4):
            for mag in ("4.0", "0.5"):
                out.append(variant("int-rcp%d-row%d-%s" % (n, k, mag),
                                   ones_except(k, mag), sfu="rcp r%d" % n,
                                   mul0="mul0: r3, sfu, #1",
                                   read=read_plain(3)))
        out.append(variant("int-rcp%d-ones" % n, ones_except(0, "1.0"),
                           sfu="rcp r%d" % n, mul0="mul0: r3, sfu, #1",
                           read=read_plain(3)))
        out.append(variant("int-rcp%d-highrows" % n, ones_except(0, "1.0"),
                           sfu="rcp r%d" % n, mul0="mul0: r3, sfu, #1",
                           read=read_plain(3), extra=EXTRA_HIGH))
    # (4) the low constant file
    alts = [("alt1", ("1.0", "2.0", "4.0", "0.5", "0.25")),
            ("alt2", ("0.25", "4.0", "0.5", "8.0", "2.0"))]
    for tag, c in alts:
        for n in range(4, 8):
            out.append(variant("int-rcp%d-%s" % (n, tag),
                               dict(AXES)["X4"], sfu="rcp r%d" % n,
                               mul0="mul0: r3, sfu, #1",
                               read=read_plain(3), c=c))
    # (5) the interpolator operands
    for ipl in ("t0.fp20, t0.fp20, NOP, NOP", "t0.fp20, NOP, NOP, NOP"):
        for n in range(4, 8):
            out.append(variant("int-rcp%d-ipl-%s" % (n, ipl.split(".")[0]),
                               dict(AXES)["X4"], sfu="rcp r%d" % n,
                               mul0="mul0: r3, sfu, #1",
                               read=read_plain(3), ipl=ipl))
    # (6) a preceding sfu word
    preceding = [
        ("nop", NOP_WORD),
        ("rcp0", mfu_word("rcp r0")),
        ("rcp10", mfu_word("rcp r10")),
        ("rcp8", mfu_word("rcp r8")),
    ]
    for n in (4, 5):
        for tag, word in preceding:
            out.append(variant("int-rcp%d-after-%s" % (n, tag),
                               dict(AXES)["X4"], sfu="rcp r%d" % n,
                               mul0="mul0: r3, sfu, #1",
                               read=read_plain(3), delay=1,
                               delay_word=word))
    # (7) one and two empty words between the sfu and the capture
    for n, axis in ((5, "Y4"), (6, "Z4")):
        for d in (1, 2):
            out.append(variant("int-rcp%d-%s-delay%d" % (n, axis, d),
                               dict(AXES)[axis], sfu="rcp r%d" % n,
                               mul0="mul0: r3, sfu, #1",
                               read=read_plain(3), delay=d))
    return out, 3


# --- 8. the decisive multiplier matrix: the dominant row is not row 0 ------

# Each vector puts the maximum somewhere other than row 0, so "row 2's value"
# and "M" are different codes, and the descriptor is different from both. Read
# through the 0.25 scale, and the negated scale for the negative rows.
CROSS2_VECS = [
    # (name, rows, d, s, M, 1/M, written rows)
    ("c0", ("4.0", "1.5", "2.0", "0.75")),   # d=0 s=+ row0 <- -2.0
    ("c1p", ("1.5", "4.0", "0.75", "0.5")),  # d=1 s=+ row1 <- -0.75
    ("c1n", ("-1.5", "-4.0", "0.75", "0.5")),  # d=1 s=- row1 <- +0.75
    ("c2n", ("1.5", "2.0", "-4.0", "0.5")),  # d=2 s=- row0 <- -1.5, desc 5
]

CROSS2_SFUS = ["nop r0", "rcp r0", "rcp r1", "rcp r8", "rcp r9",
               "rcp r10", "rcp r11", "rcp r12", "rcp r15", "rcp r50",
               "rsq r10", "sqrt r10"]


def section_cross2():
    out = []
    for vname, vec in CROSS2_VECS:
        for sfu in CROSS2_SFUS:
            tag = sfu.replace(" ", "-")
            for src in SRCS:
                for path, read in (("q", read_quarter(3)),
                                   ("qn", read_quarter_neg(3))):
                    out.append(variant("cross2-%s-%s-%s-%s" %
                                       (vname, tag, src, path), vec,
                                       sfu=sfu,
                                       mul0="mul0: r3, %s, #1" % src,
                                       read=read))
    # the second multiplier unit on one vector, as a unit control
    for sfu in ("rcp r8", "rcp r9", "rcp r10", "rcp r11"):
        tag = sfu.replace(" ", "-")
        for src in SRCS:
            out.append(variant("cross2-c1p-%s-%s-mul1" % (tag, src),
                               dict(CROSS2_VECS)["c1p"],
                               sfu=sfu,
                               mul1="mul1: r3, %s, #1" % src,
                               read=read_quarter(3)))
    return out, 3


# --- 9. the threshold behind selectors 5..7 -------------------------------

THRESH_MAGS = ("0.5", "1.0", "1.5", "2.0", "4.0")


def section_internal2():
    out = []
    for k in range(4):
        for mag in THRESH_MAGS:
            vec = ones_except(k, mag)
            for n in (4, 5, 6, 7):
                for op in ("rsq", "frc"):
                    out.append(variant("int2-%s%d-row%d-%s" %
                                       (op, n, k, mag), vec,
                                       sfu="%s r%d" % (op, n),
                                       mul0="mul0: r3, sfu, #1",
                                       read=read_plain(3)))
            for n in (5, 6, 7):
                if mag in ("1.5", "2.0"):
                    out.append(variant("int2-rcp%d-row%d-%s" % (n, k, mag),
                                       vec, sfu="rcp r%d" % n,
                                       mul0="mul0: r3, sfu, #1",
                                       read=read_plain(3)))
    for axis, vec in AXES:
        for n in (4, 5, 6, 7):
            for op in ("rsq", "frc"):
                out.append(variant("int2-%s%d-%s" % (op, n, axis), vec,
                                   sfu="%s r%d" % (op, n),
                                   mul0="mul0: r3, sfu, #1",
                                   read=read_plain(3)))
    return out, 3


# --- 10. the same source slots under the other opcodes --------------------

def section_cross3():
    out = []
    for vname in ("c0", "c1p"):
        vec = dict(CROSS2_VECS)[vname]
        for op in ("rsq", "sqrt", "frc", "lg2"):
            for n in (8, 9, 10, 11):
                for src in SRCS:
                    out.append(variant("cross3-%s-%s%d-%s-q" %
                                       (vname, op, n, src), vec,
                                       sfu="%s r%d" % (op, n),
                                       mul0="mul0: r3, %s, #1" % src,
                                       read=read_quarter(3)))
                if vname == "c1p":
                    for src in ("r0", "r1"):
                        out.append(variant("cross3-%s-%s%d-%s-qn" %
                                           (vname, op, n, src), vec,
                                           sfu="%s r%d" % (op, n),
                                           mul0="mul0: r3, %s, #1" % src,
                                           read=read_quarter_neg(3)))
    return out, 3


# --- 11. the row writes of the other opcodes ------------------------------

def section_post2():
    out = []
    for sfu in ("rsq r8", "rsq r10", "sqrt r11", "lg2 r10", "frc r9"):
        tag = sfu.replace(" ", "-")
        for n in (0, 1, 2):
            out.append(variant("post2-%s-q%d" % (tag, n), POST_VEC, sfu=sfu,
                               read=read_quarter(n)))
            out.append(variant("post2-%s-qn%d" % (tag, n), POST_VEC, sfu=sfu,
                               read=read_quarter_neg(n)))
    return out, 3


SECTIONS = {
    "zrule": (section_zrule, "s57-zrule"),
    "post2": (section_post2, "s57-post2"),
    "cross2": (section_cross2, "s57-cross2"),
    "cross3": (section_cross3, "s57-cross3"),
    "internal2": (section_internal2, "s57-internal2"),
    "ztie": (section_ztie, "s57-ztie"),
    "zsweep": (section_zsweep, "s57-zsweep"),
    "post": (section_post, "s57-post"),
    "cross": (section_cross, "s57-cross"),
    "cpos": (section_cpos, "s57-cpos"),
    "internal": (section_internal, "s57-internal"),
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
        "program": {"vs": VS, "fs": FS, "lnk": LNK},
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
