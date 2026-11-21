#!/usr/bin/env python3
"""Generate the ALU scalar-slot forwarding manifests.

The ALU packet runs up to four scalar instructions and the ISA reference says
each one "can use partial results from the previous instruction". This set
measures that directly instead of assuming it: a producer slot writes one row
register, a consumer slot in the same packet reads it, and the word says which
value the consumer saw.

  prime      r2 = 0.25 (0x40), r3 = 0.75 (0xBF) in batch 0
  producer   writes 0.5 (0x7F) to r2 in packet 1 and/or packet 2
  consumer   copies r2 to r3, also 0.5 if it saw the producer, 0.25 if it read
             the register file

The consumer lands in r3 (fx10 low) so the stored word is
r3.hi, r3.lo, r2.hi, r2.lo from the MSB. The high byte of the word is the
consumer's linear value: 0x7F means it saw the producer's 0.5, 0x40 means it
read the prime. Batch 2 is the read-back control - the same consumer in the
batch after the producer reads the register file, so a batch-1 consumer that
still returns 0x40 is not forwarding, only ordering.

Three groups:

  slot-*      every ordered pair of producer and consumer slots in one packet,
              including consumers earlier than the producer; a consumer placed
              after the producer is the forwarding case, one before it cannot
              see a write that has not issued.
  op-* / part-*  the same pair with MAD, MUL, MIN and MAX as producer and
              consumer, the three destination masks (.l*, .*h, .lh), and an
              fp20 rather than fx10 source format.
  acc-*       the (this)/(other) accumulation modifiers, the send/recv bits,
              within a packet and across a packet boundary.

`--set result-regs` is the same question for the explicit ALUN result registers,
the ISA's other partial-result path. It uses a four-packet template so a value
can be read in the producer's packet, the next packet and two packets later:
packet 0, 1 and 2 write different constants through the same slot, and the
reader's word names the packet whose value it saw. `aluN` is not a legal
destination, so the producers write a row register and only the implicit result
register is read.

`--results DIR` pins each variant to the single word its repeats agreed on, and
writes nothing if any variant has a failure or disagreeing repeats.

    make_alu_fwd.py --set fwd                  # no expectations yet
    make_alu_fwd.py --set fwd --results DIR    # pin to the words measured in DIR
    make_alu_fwd.py --set result-regs          # the ALUN timing set
"""

import argparse
import csv
import json
import pathlib

NOP = "NOP"
TEMPLATE = "tests/grate/asm/alu_fwd.in"
OUT = "tests/grate/asm_characterize.alu_fwd.json"
RESULT_TEMPLATE = "tests/grate/asm/alu_result_regs.in"
RESULT_OUT = "tests/grate/asm_characterize.alu_result_regs.json"

# 0x520's low field is the batch count: three EXEC blocks, one row.
START_WORD = 0x20006000
BATCHES = 3

# .constants indices: [2] = 0.25, [4] = 0.5, [6] = 0.75
LOW = "u2"
HIGH = "u4"
STORE = "store rt1, r2, r3"

# Prime r2 in both fx10 halves with 0.25 and r3 with 0.75, so every read of an
# untouched half returns a known 0x40/0xBF instead of whatever the previous
# draw left there. Variants using fp20 replace these with .lh writes.
PRIME = ["MAD  r2.l*,  u2,  #1,  #0",
         "MAD  r2.*h,  u2,  #1,  #0",
         "MAD  r3.l*,  u6,  #1,  #0",
         NOP]
PRIME_FP20 = ["MAD  r2.lh,  u2,  #1,  #0", NOP, "MAD  r3.l*,  u6,  #1,  #0", NOP]

PRODUCE = "MAD  r2.l*,  u4,  #1,  #0"       # r2.lo = 0.5
CONSUME = "MAD  r3.l*,  r2.l,  #1,  #0"     # r3.lo = r2.lo


def slots(producer, consumer, produce=PRODUCE, consume=CONSUME):
    """One producer instruction in slot `producer`, consumer in `consumer`."""
    packet = [NOP, NOP, NOP, NOP]
    packet[producer] = produce
    packet[consumer] = consume
    return packet


def variant(name, a=None, b=None, primes=PRIME):
    a = a or [NOP, NOP, NOP, NOP]
    b = b or [NOP, NOP, NOP, NOP]
    return {"name": name, "primes": list(primes), "a": list(a), "b": list(b)}


