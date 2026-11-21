#!/usr/bin/env python3
"""Generate the special-function-unit probe manifests.

`--set ops` is the opcode matrix: every decoded SFU opcode at representative
inputs, the result read through the multiplier's `sfu` source, plus an amplified
readout that measures the value to a fraction of an fp20 ulp. Each program is
five batches at alu_buffer_size 1:

    batch 0   prime r0=0.75, r1=0.5, r2=1.0, r3=input
    batch 1   up to three MFU words
    batch 2   up to three MFU words
    batch 3   ALU readout (fx10 conversion and, for amplified variants, the
              K*(value-expected)+0.5 offset)
    batch 4   store r2, r3

so the answer comes back in the low byte of each stored half. The multiplier
returns the SFU value in r2 and half of it in r3 (`mul1 ... r0` with r0=0.5), so
a value of 1.414 and one of 2.0 stay distinguishable; the ALU then converts both
to fx10, which DW reads as a linear 0..1 fraction. Amplified variants replace
the r2 conversion with K*(value-expected)+0.5 at K=256: an exact value stores
0x7F and one fp20 ulp moves the byte by about eight counts.

The PRE ops (PREEX2, PRESIN, PRECOS) are first steps, and their main op
consumes the intermediate in a later word; the pair variants put the two words
in consecutive MFU slots. The `alone` variants omit the first step and show what
the hardware does without it.

    make_sfu_probe.py --set ops                  # no expectations yet
    make_sfu_probe.py --set ops --results DIR    # pin to the words from DIR

The raw result words are checked in; the interpretation belongs in
asm_characterize.experiments.md. Every variant writes all four row registers
before it reads one, so a result cannot be credited to uninitialised state.
"""

import argparse
import csv
import json
import math
import pathlib

NOP = "NOP"
TEMPLATE = "tests/grate/asm/sfu_ops.in"

# 0x520's low field is the batch count; five EXEC blocks, one row.
START_WORD = 0x20006000
BATCHES = 5

# Must match the .constants table in sfu_ops.in. 30 and 31 are the per-variant
# amplifier offset and K.
INPUTS = [
    0.0, 0.0625, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75,
    1.0, 1.25, 1.5, 1.75, 2.0, 3.0, 4.0,
    -0.25, -0.5, -1.0, -2.0, -1.25,
    0.52359878, 1.57079633, 3.14159265, 6.28318531,
    2.09439510, 4.71238898, -0.52359878, 1.41421356,
    2.5, 0.875,
]
CONST = {value: index for index, value in enumerate(INPUTS)}

PI_6 = 0.52359878
PI_2 = 1.57079633
PI = 3.14159265
TWO_PI = 6.28318531
TWO_PI_3 = 2.09439510
THREE_PI_2 = 4.71238898
MINUS_PI_6 = -0.52359878

K = 256.0


def fp20(value):
    """The assembler's float_to_fp20, decoded back to the value it stores."""
    if value == 0.0:
        return 0.0
    sign = -1.0 if value < 0.0 else 1.0
    value = abs(value)
    exponent = math.floor(math.log2(value))
    mantissa = int((value / (2.0 ** exponent) - 1.0) * 8192.0)
    return sign * (1.0 + mantissa / 8192.0) * (2.0 ** exponent)


def value_slug(value):
    text = f"{value:+.6f}".replace("+", "p").replace("-", "m").replace(".", "p")
    return text.rstrip("0").rstrip("p")


def prime(row, value):
    return f"MAD  r{row}.lh,  u{CONST[value]},  #1,  #0"


def scaled(op, reg="r3"):
    """One MFU word: the SFU op, the value in r2 and half of it in r3."""
    return (f"sfu: {op} {reg} "
            f"mul0: r2, sfu, #1 mul1: r3, sfu, r1")


