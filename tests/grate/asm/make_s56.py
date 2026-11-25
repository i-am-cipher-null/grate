#!/usr/bin/env python3
"""Generate the note 56 manifests for tests/grate/asm/mfu_s56_read.in.

Usage: make_s56.py <out.json> <section> [args]

Manifests are written as tests/grate/asm_characterize.<name>.json, using the
name the section prints, and reference the templates
tests/grate/asm/mfu_s56_read.in, mfu_s56_codes.in, mfu_s56_rows.in and
mfu_s56_idx.in. `pilot` regenerates the reseeded pilot (s56_pilot2); the
earlier s56_pilot manifest, whose seeds came from uniforms 0..3, is kept only
as the run input it was and is not reproduced here.

Sections:
  codes            readback code table, two values per run
  pilot            selector-screen pilot over a spread of encodings
  sel LO HI        screen every sfu operand selector in [LO, HI]
  scale SEL...     reciprocal scaling for candidate selectors
  ops SEL...       other single-step sfu opcodes on the candidate selectors
  src SEL          multiplier source substitution with a maximum in r0 and r2
  row              sign-and-axis matrix, rows 0..7, plain and scaled reads
  path             read transforms for the positive and negative axis cases
  delay            read timing: intervening words and later batch boundaries
  shape            no-multiplier, single-multiplier and compiler-word shapes

Seed rows come from uniforms 5..8; uniforms 0..4 hold 8.0, 0.125, 3.0, 0.75,
1.0 and uniform 9 holds 0.25 for the scaled read paths, so a value read from a
row is never the value of the uniform with the same number.

Readback is `store rt1, r0, r1`: code(value) in byte 1 and 3, code = 0xF7 +
8*log2(value) for positive values, 00 for zero and negatives, FF clips at 2.0.
The plain read clips a 4.0 seed and cannot see a negative, so the scaled
(`quarter`, times 0.25) and negating paths carry most of the resolution.
"""

import json
import sys

# Seed vectors: r0, r1, r2, r3.
VEC = {
    # brief 1.1: same maximum magnitude 4.0, the dominant axis moves
    "X4": ("4.0", "2.0", "1.0", "0.5"),
    "Y4": ("2.0", "4.0", "1.0", "0.5"),
    "Z4": ("2.0", "1.0", "4.0", "0.5"),
    # scaling: maximum magnitude 2.0
    "X2": ("2.0", "1.0", "0.5", "0.25"),
    "Y2": ("1.0", "2.0", "0.5", "0.25"),
    "Z2": ("1.0", "0.5", "2.0", "0.25"),
    # scaling: maximum magnitude 8.0
    "X8": ("8.0", "2.0", "0.5", "0.25"),
    "Y8": ("2.0", "8.0", "0.5", "0.25"),
    "Z8": ("2.0", "0.5", "8.0", "0.25"),
    # negative dominant, maximum magnitude 4.0 and 8.0
    "nX4": ("-4.0", "2.0", "0.5", "0.25"),
    "nY4": ("2.0", "-4.0", "0.5", "0.25"),
    "nZ4": ("2.0", "0.5", "-4.0", "0.25"),
    "nX8": ("-8.0", "2.0", "0.5", "0.25"),
    "nY8": ("2.0", "-8.0", "0.5", "0.25"),
    "nZ8": ("2.0", "0.5", "-8.0", "0.25"),
    # brief 2.1: fixed maximum 4.0, distinct subordinates
    "pX": ("4.0", "2.0", "0.5", "1.0"),
    "pY": ("2.0", "4.0", "0.5", "1.0"),
    "pZ": ("2.0", "0.5", "4.0", "1.0"),
    "mX": ("-4.0", "2.0", "0.5", "1.0"),
    "mY": ("2.0", "-4.0", "0.5", "1.0"),
    "mZ": ("2.0", "0.5", "-4.0", "1.0"),
    # ties
    "tXY": ("4.0", "-4.0", "0.5", "1.0"),
    "tXZ": ("4.0", "0.5", "-4.0", "1.0"),
    "tYZ": ("0.5", "4.0", "-4.0", "1.0"),
    "tAll": ("-4.0", "-4.0", "-4.0", "1.0"),
    # fractional maximum for the fractional opcodes
    "fX": ("1.125", "0.5", "0.25", "0.25"),
    "fY": ("0.5", "1.125", "0.25", "0.25"),
    "fZ": ("0.5", "0.25", "1.125", "0.25"),
}

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

