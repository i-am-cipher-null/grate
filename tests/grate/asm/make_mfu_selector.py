#!/usr/bin/env python3
"""Generate the MFU multiplier source-selector manifest.

The MFU multiplier takes two 4-bit source numbers. The documented meaning is
0..3 = row registers r0..r3, 10 = the SFU result, 11/12 = the barycentric
coefficients, 13 = 1.0, and the rest unknown. Earlier hand probes measured that
table with programs that carried interpolation slots and several MFU words; this
probe isolates one selector at a time.

Every variant is one program of three batches at alu_buffer_size 1:

    batch 0   write 0.0625, 0.125, 0.25 and 0.375 into r0..r3.low
    batch 1   exactly one MFU word: mul0: r2, <source under test>, #1
    batch 2   store rt1, r2, r3

so the readback is the product the multiplier wrote into r2. If the multiplier
reads row register N it returns byte N of the constants table, and values 4..9,
14 and 15 have no row register to name, so whatever they return is not a primed
value - that is the separation this probe relies on.

The store word is r3.hi<<24 | r3.lo<<16 | r2.hi<<8 | r2.lo, so only its low 16
bits belong to the multiplier's destination; the 0x60 in byte 2 is r3's primed
0.375 and doubles as a marker that the prime arrived.

    make_mfu_selector.py                       # every source, no expectations
    make_mfu_selector.py --results DIR         # pin it to the words measured in DIR

Controls: `none` has no MFU word at all (the store must return the primed 0x40,
so the store path is not what changed) and `noprime-src0` has the prime batch
emptied (src0 must return 0, so a non-zero src0 word cannot be credited to stale
row state). `two-op-*` variants multiply two primed registers, which pins the
readout scaling independently of the 1.0 constant, and `const13-times-src0`
uses source 13 as the first operand - if 13 is the constant 1.0 it must return
the src0 plate exactly.
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


def prime_slots(enabled=True):
    out = []
    for index, const in enumerate(PRIME):
        out.append(f"MAD  r{index}.l*,  u{const},  #1,  #0" if enabled else NOP)
    return out


def variant(name, word=None, primed=True):
    """One variant: the same program, only the single MFU word differs."""
    slots = prime_slots(primed)
    replace = {f"@P{index}@": slot for index, slot in enumerate(slots)}
    replace["@MFU@"] = word or NOP
    return {
        "name": name,
        "environment": {"GRATE_POKE": f"0x520=0x{START_WORD | BATCHES:08X}"},
        "replace": {"fs": replace},
        "fs": TEMPLATE,
    }


def source_words():
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
    return out


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


def build_manifest():
    variants = [variant(name, word, primed) for name, word, primed in source_words()]
    return {
        "name": "mfu-selector-source",
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
    parser.add_argument("--results", help="run directory to pin expectations from")
    parser.add_argument("--out", help="manifest to write (default: the checked-in one)")
    args = parser.parse_args()

    manifest = build_manifest()
    out = args.out or "tests/grate/asm_characterize.mfu_selector_src.json"
    if args.results:
        unpinned = pin(manifest, args.results)
        if unpinned:
            for name, seen, failed in unpinned:
                print(f"cannot pin {name}: results {seen or 'none'} statuses {failed or 'none'}")
            print("manifest not written")
            return 1
    pathlib.Path(out).write_text(json.dumps(manifest, indent="\t") + "\n")
    print(f"wrote {out} ({len(manifest['variants'])} variants)")
    for entry in manifest["variants"]:
        print(f"{entry['name']:22} {entry.get('expected', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
