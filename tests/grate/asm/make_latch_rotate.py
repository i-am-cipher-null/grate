#!/usr/bin/env python3
"""Generate the latch-rotation scope manifest.

Notes 36 find that inside a batch with N packets a latch read in slot S of
packet k is served by packet (k-1) mod N: two-packet batches couple their
packets in both directions, and a three-packet batch couples 0<-2, 1<-0, 2<-1.
Every one of those measurements put all packets in one batch, so the scope of
the rotation is not established: it could be the *batch*, or it could be the
*material the ALU buffer holds*, which is the group (`alu_buffer_size` batches,
one packet each in this template).

This set puts one packet in each of several batches and asks the same
questions, using `asm/alu_latch.in`:

    rot2-fwd         size 2, batch 0 writes, batch 1 reads: 0x10 is the forward
                     that section 28 measured
    rot2-back        size 2, batch 1 writes, batch 0 reads: 0x40 if the
                     rotation wraps across the two batches of the group, 0 if
                     the rule is per batch
    rot3-fwd-adj     size 3, batch 1 writes, batch 2 reads: 0x10 if the forward
                     is per group (section 28 only tested batch 0 -> batch 2)
    rot3-two-apart   size 3, batch 0 writes, batch 2 reads: 0 if only the
                     adjacent packet counts, which section 28 measured at size 2
    rot3-back        size 3, batch 2 writes, batch 0 reads: 0x40 if the group's
                     rotation wraps from the first packet to the last, 0 if not
    rot3-back-adj    size 3, batch 2 writes, batch 1 reads: 0x40 if the later
                     packet feeds the earlier one, 0 if forward-only
    rot3-carry       size 3, batch 2 writes slot 0, batch 3 reads it: 0x10, the
                     group-boundary carry from the group's last packet, as a
                     control that the group is aligned as expected
    rot2-none        size 2, no write, one store: the baseline

A reader in the first batch of a group cannot be stored by that group's DW
(rows become visible at the group boundary), so every variant stores in the
first batch of the *next* group: batch 2 at size 2, batch 3 at size 3. That is
also why the reading batch and the storing batch differ for `rot3-carry`.

Slot index: 0..3 = batch 0, 4..7 = batch 1, 8..11 = batch 2, 12..15 = batch 3,
16..19 = batch 4, 20..23 = batch 5 (the template's A..G groups of four).
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/alu_latch.in"
OUT = "tests/grate/asm_characterize.latch_rotate.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"

U0, U1 = 0, 1

PREFIX = ("A", "B", "C", "E", "F", "G")


def wr(const):
    return f"MAD  r2.l*,  u{const},  #1,  #0"


def rd(latch=0):
    return f"MAD  r0.l*,  alu{latch}.l,  #1,  #0"


def variant(name, abuf, slots, dws, note):
    return {"name": name, "abuf": abuf, "slots": slots, "dws": dws, "note": note}


VARIANTS = [
    variant("rot1-fwd", 1, {0: wr(U0), 4: rd()}, {2: STORE01},
            "size 1, batch 0 writes and batch 1 reads: 0x10, section 31's "
            "cross-batch carry, reproduced here as the size-1 control"),
    variant("rot1-back", 1, {0: rd(), 4: wr(U1)}, {2: STORE01},
            "size 1, batch 1 writes and batch 0 reads: 0x40 would mean the "
            "rotation spans two batches even when every batch is its own "
            "group"),
    variant("rot1-back-2", 1, {4: rd(), 8: wr(U1)}, {3: STORE01},
            "size 1, batch 2 writes and batch 1 reads: the same question for "
            "the next pair"),
    variant("rot1-two-apart", 1, {0: wr(U0), 8: rd()}, {3: STORE01},
            "size 1, batch 0 writes and batch 2 reads: 0 if the rotation does "
            "not reach two batches at this size"),
    variant("rot2-fwd", 2, {0: wr(U0), 4: rd()}, {2: STORE01},
            "size 2, batch 0 writes and batch 1 reads: 0x10, the forward "
            "section 28 measured"),
    variant("rot2-back", 2, {0: rd(), 4: wr(U1)}, {2: STORE01},
            "size 2, batch 1 writes and batch 0 reads: 0x40 if the rotation "
            "wraps across the group's two packets"),
    variant("rot3-fwd-adj", 3, {4: wr(U0), 8: rd()}, {3: STORE01},
            "size 3, batch 1 writes and batch 2 reads: 0x10 if the forward "
            "works for the second adjacent pair too"),
    variant("rot3-two-apart", 3, {0: wr(U0), 8: rd()}, {3: STORE01},
            "size 3, batch 0 writes and batch 2 reads: 0 if only the adjacent "
            "packet is visible"),
    variant("rot3-back", 3, {0: rd(), 8: wr(U1)}, {3: STORE01},
            "size 3, batch 2 - the group's last packet - writes and batch 0 "
            "reads: 0x40 if the rotation wraps across the group"),
    variant("rot3-back-adj", 3, {4: rd(), 8: wr(U1)}, {3: STORE01},
            "size 3, batch 2 writes and batch 1 reads: the same question for "
            "the adjacent pair"),
    variant("rot3-carry", 3, {8: wr(U0), 12: rd()}, {4: STORE01},
            "size 3, the group's last batch writes slot 0 and the next "
            "group's first batch reads it: 0x10 would be section 31's "
            "cross-group carry, 0 means the carry does not survive a "
            "three-packet group"),
    variant("rot3-carry-mid", 3, {4: wr(U0), 12: rd()}, {4: STORE01},
            "size 3, the previous group's *second* batch writes slot 0 and "
            "the next group's first batch reads it, which separates \"the "
            "last packet carries\" from \"the packet the rotation register "
            "holds carries\""),
    variant("rot3-carry-first", 3, {0: wr(U0), 12: rd()}, {4: STORE01},
            "size 3, the previous group's *first* batch writes slot 0 and the "
            "next group's first batch reads it"),
    variant("rot2-carry", 2, {4: wr(U0), 8: rd()}, {3: STORE01},
            "size 2, the previous group's last batch writes slot 0 and the "
            "next group's first batch reads it: 0x10 if the carry works across "
            "a two-packet group"),
    variant("rot2-carry-first", 2, {0: wr(U0), 8: rd()}, {3: STORE01},
            "size 2, the previous group's first (and only earlier) batch "
            "writes and the next group reads"),
    variant("rot2-none", 2, {}, {2: STORE01},
            "size 2, no write, one store: the baseline"),
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
    for entry in VARIANTS:
        replace = {"@ABUF@": str(entry["abuf"])}
        for batch in range(6):
            for slot in range(4):
                replace[f"@{PREFIX[batch]}{slot}@"] = \
                    entry["slots"].get(batch * 4 + slot, NOP)
        for batch in range(6):
            replace[f"@D{batch}@"] = entry["dws"].get(batch, NOP)
        entries.append({
            "name": entry["name"],
            "fs": TEMPLATE,
            "replace": {"fs": replace},
            "environment": {"GRATE_DUMP_RAW": "1"},
            "expected_note": entry["note"],
        })
    return {
        "name": "latch-rotate",
        "repeat": 3,
        "timeout": 20,
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
        print("{:18} {}".format(entry["name"], entry.get("expected", "-")))


if __name__ == "__main__":
    main()
