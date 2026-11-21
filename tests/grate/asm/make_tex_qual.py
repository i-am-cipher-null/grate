#!/usr/bin/env python3
"""Generate the TEX qualification manifest: mip ties, wrap state and varyings.

Three questions the earlier sampling set could not answer:

1. Which level a nearest-mip filter picks exactly at a tie. The earlier chain
   had identical level 1 and 2 contents at the sampled point, so a tie could
   not be seen. `GRATE_TEX_PIXEL=0,0,0xFFFFFFFF` paints one white texel before
   the chain is generated, so the levels at that texel are all different
   (16x16: 0xFF, 0x40, 0x12, 0x05, 0x05) and the sampled word names the level.

2. What the point and linear filters do at coordinates outside [0,1] once the
   wrap state is set explicitly, which the earlier set could not do because the
   harness had no wrap control. `GRATE_TEX_WRAP=clamp|repeat|mirror` now sets
   both axes, so a repeat answer and a clamped answer are separated from the
   filter's own footprint.

3. Whether an interpolated varying can reach a TEX fetch at all. The earlier
   note concluded it could not, from a fetch that returned zero - but a black
   texel is also zero, and only `fb_data[0]` was read. Here the fetch stores to
   rt4 and the whole 64x64 region is digested, the coordinate registers are
   stored per pixel in the packet after the fetch, and a checker surface turns
   a level change into a pattern change.

Sampler state is part of every variant's environment, not of the TEX word: the
word only selects the sampler index, the destination pair and the LOD/bias
register (sections 11, 12 and 15).

Groups:

  tie    16x16, one white texel, --mipmap, nearest-mipmap-nearest at each whole
         level and around 0.5, 1.5 and 2.5, then the same fractions through
         nearest-mipmap-linear;
  wrap   16x16 two-tone surface, point and linear magnification, at coordinates
         inside and outside [0,1], with the wrap state set to repeat, clamp or
         mirror;
  vary   varying coordinates from asm/vs_texcoord_ramp.txt through
         asm/linker_texcoord.txt, the coordinate registers stored per pixel,
         and fetch maps at three coordinate gradients on a 64x64 checker.

`--results DIR` pins each variant to the word its repeats agreed on.
"""

import argparse
import csv
import json
import pathlib

OUT = "tests/grate/asm_characterize.tex_qual.json"

VS_MOV = "tests/grate/asm/vs_mov.txt"
VS_RAMP = "tests/grate/asm/vs_texcoord_ramp.txt"
LNK_VS = "tests/grate/asm/linker_vs_tests.txt"
LNK_TEX = "tests/grate/asm/linker_texcoord.txt"
FS_CONST = "tests/grate/asm/tex_probe.in"
FS_VARY = "tests/grate/asm/tex_vary.in"

NOP = "NOP"
STORE = "store rt1, r2, r3"
STORE4 = "store rt4, r2, r3"
STORE01_4 = "store rt4, r0, r1"

TEX = 0x00000420
TXL = 0x00002420

NEAREST_MIP = "nearest-mipmap-nearest"
LINEAR_MIP = "nearest-mipmap-linear"


def tag(value):
    return str(value).replace("-", "m").replace(".", "p")


def probe(name, *, s, t="0.5", lod="0.0", tex=TEX, store=STORE, env,
          mipmap=False, note=""):
    return {
        "name": name,
        "fs": FS_CONST,
        "vs": VS_MOV,
        "lnk": LNK_VS,
        "replace": {
            "fs": {
                "S_CONST": s,
                "T_CONST": t,
                "R_CONST": "0.0",
                "L_CONST": lod,
                "TEX0_SLOT": f"0x{tex:08X}",
                "TEX1_SLOT": NOP,
                "X0_SLOT": NOP,
                "X1_SLOT": NOP,
                "X2_SLOT": NOP,
                "X3_SLOT": NOP,
                "STORE_SLOT": store,
            },
        },
        "args": ["--texture"] + (["--mipmap"] if mipmap else []),
        "environment": env,
        "expected_note": note,
    }


def tie_group():
    one = {"GRATE_TEX_SIZE": "16x16",
           "GRATE_TEX_PIXEL": "0,0,0xFFFFFFFF",
           "GRATE_TEX_MIN_FILTER": NEAREST_MIP}
    blend = dict(one, **{"GRATE_TEX_MIN_FILTER": LINEAR_MIP})
    at = "0.03125"  # texel (0,0) centre of a 16x16 surface
    out = []
    for lod in ("0.0", "1.0", "2.0", "3.0", "4.0"):
        out.append(probe(f"tie-level-{tag(lod)}", s=at, t=at, lod=lod,
                         tex=TXL, mipmap=True, env=one,
                         note=f"level {lod} at the white texel"))
    for lod in ("0.25", "0.4375", "0.5", "0.5625", "0.75",
                "1.25", "1.4375", "1.5", "1.5625", "1.75",
                "2.4375", "2.5", "2.5625"):
        out.append(probe(f"tie-near-{tag(lod)}", s=at, t=at, lod=lod,
                         tex=TXL, mipmap=True, env=one,
                         note="nearest mip level for this LOD"))
    for lod in ("0.0", "0.5", "1.0", "1.5", "2.5"):
        out.append(probe(f"tie-blend-{tag(lod)}", s=at, t=at, lod=lod,
                         tex=TXL, mipmap=True, env=blend,
                         note="linear mip blend for this LOD"))
    return out


