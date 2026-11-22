#!/usr/bin/env python3
"""Generate the two-DW, two-MRT-target manifest.

Section 29 measured two DW words in one scheduling group on rt1 and rt4: the
first DW's target is written, with the group's final row, and the second DW is
dropped silently. Both targets there were special - rt1 is the colour surface
the runner reads, rt4 is the spill surface - so "first DW wins" may be a
property of that pair rather than of MRT output. This set repeats the
arrangement on rt5, the first render-target index that means MRT output
(`5..12 = MRT n+1`, so rt5 is `gl_FragData[1]`), and on the pair rt4/rt5.

Pairs and orders:

    mrt-*     rt1 against rt5, both batch orders
    spill-*   rt4 against rt5, both batch orders
    same-*    both DWs on one target (section 29's same-target control)
    single-*  one DW only: the positive control for its target, and the
              untouched-target control for the other
    pair-*    rt1 against rt5 with the two DWs storing *different* row pairs,
              so the payload names the DW rather than only the batch
    none-*    no DW in the group: the pre-draw baseline of each target

Hypotheses and the words that separate them:

    target-agnostic  first-DW-wins is a rule of the scheduling group: in every
                     order the first DW's target gets B and the second target
                     keeps its baseline. The reversed variants swap which
                     target that is, and `mrt` and `spill` agree.
    spill-specific   the rule depends on rt4 being the spill surface: `spill-*`
                     differs from `mrt-*` in which target survives.
    last-wins        the second DW's target is written and the first is not,
                     which would also have made section 29's `two-a1-b4` write
                     rt4 - it did not, so this hypothesis is already refuted
                     for that pair and here tests whether MRT changes it.

Payloads: A = 0x44332211 (batch 0), B = 0x3D2C1B0A (batch 1). Stored words name
the row pair the store read, so a store that reports A in a `pair-` variant is
the first DW's own rows and one that reports B is the group's last row write.

Every variant binds rt4 and rt5, so the draw is identical throughout and a
baseline read cannot differ because a surface was missing.

`--results DIR` pins each variant to the modal word of its repeats.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/dw_mrt.in"
OUT = "tests/grate/asm_characterize.dw_mrt.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE = {
    1: "store rt1, r0, r1",
    4: "store rt4, r0, r1",
    5: "store rt5, r0, r1",
}
STORE_PAIR = {
    1: ("store rt1, r2, r3", "store rt1, r0, r1"),
    5: ("store rt5, r2, r3", "store rt5, r0, r1"),
    4: ("store rt4, r2, r3", "store rt4, r0, r1"),
}
FETCH = {target: f"load rt{target}, r0, r1" for target in (1, 4, 5)}

ENVIRONMENT = {"GRATE_RT4": "1", "GRATE_RT5": "1"}


def prime(first, pair):
    """Four low/high writes that build payload `pair` in two row registers.

    `pair` is "A" (u0..u3) or "B" (u4..u7); `first` is the row register the
    payload starts in, so first=r0 gives r0/r1 and first=r2 gives r2/r3.
    """
    consts = (0, 1, 2, 3) if pair == "A" else (4, 5, 6, 7)
    second = "r1" if first == "r0" else "r3"
    return {
        0: f"MAD  {first}.l*,  u{consts[0]},  #1,  #0",
        1: f"MAD  {first}.*h,  u{consts[1]},  #1,  #0",
        2: f"MAD  {second}.l*,  u{consts[2]},  #1,  #0",
        3: f"MAD  {second}.*h,  u{consts[3]},  #1,  #0",
    }


def variant(name, dw0, dw1, fetch, note, prime0=("r0", "A")):
    """One program: batch 0 primes `prime0` and issues `dw0`, batch 1 primes B."""
    return {
        "name": name,
        "prime0": prime(*prime0),
        "prime1": prime("r0", "B"),
        "dw0": dw0,
        "dw1": dw1,
        "fetch": fetch,
        "note": note,
    }


READ = "read rt{target}: the only word that differs between the two runs"


def two(name, first, second):
    """Both orders of a two-target pair, read back one target per run."""
    return [
        variant(f"{name}-a{first}-b{second}-read{first}",
                STORE[first], STORE[second], FETCH[first],
                f"DW0 -> rt{first} in batch 0, DW1 -> rt{second} in batch 1; "
                + READ.format(target=first)),
        variant(f"{name}-a{first}-b{second}-read{second}",
                STORE[first], STORE[second], FETCH[second],
                f"the same program, " + READ.format(target=second)),
        variant(f"{name}-a{second}-b{first}-read{second}",
                STORE[second], STORE[first], FETCH[second],
                f"the same two DWs with the batches swapped: DW0 -> "
                f"rt{second}; " + READ.format(target=second)),
        variant(f"{name}-a{second}-b{first}-read{first}",
                STORE[second], STORE[first], FETCH[first],
                f"the swapped program, " + READ.format(target=first)),
    ]


def single(target, read):
    return variant(
        f"single-{target}-read{read}",
        STORE[target], NOP, FETCH[read],
        f"one DW, on rt{target}, in the group's first batch; "
        + READ.format(target=read)
        + (" (the positive control for the target that was written)"
           if read == target else
           " (the untouched-target control: this must stay at baseline)"))


VARIANTS = (
    two("mrt", 1, 5)
    + two("spill", 4, 5)
    + [
        variant("same-1-read1", STORE[1], STORE[1], FETCH[1],
                "both DWs on rt1 with rt5 bound: section 29's same-target "
                "result, as a control that binding another target changes "
                "nothing"),
        variant("same-5-read5", STORE[5], STORE[5], FETCH[5],
                "both DWs on rt5 - the same-target control on the MRT target"),
        single(1, 1), single(1, 5),
        single(5, 5), single(5, 1),
        single(4, 4), single(5, 4),
        variant("single-b-5-read5", NOP, STORE[5], FETCH[5],
                "the group's only DW sits in its second batch, on rt5: "
                "section 29 says that position stores nothing; does it hold "
                "for the MRT target"),
        variant("none-read1", NOP, NOP, FETCH[1],
                "no DW in the group: rt1's pre-draw content (the clear)"),
        variant("none-read4", NOP, NOP, FETCH[4],
                "no DW in the group: rt4's pre-draw content"),
        variant("none-read5", NOP, NOP, FETCH[5],
                "no DW in the group: rt5's pre-draw content"),
        variant("pair-a1-b5-read1",
                STORE_PAIR[1][0], STORE_PAIR[5][1], FETCH[1],
                "DW0 stores r2/r3 (payload A, batch 0) to rt1, DW1 stores "
                "r0/r1 (payload B, batch 1) to rt5: A means the surviving "
                "store used the first DW's own rows, B the group's last row "
                "write; read rt1", prime0=("r2", "A")),
        variant("pair-a1-b5-read5",
                STORE_PAIR[1][0], STORE_PAIR[5][1], FETCH[5],
                "the same program, read rt5", prime0=("r2", "A")),
        variant("pair-a5-b1-read5",
                STORE_PAIR[5][0], STORE_PAIR[1][1], FETCH[5],
                "the pair with the targets swapped: DW0 stores r2/r3 (A) to "
                "rt5, DW1 stores r0/r1 (B) to rt1; read rt5",
                prime0=("r2", "A")),
    ]
)

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
        replace = {}
        for batch, prefix in ((0, "P"), (1, "Q")):
            slots = entry["prime0"] if batch == 0 else entry["prime1"]
            for slot in range(4):
                replace[f"@{prefix}{slot}@"] = slots.get(slot, NOP)
        replace["@DW0@"] = entry["dw0"]
        replace["@DW1@"] = entry["dw1"]
        replace["@FETCH@"] = entry["fetch"]
        replace["@STORE@"] = STORE[1]
        entries.append({
            "name": entry["name"],
            "fs": TEMPLATE,
            "replace": {"fs": replace},
            "environment": dict(ENVIRONMENT),
            "expected_note": entry["note"],
        })
    return {
        "name": "dw-mrt",
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
        print("{:22} {}".format(entry["name"], entry.get("expected", "-")))


if __name__ == "__main__":
    main()
