#!/usr/bin/env python3
"""Generate the four-packet group manifests for the size-3 distributions.

Notes 38 and 39 measured the M = 4 latch rotation in three packings that all
share a two-batch group: 2 + 2 at `alu_buffer_size` 2 gives the forward edges
but only latch 3's wrap, while 3 + 1 and 1 + 3 give the complete map. Notes 38
also measured the entire cycle for latch 0 with one packet per batch at size 4.
So the packing and the number of batches had never been varied independently.

This generator builds the four arrangements that separate them.

**Three batches, four packets.** `asm/alu_dist211.in`, `asm/alu_dist121.in` and
`asm/alu_dist112.in` put the same four packets in a three-batch group at
`alu_buffer_size` 3, with the two-packet batch first, second and last:

    dist211   batch 0: packets 0,1   batch 1: packet 2   batch 2: packet 3
    dist121   batch 0: packet 0      batch 1: packets 1,2  batch 2: packet 3
    dist112   batch 0: packet 0      batch 1: packet 1   batch 2: packets 2,3

If the 2 + 2 shortfall is caused by having two multi-packet batches, all three
should have the complete map, because each of them holds only one multi-packet
batch. If it follows the position of that batch (or of packet 0 or packet 3),
the three differ from each other. `dist112` is the sharpest of the three: there
packet 3 shares its batch with packet 2, the local shape it has in 2 + 2, while
the group has three batches.

**Four batches, one packet each.** `asm/alu_blocks4.in` repeats the size-4
reference of notes 38 with all four slots per batch instead of one, so the
forward and wrap matrices can be completed for every latch rather than latch 0.

Every set carries the same systematic matrix, built by the loops in
`matrix()` rather than typed by hand:

    fwd{src}{dst}-alN    packet src slot N writes, packet dst slot 0 reads aluN
                         (3 edges x 4 latches = 12)
    fwd01-al0-s2,        two of the forward cells re-read from a different slot
    fwd23-al3-s2
    wrap-alN-sS          packet 3 slot N writes, packet 0 slot S reads aluN
                         (4 latches x 4 reading slots = 16)
    own-p0 .. own-p3     a packet reads alu0 from another of its own slots
    pair-pW-pR           the packet two before or after the reader
    back-pW-pR           one hop backwards, the successor direction
    both-...             two packets writing one latch, one reader
    write-only-p3        the write with no reader, stored from the row
    read-nowrite         a read with no writer
    unmarked, none       all writes and no read, and nothing at all
    row-direct           a row write stored through r2

The write values are per packet - u0 = 0.0625 (0x10), u1 = 0.125 (0x20), u2 =
0.25 (0x40), u3 = 0.375 (0x60) as fx10 - so a stored byte names the packet that
served the read. The latch index is the writer's slot, and the wrap varies the
reader's slot independently of it.

Each manifest also carries the same seven cross-shape controls, built on the
templates that already have recorded runs, so a run that returns unexpected
cells can be told apart from a broken harness: the size-2 two-packet rotation
(0x40), the size-3 rotation (0x7F), the size-1 group-boundary carry (0x10), the
2 + 2 wrap through latch 0 (0x00) and latch 3 (0x60), and the 3 + 1 wrap (0x60)
and forward hop (0x10).

Usage:

    tests/grate/asm/make_alu_m4dist.py --set dist211
    tests/grate/asm/make_alu_m4dist.py --set dist211 --only wrap-al0-s0,none
    tests/grate/asm/make_alu_m4dist.py --set dist211 --results DIR
"""

import argparse
import csv
import json
import pathlib
import re

T211 = "tests/grate/asm/alu_dist211.in"
T121 = "tests/grate/asm/alu_dist121.in"
T112 = "tests/grate/asm/alu_dist112.in"
T4 = "tests/grate/asm/alu_blocks4.in"
TPACK4 = "tests/grate/asm/alu_pack4.in"
TPACK43 = "tests/grate/asm/alu_pack43.in"
TLATCH = "tests/grate/asm/alu_latch.in"

VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"
STORE23 = "store rt1, r2, r3"

U0, U1, U2, U3 = 0, 1, 2, 3

