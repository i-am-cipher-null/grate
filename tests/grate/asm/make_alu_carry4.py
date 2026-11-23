#!/usr/bin/env python3
"""Generate the `alu_buffer_size` 4 group-boundary carry manifest.

Notes 31 and 32 measured a carry from one group to the next at
`alu_buffer_size` 1: the last packet's slot-0 result is readable by slot 0 of
the next group. Notes 36 and 38 looked for it at sizes 2, 3 and 4 in
one-packet-per-batch shapes and found nothing. That leaves two questions the
previous sets could not separate: whether size 4 has a carry shape that they
did not try, and whether the writer's position inside the group matters.

The template is `asm/alu_buf_blocks12.in`: twelve batches, one ALU packet each
with four slots, which is exactly three size-4 groups.

    group A  batches 0..3    the writer
    group B  batches 4..7    the reader
    group C  batches 8..11   the store, in group C's first batch

A row written in a group only reaches a store in the group after it (notes 3
and 28), which is why a read in group B is stored from group C rather than from
group B's own first batch - the store and the read would otherwise be the same
instruction and a zero could mean either. The same template runs at sizes 2 and
3 as the reference arrangements in the identical shape.

    carry-p3-l0-r0    group A's last packet writes alu0; group B packet 0 slot
                      0 reads it -> 0 if nothing crosses
    carry-p3-l0-r1    the same write read from group B slot 1, so the reading
                      slot is not the variable
    carry-p0/1/2-l0   the same question with the writer in an earlier packet of
                      the group, to see whether "last packet" is what would
                      make a carry special
    carry-p3-l1-r0/2  the third latch instead of the first, read from two slots
    carry-none        no write: the no-write control
    row-direct        group A's last packet writes r2, stored from group C: 0x60
                      proves the group transition and the store path work
    fwd-within-s4     batch 2 writes, batch 3 reads, stored from group B: 0x40,
                      the positive control that a within-group read at size 4
                      reaches a store in the next group
    back-within-s4    batch 3 writes, batch 2 reads: 0, the non-predecessor
                      backward read inside a size-4 group
    carry-ref-s2/s3   the same write/read/store shape at sizes 2 and 3
    carry-ctl-s1      the size-1 arrangement that does carry (notes 31): 0x10

Usage:

    tests/grate/asm/make_alu_carry4.py
    tests/grate/asm/make_alu_carry4.py --only carry-p3-l0-r0,row-direct
    tests/grate/asm/make_alu_carry4.py --results DIR     # pin from a run
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/alu_buf_blocks12.in"
TEMPLATE_LATCH = "tests/grate/asm/alu_latch.in"
OUT = "tests/grate/asm_characterize.alu_carry4.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"
STORE23 = "store rt1, r2, r3"

BLOCKS12_SLOTS = [f"Q{batch:02d}{slot}" for batch in range(12)
                  for slot in range(4)]
LATCH_SLOTS = [f"{prefix}{index}" for prefix in ("A", "B", "C", "E", "F", "G")
               for index in range(4)]


def wr(const, fmt=".l*", reg="r2"):
    return f"MAD  {reg}{fmt},  u{const},  #1,  #0"


def rd(latch, dst="r0.l*", bare=False):
    source = f"alu{latch}" if bare else f"alu{latch}.l"
    return f"MAD  {dst},  {source},  #1,  #0"


def variant(name, note, slots, store, abuf=4, word=STORE01,
            template=TEMPLATE):
    return {"name": name, "note": note, "slots": slots, "store": store,
            "abuf": abuf, "word": word, "template": template}


def slot(batch, index):
    return f"Q{batch:02d}{index}"


VARIANTS = [
    variant("carry-p3-l0-r0",
            "group A's last packet (batch 3) slot 0 writes 0x60 and group B's "
            "first packet reads alu0 from slot 0, stored by group C: 0x60 if a "
            "size-4 group carries, 0 if it does not",
            {slot(3, 0): wr(3), slot(4, 0): rd(0)}, store=8),
    variant("carry-p3-l0-r1",
            "the same write read from group B slot 1 instead: the reading slot "
            "is not what decides",
            {slot(3, 0): wr(3), slot(4, 1): rd(0)}, store=8),
    variant("carry-p0-l0",
            "group A's first packet writes 0x10, group B reads alu0: 0 - the "
            "earlier-writer control for `carry-p3-l0-r0`",
            {slot(0, 0): wr(0), slot(4, 0): rd(0)}, store=8),
    variant("carry-p1-l0",
            "group A's second packet writes 0x20, group B reads alu0: 0",
            {slot(1, 0): wr(1), slot(4, 0): rd(0)}, store=8),
    variant("carry-p2-l0",
            "group A's third packet writes 0x40, group B reads alu0: 0",
            {slot(2, 0): wr(2), slot(4, 0): rd(0)}, store=8),
    variant("carry-p3-l1-r2",
            "group A's last packet slot 1 writes 0x60 and group B reads alu1 "
            "from slot 2: 0x60 if the carry is not latch-0-only, and the "
            "reading slot differs from the latch index",
            {slot(3, 1): wr(3), slot(4, 2): rd(1)}, store=8),
    variant("carry-p3-l1-r0",
            "the same latch-1 write read from group B slot 0",
            {slot(3, 1): wr(3), slot(4, 0): rd(1)}, store=8),
    variant("carry-none",
            "no write anywhere, group B reads alu0: 0, the no-write control",
            {slot(4, 0): rd(0)}, store=8),
    variant("row-direct",
            "group A's last packet writes 0x60 into r2, stored by group C from "
            "the row: 0x60 - the group transition and the store path work",
            {slot(3, 0): wr(3, reg="r2")}, store=8, word=STORE23),
    variant("fwd-within-s4",
            "batch 2 writes 0x40 and batch 3 reads alu0 inside group A, stored "
            "from group B: 0x40 - the positive control that a within-group "
            "read is observable from the next group",
            {slot(2, 0): wr(2), slot(3, 0): rd(0)}, store=4),
    variant("back-within-s4",
            "batch 3 writes 0x60 and batch 2 reads alu0, both inside group A: "
            "0 - a later packet does not serve an earlier one when it is not "
            "the group's predecessor",
            {slot(3, 0): wr(3), slot(2, 0): rd(0)}, store=4),
    variant("carry-ref-s2",
            "the identical write/read/store shape at size 2: group A's last "
            "batch (1) writes 0x20, batch 2 reads, batch 4 stores: 0, the "
            "size-2 reference",
            {slot(1, 0): wr(1), slot(2, 0): rd(0)}, store=4, abuf=2),
    variant("carry-ref-s3",
            "the same at size 3: batch 2 writes 0x40, batch 3 reads, batch 6 "
            "stores: 0, the size-3 reference",
            {slot(2, 0): wr(2), slot(3, 0): rd(0)}, store=6, abuf=3),
    variant("carry-ctl-s1",
            "size 1 on `alu_latch.in`: batch 0 writes 0.0625 to alu0 and batch "
            "1 slot 0 reads it: 0x10 - the group-boundary carry that does work "
            "(notes 31)",
            {"A0": wr(0), "B0": rd(0)}, store=2, abuf=1,
            template=TEMPLATE_LATCH),
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

BATCHES = {TEMPLATE: 12, TEMPLATE_LATCH: 6}
PLACEHOLDERS = {TEMPLATE: BLOCKS12_SLOTS, TEMPLATE_LATCH: LATCH_SLOTS}


def build_manifest(only=None):
    entries = []
    seen = set()
    for entry in VARIANTS:
        if entry["name"] in seen:
            raise SystemExit(f"duplicate variant {entry['name']}")
        seen.add(entry["name"])
        if only and entry["name"] not in only:
            continue
        template = entry["template"]
        unknown = set(entry["slots"]) - set(PLACEHOLDERS[template])
        if unknown:
            raise SystemExit(f"{entry['name']}: unknown slots {sorted(unknown)}")
        replace = {"@ABUF@": str(entry["abuf"])}
        for name in PLACEHOLDERS[template]:
            replace[f"@{name}@"] = entry["slots"].get(name, NOP)
        for batch in range(BATCHES[template]):
            replace[f"@D{batch}@"] = NOP
        replace[f"@D{entry['store']}@"] = entry["word"]
        entries.append({
            "name": entry["name"],
            "fs": template,
            "replace": {"fs": replace},
            "environment": {"GRATE_DUMP_RAW": "1"},
            "expected_note": entry["note"],
        })
    if not entries:
        raise SystemExit("no variants selected")
    return {
        "name": "alu-carry4",
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
