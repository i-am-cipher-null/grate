#!/usr/bin/env python3
"""Generate the alu_buffer_size row matrix manifest.

The matrix measures which batch's write a later batch's store can see at
alu_buffer_size 1, 2, 3 and 4, instead of assuming the documented
``batch i -> row i mod N`` mapping. Every variant is built from one template
(asm/alu_buf_matrix.in), so all tests have the same four-batch geometry and only
the ALU packet and the DW operation of each batch change.

    make_alu_buffer_matrix.py                       # four-batch matrix, no expectations
    make_alu_buffer_matrix.py --results DIR         # pin it to the words measured in DIR
    make_alu_buffer_matrix.py --mode blocks         # eight-batch group probe instead
    make_alu_buffer_matrix.py --mode ladder         # just the batch-naming tests, per size
    make_alu_buffer_matrix.py --mode sweep          # count -> how many batches ran

The blocks mode uses asm/alu_buf_blocks8.in: one DW per group of N batches fits
four batches exactly at sizes 1, 2 and 4, so sizes 3 and 4 only get a fair test
with a batch count that is a whole number of groups.

The first form is the measurement pass. The second reads DIR/results.tsv and
copies each variant's word into the manifest as its expected value, but only
when every repeat of that variant agreed and none of them timed out; otherwise
it names the variants it cannot pin and writes nothing, so a manifest can never
be pinned to a hang or to a value that differs between repeats.

The tests per buffer size are:

  vis-w<W>-s<S>     one write in batch W, one store in batch S, for every pair.
                    This 4x4 grid is the mapping itself: V[W][S] is non-zero
                    only when the write survives to that store.
  ladder-s<S>       every batch writes its own constant, one store at S. The
                    readback names the batch whose value that store sees,
                    which is the mapping without having to infer it from a
                    present/absent grid.
  ladder-all        every batch writes its own constant and stores in the same
                    batch: the readback names the batch whose store ran last,
                    so DW participation is visible separately from row state.

A count that is not a multiple of the size hangs the engine and leaves the
channel wedged for the rest of the run, so every variant here uses the
documented count. Keep it that way when adding tests: one hang turns every
later variant in the same run into a timeout.
  conf-b0b1-s<S>    the same register written twice, 0.25 then 0.5, read at S;
                    and the same with the order reversed. Which write wins.
  two-reg-s2        two different registers written one batch apart, read
                    together, both orders. Independent rows or shared state.
  slot-<reg>-<half> every register and both halves, one slot per variant, read
                    at S. Whether the file is uniform rather than a special case
                    for r2/r3.
  baseline          nothing written at all: what an untouched row reads back as.
                    First and last in each group, so a change across the run
                    would show as state left behind by earlier draws.
"""

import argparse
import csv
import json
import pathlib
import sys

NOP = "NOP"
STORE23 = "store rt1, r2, r3"
STORE01 = "store rt1, r0, r1"

# .constants indices: [2] = 0.25, [4] = 0.5 (one exponent apart, distinct bytes)
LOW = 2
HIGH = 4

SIZES = (1, 2, 3, 4)
BATCHES = 4
# 0x520 counts execution slots: one batch occupies alu_buffer_size of them, and
# the top bits (0x20004000 | 0x2000) are mandatory on AR20.
START_WORD = 0x20006000
SLOT_STORES = (1, 2)
# 0x520 counts that land stores for the four-batch shape, three runs each. At
# sizes 1 and 2 this is the documented batches * size; at sizes 3 and 4 it is
# not, and the documented 12 and 16 land nothing from any store (measured four
# times each). A count of 4 at size 3 hangs, so the working counts are not
# interchangeable between sizes.
WORKING_COUNT = {1: 4, 2: 8, 3: 9, 4: 8}
# The ladder gives each batch its own readable byte: 0x10, 0x40, 0x7F, 0xBF.
LADDER = (0, 2, 4, 6)



def write(reg, half, const):
    """One ALU packet writing a single half of one row register."""
    mask = f"{reg}.l*" if half == "lo" else f"{reg}.*h"
    return f"MAD  {mask},  u{const},  #1,  #0"


