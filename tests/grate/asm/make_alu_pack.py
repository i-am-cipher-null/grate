#!/usr/bin/env python3
"""Generate the ALU packing manifest.

A batch's ALU schedule word carries a packet count in bits 0..1 and a packet
address in bits 2..7, and the assembler sets the count from the number of
`ALU:` blocks the batch has: a three-block batch emits `0x00000003` and uploads
24 instruction words. So a batch is not limited to one four-instruction packet
- twelve instruction slots are available - and the grammar already expresses
it.

The template puts a three-packet batch first and one-packet batches after it,
so the forwarding rules can be compared inside one program with one variable
changed. Three readings are on the table:

  unchanged   packing changes nothing about the latch pipeline: the rules in
              notes 28 hold packet by packet, and a chain inside packet 0 of a
              three-packet batch still works.
  per-packet  a batch with more than one packet forwards only from the previous
              packet, so the within-packet chain dies and a packet-to-packet
              read works for every latch.
  batch-only  only counts of one keep the intra-packet rule, and the group
              flush carry needs the writing batch to be a single packet too.

    p0-chain       set 1's chain in packet 0 of the three-packet batch
    p1-chain       the same chain in batch 1's only packet (count 1 control)
    p0-x-alu0..3   packet 0 writes through slot N, packet 1 reads that latch
    p0-x-s1        the packet-to-packet read issued by slot 1 instead of slot 0
    p0-two-pkt     packet 0 writes, packet 2 reads: two boundaries
    p0-back        packet 0 reads a latch packet 1 writes
    p0-intra-p1    two slots of packet 1, one reading the other's latch
    p0-bare        the packet-to-packet read written bare (the fp20 form)
    p0-hi          packet 0 wrote a high half only
    p0-row         packet 0 writes a row, packet 1 reads it
    chain-3pkt     one dependent hop per packet: three-deep inside one batch
    flush-3blk     the write is in the three-packet batch's first packet
    flush-3blk-last the write is in that batch's last packet
    flush-1blk     the write is in a one-packet batch (section 31's shape)
    direct         the store sees nothing: 0 baseline

`--results DIR` pins each variant to the modal word of its repeats.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/alu_pack.in"
OUT = "tests/grate/asm_characterize.alu_pack.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"

# u0 = 0.0625 -> 0x10, u1 = 0.25 -> 0x40, u2 = 0.5 -> 0x7F, u3 = 0.125 -> 0x20
U0, U1, U2, U3 = 0, 1, 2, 3


def wr(dst, const):
    return f"MAD  {dst},  u{const},  #1,  #0"


def rd(dst, latch):
    return f"MAD  {dst},  alu{latch}.l,  #1,  #0"


def row(dst, src):
    return f"MAD  {dst},  {src},  #1,  #0"


def variant(name, slots, dws, note):
    return {
        "name": name,
        "abuf": 1,
        "slots": slots,
        "dws": dws,
        "note": note,
        "fs": TEMPLATE,
    }


# Slot index: 0..11 = batch 0's three packets, 12..15 = batch 1's only packet,
# 16..19 = batch 2's only packet.
VARIANTS = [
    variant("p0-chain",
            {0: wr("r2.l*", U0), 1: rd("r0.l*", 0), 2: rd("r1.l*", 1)},
            {0: STORE01},
            "the chain from section 28, in packet 0 of a three-packet batch: "
            "0x00100010 if packing leaves the intra-packet rule alone"),
    variant("p1-chain",
            {12: wr("r2.l*", U0), 13: rd("r0.l*", 0), 14: rd("r1.l*", 1)},
            {1: STORE01},
            "the same chain in a one-packet batch of the same program: the "
            "count-1 control, 0x00100010 expected"),
    variant("p0-x-alu0",
            {0: wr("r2.l*", U0), 4: rd("r1.l*", 0)},
            {0: STORE01},
            "packet 0 writes through slot 0, packet 1 reads alu0"),
    variant("p0-x-alu1",
            {1: wr("r2.l*", U0), 4: rd("r1.l*", 1)},
            {0: STORE01},
            "the write comes from slot 1 and the read asks for alu1"),
    variant("p0-x-alu2",
            {2: wr("r2.l*", U0), 4: rd("r1.l*", 2)},
            {0: STORE01},
            "the same for alu2"),
    variant("p0-x-alu3",
            {3: wr("r2.l*", U0), 4: rd("r1.l*", 3)},
            {0: STORE01},
            "the same for alu3"),
    variant("p0-x-s1",
            {0: wr("r2.l*", U0), 5: rd("r1.l*", 0)},
            {0: STORE01},
            "the packet-to-packet read issued by slot 1 instead of slot 0"),
    variant("p0-two-pkt",
            {0: wr("r2.l*", U0), 8: rd("r1.l*", 0)},
            {0: STORE01},
            "packet 0 writes and packet 2 reads: two packet boundaries"),
    variant("p0-back",
            {0: rd("r1.l*", 1), 5: wr("r2.l*", U1)},
            {0: STORE01},
            "packet 0 reads alu1 and packet 1 writes it: a later packet "
            "satisfying an earlier read"),
    variant("p0-intra-p1",
            {5: wr("r2.l*", U1), 6: rd("r1.l*", 1)},
            {0: STORE01},
            "two slots of packet 1, slot 2 reading slot 1's latch"),
    variant("p0-bare",
            {0: wr("r2.l*", U0), 4: "MAD  r1.l*,  alu0,  #1,  #0"},
            {0: STORE01},
            "the packet-to-packet read written bare (the fp20 form)"),
    variant("p0-hi",
            {0: wr("r2.*h", U0), 4: rd("r1.l*", 0)},
            {0: STORE01},
            "packet 0 wrote a high half only"),
    variant("p0-row",
            {0: wr("r2.l*", U0), 4: row("r1.l*", "r2.l")},
            {0: STORE01},
            "packet 0 writes a row and packet 1 reads it"),
    variant("flush-3blk",
            {0: wr("r2.l*", U0), 12: rd("r1.l*", 0)},
            {1: STORE01},
            "the group flush carry when the writing batch had three packets"),
    variant("flush-1blk",
            {12: wr("r2.l*", U0), 16: rd("r1.l*", 0)},
            {2: STORE01},
            "the same when the writing batch had one packet: the section 31 "
            "shape, 0x10 expected"),
    variant("flush-3blk-last",
            {8: wr("r2.l*", U0), 12: rd("r1.l*", 0)},
            {1: STORE01},
            "the write comes from the three-packet batch's last packet: if the "
            "carry only needs the last packet this is 0x10, if a multi-packet "
            "batch has no carry it is 0 like flush-3blk"),
    variant("chain-3pkt",
            {0: wr("r2.l*", U0), 4: rd("r0.l*", 0), 8: rd("r1.l*", 0)},
            {0: STORE01},
            "a three-packet chain: packet 0 writes, packet 1 copies it, packet "
            "2 copies packet 1's slot-0 result: 0x00100010 if a dependent "
            "chain runs packet by packet"),
    variant("direct",
            {0: wr("r2.l*", U0)},
            {0: STORE01},
            "packet 0 writes a row the store does not read: 0 baseline"),
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

BATCHES = (("A", 12), ("B", 4), ("C", 4))


def build_manifest():
    names = [entry["name"] for entry in VARIANTS]
    if len(names) != len(set(names)):
        raise SystemExit("variant names are not unique")
    entries = []
    offsets = {"A": 0, "B": 12, "C": 16}
    for entry in VARIANTS:
        replace = {}
        if entry["abuf"] != 1:
            raise SystemExit(
                "the template hard-codes alu_buffer_size 1: " + entry["name"])
        for batch, (prefix, count) in enumerate(BATCHES):
            for slot in range(count):
                index = offsets[prefix] + slot
                replace[f"@{prefix}{slot}@"] = entry["slots"].get(index, NOP)
            replace[f"@D{batch}@"] = entry["dws"].get(batch, NOP)
        entries.append({
            "name": entry["name"],
            "fs": TEMPLATE,
            "replace": {"fs": replace},
            "expected_note": entry["note"],
        })
    return {
        "name": "alu-pack",
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
        print("{:20} {}".format(entry["name"], entry.get("expected", "-")))


if __name__ == "__main__":
    main()
