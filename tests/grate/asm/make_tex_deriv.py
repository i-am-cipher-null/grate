#!/usr/bin/env python3
"""Generate the derivative-LOD manifest: what sets the mip level of a TEX fetch.

Section 19 showed that the level a fetch uses changes with the coordinate
gradient, but on a chain where only level 0 differed from the rest, so the level
itself could not be named, and across two gradients only.

Readout. `GRATE_TEX_STEPS` builds a surface whose generated chain names its own
level: a one-texel checker split into four vertical bands of decreasing intensity
(0xFF, 0xBF, 0x7F, 0x3F over black). Level 0 is the checker, level 1 is four
flat bands (0x80, 0x60, 0x40, 0x20), level 2 is a coarser structure again. The
`ref-` group pins what each level looks like at a known texel through the
explicit-LOD fetch section 19 established, so the word sets used to classify the
gradient maps are measured, not assumed.

Coordinates. `asm/vs_texcoord_ramp.txt` scales the quad's texcoord by a uniform.
The texel rate was measured first, on a block grid, by counting block
transitions: du/dx = dv/dy = 0.25 * scale texels per pixel on a 64x64 target with
a 64x64 texture, and symmetric in both axes. That is the `rate-` group, and every
later group quotes the texel gradient it implies rather than trusting the factor
in the shader.

Groups:

  rate      a 4x4 block-grid surface at scales 1..16, no chain, so the texel
            rate is counted per axis;
  ref       the explicit-LOD fetch at levels 0..3 on the steps surface, at a
            texel in the first band and one in the third, which names the level
            words;
  axis      equal gradients in both axes at two magnitudes - the cells that
            separate max from norm from sum - with the single-axis
            counterparts at the same rate as controls;
  sweep     one axis, seven magnitudes, to locate the level boundaries and so
            any constant in the LOD expression;
  size      the same scale on 128x128, 64x64 and 32x32;
  oblong    the non-square pair 64x32 and 32x64 with the gradient on one axis at
            a time, which separates a texel-based derivative from a normalized
            one and width from height;
  zero      the section 19 configuration that produced two all-zero rows.

`--results DIR` pins each variant to the word its repeats agreed on.
"""

import argparse
import csv
import json
import pathlib

OUT = "tests/grate/asm_characterize.tex_deriv.json"
FS = "tests/grate/asm/tex_deriv.in"
FS_CONST = "tests/grate/asm/tex_probe.in"
VS = "tests/grate/asm/vs_texcoord_ramp.txt"
LNK = "tests/grate/asm/linker_texcoord.txt"
VS_MOV = "tests/grate/asm/vs_mov.txt"
LNK_VS = "tests/grate/asm/linker_vs_tests.txt"

TEX = 0x00000420
TXL = 0x00002420
STORE = "store rt4, r2, r3"

NEAREST_MIP = "nearest-mipmap-nearest"

STEPS = {"GRATE_TEX_SIZE": "64x64", "GRATE_TEX_STEPS": "1",
         "GRATE_TEX_MIN_FILTER": NEAREST_MIP}
RT4 = {"GRATE_RT4": "1", "GRATE_REGION": "0,0,64,64"}


def tag(value):
    return str(value).replace("-", "m").replace(".", "p")


def probe(name, *, scale, size="64x64", env=None, note="", mipmap=True,
          tex=TEX):
    environment = {"GRATE_TEX_SIZE": size}
    environment.update(RT4)
    if env:
        environment.update(env)

    return {
        "name": name,
        "fs": FS,
        "vs": VS,
        "lnk": LNK,
        "replace": {
            "fs": {
                "P0A_SLOT": "NOP",
                "P0B_SLOT": "NOP",
                "LEVEL": "0.0",
                "TEXWORD": f"0x{tex:08X}",
                "STORE": STORE,
            },
        },
        "args": ["--texture", "--vs_uniform",
                 f'["scale"]=({scale[0]},{scale[1]},1.0,1.0)']
                + (["--mipmap"] if mipmap else []),
        "environment": environment,
        "expected_note": note,
    }


def const_probe(name, *, s, t, lod, env, note=""):
    """Constant coordinates through the section 19 explicit-LOD path."""
    environment = dict(env)
    environment.update(RT4)

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
                "TEX0_SLOT": f"0x{TXL:08X}",
                "TEX1_SLOT": "NOP",
                "X0_SLOT": "NOP",
                "X1_SLOT": "NOP",
                "X2_SLOT": "NOP",
                "X3_SLOT": "NOP",
                "STORE_SLOT": "store rt4, r2, r3",
            },
        },
        "args": ["--texture", "--mipmap"],
        "environment": environment,
        "expected_note": note,
    }


