#!/usr/bin/env python3
"""Generate the six-packet group manifests.

Notes 38, 39 and 43 measured the four-packet cycle. The 2 + 2 packing at
`alu_buffer_size` 2 keeps the forward edges but restricts packet 3's wrap to
packet 0 to latch 3; 3 + 1, 1 + 3 and the size-3 splits 2 + 1 + 1, 1 + 2 + 1 and
1 + 1 + 2 have the complete map, and at size 4 with one packet per batch the
complete map holds for all four latches. That leaves three properties that
2 + 2 alone has at M = 4: two multi-packet batches, no single-packet batch, and
both wrap endpoints in multi-packet batches. Note 44 built all eight legal
five-packet splits, and every one has the complete cycle - including `2 + 3`,
which has two multi-packet batches and no single-packet batch, and `2 + 1 + 2`,
which has both wrap endpoints in multi-packet batches. None of the three
properties is sufficient at M = 5.

The question this generator is built for is narrower: is the shortfall a
property of four packets, or of batches that each hold two packets? A
six-packet group separates the two readings, because six can be packed as
`2 + 2 + 2` (uniform two per batch at a different packet count) as well as
`3 + 3` and `1 + 2 + 3` at the same group size:

    set  size  group A batches        multi-packet batches  two-packet batches
    222   3    packets 0,1 | 2,3 | 4,5        3                    3
    33    2    packets 0,1,2 | 3,4,5          2                    0
    123   3    packet 0 | 1,2 | 3,4,5         2                    1
    321   3    packets 0,1,2 | 3,4 | 5        2                    1

`222` is the primary discriminator. `33` uses two three-packet batches, so a
restricted result there would point at two-batch groups or three-packet
batches rather than at uniform two-packet occupancy. `123` has two multi-packet
batches with one of them holding two packets and the other three, so a
restricted result there would be about batch contents or endpoint position
rather than about uniform occupancy; `321` is its batch-order reverse and is
included as the reversal control. All four are encodable: a batch holds at most
three ALU packets (`src/libgrate/fragment_asm.y`, "Over 3 ALU instructions per
exec batch") and `alu_buffer_size` accepts 0..4.

Every set carries the same systematic matrix, built by the loops in `matrix()`
rather than typed by hand:

    fwd{src}{dst}-alN    packet src slot N writes, packet dst slot 0 reads aluN
                         (5 edges x 4 latches = 20)
    fwd01-al0-s2,        two of the forward cells re-read from another slot
    fwd45-al3-s2
    wrap-p5-alN-sS       packet 5 slot N writes, packet 0 slot S reads aluN
                         (4 latches x 4 reading slots = 16)
    own-p0 .. own-p5     a packet reads alu0 from another of its own slots
    succ-p{J}-p{K}       the immediate successor of the reader writes
    twoahead-p{J}-p{K}   the packet two ahead of the reader writes
    threeahead-p{J}-p{K} the packet three ahead writes
    twoback-p{J}-p{K}    the packet two behind the reader writes
                         (together, all 24 non-predecessor source/reader pairs)
    both-...             two or three packets writing one latch, one reader
                         (9 conflict cases around the six-packet ring)
    write-only-p5        the write with no reader, stored from the row
    read-nowrite         a read with no writer
    unmarked, none       all writes and no read, and nothing at all
    row-direct           a row write stored through r2, one per payload
                         (0x10, 0x20, 0x40, 0x60, 0x70, 0x08), which validates
                         the six marker values in the same run that uses them

The write values are per packet - u0 = 0.0625 (0x10), u1 = 0.125 (0x20), u2 =
0.25 (0x40), u3 = 0.375 (0x60), u4 = 0.4375 (0x70), u5 = 0.03125 (0x08) as fx10
- so a stored byte names the packet that served the read. The latch index is the
writer's slot, and the wrap varies the reader's slot independently of it. The
writer-slot/latch coupling is the hardware definition used throughout these
notes; other writer-slot/latch combinations were not tested.

Each manifest also carries the same ten cross-shape controls, built on
templates that already have recorded runs, so a run that returns unexpected
cells can be told apart from a broken harness: the size-2 two-packet rotation
(0x40), the size-3 rotation (0x7F), the size-1 group-boundary carry (0x10), the
2 + 2 wrap through latch 0 (0x00) and latch 3 (0x60), the 3 + 1 wrap (0x60) and
forward hop (0x10), the 2 + 1 + 1 wrap (0x60), and the five-packet 2 + 3 wrap
(0x70) and 3 -> 4 latch-3 hop (0x60). The controls' expected words are pinned
from the same hardware runs as the matrix, not copied from the earlier notes.

Usage:

    tests/grate/asm/make_alu_m6dist.py --set 222
    tests/grate/asm/make_alu_m6dist.py --set 222 --only wrap-p5-al0-s0,none
    tests/grate/asm/make_alu_m6dist.py --set 222 --results DIR
"""

