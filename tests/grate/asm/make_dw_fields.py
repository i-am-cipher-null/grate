#!/usr/bin/env python3
"""Generate the DW field-group manifest.

Section 8 of the ISA reference records every DW bit that was flipped on its own:
0..5, 10 and 15..17 have a function, and 6..9, 11..14 and 18..31 were "inert".
Two things make that sweep weaker than it looks, and both are what this set
addresses:

  prefill   a store that is dropped and a store that writes the value the target
            already holds are indistinguishable on a cleared target. Every
            variant here stores a distinct prefill pattern in batch 0 and the
            payload in batch 1, so a surviving prefill byte is a positive
            observation of "this byte was not written", not an absence;
  groups    the render-target class field is the cautionary example: flipping its
            two bits one at a time suggested "bit 16 kills the write, bit 17 is
            a don't-care", while the field only makes sense as a 2-bit value.
            The remaining multi-bit candidates are set as groups here - 6..9,
            11..14, 18..19, 23..25 (the bits the compiler's dw_enc_src() renders
            as operand slices) and 18..31 together - because a mask or a mode
            field would not show up under single-bit flips either.

Store modes 2 and 3 and class 1 are included as *positive* controls: with a
prefill in place, "the colour keeps the prefill" is a measurement of where those
stores went, not a measurement of a clear.

The group test then raised a question of its own: 11..14 as a group leaves the
payload all zero, while the individual bits were recorded as inert. The `bit-*`
variants split the four bits apart.

What could not be tested: whether bit 14 selects the *second row* of row
registers (`r4..r7`). Populating those needs `alu_buffer_size = 2`, and that
setting hangs the draw even for a one-batch program whose only work is one
`MAD` and a store - the same blocker [[Compiler Internals]] records for its own
bit-14 attempt, now measured rather than inferred. `row1-size1` is kept as the
record of that: the same program at size 1 returns, so the hang is the size.

Variants:

  prefill-only   batch 1 stores NOP - the prefill is what the target holds
  all            `store rt1, r2, r3` (0x00028005) - all four bytes replaced
  group-6-9      bits 6..9 set
  group-11-14    bits 11..14 set
  group-18-19    bits 18..19 set
  group-23-25    bits 23..25 set
  group-18-31    bits 18..31 set
  class-1        bits 16..17 = 1 - a colour store with no depth surface bound
  mode-0         bits 1..0 = 0 - nothing is written
  mode-2         bits 1..0 = 2 - spill write of one register
  mode-3         bits 1..0 = 3 - spill write of several registers
  bit-11/12/13/14  the four bits of the group one at a time
  bit-14-15      bit 14 with the pair field set too - does 15 change it
  bit-14-pair0   bit 14 with bit 15 clear, so the other pair is named

`--results DIR` pins each variant to the word its repeats agreed on.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/dw_fields.in"
OUT = "tests/grate/asm_characterize.dw_fields.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

BASE = 0x00028005          # store rt1, r2, r3: index 1, class 2, pair r2/r3

NOP = "NOP"


def word(value):
    return f"0x{value:08X}"


VARIANTS = [
    ("prefill-only", NOP,
     "batch 1 stores nothing: the prefill pattern 0x44332211 is what survives"),
    ("all", word(BASE),
     "the plain store replaces all four bytes of the prefill"),
    ("group-6-9", word(BASE | 0x3C0),
     "bits 6..9 as a group of four"),
    ("group-11-14", word(BASE | 0x7800),
     "bits 11..14 as a group of four"),
    ("group-18-19", word(BASE | 0x000C0000),
     "bits 18..19 - two of the slices dw_enc_src() returns"),
    ("group-23-25", word(BASE | 0x03800000),
     "bits 23..25 - the other operand slices, plus bit 24"),
    ("group-18-31", word(BASE | 0xFFFC0000),
     "every bit above the class field at once"),
    ("class-1", word((BASE & ~0x00030000) | 0x00010000),
     "class 1 on a colour target with no depth surface bound"),
    ("mode-0", word(BASE & ~0x3),
     "write mode 0 - expected to write nothing"),
    ("mode-2", word((BASE & ~0x3) | 0x2),
     "write mode 2, the spill write of one register"),
    ("mode-3", word((BASE & ~0x3) | 0x3),
     "write mode 3, the spill write of several registers"),
    ("bit-11", word(BASE | 0x0800),
     "bit 11 alone, from a group that wrote a zero payload together"),
    ("bit-12", word(BASE | 0x1000),
     "bit 12 alone"),
    ("bit-13", word(BASE | 0x2000),
     "bit 13 alone"),
    ("bit-14", word(BASE | 0x4000),
     "bit 14 alone - the documented zero-payload case"),
    ("bit-14-15", word(BASE | 0xC000),
     "bits 14 and 15 - whether the pair bit changes the zero payload"),
    ("bit-14-pair0", word((BASE & ~0x8000) | 0x4000),
     "bit 14 with bit 15 clear: the other pair is named, and the payload "
     "stays zero if 14 is a source select rather than a pair bit"),
]


def substitute(path, mapping):
    text = pathlib.Path(path).read_text()
    for key, value in mapping.items():
        if text.count(key) != 1:
            raise SystemExit(f"{path}: {key} occurs {text.count(key)} times")
        text = text.replace(key, value, 1)
    return text


def variants():
    return [{
        "name": name,
        "fs": TEMPLATE,
        "replace": {"fs": {"TEST_WORD": store}},
        "expected_note": note,
    } for name, store, note in VARIANTS]


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
        "name": "dw-fields",
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
        print(f"{entry['name']:16} {entry.get('expected', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