FS = "tests/grate/asm/mfu_s56_read.in"
FS_CODES = "tests/grate/asm/mfu_s56_codes.in"
FS_ROWS = "tests/grate/asm/mfu_s56_rows.in"

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
    """Multiply by 1.0 from the constant file through the ordinary ALU path."""
    return ("MAD r0.lh, r%d, u4, #0" % n, "MAD r1.lh, r%d, u4, #0" % n, "")


def read_twostep(n):
    return ("MAD r8.lh, r%d, #1, #0" % n, "NOP", TWO_STEP_MID)


def read_twostep_abs(n):
    return ("MAD r8.lh, abs(r%d), #1, #0" % n, "NOP", TWO_STEP_MID)


def read_rb(n):
    return ("MAD r0.lh, r%d, #1, #0" % n, "MAD r1.lh, r%d, #1, #0" % n, "")


def read_rc(n):
    return ("MAD r0.lh, #1, r%d, #0" % n, "MAD r1.lh, #1, r%d, #0" % n, "")


READS = {
    "plain": read_plain,
    "abs": read_abs,
    "neg": read_neg,
    "quarter": read_quarter,
    "quarter-abs": read_quarter_abs,
    "quarter-neg": read_quarter_neg,
    "mulconst": read_mulconst,
    "twostep": read_twostep,
    "twostep-abs": read_twostep_abs,
    "rb": read_rb,
    "rc": read_rc,
}


def variant(name, vec, sfu="rcp r10", mul0="", mul1="", read=None, delay=0,
            late=0, delay_word=NOP_WORD):
    v0, v1, v2, v3 = VEC[vec]
    pr0, pr1, mid = (read if read else read_plain(0))
    return {
        "name": name,
        "fs": FS,
        "replace": {
            "fs": {
                "@V0@": v0,
                "@V1@": v1,
                "@V2@": v2,
                "@V3@": v3,
                "@SFU@": sfu,
                "@MUL0@": mul0,
                "@MUL1@": mul1,
                "@PR0@": pr0,
                "@PR1@": pr1,
                "@DELAY@": delay_word * delay,
                "@MID@": mid,
                "@LATE@": NOP_BATCH * late,
            }
        },
    }


CAPTURE = "mul0: r3, sfu, #1"


def capture_variant(name, vec, sfu):
    """Screen shape: the sfu result goes through mul0 into r3 and r3 is read in
    the next ALU batch, so the measured value is always the multiplier output."""
    return variant(name, vec, sfu=sfu, mul0=CAPTURE, read=read_plain(3))


AXES4 = ("X4", "Y4", "Z4")


def section_codes():
    pairs = [
        ("4.0", "2.0"),
        ("1.0", "0.5"),
        ("0.25", "0.125"),
        ("0.0625", "0.03125"),
        ("0.015625", "0.0078125"),
        ("0.00390625", "0.001953125"),
        ("0.00048828125", "0.000244140625"),
        ("0.0001220703125", "0.00006103515625"),
        ("0.000030517578125", "0.0000152587890625"),
        ("0.0", "-1.0"),
        ("3.0", "0.75"),
        ("1.5", "0.0625"),
    ]
    out = []
    for lo, hi in pairs:
        out.append({
            "name": "code-%s-%s" % (lo.replace(".", "p"), hi.replace(".", "p").replace("-", "n")),
            "fs": FS_CODES,
            "replace": {"fs": {"@V0@": lo, "@V1@": hi}},
        })
    return out, 3


PILOT_SELECTORS = list(range(0, 18)) + [24, 25, 28, 31, 32, 33, 40, 41, 63]


def section_pilot():
    out = []
    for sel in PILOT_SELECTORS:
        for axis in AXES4:
            out.append(capture_variant("pilot-sel%d-%s" % (sel, axis), axis,
                                       "rcp r%d" % sel))
    for axis in AXES4:
        out.append(capture_variant("pilot-ctl-rcp1-%s" % axis, axis, "rcp r1"))
    for axis in AXES4:
        out.append(capture_variant("pilot-ctl-unit-%s" % axis, axis, "nop r0"))
    return out, 3