def no_writes():
    return [NOP] * BATCHES


def store(at, instruction):
    dw = [NOP] * BATCHES
    dw[at] = instruction
    return dw


def variants_for(size):
    p = f"n{size}"
    count = BATCHES * size
    out = []

    # Control: nothing is written. Whatever this returns is what an untouched
    # row reads back as, so no other variant can credit that value to a write.
    out.append((f"{p}-baseline-s1", no_writes(), store(1, STORE23), count))

    # Ladder: every batch writes its own constant, so a store that sees the
    # state of one batch returns a value that names it.
    for at in range(BATCHES):
        batches = [write("r2", "lo", LADDER[index]) for index in range(BATCHES)]
        out.append((f"{p}-ladder-s{at}", batches, store(at, STORE23), count))
    for tag, table in (("fwd", LADDER), ("rev", LADDER[::-1])):
        batches = [write("r2", "lo", table[index]) for index in range(BATCHES)]
        dw = [STORE23] * BATCHES
        out.append((f"{p}-ladder-all-{tag}", batches, dw, count))

    # The mapping: one write, one store, every pair of batch positions.
    for where in range(BATCHES):
        for at in range(BATCHES):
            batches = no_writes()
            batches[where] = write("r2", "lo", LOW)
            out.append((f"{p}-vis-w{where}-s{at}", batches, store(at, STORE23), count))

    # Two writes to the same register, one batch apart, read at each later
    # position, in both orders: does the older write survive, or is it the
    # latest one that a store sees?
    for order, tag in (((LOW, HIGH), "025050"), ((HIGH, LOW), "050025")):
        for at in (1, 2, 3):
            batches = no_writes()
            batches[0] = write("r2", "lo", order[0])
            batches[1] = write("r2", "lo", order[1])
            out.append((f"{p}-conf-b0b1-s{at}-{tag}", batches, store(at, STORE23), count))

    # Two different registers written one batch apart and read together: are the
    # registers independent, or does one write displace the other?
    for tag, first, second in (("r2lo-r3hi", ("r2", "lo", LOW), ("r3", "hi", HIGH)),
                               ("r3hi-r2lo", ("r3", "hi", HIGH), ("r2", "lo", LOW))):
        batches = no_writes()
        batches[0] = write(*first)
        batches[1] = write(*second)
        out.append((f"{p}-two-reg-s2-{tag}", batches, store(2, STORE23), count))

    # Every register and both halves, one slot per variant, so the mapping is
    # not a special case for the registers the other tests happen to use. The
    # three slots the variant did not write stay at their baseline value.
    for reg in ("r0", "r1", "r2", "r3"):
        for half in ("lo", "hi"):
            for at in SLOT_STORES:
                batches = no_writes()
                batches[0] = write(reg, half, LOW)
                instruction = STORE23 if reg in ("r2", "r3") else STORE01
                out.append((f"{p}-slot-{reg}-{half}-s{at}",
                            batches, store(at, instruction), count))

    # Same as the opening control, last in the group.
    out.append((f"{p}-baseline-s1-again", no_writes(), store(1, STORE23), count))
    return out


def blocks_variants():
    """Eight batches, one store or a store per batch, for the group probe."""
    out = []
    # size -> how many of the eight batches do anything
    for size, active in ((1, 8), (2, 8), (4, 8)):
        count = 8 * size
        for mode in ("store-s0", "ladder-all"):
            batches = [NOP] * 8
            dw = [NOP] * 8
            for index in range(active):
                batches[index] = write("r2", "lo", LADDER[index % len(LADDER)])
                if mode == "ladder-all" or index == 0:
                    dw[index] = STORE23
            out.append((f"b8-n{size}-{mode}-{active}b", batches, dw, count))

    # Eight batches at size 2 land nothing with the documented count of 16,
    # while four batches at size 2 do with 8. Counts 8, 12 and 16 on the same
    # program separate "the count needs to be batches * size" from "the wider
    # buffer only ran once". Count 8 repeats what four batches do, while 12 and
    # the documented 16 - the plain b8-n2-store-s0-8b variant above - land
    # nothing. Last in the list: a count the engine rejects hangs, and a
    # hang poisons every later variant in the run.
    for size, count in ((2, 8), (2, 12)):
        batches = [write("r2", "lo", LADDER[index % len(LADDER)]) for index in range(8)]
        dw = [STORE23] + [NOP] * 7
        out.append((f"b8-n{size}-store-s0-8b-count{count}", batches, dw, count))
    return out


