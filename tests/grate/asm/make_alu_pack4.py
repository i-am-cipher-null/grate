#!/usr/bin/env python3
"""Generate the four-packet group manifest.

Notes 28-37 give the latch rule for groups of M = 1, 2 and 3 ALU packets: a read
in slot S of packet k is served by packet (k-1) mod M, with format-specific latch
views and own-slot exclusion, and the rotation is group-scoped rather than
confined to one batch (notes 36 put one packet in each batch of a size-2 and a
size-3 group). The open question is whether the rule holds at M = 4.

This set builds a four-packet group the way the hardware's two knobs allow:
`alu_buffer_size` 2 puts two batches in a group, and two `ALU:` blocks per batch
gives the group four packets (`asm/alu_pack4.in`). The assembler derives the
per-batch packet count from the number of blocks, so the upload carries count 2
at packet address 0 for batch 0 and count 2 at address 2 for batch 1 - no
schedule word is patched. Every variant dumps its raw program, and
`dump_sched_words.py` records both `0x801` and `0x806` for every batch of every
draw.

**What the sets measured.** The forward half of the map holds for all four
latches (`0x10`, `0x20`, `0x40` for 0 -> 1 -> 2 -> 3), own-packet reads and
two-packets-apart reads are zero, and no group-boundary carry survives. The
wrap 0 <- 3 behaves differently: through latch 3 it returns the group's last
packet's value (`alu3-from-p3-slot3` `0x60`, every reading slot, needing packet
3's slot-3 write specifically), while through latches 0, 1 and 2 it returns 0.
The `wrap`, `probe` and `probe2` sets separate latch index from writing and
reading slot for that one result, and `size4` (`asm/alu_buf_blocks8.in`, size 4,
one packet per batch) re-asks the M=4 question in a different packing, where
the wrap *does* return latch 0's value (`m4-s4-wrap` `0x60`). So the predicted
map is confirmed for M=4 at one packet per batch and refined for the
four-packets-in-two-batches shape, where only the slot-3 wrap survives. Why the
packing decides that is not measured, and the notes keep it Open.

The cyclic map under test, with M = 4:

    packet 0 reads packet 3     packet 2 reads packet 1
    packet 1 reads packet 0     packet 3 reads packet 2

Each packet writes a distinct byte (`u0..u3` = 0x10, 0x20, 0x40, 0x60), so the
stored byte names the packet that served the read:

    src3-read0    packet 3 slot 0 writes, packet 0 slot 0 reads alu0   -> 0x60
    src0-read1    packet 0 slot 0 writes, packet 1 slot 0 reads alu0   -> 0x10
    src1-read2    packet 1 slot 0 writes, packet 2 slot 0 reads alu0   -> 0x20
    src2-read3    packet 2 slot 0 writes, packet 3 slot 0 reads alu0   -> 0x40

Those four are the same arrangement, one packet pair at a time, so a map that is
not the predicted one still shows up as a value that names the packet actually
served. The negatives separate the map from the alternatives:

    own-p0..p3             a packet reads alu0 from another of its own slots,
                           while its own slot 0 writes -> 0 if own results are
                           excluded, as at M = 2 and 3
    pair-p2-p0, p3-p1,     a read from the packet two before it (wrapping) -> 0
    p0-p2, p1-p3
    unmarked               all four packets write, no read -> the store baseline
    none                   no write, one store -> the word baseline
    row-direct             a row write in packet 0 stored by batch 2: shows the
                           observation path reaches the store
    rot2-ctl, rot3-ctl     the same two questions at sizes 2 and 3 with one
                           packet per batch, on `asm/alu_latch.in`: known-good
                           arrangements from notes 36 that must still return
                           0x20 and 0x40

The latch index and the reading slot are varied separately. Latch index is the
slot the writer occupies, so `alu1-from-p0`, `alu2-from-p1`, `alu3-from-p2` and
`alu3-from-p3-slot3` reach the same source packets through other latches, and
`alu0-slot1`, `alu0-slot2`, `alu0-slot3` re-ask the primary question from a
different reading slot.

Conflicts: `both-1-2-read3` has packets 1 and 2 write alu0 and packet 3 read it
-> 0x40 if the immediate predecessor is the source; `both-own-prev` has packet 0
write alu0, its own slot 3 read it and packet 3 write it too -> 0x60 if the
rotation beats own-slot exclusion, 0x10 if the own packet wins.

Formats: `lh-p0` and `lh-p3` write fp20 (`.lh`) and read bare, which returns
0x10 / 0x60 through the fp20 path if the rotation carries it (notes 28 measured
0x10 for an `.lh` write read bare in one packet). `lh-write-l-read` and
`bare-read` are the format mismatches that must stay 0.

Group-boundary carry is kept separate. `carry-s0`: group A's last packet
(packet 3) writes alu0, group B's first packet slot 0 reads alu0, stored by
group C's first batch -> 0 if the carry that survives a one-batch group (notes
28, 31) does not survive a four-packet group at size 2. `carry-s2` reads from
slot 2 instead, `carry-none` omits the write, and `carry-ctl-s1` is the size-1
arrangement that does carry (0x10).

**The three-dimensional wrap matrix (`--set m4matrix`).** The `wrap` set
varied the latch and the reading slot, but the writing slot always matched the
latch, so producer slot, latch index and reading slot were never separated. The
ISA does not allow an arbitrary write latch: the issuing slot selects the latch
and the destination field does not route (notes 28 and 37, the direct-write
probes). The matrix set therefore varies the three dimensions that can vary -
packet 3's writing slot P, the latch L that packet 0 reads, and packet 0's
reading slot R - over all 64 combinations `m4x-pP-alL-sR`. Where L = P the cell
is an ordinary wrap cell; where L != P packet 3 never filled `aluL`, so a value
would mean the wrap moves a result between latch indices. Every cell writes
0x60 in packet 3 slot P and reads `aluL` from packet 0 slot R, with one writer
and one reader, stored by batch 2 as in the rest of this template. The matrix
is followed by the exact known-positive and known-negative cells, the known
forward edge, the 3+1 and 2+1+1 wraps on their own templates, a direct-row
payload control and the word baseline.

Usage:

    tests/grate/asm/make_alu_pack4.py                     # the whole set
    tests/grate/asm/make_alu_pack4.py --set wrap          # the wrap isolation set
    tests/grate/asm/make_alu_pack4.py --set m4matrix      # the 64-cell matrix
    tests/grate/asm/make_alu_pack4.py --only src3-read0,src0-read1,none,row-direct
    tests/grate/asm/make_alu_pack4.py --results DIR       # pin from a run

`--only` builds a manifest with just the named variants plus the controls, for a
first run on a shape the engine has not executed before.

The `wrap` set is the follow-up the main run needed. The forward hops all hold
(0 -> 1 -> 2 -> 3), but the wrap read from packet 0 returned 0x00 for latch 0
(`src3-read0`, and `alu0-slot1` from a second reading slot) while the same wrap
through latch 3 returned 0x60 (`alu3-from-p3-slot3`), so latch index, writer
slot and reading slot were confounded. The wrap set varies them one at a time,
and it found the split: only the slot-3 write of the group's last packet is
visible to packet 0, from any reading slot, and `back-same-p0` (packet 1
writing to packet 0 inside one batch) is zero because the cyclic predecessor of
packet 0 is packet 3, which is what the map predicts:

    wrap-al1-s0, wrap-al2-s0   the last packet's slots 1 and 2, read from packet
                               0 slot 0 - 0x60 means the wrap exists for those
                               latches and only slot 0 is special
    wrap-al3-s0                the same for slot 3 from reading slot 0
    wrap-al0-s3, wrap-al0-s2   slot 0 of the last packet read from packet 0's
                               slots 3 and 2
    wrap-al1-s1                slot 1 read from slot 1: control that the
                               reading slot is not what makes a wrap fail
    back-same-p0, back-same-p2 packet 1 writing to packet 0, and packet 3
                               writing to packet 2, inside one batch: does the
                               ordering matter inside a batch at all
    back-cross-b1              packet 2 writing, packet 1 reading: a backward
                               hop across the batch boundary
    rot2-back-ctl              size 2, one packet per batch, packet 1 slot 0
                               writes and packet 0 slot 0 reads: the 0x40 that
                               notes 36 measured, so the M=4 wrap can be
                               compared with the M=2 one directly
    none                       the word baseline
"""