def rows_variant(name, sfu, mul0, read):
    pr0, pr1 = read[0], read[1]
    return {
        "name": name,
        "fs": FS_ROWS,
        "replace": {"fs": {"@SFU@": sfu, "@MUL0@": mul0,
                             "@PR0@": pr0, "@PR1@": pr1}},
    }


def section_rows():
    """Does selector i read the contents of row i? Every row is seeded with a
    distinct value; the sfu result is captured into r15, except for the selector
    that names r15 itself, which is captured into r3."""
    out = []
    for sel in range(16):
        # the multiplier destination can only be r0..r3, so the result is
        # captured into r3 and read from there; the selector reads row 3 before
        # that write, so row 3's own content is still the operand
        out.append(rows_variant("rowidx-sel%d" % sel, "rcp r%d" % sel,
                                "mul0: r3, sfu, #1", read_plain(3)))
    out.append(rows_variant("rowidx-nop", "nop r0", "", read_plain(15)))
    for row in range(16):
        out.append(rows_variant(
            "seed-r%d" % row, "nop r0", "",
            ("MAD r0.lh, r%d, u16, #0" % row,
             "MAD r1.lh, r%d, u16, #0" % row)))
    return out, 3


EACH_SELS = (0, 1, 2, 3, 8, 9, 10, 11, 12, 13, 14, 15)
EACH_VECS = ("pX", "pZ", "mX")


def section_each():
    """What each selector does to rows 0..3, through the plain and the 0.25
    scaled read. No multiplier is active, so nothing else writes the rows."""
    out = []
    for sel in EACH_SELS:
        for vec in EACH_VECS:
            for row in range(4):
                for path in ("plain", "quarter", "quarter-neg"):
                    out.append(variant(
                        "each-sel%d-%s-r%d-%s" % (sel, vec, row, path), vec,
                        sfu="rcp r%d" % sel, read=READS[path](row)))
    return out, 3


def section_diag():
    """Is the scaling uniform readable at all? Read u9, u5 and u4 directly, and
    the row times u9, with no sfu operation."""
    out = []
    reads = {
        "u9": ("MAD r0.lh, u9, #1, #0", "MAD r1.lh, u9, #1, #0"),
        "u5": ("MAD r0.lh, u5, #1, #0", "MAD r1.lh, u5, #1, #0"),
        "u4": ("MAD r0.lh, u4, #1, #0", "MAD r1.lh, u4, #1, #0"),
        "u9u9": ("MAD r0.lh, u9, u9, #1", "MAD r1.lh, u9, u9, #1"),
        "r0u9": ("MAD r0.lh, r0, u9, #0", "MAD r1.lh, r0, u9, #0"),
        "r0u4": ("MAD r0.lh, r0, u4, #0", "MAD r1.lh, r0, u4, #0"),
        "u9r0": ("MAD r0.lh, u9, r0, #0", "MAD r1.lh, u9, r0, #0"),
        "r0q": ("MAD r0.lh, r0, u9, #1", "MAD r1.lh, r0, u9, #1"),
    }
    for name, (p0, p1) in reads.items():
        for vec in ("pX", "pZ"):
            out.append(variant("diag-%s-%s" % (name, vec), vec, sfu="nop r0",
                               read=(p0, p1, "")))
    return out, 3


