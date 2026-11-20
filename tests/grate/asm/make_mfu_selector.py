#!/usr/bin/env python3
"""Generate the MFU multiplier probe manifests.

The MFU multiplier takes two 4-bit source numbers and a 3-bit destination. The
documented meaning is 0..3 = row registers r0..r3, 10 = the SFU result, 11/12 =
the barycentric coefficients, 13 = 1.0, destination 4..7 = the row registers and
1 = the barycentric weight. Earlier hand probes measured that table with programs
that carried interpolation slots and several MFU words; these probes isolate one
field per program.

Every variant is one program of three batches at alu_buffer_size 1:

    batch 0   write 0.0625, 0.125, 0.25 and 0.375 into r0..r3.low
    batch 1   exactly one MFU sub-instruction
    batch 2   store one register pair

so the readback is the product the multiplier wrote. If it reads row register N
it returns byte N of the constants table, and values 4..9, 14 and 15 have no row
register to name, so whatever they return is not a primed value - that is the
separation these probes rely on.

The store word is hi<<24 | lo<<16 | lo<<8 | lo, i.e. the first stored register
in bits 0..15 and the second in bits 16..31, .lo before .hi within each half. So
`store rt1, r2, r3` puts the multiplier's destination r2 in the low 16 bits and
the primed r3 (0x60) above it, which doubles as a marker that the prime arrived;
`store rt1, r0, r1` covers r0 and r1 the same way.

Seven sets, one manifest each - the sets are separate experiments and each is
pinned from its own run:

    --set src     one per source number, in the first source field
    --set dst     which row register the destination field writes
    --set field   row register in the second source field, against 1.0
    --set sfu     the SFU result as a source, with and without an SFU word
    --set fp20    whether the register named in `sfu:` is the value it reads
    --set w       what row 4 carries, by scaling the vertex clip w
    --set bary    selectors 11 and 12 against interpolated data
    --set persp   the selectors and a scalar varying as clip w changes
    --set matrix  both multipliers' complete source/destination matrix
    --set chain   where a weight has to sit relative to the fetch that reads it

    make_mfu_selector.py --set dst                # no expectations yet
    make_mfu_selector.py --set dst --results DIR  # pin to the words from DIR

Controls: `none*` has no MFU word at all (the store must return the primes, so
the store path is not what changed) and `noprime-src0` has the prime batch
emptied (src0 must return 0, so a non-zero src0 word cannot be credited to stale
row state). `two-op-*` variants multiply two primed registers, `const13-times-*`
uses source 13 as the first operand - if 13 is the constant 1.0 it must return
the row value untouched.
"""

import argparse
import csv
import json
import pathlib

NOP = "NOP"
TEMPLATE = "tests/grate/asm/mfu_sel.in"

# 0x520's low field is the batch count; size 1 means one batch per group, and
# the count equals the program length so each batch runs once.
START_WORD = 0x20006000
BATCHES = 3

PRIME = (0, 1, 2, 3)
STORE_23 = "store rt1, r2, r3"
STORE_01 = "store rt1, r0, r1"


def prime_slots(fmt="fx10"):
    out = []
    for index, const in enumerate(PRIME):
        mask = {"fx10": "l*", "fp20": "lh"}[fmt]
        out.append(f"MAD  r{index}.{mask},  u{const},  #1,  #0")
    return out


def variant(name, word=None, primed=True, store=STORE_23, fmt="fx10"):
    """One variant: the same program, only the fields under test differ."""
    slots = prime_slots(fmt) if primed else [NOP] * 4
    replace = {f"@P{index}@": slot for index, slot in enumerate(slots)}
    replace["@MFU@"] = word or NOP
    replace["@STORE@"] = store
    return {
        "name": name,
        "environment": {"GRATE_POKE": f"0x520=0x{START_WORD | BATCHES:08X}"},
        "replace": {"fs": replace},
        "fs": TEMPLATE,
    }


def src_variants():
    """One word per source number, plus variants that pin the readout itself."""
    out = [(f"src-{source:02d}", f"mul0: r2, src{source}, #1", True)
           for source in range(16)]
    out.append(("none", None, True))
    out.append(("noprime-src0", "mul0: r2, src0, #1", False))
    out.append(("two-op-src2-src2", "mul0: r2, src2, src2", True))
    out.append(("two-op-src3-src2", "mul0: r2, src3, src2", True))
    out.append(("two-op-src3-src3", "mul0: r2, src3, src3", True))
    out.append(("const13-times-src0", "mul0: r2, src13, src0", True))
    # Both operands are row registers and the destination is one of them. Moving
    # the destination to r3, which neither operand names, separates "row * row
    # does not multiply" from "a destination that is also a source reads zero".
    out.append(("two-op-dst3-src0-src1", "mul0: r3, src0, src1", True))
    out.append(("two-op-dst3-src2-src3", "mul0: r3, src2, src3", True))
    return [variant(name, word, primed) for name, word, primed in out]


def dst_variants():
    """Which row register each destination number writes.

    Destination numbers 4..7 are documented as r0..r3 and the source is a row
    register whose value differs from the prime of the register being written
    (r0 0x10, r1 0x20, r2 0x40, r3 0x60), so a written register changes byte and
    an unwritten one keeps its prime. The same four multiplies are also read
    through the *other* store pair, which shows that only one register moves.
    Numbers below 4 are undocumented: they are run with 1.0 as both operands,
    whose product has its own encoding (0xF7 in the .hi byte, measured for
    src-13), so a write into any row register is visible from either store pair.
    """
    out = [
        # destination 4..7 = r0..r3, read through the pair that contains it
        ("dst4-store01", "mul0: dst4, src3, #1", STORE_01),
        ("dst5-store01", "mul0: dst5, src3, #1", STORE_01),
        ("dst6-store23", "mul0: dst6, src0, #1", STORE_23),
        ("dst7-store23", "mul0: dst7, src0, #1", STORE_23),
        # baselines: no MFU word, one per store pair
        ("none-store01", None, STORE_01),
        ("none-store23", None, STORE_23),
        # the pair the write does not name must be left alone
        ("dst4-store23", "mul0: dst4, src3, #1", STORE_23),
        ("dst5-store23", "mul0: dst5, src3, #1", STORE_23),
        ("dst6-store01", "mul0: dst6, src0, #1", STORE_01),
        ("dst7-store01", "mul0: dst7, src0, #1", STORE_01),
    ]
    for dst in range(4):
        out.append((f"dst{dst}-store01", f"mul0: dst{dst}, #1, #1", STORE_01))
        out.append((f"dst{dst}-store23", f"mul0: dst{dst}, #1, #1", STORE_23))
    return [variant(name, word, True, store) for name, word, store in out]