import argparse
import csv
import json
import pathlib

TEMPLATE4 = "tests/grate/asm/alu_pack4.in"
TEMPLATE_LATCH = "tests/grate/asm/alu_latch.in"
TEMPLATE8 = "tests/grate/asm/alu_buf_blocks8.in"
TEMPLATE_PACK43 = "tests/grate/asm/alu_pack43.in"
TEMPLATE_DIST211 = "tests/grate/asm/alu_dist211.in"
OUT = "tests/grate/asm_characterize.alu_pack4.json"
OUT_MATRIX = "tests/grate/asm_characterize.alu_pack4_m4matrix.json"
OUT_WRAP = "tests/grate/asm_characterize.alu_pack4_wrap.json"
OUT_PROBE = "tests/grate/asm_characterize.alu_pack4_probe.json"
OUT_PROBE2 = "tests/grate/asm_characterize.alu_pack4_probe2.json"
OUT_SIZE4 = "tests/grate/asm_characterize.alu_pack4_size4.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE01 = "store rt1, r0, r1"
STORE23 = "store rt1, r2, r3"

U0, U1, U2, U3 = 0, 1, 2, 3

PACK4_SLOTS = (
    [f"A{index}" for index in range(8)]      # batch 0, packets 0 and 1
    + [f"B{index}" for index in range(8)]    # batch 1, packets 2 and 3
    + [f"C{index}" for index in range(4)]    # batch 2, group B
    + [f"E{index}" for index in range(4)]    # batch 3
    + [f"F{index}" for index in range(4)]    # batch 4, group C
    + [f"G{index}" for index in range(4)]    # batch 5
)

