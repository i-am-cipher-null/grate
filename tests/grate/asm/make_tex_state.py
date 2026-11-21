#!/usr/bin/env python3
"""Generate the sampler-state manifest: mip ties across levels, and per-axis wrap.

Two follow-ups from section 19, both on the constant-coordinate path that set
established (asm/tex_probe.in, the TEX word, an explicit level in r3):

1. Section 19 measured the nearest-mip tie at 0.5, 1.5 and 2.5 on a 16x16 chain
   in quarter steps. This set repeats it on a 32x32 chain, where the level words
   are 0xFF, 0x3F, 0x0F, 0x03 and 0x00, checks the values immediately around
   each tie (0.4375/0.5/0.5625, 1.4375/1.5/1.5625, 2.4375/2.5/2.5625) and adds
   finer steps at 1.5 (1.4950, 1.5000, 1.5050). The same 1.5 tie is also run on
   a 64x64 chain, so the rule is not tied to one chain length. At a tie the
   nearest filter must pick one level, and the level words differ, so the tie
   resolves to a named level. Level 4 of a one-texel chain rounds to zero, so
   the tie at 3.5 cannot be read this way and is left out.

2. Section 19 set the wrap state on both axes at once, so s-repeated with
   t-clamped was untested. `GRATE_TEX_GRID` fills a 4x4 grid of blocks with 16
   distinct words, the red byte naming the block column and the green byte the
   block row, so one fetched word names both axes. `GRATE_TEX_WRAP_S` and
   `GRATE_TEX_WRAP_T` set the axes independently. At (1.25, 1.25) the texel
   index is 20 in both axes, so repeat reads block 1, clamp block 3 and mirror
   block 2, and all nine combinations give different words. The same question at
   the edge (1.0, 1.0) with the linear filter is added, where the two taps are
   texels 15 and 16 and each mode decides what the second tap is.

TEX instruction-word semantics are not touched here: every variant uses the same
fetch word, and only the sampler's own state and the level register change.

`--results DIR` pins each variant to the word its repeats agreed on.
"""

import argparse
import csv
import json
import pathlib

OUT = "tests/grate/asm_characterize.tex_state.json"
FS = "tests/grate/asm/tex_probe.in"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

TXL = 0x00002420

NEAREST_MIP = "nearest-mipmap-nearest"
LINEAR_MIP = "nearest-mipmap-linear"


def tag(value):
    return str(value).replace("-", "m").replace(".", "p")


def probe(name, *, s, t, lod, env, note="", store="store rt4, r2, r3",
          mipmap=True):
    return {
        "name": name,
        "fs": FS,
        "vs": VS,
        "lnk": LNK,
        "replace": {
            "fs": {
                "S_CONST": s,
                "T_CONST": t,
                "R_CONST": "0.0",
                "L_CONST": lod,
                "TEX0_SLOT": f"0x{TXL:08X}",
                "TEX1_SLOT": "NOP",
                "X0_SLOT": "NOP",
                "X1_SLOT": "NOP",
                "X2_SLOT": "NOP",
                "X3_SLOT": "NOP",
                "STORE_SLOT": store,
            },
        },
        "args": ["--texture"] + (["--mipmap"] if mipmap else []),
        "environment": dict(env),
        "expected_note": note,
    }


