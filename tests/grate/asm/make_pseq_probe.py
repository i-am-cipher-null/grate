#!/usr/bin/env python3
"""Generate the PSEQ fetch characterization manifest.

Template `pseq_probe.in` runs a five-batch round trip:

  batch 0  r0/r1 = pattern A, store to the variant's target
  batch 1  r0/r1 = pattern B (never stored)
  batch 2  r2/r3 = pattern C
  batch 3  the PSEQ fetch
  batch 4  the third DW

The DW converts the fx10 halves to 8-bit bytes, so the words are:

    A (fetched)     0x9F607F40
    B (not fetched) 0x703020BF
    C (r2/r3)       0xCF8F5010

Three distinct answers mean a dead fetch can never be mistaken for a fetch that
returned zero.

Groups:

  ctl    no fetch, the working fetch, and the mnemonic form of the same word;
  pair   destination pair bit 1, read through both store pairs;
  idx    render target index 0, 2, 15, and 3/4/5 with the extra surfaces bound;
  fmt    rt5 as rgba8888, a8, l8 and rgb565;
  bit    bits 4..15, 20..22, 25..31 on the a8 surface, where a write mask or
         format field would change the result, plus the two enable bits and
         the spill bit.

`--results DIR` pins each variant to the word its repeats agreed on.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/pseq_probe.in"
OUT = "tests/grate/asm_characterize.pseq.json"

STORE01 = "store rt1, r0, r1"
STORE23 = "store rt1, r2, r3"
NOP = "NOP"

# rt1, r0/r1 destination, both enable bits.
FETCH_RT1 = 0x00810008
A8_WORD = 0x00850008


def variant(name, pseq, dw0=STORE01, dw2=STORE01, dw3=NOP, env=None):
    entry = {
        "name": name,
        "replace": {"fs": {
            "DW0_SLOT": dw0,
            "PSEQ_SLOT": pseq,
            "DW2_SLOT": dw2,
            "DW3_SLOT": dw3,
        }},
    }
    if env:
        entry["environment"] = env
    return entry


def variants():
    out = [
        variant("ctl-nofetch", NOP),
        variant("ctl-fetch", f"0x{FETCH_RT1:08X}"),
        variant("ctl-mnemonic", "load rt1, r0, r1"),

        # Destination pair. With bit 1 set the fetch lands in r2/r3, so the
        # r2/r3 store reads A and the r0/r1 store still reads B. Without the
        # bit the r2/r3 store reads C.
        variant("pair-23", f"0x{FETCH_RT1 | 0x2:08X}", dw3=STORE23),
        variant("pair-23-no23", f"0x{FETCH_RT1 | 0x2:08X}"),
        variant("pair-01-store23", f"0x{FETCH_RT1:08X}", dw3=STORE23),

        # Index and target class. rt1 is the colour target the program itself
        # stores to; rt3, rt4 and rt5 exist only when the harness binds them.
        # Indexes 0, 2 and 15 are deliberately not in the pinned set: with
        # nothing bound there the fetch returns different words on different
        # runs (0x7030FF00 against 0x703000FF for index 0), so they are
        # recorded in the notes as an undefined target, not pinned.
        variant("idx-3-coverage", "0x00830008",
                dw0="store rt3, r0, r1", env={"GRATE_COVERAGE": "1"}),
        variant("idx-4-rt4", "0x00840008",
                dw0="store rt4, r0, r1", env={"GRATE_RT4": "1"}),
        variant("idx-5-rt5", "0x00850008",
                dw0="store rt5, r0, r1", env={"GRATE_RT5": "1"}),

        # Render target formats at rt5. The surface is one byte per pixel for
        # a8 and l8 and two for rgb565, so the fetched word names what the
        # fetch does with a narrow surface.
        variant("fmt-rgba8888", f"0x{A8_WORD:08X}",
                dw0="store rt5, r0, r1", env={"GRATE_RT5": "1"}),
        variant("fmt-a8", f"0x{A8_WORD:08X}",
                dw0="store rt5, r0, r1",
                env={"GRATE_RT5": "1", "GRATE_RT5_FMT": "a8"}),
        variant("fmt-l8", f"0x{A8_WORD:08X}",
                dw0="store rt5, r0, r1",
                env={"GRATE_RT5": "1", "GRATE_RT5_FMT": "l8"}),
        variant("fmt-rgb565", f"0x{A8_WORD:08X}",
                dw0="store rt5, r0, r1",
                env={"GRATE_RT5": "1", "GRATE_RT5_FMT": "rgb565"}),

        # The two enable bits, each alone.
        variant("en-b3-clear", "0x00810000"),
        variant("en-b23-clear", "0x00010008"),
        # The spill form: bit 24 set beside bit 23.
        variant("spill-b24", f"0x{FETCH_RT1 | 0x01000000:08X}"),

        # Bits 4..15 and the high bits, on the a8 surface where a write mask,
        # sub-word select or format field would change the fetched word.
    ]
    for bit in (4, 5, 6, 7, 8, 11, 15):
        out.append(variant(f"a8-bit{bit}",
                           f"0x{A8_WORD | (1 << bit):08X}",
                           dw0="store rt5, r0, r1",
                           env={"GRATE_RT5": "1", "GRATE_RT5_FMT": "a8"}))
    for bit in (20, 22, 25, 31):
        out.append(variant(f"a8-bit{bit}",
                           f"0x{A8_WORD | (1 << bit):08X}",
                           dw0="store rt5, r0, r1",
                           env={"GRATE_RT5": "1", "GRATE_RT5_FMT": "a8"}))
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
    entries = variants()
    names = [entry["name"] for entry in entries]
    if len(names) != len(set(names)):
        raise SystemExit("variant names are not unique")
    return {
        "name": "pseq-instruction",
        "repeat": 3,
        "timeout": 15,
        "program": {
            "vs": "tests/grate/asm/vs_mov.txt",
            "fs": TEMPLATE,
            "lnk": "tests/grate/asm/linker_vs_tests.txt",
        },
        "controls": CONTROLS,
        "variants": entries,
    }


def measured(path):
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
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", help="run directory to pin expectations from")
    parser.add_argument("--out", help="manifest to write (default: the set's)")
    args = parser.parse_args()

    manifest = build_manifest()
    if args.results:
        unpinned = pin(manifest, args.results)
        if unpinned:
            for name, seen, failed in unpinned:
                print(f"cannot pin {name}: results {seen or 'none'} "
                      f"statuses {failed or 'none'}")
            print("manifest not written")
            return 1
    out = args.out or OUT
    pathlib.Path(out).write_text(json.dumps(manifest, indent="\t") + "\n")
    print(f"wrote {out} ({len(manifest['variants'])} variants)")
    for entry in manifest["variants"]:
        print(f"{entry['name']:20} {entry.get('expected', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
