#!/usr/bin/env python3
"""Generate the note 63 manifests for tests/grate/asm/mfu_s59_read.in.

Usage: make_s63.py <out.json> <section>

Sections:
  bridge  one manifest holding exact duplicates and the A/B crossover of the two
          note-62 cases that disagreed across manifests: the ALU-seeded 3.0 in
          the scalar program (code 212 in five manifests) and the ALU-seeded 3.0
          in the quad program at w = 4 (code 219 in `s62_cp`).  The order is
          B, A, B, A, B, A then the rest, so a value that follows the case cannot
          also follow its position.
  pair    priority 4: fetched against ALU-seeded for four values spanning both
          store branches, both paths in the same word sequence, with a duplicated
          capture and the gain pair for the capture-path check.

The reading template stores only bytes 1 and 3 (`store rt1, r0, r1` puts code(r0)
in byte 1 and code(r1) in byte 3); bytes 0 and 2 are never written, which is why
some words carry a non-zero second byte.  Only bytes 1 and 3 are data.

Selector 5..7 capture at 2^4: code = 151 - 4*log2(m) below |v| = 2 and
215 - 4*log2(m) at or above it, 16 codes lower at 2^6.  Selector-4 cells capture
`rcp r4` at 2^12: code = 215 - 8*log2(D).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_s59 as s59  # noqa: E402
import make_s61 as s61  # noqa: E402

FS = s61.FS
VS_PERSP = s61.VS_PERSP
VS_QUAD = s61.VS_QUAD
VS_SCALAR_U = s61.VS_SCALAR_U
LNK_DEF = s59.s57.LNK
K16 = s61.K16
K64 = "64.0"
Q1 = s61.Q1
tag = s61.tag

DSC = (1.0, 1.5, 3.0, 0.5625)


def scalar(w, a=1.0):
    return s61.uniform_args(w, a, a, a)


def quad(w, values=DSC):
    return s61.quad_args(Q1[0], Q1[1], w, values, values, values)


def seed(name, sel, value, vs, args, scale=K16):
    return s61.seed_cell(name, sel, value, vs, args, scale=scale)


def sel_cell(name, sel, vs, args, scale=K16):
    return s61.ipl_cell(name, sel, vs, args, scale=scale)


# --- 1. the bridge: exact duplicates and the A/B crossover ----------------

def section_bridge():
    """A = scalar program, ALU-seeded 3.0 at w = 1 (the 212 case).
    B = quad program, ALU-seeded 3.0 at w = 4 (the 219 case).
    """
    a = lambda n: seed(n, 5, 3.0, VS_SCALAR_U, scalar(1.0))
    b = lambda n: seed(n, 5, 3.0, VS_QUAD, quad(4.0))
    out = [b("br-b1"), a("br-a1"), b("br-b2"), a("br-a2"), b("br-b3"),
           a("br-a3")]
    # the rest of the note-62 cases, in a fixed order
    out += [
        seed("br-a-w4", 5, 3.0, VS_SCALAR_U, scalar(4.0)),
        seed("br-a-w2", 5, 3.0, VS_SCALAR_U, scalar(2.0)),
        seed("br-b-w1", 5, 3.0, VS_QUAD, quad(1.0)),
        seed("br-1ple-1", 5, 1.0, VS_SCALAR_U, scalar(1.0)),
        seed("br-1ple-4", 5, 1.0, VS_QUAD, quad(4.0)),
        sel_cell("br-ipl1-w1", 5, VS_PERSP,
                 s61.scalar_args(Q1[0], Q1[1], 1.0, 1.0, 1.0, 1.0)),
        sel_cell("br-ipl1-w4", 5, VS_PERSP,
                 s61.scalar_args(Q1[0], Q1[1], 4.0, 1.0, 1.0, 1.0)),
        s61.ipl_cell_pre("br-cp3-w4", 5, VS_QUAD, quad(4.0),
                         s61.alu_copy(2, 1)),
        s61.rows_cell("br-rows-w4", 1, 2, VS_QUAD, quad(4.0)),
        sel_cell("br-gain-x64", 5, VS_SCALAR_U, scalar(1.0), scale=K64),
        seed("br-b-again", 5, 3.0, VS_QUAD, quad(4.0)),
        a("br-a-again"),
    ]
    return out, 2


# --- 2. the paired fetched-versus-seeded cells ---------------------------

def section_pair():
    """Four values, both paths, one manifest, duplicated capture.

    Each value gets a fetched cell and an ALU-seeded cell in the *same* program
    (the quad program, whose varying carries four components, so the fetched
    row and the seeded row can both be arranged), plus a repeat of each capture.
    """
    out = []
    for v in (1.0, 1.5, 3.0, 4.0):
        vals = (v, v, v, v)          # constant varying: row value == v
        args = quad(1.0, vals)
        out.append(sel_cell("pr-f-%s" % tag(v), 5, VS_QUAD, args))
        out.append(seed("pr-s-%s" % tag(v), 5, v, VS_QUAD, args))
        out.append(sel_cell("pr-f-%s-again" % tag(v), 5, VS_QUAD, args))
        out.append(seed("pr-s-%s-again" % tag(v), 5, v, VS_QUAD, args))
        out.append(s61.rows_cell("pr-row-%s" % tag(v), 1, 0, VS_QUAD, args))
    # the capture-path gain pair at one value
    args = quad(1.0, (1.5, 1.5, 1.5, 1.5))
    out.append(sel_cell("pr-gain-x16", 5, VS_QUAD, args))
    out.append(sel_cell("pr-gain-x64", 5, VS_QUAD, args, scale=K64))
    return out, 3


def section_order():
    """Does the seeded 3.0 readback follow the *previous* draw?

    In `s62_cp` the seeded 3.0 cell was the variant right after a seeded 1.0
    cell and read 219; in every manifest where it came first, or after another
    3.0, it read 212.  This section alternates 1.0 and 3.0 cells so each 3.0 can
    be classified by what preceded it, with no other change.
    """
    s1 = lambda n: seed(n, 5, 1.0, VS_QUAD, quad(4.0))
    s3 = lambda n: seed(n, 5, 3.0, VS_QUAD, quad(4.0))
    return [s3("or-a"), s1("or-b"), s3("or-c"), s1("or-d"), s3("or-e"),
            s3("or-f"), s1("or-g"), s3("or-h"), s1("or-i"), s3("or-j"),
            s3("or-k"), s3("or-l")], 2


def section_ctx():
    """One seeded 3.0 after each kind of predecessor, all in one manifest.

    The manifest with the fetched and row-readback cells before it reads 219 for
    the seeded 3.0, and the manifest that holds only seed cells reads 212, so
    the trigger is somewhere in the predecessor set.  Each `cx-s3-*` cell sits
    directly after one candidate predecessor.
    """
    w4 = quad(4.0)
    s3 = lambda n: seed(n, 5, 3.0, VS_QUAD, w4)
    s1 = lambda n: seed(n, 5, 1.0, VS_QUAD, w4)
    return [
        s3("cx-s3-0"),
        s1("cx-s1"), s3("cx-s3-after-1ple"),
        s61.rows_cell("cx-rows01", 0, 1, VS_QUAD, w4), s3("cx-s3-after-rows"),
        sel_cell("cx-ipl5", 5, VS_QUAD, w4), s3("cx-s3-after-ipl5"),
        sel_cell("cx-ipl6", 6, VS_QUAD, w4), s3("cx-s3-after-ipl6"),
        s61.ipl_cell_pre("cx-cp21", 5, VS_QUAD, w4, s61.alu_copy(2, 1)),
        s3("cx-s3-after-copy"),
        s61.ipl_cell_pre("cx-ipl2", 6, VS_QUAD, w4, s59.IPL_WORD),
        s3("cx-s3-after-ipl2"),
        s61.rows_cell("cx-rows23", 2, 3, VS_QUAD, w4), s3("cx-s3-after-rows23"),
        s3("cx-s3-last"),
    ], 2


def section_same():
    """Twelve byte-identical seeded-3.0 cells, nothing else.

    `s63_ctx` reads 212 for the first two seeded cells and 219 for every one
    after the first rows cell.  If this run also transitions, the trigger is the
    run's own warm-up rather than a predecessor; if all twelve agree, the trigger
    is in what came before.
    """
    return [seed("sm-%02d" % n, 5, 3.0, VS_QUAD, quad(4.0)) for n in range(12)], 2


def section_names():
    """Four identical variants; one carries the exact name that read 219.

    If the code follows the *name* rather than the program, something outside
    the recorded context is keyed by it.  The generated sources of all four are
    byte-identical (`source_sha256`), so any difference here has no program
    explanation.
    """
    out = []
    for n in ("cp-seed-c-s5", "nm-a", "nm-b", "cp-seed-c-s5b"):
        out.append(seed(n, 5, 3.0, VS_QUAD, quad(4.0)))
    return out, 2


def section_pair4():
    """The same four values at w = 4, where note 62 saw a fetched/seeded gap.

    Constant varying, so the fetched row value equals the seeded value; the two
    paths sit next to each other in one manifest, with a duplicate of each.
    """
    out = []
    for v in (1.0, 1.5, 3.0, 4.0):
        vals = (v, v, v, v)
        args = quad(4.0, vals)
        out.append(sel_cell("p4-f-%s" % tag(v), 5, VS_QUAD, args))
        out.append(seed("p4-s-%s" % tag(v), 5, v, VS_QUAD, args))
        out.append(sel_cell("p4-f-%s-again" % tag(v), 5, VS_QUAD, args))
        out.append(seed("p4-s-%s-again" % tag(v), 5, v, VS_QUAD, args))
        out.append(s61.rows_cell("p4-row-%s" % tag(v), 1, 0, VS_QUAD, args))
    args = quad(4.0, (1.5, 1.5, 1.5, 1.5))
    out.append(sel_cell("p4-gain-x16", 5, VS_QUAD, args))
    out.append(sel_cell("p4-gain-x64", 5, VS_QUAD, args, scale=K64))
    return out, 3


SECTIONS = {
    "pair4": (section_pair4, "s63-pair4", LNK_DEF),
    "ctx": (section_ctx, "s63-ctx", LNK_DEF),
    "same": (section_same, "s63-same", LNK_DEF),
    "names": (section_names, "s63-names", LNK_DEF),
    "bridge": (section_bridge, "s63-bridge", LNK_DEF),
    "pair": (section_pair, "s63-pair", LNK_DEF),
    "order": (section_order, "s63-order", LNK_DEF),
}


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