def variant(name, words, input, amplified=None, linear=True):
    """One program: the primes, up to six MFU words, and a readout."""
    slots = {f"@W{index}@": word for index, word in enumerate(words)}
    for index in range(len(words), 6):
        slots[f"@W{index}@"] = NOP
    if linear:
        slots["@A0@"] = ("MAD  r2.l*,  r2,  u31,  u30" if amplified is not None
                         else "MAD  r2.l*,  r2,  #1,  #0")
        slots["@A1@"] = "MAD  r3.l*,  r3,  #1,  #0"
    else:
        slots["@A0@"] = NOP
        slots["@A1@"] = NOP
    slots["@A2@"] = NOP
    slots["@A3@"] = NOP
    offset = 0.5 - K * amplified if amplified is not None else 0.0
    replace = {
        "@P0@": prime(0, 0.75), "@P1@": prime(1, 0.5),
        "@P2@": prime(2, 1.0), "@P3@": prime(3, input),
        "@OFFSET@": f"{offset:.8f}",
        "@STORE@": "store rt1, r2, r3",
    }
    replace.update(slots)
    return {
        "name": name,
        "environment": {"GRATE_POKE": f"0x520=0x{START_WORD | BATCHES:08X}"},
        "replace": {"fs": replace},
        "fs": TEMPLATE,
    }


def pair(pre, main, input, amplified=None, linear=True):
    """PRE word followed by the main word; the multiplier reads the main op."""
    return variant(f"{main}-{value_slug(input)}",
                   [f"sfu: {pre} r3", scaled(main)],
                   input, amplified, linear)


def alone(op, input, amplified=None, linear=True):
    return variant(f"{op}-{value_slug(input)}",
                   [scaled(op)], input, amplified, linear)