import argparse
import csv
import json
import pathlib
import re

T211 = "tests/grate/asm/alu_dist211.in"
TPACK4 = "tests/grate/asm/alu_pack4.in"
TPACK43 = "tests/grate/asm/alu_pack43.in"
T5 = "tests/grate/asm/alu_m5_23.in"
TLATCH = "tests/grate/asm/alu_latch.in"

VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"
STORE23 = "store rt1, r2, r3"

U0, U1, U2, U3, U4, U5 = 0, 1, 2, 3, 4, 5
BYTE = {U0: "0x10", U1: "0x20", U2: "0x40", U3: "0x60", U4: "0x70",
        U5: "0x08"}
# D is skipped: a slot placeholder must not read as a DW placeholder @DN@.
LETTERS = "ABCEFG"

# (alu_buffer_size, batch sizes over the six packets of group A)
SPLIT = {
    "222": (3, ((0, 1), (2, 3), (4, 5))),
    "33": (2, ((0, 1, 2), (3, 4, 5))),
    "123": (3, ((0,), (1, 2), (3, 4, 5))),
    "321": (3, ((0, 1, 2), (3, 4), (5,))),
}


def split_text(section):
    abuf, groups = SPLIT[section]
    return " + ".join(str(len(g)) for g in groups)


def build_layout(section):
    """Map each group-A packet to its four slot placeholders."""
    abuf, groups = SPLIT[section]
    layout = {}
    for batch, packets in enumerate(groups):
        for index, packet in enumerate(packets):
            base = 4 * index
            layout[packet] = [f"{LETTERS[batch]}{base + comp}"
                              for comp in range(4)]
    return layout, abuf, abuf


TEMPLATE = {s: f"tests/grate/asm/alu_m6_{s}.in" for s in SPLIT}
OUT = {s: f"tests/grate/asm_characterize.alu_m6_{s}.json" for s in SPLIT}
PREFIX = {s: s + "-" for s in SPLIT}


def wr(const, fmt=".l*"):
    return f"MAD  r2{fmt},  u{const},  #1,  #0"


def rd(latch):
    return f"MAD  r0.l*,  alu{latch}.l,  #1,  #0"


def var(section, name, note, writes=(), reads=(), store=STORE01):
    layout, abuf, store_batch = build_layout(section)
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
    dws = {store_batch: store} if store else {}
    return {
        "name": PREFIX[section] + name,
        "note": note,
        "slots": slots,
        "dws": dws,
        "abuf": abuf,
        "template": TEMPLATE[section],
    }


