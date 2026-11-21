#!/usr/bin/env python3
"""Generate the ALU marker edge-case manifest.

Section 18 established the routing of the two markers inside one packet:
`(this)` on slot j adds slot j-1's own result (slot 0 takes slot 3), and
`(other)` on slot i delivers slot i's result to slot `(i-1) & ~1`, which the
target adds whether or not it is marked. Four cases were left open, and they are
what this set runs:

  slot0   slot 0 marked `(other)` transfers nowhere inside the packet, although
          the `(i-1) & ~1` rule would name slot 2. Each other slot is offered as
          an unmarked receiver, and slot 2 is also offered with `(this)`, so the
          two markers are separated;
  cross   a send issued in packet 0 and a receiver in packet 1, which is the
          first thing that can see a value crossing a packet boundary;
  three   three and four senders in one packet, with every slot writing a
          distinct register so the source of each result is unambiguous;
  comp    several senders whose target is the same slot, including the case
          where that slot also receives from its previous slot, which section 18
          lists as untested.

Values are chosen so that every sum a rule can produce is distinct and none
saturates: 0.0625 (0x10), 0.25 (0x40), 0.5 (0x7F) and 0.75 (0xBF), with the
packet 0 primes putting 0.25 in r2 and 0.75 in r3 so an unwritten destination is
still readable. `store rt4, r2, r3` reads `[r3][r2]` and `store rt4, r0, r1`
reads `[r1][r0]`, so two stores are used where both the sender's own destination
and the receiver's matter.

Sender-only, receiver-only and combined-marker controls are included
(`s0o-alone`, `xp-nomark`, `three-none`, `comp-none`).

`--results DIR` pins each variant to the word its repeats agreed on.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/alu_marks.in"
OUT = "tests/grate/asm_characterize.alu_marks_edge.json"

NOP = "NOP"
STORE23 = "store rt1, r2, r3"
STORE01 = "store rt1, r0, r1"

PRIME = ["MAD  r2.l*,  u2,  #1,  #0",
         "MAD  r3.l*,  u6,  #1,  #0",
         NOP,
         NOP]

V_0625 = "u0"
V_025 = "u2"
V_050 = "u4"
V_075 = "u6"


def marked(instr, other=False, this=False):
    tags = ""
    if other:
        tags += " (other)"
    if this:
        tags += " (this)"
    return instr + tags


def send(reg, value, other=True, this=False):
    return marked(f"MAD  r{reg}.l*,  {value},  #1,  #0", other=other, this=this)


def recv(reg, *, this=False, value=V_0625):
    return marked(f"MAD  r{reg}.l*,  {value},  #1,  #0", this=this)


def place(items):
    slots = [NOP] * 4
    for slot, instr in items.items():
        slots[slot] = instr
    return slots


def variant(name, packet, store1=STORE23, store2=NOP, p=None):
    replace = {"STORE1_SLOT": store1, "STORE2_SLOT": store2}
    for letter, slots in (("P", p or PRIME), ("A", packet)):
        for index in range(4):
            replace[f"{letter}{index}_SLOT"] = slots[index]
    return {"name": name, "replace": {"fs": replace}}


def slot0_group():
    """Slot 0 marked (other): where can it land?"""
    out = []
    for tag, packet, stores in (
        ("recv1", {0: send(0, V_025), 1: recv(3)}, (STORE23, STORE01)),
        ("recv2", {0: send(0, V_025), 2: recv(3)}, (STORE23, STORE01)),
        ("recv2this", {0: send(0, V_025), 2: recv(3, this=True)},
         (STORE23, STORE01)),
        ("recv3", {0: send(0, V_025), 3: recv(3)}, (STORE23, STORE01)),
        ("alone", {0: send(0, V_025)}, (STORE23, STORE01)),
    ):
        out.append(variant(f"s0o-{tag}", place(packet), store1=stores[0],
                           store2=stores[1]))
    return out


def cross_group():
    """A send in packet 0 and a receiver in packet 1."""
    # packet 0 carries the sender where the primes would otherwise sit
    def packet0(slot, value, other=True):
        slots = list(PRIME)
        slots[slot] = send(slot, value, other=other)
        return slots

    out = []
    for tag, p, a in (
        ("slot3-to-2", packet0(3, V_025), {2: recv(3)}),
        ("slot3-to-2-this", packet0(3, V_025), {2: recv(3, this=True)}),
        ("slot1-to-0", packet0(1, V_025), {0: recv(3)}),
        ("slot1-to-0-this", packet0(1, V_025), {0: recv(3, this=True)}),
        ("nomark", packet0(3, V_025, other=False), {2: recv(3)}),
        ("both-packets", packet0(3, V_025),
         {0: send(0, V_050), 2: recv(3)}),
    ):
        out.append(variant(f"xp-{tag}", place(a), store1=STORE23,
                           store2=STORE01, p=p))
    return out


def three_group():
    """Three and four senders, every destination distinct."""
    out = []
    out.append(variant("three-none", place({}), store1=STORE23,
                       store2=STORE01))
    out.append(variant("three-012", place({
        0: send(0, V_050), 1: send(1, V_025), 2: send(2, V_0625),
        3: recv(3, this=True),
    }), store1=STORE23, store2=STORE01))
    out.append(variant("three-012-this0", place({
        0: send(0, V_050, other=False, this=True), 1: send(1, V_025),
        2: send(2, V_0625), 3: recv(3, this=True),
    }), store1=STORE23, store2=STORE01))
    out.append(variant("three-123", place({
        1: send(1, V_050), 2: send(2, V_025), 3: send(3, V_0625),
        0: recv(0),
    }), store1=STORE23, store2=STORE01))
    out.append(variant("three-all", place({
        0: send(0, V_0625), 1: send(1, V_025), 2: send(2, V_050),
        3: send(3, V_075),
    }), store1=STORE23, store2=STORE01))
    return out


def comp_group():
    """Several senders with the same target slot, including a receiver there."""
    out = []
    out.append(variant("comp-none", place({
        0: recv(2), 1: send(0, V_025, other=False),
        2: send(1, V_050, other=False),
    }), store1=STORE23, store2=STORE01))
    out.append(variant("comp-12", place({
        0: recv(2), 1: send(0, V_025), 2: send(1, V_050),
    }), store1=STORE23, store2=STORE01))
    out.append(variant("comp-12-this0", place({
        0: recv(2, this=True), 1: send(0, V_025), 2: send(1, V_050),
    }), store1=STORE23, store2=STORE01))
    out.append(variant("comp-123-this0", place({
        0: recv(2, this=True), 1: send(0, V_025), 2: send(1, V_050),
        3: send(3, V_0625),
    }), store1=STORE23, store2=STORE01))
    out.append(variant("comp-12-0send", place({
        0: send(2, V_075, other=False, this=True), 1: send(0, V_025),
        2: send(1, V_050),
    }), store1=STORE23, store2=STORE01))
    return out


def pair(prefix, packet, p=None, dest=False):
    """One variant per observable store: the receiver's or another pair's."""
    out = [variant(f"{prefix}-acc", place(packet), store1=STORE23, p=p)]
    if dest:
        out.append(variant(f"{prefix}-dest", place(packet), store1=STORE01,
                           p=p))
    return out


def slot0_group():
    """Slot 0 marked (other): where can it land?"""
    out = []
    for tag, packet in (
        ("recv1", {0: send(0, V_025), 1: recv(3)}),
        ("recv2", {0: send(0, V_025), 2: recv(3)}),
        ("recv2this", {0: send(0, V_025), 2: recv(3, this=True)}),
        ("recv3", {0: send(0, V_025), 3: recv(3)}),
        ("alone", {0: send(0, V_025)}),
    ):
        out.extend(pair(f"s0o-{tag}", packet))
    return out


def cross_group():
    """A send in packet 0 and a receiver in packet 1."""
    def packet0(slot, value, other=True):
        slots = list(PRIME)
        slots[slot] = send(slot, value, other=other)
        return slots

    out = []
    for tag, p, a in (
        ("slot3-to-2", packet0(3, V_025), {2: recv(3)}),
        ("slot3-to-2-this", packet0(3, V_025), {2: recv(3, this=True)}),
        ("slot1-to-0", packet0(1, V_025), {0: recv(3)}),
        ("slot1-to-0-this", packet0(1, V_025), {0: recv(3, this=True)}),
        ("nomark", packet0(3, V_025, other=False), {2: recv(3)}),
        ("both-packets", packet0(3, V_025),
         {0: send(0, V_050), 2: recv(3)}),
    ):
        out.extend(pair(f"xp-{tag}", a, p=p))
    return out


def three_group():
    """Three and four senders, every destination distinct."""
    out = []
    out.extend(pair("three-none", {}, dest=True))
    out.extend(pair("three-012", {
        0: send(0, V_050), 1: send(1, V_025), 2: send(2, V_0625),
        3: recv(3, this=True),
    }, dest=True))
    out.extend(pair("three-012-this0", {
        0: send(0, V_050, other=False, this=True), 1: send(1, V_025),
        2: send(2, V_0625), 3: recv(3, this=True),
    }, dest=True))
    out.extend(pair("three-123", {
        1: send(1, V_050), 2: send(2, V_025), 3: send(3, V_0625),
        0: recv(0),
    }, dest=True))
    out.extend(pair("three-all", {
        0: send(0, V_0625), 1: send(1, V_025), 2: send(2, V_050),
        3: send(3, V_075),
    }))
    return out


def comp_group():
    """Several senders with the same target slot, including a receiver there."""
    out = []
    out.extend(pair("comp-none", {
        0: recv(2), 1: send(0, V_025, other=False),
        2: send(1, V_050, other=False),
    }))
    out.extend(pair("comp-12", {
        0: recv(2), 1: send(0, V_025), 2: send(1, V_050),
    }, dest=True))
    out.extend(pair("comp-12-this0", {
        0: recv(2, this=True), 1: send(0, V_025), 2: send(1, V_050),
    }))
    out.extend(pair("comp-123-this0", {
        0: recv(2, this=True), 1: send(0, V_025), 2: send(1, V_050),
        3: send(3, V_0625),
    }))
    out.extend(pair("comp-12-0send", {
        0: send(2, V_075, other=False, this=True), 1: send(0, V_025),
        2: send(1, V_050),
    }, dest=True))
    return out


def variants():

    return slot0_group() + cross_group() + three_group() + comp_group()


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
        "name": "alu-marks-edge",
        "repeat": 3,
        "timeout": 20,
        "program": {"vs": "tests/grate/asm/vs_mov.txt",
                    "fs": TEMPLATE,
                    "lnk": "tests/grate/asm/linker_vs_tests.txt"},
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
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
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
        print(f"{entry['name']:24} {entry.get('expected', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