def tie_group():
    """A one-texel chain whose level words all differ, around each tie."""
    def chain(size, at):
        return {"GRATE_TEX_SIZE": size,
                "GRATE_TEX_PIXEL": "0,0,0xFFFFFFFF",
                "GRATE_TEX_MIN_FILTER": NEAREST_MIP,
                "GRATE_RT4": "1", "GRATE_REGION": "0,0,64,64"}, at

    env32, at32 = chain("32x32", "0.015625")   # texel (0,0) centre of a 32x32
    env64, at64 = chain("64x64", "0.0078125")  # texel (0,0) centre of a 64x64
    out = []
    for level in ("0.0", "1.0", "2.0", "3.0", "4.0"):
        out.append(probe(f"tie32-level-{tag(level)}", s=at32, t=at32,
                         lod=level, env=env32,
                         note=f"32x32 chain, explicit level {level}"))
    for lod in ("0.4375", "0.5", "0.5625", "1.4375", "1.5", "1.5625",
                "2.4375", "2.5", "2.5625", "1.4950", "1.5000", "1.5050"):
        out.append(probe(f"tie32-near-{tag(lod)}", s=at32, t=at32, lod=lod,
                         env=env32, note="32x32 chain, nearest mip level"))
    # How far above the tie the level has to sit before it changes: 1.5 plus
    # binary fractions, plus the symmetric values below it.
    for lod in ("1.49609375", "1.4921875", "1.501953125", "1.50390625",
                "1.5078125", "1.515625", "1.53125"):
        out.append(probe(f"tie32-fine-{tag(lod)}", s=at32, t=at32, lod=lod,
                         env=env32,
                         note="how far from the tie the level changes"))
    # The same bracket around the 0.5 and 2.5 ties, to see whether the offset
    # from the tie is the same at every boundary.
    for base in ("0.5", "2.5"):
        for step in ("0.0078125", "0.015625", "0.03125", "0.0625"):
            lod = f"{float(base) + float(step):.8f}".rstrip("0") or "0"
            out.append(probe(f"tie32-fine-{tag(lod)}", s=at32, t=at32,
                             lod=lod, env=env32,
                             note="the same bracket at another tie"))
    for level in ("0.0", "1.0", "2.0"):
        out.append(probe(f"tie64-level-{tag(level)}", s=at64, t=at64,
                         lod=level, env=env64,
                         note=f"64x64 chain, explicit level {level}"))
    for lod in ("1.4375", "1.5", "1.5625"):
        out.append(probe(f"tie64-near-{tag(lod)}", s=at64, t=at64, lod=lod,
                         env=env64, note="64x64 chain, nearest mip level"))
    return out


def wrap_group():
    """The two axes' wrap states, mixed."""
    grid = {"GRATE_TEX_SIZE": "16x16", "GRATE_TEX_GRID": "1",
            "GRATE_RT4": "1", "GRATE_REGION": "0,0,64,64"}
    linear = dict(grid, **{"GRATE_TEX_MAG_FILTER": "linear"})
    out = []
    # texel index floor(1.25 * 16 - 0.5) = 20: repeat -> block 1, clamp -> 3,
    # mirror -> 31 - 20 = 11 -> block 2.
    for wrap_s in ("repeat", "clamp", "mirror"):
        for wrap_t in ("repeat", "clamp", "mirror"):
            out.append(probe(
                f"wrap-mix-{wrap_s[:3]}-{wrap_t[:3]}-1p25",
                s="1.25", t="1.25", lod="0.0",
                env=dict(grid, **{"GRATE_TEX_WRAP_S": wrap_s,
                                  "GRATE_TEX_WRAP_T": wrap_t}),
                note="both coordinates out of range, one state per axis"))
    # the same question at the edge, where the linear filter's second tap is
    # texel 16 (the first texel of the next repeat) or texel 15 again.
    for wrap_s in ("repeat", "clamp", "mirror"):
        for wrap_t in ("repeat", "clamp", "mirror"):
            out.append(probe(
                f"wrap-edge-{wrap_s[:3]}-{wrap_t[:3]}-1p0",
                s="1.0", t="1.0", lod="0.0",
                env=dict(linear, **{"GRATE_TEX_WRAP_S": wrap_s,
                                    "GRATE_TEX_WRAP_T": wrap_t}),
                note="linear footprint at the edge, one state per axis"))
    # and the low corner, where the first tap is texel -1.
    for wrap_s in ("repeat", "clamp", "mirror"):
        out.append(probe(
            f"wrap-corner-{wrap_s[:3]}-0p0", s="0.0", t="0.0", lod="0.0",
            env=dict(linear, **{"GRATE_TEX_WRAP_S": wrap_s,
                                "GRATE_TEX_WRAP_T": "clamp"}),
            note="linear footprint at the low corner"))
    return out


def variants():
    return tie_group() + wrap_group()


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
    entries = variants()
    names = [entry["name"] for entry in entries]
    if len(names) != len(set(names)):
        raise SystemExit("variant names are not unique")
    return {
        "name": "tex-state",
        "repeat": 3,
        "timeout": 20,
        "program": {"vs": VS, "fs": FS, "lnk": LNK},
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
        print(f"{entry['name']:30} {entry.get('expected', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
