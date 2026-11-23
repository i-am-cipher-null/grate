#!/usr/bin/env python3
"""Generate the cross-packet send/recv manifest.

Notes 13 and 16 established the marker matrix inside one packet, including the
(0,1) pair: a `send` in slot 0 and a `recv` in slot 1 accumulate. Notes 37 put
the markers in a two-packet batch and measured no transfer across the packet
boundary, but every receiver there sat in slot 0 of its packet, where a recv has
no earlier slot to accumulate from by the one-packet matrix itself. The specific
pairing this set finishes is the one with both markers where they do work
inside a packet: sender packet 0 slot 0, receiver packet 1 slot 1.

`asm/alu_send_cross.in` is a size-1 program whose first batch holds two
packets, so the group is the batch and the store in batch 1 is the next group.
Choosing the payloads so that three explanations name three different words is
what makes the result readable:

    receiver's own rA            0x10
    receiver's own previous slot 0x40 (packet 1 slot 0 writes it)
    the sender's value           0x60

    0x10  recv accumulated nothing
    0x50  recv added its own packet's previous slot
    0x70  the sender's value crossed the packet boundary

Variants:

    xfer-s0-s1          send packet 0 slot 0, recv packet 1 slot 1, previous
                        slot of the receiver written: 0x70 / 0x50 / 0x10
    xfer-s0-s1-noprev   the same with the receiver's previous slot left NOP:
                        0x70 or 0x10, so a 0x50 cannot be an artefact of that
                        slot
    plain-s0-s1         both instructions without markers: 0x10, the control
                        that any accumulation comes from the marker
    send-only           the sender alone, stored through r2/r3: 0x60
    recv-only           the receiver alone with its previous slot written: 0x50
                        if recv works in packet 1 at all, 0x10 if not
    reverse-s0-s1       send in packet 1 slot 0, recv in packet 0 slot 1, the
                        same question in the other direction
    same-pkt-01         send slot 0 and recv slot 1 inside packet 0: 0x70, the
                        known-good (0,1) pair of notes 13/16
    alt-1-2             send packet 0 slot 1, recv packet 1 slot 2: an
                        alternative slot pairing
    xfer-s3-s1          send packet 0 slot 3, recv packet 1 slot 1, with packet
                        1 slot 0 writing 0x20: 0x70 means the slot-3 sender's
                        value arrived, 0x30 that the receiver only saw its own
                        packet's slot 0, 0x10 that neither did. This separates a
                        send/recv transfer from the ordinary slot-index latch
                        forward, which would carry slot 1, not slot 3
    latch-read-alu0     a plain `alu0` read in packet 1 slot 1 with packet 0
                        slot 0 writing: the normal latch path from a slot with
                        no write, as the contrast for xfer-s3-s1
    recv-plain-sender   the send marker removed from the sender: 0x50 or 0x10
                        shows the marker is what carries the value, not the
                        sender's write alone
    recv-alone-s1       recv in packet 1 slot 1 with nothing else: 0x10

Every variant stores from batch 1, the group after the two-packet batch, so the
receiver's own r0 result is what the store reads.

Usage:

    tests/grate/asm/make_alu_send_cross.py
    tests/grate/asm/make_alu_send_cross.py --only xfer-s0-s1,plain-s0-s1
    tests/grate/asm/make_alu_send_cross.py --results DIR   # pin from a run
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/alu_send_cross.in"
OUT = "tests/grate/asm_characterize.alu_send_cross.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"
STORE23 = "store rt1, r2, r3"

# Packet 0 slots are A0..A3, packet 1 slots are A4..A7, batches 1 and 2 are
# B0..B3 and C0..C3.
SLOTS = (["A0", "A1", "A2", "A3"], ["A4", "A5", "A6", "A7"],
         ["B0", "B1", "B2", "B3"], ["C0", "C1", "C2", "C3"])

RECV = 0        # u0 = 0.0625 = 0x10, the receiver's own operand
FILL = 1        # u1 = 0.125  = 0x20
PREV = 2        # u2 = 0.25   = 0x40, the receiver's previous slot
SEND = 3        # u3 = 0.375  = 0x60, the sender's value


def mad(dst, const, marker=""):
    suffix = f" {marker}" if marker else ""
    return f"MAD  {dst},  u{const},  #1,  #0{suffix}"


def variant(name, note, slots, store=1, word=STORE01):
    return {"name": name, "note": note, "slots": slots, "store": store,
            "word": word}


VARIANTS = [
    variant("xfer-s0-s1",
            "sender packet 0 slot 0 sends 0x60, receiver packet 1 slot 1 "
            "receives 0x10, and the receiver's previous slot (packet 1 slot 0) "
            "writes 0x40: 0x70 if the sender crossed the packet boundary, 0x50 "
            "if only the receiver's own previous slot was added, 0x10 if "
            "neither",
            {"A0": mad("r2.l*", SEND, "(other)"),
             "A4": mad("r2.l*", PREV),
             "A5": mad("r0.l*", RECV, "(this)")}),
    variant("xfer-s0-s1-noprev",
            "the same with the receiver's previous slot left NOP: 0x70 or 0x10, "
            "so a 0x50 in `xfer-s0-s1` cannot be an artefact of that slot",
            {"A0": mad("r2.l*", SEND, "(other)"),
             "A5": mad("r0.l*", RECV, "(this)")}),
    variant("plain-s0-s1",
            "both instructions without markers: 0x10, the control that any "
            "accumulation is the marker's doing",
            {"A0": mad("r2.l*", SEND),
             "A4": mad("r2.l*", PREV),
             "A5": mad("r0.l*", RECV)}),
    variant("send-only",
            "the sender alone, stored through r2/r3: 0x60 - the send marker "
            "does not change what the sender's own instruction writes",
            {"A0": mad("r2.l*", SEND, "(other)")}, word=STORE23),
    variant("recv-only",
            "the receiver alone with its previous slot writing 0x40: 0x50 if "
            "recv accumulates inside packet 1, 0x10 if a packet-boundary "
            "packet has no accumulator at all",
            {"A4": mad("r2.l*", PREV),
             "A5": mad("r0.l*", RECV, "(this)")}),
    variant("reverse-s0-s1",
            "send in packet 1 slot 0, recv in packet 0 slot 1, packet 0 slot 0 "
            "writing 0x40: the same question in the other direction",
            {"A4": mad("r2.l*", SEND, "(other)"),
             "A0": mad("r2.l*", PREV),
             "A1": mad("r0.l*", RECV, "(this)")}),
    variant("same-pkt-01",
            "send slot 0 and recv slot 1 inside packet 0: 0x70, the known-good "
            "(0,1) pair inside one packet",
            {"A0": mad("r2.l*", SEND, "(other)"),
             "A1": mad("r0.l*", RECV, "(this)")}),
    variant("alt-1-2",
            "send packet 0 slot 1, recv packet 1 slot 2, packet 1 slot 0 "
            "writing 0x40 and slot 1 left NOP: an alternative slot pairing, "
            "0x70 if the sender crossed",
            {"A1": mad("r2.l*", SEND, "(other)"),
             "A4": mad("r2.l*", PREV),
             "A6": mad("r0.l*", RECV, "(this)")}),
    variant("xfer-s3-s1",
            "send packet 0 slot 3, recv packet 1 slot 1, packet 1 slot 0 "
            "writing 0x20: 0x70 if the slot-3 sender's value arrived, 0x30 if "
            "the receiver only saw its own packet's slot 0, 0x10 if neither. A "
            "transfer from slot 3 to slot 1 cannot be the ordinary slot-index "
            "forward, which would carry slot 1",
            {"A3": mad("r2.l*", SEND, "(other)"),
             "A4": mad("r2.l*", FILL),
             "A5": mad("r0.l*", RECV, "(this)")}),
    variant("latch-read-alu0",
            "packet 0 slot 0 writes 0x60 and packet 1 slot 1 reads alu0 the "
            "ordinary way: the latch path from a slot with no write, the "
            "contrast for `xfer-s3-s1`",
            {"A0": mad("r2.l*", SEND),
             "A5": "MAD  r0.l*,  alu0.l,  #1,  #0"}),
    variant("recv-plain-sender",
            "the send marker removed from the sender, receiver unchanged, its "
            "previous slot writing 0x40: 0x50 or 0x10 shows the marker is what "
            "carries a value across, not the sender's write alone",
            {"A0": mad("r2.l*", SEND),
             "A4": mad("r2.l*", PREV),
             "A5": mad("r0.l*", RECV, "(this)")}),
    variant("recv-alone-s1",
            "a recv in packet 1 slot 1 with nothing else: 0x10, the bare "
            "receiver control",
            {"A5": mad("r0.l*", RECV, "(this)")}),
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


def build_manifest(only=None):
    entries = []
    seen = set()
    for entry in VARIANTS:
        if entry["name"] in seen:
            raise SystemExit(f"duplicate variant {entry['name']}")
        seen.add(entry["name"])
        if only and entry["name"] not in only:
            continue
        known = {name for group in SLOTS for name in group}
        unknown = set(entry["slots"]) - known
        if unknown:
            raise SystemExit(f"{entry['name']}: unknown slots {sorted(unknown)}")
        replace = {"@ABUF@": "1"}
        for placeholders in SLOTS:
            for placeholder in placeholders:
                replace[f"@{placeholder}@"] = entry["slots"].get(
                    placeholder, NOP)
        for index in range(3):
            replace[f"@D{index}@"] = NOP
        replace[f"@D{entry['store']}@"] = entry["word"]
        entries.append({
            "name": entry["name"],
            "fs": TEMPLATE,
            "replace": {"fs": replace},
            "environment": {"GRATE_DUMP_RAW": "1"},
            "expected_note": entry["note"],
        })
    if not entries:
        raise SystemExit("no variants selected")
    return {
        "name": "alu-send-cross",
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
    parser.add_argument("--results", help="run directory to pin from")
    parser.add_argument("--out", help="manifest to write (default: the set's)")
    parser.add_argument("--only", help="comma-separated variant names to keep")
    args = parser.parse_args()

    only = set(args.only.split(",")) if args.only else None
    manifest = build_manifest(only)
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
        print("{:22} {}".format(entry["name"], entry.get("expected", "-")))


if __name__ == "__main__":
    main()
