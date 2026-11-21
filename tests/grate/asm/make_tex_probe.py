#!/usr/bin/env python3
"""Generate the TEX instruction characterization manifest.

One template, `tex_probe.in`: four fp20 coordinate registers, up to two fetches
in separate batches, an ALU packet after the first fetch, and one DW store. A
variant replaces the four coordinate constants, the two TEX words, the ALU
slots and the store, and can add environment (texture size, format, second
texture) and assembler arguments (--mipmap).

Measured so far, on the AC100's AR20:

  * sampler index 0 samples the 4x4 two-tone texture, index 1 samples the
    GRATE_TEX2 surface, and an index with no bound texture reads 1.0 in every
    component (0xFFFFFFFF) rather than failing;
  * clearing bit 10 suppresses the fetch and leaves the destination row
    registers untouched, so the store returns the primes;
  * `t` walks the texture rows (GRATE_TEX_ROWCOLOR gives four distinct rows);
    `s = 1.25` and `s = -0.25` wrap, so the default wrap is repeat;
  * GRATE_TEX_ROWCOLOR's four surface words come back unchanged through the
    fetch and the DW store.

The sweep groups in `variants()`:

  ctl-*    sensitivity controls - nothing below means anything unless these
           read differently from each other;
  en-/idx-/dest/sel-  enable, sampler index, destination pair, source select;
  bit*     the six bits the ISA reference calls unknown, one at a time, on a
           base where a change would show (a sampler-1 fetch, and an explicit
           LOD whose mip is a flat colour);
  wrap-*   coordinates outside [0, 1] on a two-tone and on a row-coloured
           texture;
  size-/pitch-/dxt1-/etc1-  dimension, row pitch and block-compressed
           formats, including four addressable block rows at 16x16;
  lod-*    explicit LOD, bias, their combination, and the same pair at two
           texture sizes where they differ.

`--results DIR` pins the manifest to the single word each variant's repeats
agreed on.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/tex_probe.in"
OUT = "tests/grate/asm_characterize.tex.json"

STORE = "store rt1, r2, r3"
NOP = "NOP"

# SAMPLE, coordinates r0..r3, destination r2/r3, enable + bias.
SAMPLE_BIAS = 0x00001420          # bit 12
SAMPLE_LOD = 0x00002420           # bit 13 only
SAMPLE_BOTH = 0x00003420          # bits 12 and 13
SAMPLE_PLAIN = 0x00000420         # no LOD control

ROW0 = "0x000000FF"
ROW1 = "0x0000FF00"


def variant(name, s="0.5", t="0.5", r="0.0", lod="0.0",
            tex0="0x00001420", tex1=NOP, alu=None, store=STORE,
            args=None, env=None):
    replace = {
        "S_CONST": s, "T_CONST": t, "R_CONST": r, "L_CONST": lod,
        "TEX0_SLOT": tex0, "TEX1_SLOT": tex1,
        "STORE_SLOT": store,
    }
    for index in range(4):
        replace[f"X{index}_SLOT"] = (alu or [NOP] * 4)[index]
    entry = {"name": name, "replace": {"fs": replace}}
    command = ["--texture"] + (args or [])
    entry["args"] = command
    if env:
        entry["environment"] = env
    return entry


def variants():
    out = [
        # Sensitivity controls. ctl-white and ctl-black differ only in s;
        # the row controls differ only in t, and must name different rows.
        variant("ctl-white", s="0.875"),
        variant("ctl-black", s="0.125"),
        variant("ctl-row0", t="0.125", env={"GRATE_TEX_ROWCOLOR": "1"}),
        variant("ctl-row1", t="0.375", env={"GRATE_TEX_ROWCOLOR": "1"}),
        variant("ctl-row3", t="0.875", env={"GRATE_TEX_ROWCOLOR": "1"}),

        # Bit 10 gates the fetch. The destination registers are primed with
        # 0.75 through R and LOD, so a suppressed fetch stores 0.75 in both
        # halves (0x0000F300 after the `.lh` prime) instead of the texel.
        variant("en-off", s="0.125", r="0.75", lod="0.75",
                tex0="0x00001020"),
        variant("en-off-white", s="0.875", r="0.75", lod="0.75",
                tex0="0x00001020"),
        variant("en-on-prime", s="0.125", r="0.75", lod="0.75"),

        # Sampler selection. Index 1 is the GRATE_TEX2 surface (0x000000FF),
        # so sampling it at a black coordinate separates "index selected
        # another texture" from "the fetch happened to be white".
        variant("idx1-white", tex0="0x00001421", env={"GRATE_TEX2": "1"}),
        variant("idx1-black", s="0.125", tex0="0x00001421",
                env={"GRATE_TEX2": "1"}),
        variant("idx2-black", s="0.125", tex0="0x00001422"),
        variant("idx3-black", s="0.125", tex0="0x00001423"),
        variant("idx5-black", s="0.125", tex0="0x00001425"),
        variant("idx15-black", s="0.125", tex0="0x0000142F"),

        # Destination pair (bit 5) and source select (bit 4). With bit 4 set
        # the coordinates come from r2, r3, r0, r1, so s is the R constant
        # (written to r2) and t is the LOD constant (written to r3).
        variant("dest-r0r1", s="0.875", tex0="0x00001400",
                store="store rt1, r0, r1"),
        variant("sel-s", s="0.875", r="0.125", tex0="0x00001430"),
        # With bit 4 set, s comes from r2 and t from r3, so R_CONST is s and
        # L_CONST is t. The pair differs only in bit 4 and must name different
        # rows (row 0 against row 3).
        variant("sel-t", r="0.5", lod="0.125", tex0="0x00001430",
                env={"GRATE_TEX_ROWCOLOR": "1"}),
        variant("sel-t-control", r="0.5", lod="0.125", tex0="0x00001420",
                env={"GRATE_TEX_ROWCOLOR": "1"}),

        # The six "unknown" TEX bits, on the sampler-1 fetch (a bit that
        # changes the sampler or the destination changes 0x000000FF) and on
        # an explicit-LOD fetch (a bit that changes the level changes 0).
    ]
    for bit in (6, 7, 8, 9, 11):
        out.append(variant(f"bit{bit}-idx1", tex0=f"0x{SAMPLE_BIAS | 1 | (1 << bit):08X}",
                           env={"GRATE_TEX2": "1"}))
    for bit in (6, 7, 8, 9, 11):
        out.append(variant(f"bit{bit}-lod1", lod="1.0", args=["--mipmap"],
                           tex0=f"0x{SAMPLE_LOD | (1 << bit):08X}"))

    out += [
        # Coordinates outside [0, 1]. Repeat and clamp give different texels
        # for all four of these; mirrored repeat gives a third answer.
        variant("wrap-s125", s="1.25"),
        variant("wrap-sn025", s="-0.25"),
        variant("wrap-s225", s="2.25"),
        variant("wrap-t125", s="0.5", t="1.25",
                env={"GRATE_TEX_ROWCOLOR": "1"}),
        variant("wrap-tn025", s="0.5", t="-0.25",
                env={"GRATE_TEX_ROWCOLOR": "1"}),

        # Dimension and pitch. A pitch the sampler does not know about
        # shears or wraps rows; GRATE_TEX_PITCH changes the allocation only.
        variant("size8-l0", env={"GRATE_TEX_SIZE": "8x8"}),
        variant("size8-lod1", lod="1.0", tex0=f"0x{SAMPLE_LOD:08X}",
                args=["--mipmap"], env={"GRATE_TEX_SIZE": "8x8"}),
        variant("size64-l0", env={"GRATE_TEX_SIZE": "64x64"}),
        variant("pitch64", s="0.5", t="0.375",
                env={"GRATE_TEX_ROWCOLOR": "1", "GRATE_TEX_PITCH": "64"}),
        variant("pitch32", s="0.5", t="0.375",
                env={"GRATE_TEX_ROWCOLOR": "1", "GRATE_TEX_PITCH": "32"}),

        # Block-compressed surfaces: each 4x4 block row is a solid colour.
        # One block at 4x4 (so t cannot select a block row), then a 16x16
        # surface with four block rows whose colours are blue, green, red and
        # white. DXT1 blocks carry 1-bit alpha, which is why an opaque block
        # comes back with alpha 0xFF even when the stored colour had none.
        variant("dxt1-4-block", t="0.125",
                env={"GRATE_TEX_FORMAT": "dxt1"}),
        variant("dxt1-16-r0", t="0.125",
                env={"GRATE_TEX_FORMAT": "dxt1", "GRATE_TEX_SIZE": "16x16"}),
        variant("dxt1-16-r1", t="0.375",
                env={"GRATE_TEX_FORMAT": "dxt1", "GRATE_TEX_SIZE": "16x16"}),
        variant("dxt1-16-r2", t="0.625",
                env={"GRATE_TEX_FORMAT": "dxt1", "GRATE_TEX_SIZE": "16x16"}),
        variant("dxt1-16-r3", t="0.875",
                env={"GRATE_TEX_FORMAT": "dxt1", "GRATE_TEX_SIZE": "16x16"}),
        variant("etc1-4-block", t="0.125",
                env={"GRATE_TEX_FORMAT": "etc1"}),
        variant("dxt1-zero", s="0.875",
                env={"GRATE_TEX_FORMAT": "dxt1", "GRATE_TEX_ZERO": "1"}),

        # Explicit LOD on the 4x4 chain: level 0 and 1 are white at the
        # centre, level 2 is black and level 3 the flat mip.
        variant("lod0", lod="0.0", tex0=f"0x{SAMPLE_LOD:08X}",
                args=["--mipmap"]),
        variant("lod1", lod="1.0", tex0=f"0x{SAMPLE_LOD:08X}",
                args=["--mipmap"]),
        variant("lod2", lod="2.0", tex0=f"0x{SAMPLE_LOD:08X}",
                args=["--mipmap"]),
        variant("lod3", lod="3.0", tex0=f"0x{SAMPLE_LOD:08X}",
                args=["--mipmap"]),
        variant("lod-half", lod="1.5", tex0=f"0x{SAMPLE_LOD:08X}",
                args=["--mipmap"]),
        variant("lod9", lod="9.0", tex0=f"0x{SAMPLE_LOD:08X}",
                args=["--mipmap"]),
        variant("lod-neg", lod="-1.0", tex0=f"0x{SAMPLE_LOD:08X}",
                args=["--mipmap"]),
        variant("lod-est", lod="2.0", tex0=f"0x{SAMPLE_PLAIN:08X}",
                args=["--mipmap"]),

        # Bias (bit 12) against explicit (bit 13) with the same register, at
        # 4x4 and 16x16. At 16x16 they return different mips, which is what
        # shows the two bits are not interchangeable; the estimate itself is
        # not fitted from three points.
        variant("bias3-4", lod="3.0", tex0=f"0x{SAMPLE_BIAS:08X}",
                args=["--mipmap"]),
        variant("expl3-4", lod="3.0", tex0=f"0x{SAMPLE_LOD:08X}",
                args=["--mipmap"]),
        variant("bias3-16", lod="3.0", tex0=f"0x{SAMPLE_BIAS:08X}",
                args=["--mipmap"], env={"GRATE_TEX_SIZE": "16x16"}),
        variant("expl3-16", lod="3.0", tex0=f"0x{SAMPLE_LOD:08X}",
                args=["--mipmap"], env={"GRATE_TEX_SIZE": "16x16"}),
        variant("both3-16", lod="3.0", tex0=f"0x{SAMPLE_BOTH:08X}",
                args=["--mipmap"], env={"GRATE_TEX_SIZE": "16x16"}),
        variant("bias0-16", lod="0.0", tex0=f"0x{SAMPLE_BIAS:08X}",
                args=["--mipmap"], env={"GRATE_TEX_SIZE": "16x16"}),
        variant("expl0-16", lod="0.0", tex0=f"0x{SAMPLE_LOD:08X}",
                args=["--mipmap"], env={"GRATE_TEX_SIZE": "16x16"}),
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
        "name": "tex-instruction",
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
