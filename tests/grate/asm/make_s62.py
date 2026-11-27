#!/usr/bin/env python3
"""Generate the note 62 manifests for tests/grate/asm/mfu_s59_read.in.

Usage: make_s62.py <out.json> <section>

Sections:
  p1   priority 1.  A gain pair (the same sfu cell captured at 2^4 and 2^6, so a
       wobble can be attributed to the operand rather than to the capture map),
       then the three distinguishing vertex configurations at the fixed sample:
         `p1a` keeps V fixed (a constant varying) and moves D with a per-vertex
              clip w on the blue vertex;
         `p1b` keeps D and V fixed and redistributes the three vertex values so
              the terms T_i change while their weighted sum does not;
         `p1c` keeps V fixed and moves the clip w onto the green vertex instead,
              which changes both D and the W assignment.
       Every configuration also has a selector-4 cell (`rcp r4`, 2^12 capture),
       so D is measured in a variant of its own instead of being taken from the
       weight table.
  zw   priority 2.  A link that exchanges the z and w tram components
       (`tram0.xywz`) with four distinct significands, rows read without an sfu,
       selectors 6 and 7 run separately, and ALU-seeded controls for both values.
  zwp  the same cells through the unswizzled link (`tram0.xyzw`), so the pair
       differs only in which row slot holds z and which holds w.
  t4   priority 3.  Does the extra input travel with the value, the row, or the
       fetch?  At w = 4 (where the fetch path differs from the ALU path) a copy
       of row 1 into row 3 is read by selector 7, a second interpolator word
       overwrites the rows before the sfu word, and both values are also seeded
       by the ALU for comparison.

The selector 5..7 capture lane is c[4]; at 2^4 the transfer function gives

    |v| < 2    code = 151 - 4*log2(m)
    |v| >= 2   code = 215 - 4*log2(m)

and at 2^6 both are 16 codes lower, so one code step is 1/8 octave and the
gain pair must differ by exactly 16 codes if the store map is clean.

The selector-4 cell captures `rcp r4` at 2^12: selector 4 is 2^16 D, `rcp` gives
2^-16/D, so the code is 215 - 8*log2(D) and D is measured without any weight.

Predicted values use note 60's fitted weights at q1 = (0.75, 0.45),
lambda = (blue 0.70, green 0.13, red 0.15), sum 0.98:

    D = sum lambda_i / W_i     N = sum lambda_i A_i / W_i     V = N / D
    T_i = lambda_i A_i / W_i

`p1b`'s three sets are chosen so that N stays within 3% of 0.98 while the
individual terms move by 2x and to zero, and `p1c` moves the clip w from the
blue vertex to the green one.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_s59 as s59  # noqa: E402
import make_s61 as s61  # noqa: E402

FS = s61.FS
VS_PERSP = s61.VS_PERSP
VS_WGREEN = "tests/grate/asm/vs_persp_wgreen.txt"
VS_WGREEN2 = "tests/grate/asm/vs_persp_wgreen2.txt"
VS_WNUM = s61.VS_WNUM
VS_QUAD = s61.VS_QUAD
VS_SCALAR_U = s61.VS_SCALAR_U
LNK_PLAIN = s61.LNK_PLAIN
LNK_ZW = "tests/grate/asm/linker_vs_s62_zw.txt"
LNK_DEF = s59.s57.LNK

ROW_A = s61.ROW_A
K16 = s61.K16                  # 2^4, the note 61 selector capture
K64 = "64.0"                   # 2^6, the gain-pair half
KD = "4096.0"                  # 2^12, the selector-4 capture
Q1 = s61.Q1
tag = s61.tag

# four different significands, two different store branches
DSC = (1.0, 1.5, 3.0, 0.5625)


# --- cell shapes -----------------------------------------------------------

def sel_cell(name, sel, vs, args, scale=K16, op="rsq"):
    return s61.ipl_cell(name, sel, vs, args, scale=scale, op=op)


def row_cell(name, vs, args):
    """Row 1 in lane 1 and row 0 in lane 2, no sfu and no multiplier."""
    return s61.rows_cell(name, 1, 0, vs, args)


def d_cell(name, vs, args):
    """Selector 4: `rcp r4` at 2^12 reads code = 215 - 8*log2(D)."""
    return s61.ipl_cell(name, 4, vs, args, scale=KD, op="rcp")


def trio(prefix, vs, args):
    return [sel_cell("%s-s5" % prefix, 5, vs, args),
            sel_cell("%s-s6" % prefix, 6, vs, args),
            row_cell("%s-row1" % prefix, vs, args),
            d_cell("%s-D" % prefix, vs, args)]


# --- 1. priority 1: the hidden quantity ------------------------------------

def section_p1():
    out = []

    # (0) the gain pair: one cell, two capture scales.  A clean store map moves
    #     the code by exactly -16 for one octave of gain, in every arm.
    for w in (1.0, 2.0, 4.0, 8.0):
        args = s61.uniform_args(w, 1.0, 1.0, 1.0)
        out.append(sel_cell("g-ua-w%s-x16" % tag(w), 5, VS_SCALAR_U, args))
        out.append(sel_cell("g-ua-w%s-x64" % tag(w), 5, VS_SCALAR_U, args,
                            scale=K64))
    for w in (1.0, 4.0):
        args = s61.uniform_args(w, 1.0, 1.0, 1.0)
        out.append(s61.seed_cell("g-seed-w%s-x16" % tag(w), 5, 1.0,
                                 VS_SCALAR_U, args))
        out.append(s61.seed_cell("g-seed-w%s-x64" % tag(w), 5, 1.0,
                                 VS_SCALAR_U, args, scale=K64))

    # (a) same V, different D: a constant varying, w on the blue vertex
    for w in (1.0, 2.0, 4.0, 8.0):
        out += trio("p1a-w%s" % tag(w), VS_PERSP,
                    s61.scalar_args(Q1[0], Q1[1], w, 1.0, 1.0, 1.0))

    # (b) same D and V, different terms: w = 1, three redistributions whose
    #     weighted sums are 0.98, 0.97 and 1.00
    for name, ar, ag, ab in (("s1", 1.0, 1.0, 1.0),
                             ("s2", 0.5, 1.5, 1.0),
                             ("s3", 2.0, 0.0, 1.0)):
        out += trio("p1b-%s" % name, VS_PERSP,
                    s61.scalar_args(Q1[0], Q1[1], 1.0, ar, ag, ab))

    # (c) same V, different W assignment: w on the blue vertex against w on the
    #     green vertex, constant varying in both
    for w in (2.0, 4.0):
        out += trio("p1c-blue-w%s" % tag(w), VS_PERSP,
                    s61.scalar_args(Q1[0], Q1[1], w, 1.0, 1.0, 1.0))
        out += trio("p1c-green-w%s" % tag(w), VS_WGREEN,
                    s61.scalar_args(Q1[0], Q1[1], w, 1.0, 1.0, 1.0))
    return out, 2


# --- 1b. the same V under two different W assignments ---------------------

def section_w():
    """A constant varying of 1.0, so V = 1.0 for every clip w and both shaders.

    Only the vertex that carries the clip w changes: blue (D = 0.28 + 0.70/w)
    against green (D = 0.15 + 0.70 + 0.13/w).  Both give V = 1.0 exactly, so a
    deviation that tracks the denominator size and one that tracks *which*
    vertex carries w predict different code pairs here.
    """
    out = []
    for w in (2.0, 4.0, 8.0):
        out += trio("w-blue-w%s" % tag(w), VS_PERSP,
                    s61.scalar_args(Q1[0], Q1[1], w, 1.0, 1.0, 1.0))
        out += trio("w-green-w%s" % tag(w), VS_WGREEN2,
                    s61.scalar_args(Q1[0], Q1[1], w, 1.0, 1.0, 1.0))
    return out, 2


# --- 2. priority 2: the z/w selector-index test ----------------------------

def zw_cells(prefix):
    args = s61.quad_args(Q1[0], Q1[1], 1.0, DSC, DSC, DSC)
    out = [
        s61.rows_cell("%s-rows01" % prefix, 0, 1, VS_QUAD, args),
        s61.rows_cell("%s-rows23" % prefix, 2, 3, VS_QUAD, args),
        sel_cell("%s-s5" % prefix, 5, VS_QUAD, args),
        sel_cell("%s-s6" % prefix, 6, VS_QUAD, args),
        sel_cell("%s-s7" % prefix, 7, VS_QUAD, args),
    ]
    # ALU-seeded controls for both values in both selectors
    for sel in (6, 7):
        for label, value in (("z", 3.0), ("w", 0.5625)):
            out.append(s61.seed_cell("%s-seed-s%d-%s" % (prefix, sel, label),
                                     sel, value, VS_QUAD, args))
    return out


def section_zw():
    return zw_cells("zw"), 2


def section_zwp():
    return zw_cells("zwp"), 2


# --- 3. priority 3: does the extra input travel with value, row or fetch? --

def section_t4():
    """w = 4, where the fetch path and the ALU path disagree by ~4 codes."""
    args = s61.quad_args(Q1[0], Q1[1], 4.0, DSC, DSC, DSC)
    out = [
        row_cell("t4-row1", VS_QUAD, args),
        sel_cell("t4-s5", 5, VS_QUAD, args),
        sel_cell("t4-s7", 7, VS_QUAD, args),
        # copy row 1 (1.0) into row 3 (0.5625) between fetch and sfu
        s61.ipl_cell_pre("t4-cp13-s7", 7, VS_QUAD, args, s61.alu_copy(1, 3)),
        s61.rows_cell_pre("t4-cp13-row3", 3, 1, VS_QUAD, args,
                          s61.alu_copy(1, 3)),
        # a second interpolator word overwrites every row before the sfu word
        s61.ipl_cell_pre("t4-ipl2-s5", 5, VS_QUAD, args, s59.IPL_WORD),
        s61.ipl_cell_pre("t4-ipl2-s7", 7, VS_QUAD, args, s59.IPL_WORD),
        # the same two values seeded by the ALU, no interpolator at all
        s61.seed_cell("t4-seed-a-s5", 5, 1.0, VS_QUAD, args),
        s61.seed_cell("t4-seed-b-s7", 7, 0.5625, VS_QUAD, args),
    ]
    return out, 3


def section_lk():
    """The transfer function measured at three clip w with ALU-seeded rows.

    The note 58 map was calibrated with rows the ALU wrote at `w = 1`.  Seeding
    the same ladder at `w = 2` and `w = 4` asks whether the map itself depends on
    the fragment's clip w.  There is no interpolator in these cells at all, so a
    movement here is an input-path effect and nothing else.
    """
    out = []
    for w in (1.0, 2.0, 4.0):
        args = s61.uniform_args(w, 1.0, 1.0, 1.0)
        for v in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0):
            out.append(s61.seed_cell("lk-w%s-v%s" % (tag(w), tag(v)), 5, v,
                                     VS_SCALAR_U, args))
    return out, 2


def section_bn():
    """V against N where they are 64 codes apart.

    `vs_persp_wnum.txt` makes the vertex value proportional to the clip w, so
    N = C*sum(lambda) is constant and V = N/D moves by 2.4x over w = 1..8.  The
    two candidates are then separated by the store branch: N stays below 2 and
    cannot leave the low branch, while V crosses 2 between w = 1 and w = 2.  The
    constant-varying arms of this note differ by only ~4 codes between V and N,
    which is the same size as the fetch-path wobble, so this is the arm that
    decides between them.
    """
    out = []
    for w in (1.0, 2.0, 4.0, 8.0):
        out += trio("bn-w%s" % tag(w), VS_WNUM,
                    s61.wnum_args(Q1[0], Q1[1], w, 1.9))
    for w in (1.0, 4.0):
        out += trio("bn0-w%s" % tag(w), VS_WNUM,
                    s61.wnum_args(Q1[0], Q1[1], w, 0.5))
    return out, 2


def section_dup():
    """One cell, three names, in one manifest, plus the same cell last.

    `cp-seed-c-s5` and `q-dsc-v3-w4` are byte-identical variant entries but read
    219 and 212 in their own manifests, each reproducibly.  If the three copies
    here agree, the difference is a position/order effect; if they disagree, the
    capture is not deterministic across draws.
    """
    args = s61.quad_args(Q1[0], Q1[1], 4.0, DSC, DSC, DSC)
    out = [s61.seed_cell("dup-a", 5, 3.0, VS_QUAD, args),
           s61.seed_cell("dup-b", 5, 3.0, VS_QUAD, args),
           s61.seed_cell("dup-c", 5, 3.0, VS_QUAD, args)]
    out.append(s61.seed_cell("dup-z-last", 5, 3.0, VS_QUAD, args))
    return out, 3


def section_q():
    """The same ALU seed in two programs, at w = 1 and w = 4.

    The scalar program's seeded ladder is byte-identical at w = 1, 2 and 4
    (`lk`), but the quad program seeds 3.0 to 212 at w = 1 and 219 at w = 4.
    These six cells separate the two remaining differences: the varying's four
    component values, and the clip w.  `quad1` uses the quad shader with all
    four components equal to 1.0, which is the scalar program's varying.
    """
    dsc = s61.quad_args(Q1[0], Q1[1], 4.0, DSC, DSC, DSC)
    dsc1 = s61.quad_args(Q1[0], Q1[1], 1.0, DSC, DSC, DSC)
    one = (1.0, 1.0, 1.0, 1.0)
    q4 = s61.quad_args(Q1[0], Q1[1], 4.0, one, one, one)
    q1 = s61.quad_args(Q1[0], Q1[1], 1.0, one, one, one)
    return [
        s61.seed_cell("q-dsc-v3-w4", 5, 3.0, VS_QUAD, dsc),
        s61.seed_cell("q-dsc-v3-w1", 5, 3.0, VS_QUAD, dsc1),
        s61.seed_cell("q-one-v3-w4", 5, 3.0, VS_QUAD, q4),
        s61.seed_cell("q-one-v3-w1", 5, 3.0, VS_QUAD, q1),
        s61.seed_cell("q-one-v1-w4", 5, 1.0, VS_QUAD, q4),
        s61.seed_cell("q-dsc-v1-w4", 5, 1.0, VS_QUAD, dsc),
    ], 3


def section_cp():
    """w = 4 with two rows that are 61 codes apart, so a copy can be seen.

    Row 1 holds 1.0 (code 151 seeded) and row 2 holds 3.0 (code 212 seeded, the
    other store branch).  Copying row 2 into row 1 and reading selector 5 asks
    whether a value that arrived through the interpolator keeps the fetch-path
    deviation when it is moved to another row by the ALU.
    """
    args = s61.quad_args(Q1[0], Q1[1], 4.0, DSC, DSC, DSC)
    return [
        s61.rows_cell("cp-rows01", 0, 1, VS_QUAD, args),
        s61.rows_cell("cp-rows23", 2, 3, VS_QUAD, args),
        sel_cell("cp-s5", 5, VS_QUAD, args),
        sel_cell("cp-s6", 6, VS_QUAD, args),
        s61.ipl_cell_pre("cp-cp21-s5", 5, VS_QUAD, args, s61.alu_copy(2, 1)),
        s61.rows_cell_pre("cp-cp21-row1", 1, 2, VS_QUAD, args,
                          s61.alu_copy(2, 1)),
        s61.ipl_cell_pre("cp-ipl2-s6", 6, VS_QUAD, args, s59.IPL_WORD),
        s61.seed_cell("cp-seed-a-s5", 5, 1.0, VS_QUAD, args),
        s61.seed_cell("cp-seed-c-s5", 5, 3.0, VS_QUAD, args),
    ], 3


# --- manifest --------------------------------------------------------------

SECTIONS = {
    "p1": (section_p1, "s62-p1", LNK_DEF),
    "zw": (section_zw, "s62-zw", LNK_ZW),
    "zwp": (section_zwp, "s62-zwp", LNK_PLAIN),
    "t4": (section_t4, "s62-t4", LNK_DEF),
    "w": (section_w, "s62-w", LNK_DEF),
    "cp": (section_cp, "s62-cp", LNK_DEF),
    "lk": (section_lk, "s62-lk", LNK_DEF),
    "q": (section_q, "s62-q", LNK_DEF),
    "dup": (section_dup, "s62-dup", LNK_DEF),
    "bn": (section_bn, "s62-bn", LNK_DEF),
}


def plain_controls(expected_pipeline):
    out = []
    for control in s59.s57.CONTROLS:
        control = dict(control)
        if control["name"] == "varying-pipeline":
            control["expected"] = expected_pipeline
        out.append(control)
    return out


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    out_path, section = sys.argv[1], sys.argv[2]
    if section not in SECTIONS:
        sys.exit("unknown section %r" % section)
    make, name, lnk = SECTIONS[section]
    variants, repeat = make()
    # The varying-pipeline control reads the tram back, so a link that moves
    # components between tram slots changes what it reads.  Both expectations
    # below are measured values, not assumptions: 0xFF0100FF for the
    # unswizzled link (note 61) and 0x01FF00FF for tram0.xywz (a one-byte
    # rotation of the default link's word).
    if lnk == LNK_PLAIN:
        controls = plain_controls("0xFF0100FF")
    elif lnk == LNK_ZW:
        controls = plain_controls("0x01FF00FF")
    else:
        controls = s59.s57.CONTROLS
    manifest = {
        "name": name,
        "repeat": repeat,
        "timeout": 20,
        "program": {"vs": s59.s57.VS, "fs": FS, "lnk": lnk},
        "controls": controls,
        "variants": variants,
    }
    with open(out_path, "w") as fp:
        json.dump(manifest, fp, indent=2)
        fp.write("\n")
    print("%s: %d variants, %d runs" % (name, len(variants),
                                        len(variants) * repeat))


if __name__ == "__main__":
    main()