LATCH_SLOTS = [f"{prefix}{index}"
               for prefix in ("A", "B", "C", "E", "F", "G")
               for index in range(4)]

BLOCKS8_SLOTS = [f"B{index}" for index in range(8)]

PACK43_SLOTS = (
    [f"A{index}" for index in range(12)]      # batch 0, packets 0-2
    + [f"B{index}" for index in range(4)]     # batch 1, packet 3
    + [f"{prefix}{index}" for prefix in ("C", "E", "F", "G")
       for index in range(4)]
)

DIST211_SLOTS = (
    [f"A{index}" for index in range(8)]       # batch 0, packets 0 and 1
    + [f"{prefix}{index}" for prefix in ("B", "C", "E", "F", "G")
       for index in range(4)]
)

TEMPLATES = {
    TEMPLATE4: (PACK4_SLOTS, 6),
    TEMPLATE_LATCH: (LATCH_SLOTS, 6),
    TEMPLATE8: (BLOCKS8_SLOTS, 8),
    TEMPLATE_PACK43: (PACK43_SLOTS, 6),
    TEMPLATE_DIST211: (DIST211_SLOTS, 6),
}


def wr(const, fmt=".l*"):
    return f"MAD  r2{fmt},  u{const},  #1,  #0"


def rd(latch, slot_dst="r0.l*", bare=False):
    source = f"alu{latch}" if bare else f"alu{latch}.l"
    return f"MAD  {slot_dst},  {source},  #1,  #0"


def variant(name, note, slots=None, dws=None, abuf=2,
            template=TEMPLATE4):
    return {"name": name, "note": note, "slots": slots or {},
            "dws": dws or {}, "abuf": abuf, "template": template}