def ops_variants():
    """Every opcode at representative inputs, and the readout controls."""
    out = [
        # no SFU word: the primes must come back, r2 as 1.0 and r3/2 as 0.25
        variant("none", [], 0.5),
    ]

    table = {
        "rcp": (None, [0.5, 0.75, 1.0, 1.5, 2.0, 4.0, -1.0, 0.0]),
        "rsq": (None, [0.0625, 0.25, 0.5, 1.0, 2.0, 4.0, -1.0, 0.0]),
        "lg2": (None, [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]),
        "ex2": ("preex2", [-2.0, -1.0, -0.5, 0.0, 0.25, 0.5, 1.0, 2.0]),
        "sqrt": (None, [0.0625, 0.25, 0.375, 0.5, 1.0, 1.5, 2.0, 4.0, -1.0]),
        "sin": ("presin", [0.0, PI_6, 0.5, 1.0, PI_2, 2.0, PI, TWO_PI,
                           MINUS_PI_6]),
        "cos": ("precos", [0.0, PI_6, 0.5, 1.0, PI_2, TWO_PI_3, PI, TWO_PI]),
        "frc": (None, [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0,
                       -0.25, -1.25]),
        "preex2": (None, [-1.0, 0.0, 0.5, 1.0]),
        "presin": (None, [0.0, PI_6, 1.0, PI]),
        "precos": (None, [0.0, 1.0, PI]),
    }
    for op, (pre, values) in table.items():
        for value in values:
            out.append(pair(pre, op, value) if pre else alone(op, value))

    # The second-step ops without their first step. These are the controls for
    # the pair rows: if they agreed with the pair, the PRE word would be doing
    # nothing.
    for op, value in (("ex2", -1.0), ("sin", 1.0), ("cos", 0.0)):
        out.append(variant(f"{op}-alone-{value_slug(value)}",
                           [scaled(op)], value))

    # Amplified exactness: K*(value-expected)+0.5. The expected values are
    # computed from the fp20 the assembler actually stores, not from the
    # decimal input, so sin(pi/6) is checked at 0.5234375.
    amplified = [
        ("rcp", None, 0.25, 4.0),
        ("rcp", None, 1.5, 1.0 / 1.5),
        ("rcp", None, 3.0, 1.0 / 3.0),
        ("rsq", None, 0.0625, 4.0),
        ("rsq", None, 4.0, 0.5),
        ("lg2", None, 0.5, -1.0),
        ("lg2", None, 1.5, math.log2(fp20(1.5))),
        ("ex2", "preex2", -1.0, 0.5),
        ("ex2", "preex2", 0.5, 2.0 ** fp20(0.5)),
        ("sqrt", None, 0.25, 0.5),
        ("sqrt", None, 2.0, math.sqrt(fp20(2.0))),
        ("sqrt", None, 4.0, 2.0),
        ("sin", "presin", PI_6, math.sin(fp20(PI_6))),
        ("sin", "presin", MINUS_PI_6, math.sin(fp20(MINUS_PI_6))),
        ("cos", "precos", 0.0, 1.0),
        ("cos", "precos", TWO_PI_3, math.cos(fp20(TWO_PI_3))),
        ("frc", None, -0.25, 0.75),
        ("frc", None, 1.75, 0.75),
        ("preex2", None, -1.0, 0.5),
        # negative and boundary results that the 0..1 readout cannot separate
        ("rcp", None, -1.0, -1.0),
        ("rcp", None, 0.5, 2.0),
        ("rsq", None, 0.25, 2.0),
        ("lg2", None, 0.25, -2.0),
        ("sqrt", None, -1.0, 0.0),
    ]
    for op, pre, value, expected in amplified:
        name = f"amp-{op}-{value_slug(value)}"
        words = [f"sfu: {pre} r3", scaled(op)] if pre else [scaled(op)]
        out.append(variant(name, words, value, amplified=expected))

    # In-place: the op alone, no multiplier. A changed r3 is the SFU writing
    # its named register; r2 stays the 1.0 prime unless the write went there.
    in_place = [
        ("rcp", None, 1.5),
        ("rsq", None, 4.0),
        ("lg2", None, 1.5),
        ("ex2", "preex2", -1.0),
        ("sqrt", None, 0.25),
        ("sin", "presin", PI_6),
        ("cos", "precos", 0.0),
        ("frc", None, 1.75),
        ("preex2", None, 1.0),
        ("presin", None, 1.0),
        ("precos", None, 1.0),
    ]
    for op, pre, value in in_place:
        words = [f"sfu: {pre} r3", f"sfu: {op} r3"] if pre else [f"sfu: {op} r3"]
        out.append(variant(f"inplace-{op}", words, value, linear=False))

    # Ordering: the SFU is evaluated first, so it must read the old r3 even
    # when a multiplier in the same word overwrites r3.
    out.append(variant(
        "order-rcp",
        ["sfu: rcp r3 mul0: r3, src13, src13 mul1: r2, sfu, #1"],
        1.5, linear=False))
    out.append(variant(
        "order-sqrt",
        ["sfu: sqrt r3 mul0: r3, src13, src13 mul1: r2, sfu, #1"],
        0.25, linear=False))
    # Both multipliers can read the same SFU result in one word.
    out.append(variant(
        "both-rcp",
        ["sfu: rcp r3 mul0: r2, sfu, #1 mul1: r3, sfu, #1"],
        2.0, linear=False))
    return out


PAIR_TEMPLATE = "tests/grate/asm/sfu_pair.in"
PAIR_BATCHES = 9


def main_word(op, reg="r3", out="r2"):
    """The main op with the multiplier that captures its result."""
    return f"sfu: {op} {reg} mul0: {out}, sfu, #1"


def pair_variant(name, words, input=1.0, clobber=None, store="r2, r3",
                 primes=None, readout=("r2", "r3")):
    """One pairing program: up to 18 MFU words and one ALU clobber word."""
    primes = primes or (0.75, 0.5, 1.0, input)
    slots = {f"@W{index}@": (words[index] if index < len(words) else NOP)
             for index in range(18)}
    for index in range(4):
        slots[f"@C{index}@"] = (clobber if index == 0 and clobber else NOP)
    slots["@A0@"] = f"MAD  {readout[0]}.l*,  {readout[0]},  #1,  #0"
    slots["@A1@"] = f"MAD  {readout[1]}.l*,  {readout[1]},  #1,  #0"
    slots["@A2@"] = NOP
    slots["@A3@"] = NOP
    replace = {
        "@P0@": prime(0, primes[0]), "@P1@": prime(1, primes[1]),
        "@P2@": prime(2, primes[2]), "@P3@": prime(3, primes[3]),
        "@STORE@": f"store rt1, {store}",
    }
    replace.update(slots)
    return {
        "name": name,
        "environment": {"GRATE_POKE": f"0x520=0x{START_WORD | PAIR_BATCHES:08X}"},
        "replace": {"fs": replace},
        "fs": PAIR_TEMPLATE,
    }