def rate_group():
    grid = {"GRATE_TEX_GRID": "1", "GRATE_TEX_MIN_FILTER": "nearest",
            "GRATE_TEX_MAG_FILTER": "nearest"}
    out = []
    for scale in ("1.0", "2.0", "4.0", "8.0", "16.0"):
        out.append(probe(f"rate-{tag(scale)}", scale=(scale, scale),
                         env=grid, mipmap=False,
                         note="a 4x4 block grid: count the block boundaries"))
    return out


def ref_group():
    # texel (0,0) is in the first band, texel (40,0) in the third; both at
    # s = t, which also keeps the point sample inside the band.
    out = []
    for level in ("0.0", "1.0", "2.0", "3.0"):
        out.append(const_probe(f"ref-band0-l{tag(level)}",
                               s="0.0078125", t="0.0078125", lod=level,
                               env=STEPS,
                               note=f"explicit level {level} in the first band"))
        out.append(const_probe(f"ref-band2-l{tag(level)}",
                               s="0.6328125", t="0.6328125", lod=level,
                               env=STEPS,
                               note=f"explicit level {level} in the third band"))
    return out


def axis_group():
    # du/dx = dv/dy = 0.25 * scale, so 4.595 gives 1.149 texels/px (log2 0.2)
    # and 6.498 gives 1.625 (log2 0.7). Both axes: max says 0 then 1, norm
    # says 1 then 1, sum says 1 then 2, so the two cells separate all three.
    out = []
    for scale in ("4.595", "6.498"):
        out.append(probe(f"axis-both-{tag(scale)}", scale=(scale, scale),
                         env=STEPS, note="equal gradients, both axes"))
        out.append(probe(f"axis-x-{tag(scale)}", scale=(scale, "1.0"),
                         env=STEPS, note="the same gradient on x only"))
        out.append(probe(f"axis-y-{tag(scale)}", scale=("1.0", scale),
                         env=STEPS, note="the same gradient on y only"))
    return out


def sweep_group():
    out = []
    for scale in ("2", "2.83", "4", "5.66", "8", "11.31", "16"):
        out.append(probe(f"sweep-x-{tag(scale)}", scale=(scale, "1.0"),
                         env=STEPS,
                         note="one gradient, x only: which level it selects"))
    out.append(probe("sweep-both-11p31", scale=("11.31", "11.31"), env=STEPS,
                     note="the same rate on both axes"))
    out.append(probe("sweep-both-16", scale=("16", "16"), env=STEPS,
                     note="the steepest gradient on both axes"))
    return out


def size_group():
    out = []
    for size in ("128x128", "64x64", "32x32"):
        out.append(probe(f"size-{size.replace('x', '-')}-s16",
                         scale=("16", "16"), size=size, env=STEPS,
                         note="the same scale, the texture size changed"))
    out.append(probe("size-128x128-s4", scale=("4", "4"), size="128x128",
                     env=STEPS, note="a moderate scale on a 128-wide texture"))
    out.append(probe("size-32x32-s4", scale=("4", "4"), size="32x32",
                     env=STEPS, note="the same scale on a 32-wide texture"))
    return out


def oblong_group():
    # 0.25 * 16 * 64/64 = 4 texels/px along the 64-texel axis, 2 along the
    # 32-texel one, so width and height are separated by which axis ramps.
    out = []
    for size in ("64x32", "32x64"):
        for axis, scale in (("x", ("16", "1")), ("y", ("1", "16"))):
            out.append(probe(f"oblong-{size}-{axis}16", scale=scale, size=size,
                             env=STEPS,
                             note="one axis ramps on a non-square texture"))
    return out


def zero_group():
    checker = {"GRATE_TEX_CHECKER": "1", "GRATE_TEX_MIN_FILTER": NEAREST_MIP}
    return [
        probe("zero-checker-s8", scale=("8", "8"), env=checker,
              note="section 19's configuration, whose rows 0 and 32 read zero"),
        probe("zero-steps-s8", scale=("8", "8"), env=STEPS,
              note="the same scale on the self-labelling surface"),
        probe("zero-steps-s8-y1", scale=("8", "1"), env=STEPS,
              note="the same x gradient with a flat y, to place the zero rows"),
    ]


def variants():
    return (rate_group() + ref_group() + axis_group() + sweep_group()
            + size_group() + oblong_group() + zero_group())


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
        "name": "tex-deriv",
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
        print(f"{entry['name']:26} {entry.get('expected', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
