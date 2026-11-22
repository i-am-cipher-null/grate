#!/usr/bin/env python3
"""Generate the two-packet ALU batch manifest.

Section 32 measured a batch with three ALU packets and found that the
within-packet bypass disappears there, replaced by a previous-packet forward,
and that the group carry comes from the batch's last packet. The count field is
2 bits and the grammar takes up to three `ALU:` blocks, so a two-packet batch is
a program the assembler emits directly: this template has two blocks in batch 0
and the dump for every variant is checked for `count = 2` at packet address 0
(schedule-words.tsv). No schedule word is patched.

Two questions per shape, so a variant changes one thing:

    packet 1 reads packet 0     a writer in packet 0, a reader in packet 1
    packet 0 reads packet 1     the same writer and reader swapped
    one packet apart            the reader in batch 1 instead (group carry)

Variants, all with `alu_buffer_size` 1:

    two-pkt-exec      packet 1 writes r0.lo, stored by batch 1: 0x40 proves the
                      second packet executed at all
    two-pkt-p0-write  the same write from packet 0 with packet 1 NOP: 0x10
    xf-alu0..3        packet 0 slot N writes, packet 1 slot 0 reads aluN
    xf-read-s1, -s3   the same forward with the read issued by slot 1 / slot 3
    xf-bare           the reader asks for the bare (fp20) form
    xf-fp20, xf-hi    the writer uses .lh / .*h instead of .l*
    back              packet 0 reads alu0, packet 1 writes it
    intra-p1          writer and reader both in packet 1, adjacent slots
    chain-2pkt        p0s0 -> p1s0 -> p1s1, a two-hop chain across the boundary
    chain-1pkt        the same two hops inside a one-packet batch (batch 1),
                      stored by batch 2: the positive control for the chain
    carry-p1          batch 0's *last* packet writes slot 0, batch 1 reads it
    carry-p0          the write is in packet 0, packet 1 is NOP
    carry-both        both packets write slot 0 (0x10 then 0x40): which one
                      carries
    carry-p1-s1       the carry read from slot 1 instead of slot 0
    row-direct        a row written in batch 0, stored by batch 1
    none              no write, one store: the word baseline

The chain variants use the latch rather than a row because the interesting hop
is packet-to-packet inside one batch; `row-direct` and the store placement are
the controls that a row is only visible in the next group.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/alu_pack2.in"
OUT = "tests/grate/asm_characterize.alu_pack2.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"
STORE23 = "store rt1, r2, r3"

U0, U1, U2, U3 = 0, 1, 2, 3


def wr(dst, const=U0, fmt=".l*"):
    return f"MAD  {dst}{fmt},  u{const},  #1,  #0"


def rd(dst, latch, bare=False):
    source = f"alu{latch}" if bare else f"alu{latch}.l"
    return f"MAD  {dst},  {source},  #1,  #0"


def variant(name, note, packet0=None, packet1=None, batch1=None, batch2=None,
            dws=None):
    """One program. Slot maps are keyed 0..3 within each packet."""
    return {
        "name": name,
        "note": note,
        "slots": {
            0: packet0 or {},
            1: packet1 or {},
            2: batch1 or {},
            3: batch2 or {},
        },
        "dws": dws or {},
    }


VARIANTS = [
    variant("two-pkt-exec",
            "packet 1 alone writes 0.25 into r0.lo, stored by batch 1: 0x40 "
            "shows the second packet executed",
            packet1={0: wr("r0", U1)}, dws={1: STORE01}),
    variant("two-pkt-p0-write",
            "packet 0 alone writes 0.0625 into r0.lo, packet 1 is NOP, stored "
            "by batch 1: 0x10",
            packet0={0: wr("r0", U0)}, dws={1: STORE01}),
    variant("xf-alu0",
            "packet 0 slot 0 writes, packet 1 slot 0 reads alu0: 0x10 if the "
            "latch survives one packet inside the batch",
            packet0={0: wr("r2")},
            packet1={0: rd("r0.l*", 0)}, dws={1: STORE01}),
    variant("xf-alu1",
            "the same for alu1", packet0={1: wr("r2")},
            packet1={0: rd("r0.l*", 1)}, dws={1: STORE01}),
    variant("xf-alu2",
            "the same for alu2", packet0={2: wr("r2")},
            packet1={0: rd("r0.l*", 2)}, dws={1: STORE01}),
    variant("xf-alu3",
            "the same for alu3", packet0={3: wr("r2")},
            packet1={0: rd("r0.l*", 3)}, dws={1: STORE01}),
    variant("xf-read-s1",
            "the forward read from slot 1 instead of slot 0: the issuing slot "
            "should not matter",
            packet0={0: wr("r2")},
            packet1={1: rd("r0.l*", 0)}, dws={1: STORE01}),
    variant("xf-read-s3",
            "producer in packet 0 slot 3, reader in packet 1 slot 3",
            packet0={3: wr("r2")},
            packet1={3: rd("r0.l*", 3)}, dws={1: STORE01}),
    variant("xf-bare",
            "the packet-boundary read written bare (fp20): 0 if the format "
            "rule of section 28 holds here too",
            packet0={0: wr("r2")},
            packet1={0: rd("r0.l*", 0, bare=True)}, dws={1: STORE01}),
    variant("xf-fp20",
            "the writer uses .lh and the reader asks .l: 0 per section 28's "
            "one-packet result",
            packet0={0: wr("r2", U0, ".lh")},
            packet1={0: rd("r0.l*", 0)}, dws={1: STORE01}),
    variant("xf-hi",
            "the writer fills only the high half and the reader asks .l: 0",
            packet0={0: wr("r2", U0, ".*h")},
            packet1={0: rd("r0.l*", 0)}, dws={1: STORE01}),
    variant("back",
            "packet 0 slot 0 reads alu0 and packet 1 slot 0 writes it: 0x40 "
            "would mean the read saw the later packet, 0 means forward-only",
            packet0={0: rd("r0.l*", 0)},
            packet1={0: wr("r2", U1)}, dws={1: STORE01}),
    variant("back-s1",
            "the same backward read for alu1, writer in packet 1 slot 1",
            packet0={1: rd("r0.l*", 1)},
            packet1={1: wr("r2", U1)}, dws={1: STORE01}),
    variant("back-s2",
            "the same backward read for alu2, writer in packet 1 slot 2",
            packet0={2: rd("r0.l*", 2)},
            packet1={2: wr("r2", U1)}, dws={1: STORE01}),
    variant("back-s3",
            "the same backward read for alu3, writer in packet 1 slot 3",
            packet0={3: rd("r0.l*", 3)},
            packet1={3: wr("r2", U1)}, dws={1: STORE01}),
    variant("back-cross-a",
            "packet 0 slot 0 reads alu1 while packet 1 slot 1 writes: "
            "separates \"the same slot number of either packet\" from "
            "\"any later slot\"",
            packet0={0: rd("r0.l*", 1)},
            packet1={1: wr("r2", U1)}, dws={1: STORE01}),
    variant("back-cross-b",
            "packet 0 slot 1 reads alu0 while packet 1 slot 0 writes: the "
            "same question with the slots swapped",
            packet0={1: rd("r0.l*", 0)},
            packet1={0: wr("r2", U1)}, dws={1: STORE01}),
    variant("two-writes-read-p0",
            "both packets write alu0 (0x10 then 0x40), packet 0 slot 1 reads "
            "it: 0x10 if the read keeps its own packet's value, 0x40 if the "
            "later packet wins",
            packet0={0: wr("r2", U0), 1: rd("r0.l*", 0)},
            packet1={0: wr("r2", U1)}, dws={1: STORE01}),
    variant("two-writes-read-p1",
            "the same two writes read from packet 1 slot 1 instead: 0x40 if "
            "the later packet's write is what a reader sees anywhere in the "
            "batch",
            packet0={0: wr("r2", U0)},
            packet1={0: wr("r2", U1), 1: rd("r0.l*", 0)},
            dws={1: STORE01}),
    variant("back-bare",
            "the backward read written bare over a .l* write: 0 if the "
            "format rule applies to this direction too",
            packet0={0: rd("r0.l*", 0, bare=True)},
            packet1={0: wr("r2", U1)}, dws={1: STORE01}),
    variant("back-unrelated",
            "packet 0 reads alu2 while packet 1 writes slot 0: 0 if only the "
            "same latch number couples the two packets",
            packet0={0: rd("r0.l*", 2)},
            packet1={0: wr("r2", U1)}, dws={1: STORE01}),
    variant("back-value",
            "the backward read with a different payload (0.5, 0x7F): the "
            "value follows the writer",
            packet0={0: rd("r0.l*", 0)},
            packet1={0: wr("r2", U2)}, dws={1: STORE01}),
    variant("back-fp20",
            "packet 1 writes .lh and packet 0 reads .l: 0 if the format rule "
            "applies to this direction too",
            packet0={0: rd("r0.l*", 0)},
            packet1={0: wr("r2", U0, ".lh")}, dws={1: STORE01}),
    variant("intra-p1",
            "writer and reader adjacent inside packet 1: 0x00100000 if a "
            "multi-packet batch keeps the intra-packet bypass",
            packet1={0: wr("r2"), 1: rd("r1.l*", 0)}, dws={1: STORE01}),
    variant("chain-2pkt",
            "p0s0 -> p1s0 -> p1s1: the first hop crosses the packet boundary, "
            "the second is inside packet 1: 0x00100010 if both work",
            packet0={0: wr("r2")},
            packet1={0: rd("r0.l*", 0), 1: rd("r1.l*", 1)},
            dws={1: STORE01}),
    variant("chain-1pkt",
            "the same two hops inside batch 1's single packet, stored by batch "
            "2: 0x00100010 is the control that the chain itself works",
            batch1={0: wr("r2"), 1: rd("r0.l*", 0), 2: rd("r1.l*", 1)},
            dws={2: STORE01}),
    variant("carry-p1",
            "batch 0's last packet writes slot 0, batch 1 slot 0 reads alu0: "
            "0x10 if the final packet's slot-0 result is the group carry",
            packet1={0: wr("r2")},
            batch1={0: rd("r0.l*", 0)}, dws={2: STORE01}),
    variant("carry-p0",
            "the same write from packet 0 with packet 1 NOP: 0 if the carry "
            "must come from the last packet",
            packet0={0: wr("r2")},
            batch1={0: rd("r0.l*", 0)}, dws={2: STORE01}),
    variant("carry-both",
            "both packets write slot 0 (0x10 then 0x40): 0x40 if the last "
            "packet's result is the one carried",
            packet0={0: wr("r2", U0)},
            packet1={0: wr("r2", U1)},
            batch1={0: rd("r0.l*", 0)}, dws={2: STORE01}),
    variant("carry-p1-s1",
            "the carry read from slot 1 instead of slot 0: 0 per section 31",
            packet1={0: wr("r2")},
            batch1={1: rd("r0.l*", 0)}, dws={2: STORE01}),
    variant("row-direct",
            "a row written in packet 0 and stored by batch 1: the row control "
            "for the group boundary",
            packet0={0: wr("r2")}, dws={1: STORE23}),
    variant("none",
            "no write, one store in batch 1: the word baseline",
            dws={1: STORE01}),
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
    seen = set()
    for entry in VARIANTS:
        if entry["name"] in seen:
            raise SystemExit(f"duplicate variant {entry['name']}")
        seen.add(entry["name"])
        replace = {}
        names = (["A0", "A1", "A2", "A3"],   # batch 0, packet 0
                 ["A4", "A5", "A6", "A7"],   # batch 0, packet 1
                 ["B0", "B1", "B2", "B3"],   # batch 1
                 ["C0", "C1", "C2", "C3"])   # batch 2
        for batch, placeholders in enumerate(names):
            slots = entry["slots"].get(batch, {})
            for slot, placeholder in enumerate(placeholders):
                replace[f"@{placeholder}@"] = slots.get(slot, NOP)
        for index in range(3):
            replace[f"@D{index}@"] = entry["dws"].get(index, NOP)
        entries.append({
            "name": entry["name"],
            "fs": TEMPLATE,
            "replace": {"fs": replace},
            "environment": {"GRATE_DUMP_RAW": "1"},
            "expected_note": entry["note"],
        })
    return {
        "name": "alu-pack2",
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
        print("{:18} {}".format(entry["name"], entry.get("expected", "-")))


if __name__ == "__main__":
    main()