def field_variants():
    """A row register in the second source field, with 1.0 in the first.

    The source sweep puts the number under test in the first field only, so a
    row register in the second field has only been shown with src0. If 13 is the
    constant 1.0, `src13 x srcN` and `srcN x src13` must both return the row
    value; if they differ, the two fields are not symmetric.
    """
    out = []
    for row in PRIME:
        out.append((f"src13-x-src{row}", f"mul0: r2, src13, src{row}", True))
    for row in PRIME:
        out.append((f"src{row}-x-src13", f"mul0: r2, src{row}, src13", True))
    return [variant(name, word, primed) for name, word, primed in out]


VS_SCALE = "tests/grate/asm/vs_scale_w.txt"


def scaled(name, scale, word, store=STORE_23):
    """Same program, the vertex shader scales clip x, y, z and w together.

    Scaling all four components leaves x/w, y/w and z/w unchanged, so the quad
    covers the same pixels and the only input that moves is the clip-space w
    that goes into the fragment's r4. The scale is a vertex uniform, so the
    stage sources are byte-identical across variants and only the argument
    differs; a value of 1.0 is the anchor that must reproduce the unscaled
    program's word.
    """
    entry = variant(name, word, True, store)
    entry["vs"] = VS_SCALE
    entry["args"] = ["--vs_uniform",
                     f'["clip_scale"]=({scale},{scale},{scale},{scale})']
    return entry


def w_variants():
    """Does the SFU read the fragment w that docs 6.5 puts in r4?

    Docs 6.5 puts the fragment's w in row 4 for the first MFU instruction, so
    the SFU operand is r4 - the register the unit reads *and* writes. Only the
    vertex shader's clip w then changes. Measured: the operand is about 2^16/w,
    so rcp moves two octaves per fourfold w, rsq lands on 1/256 and 1/128, and
    sqrt and lg2 saturate at every w. The no-SFU variants are the baseline at
    the same w, so a changed word cannot be credited to the primes moving.

    A vertex uniform that did not arrive would collapse the quad to w = 0 at
    the origin, which shows up as a lost word, not as the anchor, so the 1.0
    variants double as a check that the scaling stage works at all.
    """
    out = [
        ("w1-sqrt4", 1.0, "sfu: sqrt r4 mul0: r2, src10, #1"),
        ("w0.25-sqrt4", 0.25, "sfu: sqrt r4 mul0: r2, src10, #1"),
        ("w4-sqrt4", 4.0, "sfu: sqrt r4 mul0: r2, src10, #1"),
        ("w1-rcp4", 1.0, "sfu: rcp r4 mul0: r2, src10, #1"),
        ("w0.25-rcp4", 0.25, "sfu: rcp r4 mul0: r2, src10, #1"),
        ("w4-rcp4", 4.0, "sfu: rcp r4 mul0: r2, src10, #1"),
        ("w0.25-nosfu", 0.25, "mul0: r2, src10, #1"),
        ("w4-nosfu", 4.0, "mul0: r2, src10, #1"),
        # sqrt saturates at every w, so the operand's size needs its own ops:
        # lg2 of a ~2^16 operand saturates, of a 0..4 one it does not, and
        # rsq of 2^16 is 1/256 while rsq of 1 is 1.
        ("w1-lg2-4", 1.0, "sfu: lg2 r4 mul0: r2, src10, #1"),
        ("w4-lg2-4", 4.0, "sfu: lg2 r4 mul0: r2, src10, #1"),
        ("w1-rsq-4", 1.0, "sfu: rsq r4 mul0: r2, src10, #1"),
        ("w4-rsq-4", 4.0, "sfu: rsq r4 mul0: r2, src10, #1"),
    ]
    return [scaled(name, scale, word) for name, scale, word in out]


def sfu_variants():
    """Does source 10 expose the SFU result, and does it need the same word?

    One MFU word holds an sfu sub-instruction and both multipliers, so
    `sfu: rcp r3 mul0: ...` puts them in the same word. The comparison is the
    same multiply with the sfu sub-instruction removed, and the `-only` variants
    show whether the SFU writes a row register by itself.

    The SFU writes the register it names, so `sfu: rcp r3` followed by a mul
    reading src10 would return the same value if src10 were simply r3's content.
    `sfu: rcp r1` moves the SFU's output to a register the store pair does not
    read in the r2/r3 variants, so an unchanged r3 in `sfu-rcp1-src10` shows that
    src10 is SFU state rather than a row register. `sqrt r3` is there because
    rcp saturates, so a second operation with a different range is worth having.
    These primes are fx10, which leaves the fp20 operand the unit reads at
    zero, so what it computes here is sqrt and rcp of the format's floor;
    `--set fp20` supplies real operands and `--set w` looks at row 4.
    """
    out = [
        ("sfu-rcp3-src10", "sfu: rcp r3 mul0: r2, src10, #1", STORE_23),
        ("nosfu-src10", "mul0: r2, src10, #1", STORE_23),
        ("sfu-rcp3-src0", "sfu: rcp r3 mul0: r2, src0, #1", STORE_23),
        ("sfu-rcp3-only", "sfu: rcp r3", STORE_23),
        ("sfu-rcp1-src10", "sfu: rcp r1 mul0: r2, src10, #1", STORE_23),
        ("sfu-rcp1-only", "sfu: rcp r1", STORE_01),
        ("sfu-sqrt3-src10", "sfu: sqrt r3 mul0: r2, src10, #1", STORE_23),
        # r0 and r3 hold different fx10 values (0.0625 and 0.375). They agree
        # here, which is what `--set fp20` explains: the fp20 byte the unit
        # reads lives above the low fx10 half, so both rows look alike to it.
        ("sfu-sqrt0-src10", "sfu: sqrt r0 mul0: r2, src10, #1", STORE_23),
    ]
    return [variant(name, word, True, store) for name, word, store in out]


