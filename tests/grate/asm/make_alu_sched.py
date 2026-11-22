#!/usr/bin/env python3
"""Generate the ALU schedule-bit manifest: bits 29 and 30 of the 0x801 words.

Both bits sit in the part of the schedule word the assembler never fills, so no
program can set them through the grammar. GRATE_ALU_SCHED_OR ORs a mask into
every schedule word as the stream is emitted (the 0x806 complement stream is
built from a different array and is untouched), and each variant here is the
same program with a different mask:

    0x00000000  control, should be the word the program has without the hook
    0x20000000  bit 29
    0x40000000  bit 30
    0x60000000  both

Four shapes, each sensitive to a different mechanism, so a word that changes
names the mechanism:

    chain   three slots of one packet chained through alu0/alu1, then a store.
            Forwarding or packet scheduling working differently shows up as a
            changed or missing byte.
    dw      one ALU write to r2 and a store of r2/r3 in the same batch. A
            schedule bit that gates the DW stage shows up as a lost store.
    row     a row write in batch 0 stored in batch 1. A schedule bit that
            changes how many ALU words a batch executes, or when they commit,
            shows up here rather than in the same-batch shapes.
    plain   nothing written, only a store. The baseline for "did the store
            itself survive".

Hypotheses and what each would look like:

    inert      all four masks give the same word for every shape. Expected if
               bits 29..31 belong to the later chips' wide schedule layout,
               where the assembler writes 0xe0 into that byte itself.
    gating     the store disappears when a bit is set (the dw and plain shapes
               separate "the ALU words changed" from "the DW word was dropped").
    misdecode  a shape returns a different value rather than nothing, e.g. the
               chain loses its second link.

`--results DIR` pins each variant to the modal word of its repeats.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/alu_sched.in"
OUT = "tests/grate/asm_characterize.alu_sched.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"
STORE23 = "store rt1, r2, r3"

U0 = 0

MASKS = [
    ("m0", 0x00000000, "no bits set: the control"),
    ("m29", 0x20000000, "bit 29"),
    ("m30", 0x40000000, "bit 30"),
    ("m2930", 0x60000000, "bits 29 and 30"),
]

SHAPES = [
    ("chain",
     {0: f"MAD  r2.l*,  u{U0},  #1,  #0",
      1: "MAD  r0.l*,  alu0.l,  #1,  #0",
      2: "MAD  r1.l*,  alu1.l,  #1,  #0"},
     {0: STORE01},
     "a two-link latch chain and a store in the same batch: 0x00100010 if both "
     "links read the previous slot"),
    ("dw",
     {0: f"MAD  r2.l*,  u{U0},  #1,  #0"},
     {0: STORE23},
     "one ALU write and its store in the same batch: 0x00001000 if the store "
     "runs"),
    ("row",
     {0: f"MAD  r2.l*,  u{U0},  #1,  #0"},
     {1: STORE23},
     "the write in batch 0 stored by batch 1: 0x00001000 if the write commits "
     "and the store runs"),
    ("plain",
     {},
     {0: STORE01},
     "nothing written, one store: the baseline for the store itself"),
]


def variant(shape, mask_tag, mask, note, slots, dws):
    return {
        "name": f"{shape}-{mask_tag}",
        "mask": mask,
        "slots": slots,
        "dws": dws,
        "note": f"{note}; schedule mask 0x{mask:08X}",
    }


VARIANTS = []
for shape, slots, dws, note in SHAPES:
    for tag, mask, mask_note in MASKS:
        VARIANTS.append(variant(shape, tag, mask,
                                f"{note} ({mask_note})", slots, dws))

# Every `chain` variant runs with GRATE_DUMP_RAW, so the run's own stderr
# records the schedule words that were uploaded with that mask. That is the
# evidence that the mask reached the engine: "bit 29 does nothing" and "the
# environment never reached the child" would otherwise look the same.
DUMP_SHAPES = ("chain",)

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
    names = [entry["name"] for entry in VARIANTS]
    if len(names) != len(set(names)):
        raise SystemExit("variant names are not unique")
    entries = []
    for entry in VARIANTS:
        replace = {}
        for batch, prefix in enumerate(("A", "B")):
            for slot in range(4):
                index = batch * 4 + slot
                replace[f"@{prefix}{slot}@"] = entry["slots"].get(index, NOP)
            replace[f"@D{batch}@"] = entry["dws"].get(batch, NOP)
        environment = {}
        if entry["mask"]:
            environment["GRATE_ALU_SCHED_OR"] = f"0x{entry['mask']:08X}"
        if entry["name"].startswith(DUMP_SHAPES):
            environment["GRATE_DUMP_RAW"] = "1"
        entries.append({
            "name": entry["name"],
            "fs": TEMPLATE,
            "replace": {"fs": replace},
            "environment": environment,
            "expected_note": entry["note"],
        })
    return {
        "name": "alu-sched",
        "repeat": 3,
        "timeout": 10,
        "program": {"vs": VS, "fs": TEMPLATE, "lnk": LNK},
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
            unpinned.append((name, [w for _, w in tallies],
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
            print("warning: {} dissented ({} of {} draws, {})".format(
                name, best, total,
                ", ".join(w for _, w in tallies if w != value)))
    return unpinned


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", help="run directory to pin from")
    parser.add_argument("--out", help="manifest to write (default: the set's)")
    args = parser.parse_args()

    manifest = build_manifest()
    out = args.out or OUT
    if args.results:
        unpinned = pin(manifest, args.results)
        for name, words, statuses in unpinned:
            print(f"cannot pin {name}: results {words} statuses {statuses}")
        if unpinned:
            raise SystemExit("manifest not written")
    pathlib.Path(out).write_text(json.dumps(manifest, indent="\t") + "\n")
    print(f"wrote {out} ({len(manifest['variants'])} variants)")
    for entry in manifest["variants"]:
        print("{:14} {}".format(entry["name"], entry.get("expected", "-")))


if __name__ == "__main__":
    main()