def build_blocks_manifest():
    variants = []
    for name, batches, dw, count in blocks_variants():
        replace = {"@ABUF@": str(1)}
        for index, batch in enumerate(batches):
            replace[f"@B{index}@"] = batch
            replace[f"@D{index}@"] = dw[index]
        variants.append({
            "name": name,
            "environment": {"GRATE_POKE": f"0x520=0x{START_WORD | count:08X}"},
            "replace": {"fs": replace},
        })
    # the size is in the variant name, so recover it from there
    for variant in variants:
        size = int(variant["name"].split("-")[1][1:])
        variant["replace"]["fs"]["@ABUF@"] = str(size)
    return {
        "name": "alu-buffer-blocks",
        "repeat": 3,
        "timeout": 10,
        "program": {
            "vs": "tests/grate/asm/vs_mov.txt",
            "fs": "tests/grate/asm/alu_buf_blocks8.in",
            "lnk": "tests/grate/asm/linker_vs_tests.txt",
        },
        "controls": CONTROLS,
        "variants": variants,
    }


# Every batch of the eight-batch program writes a distinct byte, so a readback
# names the batch that wrote last among those that ran: 0x10, 0x20, 0x40, 0x60,
# 0x7F, 0x9F, 0xBF, 0xDF for batches 0..7.
LADDER8 = (0, 1, 2, 3, 4, 5, 6, 7)

# Counts swept per size. 0 and >= 128 hang, and a count that is not a multiple
# of the size hangs as well, so only multiples are swept (everything is a
# multiple of 1): a hang wedges the channel and every later variant in the same
# run with it, which is what makes an unhung sweep worth the restriction.
SWEEP = {1: tuple(range(1, 11)),
         2: (2, 4, 6, 8, 10, 12, 14, 16),
         3: (3, 6, 9, 12),
         4: (4, 8, 12, 16, 20, 24, 28, 32)}


def group_ends(size, batches=8):
    """First batch of every group of `size`: where that group's DW executes."""
    return [index for index in range(batches) if index % size == 0]


def sweep_variants():
    """One variant per (size, count): how many batches did that count run?

    Every batch writes a different constant, and the first batch of every group
    stores while the rest of its group leaves the DW empty - which is what lets
    a group's DW fire at all (a store in every batch suppresses it). Each store
    therefore reports the last write of its own group, and the last store that
    fires names the last group that completed.
    """
    out = []
    for size, counts in SWEEP.items():
        for count in counts:
            batches = [write("r2", "lo", LADDER8[index]) for index in range(8)]
            dw = [NOP] * 8
            for index in group_ends(size):
                dw[index] = STORE23
            out.append((f"sweep-n{size}-c{count}", size, count, batches, dw))
    return out


def build_sweep_manifest():
    variants = []
    for name, size, count, batches, dw in sweep_variants():
        replace = {"@ABUF@": str(size)}
        for index, batch in enumerate(batches):
            replace[f"@B{index}@"] = batch
            replace[f"@D{index}@"] = dw[index]
        variants.append({
            "name": name,
            "environment": {"GRATE_POKE": f"0x520=0x{START_WORD | count:08X}"},
            "replace": {"fs": replace},
        })
    return {
        "name": "alu-buffer-count-sweep",
        "repeat": 1,
        "timeout": 8,
        "program": {
            "vs": "tests/grate/asm/vs_mov.txt",
            "fs": "tests/grate/asm/alu_buf_blocks8.in",
            "lnk": "tests/grate/asm/linker_vs_tests.txt",
        },
        "controls": CONTROLS,
        "variants": variants,
    }


