#!/usr/bin/env python3
"""Generate the two-DW, two-target manifest.

Section 3 measured that a group with two DW instructions produces no observable
store at all, on one render target, at alu_buffer_size 2 to 4. It could not say
whether the target each DW names matters, because both wrote rt1 and the runner
reads one surface. This set puts the two DWs of one group on *different*
targets and reads each target independently, in both batch orders, with a
single-DW control and an untouched-target control for each target.

    astraight   DW0 -> rt1, DW1 -> rt4
    swapped     DW0 -> rt4, DW1 -> rt1
    same        both DWs -> rt1 (the section 3 arrangement, as a control)
    single      only DW0, on one target
    none        no DW in the group

Each program is run once per target it needs read: the fetch word in batch 2
selects rt1 or rt4 and the group-1 DW stores what it fetched into rt1, which is
what the runner reads. Only the fetch differs between the two runs of one
program, so a difference between the readbacks cannot come from the stores
under test.

Payloads: A = 0x44332211 (batch 0), B = 0x3D2C1B0A (batch 1). A store reports
the *group's* final row - section 3 measured that a store from batch 0 returns
the group's last byte - so both DWs of a group store B if either runs; the
payload names the row, and the target names the DW.

RT4 is bound by the assembler when GRATE_RT4 is set; the environment is
identical in every variant so the draw cannot differ in which targets exist.

`--results DIR` pins each variant to the modal word of its repeats.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/dw_two_target.in"
OUT = "tests/grate/asm_characterize.dw_two_target.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE1 = "store rt1, r0, r1"
STORE4 = "store rt4, r0, r1"
FETCH1 = "load rt1, r0, r1"
FETCH4 = "load rt4, r0, r1"


def variant(name, dw0, dw1, fetch, note):
    return {
        "name": name,
        "dws": (dw0, dw1, STORE1, NOP),
        "fetch": fetch,
        "note": note,
    }


VARIANTS = [
    variant("two-a1-b4-read1", STORE1, STORE4, FETCH1,
            "DW to rt1 in batch 0, DW to rt4 in batch 1; read rt1 before the "
            "readback store changes it"),
    variant("two-a1-b4-read4", STORE1, STORE4, FETCH4,
            "the same program, read rt4"),
    variant("two-a4-b1-read1", STORE4, STORE1, FETCH1,
            "the same two DWs with the batch order swapped; read rt1"),
    variant("two-a4-b1-read4", STORE4, STORE1, FETCH4,
            "the swapped program, read rt4"),
    variant("two-a1-b1-read1", STORE1, STORE1, FETCH1,
            "both DWs on rt1 - the arrangement section 3 measured - read rt1"),
    variant("two-a1-b1-read4", STORE1, STORE1, FETCH4,
            "the same, read rt4: the untouched-target control for that pair"),
    variant("single-1-read1", STORE1, NOP, FETCH1,
            "one DW in the group's first batch, on rt1: the positive control"),
    variant("single-1-read4", STORE1, NOP, FETCH4,
            "the same program, read rt4: rt4 must keep its baseline"),
    variant("single-4-read4", STORE4, NOP, FETCH4,
            "one DW, on rt4: the positive control for rt4"),
    variant("single-4-read1", STORE4, NOP, FETCH1,
            "the same program, read rt1: rt1 must keep its baseline"),
    variant("single-b-1-read1", NOP, STORE1, FETCH1,
            "the group's only DW sits in its second batch, on rt1: section 3 "
            "says that position stores nothing"),
    variant("single-b-4-read4", NOP, STORE4, FETCH4,
            "the same position on rt4, so the position rule is not an "
            "artefact of the colour target"),
    variant("none-read1", NOP, NOP, FETCH1,
            "no DW in the group: what rt1 holds before any store"),
    variant("none-read4", NOP, NOP, FETCH4,
            "no DW in the group: what rt4 holds before any store"),
]

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
        replace = {f"@DW{index}@": value
                   for index, value in enumerate(entry["dws"])}
        replace["@FETCH@"] = entry["fetch"]
        entries.append({
            "name": entry["name"],
            "fs": TEMPLATE,
            "replace": {"fs": replace},
            "environment": {"GRATE_RT4": "1"},
            "expected_note": entry["note"],
        })
    return {
        "name": "dw-two-target",
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