def fp20_variants():
    """Is the register named in `sfu:` the value the unit reads?

    The syntax doc describes the operand as in place - `sfu: RCP r1` means
    r1 = 1.0 / r1 - but the value has to be there in fp20. A `.l*` prime writes
    only the low fx10 half of the row, and section 12.7's readback shows the
    fp20 byte one byte above it, so an fx10 prime leaves the unit reading the
    floor of the format. `.lh` writes the whole row as fp20.

    With real fp20 primes the op has a result to check against 12.7's table:
    0.0625 -> D7, 0.25 -> E7, 0.375 -> EB, so sqrt(0.0625) = 0.25 -> E7,
    sqrt(0.25) = 0.5 -> EF and sqrt(0.375) = 0.612 -> F0 (the mantissa is
    truncated to the three bits the readback carries). Different results per
    named register are the evidence that the register is the input.
    """
    out = [
        ("fp20-none-store23", None),
        ("fp20-sqrt3-only", "sfu: sqrt r3"),
        ("fp20-sqrt3-src10", "sfu: sqrt r3 mul0: r2, src10, #1"),
        ("fp20-sqrt2-src10", "sfu: sqrt r2 mul0: r2, src10, #1"),
        ("fp20-sqrt0-src10", "sfu: sqrt r0 mul0: r2, src10, #1"),
        ("fp20-rcp3-src10", "sfu: rcp r3 mul0: r2, src10, #1"),
    ]
    return [variant(name, word, True, STORE_23, "fp20") for name, word in out]


VS_OFFSET = "tests/grate/asm/vs_offset.txt"
VS_PERSP = "tests/grate/asm/vs_persp_col.txt"
VS_PERSP_POS = "tests/grate/asm/vs_persp_pos.txt"
VS_POS = "tests/grate/asm/vs_pos.txt"
PARTIAL_IPL = "ipl: t0.fp20, NOP, NOP, NOP"
FULL_IPL = "ipl: t0.fp20, t0.fp20, t0.fp20, t0.fp20"


def offset(name, dx, dy, word, store=STORE_23):
    """Same program, the vertex shader moves the quad by a uniform offset.

    The harness reads pixel (0,0), which in the untranslated quad sits on the
    top-left vertex, so the barycentric weights there are (1, 0, 0). Moving the
    quad puts that pixel inside the triangle where all three weights are
    non-zero; translating by the clip-space offset is exact because w stays 1.
    """
    entry = variant(name, word, True, store)
    entry["vs"] = VS_OFFSET
    entry["args"] = ["--vs_uniform", f'["offset"]=({dx},{dy},0.0,0.0)']
    return entry