def variants():
    out = [
        # Controls: what the primes read back as with no write, and what the
        # same consumer sees one packet later (the register file).
        variant("control-prime-only"),
        variant("control-read-prime", a=slots(0, 1, produce=NOP)),
        variant("control-next-packet",
                a=[PRODUCE, NOP, NOP, NOP],
                b=[CONSUME, NOP, NOP, NOP]),
        variant("control-next-packet-p3",
                a=[NOP, NOP, NOP, PRODUCE],
                b=[CONSUME, NOP, NOP, NOP]),
    ]

    # Producer/consumer slot matrix within one packet, both orders.
    for producer in range(4):
        for consumer in range(4):
            if producer == consumer:
                continue
            out.append(variant(f"slot-p{producer}c{consumer}",
                               a=slots(producer, consumer)))

    # Producer and consumer opcodes, producer always in slot 0 and consumer in
    # slot 1. MUL writes both halves, with the mask naming which half gets
    # rA*rB and the other getting rC*rD (measured in the addition-disable set).
    out += [
        variant("op-prod-mad", a=slots(0, 1)),
        variant("op-prod-mul", a=slots(0, 1, produce="MUL  r2.l*,  u4,  #1,  u4")),
        variant("op-prod-mul-diff", a=slots(0, 1, produce="MUL  r2.l*,  u4,  #1,  u2")),
        variant("op-prod-min", a=slots(0, 1, produce="MIN  r2.l*,  u4,  #1,  u4")),
        variant("op-prod-max", a=slots(0, 1, produce="MAX  r2.l*,  u4,  #1,  u2")),
        variant("op-cons-mad", a=slots(0, 1)),
        variant("op-cons-mul", a=slots(0, 1, consume="MUL  r3.l*,  r2.l,  #1,  #1")),
        variant("op-cons-min", a=slots(0, 1, consume="MIN  r3.l*,  r2.l,  #1,  #1")),
        variant("op-cons-max", a=slots(0, 1, consume="MAX  r3.l*,  r2.l,  #1,  #0")),
    ]

    # Destination masks and source formats. A .l* write is one fx10 half, so a
    # consumer reading the other half must still see the prime; an .lh write is
    # fp20 in both halves and is read back through the fp20 source format.
    out += [
        variant("part-lo-read-hi",
                a=slots(0, 1, consume="MAD  r3.l*,  r2.h,  #1,  #0")),
        variant("part-hi-read-lo",
                a=slots(0, 1, produce="MAD  r2.*h,  u4,  #1,  #0",
                        consume="MAD  r3.l*,  r2.l,  #1,  #0")),
        variant("part-hi-read-hi",
                a=slots(0, 1, produce="MAD  r2.*h,  u4,  #1,  #0",
                        consume="MAD  r3.l*,  r2.h,  #1,  #0")),
        variant("part-lh-read-fp20",
                primes=PRIME_FP20,
                a=slots(0, 1, produce="MAD  r2.lh,  u4,  #1,  #0",
                        consume="MAD  r3.l*,  r2,  #1,  #0")),
        variant("part-lh-read-h",
                primes=PRIME_FP20,
                a=slots(0, 1, produce="MAD  r2.lh,  u4,  #1,  #0",
                        consume="MAD  r3.l*,  r2.h,  #1,  #0")),
    ]

    # Accumulation. (this) is the recv bit, (other) the send bit. A consumer
    # with recv takes the accumulation value in rC, so `MAD r3.l* ..., #0`
    # returning 0.75 rather than 0.5 says the recv path carried 0.25.
    out += [
        variant("acc-none", a=slots(0, 1)),
        variant("acc-this-01", a=slots(0, 1,
                produce="MAD  r2.l*,  u2,  #1,  #0 (this)",
                consume="MAD  r3.l*,  u4,  #1,  #0 (this)")),
        variant("acc-this-12", a=slots(1, 2,
                produce="MAD  r2.l*,  u2,  #1,  #0 (this)",
                consume="MAD  r3.l*,  u4,  #1,  #0 (this)")),
        variant("acc-this-012", a=[
            "MAD  r0.l*,  u1,  #1,  #0 (this)",
            "MAD  r2.l*,  u2,  #1,  #0 (this)",
            "MAD  r3.l*,  u4,  #1,  #0 (this)",
            NOP]),
        variant("acc-this-only-0", a=slots(0, 1,
                produce="MAD  r2.l*,  u2,  #1,  #0 (this)")),
        variant("acc-this-only-1", a=slots(0, 1,
                consume="MAD  r3.l*,  u4,  #1,  #0 (this)")),
        variant("acc-other-01", a=slots(0, 1,
                produce="MAD  r2.l*,  u2,  #1,  #0 (other)",
                consume="MAD  r3.l*,  u4,  #1,  #0 (this)")),
        variant("acc-both-01", a=slots(0, 1,
                produce="MAD  r2.l*,  u2,  #1,  #0 (this)(other)",
                consume="MAD  r3.l*,  u4,  #1,  #0 (this)")),
        variant("acc-send-12", a=slots(1, 2,
                produce="MAD  r2.l*,  u2,  #1,  #0 (other)",
                consume="MAD  r3.l*,  u4,  #1,  #0 (this)")),
        # Across the packet boundary: batch 1 produces, batch 2 consumes. The
        # register-file read is the control; a 0.75 says the recv path carried
        # the previous packet's accumulator into ALU0.
        variant("acc-xpacket",
                a=["MAD  r2.l*,  u2,  #1,  #0 (this)", NOP, NOP, NOP],
                b=["MAD  r3.l*,  u4,  #1,  #0 (this)", NOP, NOP, NOP]),
        variant("acc-xpacket3",
                a=[NOP, NOP, NOP, "MAD  r2.l*,  u2,  #1,  #0 (this)"],
                b=["MAD  r3.l*,  u4,  #1,  #0 (this)", NOP, NOP, NOP]),
    ]
    # Several slots writing the same destination in one packet. The result-
    # register set's four-packet program turned this up by accident: two slots
    # writing r2.lo with 0.25 and 0.5 left 0.75, not 0.5. The packet here writes
    # one constant per slot so the readback can name every contribution: if the
    # writes add, 0.0625 + 0.125 + 0.25 is 0.4375 (0x70); if the last one wins
    # it is 0.25 (0x40), and if the first one wins it is 0.0625 (0x10).
    out += [
        variant("w-one", a=["MAD  r2.l*,  u0,  #1,  #0", NOP, NOP, NOP]),
        variant("w-two", a=["MAD  r2.l*,  u0,  #1,  #0",
                            "MAD  r2.l*,  u1,  #1,  #0", NOP, NOP]),
        variant("w-three", a=["MAD  r2.l*,  u0,  #1,  #0",
                              "MAD  r2.l*,  u1,  #1,  #0",
                              "MAD  r2.l*,  u2,  #1,  #0", NOP]),
        variant("w-rev", a=[NOP, NOP, "MAD  r2.l*,  u0,  #1,  #0",
                            "MAD  r2.l*,  u1,  #1,  #0"]),
        variant("w-same", a=["MAD  r2.l*,  u2,  #1,  #0",
                             "MAD  r2.l*,  u2,  #1,  #0", NOP, NOP]),
        # Same value, different constant table entries (u2 and u8 are both
        # 0.25): separates "equal results do not add" from "reading one uniform
        # twice in a packet loses the second read".
        variant("w-same-value", a=["MAD  r2.l*,  u2,  #1,  #0",
                                   "MAD  r2.l*,  u8,  #1,  #0", NOP, NOP]),
        variant("w-same-half", a=["MAD  r2.l*,  u4,  #1,  #0",
                                  "MAD  r2.l*,  u9,  #1,  #0", NOP, NOP]),
        variant("w-same-half-const", a=["MAD  r2.l*,  u4,  #1,  #0",
                                        "MAD  r2.l*,  u4,  #1,  #0", NOP, NOP]),
        # Bitwise OR or arithmetic sum? 0.25 (0x40) and 0.75 (0xC0) OR to 0xC0
        # and read back as 0.75 (0xBF), while a sum is 1.0 (0xFF). 0.5 (0x80)
        # and 0.75 likewise OR to 0xC0 but sum to 1.25.
        variant("w-or-25-75", a=["MAD  r2.l*,  u2,  #1,  #0",
                                 "MAD  r2.l*,  u6,  #1,  #0", NOP, NOP]),
        variant("w-or-50-75", a=["MAD  r2.l*,  u4,  #1,  #0",
                                 "MAD  r2.l*,  u6,  #1,  #0", NOP, NOP]),
        # Same value and same constants, different instruction shape: is it the
        # uniform read or the identical instruction word that matters?
        variant("w-shape", a=["MAD  r2.l*,  u2,  #1,  #0",
                              "MAD  r2.l*,  #1,  u2,  #0", NOP, NOP]),
        variant("w-halves", a=["MAD  r2.l*,  u2,  #1,  #0",
                               "MAD  r2.*h,  u4,  #1,  #0", NOP, NOP]),
        variant("w-three-same", a=["MAD  r2.l*,  u2,  #1,  #0",
                                   "MAD  r2.l*,  u2,  #1,  #0",
                                   "MAD  r2.l*,  u2,  #1,  #0", NOP]),
        variant("w-mul", a=["MAD  r2.l*,  u2,  #1,  #0",
                            "MUL  r2.l*,  u4,  #1,  u2", NOP, NOP]),
    ]
    return out