VARIANTS = [
    # The four one-hop dependencies.
    variant("src3-read0",
            "packet 3 slot 0 writes 0x60, packet 0 slot 0 reads alu0: 0x60 if "
            "the rotation wraps 0 <- 3 in a four-packet group",
            {"B0": wr(U3), "A0": rd(0)}, {2: STORE01}),
    variant("src0-read1",
            "packet 0 slot 0 writes 0x10, packet 1 slot 0 reads alu0: 0x10",
            {"A0": wr(U0), "A4": rd(0)}, {2: STORE01}),
    variant("src1-read2",
            "packet 1 slot 0 writes 0x20, packet 2 slot 0 reads alu0: 0x20",
            {"A4": wr(U1), "B0": rd(0)}, {2: STORE01}),
    variant("src2-read3",
            "packet 2 slot 0 writes 0x40, packet 3 slot 0 reads alu0: 0x40",
            {"B0": wr(U2), "B4": rd(0)}, {2: STORE01}),

    # The same source packets reached through other latches.
    variant("alu1-from-p0",
            "packet 0 slot 1 writes 0x10, packet 1 slot 0 reads alu1: 0x10 - "
            "the second latch holds the same packet's result",
            {"A1": wr(U0), "A4": rd(1)}, {2: STORE01}),
    variant("alu2-from-p1",
            "packet 1 slot 2 writes 0x20, packet 2 slot 0 reads alu2: 0x20",
            {"A6": wr(U1), "B0": rd(2)}, {2: STORE01}),
    variant("alu3-from-p2",
            "packet 2 slot 3 writes 0x40, packet 3 slot 0 reads alu3: 0x40",
            {"B3": wr(U2), "B4": rd(3)}, {2: STORE01}),
    variant("alu3-from-p3-slot3",
            "packet 3 slot 3 writes 0x60, packet 0 slot 3 reads alu3: 0x60 - "
            "the wrap through the last latch, read from the last slot",
            {"B7": wr(U3), "A3": rd(3)}, {2: STORE01}),

    # Own-packet negatives.
    variant("own-p0",
            "packet 0 slot 0 writes 0x10 and its slot 3 reads alu0: 0 if a "
            "packet never sees its own result",
            {"A0": wr(U0), "A3": rd(0)}, {2: STORE01}),
    variant("own-p1",
            "packet 1 slot 0 writes 0x20 and its slot 3 reads alu0: 0",
            {"A4": wr(U1), "A7": rd(0)}, {2: STORE01}),
    variant("own-p2",
            "packet 2 slot 0 writes 0x40 and its slot 3 reads alu0: 0",
            {"B0": wr(U2), "B3": rd(0)}, {2: STORE01}),
    variant("own-p3",
            "packet 3 slot 0 writes 0x60 and its slot 3 reads alu0: 0",
            {"B4": wr(U3), "B7": rd(0)}, {2: STORE01}),

    # Two-packets-apart negatives.
    variant("pair-p2-p0",
            "packet 2 writes 0x40, packet 0 reads alu0: 0 - two apart is not a "
            "forward",
            {"B0": wr(U2), "A0": rd(0)}, {2: STORE01}),
    variant("pair-p3-p1",
            "packet 3 writes 0x60, packet 1 reads alu0: 0",
            {"B4": wr(U3), "A4": rd(0)}, {2: STORE01}),
    variant("pair-p0-p2",
            "packet 0 writes 0x10, packet 2 reads alu0: 0",
            {"A0": wr(U0), "B0": rd(0)}, {2: STORE01}),
    variant("pair-p1-p3",
            "packet 1 writes 0x20, packet 3 reads alu0: 0",
            {"A4": wr(U1), "B4": rd(0)}, {2: STORE01}),

    # Reading slot and latch index kept apart.
    variant("alu0-slot1",
            "the primary wrap read from packet 0's slot 1 instead of slot 0: "
            "0x60 if the reading slot does not matter",
            {"B0": wr(U3), "A1": rd(0)}, {2: STORE01}),
    variant("alu0-slot2",
            "packet 3 slot 2 reads the alu0 that packet 2 slot 0 wrote: 0x40",
            {"B0": wr(U2), "B6": rd(0)}, {2: STORE01}),
    variant("alu0-slot3",
            "packet 1 slot 3 reads the alu0 that packet 0 slot 0 wrote: 0x10",
            {"A0": wr(U0), "A7": rd(0)}, {2: STORE01}),

    # Format paths.
    variant("lh-p0",
            "packet 0 writes fp20 (.lh) 0.0625, packet 1 slot 0 reads bare: "
            "0x10 if the fp20 path forwards the same source packet",
            {"A0": wr(U0, ".lh"), "A4": rd(0, bare=True)}, {2: STORE01}),
    variant("lh-p3",
            "packet 3 writes fp20 0.375, packet 0 reads bare: 0x60 if the wrap "
            "holds for the fp20 path too",
            {"B0": wr(U3, ".lh"), "A0": rd(0, bare=True)}, {2: STORE01}),
    variant("lh-write-l-read",
            "packet 3 writes fp20 0.375 and packet 0 reads .l: 0, the format "
            "rule of notes 28",
            {"B0": wr(U3, ".lh"), "A0": rd(0)}, {2: STORE01}),
    variant("bare-read",
            "packet 3 writes fx10 and packet 0 reads bare: 0, the same rule "
            "the other way round",
            {"B0": wr(U3), "A0": rd(0, bare=True)}, {2: STORE01}),

    # Two writers.
    variant("both-1-2-read3",
            "packets 1 (0x20) and 2 (0x40) write alu0, packet 3 reads it: "
            "0x40 if the immediate predecessor is the source",
            {"A4": wr(U1), "B0": wr(U2), "B4": rd(0)}, {2: STORE01}),
    variant("both-own-prev",
            "packet 0 writes 0x10, its own slot 3 reads alu0, and packet 3 "
            "writes 0x60: 0x60 if the rotation beats own-slot exclusion, 0x10 "
            "if the own packet wins",
            {"A0": wr(U0), "A3": rd(0), "B0": wr(U3)}, {2: STORE01}),
    variant("both-0-1-read2",
            "packets 0 (0x10) and 1 (0x20) write alu0, packet 2 reads it: 0x20",
            {"A0": wr(U0), "A4": wr(U1), "B0": rd(0)}, {2: STORE01}),

    # Group-boundary carry, stored by group C.
    variant("carry-s0",
            "group A's last packet writes 0x60 to alu0, group B's first packet "
            "slot 0 reads it, group C's first batch stores: 0 if the carry "
            "does not survive a four-packet group of two batches",
            {"B4": wr(U3), "C0": rd(0)}, {4: STORE01}),
    variant("carry-s2",
            "the same write read from group B slot 2 instead of slot 0: 0",
            {"B4": wr(U3), "C2": rd(0)}, {4: STORE01}),
    variant("carry-none",
            "group B reads alu0 with no write in group A: 0",
            {"C0": rd(0)}, {4: STORE01}),

    # Controls.
    variant("row-direct",
            "a row write in packet 0, stored by batch 2: 0x10, the control "
            "that a row from group A reaches the group-B store",
            {"A0": wr(U0)}, {2: STORE23}),
    variant("unmarked",
            "all four packets write their own byte, no read: 0",
            {"A0": wr(U0), "A4": wr(U1), "B0": wr(U2), "B4": wr(U3)},
            {2: STORE01}),
    variant("none",
            "no write, one store in batch 2: the word baseline",
            {}, {2: STORE01}),
    variant("rot2-ctl",
            "size 2, one packet per batch, batch 0 writes 0.25 and batch 1 "
            "reads alu0 (notes 36's `rot2-fwd`): 0x40 - the known-good "
            "two-packet rotation. `alu_latch.in` numbers its constants "
            "0.0625/0.25/0.5/0.125, so u1 is 0x40 here, not 0x20",
            {"A0": wr(U1), "B0": rd(0)}, {2: STORE01}, abuf=2,
            template=TEMPLATE_LATCH),
    variant("rot3-ctl",
            "size 3, one packet per batch, batch 1 writes 0.5 (u2, 0x7F) and "
            "batch 2 reads alu0: 0x7F - the known-good three-packet rotation",
            {"B0": wr(U2), "C0": rd(0)}, {3: STORE01}, abuf=3,
            template=TEMPLATE_LATCH),
    variant("carry-ctl-s1",
            "size 1, batch 0 writes 0x10 to alu0 and batch 1 slot 0 reads it: "
            "0x10 - the group-boundary carry that does work",
            {"A0": wr(U0), "B0": rd(0)}, {2: STORE01}, abuf=1,
            template=TEMPLATE_LATCH),
]

