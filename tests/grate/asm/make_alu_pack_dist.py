#!/usr/bin/env python3
"""Generate the 3+1 and 1+3 four-packet group manifests.

Notes 38 put four ALU packets in one group in the 2 + 2 packing (size 2, two
`ALU:` blocks in each of two batches) and in the 1 + 1 + 1 + 1 packing (size 4,
one block per batch). The forward edges held in both; the wrap 0 <- 3 held in
the 1 + 1 + 1 + 1 packing for latch 0 and only through latch 3 in the 2 + 2 one.
The two shapes differ in packing, batch count and buffer size at once, so the
next step is to change only the packing: the same four packets in a size-2
group, split 3 + 1 and 1 + 3.

    --set 31   three packets in batch 0, one in batch 1   (`asm/alu_pack43.in`)
    --set 13   one packet in batch 0, three in batch 1    (`asm/alu_pack13.in`)

Both templates are six batches - three whole groups at size 2 - and the
assembler derives each batch's schedule count from its block count, so the
upload carries count 3 and count 1 (or 1 and 3) without a patched schedule word.
`dump_sched_words.py` reads the counts back from the run's own raw dumps.

Each packet writes its own byte, so a stored byte names the packet that served
the read:

    packet 0 = u0 = 0.0625 = 0x10      packet 2 = u2 = 0.25  = 0x40
    packet 1 = u1 = 0.125  = 0x20      packet 3 = u3 = 0.375 = 0x60

**The matrices.** Both packings get the complete answers rather than
representative ones:

    fwd-s0s1-al0 .. al3     packet 1 slot L reads aluL written by packet 0 slot L
    fwd-s1s2-al0 .. al3     the 1 -> 2 edge, every latch
    fwd-s2s3-al0 .. al3     the 2 -> 3 edge, every latch
    wrap-alL-rS             packet 3 slot L writes, packet 0 slot S reads aluL,
                            all 16 latch/reading-slot combinations

The wrap set keeps the latch index and the reading slot independent: `wrap-al1-r0`
writes latch 1 and reads from packet 0's slot 0, `wrap-al0-r1` the other way
round. A wrap that works only for one latch, or only for one reading slot, is
therefore visible as a pattern in the 4 x 4 block instead of being hidden by a
combined change.

**Negatives.** Own-packet reads for all four packets, two-packets-apart in both
directions, two non-predecessor reads, a packet-3 write nothing reads, a packet-0
read nothing writes, two packets writing the same latch (which packet supplies
the read), and the all-write and no-write baselines.

**Formats.** `.lh` (fp20) producer read bare, both on a forward edge and on the
wrap; the two format mismatches that must stay zero.

**Same-run baselines.** The 2 + 2 arrangement (notes 38's shape) and the
1 + 1 + 1 + 1 arrangement are re-run in the same manifest as `ref-*` variants, so
this cycle's packing results are compared with arrangements measured in the same
session rather than across runs. `ref-rot2-ctl` and `ref-rot3-ctl` are the
known-good M = 2 and M = 3 rotations from notes 36.

Usage:

    tests/grate/asm/make_alu_pack_dist.py --set 31
    tests/grate/asm/make_alu_pack_dist.py --set 13 --only fwd-s0s1-al0,none
    tests/grate/asm/make_alu_pack_dist.py --set 31 --results DIR   # pin from a run
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = {"31": "tests/grate/asm/alu_pack43.in",
            "13": "tests/grate/asm/alu_pack13.in"}
TEMPLATE_LATCH = "tests/grate/asm/alu_latch.in"
TEMPLATE22 = "tests/grate/asm/alu_pack4.in"
TEMPLATE8 = "tests/grate/asm/alu_buf_blocks8.in"
OUT = {"31": "tests/grate/asm_characterize.alu_pack43.json",
       "13": "tests/grate/asm_characterize.alu_pack13.json"}
NAME = {"31": "alu-pack43", "13": "alu-pack13"}
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"
STORE23 = "store rt1, r2, r3"

PAYLOAD = {0: "0x10", 1: "0x20", 2: "0x40", 3: "0x60"}

# Packet slot placeholders of each packing, packet 0..3.
PACKETS = {
    "31": [["A0", "A1", "A2", "A3"],
           ["A4", "A5", "A6", "A7"],
           ["A8", "A9", "A10", "A11"],
           ["B0", "B1", "B2", "B3"]],
    "13": [["A0", "A1", "A2", "A3"],
           ["B0", "B1", "B2", "B3"],
           ["B4", "B5", "B6", "B7"],
           ["B8", "B9", "B10", "B11"]],
}

SECOND_BATCH = 2          # group B's first batch, stores group A's rows
THIRD_BATCH = 4           # group C's first batch, stores group B's rows


def spec(name, note, writes=(), reads=(), store=SECOND_BATCH,
         word=STORE01, slots=None, abuf=2, template=None):
    return {"name": name, "note": note, "writes": list(writes),
            "reads": list(reads), "store": store, "word": word,
            "slots": slots or {}, "abuf": abuf, "template": template}


def w(packet, slot, fmt=".l*", reg="r2"):
    """A write of the packet's own byte; default target r2 for a row store."""
    return (packet, slot, fmt, reg)