# A packet of four slots, used as the default in the result-register set.
QUIET = [NOP, NOP, NOP, NOP]


def result_variant(name, a=None, b=None, c=None, e=None, store=3):
    """One variant of the four-packet result-register template."""
    replace = {}
    for letter, packet in zip("ABCE", (a, b, c, e)):
        for slot in range(4):
            replace[f"@{letter}{slot}@"] = (packet or QUIET)[slot]
    for index in range(4):
        replace[f"@D{index}@"] = STORE if index == store else NOP
    return {
        "name": name,
        "environment": {"GRATE_POKE": f"0x520=0x{START_WORD | RESULT_BATCHES:08X}"},
        "replace": {"fs": replace},
    }


def result_variants():
    """Where does an `aluN` source get its value?"""
    # Packet 0, 1 and 2 write 0.0625, 0.125 and 0.25 through slot 1, so a
    # readback names the packet whose result it saw. Slot 0 and slot 3 get
    # distinct values in the same packets so alu0/alu3 can be told apart too.
    p0 = ["MAD  r2.l*,  u0,  #1,  #0", "MAD  r2.l*,  u1,  #1,  #0", NOP,
          "MAD  r2.l*,  u0,  #1,  #0"]
    p1 = ["MAD  r2.l*,  u1,  #1,  #0", "MAD  r2.l*,  u2,  #1,  #0", NOP, NOP]
    p2 = ["MAD  r2.l*,  u2,  #1,  #0", "MAD  r2.l*,  u4,  #1,  #0", NOP, NOP]

    def reader(alu, slot, packet):
        packet = list(packet)
        packet[slot] = f"MAD  r3.l*,  {alu},  #1,  #0"
        return packet

    out = []
    # Reader after the producer in the producer's own packet: only a within-
    # packet forwarding path can return the producer's 0.25.
    out.append(result_variant("r-same-s2", a=p0, b=reader("alu1", 2, p1), store=1))
    # Reader in slot 0, before the producer slot: 0.125 is packet 0's result,
    # 0.25 is packet 1's, and 0 is no result register at all.
    out.append(result_variant("r-before-s0", a=p0, b=reader("alu1", 0, p1), store=1))
    # alu0: the producer is slot 0 of the same packet, which has already issued
    # by the time slot 2 runs.
    out.append(result_variant("r-alu0-same", a=p0, b=reader("alu0", 2, p1), store=1))
    # alu3: slot 3 has not issued when slot 2 runs in the same packet.
    out.append(result_variant("r-alu3-same", a=p0, b=reader("alu3", 2, p1), store=1))
    # One packet later: packet 1's producer is 0.25, packet 0's is 0.125.
    out.append(result_variant("r-next-s0", a=p0, b=p1, c=reader("alu1", 0, p2), store=2))
    # Reader after packet 2's own producer: 0.5 is the same packet, 0.25 is one
    # packet back, 0.125 is two packets back.
    out.append(result_variant("r-next-s2", a=p0, b=p1, c=reader("alu1", 2, p2), store=2))
    out.append(result_variant("r-alu0-next", a=p0, b=p1,
                              c=reader("alu0", 0, QUIET), store=2))
    # Control: no producer at all in the packet or earlier.
    out.append(result_variant("r-none", b=reader("alu1", 2, QUIET), store=1))
    # fp20 producer: does the result register keep the fp20 word (0xEF00 for
    # 0.5, read back through the fp20 source as 0x7F) or an fx10 half?
    fp = [NOP, "MAD  r2.lh,  u4,  #1,  #0", NOP, NOP]
    out.append(result_variant("r-fp20", b=fp, c=reader("alu1", 2, QUIET), store=2))
    out.append(result_variant("r-fp20-h", b=fp, c=reader("alu1.h", 2, QUIET), store=2))
    out.append(result_variant("r-fp20-l", b=fp, c=reader("alu1.l", 2, QUIET), store=2))
    # Does the send or recv bit populate the result register even though a plain
    # write does not?
    out.append(result_variant("r-send-next",
                              b=[NOP, "MAD  r2.l*,  u4,  #1,  #0 (other)", NOP, NOP],
                              c=reader("alu1", 2, QUIET), store=2))
    out.append(result_variant("r-recv-next",
                              b=[NOP, "MAD  r2.l*,  u4,  #1,  #0 (this)", NOP, NOP],
                              c=reader("alu1", 2, QUIET), store=2))
    return out


