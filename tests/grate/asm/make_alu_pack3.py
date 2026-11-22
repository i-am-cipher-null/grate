#!/usr/bin/env python3
"""Generate the three-packet cross-check manifest.

Section 32 measured a three-packet batch and concluded that a latch read is
served by the *previous* packet's result. The two-packet set (notes 36) finds
something different: a read in packet 0 sees the value packet 1 wrote, and a
read in packet 1 sees packet 0's, even when packet 1 wrote the same latch
itself. Section 32's backward test (`p0-back`) read alu1 from slot 0 while slot
1 wrote it, so it differs from the new tests in two ways at once - packet count
and slot index - and cannot be compared with them directly.

This set re-runs the shapes on the template section 32 used (`alu_pack.in`,
three `ALU:` blocks in batch 0) with the read slot and the written latch varied
one at a time, so the two explanations can be separated:

    same index       the read asks for the latch its writer's slot number names
    cross index      the read asks for a different latch's number

and it adds the both-write case and the packet-1-to-packet-2 pairs, which the
three-packet set never tested. Everything else is section 32's own arrangement:
`alu_buffer_size` 1, one store per batch, and the rows a reader writes are
stored by the next batch so the group boundary does not hide them.

Slot index: 0..3 = batch 0 packet 0, 4..7 = packet 1, 8..11 = packet 2;
12..15 = batch 1; 16..19 = batch 2.

Values: u0 = 0.0625 (0x10), u1 = 0.25 (0x40).
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/alu_pack.in"
OUT = "tests/grate/asm_characterize.alu_pack3.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"

U0, U1 = 0, 1


def wr(const=U0):
    return f"MAD  r2.l*,  u{const},  #1,  #0"


def rd(latch, dst="r0.l*", bare=False):
    source = f"alu{latch}" if bare else f"alu{latch}.l"
    return f"MAD  {dst},  {source},  #1,  #0"


VARIANTS = [
    ("fwd-same",
     {0: wr(U0), 4: rd(0)}, 1,
     "packet 0 slot 0 writes, packet 1 slot 0 reads alu0: section 32's "
     "forward shape, 0x10 if it reproduces"),
    ("back-same",
     {0: rd(0), 4: wr(U1)}, 1,
     "packet 0 slot 0 reads alu0 and packet 1 slot 0 writes it: 0x40 if a "
     "later packet satisfies an earlier read with the slot numbers equal"),
    ("back-cross-a",
     {0: rd(1), 5: wr(U1)}, 1,
     "section 32's exact backward shape: packet 0 slot 0 reads alu1, packet 1 "
     "slot 1 writes it (measured 0 there)"),
    ("back-cross-b",
     {1: rd(0), 4: wr(U1)}, 1,
     "packet 0 slot 1 reads alu0 while packet 1 slot 0 writes: the cross-index "
     "case with the slots the other way round"),
    ("both-read-p1",
     {0: wr(U0), 4: wr(U1), 5: rd(0)}, 1,
     "both packets write alu0 (0x10 then 0x40), packet 1 slot 1 reads it: "
     "0x10 if a read sees the other packet's value, 0x40 if the last write "
     "wins"),
    ("fwd-1",
     {4: wr(U0), 8: rd(0)}, 1,
     "packet 1 writes, packet 2 reads: is the adjacency rule the same for the "
     "second pair as for the first"),
    ("back-2",
     {4: rd(0), 8: wr(U1)}, 1,
     "packet 1 reads alu0 while packet 2 writes it"),
    ("fwd-two-apart",
     {0: wr(U0), 8: rd(0)}, 1,
     "packet 0 writes, packet 2 reads: section 32 measured 0 for this shape"),
    ("back-two-apart",
     {8: rd(0), 0: wr(U0)}, 1,
     "packet 2 reads alu0 while packet 0 writes it: the reverse of the "
     "two-apart shape"),
    ("back-from-last",
     {0: rd(0), 8: wr(U1)}, 1,
     "packet 0 reads alu0 while packet 2 - the batch's last packet - writes "
     "it: 0x40 if packet 0 sees the last packet, 0 if it sees nothing"),
    ("both-read-p0",
     {0: wr(U0), 4: wr(U1), 1: rd(0)}, 1,
     "both packet 0 and packet 1 write alu0, packet 0 slot 1 reads it: 0x10 "
     "if a read keeps its own packet's value, 0x40 if another packet's wins"),
    ("both-read-p0-last",
     {0: wr(U0), 8: wr(U1), 1: rd(0)}, 1,
     "packet 0 and the batch's last packet both write alu0 (0x10, 0x40), "
     "packet 0 slot 1 reads it: 0x40 if a read sees the previous packet in "
     "the circular order, which for packet 0 is the last one"),
    ("intra-p1",
     {4: wr(U0), 5: rd(0)}, 1,
     "both slots in packet 1, one reading the other's latch: section 32 "
     "measured 0 for the equivalent arrangement"),
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
    for name, slots, store_batch, note in VARIANTS:
        replace = {}
        for index in range(12):
            replace[f"@A{index}@"] = slots.get(index, NOP)
        for index in range(12, 20):
            replace[f"@{'BCD'[(index - 12) // 4]}{(index - 12) % 4}@"] = \
                slots.get(index, NOP)
        for batch in range(3):
            replace[f"@D{batch}@"] = STORE01 if batch == store_batch else NOP
        entries.append({
            "name": name,
            "fs": TEMPLATE,
            "replace": {"fs": replace},
            "environment": {"GRATE_DUMP_RAW": "1"},
            "expected_note": note,
        })
    return {
        "name": "alu-pack3",
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
