#!/usr/bin/env python3
"""Generate the ALU latch flush-carry manifest.

Section 28 established that one latch read survives a group flush: `alu0`, read
from slot 0, of a write that slot 0 issued in the previous group. What that
value *is* was not settled, and three hypotheses predict different words:

  forwarded   the flush forwards the previous group's slot-0 result. Nothing is
              stored, so a group whose slot 0 wrote nothing carries nothing,
              and the value is one group deep.
  row-alias   `alu0` at flush distance reads a row register that the previous
              group's write landed in. Which row the writer picked would then
              matter, and reading through the row path would agree with it.
  register    the flush keeps a real saved register that holds until something
              overwrites it, whether or not the idling group's slot 0 writes.

Every variant writes with one slot and reads with one slot, one group apart
unless its name says otherwise, at `alu_buffer_size` 1 where every batch is its
own group. The reader always writes `r0.lo`, so a zero in the stored word is a
zero read and never an unwritten pixel.

    flush-carry        writer r2, reader slot 0          replicate section 28
    flush-carry-r5     writer r5                        does the row index matter
    flush-carry-r7     writer r7                        other end of the bank
    flush-two-values   b0 writes r2 = 0x10, b1 writes r6 = 0x40, b2 reads
                       previous group's slot-0 result (0x40) or an older saved
                       value (0x10) or the row alias (0)
    flush-idle         b0 writes, b1 is all NOP, b2 reads
                       forwarded (0) or a register that holds (0x10)
    flush-two-deep     b0 writes, b1 and b2 NOP, b3 reads
    flush-writer-s1    the writer is slot 1 instead of slot 0
    flush-writer-s3    the writer is slot 3
    flush-reader-s1    the reader is slot 1 instead of slot 0
    flush-row-direct   the reader copies r2.lo instead of alu0: the control
                       that a row write at this distance does reach the store
    flush-writer-hi    the writer wrote a high half only
    flush-carry-bare   the reader is written bare (the fp20 form)
    flush-busy-middle  b0 writes through slot 0, b1 writes through slot 3, b2
                       reads: does another group's activity disturb the carry

`--results DIR` pins each variant to the modal word of its repeats.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/alu_latch.in"
OUT = "tests/grate/asm_characterize.alu_latch_flush.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"

# u0 = 0.0625 -> 0x10, u1 = 0.25 -> 0x40, u3 = 0.125 -> 0x20
U0, U1 = 0, 1


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


# Slot index: 0..3 = batch 0, 4..7 = batch 1, 8..11 = batch 2, 12..15 = batch 3,
# 16..19 = batch 4, 20..23 = batch 5. The store sits in the reader's batch,
# which at size 1 is a group of its own.
VARIANTS = [
    variant("flush-carry",
            {0: wr("r2.l*", U0), 4: rd("r0.l*", 0)},
            {1: STORE01},
            "the section 28 shape again: writer in batch 0, reader in batch 1, "
            "0x10 if the flush carries the previous group's slot-0 result"),
    variant("flush-carry-r5",
            {0: wr("r5.l*", U0), 4: rd("r0.l*", 0)},
            {1: STORE01},
            "the same with the writer's destination at r5, a row `alu0` has no "
            "reason to alias: 0 means the carry is the latch, not a row"),
    variant("flush-carry-r7",
            {0: wr("r7.l*", U0), 4: rd("r0.l*", 0)},
            {1: STORE01},
            "the writer's destination at the last row of the bank"),
    variant("flush-two-values",
            {0: wr("r2.l*", U0), 4: wr("r6.l*", U1), 8: rd("r0.l*", 0)},
            {2: STORE01},
            "two groups each write through slot 0 (0.0625 then 0.25) and the "
            "third reads: 0x40 for the previous group's result, 0x10 for an "
            "older saved value, 0 for the row alias"),
    variant("flush-idle",
            {0: wr("r2.l*", U0), 8: rd("r0.l*", 0)},
            {2: STORE01},
            "an all-NOP group between the write and the read: 0 if the flush "
            "forwards, 0x10 if a register holds"),
    variant("flush-two-deep",
            {0: wr("r2.l*", U0), 12: rd("r0.l*", 0)},
            {3: STORE01},
            "three groups after the write"),
    variant("flush-writer-s1",
            {1: wr("r2.l*", U0), 4: rd("r0.l*", 0)},
            {1: STORE01},
            "the write came from slot 1, not slot 0"),
    variant("flush-writer-s3",
            {3: wr("r2.l*", U0), 4: rd("r0.l*", 0)},
            {1: STORE01},
            "the write came from slot 3"),
    variant("flush-reader-s1",
            {0: wr("r2.l*", U0), 5: rd("r0.l*", 0)},
            {1: STORE01},
            "the read is issued by slot 1, not slot 0"),
    variant("flush-row-direct",
            {0: wr("r2.l*", U0), 4: row("r0.l*", "r2.l")},
            {1: STORE01},
            "the row path at the same distance: the control that a value can "
            "reach this store at all"),
    variant("flush-writer-hi",
            {0: wr("r2.*h", U0), 4: rd("r0.l*", 0)},
            {1: STORE01},
            "the writer filled a high half only"),
    variant("flush-carry-bare",
            {0: wr("r2.l*", U0), 4: "MAD  r0.l*,  alu0,  #1,  #0"},
            {1: STORE01},
            "the reader written bare (the fp20 form) at flush distance"),
    variant("flush-busy-middle",
            {0: wr("r2.l*", U0), 7: wr("r3.l*", U1), 8: rd("r0.l*", 0)},
            {2: STORE01},
            "the group between the write and the read wrote through slot 3: 0 "
            "if the carry is the previous group's slot-0 result, 0x10 if it is "
            "a register nothing overwrote"),
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
    names = [entry["name"] for entry in VARIANTS]
    if len(names) != len(set(names)):
        raise SystemExit("variant names are not unique")
    entries = []
    for entry in VARIANTS:
        replace = {"@ABUF@": str(entry["abuf"])}
        for batch, prefix in enumerate(("A", "B", "C", "E", "F", "G")):
            for slot in range(4):
                index = batch * 4 + slot
                replace[f"@{prefix}{slot}@"] = entry["slots"].get(index, NOP)
            replace[f"@D{batch}@"] = entry["dws"].get(batch, NOP)
        entries.append({
            "name": entry["name"],
            "fs": TEMPLATE,
            "replace": {"fs": replace},
            "expected_note": entry["note"],
        })
    return {
        "name": "alu-latch-flush",
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