WRAP_VARIANTS = [
    variant("wrap-al1-s0",
            "packet 3 slot 1 writes 0x60, packet 0 slot 0 reads alu1: 0x60 if "
            "the wrap exists for latch 1 and only slot 0 is special",
            {"B1": wr(U3), "A0": rd(1)}, {2: STORE01}),
    variant("wrap-al2-s0",
            "packet 3 slot 2 writes 0x60, packet 0 slot 0 reads alu2: 0x60",
            {"B2": wr(U3), "A0": rd(2)}, {2: STORE01}),
    variant("wrap-al3-s0",
            "packet 3 slot 3 writes 0x60, packet 0 slot 0 reads alu3: 0x60 if "
            "the failure of `src3-read0` follows the latch index rather than "
            "the reading slot",
            {"B7": wr(U3), "A0": rd(3)}, {2: STORE01}),
    variant("wrap-al0-s3",
            "packet 3 slot 0 writes 0x60, packet 0 slot 3 reads alu0: 0x60 if "
            "the writer slot rather than the reader slot is what fails",
            {"B0": wr(U3), "A3": rd(0)}, {2: STORE01}),
    variant("wrap-al0-s2",
            "the same writer read from packet 0's slot 2",
            {"B0": wr(U3), "A2": rd(0)}, {2: STORE01}),
    variant("wrap-al1-s1",
            "packet 3 slot 1 writes, packet 0 slot 1 reads: control that the "
            "reading slot is not what makes a wrap fail",
            {"B1": wr(U3), "A1": rd(1)}, {2: STORE01}),
    variant("back-same-p0",
            "packet 1 slot 0 writes 0x20 and packet 0 slot 0 reads alu0, both "
            "in batch 0: 0x20 if direction does not matter inside one batch",
            {"A4": wr(U1), "A0": rd(0)}, {2: STORE01}),
    variant("back-same-p2",
            "packet 3 slot 0 writes 0x60 and packet 2 slot 0 reads alu0, both "
            "in batch 1: 0x60 or 0, the same question for the second batch",
            {"B4": wr(U3), "B0": rd(0)}, {2: STORE01}),
    variant("back-cross-b1",
            "packet 2 slot 0 writes 0x40 and packet 1 slot 0 reads alu0: a "
            "backward hop across the batch boundary inside the group",
            {"B0": wr(U2), "A4": rd(0)}, {2: STORE01}),
    variant("rot2-back-ctl",
            "size 2, one packet per batch, packet 1 slot 0 writes 0.25 (0x40) "
            "and packet 0 slot 0 reads alu0 (notes 36's `rot2-back`): 0x40, "
            "the M=2 wrap the M=4 wrap is compared against",
            {"A0": rd(0), "B0": wr(U1)}, {2: STORE01}, abuf=2,
            template=TEMPLATE_LATCH),
    variant("none",
            "no write, one store in batch 2: the word baseline",
            {}, {2: STORE01}),
]