def section_diag2():
    """Does an sfu word change what the next ALU batch reads from a uniform
    operand? The 0.25 scaling uniform read back as zero after `rcp rN` but not
    after `nop r0`."""
    reads = {
        "u9": ("MAD r0.lh, u9, #1, #0", "MAD r1.lh, u9, #1, #0"),
        "u4": ("MAD r0.lh, u4, #1, #0", "MAD r1.lh, u4, #1, #0"),
        "u5": ("MAD r0.lh, u5, #1, #0", "MAD r1.lh, u5, #1, #0"),
        "r0u9": ("MAD r0.lh, r0, u9, #0", "MAD r1.lh, r0, u9, #0"),
        "r0r1": ("MAD r0.lh, r0, r1, #0", "MAD r1.lh, r0, r1, #0"),
        "r0one": ("MAD r0.lh, r0, #1, #0", "MAD r1.lh, r0, #1, #0"),
    }
    words = {"nop": "nop r0", "rcp0": "rcp r0", "rcp10": "rcp r10"}
    out = []
    for wname, word in words.items():
        for rname, read in reads.items():
            for late in (0, 1):
                out.append(variant(
                    "diag2-%s-%s-l%d" % (wname, rname, late), "pX",
                    sfu=word, read=(read[0], read[1], ""), late=late))
    # does an intervening MFU word restore the uniform read?
    for rname, read in reads.items():
        out.append(variant(
            "diag2-rcp10-delay-%s" % rname, "pX", sfu="rcp r10",
            read=(read[0], read[1], ""), delay=1))
    return out, 3


WORD2 = {
    "reread-sfu0": (
        "EXEC\n\tMFU:\tsfu: rcp r0\n\t\tmul0: r3, sfu, #1\n"
        "\t\tipl:  NOP, NOP, NOP, NOP\n;\n"
    ),
    "reread-sfu10": (
        "EXEC\n\tMFU:\tsfu: rcp r10\n\t\tmul0: r3, sfu, #1\n"
        "\t\tipl:  NOP, NOP, NOP, NOP\n;\n"
    ),
    "reread-mul0": (
        "EXEC\n\tMFU:\tsfu: nop r0\n\t\tmul0: r3, r0, #1\n"
        "\t\tipl:  NOP, NOP, NOP, NOP\n;\n"
    ),
    "reread-mul2": (
        "EXEC\n\tMFU:\tsfu: nop r0\n\t\tmul0: r3, r2, #1\n"
        "\t\tipl:  NOP, NOP, NOP, NOP\n;\n"
    ),
}


def section_reread():
    """Is the post-word value a write to the row or a property of the read?
    Re-read the rows with a second word's sfu and with a second word's multiplier
    source, and read them after one and two later batch boundaries."""
    out = []
    for vec in ("pX", "mX", "pZ", "mZ"):
        for sel in (10, 0):
            word = "rcp r%d" % sel
            for wname, word2 in WORD2.items():
                out.append(variant("%s-%s-sel%d" % (wname, vec, sel), vec,
                                   sfu=word, read=read_plain(3), delay=1,
                                   delay_word=word2))
            for late in (1, 2):
                out.append(variant("reread-late%d-%s-sel%d" % (late, vec, sel),
                                   vec, sfu=word, read=read_plain(0),
                                   late=late))
                out.append(variant("reread-late%d-q-%s-sel%d" % (late, vec, sel),
                                   vec, sfu=word, read=read_quarter(0),
                                   late=late))
            # read the same row from the other ALU source slot
            out.append(variant("reread-rc-%s-sel%d" % (vec, sel), vec,
                               sfu=word, read=read_rc(0)))
            out.append(variant("reread-rc-q-%s-sel%d" % (vec, sel), vec,
                               sfu=word, read=("MAD r0.lh, u9, r0, #0",
                                               "MAD r1.lh, u9, r0, #0", "")))
            # abs of the same row, in this run only
            out.append(variant("reread-abs-%s-sel%d" % (vec, sel), vec,
                               sfu=word, read=read_abs(0)))
            out.append(variant("reread-abs-q-%s-sel%d" % (vec, sel), vec,
                               sfu=word,
                               read=("MAD r0.lh, abs(r0), u9, #0",
                                     "MAD r1.lh, abs(r0), u9, #0", "")))
    return out, 3


SWEEP_VALUES = ("-8.0", "-4.0", "-2.0", "-1.0", "-0.5", "0.5", "1.0",
                "2.0", "4.0", "8.0")


def vec_named(name, values):
    VEC[name] = values
    return name


def section_sweep():
    """Move one component at a time through the dominance boundary and read all
    three coordinate rows with the scaled and the negated-scaled path, so each
    post-word value gets its magnitude and its sign."""
    out = []
    base = ["1.25", "0.75"]
    for slot in range(3):
        for i, value in enumerate(SWEEP_VALUES):
            seed = [base[0], base[1], "0.5", "1.0"]
            seed[slot] = value
            name = vec_named("sw%d%d" % (slot, i), tuple(seed))
            for row in range(3):
                for path in ("quarter", "quarter-neg"):
                    out.append(variant(
                        "sweep-s%d-v%d-r%d-%s" % (slot, i, row, path), name,
                        sfu="rcp r10", read=READS[path](row)))
    return out, 3


