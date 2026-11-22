#!/usr/bin/env python3
"""Generate the PSEQ spill-form manifest.

Section 9 of the ISA reference ends with "bit 24 with bit 23 set selects the
spill-fetch form, whose write mask and slot fields overlay the low bits of this
word (section 9.2)" - and there is no 9.2. The form itself was never measured:
what is known is only that setting bit 24 on an otherwise working render-target
fetch suppresses the fetch, and that bits 4..15 are inert in the
render-target form.

This set asks the question from the outside, with the round trip section 9 uses:
batch 0 puts a tag word in rt4, batch 1 issues the word under test, batch 2
stores r0/r1 to colour. A word that fetches shows the tag; a word that does
nothing shows the "did nothing" pattern (0x00002010).

Variants:

  rt4-fetch      0x00840008, the render-target form for index 4 - the control
                 that says the round trip works at all
  nofetch        NOP - the pattern the target keeps if nothing fetches
  rt4-index0     0x00800008, the same word with index 0 - does the index still
                 select with this round trip
  spill-24       0x01000008, bit 24 with the low enable bit
  spill-24-23    0x01800008, bit 24 and bit 23 - the pair the reference names
  spill-b4       .. plus bit 4    - a candidate low-bit overlay
  spill-b12      .. plus bit 12
  spill-b15      .. plus bit 15
  spill-low      .. plus bits 4..15 together
  spill-b0/b2    the two bits the word layout marks "unknown", with bit 24
  spill-pair     bit 24 and bit 1 (the destination-pair select), with r2/r3 read
                 instead, so a pair select would be visible
  spill-b1       0x0100000A on its own, the pair bit without bit 23
  spill-nolow    bit 24 with the low enable bit *clear*, and the same for the
                 half-selecting bits, to see whether the low enable bit is part
                 of the mask
  *-then-fetch  the word under test followed by a render-target fetch of rt4,
                 which separates "the spill form read a zero location" from
                 "the spill form wrote the surface"

`--results DIR` pins each variant to the word its repeats agreed on.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/pseq_spill.in"
OUT = "tests/grate/asm_characterize.pseq_spill.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"
STORE23 = "store rt1, r2, r3"
FETCH_RT4 = 0x00840008

VARIANTS = [
    ("rt4-fetch", 0x00840008, STORE01, NOP,
     "the render-target form for index 4: the tag must land in r0/r1"),
    ("nofetch", NOP, STORE01, NOP,
     "no fetch: the r0/r1 pattern is what the store sees"),
    ("rt4-index0", 0x00800008, STORE01, NOP,
     "index 0 with the same enables, on a target nothing wrote"),
    ("spill-24", 0x01000008, STORE01, NOP,
     "bit 24 with the low enable bit only"),
    ("spill-24-23", 0x01800008, STORE01, NOP,
     "bit 24 and bit 23 - the form the reference names"),
    ("spill-b4", 0x01800018, STORE01, NOP,
     "bit 24, 23 and bit 4 - a candidate low-bit overlay"),
    ("spill-b12", 0x01809008, STORE01, NOP,
     "bit 24, 23 and bit 12"),
    ("spill-b15", 0x01888008, STORE01, NOP,
     "bit 24, 23 and bit 15"),
    ("spill-low", 0x0180FFF8, STORE01, NOP,
     "bit 24, 23 and every bit 4..15"),
    ("spill-b0", 0x01000009, STORE01, NOP,
     "the low word bit 0 set beside bit 24"),
    ("spill-b2", 0x0100000C, STORE01, NOP,
     "the low word bit 2 set beside bit 24"),
    ("spill-b1", 0x0100000A, STORE01, NOP,
     "the low word bit 1 set beside bit 24, without bit 23"),
    ("spill-pair", 0x0180000A, STORE23, NOP,
     "bit 24, 23 and bit 1, with r2/r3 read instead"),
    ("spill-nolow", 0x01000000, STORE01, NOP,
     "bit 24 with no low bit at all - does the mask still write a half"),
    ("spill-b23-nolow", 0x01800000, STORE01, NOP,
     "bit 24 and bit 23, low bits clear"),
    ("spill-b2-nolow", 0x01000004, STORE01, NOP,
     "bit 24 and bit 2, low enable clear"),
    ("spill-b0-nolow", 0x01000001, STORE01, NOP,
     "bit 24 and bit 0, low enable clear"),
    ("spill-24-then-fetch", 0x01000008, STORE01, FETCH_RT4,
     "the spill word, then rt4 read back through the render-target form"),
    ("spill-24-23-then-fetch", 0x01800008, STORE01, FETCH_RT4,
     "the bit-23 form, then rt4 read back"),
    ("tag-then-fetch", NOP, STORE01, FETCH_RT4,
     "its control: no spill word, so the fetch must return the tag"),
]


def word(value):
    return f"0x{value:08X}"


def variants():
    out = []
    for name, fetch, store, final, note in VARIANTS:
        out.append({
            "name": name,
            "fs": TEMPLATE,
            "replace": {"fs": {"FETCH_WORD": (NOP if fetch is NOP
                                              else word(fetch)),
                               "FINAL_WORD": (NOP if final is NOP
                                              else word(final)),
                               "STORE_WORD": store}},
            "environment": {"GRATE_RT4": "1"},
            "expected_note": note,
        })
    return out


CONTROLS = [
    {
        "name": "immediate-operand",
        "vs": VS,
        "fs": "tests/grate/asm/fs_alu_immediate_control.txt",
        "lnk": LNK,
        "expected": "0x000000FF",
    },
    {
        "name": "varying-pipeline",
        "vs": VS,
        "fs": "tests/grate/asm/fs_vs_tests.txt",
        "lnk": LNK,
        "expected": "0xFF01FF00",
    },
]


def build_manifest():
    entries = variants()
    names = [entry["name"] for entry in entries]
    if len(names) != len(set(names)):
        raise SystemExit("variant names are not unique")
    return {
        "name": "pseq-spill",
        "repeat": 3,
        "timeout": 20,
        "program": {"vs": VS, "fs": TEMPLATE, "lnk": LNK},
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
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", help="run directory to pin from")
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
        print(f"{entry['name']:14} {entry.get('expected', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