PROBE_VARIANTS = [
    variant("al3-nowrite",
            "packet 0 slot 3 reads alu3 with no write anywhere: 0 if the 0x60 "
            "`alu3-from-p3-slot3` measured came from packet 3's write and not "
            "from leftover state",
            {"A3": rd(3)}, {2: STORE01}),
    variant("al3-p3s3-p0s1",
            "packet 3 slot 3 writes 0x60, packet 0 slot 1 reads alu3: does the "
            "wrap need the reading slot to be 3",
            {"B7": wr(U3), "A1": rd(3)}, {2: STORE01}),
    variant("al3-p3s3-p0s2",
            "the same read from packet 0 slot 2",
            {"B7": wr(U3), "A2": rd(3)}, {2: STORE01}),
    variant("al2-p3s3-p0s3",
            "packet 3 slot 3 writes 0x60, packet 0 slot 3 reads alu2: 0 if the "
            "success follows the latch index rather than the reading slot",
            {"B7": wr(U3), "A3": rd(2)}, {2: STORE01}),
    variant("al3-p3s2-p0s3",
            "packet 3 slot 2 writes 0x60, packet 0 slot 3 reads alu3: 0 if the "
            "writing slot must be 3 too",
            {"B2": wr(U3), "A3": rd(3)}, {2: STORE01}),
    variant("al3-p3s1-p0s3",
            "packet 3 slot 1 writes 0x60, packet 0 slot 3 reads alu3: 0",
            {"B1": wr(U3), "A3": rd(3)}, {2: STORE01}),
    variant("p3s3-write-only",
            "packet 3 slot 3 writes 0x60 and nothing reads it, stored from the "
            "row: 0x60 proves the write itself lands",
            {"B7": wr(U3)}, {2: STORE23}),
    variant("carry-s3",
            "group A's last packet slot 3 writes 0x60, group B slot 3 reads "
            "alu3, stored by group C: the same question as `carry-s0` for the "
            "third latch",
            {"B7": wr(U3), "C3": rd(3)}, {4: STORE01}),
    variant("rot2-al3-s3",
            "size 2, one packet per batch, batch 1 slot 3 writes 0.5 (0x7F) and "
            "batch 0 slot 3 reads alu3: the M=2 mirror of the one wrap read "
            "that returned a value at M=4",
            {"A3": rd(3), "B3": wr(U2)}, {2: STORE01}, abuf=2,
            template=TEMPLATE_LATCH),
    variant("rot2-al3-s0",
            "the same M=2 wrap read from batch 0 slot 0 instead of slot 3",
            {"A0": rd(3), "B3": wr(U2)}, {2: STORE01}, abuf=2,
            template=TEMPLATE_LATCH),
    variant("none",
            "no write, one store in batch 2: the word baseline",
            {}, {2: STORE01}),
]

