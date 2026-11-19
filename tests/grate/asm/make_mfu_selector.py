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
    out.append(offset("p1-coef-noipl", -0.5, -0.4, word(coef, None)))
    out.append(offset("p1-interp-rg", -0.5, -0.4, word(interp, FULL_IPL), STORE_01))
    out.append(offset("p1-interp-ba", -0.5, -0.4, word(interp, FULL_IPL), STORE_23))
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