def wrap_group():
    tone = {"GRATE_TEX_SIZE": "16x16"}
    point = dict(tone)
    linear = dict(tone, **{"GRATE_TEX_MAG_FILTER": "linear"})
    out = []
    for wrap in ("repeat", "clamp", "mirror"):
        for s in ("-0.5", "1.0", "1.25", "2.0"):
            out.append(probe(f"wrap-point-{wrap}-{tag(s)}", s=s, env=dict(
                point, **{"GRATE_TEX_WRAP": wrap}),
                note="point filter, wrap state set explicitly"))
    for wrap in ("repeat", "clamp"):
        for s in ("0.0", "0.0625", "0.9375", "1.0", "1.25"):
            out.append(probe(f"wrap-lin-{wrap}-{tag(s)}", s=s,
                             env=dict(linear, **{"GRATE_TEX_WRAP": wrap}),
                             note="linear footprint against the wrap state"))
    # The t axis needs rows that differ, so these use the row-colour surface:
    # t = 1.125 is row 18, which is row 2 under repeat, the last row under
    # clamp and row 13 under mirrored repeat, all different colours.
    rows = dict(tone, **{"GRATE_TEX_ROWCOLOR": "1"})
    for wrap in ("repeat", "clamp", "mirror"):
        out.append(probe(f"wrap-t-{wrap}-1p125", s="0.875", t="1.125",
                         env=dict(rows, **{"GRATE_TEX_WRAP": wrap}),
                         note="the same question on t, on a row-colour surface"))
    return out


def vary_probe(name, *, p0=(NOP, NOP), tex=NOP, store=STORE4, scale="1.0",
               env, mipmap=False, note=""):
    return {
        "name": name,
        "fs": FS_VARY,
        "vs": VS_RAMP,
        "lnk": LNK_TEX,
        "replace": {
            "fs": {
                "P0A_SLOT": p0[0],
                "P0B_SLOT": p0[1],
                "TEXWORD": f"0x{tex:08X}" if isinstance(tex, int) else tex,
                "STORE": store,
            },
        },
        "args": ["--texture", "--vs_uniform",
                 f'["scale"]=({scale},{scale},1.0,1.0)']
                + (["--mipmap"] if mipmap else []),
        "environment": env,
        "expected_note": note,
    }


def vary_group():
    checker = {"GRATE_TEX_SIZE": "64x64", "GRATE_TEX_CHECKER": "1",
               "GRATE_RT4": "1", "GRATE_REGION": "0,0,64,64",
               "GRATE_TEX_MIN_FILTER": NEAREST_MIP}
    l0 = dict(checker)
    l0.pop("GRATE_TEX_MIN_FILTER")
    out = [
        vary_probe("vary-coords-s4", store=STORE01_4, scale="4.0",
                   env=dict(checker), note="the coordinate registers per pixel"),
        vary_probe("vary-nofetch-s4", tex=NOP, store=STORE01_4, scale="4.0",
                   env=dict(checker),
                   note="no fetch: the same registers, not a texel"),
        vary_probe("vary-level0-s4", tex=TEX, scale="4.0", env=l0,
                   note="level 0: the checker pattern"),
        vary_probe("vary-mip-s1", tex=TEX, scale="1.0", env=dict(checker),
                   mipmap=True, note="small gradient, level 0"),
        vary_probe("vary-mip-s4", tex=TEX, scale="4.0", env=dict(checker),
                   mipmap=True, note="one texel per pixel"),
        vary_probe("vary-mip-s8", tex=TEX, scale="8.0", env=dict(checker),
                   mipmap=True, note="above one texel per pixel: level 1"),
        vary_probe("vary-const-mip", tex=TEX, scale="1.0",
                   p0=("MAD  r0.lh,  u2,  #1,  #0",
                       "MAD  r1.lh,  u2,  #1,  #0"),
                   env=dict(checker), mipmap=True,
                   note="constant coordinates through the same path"),
    ]
    return out


def variants():
    return tie_group() + wrap_group() + vary_group()


CONTROLS = [
    {
        "name": "immediate-operand",
        "vs": VS_MOV,
        "fs": "tests/grate/asm/fs_alu_immediate_control.txt",
        "lnk": LNK_VS,
        "expected": "0x000000FF",
    },
    {
        "name": "varying-pipeline",
        "vs": VS_MOV,
        "fs": "tests/grate/asm/fs_vs_tests.txt",
        "lnk": LNK_VS,
        "expected": "0xFF01FF00",
    },
]


def build_manifest():
    entries = variants()
    names = [entry["name"] for entry in entries]
    if len(names) != len(set(names)):
        raise SystemExit("variant names are not unique")
    return {
        "name": "tex-qual",
        "repeat": 3,
        "timeout": 20,
        "program": {"vs": VS_MOV, "fs": FS_CONST, "lnk": LNK_VS},
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
        print(f"{entry['name']:24} {entry.get('expected', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