CONTROLS = [
    {
        "name": "immediate-operand",
        "fs": "tests/grate/asm/fs_alu_immediate_control.txt",
        "expected": "0x000000FF",
    },
    {
        "name": "varying-pipeline",
        "fs": "tests/grate/asm/fs_vs_tests.txt",
        "expected": "0xFF01FF00",
    },
]


def build_manifest(sizes=SIZES, count=None):
    variants = []
    for size in sizes:
        # The count that reproduces, not batches * size: the matrix is the
        # proof, so it may only use counts that survive a re-run.
        variant_count = count or WORKING_COUNT[size]
        for name, batches, dw, _ in variants_for(size):
            replace = {"@ABUF@": str(size)}
            for index in range(BATCHES):
                replace[f"@B{index}@"] = batches[index]
                replace[f"@D{index}@"] = dw[index]
            variants.append({
                "name": name,
                "environment": {"GRATE_POKE": f"0x520=0x{START_WORD | variant_count:08X}"},
                "replace": {"fs": replace},
            })

    names = [variant["name"] for variant in variants]
    if len(names) != len(set(names)):
        raise SystemExit("variant names are not unique")

    return {
        "name": "alu-buffer-matrix",
        "repeat": 3,
        "timeout": 10,
        "program": {
            "vs": "tests/grate/asm/vs_mov.txt",
            "fs": "tests/grate/asm/alu_buf_matrix.in",
            "lnk": "tests/grate/asm/linker_vs_tests.txt",
        },
        "controls": CONTROLS,
        "variants": variants,
    }


def build_ladder_manifest(sizes, count=None):
    """Only the tests that name a batch: the ladder, its all-batch form and the
    untouched-row control, at the count that makes stores land for that size.
    Cheap enough to re-run per size, which keeps a count that hangs away from
    the tests that would otherwise be measured after it."""
    variants = []
    for size in sizes:
        for name, batches, dw, _ in variants_for(size):
            if not ("ladder" in name or "baseline" in name):
                continue
            replace = {"@ABUF@": str(size)}
            for index in range(BATCHES):
                replace[f"@B{index}@"] = batches[index]
                replace[f"@D{index}@"] = dw[index]
            variants.append({
                "name": name,
                "environment": {"GRATE_POKE": f"0x520=0x{START_WORD | (count or WORKING_COUNT[size]):08X}"},
                "replace": {"fs": replace},
            })
    return {
        "name": "alu-buffer-ladder",
        "repeat": 3,
        "timeout": 10,
        "program": {
            "vs": "tests/grate/asm/vs_mov.txt",
            "fs": "tests/grate/asm/alu_buf_matrix.in",
            "lnk": "tests/grate/asm/linker_vs_tests.txt",
        },
        "controls": CONTROLS,
        "variants": variants,
    }


# Leak probe: the same ladder block repeated, in an order that shows whether a
# draw changes what the next one does. Sizes 3 and 4 both gave two different
# answers in two runs of the same manifest, and what differed between those runs
# was which sizes ran before them, so each block here is separately named and
# the order is: size 4 twice (is it stable on its own), then size 3, then size 4
# (did size 3 change it), then size 3 (did size 4 change size 3), then size 4.
LEAK_SEQUENCE = (
    (4, 8, "a1"),
    (4, 8, "a2"),
    (3, 9, "b1"),
    (4, 8, "c1"),
    (3, 9, "b2"),
    (4, 8, "c2"),
)


# Count probe: the same ladder program at two counts, back to back, so the only
# variable is 0x520. Size 4 count 16 returned zero from every store in three runs
# and a store per group in a fourth, so this run alternates 8 and 16 to separate
# "count 16 does not land" from "something changed since the last run". The size
# 3 block at count 12 goes last because it is the one that has hung.
COUNT_SEQUENCE = (
    (1, 8, "size1-c8"),
    (2, 8, "size2-c8"),
    (4, 16, "size4-c16"),
    (4, 8, "size4-c8"),
    (3, 12, "size3-c12"),
    (3, 9, "size3-c9"),
    # a count that hangs takes the rest of the run with it, so the two counts
    # that are expected to be rejected go last
    (2, 4, "size2-c4"),
    (3, 4, "size3-c4"),
)