SIGN2 = {
    "mZ-base": ("2.0", "0.5", "-4.0", "1.0"),
    "mZ-r0-025": ("0.25", "0.5", "-4.0", "1.0"),
    "mZ-r0-3": ("3.0", "0.5", "-4.0", "1.0"),
    "mZ-r1-3": ("2.0", "3.0", "-4.0", "1.0"),
    "mZ-r1-025": ("2.0", "0.25", "-4.0", "1.0"),
    "mZ8": ("4.0", "0.5", "-8.0", "1.0"),
    "pZ-base": ("2.0", "0.5", "4.0", "1.0"),
    "pX3": ("3.0", "2.0", "1.0", "1.0"),
    "mY3": ("3.0", "-2.0", "1.0", "1.0"),
    "mX3": ("-3.0", "2.0", "1.0", "1.0"),
    "mZ-small": ("3.0", "2.0", "-1.0", "1.0"),
    "pY3": ("0.5", "2.0", "1.0", "1.0"),
    "zero": ("0.0", "0.0", "0.0", "1.0"),
}


def section_sign2():
    """Pin what rows 0..2 hold after a special word with seeds chosen so each
    candidate rule gives a different reading."""
    out = []
    for name, seed in SIGN2.items():
        vec = vec_named("s2-" + name, seed)
        for slot in range(3):
            for row in range(3):
                for path in ("quarter", "quarter-neg"):
                    out.append(variant(
                        "sign2-%s-s%d-r%d-%s" % (name, slot, row, path), vec,
                        sfu="rcp r10", read=READS[path](row)))
    return out, 3


SIGN3 = {
    "a": ("4.0", "2.0", "0.75", "1.0"),
    "b": ("0.25", "3.0", "0.75", "1.0"),
    "c": ("1.0", "2.0", "-0.75", "1.0"),
    "d": ("0.5", "0.25", "-3.0", "1.0"),
    "e": ("2.0", "0.25", "-3.0", "1.0"),
    "f": ("2.0", "0.25", "3.0", "1.0"),
    "g": ("3.0", "0.5", "0.75", "1.0"),
    "h": ("0.75", "3.0", "0.5", "1.0"),
    "i": ("-0.5", "-0.25", "-3.0", "1.0"),
    "j": ("4.0", "4.0", "4.0", "1.0"),
    "k": ("4.0", "4.0", "0.5", "1.0"),
    "l": ("-4.0", "-4.0", "0.5", "1.0"),
    "m": ("1.0", "4.0", "2.0", "1.0"),
    "n": ("-1.0", "4.0", "2.0", "1.0"),
}


def section_sign3():
    """Test the rule the sign2 sweep suggests: the row of the dominant axis takes
    minus the value that row 2 held, the other coordinate row keeps its seed, and
    row 2 takes the axis/sign code; when row 2 itself is the negative dominant,
    row 0 is negated instead."""
    out = []
    for name, seed in SIGN3.items():
        vec = vec_named("s3-" + name, seed)
        for row in range(3):
            for path in ("quarter", "quarter-neg"):
                out.append(variant(
                    "sign3-%s-r%d-%s" % (name, row, path), vec,
                    sfu="rcp r10", read=READS[path](row)))
    return out, 3


FS_IDX = "tests/grate/asm/mfu_s56_idx.in"


def section_idx():
    """Set one row to 0.5 or 4.0 with every other row at 1.0 and run the matching
    operand index: a selector that reads row N returns 2.0 (FF) or 0.25 (E7),
    a selector that reads an internal source returns the same code for both."""
    out = []
    for target in range(16):
        for value in ("0.5", "4.0"):
            rows = {i: "1.0" for i in range(16)}
            rows[target] = value
            replace = {"@W%d@" % i: rows[i] for i in range(16)}
            replace["@SFU@"] = "rcp r%d" % target
            out.append({
                "name": "idx-%d-%s" % (target, value.replace(".", "p")),
                "fs": FS_IDX,
                "replace": {"fs": replace},
            })
    return out, 3


