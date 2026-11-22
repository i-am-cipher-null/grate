#!/usr/bin/env python3
"""Generate the interpolation-disable manifest.

The linker's `(dis)` modifier sets the per-component "triangle interpolation
disable" bit, and the reference records the fragment-side effect only as "the
modifier replaces that component's interpolated value with a vertex value.
Which vertex is not determined by this single sample." That is the question
here: with three distinct per-vertex values and one sample, the stored word
names the vertex.

The vertex shader exports `a[1].zyxx`, so the harness's unit-vector vertex
colours become

    vertex 0 (1,0,0) -> (0, 0, 1, 1)
    vertex 1 (0,1,0) -> (1, 0, 0, 0)
    vertex 2 (0,0,1) -> (0, 1, 0, 0)
    vertex 3 (1,0,0) -> (0, 0, 1, 1)

and the fs stores them as [w][z][x][y] bytes. The first fragment sits inside the
first triangle, whose vertices are 1, 2 and 3, so three of the four candidates
are distinct at that sample - and the interpolated (no-disable) word is a fourth
distinct value, which is what makes "the modifier did nothing" identifiable
rather than assumed.

Which vertex a `(dis)` component takes is answered by comparison rather than by
reasoning about the slot-to-component mapping: three reference variants store
each candidate vertex vector through the same destination bytes and store word,
so the `(dis)` word is matched against them. If it matches none, the modifier
mixes components from different vertices, which is itself the answer.

Variants:

  plain          the unmodified linker word, so the sample is the interpolated
                 value - the control that the varying path works at all
  dis-all        `(dis)` on all four columns
  dis-x/y/z      `(dis)` on one column only, in each of the three positions
                 the earlier probes did not use
  ref-v0v3       the vector of vertex 0/3, (x, y, z, w) = (0, 0, 1, 1)
  ref-v1         vertex 1's vector, (0, 1, 0, 0)
  ref-v2         vertex 2's vector, (1, 0, 0, 0)

`--results DIR` pins each variant to the word its repeats agreed on.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/fs_dis.in"
OUT = "tests/grate/asm_characterize.mfu_dis.json"
VS = "tests/grate/asm/vs_dis.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"
LNK_DIS = "tests/grate/asm/linker_dis.txt"
LNK_DIS_X = "tests/grate/asm/linker_dis_x.txt"
LNK_DIS_Y = "tests/grate/asm/linker_dis_y.txt"
LNK_DIS_Z = "tests/grate/asm/linker_dis_z.txt"

REF = "tests/grate/asm/fs_dis_ref.in"

VARIANTS = [
    ("plain", TEMPLATE, LNK, {},
     "no modifier: the sample is the interpolated value"),
    ("dis-all", TEMPLATE, LNK_DIS, {},
     "every column disabled: the word names the vertex the fragment sees"),
    ("dis-x", TEMPLATE, LNK_DIS_X, {},
     "the first column disabled, the other three interpolated"),
    ("dis-y", TEMPLATE, LNK_DIS_Y, {},
     "the second column disabled"),
    ("dis-z", TEMPLATE, LNK_DIS_Z, {},
     "the third column disabled"),
    ("ref-v0v3", REF, LNK,
     {"REF_X": "#0", "REF_Y": "#0", "REF_Z": "#1", "REF_W": "#1"},
     "vertex 0/3's vector (0, 0, 1, 1) through the same bytes"),
    ("ref-v1", REF, LNK,
     {"REF_X": "#0", "REF_Y": "#1", "REF_Z": "#0", "REF_W": "#0"},
     "vertex 1's vector (0, 1, 0, 0)"),
    ("ref-v2", REF, LNK,
     {"REF_X": "#1", "REF_Y": "#0", "REF_Z": "#0", "REF_W": "#0"},
     "vertex 2's vector (1, 0, 0, 0)"),
]


def variants():
    out = []
    for name, fs, lnk, mapping, note in VARIANTS:
        entry = {"name": name, "fs": fs, "lnk": lnk,
                 "expected_note": note}
        if mapping:
            entry["replace"] = {"fs": mapping}
        out.append(entry)
    return out


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
        "vs": "tests/grate/asm/vs_mov.txt",
        "fs": "tests/grate/asm/fs_vs_tests.txt",
        "lnk": LNK,
        "expected": "0xFF01FF00",
    },
]


def build_manifest():
    entries = variants()
    names = [entry["name"] for entry in entries]
    if len(names) != len(set(names)):
        raise SystemExit("variant names are not unique")
    return {
        "name": "mfu-dis",
        "repeat": 3,
        "timeout": 20,
        "program": {"vs": VS, "fs": TEMPLATE, "lnk": LNK},
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
    parser.add_argument("--results", help="run directory to pin from")
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
        print(f"{entry['name']:10} {entry.get('expected', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