PROBE2_VARIANTS = [
    variant("al3-p1s3-p0s3",
            "packet 1 slot 3 writes 0.125 (0x20), packet 0 slot 3 reads alu3: "
            "0x20 if packet 0's latch-3 read is served by the packet after "
            "it rather than only by packet 3",
            {"A7": wr(U1), "A3": rd(3)}, {2: STORE01}),
    variant("al3-p2s3-p0s3",
            "packet 2 slot 3 writes 0.25 (0x40), packet 0 slot 3 reads alu3: "
            "0x40 if a mid-group packet can serve the read",
            {"B3": wr(U2), "A3": rd(3)}, {2: STORE01}),
    variant("al3-p1p3-p0s3",
            "packets 1 (0x20) and 3 (0x60) both write slot 3, packet 0 slot 3 "
            "reads alu3: 0x60 if the group's last writer wins, 0x20 if the "
            "next packet does",
            {"A7": wr(U1), "B7": wr(U3), "A3": rd(3)}, {2: STORE01}),
    variant("al3-p3s3-p1s3",
            "packet 3 slot 3 writes 0x60, packet 1 slot 3 reads alu3: the "
            "control that latch 3 does not simply see the group's last write",
            {"B7": wr(U3), "A7": rd(3)}, {2: STORE01}),
    variant("al1-p1s1-p0s1",
            "packet 1 slot 1 writes 0.125 (0x20), packet 0 slot 1 reads alu1: "
            "0x20 would mean the next-packet path exists for latch 1 too, 0 "
            "that it is latch-3 only",
            {"A5": wr(U1), "A1": rd(1)}, {2: STORE01}),
    variant("rot3-al3-ctl",
            "size 3, one packet per batch, batch 2 slot 3 writes 0.5 (0x7F) and "
            "batch 0 slot 3 reads alu3: the M=3 wrap for the third latch",
            {"A3": rd(3), "C3": wr(U2)}, {3: STORE01}, abuf=3,
            template=TEMPLATE_LATCH),
    variant("rot3-al0-ctl",
            "the same M=3 wrap for latch 0, which notes 36 measured as 0x40 "
            "with a 0x40 payload",
            {"A0": rd(0), "C0": wr(U2)}, {3: STORE01}, abuf=3,
            template=TEMPLATE_LATCH),
    variant("none",
            "no write, one store in batch 2: the word baseline",
            {}, {2: STORE01}),
]

SIZE4_VARIANTS = [
    variant("m4-s4-wrap",
            "size 4, one packet per batch, batch 3 slot 0 writes 0.375 (0x60) "
            "and batch 0 slot 0 reads alu0: 0x60 if the M=4 wrap works when "
            "each batch holds one packet, which separates M=4 from the "
            "two-packets-per-batch shape",
            {"B3": wr(U3), "B0": rd(0)}, {4: STORE01}, abuf=4,
            template=TEMPLATE8),
    variant("m4-s4-fwd01",
            "size 4, batch 0 writes 0.0625 (0x10) and batch 1 reads: 0x10, the "
            "adjacency control",
            {"B0": wr(U0), "B1": rd(0)}, {4: STORE01}, abuf=4,
            template=TEMPLATE8),
    variant("m4-s4-fwd12",
            "size 4, batch 1 writes 0.125 (0x20) and batch 2 reads: 0x20",
            {"B1": wr(U1), "B2": rd(0)}, {4: STORE01}, abuf=4,
            template=TEMPLATE8),
    variant("m4-s4-fwd23",
            "size 4, batch 2 writes 0.25 (0x40) and batch 3 reads: 0x40",
            {"B2": wr(U2), "B3": rd(0)}, {4: STORE01}, abuf=4,
            template=TEMPLATE8),
    variant("m4-s4-twoaway",
            "size 4, batch 2 writes 0x40 and batch 0 reads: 0, two apart",
            {"B2": wr(U2), "B0": rd(0)}, {4: STORE01}, abuf=4,
            template=TEMPLATE8),
    variant("m4-s4-back",
            "size 4, batch 1 writes 0.125 (0x20) and batch 0 reads: 0x20 if a "
            "later packet serves an earlier one in this geometry",
            {"B1": wr(U1), "B0": rd(0)}, {4: STORE01}, abuf=4,
            template=TEMPLATE8),
    variant("m4-s4-write-only",
            "size 4, batch 3 writes 0x60 and nothing reads, stored from the "
            "row: 0x60 proves the write lands",
            {"B3": wr(U3)}, {4: STORE23}, abuf=4,
            template=TEMPLATE8),
    variant("m4-s4-none",
            "size 4, no write, one store in batch 4: the word baseline",
            {}, {4: STORE01}, abuf=4, template=TEMPLATE8),
]

MATRIX_VARIANTS = [
    variant(
        f"m4x-p{writer}-al{latch}-s{reader}",
        f"packet 3 slot {writer} writes 0x60, which fills alu{writer} because "
        f"the issuing slot selects the latch; packet 0 slot {reader} reads "
        f"alu{latch}: 0x60 if the wrap delivers the last slot-{writer} result "
        f"of the group to latch {latch}" + (
            "" if latch == writer else
            f"; packet 3 never wrote alu{latch}, so a value would mean the "
            "wrap crosses latch indices"),
        {f"B{4 + writer}": wr(U3), f"A{reader}": rd(latch)},
        {2: STORE01})
    for writer in range(4)
    for latch in range(4)
    for reader in range(4)
]