MULMAP_SRC = {"r0": "r0", "r1": "r1", "r2": "r2", "r3": "r3",
              "sfu": "sfu", "c1": "#1"}


def section_mulmap():
    """What does each multiplier source deliver in a word whose sfu source is in
    the normalization family? No sfu capture, so the row operands are what the
    multiplier reads, plus the substitution the note 55 run found on row 2."""
    out = []
    for sel in range(8, 16):
        for vec in ("pX", "pZ", "mX"):
            for name, src in MULMAP_SRC.items():
                out.append(variant(
                    "mulmap-%d-%s-%s" % (sel, name, vec), vec,
                    sfu="rcp r%d" % sel, mul0="mul0: r3, %s, #1" % src,
                    read=read_plain(3)))
    return out, 3


def section_mulmap2():
    """What the multiplier reads from rows 0 and 2 for every normalization-family
    index, with the maximum magnitude at 2.0 and at 8.0."""
    out = []
    for sel in range(8, 16):
        for vec in ("X2", "X8", "pZ"):
            for name in ("r0", "r1", "r2"):
                out.append(variant(
                    "mulmap2-%d-%s-%s" % (sel, name, vec), vec,
                    sfu="rcp r%d" % sel,
                    mul0="mul0: r3, %s, #1" % name, read=read_plain(3)))
    return out, 3


def section_mulmap3():
    """Scale the multiplier's row-2 capture by 0.25 in the reading batch, so a
    value at or above 2.0 becomes a number the code table can resolve."""
    out = []
    for sel in (8, 10, 15):
        for vec in ("X2", "X8"):
            for name in ("r2", "r0"):
                for path in ("quarter", "quarter-neg"):
                    out.append(variant(
                        "mulmap3-%d-%s-%s-%s" % (sel, name, vec, path), vec,
                        sfu="rcp r%d" % sel,
                        mul0="mul0: r3, %s, #1" % name, read=READS[path](3)))
    return out, 3


def section_map():
    """Which register does each of the sixteen operand indices read, per opcode?
    Rows 0..15 hold sixteen distinct values, so the reciprocal and the reciprocal
    square root of each row are all different codes."""
    out = []
    for op in ("rcp", "rsq", "frc"):
        for sel in range(16):
            out.append(rows_variant("map-%s-%d" % (op, sel),
                                    "%s r%d" % (op, sel),
                                    "mul0: r3, sfu, #1", read_plain(3)))
    return out, 3


def section_sel(lo, hi):
    out = []
    for sel in range(lo, hi + 1):
        for axis in AXES4:
            out.append(capture_variant("sel%d-%s" % (sel, axis), axis,
                                       "rcp r%d" % sel))
    for axis in AXES4:
        out.append(capture_variant("sel-ctl-rcp1-%s" % axis, axis, "rcp r1"))
    return out, 3


def section_scale(sels):
    out = []
    for sel in sels:
        for vec in ("X2", "Y2", "Z2", "X4", "Y4", "Z4", "X8", "Y8", "Z8",
                    "nX4", "nY4", "nZ4", "nX8", "nY8", "nZ8"):
            out.append(capture_variant("sc%d-%s" % (sel, vec), vec,
                                       "rcp r%d" % sel))
        # row-2 alias and explicit sfu source with maxima in r0 and r2
        out.append(variant("sc%d-alias-r2-pX" % sel, "pX", sfu="rcp r%d" % sel,
                           mul0="mul0: r3, r2, #1", read=read_plain(3)))
        out.append(variant("sc%d-alias-r2-pZ" % sel, "pZ", sfu="rcp r%d" % sel,
                           mul0="mul0: r3, r2, #1", read=read_plain(3)))
        out.append(variant("sc%d-sfu-pX" % sel, "pX", sfu="rcp r%d" % sel,
                           mul0=CAPTURE, read=read_plain(3)))
        out.append(variant("sc%d-sfu-pZ" % sel, "pZ", sfu="rcp r%d" % sel,
                           mul0=CAPTURE, read=read_plain(3)))
        # ordinary reciprocal of the same magnitudes as a scale control
        for vec in ("X2", "X4", "X8"):
            out.append(capture_variant("sc%d-ctl-row0-%s" % (sel, vec), vec,
                                       "rcp r0"))
    return out, 3


