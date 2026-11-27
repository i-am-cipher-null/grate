#!/usr/bin/env python3
"""Generate the note 61 manifests for tests/grate/asm/mfu_s59_read.in.

Usage: make_s61.py <out.json> <section>

Sections:
  num    priority 1: the three discriminating varying configurations.
         `a` is a constant vertex value, so N = C*D moves with clip w and
         V = C does not; `b` is a vertex value proportional to clip w, so
         N = C*sum(lambda) is constant and V = N/D moves the other way; `ub`
         is the same two-property pair with a *uniform* clip w and the vertex
         value scaled with it, which needs no fitted weight at all (the w
         cancels inside every term of N); `lad` holds the significand and
         steps the magnitude by octaves, which separates a significand rule
         from an exponent-parity one.
  vtx    priority 1, configuration C: one vertex of the varying at a time, so
         the individual terms T_i = lambda_i A_i / W_i and their corrected
         values T_i / D are separable.
  comp   priority 2: four independent varying components in one program, one
         component changed at a time, selectors 5..7 plus the four row
         readbacks.
  swz    priority 2: the same component values through a link that does not
         swap tram0 x/y, so the row slots and the TRAM components holding them
         can be separated.
  bnd    priority 3: interpolator fetch then sfu, fetch then an ALU identity
         rewrite then sfu, fetch then a copy into a different row then sfu on
         that row, and the ALU-seeded equivalent.

The capture scale of every selector 5..7 cell is 2^4.  At that scale the two
branches of the transfer function (note 58.1: sign*v with the exponent replaced,
K = 32 below |v| = 2 and 16 at or above it) are 64 store codes apart and both
land inside the store map:

    |v| < 2     code = 151 - 4 * log2(m)
    |v| >= 2    code = 215 - 4 * log2(m)

with m the significand, 1 <= m < 2, so one reading gives the branch bit and the
significand separately.  Note 60's cells used 2^12 on some arms, which is 64
codes higher; the `num` arms re-anchor that at 2^4.

Predicted values use note 60's fitted screen-space weights at q1 = (0.75, 0.45),
lambda = (blue 0.70, green 0.13, red 0.15), sum 0.98, so

    D(w) = 0.98 - 0.70 + 0.70 / w

is the perspective denominator for a clip w on the blue vertex.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_s59 as s59  # noqa: E402

FS = s59.FS
VS_PERSP = s59.VS_PERSP
VS_WNUM = "tests/grate/asm/vs_persp_wnum.txt"
VS_QUAD = "tests/grate/asm/vs_persp_quad.txt"
VS_SCALAR_U = "tests/grate/asm/vs_scalar_u.txt"
LNK_PLAIN = "tests/grate/asm/linker_vs_s61_plain.txt"

ROW_A = s59.ROW_A          # 2^-15, the row readback lane
K16 = "16.0"               # 2^4, the selector 5..7 capture lane

Q1 = (0.75, 0.45)

LAM = {"blue": 0.70, "green": 0.13, "red": 0.15}
LAM_SUM = sum(LAM.values())


def tag(v):
    return ("%g" % v).replace(".", "p").replace("-", "n")


def d_of(w):
    return LAM["green"] + LAM["red"] + LAM["blue"] / w


# --- uniform plumbing -----------------------------------------------------

def wnum_args(x, y, w, c):
    """Config B: A_i = c * W_i through the two folded uniforms."""
    return [
        "--vs_uniform", '["offset"]=(%s,%s,0.0,0.0)' % (round(-(1.0 + x), 6),
                                                       round(-(1.0 + y), 6)),
        "--vs_uniform", '["wdelta"]=(%s,0.0,0.0,0.0)' % (w - 1),
        "--vs_uniform", '["cwd"]=(%s,0.0,0.0,0.0)' % (c * (w - 1)),
        "--vs_uniform", '["cbase"]=(%s,0.0,0.0,0.0)' % c,
    ]


def quad_args(x, y, w, cred, cgreen, cblue):
    args = [
        "--vs_uniform", '["offset"]=(%s,%s,0.0,0.0)' % (round(-(1.0 + x), 6),
                                                       round(-(1.0 + y), 6)),
        "--vs_uniform", '["wdelta"]=(%s,0.0,0.0,0.0)' % (w - 1),
    ]
    for name, value in (("cred", cred), ("cgreen", cgreen), ("cblue", cblue)):
        args += ["--vs_uniform", '["%s"]=(%.10g,%.10g,%.10g,%.10g)' % (
            name, value[0], value[1], value[2], value[3])]
    return args


def scalar_args(x, y, w, ar, ag, ab):
    return s59.persp_args(x, y, w, ar, ag, ab)


def uniform_args(w, ar, ag, ab):
    """Uniform clip w: the projection does not move, D = 1/w exactly."""
    return [
        "--vs_uniform", '["clip_scale"]=(%s,%s,%s,%s)' % (w, w, w, w),
        "--vs_uniform", '["ar"]=(%s,0.0,0.0,0.0)' % ar,
        "--vs_uniform", '["ag"]=(%s,0.0,0.0,0.0)' % ag,
        "--vs_uniform", '["ab"]=(%s,0.0,0.0,0.0)' % ab,
    ]


# --- cell shapes ----------------------------------------------------------

def ipl_cell(name, sel, vs, args, scale=K16, op="rsq"):
    """The interpolator fills the rows in a preceding word, the sfu word reads
    one of them.  Lane 1 reads row 1 back through the ALU, lane 2 is the
    capture through the explicit `sfu` multiplier token."""
    read = ("MAD r0.lh, r1, u9, #0", "MAD r1.lh, r3, u4, #0")
    c = ("1.0", "1.0", "1.0", "1.0", scale)
    return s59.variant(name, vec=("1.0", "1.0", "1.0", "1.0"),
                       sfu="%s r%d" % (op, sel), pre=s59.IPL_WORD,
                       mul0="mul0: r3, sfu, #1", read=read, c=c, u9=ROW_A,
                       seed=False, vs=vs, args=args)


def ipl_cell_pre(name, sel, vs, args, extra, scale=K16, op="rsq"):
    """As ipl_cell, with an extra ALU batch between the fetch and the sfu word."""
    read = ("MAD r0.lh, r1, u9, #0", "MAD r1.lh, r3, u4, #0")
    c = ("1.0", "1.0", "1.0", "1.0", scale)
    return s59.variant(name, vec=("1.0", "1.0", "1.0", "1.0"),
                       sfu="%s r%d" % (op, sel), pre=s59.IPL_WORD + extra,
                       mul0="mul0: r3, sfu, #1", read=read, c=c, u9=ROW_A,
                       seed=False, vs=vs, args=args)


def seed_cell(name, sel, value, vs, args, scale=K16, op="rsq"):
    """No interpolator: the ALU seeds row sel-4 with `value`."""
    read = ("MAD r0.lh, r1, u9, #0", "MAD r1.lh, r3, u4, #0")
    c = ("1.0", "1.0", "1.0", "1.0", scale)
    vec = ["1.0", "1.0", "1.0", "1.0"]
    text = "%.10g" % value
    if "." not in text and "e" not in text:
        text += ".0"          # the constant parser wants a decimal point
    vec[sel - 4] = text
    return s59.variant(name, vec=tuple(vec), sfu="%s r%d" % (op, sel),
                       mul0="mul0: r3, sfu, #1", read=read, c=c, u9=ROW_A,
                       seed=True, vs=vs, args=args)


def rows_cell(name, ra, rb, vs, args):
    """Two rows read through the ALU after the interpolator fills them: no sfu,
    no multiplier, so the row claim does not depend on the capture path."""
    read = ("MAD r0.lh, r%d, u9, #0" % ra, "MAD r1.lh, r%d, u9, #0" % rb)
    c = ("1.0", "1.0", "1.0", "1.0", "1.0")
    return s59.variant(name, vec=("1.0", "1.0", "1.0", "1.0"), sfu="nop r0",
                       pre=s59.IPL_WORD, read=read, c=c, u9=ROW_A, seed=False,
                       vs=vs, args=args)


def rows_cell_pre(name, ra, rb, vs, args, extra):
    read = ("MAD r0.lh, r%d, u9, #0" % ra, "MAD r1.lh, r%d, u9, #0" % rb)
    c = ("1.0", "1.0", "1.0", "1.0", "1.0")
    return s59.variant(name, vec=("1.0", "1.0", "1.0", "1.0"), sfu="nop r0",
                       pre=s59.IPL_WORD + extra, read=read, c=c, u9=ROW_A,
                       seed=False, vs=vs, args=args)


def alu_copy(src, dst):
    return ("EXEC\n\tALU:\n\t\tALU0:\tMAD r%d.lh, r%d, u0, #0\n"
            "\t\tALU1:\tNOP\n\t\tALU2:\tNOP\n\t\tALU3:\tNOP\n;\n"
            % (dst, src))


# --- 1. priority 1: numerator against corrected varying -------------------

def section_num():
    out = []
    # (a) per-vertex clip w: A moves with D, V does not
    for c in (0.5, 1.9):
        for w in (1.0, 2.0, 4.0, 8.0):
            out.append(ipl_cell("num-a%s-w%s" % (tag(c), tag(w)), 5, VS_PERSP,
                                scalar_args(Q1[0], Q1[1], w, c, c, c)))
    # (b) A_i proportional to W_i: N is constant, V moves
    for c in (0.5, 1.9):
        for w in (1.0, 2.0, 4.0, 8.0):
            out.append(ipl_cell("num-b%s-w%s" % (tag(c), tag(w)), 5, VS_WNUM,
                                wnum_args(Q1[0], Q1[1], w, c)))
    # (c) uniform clip w, which makes D = 1/w exact and removes every weight
    #     from the prediction: `ua` keeps V constant, `ub` scales the vertex
    #     value with w so N is constant and V grows as w
    for w in (1.0, 2.0, 4.0, 8.0):
        out.append(ipl_cell("num-ua-w%s" % tag(w), 5, VS_SCALAR_U,
                            uniform_args(w, 0.5, 0.5, 0.5)))
    for w in (1.0, 2.0, 4.0, 8.0):
        out.append(ipl_cell("num-ub-w%s" % tag(w), 5, VS_SCALAR_U,
                            uniform_args(w, 0.5 * w, 0.5 * w, 0.5 * w)))
    # (d) the same significand one octave apart: a significand rule predicts
    #     four identical codes, an exponent rule does not
    for w in (1.0, 4.0):
        for c in (0.25, 0.5, 1.0, 2.0):
            out.append(ipl_cell("num-lad-w%s-c%s" % (tag(w), tag(c)), 5,
                                VS_PERSP,
                                scalar_args(Q1[0], Q1[1], w, c, c, c)))
    # repeats
    out.append(ipl_cell("num-a1p9-w4-again", 5, VS_PERSP,
                        scalar_args(Q1[0], Q1[1], 4.0, 1.9, 1.9, 1.9)))
    out.append(ipl_cell("num-b1p9-w4-again", 5, VS_WNUM,
                        wnum_args(Q1[0], Q1[1], 4.0, 1.9)))
    # (e) ALU-seeded fingerprints of every candidate at w = 4, so the sfu
    #     result is compared with a *measured* transfer function
    d4 = d_of(4.0)
    for label, value in (("V-b1p9", 1.9 * LAM_SUM / d4),
                         ("N-b1p9", 1.9 * LAM_SUM),
                         ("V-a1p9", 1.9),
                         ("N-a1p9", 1.9 * d4),
                         ("D", d4),
                         ("V-a0p5", 0.5),
                         ("N-a0p5", 0.5 * d4)):
        out.append(seed_cell("num-seed-%s-w4" % label, 5, value, VS_PERSP,
                             scalar_args(Q1[0], Q1[1], 4.0, 1.0, 1.0, 1.0)))
    return out, 2


# --- 2. priority 1, configuration C ---------------------------------------

def section_vtx():
    out = []
    for label, ar, ag, ab in (("r", 1.0, 0.0, 0.0), ("g", 0.0, 1.0, 0.0),
                              ("b", 0.0, 0.0, 1.0), ("all", 1.0, 1.0, 1.0)):
        for w in (1.0, 4.0):
            out.append(ipl_cell("vtx-%s-w%s" % (label, tag(w)), 5, VS_PERSP,
                                scalar_args(Q1[0], Q1[1], w, ar, ag, ab)))
    # the individual terms and their corrected values at w = 4
    d4 = d_of(4.0)
    for label, value in (("T-r", LAM["red"]), ("T-g", LAM["green"]),
                         ("T-b", LAM["blue"] / 4.0),
                         ("V-r", LAM["red"] / d4),
                         ("V-g", LAM["green"] / d4),
                         ("V-b", LAM["blue"] / 4.0 / d4),
                         ("sum-V", 1.0)):
        out.append(seed_cell("vtx-seed-%s-w4" % label, 5, value, VS_PERSP,
                             scalar_args(Q1[0], Q1[1], 4.0, 1.0, 1.0, 1.0)))
    out.append(ipl_cell("vtx-r-w4-again", 5, VS_PERSP,
                        scalar_args(Q1[0], Q1[1], 4.0, 1.0, 0.0, 0.0)))
    return out, 2


# --- 3. priority 2: which component does a selector follow ----------------

COMP_BASE = (0.25, 0.5, 1.0, 1.5)
COMP_ALT_X = (3.0, 0.5, 1.0, 1.5)
COMP_ALT_W = (0.25, 0.5, 1.0, 3.0)
# One significand signature per component, chosen so the z and w slots sit on
# opposite sides of the |v| = 2 branch: the interpolator word fills the rows in
# its own order, and a selector that follows a row index rather than a TRAM
# component must then move when the link permutes the slots.
COMP_SIG = (0.3, 0.6, 3.0, 0.25)
COMP_SIG_P = (0.6, 0.3, 3.0, 0.25)
# `sig` leaves x and y with one significand, so it cannot separate a selector
# that follows a row slot from one that follows a TRAM component.  These four
# values instead have four different significands and two different store
# branches: x 1.0 -> 151, y 1.5 -> 148, z 3.0 -> 212, w 0.5625 -> 150.  The two
# links put x and y in opposite row slots, so s5 alone decides.
COMP_DSC = (1.0, 1.5, 3.0, 0.5625)


def comp_cells(prefix, values, w, link=None):
    args = quad_args(Q1[0], Q1[1], w, values, values, values)
    out = [
        rows_cell("%s-rows01-w%s" % (prefix, tag(w)), 0, 1, VS_QUAD, args),
        rows_cell("%s-rows23-w%s" % (prefix, tag(w)), 2, 3, VS_QUAD, args),
    ]
    for sel in (5, 6, 7):
        out.append(ipl_cell("%s-w%s-s%d" % (prefix, tag(w), sel), sel, VS_QUAD,
                            args))
    return out


def section_comp():
    out = []
    out += comp_cells("comp-base", COMP_BASE, 1.0)
    out += comp_cells("comp-x", COMP_ALT_X, 1.0)
    out += comp_cells("comp-w", COMP_ALT_W, 1.0)
    out += comp_cells("comp-base", COMP_BASE, 4.0)
    out += comp_cells("comp-sig", COMP_SIG, 1.0)
    out += comp_cells("comp-dsc", COMP_DSC, 1.0)
    # a repeat of the base arm
    out.append(ipl_cell("comp-base-w1-s5-again", 5, VS_QUAD,
                        quad_args(Q1[0], Q1[1], 1.0, COMP_BASE, COMP_BASE,
                                  COMP_BASE)))
    return out, 2


def section_swz():
    """The plain link: the TRAM components and the row slots no longer match.

    `comp-sig` runs the same values through the default link, so the two arms
    differ only in the link.  The values put z above 2 and w at 0.25, and the
    two fill orders the links are known to produce place z and w in different
    rows, so a selector that follows a row index moves by about 60 codes while
    one that follows a TRAM component does not.  `swz-sigp` swaps x and y as a
    second order check.
    """
    out = []
    out += comp_cells("swz-sig", COMP_SIG, 1.0)
    out += comp_cells("swz-sigp", COMP_SIG_P, 1.0)
    out += comp_cells("swz-dsc", COMP_DSC, 1.0)
    return out, 2


# --- 5. priority 3: does the sfu read the row at fetch or later -----------

def section_p3():
    """Four *different* row values, then a rewrite between fetch and the sfu.

    The `bnd` copy arm held one scalar in every row, so copying a row could not
    move the sfu result.  Here the rows hold 1.0, 1.5, 3.0 and 0.5625 (codes
    151, 148, 212, 150), so a selector that reads the row that the sfu word
    names must follow the copy, and one that latched the fetched word must not.
    """
    args = quad_args(Q1[0], Q1[1], 1.0, COMP_DSC, COMP_DSC, COMP_DSC)
    out = [
        rows_cell("p3-rows01", 0, 1, VS_QUAD, args),
        rows_cell("p3-rows23", 2, 3, VS_QUAD, args),
        ipl_cell("p3-s5", 5, VS_QUAD, args),
        ipl_cell("p3-s7", 7, VS_QUAD, args),
        # overwrite row 1 (x = 1.0) with row 2 (z = 3.0)
        ipl_cell_pre("p3-rw21-s5", 5, VS_QUAD, args, alu_copy(2, 1)),
        rows_cell_pre("p3-rw21-row1", 1, 2, VS_QUAD, args, alu_copy(2, 1)),
        # copy row 2 (z = 3.0) into row 3 (w = 0.5625)
        ipl_cell_pre("p3-cp23-s7", 7, VS_QUAD, args, alu_copy(2, 3)),
        rows_cell_pre("p3-cp23-row3", 3, 2, VS_QUAD, args, alu_copy(2, 3)),
        # the same operand seeded by the ALU, no interpolator at all
        seed_cell("p3-seed-s5", 5, 3.0, VS_QUAD, args),
        seed_cell("p3-seed-s7", 7, 3.0, VS_QUAD, args),
    ]
    return out, 2


# --- 6. the same operand through both paths at four clip w ---------------

def section_ws():
    """One value, four clip w, twice: seeded by the ALU and fetched by the ipl.

    `comp-base w1` and `w4` read the same row (the row readbacks are identical)
    but their sfu codes differ, so something that moves with w reaches the sfu
    that the ALU row does not carry.  If the ALU-seeded codes also move with w,
    that factor is part of the sfu's own input path; if they do not, it is part
    of the interpolator fetch.
    """
    out = []
    for w in (1.0, 2.0, 4.0, 8.0):
        args = scalar_args(Q1[0], Q1[1], w, 1.0, 1.0, 1.0)
        out.append(seed_cell("ws-seed-w%s" % tag(w), 5, 1.0, VS_PERSP, args))
        out.append(ipl_cell("ws-ipl-w%s" % tag(w), 5, VS_PERSP, args))
    return out, 2


# --- 4. priority 3: the interpolator-to-ALU boundary ----------------------

def section_bnd():
    out = []
    for w in (1.0, 4.0):
        args = scalar_args(Q1[0], Q1[1], w, 0.5, 0.5, 0.5)
        out.append(ipl_cell("bnd-ipl-s5-w%s" % tag(w), 5, VS_PERSP, args))
        out.append(ipl_cell_pre("bnd-rw-s5-w%s" % tag(w), 5, VS_PERSP, args,
                                alu_copy(1, 1)))
        out.append(ipl_cell("bnd-s7-w%s" % tag(w), 7, VS_PERSP, args))
        out.append(ipl_cell_pre("bnd-cp-s7-w%s" % tag(w), 7, VS_PERSP, args,
                                alu_copy(1, 3)))
        out.append(rows_cell_pre("bnd-cp-row3-w%s" % tag(w), 3, 1, VS_PERSP,
                                 args, alu_copy(1, 3)))
        out.append(seed_cell("bnd-seed-s5-w%s" % tag(w), 5, 0.5, VS_PERSP,
                             args))
    return out, 2


SECTIONS = {
    "num": (section_num, "s61-num", s59.s57.LNK),
    "vtx": (section_vtx, "s61-vtx", s59.s57.LNK),
    "comp": (section_comp, "s61-comp", s59.s57.LNK),
    "swz": (section_swz, "s61-swz", LNK_PLAIN),
    "bnd": (section_bnd, "s61-bnd", s59.s57.LNK),
    "p3": (section_p3, "s61-p3", s59.s57.LNK),
    "ws": (section_ws, "s61-ws", s59.s57.LNK),
}


def plain_controls():
    """The standard controls with the plain link's own green/blue exchange.

    `varying-pipeline` reads back the exported varying, and the plain link drops
    the default link's exchange, so the same program reads 0xFF0100FF there.
    Keeping the control with the permuted expectation makes that exchange part
    of the measurement instead of an assumption, and the immediate-operand
    control is unchanged, so both standard controls still run.
    """
    out = []
    for control in s59.s57.CONTROLS:
        control = dict(control)
        if control["expected"] == "0xFF01FF00":
            control["expected"] = "0xFF0100FF"
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
    manifest = {
        "name": name,
        "repeat": repeat,
        "timeout": 20,
        "program": {"vs": s59.s57.VS, "fs": FS, "lnk": lnk},
        "controls": plain_controls() if lnk == LNK_PLAIN else s59.s57.CONTROLS,
        "variants": variants,
    }
    with open(out_path, "w") as fp:
        json.dump(manifest, fp, indent=2)
        fp.write("\n")
    print("%s: %d variants, %d runs" % (name, len(variants),
                                        len(variants) * repeat))


if __name__ == "__main__":
    main()