MATRIX_CONTROLS = [
    variant("ctl-ref22-wrap3",
            "the exact known-positive wrap from notes 38: packet 3 slot 3 "
            "writes 0x60, packet 0 slot 3 reads alu3: 0x60. Identical to the "
            "matrix cell `m4x-p3-al3-s3`",
            {"B7": wr(U3), "A3": rd(3)}, {2: STORE01}),
    variant("ctl-ref22-wrap0",
            "the exact known-negative wrap from notes 38: packet 3 slot 0 "
            "writes 0x60, packet 0 slot 0 reads alu0: 0. Identical to the "
            "matrix cell `m4x-p0-al0-s0`",
            {"B0": wr(U3), "A0": rd(0)}, {2: STORE01}),
    variant("ctl-ref22-fwd",
            "the known forward edge from notes 38: packet 2 slot 0 writes 0.25 "
            "(0x40), packet 3 slot 0 reads alu0: 0x40",
            {"B0": wr(U2), "B4": rd(0)}, {2: STORE01}),
    variant("ctl-ref31-wrap",
            "the known-good M=4 3+1 wrap from notes 39, on `asm/alu_pack43.in`: "
            "packet 3 slot 0 writes 0x60, packet 0 slot 0 reads alu0: 0x60",
            {"B0": wr(U3), "A0": rd(0)}, {2: STORE01},
            template=TEMPLATE_PACK43),
    variant("ctl-ref211-wrap",
            "the known-good M=4 size-3 2+1+1 wrap from notes 43, on "
            "`asm/alu_dist211.in`: packet 3 slot 0 writes 0x60, packet 0 slot "
            "0 reads alu0, stored by batch 3: 0x60",
            {"C0": wr(U3), "A0": rd(0)}, {3: STORE01},
            abuf=3, template=TEMPLATE_DIST211),
    variant("ctl-row-direct-60",
            "packet 3 slot 3 writes 0x60 and nothing reads a latch; batch 2 "
            "stores r2: 0x60, the direct-row control for the payload",
            {"B7": wr(U3)}, {2: STORE23}),
    variant("none",
            "no write, one store in batch 2: the word baseline",
            {}, {2: STORE01}),
]

SETS = {"main": VARIANTS, "wrap": WRAP_VARIANTS, "probe": PROBE_VARIANTS,
        "probe2": PROBE2_VARIANTS, "size4": SIZE4_VARIANTS,
        "m4matrix": MATRIX_VARIANTS + MATRIX_CONTROLS}

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


def build_manifest(only=None, section="main"):
    entries = []
    seen = set()
    for entry in SETS[section]:
        if entry["name"] in seen:
            raise SystemExit(f"duplicate variant {entry['name']}")
        seen.add(entry["name"])
        if only and entry["name"] not in only:
            continue
        placeholders, batches = TEMPLATES[entry["template"]]
        unknown = set(entry["slots"]) - set(placeholders)
        if unknown:
            raise SystemExit(f"{entry['name']}: unknown slots {sorted(unknown)}")
        unknown = set(entry["dws"]) - set(range(batches))
        if unknown:
            raise SystemExit(f"{entry['name']}: unknown DW batches {sorted(unknown)}")
        replace = {"@ABUF@": str(entry["abuf"])}
        for name in placeholders:
            replace[f"@{name}@"] = entry["slots"].get(name, NOP)
        for batch in range(batches):
            replace[f"@D{batch}@"] = entry["dws"].get(batch, NOP)
        entries.append({
            "name": entry["name"],
            "fs": entry["template"],
            "replace": {"fs": replace},
            "environment": {"GRATE_DUMP_RAW": "1"},
            "expected_note": entry["note"],
        })
    if not entries:
        raise SystemExit("no variants selected")
    return {
        "name": f"alu-pack4-{section}" if section != "main" else "alu-pack4",
        "repeat": 3,
        "timeout": 20,
        "program": {"vs": VS, "fs": TEMPLATE4, "lnk": LNK},
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
    parser.add_argument("--only", help="comma-separated variant names to keep")
    parser.add_argument("--set", dest="section", choices=sorted(SETS),
                        default="main", help="variant set to build")
    args = parser.parse_args()

    only = set(args.only.split(",")) if args.only else None
    manifest = build_manifest(only, args.section)
    out = args.out or {"main": OUT, "wrap": OUT_WRAP, "probe": OUT_PROBE,
                       "probe2": OUT_PROBE2, "size4": OUT_SIZE4,
                       "m4matrix": OUT_MATRIX}[args.section]
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