LAYOUT = {
    "dist211": (
        ("A0", "A1", "A2", "A3"),
        ("A4", "A5", "A6", "A7"),
        ("B0", "B1", "B2", "B3"),
        ("C0", "C1", "C2", "C3"),
    ),
    "dist121": (
        ("A0", "A1", "A2", "A3"),
        ("B0", "B1", "B2", "B3"),
        ("B4", "B5", "B6", "B7"),
        ("C0", "C1", "C2", "C3"),
    ),
    "dist112": (
        ("A0", "A1", "A2", "A3"),
        ("B0", "B1", "B2", "B3"),
        ("C0", "C1", "C2", "C3"),
        ("C4", "C5", "C6", "C7"),
    ),
    "blocks4": (
        ("A0", "A1", "A2", "A3"),
        ("B0", "B1", "B2", "B3"),
        ("C0", "C1", "C2", "C3"),
        ("E0", "E1", "E2", "E3"),
    ),
}

TEMPLATE = {"dist211": T211, "dist121": T121, "dist112": T112,
            "blocks4": T4}
ABUF = {"dist211": 3, "dist121": 3, "dist112": 3, "blocks4": 4}
STORE_BATCH = {"dist211": 3, "dist121": 3, "dist112": 3, "blocks4": 4}
PREFIX = {"dist211": "211-", "dist121": "121-", "dist112": "112-",
          "blocks4": "s4-"}
OUT = {
    "dist211": "tests/grate/asm_characterize.alu_dist211.json",
    "dist121": "tests/grate/asm_characterize.alu_dist121.json",
    "dist112": "tests/grate/asm_characterize.alu_dist112.json",
    "blocks4": "tests/grate/asm_characterize.alu_blocks4.json",
}

BYTE = {U0: "0x10", U1: "0x20", U2: "0x40", U3: "0x60"}


def wr(const, fmt=".l*"):
    return f"MAD  r2{fmt},  u{const},  #1,  #0"


def rd(latch):
    return f"MAD  r0.l*,  alu{latch}.l,  #1,  #0"


def var(section, name, note, writes=(), reads=(), store=STORE01):
    layout = LAYOUT[section]
    slots = {}
    for packet, slot, const in writes:
        placeholder = layout[packet][slot]
        if placeholder in slots:
            raise SystemExit(f"{name}: slots {placeholder} written twice")
        slots[placeholder] = wr(const)
    for packet, slot, latch in reads:
        placeholder = layout[packet][slot]
        if placeholder in slots:
            raise SystemExit(f"{name}: slots {placeholder} both written and read")
        slots[placeholder] = rd(latch)
    dws = {STORE_BATCH[section]: store} if store else {}
    return {
        "name": PREFIX[section] + name,
        "note": note,
        "slots": slots,
        "dws": dws,
        "abuf": ABUF[section],
        "template": TEMPLATE[section],
    }


