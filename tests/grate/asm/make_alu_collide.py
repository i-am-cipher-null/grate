#!/usr/bin/env python3
"""Generate the ALU same-register collision manifest.

The forwarding set measured what several writers into one half do when the
writers use `.l*` / `.*h` (fx10): the codes OR together, order does not matter,
and two 0.25 writes stay 0.25. It left three things open, and two of them are
what this set asks:

  fp20      two writers that both use `.lh` (the fp20 destination) - the
            forwarding set never wrote a value an fp20 read could see;
  mixed     one fx10 writer and one fp20 writer on the same register, in both
            orders, so a writer that clears the other format is visible;
  aluN      writing the result latches directly. Not testable: a 64-bit literal
            in an ALU slot is not a standalone instruction (section 16), and the
            grammar refuses `aluN` as a destination, so this set records the
            blocker rather than a measurement.

Readback: batch 1 reads the register as fp20 and writes the fx10 conversion into
`r2.lo` (and `r3`, primed to 0.75, into `r2.hi`), so the stored word's byte 0 is
the collided value and bytes 2..3 are the marker.

Variants:

  lh-one         one `.lh` writer of 0.25  - the readback baseline
  lh-same        the same value twice
  lh-two         0.25 then 0.5
  lh-rev         0.5 then 0.25  - order
  lh-three       0.25, 0.5, 0.75
  fx10-then-fp20 `.l*` 0.75, then `.lh` 0.25
  fp20-then-fx10 `.lh` 0.25, then `.l*` 0.75

`--results DIR` pins each variant to the word its repeats agreed on.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/alu_collide.in"
OUT = "tests/grate/asm_characterize.alu_collide.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"


def lh(value):
    return f"MAD  r2.lh,  u{value},  #1,  #0"


def fx10(value):
    return f"MAD  r2.l*,  u{value},  #1,  #0"


VARIANTS = [
    ("lh-one", [lh(0), NOP],
     "a single fp20 writer of 0.25: the readback baseline"),
    ("lh-same", [lh(0), lh(0)],
     "0.25 twice"),
    ("lh-two", [lh(0), lh(2)],
     "0.25 then 0.5"),
    ("lh-rev", [lh(2), lh(0)],
     "0.5 then 0.25 - the order swap"),
    ("lh-three", [lh(0), lh(2), lh(4)],
     "0.25, 0.5 then 0.75 in three slots"),
    ("fx10-then-fp20", [fx10(4), lh(0)],
     "an fx10 write of 0.75, then an fp20 write of 0.25"),
    ("fp20-then-fx10", [lh(0), fx10(4)],
     "an fp20 write of 0.25, then an fx10 write of 0.75"),
]


def substitute(path, mapping):
    text = pathlib.Path(path).read_text()
    for key, value in mapping.items():
        if text.count(key) != 1:
            raise SystemExit(f"{path}: {key} occurs {text.count(key)} times")
        text = text.replace(key, value, 1)
    return text


def variants():
    out = []
    for name, slots, note in VARIANTS:
        slots = list(slots) + [NOP] * (3 - len(slots))
        out.append({
            "name": name,
            "fs": TEMPLATE,
            "replace": {"fs": {"W0": slots[0], "W1": slots[1],
                                "W2": slots[2]}},
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
        "name": "alu-collide",
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
