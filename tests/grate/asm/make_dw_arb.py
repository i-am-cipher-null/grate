#!/usr/bin/env python3
"""Generate the DW arbitration manifests: three words, and depth against colour.

Notes 29 and 33 measured two DW words in one scheduling group: the first word's
target is written with the group's final row, the second is dropped silently,
on colour, spill and MRT targets. Three open edges remain:

    --set three   three DW words in one group, and two words on one surface
    --set class   a depth-class word (bits 16..17 = 1) against a colour word

**three.** `asm/dw_three.in` is six batches at `alu_buffer_size` 3, so group A
is batches 0..2 - one DW per batch, three per group - and group B is batches
3..5, where batch 3 fetches one target into r0/r1 and a DW stores it to rt1, the
surface the runner reads. A variant is run once per target it needs read, only
the fetch word differing between those runs.

Each batch primes two row pairs, r0/r1 with A, B or C and r2/r3 with D, E or F,
so a `pair` variant can put the two words on different row pairs and the stored
word then names which pair's content the surviving store saw:

    A = 0x44332211   B = 0x3D2C1B0A   C = 0x7F6E5D4C
    D = 0x0F1E2D3C   E = 0x3F4E5D6C   F = 0x1F2A3B4C

The group's final content of pair 0 is C and of pair 1 is F, so `same-1-5-1`
reads C from rt1 if the first word won and F if the third did.

**class.** `asm/dw_class.in` is four batches at `alu_buffer_size` 2: group A
primes r0/r1 and r2/r3 and issues two DWs, group B fetches rt1 into r0/r1 and
stores it to rt1. `store rt1, r0, r1` is a class-2 colour word;
`store rt0, r0, r1 class 1` is the class-1 depth word 0x00010001.

The depth payload is primed into r3 because a class-1 store reads the register
of the pair bit 15 does not select, and only that pair's high register: r3.hi =
1.0 encodes 0x4000, while the rasterizer's own z for the full-screen quad is
0x8000. A dropped depth word therefore leaves 0x8000 and a written one gives
0x4000, which the `depth16 row0:` line of `GRATE_DEPTH=1` prints. The colour
target is read by the runner's result word as usual, so each variant reports
both surfaces at once.

Usage:

    tests/grate/asm/make_dw_arb.py --set three
    tests/grate/asm/make_dw_arb.py --set class
    tests/grate/asm/make_dw_arb.py --set three --results DIR   # pin from a run
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = {"three": "tests/grate/asm/dw_three.in",
            "class": "tests/grate/asm/dw_class.in",
            "size3": "tests/grate/asm/dw_size3.in",
            "class1p": "tests/grate/asm/dw_two_target.in"}
OUT = {"three": "tests/grate/asm_characterize.dw_three.json",
       "class": "tests/grate/asm_characterize.dw_class.json",
       "size3": "tests/grate/asm_characterize.dw_size3.json",
       "class1p": "tests/grate/asm_characterize.dw_class1p.json"}
NAME = {"three": "dw-three", "class": "dw-class",
        "size3": "dw-size3", "class1p": "dw-class1p"}
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE = {
    1: "store rt1, r0, r1",
    4: "store rt4, r0, r1",
    5: "store rt5, r0, r1",
}
STORE_PAIR = {
    1: "store rt1, r2, r3",
    4: "store rt4, r2, r3",
    5: "store rt5, r2, r3",
}
DEPTH = "store rt0, r0, r1 class 1"
FETCH = {target: f"load rt{target}, r0, r1" for target in (1, 4, 5)}
READBACK = "store rt1, r0, r1"

ENVIRONMENT = {
    "three": {"GRATE_RT4": "1", "GRATE_RT5": "1",
              "GRATE_DUMP_RAW": "1"},
    "class": {"GRATE_DEPTH": "1", "GRATE_DUMP_RAW": "1"},
    "size3": {"GRATE_RT4": "1", "GRATE_RT5": "1",
              "GRATE_DUMP_RAW": "1"},
    "class1p": {"GRATE_DEPTH": "1", "GRATE_DUMP_RAW": "1"},
}


def prime(first, consts):
    """Four low/high writes building a payload in two row registers."""
    second = "r1" if first == "r0" else "r3"
    return {
        0: f"MAD  {first}.l*,  u{consts[0]},  #1,  #0",
        1: f"MAD  {first}.*h,  u{consts[1]},  #1,  #0",
        2: f"MAD  {second}.l*,  u{consts[2]},  #1,  #0",
        3: f"MAD  {second}.*h,  u{consts[3]},  #1,  #0",
    }


# Payload bytes for `dw_three.in`: A/B/C in r0/r1, D/E/F in r2/r3.
PAIRS = {"A": (0, 1, 2, 3), "B": (4, 5, 6, 7), "C": (8, 9, 10, 11),
         "D": (12, 13, 14, 15), "E": (16, 17, 18, 19), "F": (20, 21, 22, 23)}
# `dw_class.in` primes r2/r3 in the second block: r3.lo = u14 = 0.0 and
# r3.hi = u15 = 1.0, which is the depth payload 0x4000.
DEPTH_PAIR = (12, 13, 14, 15)


NOP4 = {0: NOP, 1: NOP, 2: NOP, 3: NOP}


def three_variant(name, note, dws, fetch, pair0=True):
    """One `dw_three` program: A/D, B/E, C/F priming and three DW words."""
    return {"name": name, "note": note, "dws": dws, "fetch": fetch,
            "primes": [prime("r0", PAIRS["A"]), prime("r0", PAIRS["B"]),
                       prime("r0", PAIRS["C"])],
            "pairs": [prime("r2", PAIRS["D"]) if pair0 else dict(NOP4),
                      prime("r2", PAIRS["E"]), prime("r2", PAIRS["F"])]}


def three_run(name, dws, targets):
    """Both orders of a three-word group, read back one target per run."""
    out = []
    for target in targets:
        out.append(three_variant(
            f"{name}-read{target}",
            f"{dws[0]} / {dws[1]} / {dws[2]} in batches 0, 1 and 2; read "
            f"rt{target} through the fetch in batch 3",
            dws, FETCH[target]))
    return out


THREE_VARIANTS = (
    three_run("three-1-5-4", (STORE[1], STORE[5], STORE[4]), (1, 5, 4))
    + three_run("three-4-5-1", (STORE[4], STORE[5], STORE[1]), (4, 5, 1))
    + [
        three_variant("same-1-5-1-read1",
                      "rt1 (pair r0/r1) / rt5 / rt1 (pair r2/r3): the first "
                      "and third words name the same surface from different "
                      "row pairs, so 0x7F6E5D4C is pair 0's final content (C, "
                      "the first word won) and 0x1F2A3B4C is pair 1's (F, the "
                      "third won); read rt1",
                      (STORE[1], STORE[5], STORE_PAIR[1]), FETCH[1]),
        three_variant("same-1-5-1-read5",
                      "the same program, read rt5",
                      (STORE[1], STORE[5], STORE_PAIR[1]), FETCH[5]),
        three_variant("single-1-read1",
                      "one DW, rt1, in the group's first batch: 0x7F6E5D4C, "
                      "the group's final pair-0 content - the position control",
                      (STORE[1], NOP, NOP), FETCH[1]),
        three_variant("single-1-read5",
                      "the same program read at rt5: 0, untouched",
                      (STORE[1], NOP, NOP), FETCH[5]),
        three_variant("single-5-read5",
                      "one DW on rt5, in the group's first batch: the positive "
                      "control for the MRT target",
                      (STORE[5], NOP, NOP), FETCH[5]),
        three_variant("single-5-read1",
                      "the same program read at rt1: 0x4C4C4C4C, the clear",
                      (STORE[5], NOP, NOP), FETCH[1]),
        three_variant("single-4-read4",
                      "one DW on the spill surface rt4, in the group's first "
                      "batch: the positive control",
                      (STORE[4], NOP, NOP), FETCH[4]),
        three_variant("single-4-read1",
                      "the same program read at rt1: 0x4C4C4C4C",
                      (STORE[4], NOP, NOP), FETCH[1]),
        three_variant("pos-b1-1-read1",
                      "the group's only DW sits in batch 1, not in the group's "
                      "first batch: 0x4C4C4C4C - the position rule at size 3",
                      (NOP, STORE[1], NOP), FETCH[1]),
        three_variant("none-read1",
                      "no DW in the group: rt1's pre-draw content, the clear",
                      (NOP, NOP, NOP), FETCH[1]),
        three_variant("none-read4",
                      "no DW: rt4's baseline",
                      (NOP, NOP, NOP), FETCH[4]),
        three_variant("none-read5",
                      "no DW: rt5's baseline",
                      (NOP, NOP, NOP), FETCH[5]),
    ]
)


def class_variant(name, note, dws):
    return {"name": name, "note": note, "dws": dws, "fetch": FETCH[1],
            "primes": [prime("r0", PAIRS["A"]), prime("r0", PAIRS["B"])],
            "pairs": [prime("r2", DEPTH_PAIR), prime("r2", DEPTH_PAIR)]}


CLASS_VARIANTS = [
    class_variant("depth-then-colour",
                  "class-1 depth word in batch 0, class-2 colour word on rt1 in "
                  "batch 1: colour 0x4C4C4C4C and depth 0x4000 if the first "
                  "word wins, colour 0x3D2C1B0A and depth 0x8000 if the "
                  "second does",
                  (DEPTH, STORE[1])),
    class_variant("colour-then-depth",
                  "the same two words with the batches swapped: the same two "
                  "readings, with the target that wins swapped",
                  (STORE[1], DEPTH)),
    class_variant("single-depth",
                  "one class-1 depth word and nothing else: the positive "
                  "control for the depth class (depth 0x4000)",
                  (DEPTH, NOP)),
    class_variant("single-colour",
                  "one class-2 colour word on rt1: the positive control for "
                  "the colour side (colour 0x3D2C1B0A, depth 0x8000)",
                  (STORE[1], NOP)),
    class_variant("none",
                  "no DW in either batch: colour 0x4C4C4C4C and depth 0x8000, "
                  "both baselines in one run",
                  (NOP, NOP)),
]

def size3_variant(name, note, dws, fetch):
    """One `dw_size3` program: A, B, C priming and three DW words."""
    return {"name": name, "note": note, "dws": dws, "fetch": fetch,
            "primes": [prime("r0", PAIRS["A"]), prime("r0", PAIRS["B"]),
                       prime("r0", PAIRS["C"])]}


SIZE3_VARIANTS = [
    size3_variant("s3-single-1-read1",
                  "size 3, one packet per batch, one DW on rt1 in the group's "
                  "first batch: 0x7F6E5D4C if the position rule holds with one "
                  "packet per batch at this size",
                  (STORE[1], NOP, NOP), FETCH[1]),
    size3_variant("s3-single-5-read5",
                  "the same program with the one DW on rt5: 0x7F6E5D4C if the "
                  "MRT target is written here, 0 if size 3 loses it",
                  (STORE[5], NOP, NOP), FETCH[5]),
    size3_variant("s3-single-4-read4",
                  "the same on the spill surface rt4",
                  (STORE[4], NOP, NOP), FETCH[4]),
    size3_variant("s3-single-1-read5",
                  "the rt1 program read at rt5: 0, untouched-target control",
                  (STORE[1], NOP, NOP), FETCH[5]),
    size3_variant("s3-three-1-5-4-read1",
                  "three DW words on rt1, rt5, rt4 in one size-3 group with one "
                  "packet per batch: read rt1",
                  (STORE[1], STORE[5], STORE[4]), FETCH[1]),
    size3_variant("s3-three-1-5-4-read5",
                  "the same program, read rt5",
                  (STORE[1], STORE[5], STORE[4]), FETCH[5]),
    size3_variant("s3-three-1-5-4-read4",
                  "the same program, read rt4",
                  (STORE[1], STORE[5], STORE[4]), FETCH[4]),
    size3_variant("s3-none-read1",
                  "no DW in the group: the clear-colour baseline",
                  (NOP, NOP, NOP), FETCH[1]),
    size3_variant("s3-none-read5",
                  "no DW: the rt5 baseline, which is not cleared",
                  (NOP, NOP, NOP), FETCH[5]),
]

# A class-1 word stores the register of the pair bit 15 does not select, so
# `store rt0, r2, r3 class 1` reads r1 - which the one-packet template already
# primes. r1.lo = 0.19921875 and r1.hi = 0.265625 encode 0x1103, far from the
# rasterizer's 0x8000.
DEPTH_PAIR1 = "store rt0, r2, r3 class 1"


def class1p_variant(name, note, dws):
    return {"name": name, "note": note, "dws": dws, "fetch": FETCH[1],
            "primes": [prime("r0", PAIRS["A"]), prime("r0", PAIRS["B"])]}


CLASS1P_VARIANTS = [
    class1p_variant("c1p-depth-then-colour",
                    "one packet per batch, notes 33's shape: a class-1 depth "
                    "word (reading r1, payload 0x1103) in batch 0 and a class-2 "
                    "colour word on rt1 in batch 1; colour 0x4C4C4C4C with "
                    "depth 0x1103 if only the first word stores, colour 0 with "
                    "depth 0x1103 if the second word stores zeros",
                    (DEPTH_PAIR1, STORE[1])),
    class1p_variant("c1p-colour-then-depth",
                    "the same two words with the batches swapped: colour "
                    "0x3D2C1B0A with depth 0x8000 if only the first word "
                    "stores, depth 0x0000 if the second word stores zeros",
                    (STORE[1], DEPTH_PAIR1)),
    class1p_variant("c1p-single-depth",
                    "one class-1 word alone: the depth positive control",
                    (DEPTH_PAIR1, NOP)),
    class1p_variant("c1p-single-colour",
                    "one class-2 colour word alone: the colour positive control",
                    (STORE[1], NOP)),
    class1p_variant("c1p-none",
                    "no DW: both baselines",
                    (NOP, NOP)),
]

SETS = {"three": THREE_VARIANTS, "class": CLASS_VARIANTS,
        "size3": SIZE3_VARIANTS, "class1p": CLASS1P_VARIANTS}

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


def build_manifest(key, only=None):
    entries = []
    seen = set()
    for entry in SETS[key]:
        if entry["name"] in seen:
            raise SystemExit(f"duplicate variant {entry['name']}")
        seen.add(entry["name"])
        if only and entry["name"] not in only:
            continue
        # `dw_two_target.in` fixes alu_buffer_size at 2 and has no placeholder.
        replace = {} if key == "class1p" else {
            "@ABUF@": "3" if key in ("three", "size3") else "2"}
        if key == "class1p":
            # `dw_two_target.in` primes A and B itself and has four DW slots:
            # 0 and 1 are the words under test, 2 is the readback store.
            for index, dw in enumerate(entry["dws"]):
                replace[f"@DW{index}@"] = dw
            replace["@DW2@"] = READBACK
            replace["@DW3@"] = NOP
            replace["@FETCH@"] = entry["fetch"]
        elif key == "size3":
            for batch, prefix in ((0, "P"), (1, "Q"), (2, "R")):
                for slot in range(4):
                    replace[f"@{prefix}{slot}@"] = entry["primes"][batch][slot]
            for index, dw in enumerate(entry["dws"]):
                replace[f"@DW{index}@"] = dw
            replace["@FETCH@"] = entry["fetch"]
            replace["@STORE@"] = READBACK
        elif key == "three":
            for batch, prefix in ((0, "P"), (1, "Q"), (2, "R")):
                for slot in range(4):
                    replace[f"@{prefix}{slot}@"] = entry["primes"][batch][slot]
            for batch, prefix in ((0, "T"), (1, "U"), (2, "V")):
                for slot in range(4):
                    replace[f"@{prefix}{slot}@"] = entry["pairs"][batch][slot]
            for index, dw in enumerate(entry["dws"]):
                replace[f"@DW{index}@"] = dw
        else:
            for batch, (prefix, pair_prefix) in enumerate(
                    (("P", "X"), ("Q", "Y"))):
                for slot in range(4):
                    replace[f"@{prefix}{slot}@"] = entry["primes"][batch][slot]
                    replace[f"@{pair_prefix}{slot}@"] = \
                        entry["pairs"][batch][slot]
            for index, dw in enumerate(entry["dws"]):
                replace[f"@DW{index}@"] = dw
        if key != "class1p":
            replace["@FETCH@"] = entry["fetch"]
            replace["@STORE@"] = READBACK
        entries.append({
            "name": entry["name"],
            "fs": TEMPLATE[key],
            "replace": {"fs": replace},
            "environment": dict(ENVIRONMENT[key]),
            "expected_note": entry["note"],
        })
    if not entries:
        raise SystemExit("no variants selected")
    return {
        "name": NAME[key],
        "repeat": 3,
        "timeout": 20,
        "program": {"vs": VS, "fs": TEMPLATE[key], "lnk": LNK},
        "controls": CONTROLS,
        "variants": entries,
    }


def measured(path):
    counts = {}
    failures = {}
    with (pathlib.Path(path) / "results.tsv").open() as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            if row["phase"] != "variant":
                continue
            name = row["name"]
            if row["status"] != "pass" or not row["result"]:
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
        tallies = sorted((n, word) for (variant, word), n in counts.items()
                         if variant == name)
        if name in failures or not tallies:
            unpinned.append((name, [word for _, word in tallies],
                             sorted(failures.get(name, ()))))
            continue
        best, value = max(tallies)
        total = sum(n for n, _ in tallies)
        if best * 2 <= total:
            unpinned.append((name, [word for _, word in tallies],
                             sorted(failures.get(name, ()))))
            continue
        entry["expected"] = value
        if len(tallies) > 1:
            print("warning: {} dissented ({} of {} draws, {})".format(
                name, best, total,
                ", ".join(word for _, word in tallies if word != value)))
    return unpinned


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", dest="key", choices=sorted(SETS),
                        default="three", help="variant set to build")
    parser.add_argument("--results", help="run directory to pin from")
    parser.add_argument("--out", help="manifest to write (default: the set's)")
    parser.add_argument("--only", help="comma-separated variant names to keep")
    args = parser.parse_args()

    only = set(args.only.split(",")) if args.only else None
    manifest = build_manifest(args.key, only)
    out = args.out or OUT[args.key]
    if args.results:
        unpinned = pin(manifest, args.results)
        for name, words, statuses in unpinned:
            print(f"cannot pin {name}: results {words} statuses {statuses}")
        if unpinned:
            raise SystemExit("manifest not written")
    pathlib.Path(out).write_text(json.dumps(manifest, indent="\t") + "\n")
    print(f"wrote {out} ({len(manifest['variants'])} variants)")
    for entry in manifest["variants"]:
        print("{:22} {}".format(entry["name"], entry.get("expected", "-")))


if __name__ == "__main__":
    main()