def matrix(section):
    out = []

    # The three adjacent forward edges, every latch.
    for latch in range(4):
        for src, dst, const in ((0, 1, U0), (1, 2, U1), (2, 3, U2)):
            out.append(var(
                section, f"fwd{src}{dst}-al{latch}",
                f"packet {src} slot {latch} writes {BYTE[const]}, packet {dst} "
                f"slot 0 reads alu{latch}: {BYTE[const]} if the one-hop forward "
                f"edge carries latch {latch}",
                [(src, latch, const)], [(dst, 0, latch)]))

    # Two forward cells re-read from another slot.
    out.append(var(
        section, "fwd01-al0-s2",
        "packet 0 slot 0 writes 0x10, packet 1 slot 2 reads alu0: 0x10 if the "
        "reading slot is free",
        [(0, 0, U0)], [(1, 2, 0)]))
    out.append(var(
        section, "fwd23-al3-s2",
        "packet 2 slot 3 writes 0x40, packet 3 slot 2 reads alu3: 0x40",
        [(2, 3, U2)], [(3, 2, 3)]))

    # The wrap: writer latch and reading slot varied independently.
    for latch in range(4):
        for rslot in range(4):
            out.append(var(
                section, f"wrap-al{latch}-s{rslot}",
                f"packet 3 slot {latch} writes 0x60, packet 0 slot {rslot} "
                f"reads alu{latch}: 0x60 if packet 3's latch-{latch} result "
                f"reaches packet 0 from reading slot {rslot}",
                [(3, latch, U3)], [(0, rslot, latch)]))

    # Own-packet negatives.
    for packet in range(4):
        out.append(var(
            section, f"own-p{packet}",
            f"packet {packet} slot 0 writes {BYTE[packet]}, its own slot 2 "
            f"reads alu0: 0 if a packet never sees its own result",
            [(packet, 0, packet)], [(packet, 2, 0)]))

    # Two packets apart, both directions.
    for writer, reader in ((0, 2), (2, 0), (1, 3), (3, 1)):
        out.append(var(
            section, f"pair-p{writer}-p{reader}",
            f"packet {writer} slot 0 writes {BYTE[writer]}, packet {reader} "
            f"slot 0 reads alu0: 0 if two packets apart is not a forward",
            [(writer, 0, writer)], [(reader, 0, 0)]))

    # One hop backwards, the successor direction.
    for writer, reader in ((1, 0), (2, 1), (3, 2)):
        out.append(var(
            section, f"back-p{writer}-p{reader}",
            f"packet {writer} slot 0 writes {BYTE[writer]}, packet {reader} "
            f"slot 0 reads alu0: 0 if a later packet does not serve an earlier "
            f"one",
            [(writer, 0, writer)], [(reader, 0, 0)]))

    # Two writers on one latch.
    out.append(var(
        section, "both-1-2-read3",
        "packets 1 (0x20) and 2 (0x40) write alu0, packet 3 reads it: 0x40 if "
        "the immediate predecessor is the source",
        [(1, 0, U1), (2, 0, U2)], [(3, 0, 0)]))
    out.append(var(
        section, "both-2-3-read0",
        "packets 2 (0x40) and 3 (0x60) write alu0, packet 0 reads it: 0x60 if "
        "the predecessor packet 3 is the source",
        [(2, 0, U2), (3, 0, U3)], [(0, 0, 0)]))
    out.append(var(
        section, "both-0-1-read3",
        "packets 0 (0x10) and 1 (0x20) write alu0, packet 3 reads it: 0 if the "
        "reader only sees packet 2, its predecessor",
        [(0, 0, U0), (1, 0, U1)], [(3, 0, 0)]))

    # Baselines.
    out.append(var(
        section, "write-only-p3",
        "packet 3 slot 0 writes 0x60 and nothing reads it, stored from r2: 0x60 "
        "proves the write lands",
        [(3, 0, U3)], store=STORE23))
    out.append(var(
        section, "read-nowrite",
        "packet 0 slot 0 reads alu0 with no writer anywhere: 0",
        [], [(0, 0, 0)]))
    out.append(var(
        section, "unmarked",
        "all four packets write their own byte, no read: 0",
        [(0, 0, U0), (1, 0, U1), (2, 0, U2), (3, 0, U3)]))
    out.append(var(
        section, "none",
        "no write, the store alone: the word baseline",
        []))
    out.append(var(
        section, "row-direct",
        "a row write in packet 0, stored through r2: 0x10, the control that the "
        "row path itself works",
        [(0, 0, U0)], store=STORE23))

    return out