def rd(packet, slot, latch, bare=False):
    return (packet, slot, latch, bare)


def common():
    """The variants both packings ask, described packet-relative."""
    out = []

    # Complete forward matrix: every latch against all three edges.
    for latch in range(4):
        for src, dst in ((0, 1), (1, 2), (2, 3)):
            out.append(spec(
                f"fwd-s{src}s{dst}-al{latch}",
                f"packet {src} slot {latch} writes {PAYLOAD[src]} and packet "
                f"{dst} slot 0 reads alu{latch}: {PAYLOAD[src]} if the "
                f"{src} -> {dst} edge serves latch {latch}",
                writes=[w(src, latch)], reads=[rd(dst, 0, latch)]))

    # Complete wrap matrix: every latch from every reading slot.
    for latch in range(4):
        for rslot in range(4):
            out.append(spec(
                f"wrap-al{latch}-r{rslot}",
                f"packet 3 slot {latch} writes 0x60 and packet 0 slot {rslot} "
                f"reads alu{latch}: 0x60 if the wrap serves latch {latch} from "
                f"reading slot {rslot}",
                writes=[w(3, latch)], reads=[rd(0, rslot, latch)]))

    # Own-packet negatives.
    for packet in range(4):
        out.append(spec(
            f"own-p{packet}",
            f"packet {packet} slot 0 writes {PAYLOAD[packet]} and its own slot "
            f"3 reads alu0: 0 if a packet never sees its own result",
            writes=[w(packet, 0)], reads=[rd(packet, 3, 0)]))

    # Two packets apart, both directions.
    for src, dst in ((0, 2), (1, 3), (2, 0), (3, 1)):
        out.append(spec(
            f"two-p{src}p{dst}",
            f"packet {src} slot 0 writes {PAYLOAD[src]} and packet {dst} slot 0 "
            f"reads alu0: 0 - two apart is neither the predecessor nor the "
            f"next packet",
            writes=[w(src, 0)], reads=[rd(dst, 0, 0)]))

    # Non-predecessor reads: a packet exactly one hop away in the wrong
    # direction.
    out.append(spec(
        "nonpred-p1-reads-p2",
        "packet 2 slot 0 writes 0x40 and packet 1 slot 0 reads alu0: 0 - "
        "packet 1's predecessor is packet 0, so a later packet must not serve it",
        writes=[w(2, 0)], reads=[rd(1, 0, 0)]))
    out.append(spec(
        "nonpred-p2-reads-p3",
        "packet 3 slot 0 writes 0x60 and packet 2 slot 0 reads alu0: 0 - a "
        "backward hop of two is not the wrap",
        writes=[w(3, 0)], reads=[rd(2, 0, 0)]))

    # A write nothing reads, stored from the row.
    out.append(spec(
        "wrap-write-only",
        "packet 3 slot 0 writes 0x60 into r2 and nothing reads it, stored from "
        "the row: 0x60 proves the write itself lands in this packing",
        writes=[w(3, 0, reg="r2")], store=SECOND_BATCH, word=STORE23))

    # A read nothing writes.
    out.append(spec(
        "wrap-nowrite",
        "packet 0 slot 0 reads alu0 with no write anywhere: 0, the no-write "
        "control for the wrap",
        reads=[rd(0, 0, 0)]))

    # Two writers of one latch.
    out.append(spec(
        "both-p1p3-read0",
        "packets 1 (0x20) and 3 (0x60) both write alu0 and packet 0 reads it: "
        "0x60 if the group's predecessor supplies the read, 0x20 if packet 1 "
        "does, 0 if neither",
        writes=[w(1, 0), w(3, 0)], reads=[rd(0, 0, 0)]))
    out.append(spec(
        "both-p0p1-read2",
        "packets 0 (0x10) and 1 (0x20) both write alu0 and packet 2 reads it: "
        "0x20 if the immediate predecessor supplies the read",
        writes=[w(0, 0), w(1, 0)], reads=[rd(2, 0, 0)]))

    # Baselines and the row path.
    out.append(spec(
        "unmarked",
        "all four packets write their own byte and nothing reads: 0, the "
        "all-write baseline",
        writes=[w(0, 0), w(1, 0), w(2, 0), w(3, 0)]))
    out.append(spec(
        "none",
        "no write, one store in the group after: the word baseline",
        ))
    out.append(spec(
        "row-direct",
        "packet 3 writes its byte into r2 and nothing reads it, stored from the "
        "row: 0x60, the control that the last packet's row reaches the store",
        writes=[w(3, 0, reg="r2")], store=SECOND_BATCH, word=STORE23))

    # Formats.
    out.append(spec(
        "lh-fwd",
        "packet 0 writes fp20 (.lh) 0.0625 and packet 1 slot 0 reads bare: 0x10 "
        "if the fp20 path forwards the one-hop source",
        writes=[w(0, 0, fmt=".lh")], reads=[rd(1, 0, 0, bare=True)]))
    out.append(spec(
        "lh-wrap",
        "packet 3 writes fp20 0.375 and packet 0 slot 0 reads bare: 0x60 if the "
        "wrap holds through the fp20 path too",
        writes=[w(3, 0, fmt=".lh")], reads=[rd(0, 0, 0, bare=True)]))
    out.append(spec(
        "lh-write-l-read",
        "packet 3 writes fp20 0.375 and packet 0 reads .l: 0, the format rule "
        "of notes 28",
        writes=[w(3, 0, fmt=".lh")], reads=[rd(0, 0, 0)]))
    out.append(spec(
        "bare-read",
        "packet 3 writes fx10 and packet 0 reads bare: 0, the same rule the "
        "other way round",
        writes=[w(3, 0)], reads=[rd(0, 0, 0, bare=True)]))
    return out


