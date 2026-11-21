#!/usr/bin/env python3
"""Generate the ALU control, condition and send characterization manifest.

Template `alu_control.in` has three packets. The primes leave 0.25 in r2.lo and
0.75 in r3.lo; a consumer writes r3.l* and the word is `r3 << 16 | r2` after
the DW conversion, so a consumer result of 0.5 reads 0x007F....

Groups:

  csel-*    CSEL's two data paths. rA and rB form one product, rC and rD the
            other; the sign of rA*rB picks the branch, and the operand order
            separates "returns rB" from "returns rA*rB".
  cr-*      condition-register writes and reads: next packet, same packet,
            the two sub-registers, two slots writing different registers, and
            a conditional write.
  send-*    (other)/(this) arrangements: two and three senders with one
            receiver, a sender in the last slot with the receiver next packet,
            a receiver before its sender, a MIN producer, and a MUL producer.
  fw-*      several slots writing one fp20 destination, against the fx10 OR
            control, including one fp20 and one fx10 writer on the same word.
  mfu-*/tex-*  an intervening MFU or TEX stage between a producer and its
            consumer, and a stage that overwrites the producer's register.

`--results DIR` pins each variant to the word its repeats agreed on.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/alu_control.in"
OUT = "tests/grate/asm_characterize.alu_control.json"

NOP = "NOP"
STORE = "store rt1, r2, r3"

# Primes: r2.lo = 0.25, r3.lo = 0.75 through fx10 halves.
PRIME = ["MAD  r2.l*,  u2,  #1,  #0",
         "MAD  r3.l*,  u6,  #1,  #0",
         NOP,
         NOP]


def variant(name, p=None, a=None, b=None,
            m1=NOP, t1=NOP, m2=NOP, t2=NOP, store=STORE, args=None):
    replace = {"STORE_SLOT": store, "M1_SLOT": m1, "T1_SLOT": t1,
               "M2_SLOT": m2, "T2_SLOT": t2}
    for letter, packet in zip("PAB", (p, a, b)):
        for slot in range(4):
            replace[f"{letter}{slot}_SLOT"] = (packet or [NOP] * 4)[slot]
    entry = {"name": name, "replace": {"fs": replace}}
    if args:
        entry["args"] = args
    return entry


def variants():
    out = [
        # CSEL: rA*rB against rC*rD. With rB = #1 the two candidates differ
        # only in the positive case; the negative case uses rB = 0.5 so the
        # true branch can be told from rA*rB.
        variant("csel-base", a=["CSEL r3.l*,  u2,  #1,  u6,  #1", NOP, NOP, NOP]),
        variant("csel-pos", a=["CSEL r3.l*,  u4,  #1,  u2,  #1", NOP, NOP, NOP]),
        variant("csel-neg-rb", a=["CSEL r3.l*,  -u2,  u4,  u6,  #1", NOP, NOP, NOP]),
        variant("csel-neg-a", a=["CSEL r3.l*,  -u2,  #1,  u6,  #1", NOP, NOP, NOP]),
        variant("csel-zero", a=["CSEL r3.l*,  #0,  u4,  u6,  #1", NOP, NOP, NOP]),
        # rD is only spellable as the constant 1.0, and `#0` there is a parse
        # error ("Invalid src register"): disabling rD means omitting the
        # fourth operand. So CSEL's rC path is tested with rD absent and with
        # rD = 1.0.
        variant("csel-rd-off", a=["CSEL r3.l*,  u2,  u4,  u6", NOP, NOP, NOP]),
        variant("csel-rd-one", a=["CSEL r3.l*,  u2,  u4,  u6,  #1", NOP, NOP, NOP]),
        variant("csel-cc-eq", a=["CSEL r3.l*,  u2,  u4,  u6,  #1 (eq)", NOP, NOP, NOP]),
        variant("csel-cc-gt", a=["CSEL r3.l*,  u2,  u4,  u6,  #1 (gt)", NOP, NOP, NOP]),

        # Condition registers. A write without a condition stores
        # (result != 0); a read in the same packet reads 0.
        variant("cr-next", a=["MAD  cr0,  u6,  #1,  #0", NOP, NOP, NOP],
                b=["MAD  r3.l*,  cr0,  #1,  #0", NOP, NOP, NOP]),
        variant("cr-same", a=["MAD  cr0,  u6,  #1,  #0",
                              "MAD  r3.l*,  cr0,  #1,  #0", NOP, NOP]),
        variant("cr-pair-low", a=["MAD  cr0,  u6,  #1,  #0", NOP, NOP, NOP],
                b=["MAD  r3.l*,  cr1,  #1,  #0", NOP, NOP, NOP]),
        variant("cr-pair-high", a=["MAD  cr1,  u6,  #1,  #0", NOP, NOP, NOP],
                b=["MAD  r3.l*,  cr1,  #1,  #0", NOP, NOP, NOP]),
        variant("cr-two-slots", a=["MAD  cr2,  u2,  #1,  #0",
                                   "MAD  cr4,  u4,  #1,  #0", NOP, NOP],
                b=["MAD  r3.l*,  cr2,  #1,  #0", NOP, NOP, NOP]),
        variant("cr-cond-write", a=["MAD  cr0,  u0,  #1,  #0 (eq)", NOP, NOP, NOP],
                b=["MAD  r3.l*,  cr0,  #1,  #0", NOP, NOP, NOP]),

        # (other) sends, (this) receives. The receiver replaces rC with the
        # accumulator, so its own rC value never reaches the result.
        variant("send-two", a=["MAD  r2.l*,  u2,  #1,  #0 (this)",
                               "MAD  r2.l*,  u4,  #1,  #0 (this)",
                               "MAD  r3.l*,  u0,  #1,  #0 (this)", NOP]),
        variant("send-three", a=["MAD  r2.l*,  u2,  #1,  #0 (this)",
                                 "MAD  r2.l*,  u4,  #1,  #0 (this)",
                                 "MAD  r2.l*,  u6,  #1,  #0 (this)",
                                 "MAD  r3.l*,  u0,  #1,  #0 (this)"]),
        variant("send-last-then-recv", a=["MAD  r2.l*,  u6,  #1,  #0 (this)",
                                          NOP, NOP,
                                          "MAD  r2.l*,  u4,  #1,  #0 (other)"],
                b=["MAD  r3.l*,  u0,  #1,  #0 (this)", NOP, NOP, NOP]),
        variant("send-min", a=["MIN  r2.l*,  u2,  #1,  u6,  #1 (other)",
                               "MAD  r3.l*,  u0,  #1,  #0 (this)", NOP, NOP]),
        variant("send-before", a=["MAD  r3.l*,  u0,  #1,  #0 (this)",
                                  "MAD  r2.l*,  u6,  #1,  #0 (other)",
                                  NOP, NOP]),
        variant("send-mul", a=["MUL  r2.l*,  u2,  #1,  u4 (other)",
                               "MAD  r3.l*,  u0,  #1,  #0 (this)", NOP, NOP]),

        # Several writers to one fp20 destination. The fx10 control is the
        # measured OR (0.25 | 0.75 = 0xC0 -> 0.75); the fp20 cases store the
        # raw fp20 word, so the word itself names the winner or the merge.
        variant("fw-two", a=["MAD  r2.lh,  u2,  #1,  #0",
                             "MAD  r2.lh,  u6,  #1,  #0", NOP, NOP],
                store="store rt1, r2, r3"),
        variant("fw-three", a=["MAD  r2.lh,  u0,  #1,  #0",
                               "MAD  r2.lh,  u2,  #1,  #0",
                               "MAD  r2.lh,  u6,  #1,  #0", NOP]),
        variant("fw-fx10-two", a=["MAD  r2.l*,  u2,  #1,  #0",
                                  "MAD  r2.l*,  u6,  #1,  #0", NOP, NOP]),
        variant("fw-lh-then-fx10", a=["MAD  r2.lh,  u2,  #1,  #0",
                                      "MAD  r2.l*,  u6,  #1,  #0", NOP, NOP]),
        variant("fw-lh-then-high", a=["MAD  r2.lh,  u2,  #1,  #0",
                                      "MAD  r2.*h,  u6,  #1,  #0", NOP, NOP]),

        # An intervening stage. The MFU writes r5, which nothing reads; the
        # TEX writes r0/r1, so r2's producer value should survive either one.
        variant("mfu-between", a=["MAD  r2.l*,  u4,  #1,  #0", NOP, NOP, NOP],
                m2="sfu: rcp r5",
                b=["MAD  r3.l*,  r2.l,  #1,  #0", NOP, NOP, NOP]),
        variant("tex-between", a=["MAD  r2.l*,  u4,  #1,  #0", NOP, NOP, NOP],
                t2="0x00001400",
                b=["MAD  r3.l*,  r2.l,  #1,  #0", NOP, NOP, NOP],
                args=["--texture"]),
        # The same TEX but into r2/r3: the fetch is the new writer, so the
        # consumer must see the texel (white at s in [0.5, 1]), not 0.5.
        variant("tex-clobber", a=["MAD  r2.l*,  u4,  #1,  #0", NOP, NOP, NOP],
                t2="0x00001420",
                b=["MAD  r3.l*,  r2.l,  #1,  #0", NOP, NOP, NOP],
                args=["--texture"]),
        # An MFU SFU op that writes the producer's register.
        variant("mfu-clobber", a=["MAD  r2.l*,  u4,  #1,  #0", NOP, NOP, NOP],
                m2="sfu: rcp r2",
                b=["MAD  r3.l*,  r2.l,  #1,  #0", NOP, NOP, NOP]),
    ]
    return out


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


def build_manifest():
    entries = variants()
    names = [entry["name"] for entry in entries]
    if len(names) != len(set(names)):
        raise SystemExit("variant names are not unique")
    return {
        "name": "alu-control",
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
