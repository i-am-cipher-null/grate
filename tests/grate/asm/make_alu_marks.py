#!/usr/bin/env python3
"""Generate the ALU marker-combination characterization manifest.

The send set (section 16) marked only the sender or only the receiver, in one
fixed pairing, so it could not say which marker does what. This set runs every
ordered sender/receiver slot pair with all four marker combinations:

    nn   neither instruction marked
    sn   sender marked (other) only
    nr   receiver marked (this) only
    sr   both marked

with the same two instructions in every variant:

    MAD  r2.l*,  u2,  #1,  #0     sends 0.25, writes r2
    MAD  r3.l*,  u0,  #1,  #0     receives 0.0625 + accumulator, writes r3

packet 0 primes r2 = 0.25 and r3 = 0.75, and `store rt1, r2, r3` reads both
destinations in one word: [r3.hi][r3.lo][r2.hi][r2.lo]. So for every variant
two facts are recorded separately:

  received   r3.lo = 0x50 (0.0625 + 0.25) if the receiver got the sender's
             result, 0x10 (0.0625) if it did not;
  sent-own   r2.lo = 0x40 (0.25) if the sender still wrote its own destination,
             anything else if the marker changed its own result.

Follow-ups in the same manifest:

  two-*    two senders, 0.25 into r0 and 0.5 into r1, one receiver in r3;
           the `-acc` variant stores r2/r3 and shows what reached the
           receiver (0x10 none, 0x50 the first, 0x90 the second, 0xD0 both),
           the `-dest` variant stores r0/r1 and shows both senders' own words;
  chain-*  a sender in r2, a middle instruction in r1 that both receives and
           sends, and a receiver in r3; `-acc` stores r2/r3, `-mid` stores
           r0/r1 so the middle's own result is visible;
  dst-*    a single write to r0 or r1 from each slot, stored as r0/r1, so the
           destination field's r0..r3 range is established from hardware
           write-backs rather than from assembler words.

`--results DIR` pins each variant to the word its repeats agreed on.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/alu_marks.in"
OUT = "tests/grate/asm_characterize.alu_marks.json"

NOP = "NOP"
STORE23 = "store rt1, r2, r3"
STORE01 = "store rt1, r0, r1"

PRIME = ["MAD  r2.l*,  u2,  #1,  #0",
         "MAD  r3.l*,  u6,  #1,  #0",
         NOP,
         NOP]

SEND = "MAD  r2.l*,  u2,  #1,  #0"
RECV = "MAD  r3.l*,  u0,  #1,  #0"


def marked(instr, other=False, this=False):
    tags = ""
    if other:
        tags += " (other)"
    if this:
        tags += " (this)"
    return instr + tags


def variant(name, packet, store1=STORE23, store2=NOP, p=None):
    replace = {"STORE1_SLOT": store1, "STORE2_SLOT": store2}
    for letter, slots in (("P", p or PRIME), ("A", packet)):
        for index in range(4):
            replace[f"{letter}{index}_SLOT"] = slots[index]
    return {"name": name, "replace": {"fs": replace}}


def place(items):
    """items: {slot: instruction} -> four slots with NOP elsewhere."""
    slots = [NOP] * 4
    for slot, instr in items.items():
        slots[slot] = instr
    return slots


def matrix():
    out = []
    for combo, other, this in (("nn", False, False), ("sn", True, False),
                               ("nr", False, True), ("sr", True, True)):
        for send_slot in range(4):
            for recv_slot in range(4):
                if send_slot == recv_slot:
                    continue
                name = f"{combo}-s{send_slot}r{recv_slot}"
                out.append(variant(name, place({
                    send_slot: marked(SEND, other=other),
                    recv_slot: marked(RECV, this=this),
                })))
    return out


# (sender A slot, sender B slot, receiver slot)
TWO_SPOTS = [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)]


def two_senders():
    out = []
    for a_slot, b_slot, recv_slot in TWO_SPOTS:
        tag = f"{a_slot}{b_slot}{recv_slot}"
        packet = place({
            a_slot: marked("MAD  r0.l*,  u2,  #1,  #0", other=True),
            b_slot: marked("MAD  r1.l*,  u4,  #1,  #0", other=True),
            recv_slot: marked(RECV, this=True),
        })
        out.append(variant(f"two-{tag}-acc", packet, store1=STORE23))
        out.append(variant(f"two-{tag}-dest", packet, store1=STORE01))
    return out


# (sender slot, middle slot, receiver slot)
CHAIN_SPOTS = [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)]
MIDDLE = "MAD  r1.l*,  u4,  #1,  #0"


def chain():
    out = []
    for send_slot, mid_slot, recv_slot in CHAIN_SPOTS:
        tag = f"{send_slot}{mid_slot}{recv_slot}"
        packet = place({
            send_slot: marked(SEND, other=True),
            mid_slot: marked(MIDDLE, other=True, this=True),
            recv_slot: marked(RECV, this=True),
        })
        out.append(variant(f"chain-{tag}-acc", packet, store1=STORE23))
        out.append(variant(f"chain-{tag}-mid", packet, store1=STORE01))

    plain = place({0: SEND, 1: MIDDLE, 2: RECV})
    out.append(variant("chain-plain-acc", plain, store1=STORE23))
    out.append(variant("chain-plain-mid", plain, store1=STORE01))
    return out


def destinations():
    out = []
    for reg, pair, others in ((0, STORE01, "dst-r0"), (1, STORE01, "dst-r1")):
        for slot in range(4):
            packet = place({slot: f"MAD  r{reg}.l*,  u2,  #1,  #0"})
            out.append(variant(f"{others}-s{slot}", packet, store1=pair))
    out.append(variant("dst-none", place({}), store1=STORE01))
    out.append(variant("dst-none-23", place({}), store1=STORE23))
    return out


def variants():
    return matrix() + two_senders() + chain() + destinations()


CONTROLS = [
    {
        "name": "immediate-operand",
        "vs": "tests/grate/asm/vs_mov.txt",
        "fs": "tests/grate/asm/fs_alu_immediate_control.txt",
        "lnk": "tests/grate/asm/linker_vs_tests.txt",
        "expected": "0x000000FF",
    },
    {
        "name": "varying-pipeline",
        "vs": "tests/grate/asm/vs_mov.txt",
        "fs": "tests/grate/asm/fs_vs_tests.txt",
        "lnk": "tests/grate/asm/linker_vs_tests.txt",
        "expected": "0xFF01FF00",
    },
]


def build_manifest():
    entries = variants()
    names = [entry["name"] for entry in entries]
    if len(names) != len(set(names)):
        raise SystemExit("variant names are not unique")
    return {
        "name": "alu-marks",
        "repeat": 3,
        "timeout": 15,
        "program": {
            "vs": "tests/grate/asm/vs_mov.txt",
            "fs": TEMPLATE,
            "lnk": "tests/grate/asm/linker_vs_tests.txt",
        },
        "controls": CONTROLS,
        "variants": entries,
    }


def measured(path):
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


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", help="run directory to pin expectations from")
    parser.add_argument("--out", help="manifest to write (default: the set's)")
    args = parser.parse_args()

    manifest = build_manifest()
    if args.results:
        unpinned = pin(manifest, args.results)
        if unpinned:
            for name, seen, failed in unpinned:
                print(f"cannot pin {name}: results {seen or 'none'} "
                      f"statuses {failed or 'none'}")
            print("manifest not written")
            return 1
    out = args.out or OUT
    pathlib.Path(out).write_text(json.dumps(manifest, indent="\t") + "\n")
    print(f"wrote {out} ({len(manifest['variants'])} variants)")
    for entry in manifest["variants"]:
        print(f"{entry['name']:20} {entry.get('expected', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
