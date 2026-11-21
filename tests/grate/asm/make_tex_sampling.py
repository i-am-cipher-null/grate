#!/usr/bin/env python3
"""Generate the TEX destination and sampling characterization manifest.

One template, `asm/tex_probe.in`: four fp20 coordinate registers, up to two
fetches in separate batches, an ALU packet after the first fetch, and one DW
store. A variant replaces the four coordinate constants, the two TEX words, the
ALU slots and the store, and can add environment (texture size, format,
filters) and assembler arguments (--mipmap).

Coordinates are constants, not a varying. An interpolated varying does not
reach the fetch in this harness: with `vs_texcoord_quad.txt`, its probe
shaders, `vs_mov.txt`, and `filter_quad_vs.txt` driven by an identity mvp, the
stored r0/r1 is zero and nine different s values sample the same texel. A fetch
placed in the first exec, where the linked varying is visible, returns nothing
either. Derivative-based LOD selection therefore cannot be measured here and is
not in this set.

Groups:

  chan    each single-channel texel and the four-byte word, through r0/r1 and
          through r2/r3, with the same coordinates in both;
  mag     a point and a linear magnification filter over the same coordinates,
          so the filter's coordinate convention and footprint are named;
  row     the same question along t on a four-colour row texture;
  level   the content of each mip level at one fixed point, read with nearest
          level selection;
  mip     explicit fractional LOD with nearest and with blending mip filters;
  bias    the same fractions through the bias register;
  txl     the explicit-LOD fraction above and below 1, against the bias curve.

`GRATE_TEX_CHANNELS` paints one channel per texel in row 0 and a word with four
distinct bytes in row 1. `GRATE_TEX_MIN_FILTER` / `GRATE_TEX_MAG_FILTER` select
the sampler filters by name.

`--results DIR` pins each variant to the word its repeats agreed on.
"""

import argparse
import csv
import json
import pathlib

OUT = "tests/grate/asm_characterize.tex_sampling.json"

VS_MOV = "tests/grate/asm/vs_mov.txt"
LNK_VS = "tests/grate/asm/linker_vs_tests.txt"
FS_CONST = "tests/grate/asm/tex_probe.in"

NOP = "NOP"
STORE = "store rt1, r2, r3"
STORE01 = "store rt1, r0, r1"

# TEX word: sampler 0, destination pair r2/r3, enable.
TEX = 0x00000420
# Bit 12: the register is a LOD bias.
TXB = 0x00001420
# Bit 13: the register is an absolute LOD.
TXL = 0x00002420


def tag(value):
    return value.replace(".", "p")