def build_leak_manifest(sequence=LEAK_SEQUENCE):
    """One ladder block per sequence entry, each with its own variant names."""
    variants = []
    for size, count, tag in sequence:
        for name, batches, dw, _ in variants_for(size):
            if not ("ladder" in name or "baseline" in name):
                continue
            replace = {"@ABUF@": str(size)}
            for index in range(BATCHES):
                replace[f"@B{index}@"] = batches[index]
                replace[f"@D{index}@"] = dw[index]
            variants.append({
                "name": f"{name}-{tag}",
                "environment": {"GRATE_POKE": f"0x520=0x{START_WORD | count:08X}"},
                "replace": {"fs": replace},
            })
    return {
        "name": "alu-buffer-leak",
        "repeat": 3,
        "timeout": 10,
        "program": {
            "vs": "tests/grate/asm/vs_mov.txt",
            "fs": "tests/grate/asm/alu_buf_matrix.in",
            "lnk": "tests/grate/asm/linker_vs_tests.txt",
        },
        "controls": CONTROLS,
        "variants": variants,
    }


def measured(path):
    """variant name -> the single result word every repeat agreed on."""
    words = {}
    failures = {}
    with (pathlib.Path(path) / "results.tsv").open() as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            if row["phase"] != "variant":
                continue
            name = row["name"]
            if row["status"] != "pass" or not row["result"]:
                failures.setdefault(name, set()).add(row["status"])
                continue
            words.setdefault(name, set()).add(row["result"])
    return words, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode",
                        choices=("matrix", "blocks", "ladder", "leak", "count", "sweep"),
                        default="matrix",
                        help="which manifest to generate (default: %(default)s)")
    parser.add_argument("--results", help="characterization run directory to pin expectations from")
    parser.add_argument("--out",
                        help="manifest to write (default: the mode's checked-in manifest)")
    parser.add_argument("--sizes", type=int, nargs="+",
                        help="restrict the matrix to these buffer sizes")
    parser.add_argument("--count", type=int,
                        help="override the 0x520 count for every variant")
    args = parser.parse_args()

    if args.mode == "blocks":
        manifest = build_blocks_manifest()
        out = args.out or "tests/grate/asm_characterize.alu_buffer_blocks.json"
    elif args.mode == "ladder":
        manifest = build_ladder_manifest(args.sizes or SIZES, args.count)
        out = args.out or "tests/grate/asm_characterize.alu_buffer_ladder.json"
    elif args.mode == "leak":
        manifest = build_leak_manifest()
        out = args.out or "/tmp/alu_buffer_leak.json"
    elif args.mode == "count":
        manifest = build_leak_manifest(COUNT_SEQUENCE)
        out = args.out or "/tmp/alu_buffer_count.json"
    elif args.mode == "sweep":
        manifest = build_sweep_manifest()
        out = args.out or "tests/grate/asm_characterize.alu_buffer_sweep.json"
    else:
        manifest = build_manifest(args.sizes or SIZES, args.count)
        out = args.out or "tests/grate/asm_characterize.alu_buffer_matrix.json"
    if args.results:
        words, failures = measured(args.results)
        problems = []
        for variant in manifest["variants"]:
            name = variant["name"]
            seen = words.get(name, set())
            if name in failures:
                problems.append(f"{name}: {', '.join(sorted(failures[name]))}")
            elif len(seen) != 1:
                problems.append(f"{name}: "
                                + ("no result" if not seen
                                   else "repeats disagree: " + ", ".join(sorted(seen))))
            else:
                variant["expected"] = seen.pop()
        if problems:
            print(f"cannot pin {len(problems)} variant(s):", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
        for variant in manifest["variants"]:
            print(f"{variant['name']:34s} {variant['expected']}")

    pathlib.Path(out).write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {out} ({len(manifest['variants'])} variants)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