def pair_variants():
    """What preserves, replaces or destroys a pending PRE intermediate.

    PRESIN of 1.0 is the base pair: with it, SIN reads 0.839 (0xD6); without it
    SIN returns its operand 1.0 (0xFF). A lost pair is therefore one readout
    byte away from the control, not a subtle value.

    `gap-N` moves the main word N MFU words after the PRE, through the two
    three-word batches and across their boundaries. The clobber rows put a
    write to the operand between the words: a same-value multiplier, a
    different-value multiplier, an ALU write, and an SFU op. The other-register
    rows are the controls that separate "the pending pair was destroyed" from
    "the intervening word disturbed the SFU pipeline".
    """
    out = []

    # A. controls, with and without the first step
    out.append(pair_variant("pair-sin", ["sfu: presin r3", main_word("sin")]))
    out.append(pair_variant("pair-ex2", ["sfu: preex2 r3", main_word("ex2")],
                            input=-1.0))
    out.append(pair_variant("pair-cos", ["sfu: precos r3", main_word("cos")],
                            input=0.0))
    out.append(pair_variant("alone-sin", [main_word("sin")]))
    out.append(pair_variant("alone-ex2", [main_word("ex2")], input=-1.0))
    out.append(pair_variant("alone-cos", [main_word("cos")], input=0.0))

    # B. separation: main at W1..W17, so through five batch boundaries
    for gap in (1, 2, 3, 4, 5, 6, 9, 12, 15, 17):
        words = [NOP] * (gap + 1)
        words[0] = "sfu: presin r3"
        words[gap] = main_word("sin")
        out.append(pair_variant(f"gap-{gap}", words))
    words = [NOP] * 13
    words[0] = "sfu: preex2 r3"
    words[12] = main_word("ex2")
    out.append(pair_variant("gap-ex2-12", words, input=-1.0))
    words = [NOP] * 18
    words[0] = "sfu: precos r3"
    words[17] = main_word("cos")
    out.append(pair_variant("gap-cos-17", words, input=0.0))

    # C. the main has to name the register the PRE named
    out.append(pair_variant(
        "reg-pre0-main0", ["sfu: presin r0", main_word("sin", "r0")],
        primes=(1.0, 0.5, 1.0, 0.5)))
    out.append(pair_variant(
        "reg-pre0-main3", ["sfu: presin r0", main_word("sin", "r3")],
        primes=(1.0, 0.5, 1.0, 1.0)))
    out.append(pair_variant(
        "reg-pre3-main1", ["sfu: presin r3", main_word("sin", "r1")],
        store="r0, r1", readout=("r0", "r1")))
    out.append(pair_variant(
        "reg-pre3-main1-then3",
        ["sfu: presin r3", main_word("sin", "r1"), main_word("sin", "r3")]))

    # D. a write to the operand between the two words
    base = ["sfu: presin r3", NOP, NOP, main_word("sin")]
    out.append(pair_variant("clobber-none", base))
    out.append(pair_variant(
        "clobber-mul-same",
        ["sfu: presin r3", "mul0: r3, src13, src13", NOP, main_word("sin")]))
    out.append(pair_variant(
        "clobber-mul-other",
        ["sfu: presin r3", "mul0: r1, src13, src13", NOP, main_word("sin")]))
    out.append(pair_variant(
        "clobber-mul-different",
        ["sfu: presin r3", "mul0: r3, r0, src13", NOP, main_word("sin")],
        primes=(0.75, 0.5, 1.0, 1.0)))
    out.append(pair_variant("clobber-alu-same", base,
                            clobber="MAD  r3.lh,  u8,  #1,  #0"))
    out.append(pair_variant("clobber-alu-other", base,
                            clobber="MAD  r1.lh,  u8,  #1,  #0"))
    out.append(pair_variant("clobber-alu-different", base,
                            clobber="MAD  r3.lh,  u7,  #1,  #0"))

    # E. every other SFU op between the two words, on the same register
    for op in ("rcp", "rsq", "lg2", "ex2", "sqrt", "cos", "frc",
               "preex2", "presin", "precos"):
        out.append(pair_variant(
            f"intervene-{op}",
            ["sfu: presin r3", f"sfu: {op} r3", NOP, main_word("sin")]))
    # COS and SIN with a multiplier, so they cannot be skipped as an
    # uncaptured second step. The capture goes to r1, away from the operand.
    out.append(pair_variant(
        "intervene-sin",
        ["sfu: presin r3", "sfu: sin r3", NOP, main_word("sin")]))
    out.append(pair_variant(
        "intervene-sin-cap",
        ["sfu: presin r3", "sfu: sin r3 mul0: r1, sfu, #1", NOP,
         main_word("sin")]))
    out.append(pair_variant(
        "intervene-cos-cap",
        ["sfu: presin r3", "sfu: cos r3 mul0: r1, sfu, #1", NOP,
         main_word("sin")]))

    # F. does the main consume the intermediate, and does a new PRE re-arm it?
    out.append(pair_variant(
        "main-twice",
        ["sfu: presin r3", main_word("sin"), main_word("sin")]))
    out.append(pair_variant(
        "main-thrice",
        ["sfu: presin r3", main_word("sin"), main_word("sin"),
         main_word("sin")]))
    out.append(pair_variant(
        "rearm", ["sfu: presin r3", main_word("sin"), "sfu: presin r3",
                  main_word("sin")]))
    out.append(pair_variant(
        "rearm-other", ["sfu: presin r3", main_word("sin"),
                        "sfu: preex2 r0", main_word("sin")],
        primes=(0.75, 0.5, 1.0, 1.0)))

    # G. two chains in flight, results in r1 and r2
    # 
    # Two chains in flight: r0 and r1 hold the operands, the results go to r2
    # and r3, and the store already covers that pair.
    out.append(pair_variant(
        "interleave-sin-ex2",
        ["sfu: presin r0", "sfu: preex2 r1", main_word("sin", "r0", "r2"),
         main_word("ex2", "r1", "r3")],
        primes=(1.0, -1.0, 1.0, 0.5)))
    out.append(pair_variant(
        "interleave-ex2-sin",
        ["sfu: preex2 r1", "sfu: presin r0", main_word("ex2", "r1", "r2"),
         main_word("sin", "r0", "r3")],
        primes=(1.0, -1.0, 0.75, 0.5)))
    out.append(pair_variant(
        "interleave-two-sin",
        ["sfu: presin r0", "sfu: presin r1", main_word("sin", "r0", "r2"),
         main_word("sin", "r1", "r3")],
        primes=(1.0, 1.0, 0.75, 0.5)))
    out.append(pair_variant(
        "interleave-gap",
        ["sfu: presin r0", "sfu: preex2 r1", NOP, NOP, NOP,
         main_word("sin", "r0", "r2"), main_word("ex2", "r1", "r3")],
        primes=(1.0, -1.0, 1.0, 0.5)))
    return out


SETS = {
    "ops": ("sfu-opcode-matrix", "tests/grate/asm_characterize.sfu_ops.json",
            TEMPLATE, ops_variants),
    "pair": ("sfu-pre-pairing", "tests/grate/asm_characterize.sfu_pair.json",
             PAIR_TEMPLATE, pair_variants),
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


def build_manifest(name, variants, fs):
    return {
        "name": name,
        "repeat": 3,
        "timeout": 10,
        "program": {
            "vs": "tests/grate/asm/vs_mov.txt",
            "fs": fs,
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
    parser.add_argument("--set", choices=sorted(SETS), default="ops",
                        help="which probe set to generate (default: ops)")
    parser.add_argument("--results", help="run directory to pin expectations from")
    parser.add_argument("--out", help="manifest to write (default: the checked-in one)")
    args = parser.parse_args()

    name, default_out, fs, build = SETS[args.set]
    manifest = build_manifest(name, build(), fs)
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
        print(f"{entry['name']:28} {entry.get('expected', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
