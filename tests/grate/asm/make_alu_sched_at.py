#!/usr/bin/env python3
"""Generate the per-batch ALU schedule-bit manifest.

Section 30 OR-ed bits 29 and 30 into every 0x801 word at once and saw no
change in four shapes. That cannot separate "the bit does nothing" from "the
bit matters only for the packet it is attached to": a global mask sets it on
the producer, the consumer and an idle batch together.

This set masks one batch at a time with GRATE_ALU_SCHED_OR_AT, on three batches
that are one packet each, so the batch index is also the packet and group
index:

    batch 0  producer (and, in `chain`/`dw`/`plain`, the whole shape)
    batch 1  consumer of the group-boundary carry (`flush`), the storing batch
             (`row`), or nothing (`chain`, `dw`, `plain`)
    batch 2  the readback packet, which takes no part in any shape

Shapes:

    chain  alu0 -> alu1 two-link chain in batch 0, stored by batch 0:
           0x00100010 when the intra-packet forward works (section 30)
    flush  batch 0 writes alu0, batch 1 reads it as the group-boundary carry,
           batch 2 stores: 0x00000010 (sections 28 and 31)
    row    batch 0 writes r2.lo, batch 1 stores r2/r3: 0x00000010
    dw     batch 0 writes r2.lo and stores it in the same batch: 0x00000010
    plain  no write, one store in batch 0: 0x00000000

Settings per shape: no mask, then bits 29, 30 and both on batch 0, and the same
three on batch 1. Every variant runs with GRATE_DUMP_RAW, so the run's own log
carries the 0x801 and 0x806 words the assembler uploaded; dump_sched_words.py
turns those into schedule-words.tsv. That is what separates "the bit is inert"
from "the bit never reached that batch".

Predictions:

    inert everywhere   all seven settings of a shape give its baseline word and
                       the dump shows the mask on exactly one 0x801 word
    per-batch effect   the masked-batch setting differs from that shape's
                       baseline while the same bit on another batch does not
    count/address      an OR could also change how many packets a batch runs;
                       `chain`, `dw` and `plain` would change or disappear

`--results DIR` pins each variant to the modal word of its repeats.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/alu_sched_at.in"
OUT = "tests/grate/asm_characterize.alu_sched_at.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"
STORE23 = "store rt1, r2, r3"

U0 = 0


def wr(dst, const=U0):
    return f"MAD  {dst},  u{const},  #1,  #0"


def rd_latch(dst, latch):
    return f"MAD  {dst},  alu{latch}.l,  #1,  #0"


# (name, {batch: {slot: instruction}}, {batch: dw}, baseline note)
SHAPES = [
    ("chain",
     {0: {0: wr("r2.l*"), 1: rd_latch("r0.l*", 0), 2: rd_latch("r1.l*", 1)}},
     {0: STORE01},
     "the intra-packet chain of section 30, all in batch 0"),
    ("flush",
     {0: {0: wr("r2.l*")}, 1: {0: rd_latch("r0.l*", 0)}},
     {2: STORE01},
     "batch 0 writes alu0, batch 1 reads it across the group flush, batch 2 "
     "stores the row"),
    ("row",
     {0: {0: wr("r2.l*")}},
     {1: STORE23},
     "a row write in batch 0 stored by batch 1"),
    ("dw",
     {0: {0: wr("r2.l*")}},
     {0: STORE23},
     "an ALU write and its store in the same batch"),
    ("plain",
     {},
     {0: STORE01},
     "no write, one store: the baseline for the store itself"),
]

# (tag, mask, batch the mask is applied to)
SETTINGS = [
    ("none", 0, None),
    ("b0-29", 0x20000000, 0),
    ("b0-30", 0x40000000, 0),
    ("b0-both", 0x60000000, 0),
    ("b1-29", 0x20000000, 1),
    ("b1-30", 0x40000000, 1),
    ("b1-both", 0x60000000, 1),
]

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
    entries = []
    names = set()
    for shape, slots, dws, note in SHAPES:
        for tag, mask, batch in SETTINGS:
            name = f"{shape}-{tag}"
            if name in names:
                raise SystemExit(f"duplicate variant {name}")
            names.add(name)
            replace = {}
            for index, prefix in enumerate("ABC"):
                for slot in range(4):
                    replace[f"@{prefix}{slot}@"] = \
                        slots.get(index, {}).get(slot, NOP)
                replace[f"@D{index}@"] = dws.get(index, NOP)
            environment = {"GRATE_DUMP_RAW": "1"}
            if mask:
                environment["GRATE_ALU_SCHED_OR"] = f"0x{mask:08X}"
                environment["GRATE_ALU_SCHED_OR_AT"] = str(batch)
            if batch is None:
                where = "no schedule mask"
            else:
                where = f"mask 0x{mask:08X} on batch {batch} only"
            entries.append({
                "name": name,
                "fs": TEMPLATE,
                "replace": {"fs": replace},
                "environment": environment,
                "expected_note": f"{note}; {where}",
            })
    return {
        "name": "alu-sched-at",
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
        print("{:16} {}".format(entry["name"], entry.get("expected", "-")))


if __name__ == "__main__":
    main()