def bary_variants():
    """What do selectors 11 and 12 read, with interpolation actually running?

    The source sweep read 0xFF00 for both, which is the readback of a value at
    or above 2.0 - not a 0..1 weight - and it did so without fetching anything.
    So the numbers say nothing yet about what they are. Two readings separate
    the candidates:

      * raw (`src11 x #1`) shows the magnitude class only, since anything at or
        above 2.0 reads as FF;
      * scaled (`src11 x sfu`) multiplies by rcp(r4), which the same word also
        measures with 1.0 (`src13 x sfu`). The readback is a log byte, so the
        difference of the two bytes is 8*log2(coef) and the coefficient comes
        out without needing to know the unit's scale.

    A coefficient is a function of the pixel's position in the triangle, not of
    the vertex data, so each reading is taken at three positions: the corner
    (1, 0, 0), and two interior points where the weights differ. The last two
    variants fetch the colour varying for real and store the interpolated
    result, which gives the weights from the vertex colours (red, green, blue)
    as an independent check of the position.

    The partial `ipl` writes only r0, so it cannot clobber the r2/r3 the
    multipliers write in the same word; `noipl` drops the fetch entirely.

    Which vertex each coefficient belongs to is a separate question: the colour
    varying ties channels to vertices only through the linker's component
    swizzle, which is not pinned. The four `p1-mask-vN` variants sidestep it by
    exporting 1 at one corner and 0 at the others, built from the position
    attribute in the vertex shader, and replicating it across all four
    components - so whatever the swizzle does, the interpolated value is that
    corner's weight alone. The three non-zero weights must sum to 1.
    """
    raw = ("r2, src11, #1", "r3, src12, #1")
    coef = ("r2, src11, sfu", "r3, src12, sfu")
    scale = ("r2, src13, sfu", "r3, src13, sfu")
    interp = ("bar, sfu, bar0", "bar, sfu, bar1")

    def word(mul, ipl=PARTIAL_IPL):
        text = (f"sfu: rcp r4 mul0: {mul[0]} mul1: {mul[1]}")
        return text + (f" {ipl}" if ipl else "")

    # fb_data[0] is the row at NDC y = -1, so both offsets are negative: the
    # quad has to move down-left for pixel (0, 0) to fall inside it.
    positions = (("p0", 0.0, 0.0), ("p1", -0.5, -0.4), ("p2", -0.25, -0.2))
    out = [offset("p0-none", 0.0, 0.0, None)]
    for label, dx, dy in positions:
        out.append(offset(f"{label}-raw", dx, dy, word(raw)))
        out.append(offset(f"{label}-coef", dx, dy, word(coef)))
        out.append(offset(f"{label}-scale", dx, dy, word(scale)))
    # Does the p2 raw reading belong to selector 12 or to the mul1 slot? The
    # same two products with the multipliers exchanged answer that.
    swap_raw = ("r2, src12, #1", "r3, src11, #1")
    swap_coef = ("r2, src12, sfu", "r3, src11, sfu")
    out.append(offset("p1-swap-raw", -0.5, -0.4, word(swap_raw)))
    out.append(offset("p1-swap-coef", -0.5, -0.4, word(swap_coef)))
    out.append(offset("p2-swap-raw", -0.25, -0.2, word(swap_raw)))
    out.append(offset("p2-swap-coef", -0.25, -0.2, word(swap_coef)))
    # The interpolated position is the sample point itself: the shader exports
    # the *untranslated* a[0] as the varying and translates only the drawn
    # position, so the stored varying gives (x, y) in quad coordinates. With
    # the weights summing to one (the alpha channel is a constant one varying,
    # so it reads back as exactly 1.0) that fixes all three weights of whichever
    # triangle covers the sample, independently of the coefficient selectors.
    def pos(name, dx, dy, store):
        entry = variant(name, word(interp, FULL_IPL), True, store)
        entry["vs"] = VS_POS
        entry["args"] = ["--vs_uniform", f'["offset"]=({dx},{dy},0.0,0.0)']
        return entry

    out.append(pos("p1-pos-rg", -0.5, -0.4, STORE_01))
    out.append(pos("p1-pos-ba", -0.5, -0.4, STORE_23))

    # p3 puts the sample on the other side of the quad's diagonal, where the
    # red vertex 0 is inside the triangle and vertex 3 is not, so the red
    # channel is w0 alone. Comparing it with the coefficient fixes which of the
    # two red corners selector 11 follows, which p0 and p1 cannot: both sit near
    # the corner the two triangles share.
    out.append(offset("p3-coef", -0.2, -0.6, word(coef)))
    out.append(offset("p3-interp-rg", -0.2, -0.6, word(interp, FULL_IPL), STORE_01))
    out.append(pos("p3-pos-rg", -0.2, -0.6, STORE_01))

    # p4 lands on the quad centre, where the barycentric weights are 0.25, 0.5,
    # 0.25 in some order and the colour is a balanced mix of the three vertex
    # colours. Unlike the corner samples it gives an unambiguous prediction
    # from the harness's fixed vertex data.
    out.append(offset("p4-coef", -0.95, -0.95, word(coef)))
    out.append(offset("p4-interp-rg", -0.95, -0.95, word(interp, FULL_IPL), STORE_01))
    out.append(offset("p4-interp-ba", -0.95, -0.95, word(interp, FULL_IPL), STORE_23))
    out.append(pos("p4-pos-rg", -0.95, -0.95, STORE_01))

    # Control: a constant varying. The interpolator must return it unchanged at
    # every sample point, so this separates "the varying path is wrong" from
    # "the colour attribute data is not what the harness appears to bind".
    const = variant("constvar-rg", word(interp, FULL_IPL), True, STORE_01)
    const["vs"] = "tests/grate/asm/vs_constvar.txt"
    out.append(const)
    const = variant("constvar-ba", word(interp, FULL_IPL), True, STORE_23)
    const["vs"] = "tests/grate/asm/vs_constvar.txt"
    out.append(const)

    # And does a two-component export pack the same way as the four-component
    # one? Same constant, only the export mask differs.
    const = variant("constvar2-rg", word(interp, FULL_IPL), True, STORE_01)
    const["vs"] = "tests/grate/asm/vs_constvar2.txt"
    out.append(const)
    const = variant("constvar2-ba", word(interp, FULL_IPL), True, STORE_23)
    const["vs"] = "tests/grate/asm/vs_constvar2.txt"
    out.append(const)

    def group(prefix, vs_col, vs_pos, dx, dy):
        """Coefficient, colour and position readback at one sample point."""
        for name, build, store, shader in (
            ("coef", lambda: word(coef), STORE_23, vs_col),
            ("interp-rg", lambda: word(interp, FULL_IPL), STORE_01, vs_col),
            ("interp-ba", lambda: word(interp, FULL_IPL), STORE_23, vs_col),
            ("pos-rg", lambda: word(interp, FULL_IPL), STORE_01, vs_pos),
        ):
            entry = variant(f"{prefix}-{name}", build(), True, store)
            entry["vs"] = shader
            entry["args"] = ["--vs_uniform", f'["offset"]=({dx},{dy},0.0,0.0)']
            out.append(entry)

    # One triangle: v2 is moved onto v0, so (0,1,2) is degenerate and (1,2,3) -
    # (-1,-1), (-1,1), (1,-1) in quad coordinates - is the only triangle drawn.
    # There is no shared diagonal to land on: every sample is interior.
    group("s1a", "tests/grate/asm/vs_single_col.txt", "tests/grate/asm/vs_single_pos.txt", -1.2, -0.4)
    group("s1b", "tests/grate/asm/vs_single_col.txt", "tests/grate/asm/vs_single_pos.txt", -0.4, -0.8)

    # Unequal clip w: the blue vertex gets w = 2, the others keep w = 1, so the
    # projected quad is a perspective trapezium and interpolation must divide by
    # the interpolated w. The colour varying and the position varying are both
    # read back, which is what separates perspective-correct from screen-space
    # interpolation without modelling the rasteriser.
    group("wwa", "tests/grate/asm/vs_ww_col.txt", "tests/grate/asm/vs_ww_pos.txt", -0.5, -0.5)
    group("wwb", "tests/grate/asm/vs_ww_col.txt", "tests/grate/asm/vs_ww_pos.txt", -0.4, -0.9)

    # One triangle only: triangle (0,1,2) is collapsed, so (1,2,3) - bottom-left,
    # top-right, bottom-right - is the only triangle left. A sample inside it has
    # weights (w_BL, w_TR, w_BR) = ((0.5-y)/2, (x+1)/2 - w_BR, (x-y)/2) with no
    # diagonal to fall on, and its colour is (R, G, B) = (w_BR, w_BL, w_TR).
    return out