def build_result_manifest():
    entries = result_variants()
    names = [entry["name"] for entry in entries]
    if len(names) != len(set(names)):
        raise SystemExit("variant names are not unique")
    return {
        "name": "alu-result-regs",
        "repeat": 3,
        "timeout": 10,
        "program": {
            "vs": "tests/grate/asm/vs_mov.txt",
            "fs": RESULT_TEMPLATE,
            "lnk": "tests/grate/asm/linker_vs_tests.txt",
        },
        "controls": CONTROLS,
        "variants": entries,
    }


RESULT_BATCHES = 4


def build_manifest():
    entries = []
    for entry in variants():
        replace = {}
        for index in range(4):
            replace[f"@P{index}@"] = entry["primes"][index]
            replace[f"@A{index}@"] = entry["a"][index]
            replace[f"@B{index}@"] = entry["b"][index]
        replace["@STORE@"] = STORE
        entries.append({
            "name": entry["name"],
            "environment": {"GRATE_POKE": f"0x520=0x{START_WORD | BATCHES:08X}"},
            "replace": {"fs": replace},
        })
    names = [entry["name"] for entry in entries]
    if len(names) != len(set(names)):
        raise SystemExit("variant names are not unique")
    return {
        "name": "alu-forwarding",
        "repeat": 3,
        "timeout": 10,
        "program": {
            "vs": "tests/grate/asm/vs_mov.txt",
            "fs": TEMPLATE,
            "lnk": "tests/grate/asm/linker_vs_tests.txt",
        },
        "controls": CONTROLS,
        "variants": entries,
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


def measured(path):
    """variant name -> the single word every repeat agreed on."""
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


def pin(manifest, results):
    words, failures = measured(results)
    unpinned = []
    for entry in manifest["variants"]:
        name = entry["name"]
        seen = words.get(name, set())
        if name in failures or len(seen) != 1:
            unpinned.append((name, sorted(seen), sorted(failures.get(name, ()))))
            continue
        entry["expected"] = seen.pop()
    return unpinned


SETS = {
    "fwd": (OUT, build_manifest),
    "result-regs": (RESULT_OUT, build_result_manifest),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", choices=sorted(SETS), default="fwd",
                        help="which set to generate (default: fwd)")
    parser.add_argument("--results", help="run directory to pin expectations from")
    parser.add_argument("--out", help="manifest to write (default: the set's)")
    args = parser.parse_args()

    default_out, build = SETS[args.set]
    manifest = build()
    if args.results:
        unpinned = pin(manifest, args.results)
        if unpinned:
            for name, seen, failed in unpinned:
                print(f"cannot pin {name}: results {seen or 'none'} "
                      f"statuses {failed or 'none'}")
            print("manifest not written")
            return 1
    out = args.out or default_out
    pathlib.Path(out).write_text(json.dumps(manifest, indent="\t") + "\n")
    print(f"wrote {out} ({len(manifest['variants'])} variants)")
    for entry in manifest["variants"]:
        print(f"{entry['name']:28} {entry.get('expected', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
