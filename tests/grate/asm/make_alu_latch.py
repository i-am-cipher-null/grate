#!/usr/bin/env python3
"""Generate the ALU result-register (latch) reconciliation manifest.

The template has six batches with four scalar slots and one DW operation each,
so a store can sit in any batch and only the slots change between variants.

Three readings of `alu0..alu3` are on the table, and they predict different
words for the same programs:

  retired   a latch read returns the value its slot wrote in the *previous*
            packet, from any slot. Same-packet reads are zero, and every
            next-packet read returns the producer's value whatever slot asks.
  write-bus a read is served by the current packet's slot of the same number,
            low-half writes only, whatever the slot order. Same-packet reads
            work; the only value that survives a packet boundary is `alu0`
            read from slot 0.
  nothing   every latch read is zero - what section 9 recorded.

The distances are separated by `alu_buffer_size`. At size 1 every batch is its
own group, so two batches are separated by a group flush; at size 2 two batches
share a group and a producer in batch 0 with a reader in batch 1 differs from it
by a packet boundary only.

Slots: index 0..3 = batch 0, 4..7 = batch 1, 8..11 = batch 2, 12..15 = batch 3,
16..19 = batch 4, 20..23 = batch 5. Each variant gives the slots it wants and
leaves the rest NOP, plus the six DW slots. Six batches, because the size-3
variants need whole groups: four batches at size 3 timed out twice at 20 s.

The `dist-*` variants write the result latches directly: the assembler refuses
`aluN` as a destination, so the word is the assembled form of
`MAD r2.l*, u0, #1, #0` (part1 0x00012800, part0 0x3F41F200) with the
destination field (part1 bits 15..21) rewritten to 24..27. `dist-raw-orig`
keeps the original destination as the control that the raw form behaves like
the symbolic instruction.

`--results DIR` pins each variant to the modal word of its repeats.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/alu_latch.in"
OUT = "tests/grate/asm_characterize.alu_latch.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"
STORE23 = "store rt1, r2, r3"

# u0 = 0.0625 -> 0x10, u1 = 0.25 -> 0x40, u2 = 0.5 -> 0x7F, u3 = 0.125 -> 0x20
U0, U1, U2, U3 = 0, 1, 2, 3

# assembled `MAD r2.l*, u0, #1, #0` with the destination field rewritten
RAW_ORIG = "0x000128003F41F200"
RAW_DEST = {
    24: "0x000C28003F41F200",
    25: "0x000CA8003F41F200",
    26: "0x000D28003F41F200",
    27: "0x000DA8003F41F200",
}


def wr(dst, const):
    return f"MAD  {dst},  u{const},  #1,  #0"


def rd(dst, latch):
    return f"MAD  {dst},  alu{latch}.l,  #1,  #0"


def row(dst, src):
    return f"MAD  {dst},  {src},  #1,  #0"


def variant(name, abuf, slots, dws, note):
    return {
        "name": name,
        "abuf": abuf,
        "slots": slots,
        "dws": dws,
        "note": note,
        "fs": TEMPLATE,
    }


# Same packet: two or more slots of one packet, the store in that same batch.
# At size 1 a batch is its own group, so the store in batch 0 fires.
SAME = [
    variant("same-chain", 1,
            {0: wr("r2.l*", U0), 1: rd("r0.l*", 0), 2: rd("r1.l*", 1),
             3: rd("r1.*h", 2)},
            {0: STORE01},
            "a three-link copy chain through low-half writes: 0x10 in r0.lo, "
            "r1.lo and r1.hi if every link reads the slot before it"),
    variant("same-chain-hi", 1,
            {0: wr("r2.l*", U0), 1: rd("r0.l*", 0), 2: rd("r0.*h", 1),
             3: rd("r1.l*", 2)},
            {0: STORE01},
            "the same chain with its middle link writing a high half: r1.lo "
            "stays 0 if a high-half write leaves its latch empty"),
    variant("same-backward", 1,
            {0: rd("r0.l*", 1), 1: wr("r2.l*", U1)},
            {0: STORE01},
            "the reader sits before the writer and asks for its latch: 0x40 "
            "means the read is served by the packet's write, not its order"),
    variant("same-nop", 1,
            {0: rd("r0.l*", 2)},
            {0: STORE01},
            "the latch of a slot that is NOP in this packet: 0 baseline"),
    variant("same-self", 1,
            {2: rd("r0.l*", 2)},
            {0: STORE01},
            "slot 2 reads alu2, its own latch, while writing it: what a "
            "self-satisfied read returns"),
    variant("same-two-values", 1,
            {0: wr("r2.l*", U0), 1: wr("r2.*h", U3),
             2: rd("r0.l*", 0), 3: rd("r0.*h", 1)},
            {0: STORE01},
            "two different producers read by two readers in one packet: 0x10 "
            "and 0x20 if the latches are per slot"),
    variant("same-row", 1,
            {0: wr("r2.l*", U0), 1: row("r0.l*", "r2.l")},
            {0: STORE01},
            "row-register read in the same packet as the write: 0, the row is "
            "not forwarded inside a packet (section 9)"),
    variant("same-row-baseline", 1,
            {1: row("r0.l*", "r2.l")},
            {0: STORE01},
            "the same row read with nothing written: the 0 baseline"),
    variant("same-bare", 1,
            {0: wr("r2.l*", U0), 1: "MAD  r0.l*,  alu0,  #1,  #0"},
            {0: STORE01},
            "the same-packet read written bare (the fp20 source form) instead "
            "of alu0.l: 0 if a bare read takes a half a low-half write never "
            "fills - which is how section 9 measured its zero"),
    variant("same-bare-lh", 1,
            {0: wr("r2.lh", U0), 1: "MAD  r0.l*,  alu0,  #1,  #0"},
            {0: STORE01},
            "the same bare read with an fp20 (.lh) producer, which writes "
            "both halves"),
    variant("pkt-bare", 2,
            {0: wr("r2.l*", U0), 4: "MAD  r0.l*,  alu0,  #1,  #0"},
            {2: STORE01},
            "the bare read one packet later in the same group"),
]

# Next packet inside one group: size 2, producers in batch 0, readers in batch
# 1, and the store in batch 2, which is the next group's first batch and so the
# only batch there whose DW produces an observable store.
PKT = [
    variant("pkt-alu0-s0", 2,
            {0: wr("r2.l*", U0), 4: rd("r0.l*", 0)}, {2: STORE01},
            "alu0 asked from slot 0: 0x10 if the slot-0 latch carries over the "
            "packet boundary"),
    variant("pkt-alu0-s1", 2,
            {0: wr("r2.l*", U0), 5: rd("r0.l*", 0)}, {2: STORE01},
            "alu0 asked from slot 1: 0x10 would mean the carry does not need "
            "slot 0; 0 is the write-bus reading"),
    variant("pkt-alu1-s0", 2,
            {1: wr("r2.l*", U1), 4: rd("r0.l*", 1)}, {2: STORE01},
            "alu1 asked from slot 0 one packet later: 0x40 if a latch carries"),
    variant("pkt-alu2-s0", 2,
            {2: wr("r2.l*", U2), 4: rd("r0.l*", 2)}, {2: STORE01},
            "alu2 asked from slot 0 one packet later"),
    variant("pkt-alu3-s0", 2,
            {3: wr("r2.l*", U3), 4: rd("r0.l*", 3)}, {2: STORE01},
            "alu3 asked from slot 0 one packet later"),
    variant("pkt-alu0-hi", 2,
            {0: wr("r2.*h", U0), 4: rd("r0.l*", 0)}, {2: STORE01},
            "the producer wrote only the high half: 0 if a latch needs a "
            "low-half write"),
    variant("pkt-alu0-fp20", 2,
            {0: wr("r2.lh", U0), 4: rd("r0.l*", 0)}, {2: STORE01},
            "the producer wrote fp20 (both halves, .lh): 0 if a latch takes "
            "only fx10 low writes"),
    variant("pkt-row", 2,
            {0: wr("r2.l*", U0), 4: row("r0.l*", "r2.l")}, {2: STORE01},
            "row read one packet later, same group: 0x10, the register-file "
            "control for this distance"),
    variant("pkt-direct", 2,
            {0: wr("r0.l*", U1)}, {2: STORE01},
            "no latch at all: batch 0 writes the readback row and batch 2 "
            "stores it, which proves the path from batch 0 to the store"),
    variant("pkt-baseline", 2,
            {}, {2: STORE01},
            "nothing written: the store's own baseline at this distance"),
    variant("pkt-two-latches", 2,
            {1: wr("r2.l*", U1), 2: wr("r2.l*", U2),
             4: rd("r0.l*", 1), 5: rd("r0.*h", 2)}, {2: STORE01},
            "two producers in packet 0 read one packet later from slots that "
            "are not their own: 0x40 in r0.lo and 0x7F in r0.hi if the latch "
            "number, not the reading slot, decides"),
    variant("pkt3-alu0-s0", 3,
            {0: wr("r2.l*", U0), 8: rd("r0.l*", 0)}, {3: STORE01},
            "size 3: the reader is two packets after the producer, and batch 3 "
            "is the next group's first batch, so its store fires"),
    variant("pkt3-alu1-s0", 3,
            {1: wr("r2.l*", U1), 8: rd("r0.l*", 1)}, {3: STORE01},
            "the same two-packet distance for alu1"),
]

# Across a batch boundary: size 1, so the ALU buffer flushes between batches.
BATCH = [
    variant("n1-alu0-s0", 1,
            {0: wr("r2.l*", U0), 4: rd("r0.l*", 0)}, {1: STORE01},
            "alu0 one batch later at size 1: a latch would have to survive the "
            "group flush"),
    variant("n1-alu1-s0", 1,
            {1: wr("r2.l*", U1), 4: rd("r0.l*", 1)}, {1: STORE01},
            "alu1 one batch later at size 1"),
    variant("n1-alu0-s1", 1,
            {0: wr("r2.l*", U0), 5: rd("r0.l*", 0)}, {1: STORE01},
            "alu0 one batch later, asked from slot 1"),
    variant("n1-row", 1,
            {0: wr("r2.l*", U0), 4: row("r0.l*", "r2.l")}, {1: STORE01},
            "row read one batch later: 0x10, the register-file control"),
    variant("n1-direct", 1,
            {0: wr("r0.l*", U1)}, {1: STORE01},
            "no latch: batch 0 writes the readback row and batch 1 stores it"),
]

# Direct writes to the result latches, destination field patched.
DEST = [
    variant("dist-alu0-same", 1,
            {0: RAW_DEST[24], 1: rd("r0.l*", 0)}, {0: STORE01},
            "a patched write to alu0, read from slot 1 in the same packet: "
            "0x10 if the latch is writable and readable"),
    variant("dist-alu1-same", 1,
            {0: RAW_DEST[25], 1: rd("r0.l*", 1)}, {0: STORE01},
            "the same word with the destination field at 25 (alu1)"),
    variant("dist-alu2-same", 1,
            {0: RAW_DEST[26], 1: rd("r0.l*", 2)}, {0: STORE01},
            "the same word with the destination field at 26 (alu2)"),
    variant("dist-alu3-same", 1,
            {0: RAW_DEST[27], 1: rd("r0.l*", 3)}, {0: STORE01},
            "the same word with the destination field at 27 (alu3)"),
    variant("dist-alu0-next", 1,
            {0: RAW_DEST[24], 4: rd("r0.l*", 0)}, {1: STORE01},
            "the alu0 write one batch later at size 1"),
    variant("dist-row", 1,
            {0: RAW_DEST[24]}, {0: STORE23},
            "the patched word with nothing reading it: r2 must stay 0, so the "
            "write went to the latch and not to the row"),
    variant("dist-raw-orig", 1,
            {0: RAW_ORIG}, {0: STORE23},
            "the unpatched raw word writing r2.l*: 0x10, the control that the "
            "hex form behaves like the symbolic instruction"),
    variant("dist-raw-read", 1,
            {0: RAW_ORIG, 1: row("r0.l*", "r2.l")}, {0: STORE01},
            "the same raw write read back as a row register in the same "
            "packet: 0, matching same-row"),
    variant("dist-slot1-dest24-alu1", 1,
            {1: RAW_DEST[24], 0: rd("r0.l*", 1)}, {0: STORE01},
            "a patched write issued from slot 1 with the destination field at "
            "24, read as alu1: 0x10 means the field is ignored and the slot "
            "number names the latch"),
    variant("dist-slot1-dest25-alu1", 1,
            {1: RAW_DEST[25], 0: rd("r0.l*", 1)}, {0: STORE01},
            "the same write with the field at 25, read as alu1"),
    variant("dist-slot1-dest24-alu0", 1,
            {1: RAW_DEST[24], 0: rd("r0.l*", 0)}, {0: STORE01},
            "the field at 24 filled from slot 1 but read as alu0: 0 if only "
            "the issuing slot feeds the latch"),
]

VARIANTS = SAME + PKT + BATCH + DEST

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
        "name": "alu-latch",
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