def persp_variants():
    """Screen-space geometry held fixed, clip w varied at one vertex.

    The earlier unequal-w probe moved the projected triangle as well, so its
    two samples could not be compared point by point. This set removes that:
    the vertex shader premultiplies the clip position by a per-vertex w, so
    x/w and y/w - the projected positions the rasteriser sees - are the same
    for every w and only the interpolation weights change. w is 1 at every
    vertex except the blue one, which carries 1 + wdelta.

    Three sample points inside triangle (1, 2, 3) - the only one of the
    harness's two triangles with y < x - are fixed by the quad offset, so the
    screen-space weights lambda are the same across w:

        q1  (0.75, 0.45)    (0.125, 0.725, 0.15)
        q2  (0.35, -0.35)   (0.325, 0.325, 0.35)
        q3  (-0.6, -0.8)    (0.8, 0.1, 0.1)

    With w = 4 at the blue vertex the two candidate models predict, for the
    scalar varying whose vertex values are 1.0 (v3), 0.5 (v1) and 0.25 (v2):

        screen space   sum lambda_i A_i                    0.394 0.594 0.525
        perspective    sum (lambda_i A_i / w_i) / sum ...   0.565 0.705 0.547

    and the selectors themselves either stay at (lambda_1, lambda_2) or move
    to (lambda_1/w_1, lambda_2/w_2) over sum (lambda_j/w_j), which for q1 is
    (0.274, 0.397) - far outside the readback's one-step resolution, so q1
    decides this even with the truncation.

    Each point is read three ways at each w: the coefficient derived from the
    subunits, the scalar varying, and src13 x rcp(r4) - the latter is the
    anchor for r4 itself, so a coefficient that moves can be attributed to the
    selector rather than to the reciprocal. The `-pos` variant reads the
    sample point back at w = 1 to confirm the offset arithmetic.
    """
    coef = ("r2, src11, sfu", "r3, src12, sfu")
    scale = ("r2, src13, sfu", "r3, src13, sfu")
    interp = ("bar, sfu, bar0", "bar, sfu, bar1")

    def word(mul, ipl=PARTIAL_IPL):
        return f"sfu: rcp r4 mul0: {mul[0]} mul1: {mul[1]} {ipl}"

    def values(ar, ag, ab):
        """The three vertex values as uniforms: red v3, green v1, blue v2."""
        return ["--vs_uniform", f'["ar"]=({ar},0.0,0.0,0.0)',
                "--vs_uniform", f'["ag"]=({ag},0.0,0.0,0.0)',
                "--vs_uniform", f'["ab"]=({ab},0.0,0.0,0.0)']

    def at(x, y):
        return ["--vs_uniform",
                f'["offset"]=({round(-(1.0 + x), 6)},{round(-(1.0 + y), 6)},0.0,0.0)']

    def emit(name, args, mul, store):
        ipl = FULL_IPL if mul is interp else PARTIAL_IPL
        entry = variant(name, word(mul, ipl), True, store)
        entry["vs"] = VS_PERSP
        entry["args"] = args
        out.append(entry)

    points = (("q1", 0.75, 0.45), ("q2", 0.35, -0.35), ("q3", -0.6, -0.8))
    unit = values(1.0, 0.5, 0.25)
    asym = values(0.375, 1.5, 0.0625)
    out = []
    for label, x, y in points:
        base = at(x, y)
        pos = variant(f"{label}-pos", word(interp, FULL_IPL), True, STORE_01)
        pos["vs"] = "tests/grate/asm/vs_persp_pos.txt"
        pos["args"] = base
        out.append(pos)
        for w in (1, 2, 4):
            args = base + unit + ["--vs_uniform",
                                  f'["wdelta"]=({w - 1},0.0,0.0,0.0)']
            emit(f"{label}-w{w}-coef", args, coef, STORE_23)
            emit(f"{label}-w{w}-scale", args, scale, STORE_23)
            emit(f"{label}-w{w}-scalar", args, interp, STORE_01)

    # The asymmetric value set - three different values, none a power of two -
    # on the two points that discriminate, so the interpolation equation is
    # checked without relying on the vertex-colour channel order.
    for label, x, y in points[:2]:
        for w in (1, 4):
            args = at(x, y) + asym + ["--vs_uniform",
                                      f'["wdelta"]=({w - 1},0.0,0.0,0.0)']
            emit(f"{label}-asym-w{w}-coef", args, coef, STORE_23)
            emit(f"{label}-asym-w{w}-scalar", args, interp, STORE_01)
    return out


TEMPLATE_MATRIX = "tests/grate/asm/mfu_matrix.in"
# The translating shader, not vs_mov.txt: the interpolation variants need
# pixel (0, 0) inside the quad, where all three weights are non-zero.
VS_INTERP = VS_OFFSET
# fb_data[0] is the row at NDC y = -1, so a negative offset moves the quad
# down-left until pixel (0, 0) is inside it and the weights are all non-zero.
INSIDE = (-0.5, -0.4)


def shift(dx, dy):
    return ["--vs_uniform", f'["offset"]=({dx},{dy},0.0,0.0)']


def matrix_variant(name, word1, word0=None, store=STORE_23, slots=None,
                   vs=None, args=None):
    """One variant of mfu_matrix.in.

    `word1` is the MFU word of batch 1 (the one under test) and `word0` an
    optional word in the prime batch; `slots` replaces the four ALU primes.
    """
    replace = {f"@P{index}@": slot
               for index, slot in enumerate(slots or prime_slots())}
    replace["@MFU0@"] = word0 or NOP
    replace["@MFU1@"] = word1 or NOP
    replace["@STORE@"] = store
    entry = {
        "name": name,
        "environment": {"GRATE_POKE": f"0x520=0x{START_WORD | BATCHES:08X}"},
        "replace": {"fs": replace},
        "fs": TEMPLATE_MATRIX,
    }
    if vs is not None:
        entry["vs"] = vs
    if args is not None:
        entry["args"] = args
    return entry