def matrix(section):
    out = []

    # The five adjacent forward edges, every latch.
    for latch in range(4):
        for src in range(5):
            dst = src + 1
            out.append(var(
                section, f"fwd{src}{dst}-al{latch}",
                f"packet {src} slot {latch} writes {BYTE[src]}, packet {dst} "
                f"slot 0 reads alu{latch}: {BYTE[src]} if the one-hop forward "
                f"edge carries latch {latch}",
                [(src, latch, src)], [(dst, 0, latch)]))

    # Two forward cells re-read from another slot.
    out.append(var(
        section, "fwd01-al0-s2",
        "packet 0 slot 0 writes 0x10, packet 1 slot 2 reads alu0: 0x10 if the "
        "reading slot is free",
        [(0, 0, U0)], [(1, 2, 0)]))
    out.append(var(
        section, "fwd45-al3-s2",
        "packet 4 slot 3 writes 0x70, packet 5 slot 2 reads alu3: 0x70 if the "
        "last forward edge does not care about the reading slot",
        [(4, 3, U4)], [(5, 2, 3)]))

    # The wrap from the group's last packet to packet 0, writer latch and
    # reading slot varied independently.
    for latch in range(4):
        for rslot in range(4):
            out.append(var(
                section, f"wrap-p5-al{latch}-s{rslot}",
                f"packet 5 slot {latch} writes 0x08, packet 0 slot {rslot} "
                f"reads alu{latch}: 0x08 if packet 5's latch-{latch} result "
                f"reaches packet 0 from reading slot {rslot}",
                [(5, latch, U5)], [(0, rslot, latch)]))

    # Own-packet negatives.
    for packet in range(6):
        out.append(var(
            section, f"own-p{packet}",
            f"packet {packet} slot 0 writes {BYTE[packet]}, its own slot 2 "
            f"reads alu0: 0 if a packet never sees its own result",
            [(packet, 0, packet)], [(packet, 2, 0)]))

    # Every non-predecessor source/reader pair: immediate successor, two ahead,
    # three ahead, two behind. Offsets 1..4 leave out the reader itself and its
    # cyclic predecessor (offset 5), so the 24 cells are the complete set.
    for reader in range(6):
        for offset, label in ((1, "succ"), (2, "twoahead"),
                              (3, "threeahead"), (4, "twoback")):
            writer = (reader + offset) % 6
            out.append(var(
                section, f"{label}-p{writer}-p{reader}",
                f"packet {writer} slot 0 writes {BYTE[writer]}, packet "
                f"{reader} slot 0 reads alu0: 0 if only the immediate "
                f"predecessor serves a read",
                [(writer, 0, writer)], [(reader, 0, 0)]))

    # Two or three writers on one latch with one reader, around the ring.
    out.append(var(
        section, "both-1-2-read3",
        "packets 1 (0x20) and 2 (0x40) write alu0, packet 3 reads it: 0x40 if "
        "the immediate predecessor is the source",
        [(1, 0, U1), (2, 0, U2)], [(3, 0, 0)]))
    out.append(var(
        section, "both-2-3-read4",
        "packets 2 (0x40) and 3 (0x60) write alu0 in the same or adjacent "
        "batches, packet 4 reads it: 0x60 if the immediate predecessor wins",
        [(2, 0, U2), (3, 0, U3)], [(4, 0, 0)]))
    out.append(var(
        section, "both-3-4-read5",
        "packets 3 (0x60) and 4 (0x70) write alu0, packet 5 reads it: 0x70 if "
        "the immediate predecessor wins",
        [(3, 0, U3), (4, 0, U4)], [(5, 0, 0)]))
    out.append(var(
        section, "both-4-5-read0",
        "packets 4 (0x70) and 5 (0x08) write alu0, packet 0 reads it: 0x08 if "
        "the wrap's writer wins over the packet before it",
        [(4, 0, U4), (5, 0, U5)], [(0, 0, 0)]))
    out.append(var(
        section, "both-1-5-read0",
        "packets 1 (0x20) and 5 (0x08) write alu0, packet 0 reads it: 0x08 if "
        "the wrap's writer wins even with an earlier writer behind it",
        [(1, 0, U1), (5, 0, U5)], [(0, 0, 0)]))
    out.append(var(
        section, "both-2-5-read0",
        "packets 2 (0x40) and 5 (0x08) write alu0, packet 0 reads it: 0x08 if "
        "the predecessor supplies the read over a writer two packets back",
        [(2, 0, U2), (5, 0, U5)], [(0, 0, 0)]))
    out.append(var(
        section, "both-0-5-read2",
        "packets 0 (0x10) and 5 (0x08) write alu0, packet 2 reads it: 0 if a "
        "reader only sees packet 1, its predecessor",
        [(0, 0, U0), (5, 0, U5)], [(2, 0, 0)]))
    out.append(var(
        section, "three-1-3-5-read0",
        "packets 1 (0x20), 3 (0x60) and 5 (0x08) write alu0, packet 0 reads "
        "it: 0x08 if the pair before the reader decides, not the earlier pair",
        [(1, 0, U1), (3, 0, U3), (5, 0, U5)], [(0, 0, 0)]))
    out.append(var(
        section, "three-0-2-4-read5",
        "packets 0 (0x10), 2 (0x40) and 4 (0x70) write alu0, packet 5 reads "
        "it: 0x70 if the immediate predecessor wins",
        [(0, 0, U0), (2, 0, U2), (4, 0, U4)], [(5, 0, 0)]))

    # Baselines, including one row-direct readback per payload value.
    out.append(var(
        section, "write-only-p5",
        "packet 5 slot 0 writes 0x08 and nothing reads it, stored from r2: "
        "0x08 proves the write lands",
        [(5, 0, U5)], store=STORE23))
    out.append(var(
        section, "read-nowrite",
        "packet 0 slot 0 reads alu0 with no writer anywhere: 0",
        [], [(0, 0, 0)]))
    out.append(var(
        section, "unmarked",
        "all six packets write their own byte, no read: 0",
        [(0, 0, U0), (1, 0, U1), (2, 0, U2), (3, 0, U3), (4, 0, U4),
         (5, 0, U5)]))
    out.append(var(
        section, "none",
        "no write, the store alone: the word baseline",
        []))
    for const in (U0, U1, U2, U3, U4, U5):
        out.append(var(
            section, f"row-direct-p{const}",
            f"a row write of packet 0's payload {BYTE[const]} in packet 0, "
            f"stored through r2: {BYTE[const]}, the control that the row path "
            f"and this payload value work",
            [(0, 0, const)], store=STORE23))

    return out