def probe(name, *, s, t, r="0.0", lod="0.0", tex=TEX, store=STORE,
          env=None, mipmap=False):
    return {
        "name": name,
        "fs": FS_CONST,
        "vs": VS_MOV,
        "lnk": LNK_VS,
        "replace": {
            "fs": {
                "S_CONST": s,
                "T_CONST": t,
                "R_CONST": r,
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
        "environment": env or {},
    }


CHANNELS = (
    ("a", "0.125"),
    ("b", "0.375"),
    ("g", "0.625"),
    ("r", "0.875"),
)

LINEAR = {"GRATE_TEX_MAG_FILTER": "linear"}

# A 4x4 four-colour row texture: row k is row_color[k & 3].
ROWCOLOR = {"GRATE_TEX_ROWCOLOR": "1"}

# Level 0 is white at an even row, level 1 and above are the flat average.
ROWCHECK = {"GRATE_TEX_ROWCHECK": "1"}


def variants():
    out = []

    # Channel order: the same texel through both destination pairs. Row 0
    # holds one channel per texel, row 1 the word 0x11223344.
    channels = {"GRATE_TEX_CHANNELS": "1"}
    # Texel centres of the 4x4 surface under the filter convention measured
    # below: texel i is centred at s = (i + 0.5) / 4.
    for name, s in CHANNELS:
        out.append(probe(f"chan-{name}-23", s=s, t="0.125", tex=TEX,
                         env=channels))
        out.append(probe(f"chan-{name}-01", s=s, t="0.125",
                         tex=TEX & ~0x20, store=STORE01, env=channels))
    out.append(probe("chan-mix-23", s="0.125", t="0.375", tex=TEX,
                     env=channels))
    out.append(probe("chan-mix-01", s="0.125", t="0.375",
                     tex=TEX & ~0x20, store=STORE01, env=channels))

    # Magnification along s on the two-tone surface: black in texels 0 and 1,
    # white in 2 and 3. A solid texel reads the same through both filters, the
    # boundary between texels 1 and 2 blends, and which coordinate is which
    # names the filter's half-texel convention.
    for s in ("0.0625", "0.125", "0.1875", "0.25", "0.3125", "0.375",
              "0.5", "0.625", "0.875"):
        out.append(probe(f"magpoint-{tag(s)}", s=s, t="0.5"))
        out.append(probe(f"magline-{tag(s)}", s=s, t="0.5", env=LINEAR))

    # The same along t, where four distinct row colours make an off-by-half
    # blend visible instead of a black/white average.
    for t in ("0.0625", "0.125", "0.1875", "0.25", "0.375", "0.625"):
        out.append(probe(f"magrow-{tag(t)}", s="0.125", t=t,
                         env=dict(ROWCOLOR, **LINEAR)))

    # The contents of each mip level at one fixed point, read with nearest
    # level selection, on both 4x4 surfaces. These name the words the blends
    # below are made of instead of inferring them.
    for surface, label in ((ROWCHECK, "check"), (ROWCOLOR, "color"),
                           ({}, "tone")):
        for lod in ("0.0", "1.0", "2.0"):
            out.append(probe(f"level-{label}-{tag(lod)}", s="0.125",
                             t="0.125", tex=TXL, lod=lod, mipmap=True,
                             env=dict(surface, **{
                                 "GRATE_TEX_MIN_FILTER":
                                 "nearest-mipmap-nearest"})))

    # Mip level blending: the same fractional LOD through the nearest and the
    # blending mip filter, on a 0/1 pair (ROWCHECK) and on a 1/2 pair
    # (two-tone).
    row_blend = dict(ROWCHECK, **{"GRATE_TEX_MIN_FILTER":
                                  "nearest-mipmap-linear"})
    row_near = dict(ROWCHECK, **{"GRATE_TEX_MIN_FILTER":
                                 "nearest-mipmap-nearest"})
    out += [
        probe("mip-lin-00", s="0.125", t="0.125", tex=TXL, lod="0.0",
              mipmap=True, env=row_blend),
        probe("mip-lin-10", s="0.125", t="0.125", tex=TXL, lod="1.0",
              mipmap=True, env=row_blend),
        probe("mip-near-05", s="0.125", t="0.125", tex=TXL, lod="0.5",
              mipmap=True, env=row_near),
        probe("mip-lin-05", s="0.125", t="0.125", tex=TXL, lod="0.5",
              mipmap=True, env=row_blend),
        probe("mip-near-15", s="0.125", t="0.125", tex=TXL, lod="1.5",
              mipmap=True, env={"GRATE_TEX_MIN_FILTER":
                                "nearest-mipmap-nearest"}),
        probe("mip-lin-15", s="0.125", t="0.125", tex=TXL, lod="1.5",
              mipmap=True, env={"GRATE_TEX_MIN_FILTER":
                                "nearest-mipmap-linear"}),
        probe("mip-lin-20", s="0.125", t="0.125", tex=TXL, lod="2.0",
              mipmap=True, env={"GRATE_TEX_MIN_FILTER":
                                "nearest-mipmap-linear"}),
    ]

    # Fractional bias, through the same blending filter: if the register is
    # added to the estimate as a fraction, each quarter step moves the word by
    # a quarter of the level difference.
    out += [
        probe("bias-000", s="0.125", t="0.125", tex=TXB, lod="0.0",
              mipmap=True, env=row_blend),
        probe("bias-025", s="0.125", t="0.125", tex=TXB, lod="0.25",
              mipmap=True, env=row_blend),
        probe("bias-050", s="0.125", t="0.125", tex=TXB, lod="0.5",
              mipmap=True, env=row_blend),
        probe("bias-100", s="0.125", t="0.125", tex=TXB, lod="1.0",
              mipmap=True, env={"GRATE_TEX_MIN_FILTER":
                                "nearest-mipmap-linear"}),
        probe("bias-125", s="0.125", t="0.125", tex=TXB, lod="1.25",
              mipmap=True, env={"GRATE_TEX_MIN_FILTER":
                                "nearest-mipmap-linear"}),
        probe("bias-150", s="0.125", t="0.125", tex=TXB, lod="1.5",
              mipmap=True, env={"GRATE_TEX_MIN_FILTER":
                                "nearest-mipmap-linear"}),
        probe("bias-175", s="0.125", t="0.125", tex=TXB, lod="1.75",
              mipmap=True, env={"GRATE_TEX_MIN_FILTER":
                                "nearest-mipmap-linear"}),
        # The same bias ladder on the opaque row texture, where level 0 and
        # level 1 differ (0xFF against 0x80) at every coordinate: if the bias
        # curve here sits a level above the two-tone's, the two surfaces are
        # reading different levels for the same register.
        probe("bias-check-100", s="0.125", t="0.125", tex=TXB, lod="1.0",
              mipmap=True, env=row_blend),
        probe("bias-check-125", s="0.125", t="0.125", tex=TXB, lod="1.25",
              mipmap=True, env=row_blend),
        probe("bias-check-150", s="0.125", t="0.125", tex=TXB, lod="1.5",
              mipmap=True, env=row_blend),
        probe("bias-check-175", s="0.125", t="0.125", tex=TXB, lod="1.75",
              mipmap=True, env=row_blend),
    ]

    # The two-tone's level 1 across the surface: constant means the level the
    # sampler reads is not a 2x2 of the black and white halves.
    for s in ("0.125", "0.375", "0.625", "0.875"):
        out.append(probe(f"tone-1p0-{tag(s)}", s=s, t="0.125", tex=TXL,
                         lod="1.0", mipmap=True,
                         env={"GRATE_TEX_MIN_FILTER":
                              "nearest-mipmap-nearest"}))

    # The explicit-LOD fraction, above and below 1, against the bias curve.
    for lod in ("0.25", "0.5", "0.75", "1.0", "1.125", "1.25", "1.375",
                "1.5", "1.625", "1.75", "2.0"):
        out.append(probe(f"txl-{tag(lod)}", s="0.125", t="0.125", tex=TXL,
                         lod=lod, mipmap=True,
                         env={"GRATE_TEX_MIN_FILTER":
                              "nearest-mipmap-linear"}))
    return out


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
        "name": "tex-sampling",
        "repeat": 3,
        "timeout": 20,
        "program": {
            "vs": VS_MOV,
            "fs": FS_CONST,
            "lnk": LNK_VS,
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