def matrix_variants():
    """Every source pair, every destination position, and both units.

    The source sweep read one number at a time with 1.0 on the other side, and
    the destination sweep wrote one register with 1.0 on both sides. That leaves
    the combination - a row register against another row register, and a
    destination that is also an operand - described by five samples. This set
    runs the whole 4 x 4 table, in both prime formats, with the destination on
    neither operand and then on each of them: the row x row zeros turn out to be
    the prime format (a `.l*` prime is fp20 for 2^-31, so two of them
    underflow), and the destination's position changes nothing.

    The rest closes the numbers the earlier sets left as footnotes:

      * `mul1` on its own, since every source/destination measurement so far
        drove `mul0`, and the two units together writing different registers;
      * row x `sfu`, `sfu` x `sfu` and the coefficient pairs, the source classes
        the sweep and the field set did not cross;
      * selectors 4..9 and 14..15 in the second field and at a second pixel,
        because the sweep only tested them in the first field at one sample and
        they read internal state that may be per-pixel;
      * destinations 0..3 in the word that interpolates, which is the only
        observable those numbers can have: they do not write a row register, so
        the question is whether they write the weight.
    """
    out = []

    # 1. Every row-register pair, the destination on neither operand, in both
    # prime formats. There are four destinations and only two operands, so one
    # that is free always exists; the store pair is the one that contains it,
    # which leaves the other register of the pair holding its prime as a marker.
    #
    # The format matters: the MFU reads a row as fp20, so a `.l*` prime is not
    # 0.0625 but a value near the floor of the format, and two of those multiply
    # to zero through underflow. The fx10 table is kept because that is what the
    # earlier sets measured, and the fp20 table is the one that answers the
    # source question.
    def row_table(prefix, slots=None):
        for first in PRIME:
            for second in PRIME:
                dst = min(d for d in (4, 5, 6, 7) if d - 4 not in (first, second))
                out.append(matrix_variant(
                    f"{prefix}-rows-{first}x{second}",
                    f"mul0: dst{dst}, src{first}, src{second}",
                    store=STORE_01 if dst < 6 else STORE_23, slots=slots))

    row_table("fx10")
    row_table("fp20", prime_slots("fp20"))

    # 2. The same products with the destination moved onto each operand, which
    # is the arrangement every earlier zero was measured in.
    for prefix, slots in (("fx10", None), ("fp20", prime_slots("fp20"))):
        for first, second in ((0, 1), (1, 2), (2, 3), (0, 3)):
            for label, dst in (("first", 4 + first), ("second", 4 + second)):
                out.append(matrix_variant(
                    f"{prefix}-rows-{first}x{second}-ondst-{label}",
                    f"mul0: dst{dst}, src{first}, src{second}",
                    store=STORE_01 if dst < 6 else STORE_23, slots=slots))

    # 3. The two units. `mul1` has only ever run next to a `mul0` that read the
    # other barycentric selector, so its own source and destination numbering is
    # untested; `both-*` then puts one result in each row pair and asks what two
    # writes to one register do. These use the fp20 primes too, so a product is
    # the product and not an underflow.
    fp20 = prime_slots("fp20")
    out.extend(matrix_variant(name, word, store=store, slots=fp20)
               for name, word, store in (
        ("mul1-row0", "mul1: r2, src0, #1", STORE_23),
        ("mul1-13-x-row0", "mul1: r2, src13, src0", STORE_23),
        ("mul1-rows0-x-1", "mul1: r2, src0, src1", STORE_23),
        # src0, not src3: 0.375 is r3's own prime, so a write there would be
        # indistinguishable from not writing at all.
        ("mul1-dst6", "mul1: dst6, src0, #1", STORE_23),
        # Every written value here differs from the destination's own prime and
        # from the other unit's value, so the word says which unit wrote.
        ("both-independent", "mul0: r0, src1, src13 mul1: r1, src2, src13",
         STORE_01),
        ("both-same-dest-01", "mul0: r0, src2, src13 mul1: r0, src3, src13",
         STORE_01),
        ("both-same-dest-23", "mul0: r2, src0, src13 mul1: r2, src3, src13",
         STORE_23),
        ("both-rows", "mul0: r0, src2, src13 mul1: r1, src2, src3", STORE_01),
    ))

    # 3b. The same products with the rows primed as fp20 (`rN.lh`), which is the
    # format the multiplier reads. A `.l*` prime is fx10, and the 16 bits of
    # 0.0625 in that format are 2^-31 as fp20, so two `.l*` primes multiply to
    # about 2^-60 and underflow - which is all the `fx10-*` zeros above are. With
    # fp20 primes the products land on bytes nothing can truncate: 0.0625 x 0.125
    # is 0xBF, 0.25 x 0.375 is 0xDB, and 0.0625 x 1.0 is 0xD7.
    out.extend(matrix_variant(name, word, slots=fp20) for name, word in (
        ("fp20-row0-x-1", "mul0: r2, src0, src13"),
        ("fp20-rows0-x-1", "mul0: r2, src0, src1"),
        ("fp20-rows2-x-3", "mul0: r2, src2, src3"),
        ("fp20-row0-x-sfu", "sfu: sqrt r3 mul0: r2, src0, sfu"),
        ("fp20-const-x-sfu", "sfu: sqrt r3 mul0: r2, src13, sfu"),
    ))

    # 3c. Which pairs of source *classes* multiply. The sweep crossed a row
    # register with 1.0 and the interpolation crosses a coefficient with the SFU
    # result, so every other combination was untested. All of these use the fp20
    # primes, so a small product is still visible as a byte; every pair
    # multiplies, and the coefficient pairs saturate because an unscaled
    # coefficient is at least 2.0.
    out.extend(matrix_variant(name, word, slots=fp20) for name, word in (
        ("class-sfu-x-row0", "sfu: sqrt r3 mul0: r2, sfu, src0"),
        ("class-sfu-x-sfu", "sfu: sqrt r3 mul0: r2, sfu, sfu"),
        ("class-row0-x-bar0", "mul0: r2, src0, src11"),
        ("class-bar0-x-row0", "mul0: r2, src11, src0"),
        ("class-bar0-x-bar1", "mul0: r2, src11, src12"),
        ("class-13-x-bar0", "mul0: r2, src13, src11"),
        ("class-sfu-x-bar0", "sfu: rcp r4 mul0: r2, sfu, src11"),
    ))

    # 4. The numbers with no row register behind them. Field 1 was only ever
    # tested with 13 and a row, and the sweep's one sample sat on the corner
    # pixel, so both the field and the pixel position are new here.
    for source in (4, 5, 6, 7, 8, 9, 14, 15):
        out.append(matrix_variant(f"sel{source}-field1",
                                  f"mul0: r2, src13, src{source}"))
        out.append(matrix_variant(f"sel{source}-corner",
                                  f"mul0: r2, src{source}, #1"))
        out.append(matrix_variant(f"sel{source}-inside",
                                  f"mul0: r2, src{source}, #1",
                                  vs=VS_OFFSET, args=shift(*INSIDE)))
    # And with a varying fetch in the word: the hand probe read 0x4100 for src9
    # in a program with interpolation and zero without, which is the one hint
    # that some of these numbers are interpolation state.
    for source in (4, 5, 6, 9):
        out.append(matrix_variant(f"sel{source}-ipl",
                                  f"sfu: rcp r4 {PARTIAL_IPL} "
                                  f"mul0: r2, src{source}, #1",
                                  vs=VS_OFFSET, args=shift(*INSIDE)))
    # src9 moved with the fetch in place, so a second fetch opcode tells whether
    # the value belongs to the fetch or to the sample.
    out.append(matrix_variant("sel9-ipl-fx10",
                              "sfu: rcp r4 ipl: t0.fx10, NOP, NOP, NOP "
                              "mul0: r2, src9, #1",
                              vs=VS_OFFSET, args=shift(*INSIDE)))

    # 5. Destinations 0..3 do not write a row register, so the interpolation is
    # the only place they can show. The blob writes both weights to `bar`
    # (destination 1) in the word that also fetches, and a word that fetches
    # without computing one of them interpolates with a degenerate weight set,
    # so "destination N instead of bar" is the direct question: whichever number
    # reproduces the control is the weight register.
    def weight(name, replace_dst=None, word0=None, word1=None, point=INSIDE):
        word1 = word1 or (f"sfu: rcp r4 "
                          f"mul0: bar, sfu, bar0 mul1: bar, sfu, bar1 {FULL_IPL}")
        if replace_dst is not None:
            word1 = word1.replace("mul0: bar", f"mul0: dst{replace_dst}", 1)
        out.append(matrix_variant(name, word1, word0=word0, store=STORE_01,
                                  vs=VS_INTERP, args=shift(*point)))

    weight("weight-bar")
    for dst in (1, 2, 3):
        weight(f"weight-dst{dst}", replace_dst=dst)

    # The same table at a second sample point. 1, 2 and 3 agree at the first, so
    # either they name one weight or they are three weights that happen to land
    # on the same colour there; a point with different weights separates those.
    # Destination 0 is deliberately absent: with no weight written the fetch
    # returns state nobody in the program wrote, and the result word differs in
    # its low byte between runs (0xF300AF00 at this point, and 0xF5FFAF00 then
    # 0xF5FFAF40 at the second), so it cannot be pinned.
    weight("weight-bar-q2", point=(-0.25, -0.2))
    for dst in (1, 2, 3):
        weight(f"weight-dst{dst}-q2", replace_dst=dst, point=(-0.25, -0.2))

    # 5b. A weight written a batch earlier, with the fetch word still computing
    # both of its own: 1.0 is the widest corruption of a set that sums to one,
    # and the control is the same program without the earlier write.
    weight("keep-none")
    for dst in range(4):
        weight(f"keep-dst{dst}", word0=f"mul0: dst{dst}, src13, src13")

    # Does a weight survive a batch boundary at all? Both variants fetch in a
    # word that writes only w1; the first has w0 = 1.0 written a batch earlier,
    # so if the earlier value were used the colour would be vertex 0's (1, 0).
    # The control is the same program without that write.
    weight("keep-w0-one", word0="mul0: dst1, src13, src13",
           word1=f"sfu: rcp r4 mul1: bar, sfu, bar1 {FULL_IPL}")
    weight("keep-w0-none", word1=f"sfu: rcp r4 mul1: bar, sfu, bar1 {FULL_IPL}")
    return out