def controls():
    """The same cross-shape controls in every manifest."""
    def ctl(name, note, template, abuf, slots, dws):
        return {"name": name, "note": note, "slots": slots, "dws": dws,
                "abuf": abuf, "template": template}

    return [
        ctl("ctl-rot2",
            "size 2, one packet per batch, batch 0 writes uniform 1 of "
            "alu_latch.in (0.25) and batch 1 slot 0 reads alu0: 0x40, the "
            "known-good two-packet rotation",
            TLATCH, 2, {"A0": wr(U1), "B0": rd(0)}, {2: STORE01}),
        ctl("ctl-rot3",
            "size 3, one packet per batch, batch 1 writes uniform 2 of "
            "alu_latch.in (0.5) and batch 2 slot 0 reads alu0: 0x7F, the "
            "known-good three-packet rotation",
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
        ctl("ctl-ref211-wrap0",
            "2 + 1 + 1 at size 3, packet 3 slot 0 writes 0x60, packet 0 slot 0 "
            "reads alu0: 0x60, the three-batch packing with the complete map",
            T211, 3, {"C0": wr(U3), "A0": rd(0)}, {3: STORE01}),
        ctl("ctl-ref5-wrap0",
            "five-packet 2 + 3 at size 2, packet 4 slot 0 writes 0x70, packet "
            "0 slot 0 reads alu0: 0x70, the known-good five-packet wrap",
            T5, 2, {"B8": wr(U4), "A0": rd(0)}, {2: STORE01}),
        ctl("ctl-ref5-fwd34",
            "five-packet 2 + 3 at size 2, packet 3 slot 3 writes 0x60, packet "
            "4 slot 0 reads alu3: 0x60, the known-good five-packet forward hop",
            T5, 2, {"B7": wr(U3), "B8": rd(3)}, {2: STORE01}),
    ]


def template_placeholders(template):
    text = pathlib.Path(template).read_text()
    tokens = re.findall(r"@([A-Za-z][A-Za-z0-9]*)@", text)
    slots = sorted({t for t in tokens
                    if t != "ABUF" and not re.fullmatch(r"D\d+", t)})
    batches = max(int(t[1:]) for t in tokens if re.fullmatch(r"D\d+", t)) + 1
    return slots, batches


def build_manifest(only=None, section="222"):
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
        "name": f"alu-m6-{section}",
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
    parser.add_argument("--set", dest="section", choices=sorted(SPLIT),
                        default="222", help="variant set to build")
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
