#!/usr/bin/env python3
"""Generate the ALU send-ordering characterization manifest.

One template, `asm/alu_control.in`: three packets, four scalar slots each.
Packet 0 primes r2 with 0.25 and r3 with 0.75; packet 2 holds the arrangement
under test and stores r2/r3.

The questions:

  * with one sender and one receiver in the same packet, which slot pairs
    actually transfer, and what does the receiver's accumulator hold;
  * what a sender writes to its own destination once the send bit is set,
    with the same operands and destination in every arrangement.

Every variant uses the same two instructions:

    MAD  r2.l*,  u2,  #1,  #0 (other)     sender, 0.25, writes r2
    MAD  r3.l*,  u0,  #1,  #0 (this)      receiver, 0.0625 + accumulator

Only the slots they sit in change, so any difference in the stored word comes
from the arrangement. The `plain-*` twins use the same instructions without the
`(other)`/`(this)` markers, so the marker's effect is separated from the
instruction's own result.

`--results DIR` pins each variant to the word its repeats agreed on.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/alu_control.in"
OUT = "tests/grate/asm_characterize.alu_send.json"

NOP = "NOP"
STORE = "store rt1, r2, r3"

SENDER = "MAD  r2.l*,  u2,  #1,  #0 (other)"
RECEIVER = "MAD  r3.l*,  u0,  #1,  #0 (this)"
PLAIN_SENDER = "MAD  r2.l*,  u2,  #1,  #0"
PLAIN_RECEIVER = "MAD  r3.l*,  u0,  #1,  #0"

PRIME = ["MAD  r2.l*,  u2,  #1,  #0",
         "MAD  r3.l*,  u6,  #1,  #0",
         NOP,
         NOP]


def variant(name, packet, p=None, store=STORE):
    replace = {"STORE_SLOT": store, "M1_SLOT": NOP, "T1_SLOT": NOP,
               "M2_SLOT": NOP, "T2_SLOT": NOP}
    for letter, slots in (("P", p or PRIME), ("A", [NOP] * 4),
                          ("B", packet)):
        for index in range(4):
            replace[f"{letter}{index}_SLOT"] = slots[index]
    return {"name": name, "replace": {"fs": replace}}


def arrange(sender, receiver, sender_slot, receiver_slot):
    slots = [NOP] * 4
    slots[sender_slot] = sender
    slots[receiver_slot] = receiver
    return slots


def variants():
    out = []
    for sender_slot in range(4):
        for receiver_slot in range(4):
            if sender_slot == receiver_slot:
                continue
            name = f"send-s{sender_slot}r{receiver_slot}"
            out.append(variant(name, arrange(SENDER, RECEIVER,
                                             sender_slot, receiver_slot)))
            name = f"plain-s{sender_slot}r{receiver_slot}"
            out.append(variant(name, arrange(PLAIN_SENDER, PLAIN_RECEIVER,
                                             sender_slot, receiver_slot)))

    # Single-instruction controls: the receiver alone, the sender alone in each
    # slot, and the primes with no packet.
    out.append(variant("recv-only", [RECEIVER, NOP, NOP, NOP]))
    out.append(variant("plain-recv-only",
                       [PLAIN_RECEIVER, NOP, NOP, NOP]))
    for slot in range(4):
        slots = [NOP] * 4
        slots[slot] = SENDER
        out.append(variant(f"send-only-s{slot}", slots))
        slots = [NOP] * 4
        slots[slot] = PLAIN_SENDER
        out.append(variant(f"plain-only-s{slot}", slots))
    out.append(variant("primes-only", [NOP] * 4))
    return out


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
        "name": "alu-send",
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