TEMPLATE_CHAIN = "tests/grate/asm/mfu_chain.in"

# The blob's interpolation pattern, split into its parts: the SFU's rcp of r4
# puts 1/w where the coefficient scale cancels, and the two multipliers scale
# the coefficients into w0 and w1.
RCP = "sfu: rcp r4"
WEIGHT0 = "mul0: bar, sfu, bar0"
WEIGHT1 = "mul1: bar, sfu, bar1"


def chain_variant(name, words, word0=None, store=STORE_01):
    """One variant of mfu_chain.in: up to three MFU words in batch 1."""
    replace = {f"@P{index}@": slot
               for index, slot in enumerate(prime_slots("fp20"))}
    replace["@MFU0@"] = word0 or NOP
    for index in range(3):
        word = words[index] if index < len(words) else NOP
        replace[f"@MFU{index + 1}@"] = word or NOP
    replace["@STORE@"] = store
    entry = {
        "name": name,
        "environment": {"GRATE_POKE": f"0x520=0x{START_WORD | BATCHES:08X}"},
        "replace": {"fs": replace},
        "fs": TEMPLATE_CHAIN,
        "vs": VS_INTERP,
        "args": shift(*INSIDE),
    }
    return entry


def chain_variants():
    """Where in a batch a barycentric weight has to be written, and which
    destination numbers `mul1` writes it with.

    The matrix set wrote both weights in the word that fetches and showed that a
    weight written a batch earlier is not used. Those are two ends of a range: an
    MFU word is not one instruction, the schedule runs up to three of them in
    series inside a batch, and the fetch is a slot of one of those words. The
    same program with the weights one word earlier, two words earlier, or after
    the fetch separates "the same word" from "the same batch" and from "the
    fetch reads what was written before it".

    Every one of those variants writes both weights, whatever slot it writes
    them in. That is not cosmetic: `w0` and `w1` are read by the fetch whether or
    not the program wrote them, so a variant that writes only one of them
    interpolates with an uninitialised weight, and `garbage-*` below shows what
    that looks like - two programs that differ only in which MFU slot holds the
    same words return 0xF300AF00 and 0xBD00AF00. An earlier version of this set
    compared `prev-batch` against `w1-only` and it did not reproduce itself
    within one run for that reason, so "the earlier weight is not used" rested on
    two uninitialised words happening to agree. The previous-batch question is
    asked again here with both weights written in batch 0 (`prev-both`), where
    the answer is a defined word rather than a coincidence.

    The second half repeats the destination 1..3 measurement with `mul1` writing
    the weight. The matrix set varied `mul0`'s destination while `mul1` wrote the
    other weight, so it says what `mul0` does with the field and nothing about
    `mul1`; both are given the same control here, and a variant that does not
    write its weight shows up as a word that differs from it.

    Destination 0 is absent: nothing writes a weight with it, so the fetch
    returns the uninitialised state the `garbage-*` pair demonstrates and the word
    cannot be pinned.
    """
    both = f"{RCP} {WEIGHT0} {WEIGHT1}"
    ONE_ONE = "mul0: bar, src13, src13 mul1: bar, src13, src13"
    # `next-word` repeats the rcp of r4 in both words. That is not a second
    # perspective divide: `rcp r4` rewrites r4 in place, so the second rcp is the
    # reciprocal of the reciprocal and the weights come out on the same scale as
    # the single-word case. `next-word-nosfu` drops it entirely and returns the
    # same word, so the fetch does not need an sfu in its own word either.
    out = [
        # A. where the fetch sits relative to the weights it reads
        chain_variant("same-word", [f"{both} {FULL_IPL}"]),
        chain_variant("next-word", [both, f"{RCP} {FULL_IPL}"]),
        chain_variant("next-word-nosfu", [both, FULL_IPL]),
        chain_variant("two-words-before",
                      [both, "mul0: r2, src0, src13", f"{RCP} {FULL_IPL}"]),
        # the fetch first and the weights after it: does the fetch read a weight
        # written later in its own batch?
        chain_variant("fetch-before-weight", [f"{RCP} {FULL_IPL}", both]),
        # B. the previous batch, twice. `one-one-*` writes both weights as 1.0,
        # which needs no sfu and so cannot disturb r4, and asks whether a weight
        # pair from the batch before is read at all. `rcp-twice` puts the blob's
        # weight word in batch 0 instead: it differs from `same-word`, which is
        # `rcp r4` being destructive, so the earlier batch leaves batch 1 a
        # different operand.
        chain_variant("one-one-same", [f"{RCP} {ONE_ONE} {FULL_IPL}"]),
        chain_variant("one-one-prev", [f"{RCP} {FULL_IPL}"], word0=ONE_ONE),
        # the matrix set's own `keep-w0-one` pair, now with the control it lacked:
        # w0 = 1.0 and w1 from the multiplier, written either both in the fetch
        # word or with w0 a batch earlier. Equal words mean the earlier write was
        # read, which is what the two garbage words it was compared against
        # before could not show.
        chain_variant("w0-one-same",
                      [f"{RCP} mul0: bar, src13, src13 {WEIGHT1} {FULL_IPL}"]),
        chain_variant("w0-one-prev", [f"{RCP} {WEIGHT1} {FULL_IPL}"],
                      word0="mul0: bar, src13, src13"),
        chain_variant("rcp-twice", [f"{both} {FULL_IPL}"], word0=both),
        chain_variant("none-weights", [f"{RCP} {FULL_IPL}"]),
        # C. what an unwritten weight looks like, at two MFU slots: not zero, and
        # not the same word, so no variant below leaves one of them unwritten
        chain_variant("garbage-first", [f"{RCP} {WEIGHT1} {FULL_IPL}"]),
        chain_variant("garbage-second",
                      [NOP, f"{RCP} {WEIGHT1} {FULL_IPL}"]),
        # D. the control the destination twins are read against
        chain_variant("both", [f"{both} {FULL_IPL}"]),
    ]
    # F. the same two programs with a single MFU slot in the batch, which is the
    # shape the matrix template has. `keep-w0-one` there returned 0xF300AF00, the
    # word this run gets when w0 is written nowhere (`garbage-first`), while the
    # three-slot shape returns 0x0000F701 for both the fetch-word and the
    # previous-batch write. Running both shapes in one manifest separates the
    # batch length from the program's history.
    one_slot = dict(store=STORE_01, slots=prime_slots("fp20"), vs=VS_INTERP,
                    args=shift(*INSIDE))
    out.append(matrix_variant(
        "one-slot-same", f"{RCP} mul0: bar, src13, src13 {WEIGHT1} {FULL_IPL}",
        **one_slot))
    out.append(matrix_variant(
        "one-slot-prev", f"{RCP} {WEIGHT1} {FULL_IPL}",
        word0="mul0: bar, src13, src13", **one_slot))

    # E. the destination field with each unit as the producer of its own weight.
    # The unit not under test writes its weight to `bar` as usual, so a variant
    # that reproduces `both` did supply its weight through the number under test.
    for dst in (1, 2, 3):
        out.append(chain_variant(
            f"mul0-dst{dst}",
            [f"{RCP} mul0: dst{dst}, sfu, bar0 {WEIGHT1} {FULL_IPL}"]))
        out.append(chain_variant(
            f"mul1-dst{dst}",
            [f"{RCP} {WEIGHT0} mul1: dst{dst}, sfu, bar1 {FULL_IPL}"]))
    return out


