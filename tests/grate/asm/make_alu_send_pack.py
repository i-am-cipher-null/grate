#!/usr/bin/env python3
"""Generate the send/recv two-packet manifest.

Notes 13 and 16 established what the two ALU markers do inside one packet:
`(this)` is the recv bit and overrides rC with the previous slot's result,
`(other)` is the send bit, and the transfer set is (0,1) (1,0) (1,2) (2,0)
(2,3) (3,0) (3,2). They also measured that a send in one batch and a recv in the
next does *not* accumulate, but every one of those batches held a single packet,
so nothing is known about either marker inside a multi-packet batch.

Task 2's two-packet template is the right vehicle: two `ALU:` blocks in batch 0
put packet 0 and packet 1 in one batch, and notes 36 shows that a read in packet
N sees packet N-1's slot results (circularly, so packet 0 sees packet 1).
Whether the send/recv path follows the same rotation is what this set asks:

    recv-p1-s0          recv alone in packet 1: its own value, accumulator 0
    recv-p0-s0          recv alone in packet 0: the same
    send-p0-recv-p1     send in packet 0 slot 3, recv in packet 1 slot 0:
                        0x50 if the accumulator crosses the packet boundary,
                        0x10 if it does not
    plain-p0-recv-p1    the same instructions without the markers: 0x10 is the
                        control that the difference is the marker
    send-p1-recv-p0     the two markers swapped in direction, which is the
                        circular-previous pair for packet 0
    send-p1-recv-p1     both markers inside packet 1, on a transfer pair the
                        one-packet matrix says works (slot 1 sends, slot 2
                        receives)
    plain-p1-recv-p1    its unmarked twin
    send-only-p0        the sender alone, observed through r2/r3: the sender's
                        own write must not depend on the marker
    send-two-p0         two senders in packet 0, a receiver in packet 1: notes
                        13 measured the last sender's value in one packet
    carry-recv-p1       batch 0's last packet slot 0 is the receiver, batch 1
                        reads alu0: does the group carry hold the accumulated
                        value (0x50) or the plain result (0x10)
    carry-plain-p1      the unmarked twin, 0x10

Every variant stores in a later batch than the packet under test, because a row
written inside a batch is not visible until the next group (notes 28).
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/alu_pack2.in"
OUT = "tests/grate/asm_characterize.alu_send_pack.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"
STORE23 = "store rt1, r2, r3"

U0, U1, U2 = 0, 1, 2

SLOTS = (["A0", "A1", "A2", "A3"], ["A4", "A5", "A6", "A7"],
         ["B0", "B1", "B2", "B3"], ["C0", "C1", "C2", "C3"])


def mad(dst, const, marker=""):
    suffix = f" {marker}" if marker else ""
    return f"MAD  {dst},  u{const},  #1,  #0{suffix}"


def variant(name, note, slots, dws):
    return {"name": name, "note": note, "slots": slots, "dws": dws}


VARIANTS = [
    variant("recv-p1-s0",
            "recv alone in packet 1 slot 0: 0x10, its own value, no "
            "accumulator to add",
            {1: mad("r0.l*", U0, "(this)")}, {1: STORE01}),
    variant("recv-p0-s0",
            "recv alone in packet 0 slot 0: the same value",
            {0: mad("r0.l*", U0, "(this)")}, {1: STORE01}),
    variant("send-p0-recv-p1",
            "send 0.25 in packet 0 slot 3, recv 0.0625 in packet 1 slot 0: "
            "0x50 if the accumulator crosses the packet boundary, 0x10 if the "
            "markers are confined to a packet",
            {3: mad("r2.l*", U1, "(other)"),
             4: mad("r0.l*", U0, "(this)")}, {1: STORE01}),
    variant("plain-p0-recv-p1",
            "the same two instructions without the markers: 0x10 is the "
            "control that any difference comes from the markers",
            {3: mad("r2.l*", U1), 4: mad("r0.l*", U0)}, {1: STORE01}),
    variant("send-p1-recv-p0",
            "the markers the other way round: send in packet 1 slot 3, recv in "
            "packet 0 slot 0, which is the pair notes 36 found for latch reads",
            {0: mad("r0.l*", U0, "(this)"),
             7: mad("r2.l*", U1, "(other)")}, {1: STORE01}),
    variant("send-p1-recv-p1",
            "both markers inside packet 1 on the (1,2) pair the one-packet "
            "matrix says transfers: 0x50 if that path still works in a "
            "packet that is not the batch's first",
            {5: mad("r2.l*", U1, "(other)"),
             6: mad("r0.l*", U0, "(this)")}, {1: STORE01}),
    variant("plain-p1-recv-p1",
            "the unmarked twin of the pair inside packet 1: 0x10",
            {5: mad("r2.l*", U1), 6: mad("r0.l*", U0)}, {1: STORE01}),
    variant("send-only-p0",
            "the sender alone in packet 0 slot 3, stored through r2/r3: 0x40, "
            "so the marker does not change what a sender writes",
            {3: mad("r2.l*", U1, "(other)")}, {1: STORE23}),
    variant("send-two-p0",
            "two senders in packet 0 (0.0625 and 0.25) and a receiver in "
            "packet 1: notes 13 measured the last sender's value in one packet, "
            "0x50 here",
            {0: mad("r2.l*", U0, "(other)"), 1: mad("r2.l*", U1, "(other)"),
             4: mad("r0.l*", U0, "(this)")}, {1: STORE01}),
    variant("send-then-plain-read",
            "packet 0 slot 0 writes with the send marker, packet 1 slot 0 "
            "reads the latch plainly: 0x10 if the marker leaves the forward "
            "path between packets alone, 0 if sending consumes it",
            {0: mad("r2.l*", U0, "(other)"),
             4: "MAD  r0.l*,  alu0.l,  #1,  #0"}, {1: STORE01}),
    variant("carry-recv-p1",
            "batch 0's last packet slot 0 is the receiver of packet 0's "
            "sender; batch 1 reads alu0 slot 0 and batch 2 stores: 0x50 if the "
            "group carry holds the accumulated result, 0x10 if it holds the "
            "plain result",
            {3: mad("r2.l*", U1, "(other)"),
             4: mad("r0.l*", U0, "(this)"),
             8: "MAD  r0.l*,  alu0.l,  #1,  #0"}, {2: STORE01}),
    variant("carry-plain-p1",
            "the unmarked twin of the carry test: 0x10",
            {3: mad("r2.l*", U1), 4: mad("r0.l*", U0),
             8: "MAD  r0.l*,  alu0.l,  #1,  #0"}, {2: STORE01}),
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
        for batch, placeholders in enumerate(SLOTS):
            slots = entry["slots"]
            for slot, placeholder in enumerate(placeholders):
                replace[f"@{placeholder}@"] = slots.get(batch * 4 + slot, NOP)
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
        "name": "alu-send-pack",
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
        print("{:20} {}".format(entry["name"], entry.get("expected", "-")))


if __name__ == "__main__":
    main()