def controls():
    """The same cross-shape controls in every manifest."""
    def ctl(name, note, template, abuf, slots, dws):
        return {"name": name, "note": note, "slots": slots, "dws": dws,
                "abuf": abuf, "template": template}

    return [
        ctl("ctl-rot2",
            "size 2, one packet per batch, batch 0 writes 0.25 (0x40) and batch "
            "1 slot 0 reads alu0: 0x40, the known-good two-packet rotation",
            TLATCH, 2, {"A0": wr(U1), "B0": rd(0)}, {2: STORE01}),
        ctl("ctl-rot3",
            "size 3, one packet per batch, batch 1 writes 0.5 (0x7F) and batch "
            "2 slot 0 reads alu0: 0x7F, the known-good three-packet rotation",
            TLATCH, 3, {"B0": wr(U2), "C0": rd(0)}, {3: STORE01}),
        ctl("ctl-carry-s1",
            "size 1, batch 0 writes 0x10 to alu0 and batch 1 slot 0 reads it: "
            "0x10, the group-boundary carry that does work",
            TLATCH, 1, {"A0": wr(U0), "B0": rd(0)}, {2: STORE01}),
        ctl("ctl-ref22-wrap0",
            "2 + 2 at size 2, packet 3 slot 0 writes 0x60, packet 0 slot 0 "
            "reads alu0: 0x00, the restricted wrap this study is about",
            TPACK4, 2, {"B0": wr(U3), "A0": rd(0)}, {2: STORE01}),
        ctl("ctl-ref22-wrap3",
            "2 + 2 at size 2, packet 3 slot 3 writes 0x60, packet 0 slot 3 "
            "reads alu3: 0x60, the one wrap that survives that packing",
            TPACK4, 2, {"B7": wr(U3), "A3": rd(3)}, {2: STORE01}),
        ctl("ctl-ref31-wrap0",
            "3 + 1 at size 2, packet 3 slot 0 writes 0x60, packet 0 slot 0 "
            "reads alu0: 0x60, the packing that has the complete map",
            TPACK43, 2, {"B0": wr(U3), "A0": rd(0)}, {2: STORE01}),
        ctl("ctl-ref31-fwd01",
            "3 + 1 at size 2, packet 0 slot 0 writes 0x10, packet 1 slot 0 "
            "reads alu0: 0x10, the adjacent hop in the same shape",
            TPACK43, 2, {"A0": wr(U0), "A4": rd(0)}, {2: STORE01}),
    ]


def template_placeholders(template):
    text = pathlib.Path(template).read_text()
    tokens = re.findall(r"@([A-Za-z][A-Za-z0-9]*)@", text)
    slots = sorted({t for t in tokens
                    if t != "ABUF" and not re.fullmatch(r"D\d+", t)})
    batches = max(int(t[1:]) for t in tokens if re.fullmatch(r"D\d+", t)) + 1
    return slots, batches


def build_manifest(only=None, section="dist211"):
    entries = []
    seen = set()
    for entry in matrix(section) + controls():
        name = entry["name"]
        if name in seen:
            raise SystemExit(f"duplicate variant {name}")
        seen.add(name)
        if only and name not in only:
            continue
        placeholders, batches = template_placeholders(entry["template"])
        known = set(placeholders) | {f"D{i}" for i in range(batches)}
        unknown = set(entry["slots"]) - known
        if unknown:
            raise SystemExit(f"{name}: unknown slots {sorted(unknown)}")
        unknown = set(f"D{b}" for b in entry["dws"]) - known
        if unknown:
            raise SystemExit(f"{name}: unknown DW batches {sorted(unknown)}")
        replace = {"@ABUF@": str(entry["abuf"])}
        for placeholder in placeholders:
            replace[f"@{placeholder}@"] = entry["slots"].get(placeholder, NOP)
        for batch in range(batches):
            replace[f"@D{batch}@"] = entry["dws"].get(batch, NOP)
        entries.append({
            "name": name,
            "fs": entry["template"],
            "replace": {"fs": replace},
            "environment": {"GRATE_DUMP_RAW": "1"},
            "expected_note": entry["note"],
        })
    if not entries:
        raise SystemExit("no variants selected")
    return {
        "name": f"alu-m4-{section}",
        "repeat": 3,
        "timeout": 20,
        "program": {"vs": VS, "fs": TEMPLATE[section], "lnk": LNK},
        "controls": CONTROLS,
        "variants": entries,
    }


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
    parser.add_argument("--only", help="comma-separated variant names to keep")
    parser.add_argument("--set", dest="section", choices=sorted(LAYOUT),
                        default="dist211", help="variant set to build")
    args = parser.parse_args()

    only = set(args.only.split(",")) if args.only else None
    manifest = build_manifest(only, args.section)
    out = args.out or OUT[args.section]
    if args.results:
        unpinned = pin(manifest, args.results)
        for name, words, statuses in unpinned:
            print(f"cannot pin {name}: results {words} statuses {statuses}")
        if unpinned:
            raise SystemExit("manifest not written")
    pathlib.Path(out).write_text(json.dumps(manifest, indent="\t") + "\n")
    print(f"wrote {out} ({len(manifest['variants'])} variants)")
    for entry in manifest["variants"]:
        print("{:24} {}".format(entry["name"], entry.get("expected", "-")))


if __name__ == "__main__":
    main()