SETS = {
    "src": ("mfu-selector-source", "tests/grate/asm_characterize.mfu_selector_src.json",
            src_variants),
    "dst": ("mfu-selector-destination", "tests/grate/asm_characterize.mfu_selector_dst.json",
            dst_variants),
    "field": ("mfu-selector-second-field",
              "tests/grate/asm_characterize.mfu_selector_field.json", field_variants),
    "sfu": ("mfu-selector-sfu-result", "tests/grate/asm_characterize.mfu_selector_sfu.json",
            sfu_variants),
    "w": ("sfu-input-w", "tests/grate/asm_characterize.sfu_input_w.json", w_variants),
    "fp20": ("sfu-operand", "tests/grate/asm_characterize.sfu_operand.json",
             fp20_variants),
    "bary": ("barycentric-coefficients",
              "tests/grate/asm_characterize.barycentric.json", bary_variants),
    "persp": ("perspective-selector",
              "tests/grate/asm_characterize.persp.json", persp_variants),
    "matrix": ("mfu-selector-matrix",
               "tests/grate/asm_characterize.mfu_selector_matrix.json",
               matrix_variants),
    "chain": ("mfu-weight-chain", "tests/grate/asm_characterize.mfu_chain.json",
              chain_variants),
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


def build_manifest(name, variants):
    return {
        "name": name,
        "repeat": 3,
        "timeout": 10,
        "program": {
            "vs": "tests/grate/asm/vs_mov.txt",
            "fs": TEMPLATE,
            "lnk": "tests/grate/asm/linker_vs_tests.txt",
        },
        "controls": CONTROLS,
        "variants": variants,
    }


def measured(path):
    """variant name -> the single word every repeat agreed on."""
    words = {}
    failures = {}
    with (pathlib.Path(path) / "results.tsv").open() as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            if row["phase"] != "variant":
                continue
            name = row["name"]
            if row["status"] != "pass" or not row["result"]:
                failures.setdefault(name, set()).add(row["status"])
                continue
            words.setdefault(name, set()).add(row["result"])
    return words, failures


def pin(manifest, results):
    words, failures = measured(results)
    unpinned = []
    for entry in manifest["variants"]:
        name = entry["name"]
        seen = words.get(name, set())
        if name in failures or len(seen) != 1:
            unpinned.append((name, sorted(seen), sorted(failures.get(name, ()))))
            continue
        entry["expected"] = seen.pop()
    return unpinned


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", choices=sorted(SETS), default="src",
                        help="which probe set to generate (default: src)")
    parser.add_argument("--results", help="run directory to pin expectations from")
    parser.add_argument("--out", help="manifest to write (default: the checked-in one)")
    args = parser.parse_args()

    name, default_out, build = SETS[args.set]
    manifest = build_manifest(name, build())
    out = args.out or default_out
    if args.results:
        unpinned = pin(manifest, args.results)
        if unpinned:
            for variant_name, seen, failed in unpinned:
                print(f"cannot pin {variant_name}: results {seen or 'none'} "
                      f"statuses {failed or 'none'}")
            print("manifest not written")
            return 1
    pathlib.Path(out).write_text(json.dumps(manifest, indent="\t") + "\n")
    print(f"wrote {out} ({len(manifest['variants'])} variants)")
    for entry in manifest["variants"]:
        print(f"{entry['name']:22} {entry.get('expected', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
