#!/usr/bin/env python3
"""Generate the PSEQ bit-24 half-selector manifest.

Section 25 of the notes measured a handful of combinations of the bits that
choose what the bit-24 form writes, and the reference turned them into a rule -
"bit 2 adds the second half, bits 0 and 23 redirect to it". Four cells that the
rule predicted were then run and none of them matched:

  {0,2}      wrote r1.lo, not r1.hi
  {0,2,3}    wrote r1.lo and left r1.hi at 0x09, not zero
  {2,3,23}   wrote r1.lo = 0xFF and zeroed r1.hi
  {0,3,23}   zeroed r0.lo and r1.hi

Two problems made those cells unreadable in the earlier set: the primes put the
same pair in r0 and r1, so a byte could not be attributed to a register, and the
table only covered eight of the sixteen combinations. This manifest runs the
whole four-bit grid with all four halves primed differently, so each cell names
the bytes it writes and the value it writes into them.

Bit 1 is left out: it suppresses the write (two cells in the earlier set), and
it is not one of the four selector bits.

Variants are named by the bits they set beside 24, in the order 3, 2, 0, 23.

`--set value` emits the other manifest instead: the two words that read a
non-zero byte in the earlier program shape, run against that shape
(`pseq_spill.in`, identical primes, an extra NOP fetch batch), so the claim has
a recorded run behind it.

`--results DIR` pins each variant to the word its repeats agreed on.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/pseq_spill_sel.in"
OUT = "tests/grate/asm_characterize.pseq_spill_sel.json"
OLD_TEMPLATE = "tests/grate/asm/pseq_spill.in"
OLD_OUT = "tests/grate/asm_characterize.pseq_spill_value.json"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

SELECTOR_BITS = (0x8, 0x4, 0x1, 0x800000)     # bit 3, bit 2, bit 0, bit 23


def cells():
    out = []
    for value in range(16):
        bits = [SELECTOR_BITS[i] for i in range(4) if value & (1 << (3 - i))]
        name = "sel-" + ("-".join({0x8: "b3", 0x4: "b2",
                                   0x1: "b0", 0x800000: "b23"}[b]
                                  for b in bits) or "none")
        out.append((name, 0x01000000 | sum(bits)))
    return out


def word(value):
    return f"0x{value:08X}"


def variants():
    out = []
    for name, bits in cells():
        out.append({
            "name": name,
            "fs": TEMPLATE,
            "replace": {"fs": {"SEL_WORD": word(bits)}},
            "environment": {"GRATE_RT4": "1"},
            "expected_note": f"bits {name[4:]} beside 24",
        })
    return out


VALUE_WORDS = [
    ("value-3-2-0", 0x0100000D),
    ("value-3-2-23", 0x0180000C),
]


def old_variants():
    return [{
        "name": name,
        "fs": OLD_TEMPLATE,
        # The older template's PSEQ slot is FETCH_WORD; the word under test
        # goes there, and the follow-up fetch batch stays a NOP.
        "replace": {"fs": {"FETCH_WORD": word(bits),
                            "FINAL_WORD": NOP,
                            "STORE_WORD": STORE01}},
        "environment": {"GRATE_RT4": "1"},
        "expected_note": f"word {word(bits)} in the earlier program shape",
    } for name, bits in VALUE_WORDS]


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


def build_manifest(shape="grid"):
    entries = old_variants() if shape == "value" else variants()
    names = [entry["name"] for entry in entries]
    if len(names) != len(set(names)):
        raise SystemExit("variant names are not unique")
    return {
        "name": "pseq-spill-sel" if shape == "grid" else "pseq-spill-value",
        # Five repeats: one draw in six of the earlier three-repeat set came
        # back with the tag batch's constant 0x50 in both low halves instead
        # of the primed values, so a single disagreement must be visible.
        "repeat": 5,
        "timeout": 20,
        "program": {"vs": VS,
                    "fs": TEMPLATE if shape == "grid" else OLD_TEMPLATE,
                    "lnk": LNK},
        "controls": CONTROLS,
        "variants": entries,
    }


def measured(path):
    """Tally every variant draw, and the draws that produced no word at all.

    The runner compares each repeat against the manifest's `expected` and exits
    non-zero when a repeat differs, so a recorded word with a failing status is
    a dissent to be counted, not a broken run. A draw that produced no word
    (timeout, assembler error) is a failure and blocks pinning.
    """
    counts = {}
    failures = {}
    with (pathlib.Path(path) / "results.tsv").open() as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            if row["phase"] != "variant":
                continue
            name = row["name"]
            if not row["result"]:
                failures.setdefault(name, set()).add(row["status"])
                continue
            key = (name, row["result"])
            counts[key] = counts.get(key, 0) + 1
    return counts, failures


def pin(manifest, results):
    counts, failures = measured(results)
    unpinned = []
    for entry in manifest["variants"]:
        name = entry["name"]
        tallies = sorted((n, w) for (v, w), n in counts.items() if v == name)
        if name in failures or not tallies:
            unpinned.append((name,
                             [w for _, w in tallies],
                             sorted(failures.get(name, ()))))
            continue
        best, value = max(tallies)
        total = sum(n for n, _ in tallies)
        if best * 2 <= total:
            unpinned.append((name, [w for _, w in tallies],
                             sorted(failures.get(name, ()))))
            continue
        entry["expected"] = value
        if len(tallies) > 1:
            # Reported, not recorded: the note would change the manifest from
            # run to run, and the manifest has to re-pin byte for byte.
            print("warning: {} dissented ({} of {} draws, {})".format(
                name, best, total,
                ", ".join(w for _, w in tallies if w != value)))
    return unpinned


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", choices=("grid", "value"), default="grid",
                        help="which manifest to emit (default: grid)")
    parser.add_argument("--results", help="run directory to pin from")
    parser.add_argument("--out", help="manifest to write (default: the set's)")
    args = parser.parse_args()

    manifest = build_manifest(args.set)
    if args.results:
        unpinned = pin(manifest, args.results)
        if unpinned:
            for name, seen, failed in unpinned:
                print(f"cannot pin {name}: results {seen or 'none'} "
                      f"statuses {failed or 'none'}")
            print("manifest not written")
            return 1
    out = args.out or (OLD_OUT if args.set == "value" else OUT)
    pathlib.Path(out).write_text(json.dumps(manifest, indent="\t") + "\n")
    print(f"wrote {out} ({len(manifest['variants'])} variants)")
    for entry in manifest["variants"]:
        print(f"{entry['name']:18} {entry.get('expected', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