# For each opcode: vectors and the ordinary-row control vector.
OPCODE_VECTORS = {
    "rsq": ("X2", "Y2", "Z2", "X4", "Y4", "Z4", "X8", "Y8", "Z8"),
    "sqrt": ("X2", "Y2", "Z2"),
    "lg2": ("X4", "Y4", "Z4"),
    "frc": ("fX", "fY", "fZ"),
}
OPCODE_CONTROL = {"rsq": "X2", "sqrt": "X2", "lg2": "X4", "frc": "fX"}


def section_ops(sels):
    out = []
    for op in ("rsq", "sqrt", "lg2", "frc"):
        vecs = OPCODE_VECTORS[op]
        for sel in sels:
            for vec in vecs:
                out.append(capture_variant("%s-%d-%s" % (op, sel, vec), vec,
                                           "%s r%d" % (op, sel)))
            ctl = OPCODE_CONTROL[op]
            out.append(capture_variant("%s-%d-row1-%s" % (op, sel, ctl), ctl,
                                       "%s r1" % op))
            out.append(capture_variant("%s-%d-row0-%s" % (op, sel, ctl), ctl,
                                       "%s r0" % op))
    return out, 3


SRC_MULS = {
    "row0": "mul0: r3, r0, #1",
    "row1": "mul0: r3, r1, #1",
    "row2": "mul0: r3, r2, #1",
    "row3": "mul0: r3, r3, #1",
    "sfu": CAPTURE,
    "one": "mul0: r3, #1, #1",
}


def section_src(sel):
    out = []
    for vec in ("pX", "pY", "pZ"):
        for name, mul in SRC_MULS.items():
            out.append(variant("src%s-%s-%s" % (sel, name, vec), vec,
                               sfu="rcp r%d" % sel, mul0=mul,
                               read=read_plain(3)))
    return out, 3


MATRIX_VECS = ("pX", "pY", "pZ", "mX", "mY", "mZ",
               "tXY", "tXZ", "tYZ", "tAll")


def section_row():
    out = []
    for vec in MATRIX_VECS:
        for row in range(0, 8):
            out.append(variant("row-%s-r%d-plain" % (vec, row), vec,
                               read=read_plain(row)))
            out.append(variant("row-%s-r%d-q" % (vec, row), vec,
                               read=read_quarter(row)))
            out.append(variant("row-%s-r%d-qn" % (vec, row), vec,
                               read=read_quarter_neg(row)))
    return out, 3


PATH_VECS = ("pX", "mX", "pZ", "mZ")
PATH_NAMES = ("plain", "abs", "neg", "quarter", "quarter-abs", "quarter-neg",
              "mulconst", "twostep", "twostep-abs", "rb", "rc")


def section_path():
    out = []
    for vec in PATH_VECS:
        for row in range(0, 4):
            for name in PATH_NAMES:
                out.append(variant("path-%s-r%d-%s" % (vec, row, name), vec,
                                   read=READS[name](row)))
    # calibration of every path on unmodified rows
    for vec in ("pX", "mX"):
        for row in range(0, 4):
            for name in PATH_NAMES:
                out.append(variant("cal-%s-r%d-%s" % (vec, row, name), vec,
                                   sfu="nop r0", read=READS[name](row)))
    return out, 3


DELAY_VECS = ("pX", "mX", "pZ", "mZ")


def section_delay():
    out = []
    for vec in DELAY_VECS:
        for row in (0, 2):
            for delay in (0, 1, 2):
                for late in (0, 1):
                    tag = "delay-%s-r%d-d%d-l%d" % (vec, row, delay, late)
                    out.append(variant(tag + "-plain", vec, delay=delay,
                                       late=late, read=read_plain(row)))
                    out.append(variant(tag + "-q", vec, delay=delay, late=late,
                                       read=read_quarter(row)))
            # shadow row control: the same delayed read of an untouched row
            for delay in (0, 1, 2):
                out.append(variant("delay-%s-r4-d%d" % (vec, delay), vec,
                                   delay=delay, read=read_quarter(4)))
    return out, 3