def block(prefix, count):
    return [f"{prefix}{index}" for index in range(count)]


# Every placeholder a template contains, so an unmentioned slot can be filled
# with NOP: the runner replaces what the manifest names, not what it omits.
PLACEHOLDERS = {
    TEMPLATE["31"]: (block("A", 12) + block("B", 4) + block("C", 4)
                     + block("E", 4) + block("F", 4) + block("G", 4)),
    TEMPLATE["13"]: (block("A", 4) + block("B", 12) + block("C", 4)
                     + block("E", 4) + block("F", 4) + block("G", 4)),
    TEMPLATE_LATCH: (block("A", 4) + block("B", 4) + block("C", 4)
                     + block("E", 4) + block("F", 4) + block("G", 4)),
    TEMPLATE22: (block("A", 8) + block("B", 8) + block("C", 4)
                 + block("E", 4) + block("F", 4) + block("G", 4)),
    TEMPLATE8: block("B", 8),
}

BATCHES = {TEMPLATE["31"]: 6, TEMPLATE["13"]: 6, TEMPLATE_LATCH: 6,
           TEMPLATE22: 6, TEMPLATE8: 8}

REFERENCES = [
    # Notes 38's 2 + 2 shape, re-measured in this session.
    spec("ref22-fwd01",
         "the 2 + 2 packing (notes 38): packet 0 slot 0 writes 0x10 and packet "
         "1 slot 0 reads alu0: 0x10, the same-run forward baseline",
         slots={"A0": "MAD  r2.l*,  u0,  #1,  #0",
                "A4": "MAD  r0.l*,  alu0.l,  #1,  #0"},
         template=TEMPLATE22),
    spec("ref22-wrap-al0",
         "the 2 + 2 packing: packet 3 slot 0 writes 0x60 and packet 0 slot 0 "
         "reads alu0: 0, notes 38's latch-0 wrap result, re-measured here",
         slots={"B0": "MAD  r2.l*,  u3,  #1,  #0",
                "A0": "MAD  r0.l*,  alu0.l,  #1,  #0"},
         template=TEMPLATE22),
    spec("ref22-wrap-al3",
         "the 2 + 2 packing: packet 3 slot 3 writes 0x60 and packet 0 slot 3 "
         "reads alu3: 0x60, notes 38's one working wrap",
         slots={"B7": "MAD  r2.l*,  u3,  #1,  #0",
                "A3": "MAD  r0.l*,  alu3.l,  #1,  #0"},
         template=TEMPLATE22),
    # The one-packet-per-batch reference.
    spec("ref11-wrap",
         "the 1 + 1 + 1 + 1 packing (notes 38): size 4, batch 3 slot 0 writes "
         "0x60 and batch 0 slot 0 reads alu0: 0x60, the full-wrap reference",
         slots={"B3": "MAD  r2.l*,  u3,  #1,  #0",
                "B0": "MAD  r0.l*,  alu0.l,  #1,  #0"},
         abuf=4, template=TEMPLATE8, store=4),
    # Known-good rotations from notes 36.
    spec("ref-rot2-ctl",
         "size 2, one packet per batch, batch 0 writes 0.25 (0x40) and batch 1 "
         "reads alu0: 0x40 - notes 36's known-good M = 2 rotation",
         slots={"A0": "MAD  r2.l*,  u1,  #1,  #0",
                "B0": "MAD  r0.l*,  alu0.l,  #1,  #0"},
         template=TEMPLATE_LATCH),
    spec("ref-rot3-ctl",
         "size 3, one packet per batch, batch 1 writes 0.5 (0x7F) and batch 2 "
         "reads alu0: 0x7F - notes 36's known-good M = 3 rotation",
         slots={"B0": "MAD  r2.l*,  u2,  #1,  #0",
                "C0": "MAD  r0.l*,  alu0.l,  #1,  #0"},
         abuf=3, template=TEMPLATE_LATCH, store=3),
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

VARIANTS = {key: common() + REFERENCES for key in TEMPLATE}


PACKETS_BY_TEMPLATE = {TEMPLATE[key]: PACKETS[key] for key in PACKETS}


def slots_for(key, entry):
    """Placeholder mapping for one variant."""
    if entry["slots"]:
        return dict(entry["slots"])
    template = entry["template"] or TEMPLATE[key]
    packets = PACKETS_BY_TEMPLATE[template]
    slots = {}
    for packet, slot, fmt, reg in entry["writes"]:
        slots[packets[packet][slot]] = \
            f"MAD  {reg}{fmt},  u{packet},  #1,  #0"
    for packet, slot, latch, bare in entry["reads"]:
        source = f"alu{latch}" if bare else f"alu{latch}.l"
        slots[packets[packet][slot]] = f"MAD  r0.l*,  {source},  #1,  #0"
    return slots


def build_manifest(key, only=None):
    entries = []
    seen = set()
    for entry in VARIANTS[key]:
        if entry["name"] in seen:
            raise SystemExit(f"duplicate variant {entry['name']}")
        seen.add(entry["name"])
        if only and entry["name"] not in only:
            continue
        template = entry["template"] or TEMPLATE[key]
        slots = slots_for(key, entry)
        unknown = set(slots) - set(PLACEHOLDERS[template])
        if unknown:
            raise SystemExit(f"{entry['name']}: unknown slots {sorted(unknown)}")
        replace = {"@ABUF@": str(entry["abuf"])}
        for name in PLACEHOLDERS[template]:
            replace[f"@{name}@"] = slots.get(name, NOP)
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
        "name": NAME[key],
        "repeat": 3,
        "timeout": 20,
        "program": {"vs": VS, "fs": TEMPLATE[key], "lnk": LNK},
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
    parser.add_argument("--set", dest="key", choices=sorted(TEMPLATE),
                        default="31", help="packing to build")
    parser.add_argument("--results", help="run directory to pin from")
    parser.add_argument("--out", help="manifest to write (default: the set's)")
    parser.add_argument("--only", help="comma-separated variant names to keep")
    args = parser.parse_args()

    only = set(args.only.split(",")) if args.only else None
    manifest = build_manifest(args.key, only)
    out = args.out or OUT[args.key]
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