SHAPES = {
    "none": ("", ""),
    "mul0": ("mul0: r3, sfu, #1", ""),
    "mul1": ("", "mul1: r3, sfu, #1"),
    "mul0-r1": ("mul0: r1, sfu, #1", ""),
    "mul1-r0": ("", "mul1: r0, sfu, #1"),
    "compiler": ("mul0: r0, r2, r0", "mul1: r1, r2, r1"),
}


def section_shape():
    out = []
    for vec in MATRIX_VECS:
        for name, (mul0, mul1) in SHAPES.items():
            tag = "shape-%s-%s" % (name, vec)
            for row in range(0, 4):
                out.append(variant(tag + "-q%d" % row, vec, sfu="rcp r10",
                                   mul0=mul0, mul1=mul1,
                                   read=read_quarter(row)))
    return out, 3


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    section = sys.argv[2]
    if section == "codes":
        variants, repeat = section_codes()
        name = "s56-codes"
    elif section == "pilot":
        variants, repeat = section_pilot()
        name = "s56-pilot2"
    elif section == "sel":
        lo, hi = int(sys.argv[3]), int(sys.argv[4])
        variants, repeat = section_sel(lo, hi)
        name = "s56-sel-%d-%d" % (lo, hi)
    elif section == "mulmap3":
        variants, repeat = section_mulmap3()
        name = "s56-mulmap3"
    elif section == "mulmap2":
        variants, repeat = section_mulmap2()
        name = "s56-mulmap2"
    elif section == "mulmap":
        variants, repeat = section_mulmap()
        name = "s56-mulmap"
    elif section == "idx":
        variants, repeat = section_idx()
        name = "s56-idx"
    elif section == "map":
        variants, repeat = section_map()
        name = "s56-map"
    elif section == "rows":
        variants, repeat = section_rows()
        name = "s56-rows"
    elif section == "sign3":
        variants, repeat = section_sign3()
        name = "s56-sign3"
    elif section == "sign2":
        variants, repeat = section_sign2()
        name = "s56-sign2"
    elif section == "sweep":
        variants, repeat = section_sweep()
        name = "s56-sweep"
    elif section == "reread":
        variants, repeat = section_reread()
        name = "s56-reread"
    elif section == "diag2":
        variants, repeat = section_diag2()
        name = "s56-diag2"
    elif section == "diag":
        variants, repeat = section_diag()
        name = "s56-diag"
    elif section == "each":
        variants, repeat = section_each()
        name = "s56-each"
    elif section == "scale":
        sels = [int(x) for x in sys.argv[3:]] or [10]
        variants, repeat = section_scale(sels)
        name = "s56-scale-" + "-".join(str(s) for s in sels)
    elif section == "ops":
        sels = [int(x) for x in sys.argv[3:]] or [10]
        variants, repeat = section_ops(sels)
        name = "s56-ops-" + "-".join(str(s) for s in sels)
    elif section == "src":
        sel = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        variants, repeat = section_src(sel)
        name = "s56-src-%d" % sel
    elif section == "row":
        variants, repeat = section_row()
        name = "s56-row"
    elif section == "path":
        variants, repeat = section_path()
        name = "s56-path"
    elif section == "delay":
        variants, repeat = section_delay()
        name = "s56-delay"
    elif section == "shape":
        variants, repeat = section_shape()
        name = "s56-shape"
    else:
        sys.exit("unknown section %r" % section)

    manifest = {
        "name": name,
        "repeat": repeat,
        "timeout": 20,
        "program": {
            "vs": "tests/grate/asm/vs_mov.txt",
            "fs": {"codes": FS_CODES, "rows": FS_ROWS,
                   "map": FS_ROWS, "idx": FS_IDX}.get(section, FS),
            "lnk": "tests/grate/asm/linker_vs_tests.txt",
        },
        "controls": CONTROLS,
        "variants": variants,
    }
    with open(sys.argv[1], "w") as fp:
        json.dump(manifest, fp, indent=2)
        fp.write("\n")
    print("%s: %d variants, %d runs" % (name, len(variants),
                                        len(variants) * repeat))


if __name__ == "__main__":
    main()
