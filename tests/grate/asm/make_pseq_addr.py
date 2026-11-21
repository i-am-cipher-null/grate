#!/usr/bin/env python3
"""Generate the PSEQ addressing characterization manifest.

The earlier PSEQ set stored one word in every pixel, so a fetch that read a
different pixel returned the same word and the addressing could not be seen.
This set tags each pixel with its own window position instead:

    R = frag.x + 1    G = frag.y + 1    B = 0x9F    A = 0xBF

The tag is unique inside a 128-wide quad and differs from pattern B
(0x9F607F40), pattern C (0x10703020) and zero, so the pictures are:

    tag-nofetch   the tags themselves (the reference picture)
    ctl-nofetch   pattern B everywhere (the fetch did not happen)
    ctl-zeros     zero everywhere (the fetch read a zeroed surface)
    tag-fetch     the address map: tag(p) at pixel p means the fetch read p
    last-store    pattern C, stored over the tag after it and before the fetch,
                  so C everywhere means the fetch read the surface rather than
                  returning the tag store or the registers

The template stores the tag to the variant's fetch target and always stores the
fetched word to the linear rt4 surface, which `GRATE_REGION` dumps and digests.
The digest of the rectangle becomes the reported result word, so the whole map
is pinned, not just fb_data[0].

Groups:

  map     the four pictures above plus the destination-pair counterpart;
  src     the same tags read through rt1 (tiled framebuffer), rt3 linear,
          tiled, and tiled with the quad straddling tile boundaries, and rt5 as
          rgba8888, rgb565, a8 and l8, so a surface layout or pitch problem
          shows up as a broken map rather than as a word;
  off     the same quad at x=32,y=16 and at x=1,y=0, to separate absolute
          position from quad-relative position and to move off pixel 0.

The a8 and l8 surfaces keep only one byte, so those variants tag the surviving
channel with x in one variant and y in another. rgb565 keeps bytes 0..2; the
6-bit green recovers y exactly and the 5-bit red recovers x/2.

`--results DIR` pins each variant to the digest its repeats agreed on.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE = "tests/grate/asm/pseq_addr.in"
OUT = "tests/grate/asm_characterize.pseq_addr.json"
VS = "tests/grate/asm/vs_scale_offset.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

STORE4 = "store rt4, r0, r1"
STORE23 = "store rt4, r2, r3"
NOP = "NOP"

ENC_X = "\t\tALU0:\tMAD  r0.l*,  r2,  u19,  u19"
ENC_Y_HI = "\t\tALU1:\tMAD  r0.*h,  r3,  u19,  u19"
ENC_Y_LO = "\t\tALU0:\tMAD  r0.l*,  r3,  u19,  u19"
ENC_X_HI = "\t\tALU3:\tMAD  r1.*h,  r2,  u19,  u19"
ENC_Y_A = "\t\tALU3:\tMAD  r1.*h,  r3,  u19,  u19"
MARK_B = "\t\tALU2:\tMAD  r1.l*,  u3,  #1,  #0"
MARK_A = "\t\tALU3:\tMAD  r1.*h,  u21,  #1,  #0"

TAG = [ENC_X, ENC_Y_HI, MARK_B, MARK_A]
A8_X = ["\t\tALU0:\tMAD  r0.l*,  u3,  #1,  #0",
        "\t\tALU1:\tMAD  r0.*h,  u21,  #1,  #0", MARK_B, ENC_X_HI]
A8_Y = ["\t\tALU0:\tMAD  r0.l*,  u3,  #1,  #0",
        "\t\tALU1:\tMAD  r0.*h,  u21,  #1,  #0", MARK_B, ENC_Y_A]
L8_X = [ENC_X, "\t\tALU1:\tMAD  r0.*h,  u21,  #1,  #0", MARK_B, MARK_A]
L8_Y = [ENC_Y_LO, "\t\tALU1:\tMAD  r0.*h,  u21,  #1,  #0", MARK_B, MARK_A]

# diag-pos reports the position registers themselves: R = posx - 8192,
# G = posy - 8192, which is the tag's x and y before the 2x+1 scaling.

DIAG = ["\t\tALU0:\tMAD  r0.l*,  posx,  u19,  u20",
        "\t\tALU1:\tMAD  r0.*h,  posy,  u19,  u20", MARK_B, MARK_A]

ZERO = ["\t\tALU0:\tMAD  r0.l*,  u17,  #1,  #0",
        "\t\tALU1:\tMAD  r0.*h,  u17,  #1,  #0",
        "\t\tALU2:\tMAD  r1.l*,  u17,  #1,  #0",
        "\t\tALU3:\tMAD  r1.*h,  u17,  #1,  #0"]

B_PATTERN = ["\t\tALU0:\tMAD  r0.l*,  u0,  #1,  #0",
             "\t\tALU1:\tMAD  r0.*h,  u1,  #1,  #0",
             "\t\tALU2:\tMAD  r1.l*,  u2,  #1,  #0",
             "\t\tALU3:\tMAD  r1.*h,  u3,  #1,  #0"]

B_NOPS = [f"\t\tALU{n}:\tNOP" for n in range(4)]

# source -> (batch-0 store, fetch word, environment)
SOURCES = {
    "rt1": ("store rt1, r0, r1", 0x00810008, {}),
    "rt3": ("store rt3, r0, r1", 0x00830008, {"GRATE_COVERAGE": "1"}),
    "rt3tiled": ("store rt3, r0, r1", 0x00830008,
                 {"GRATE_COVERAGE": "1", "GRATE_COVERAGE_TILED": "1"}),
    "rt5": ("store rt5, r0, r1", 0x00850008, {"GRATE_RT5": "1"}),
    "rt5rgb565": ("store rt5, r0, r1", 0x00850008,
                  {"GRATE_RT5": "1", "GRATE_RT5_FMT": "rgb565"}),
    "rt5a8": ("store rt5, r0, r1", 0x00850008,
              {"GRATE_RT5": "1", "GRATE_RT5_FMT": "a8"}),
    "rt5l8": ("store rt5, r0, r1", 0x00850008,
              {"GRATE_RT5": "1", "GRATE_RT5_FMT": "l8"}),
}


def uniform(offset):
    return ["--vs_uniform", '["offset"]=(%r,%r,0.0,0.0)' % offset]


def variant(name, *, enc=TAG, b_pattern=True, pseq=None, source="rt5",
            store3=STORE4, store_c=NOP, region="0,0,64,64", args=None):
    store0, word, src_env = SOURCES[source]
    blines = B_PATTERN if b_pattern else B_NOPS
    entry = {
        "name": name,
        "replace": {
            "fs": {
                "ENC0_SLOT": enc[0],
                "ENC1_SLOT": enc[1],
                "ENC2_SLOT": enc[2],
                "ENC3_SLOT": enc[3],
                "STORE0_SLOT": store0,
                "B0_SLOT": blines[0],
                "B1_SLOT": blines[1],
                "B2_SLOT": blines[2],
                "B3_SLOT": blines[3],
                "PSEQ_SLOT": NOP if pseq is None else f"0x{pseq:08X}",
                "STORE_C_SLOT": store_c,
                "STORE3_SLOT": store3,
            },
        },
        "args": args or uniform((-0.75, -0.75)),
        # rt4 is bound for every variant: it is the observation surface that
        # GRATE_REGION dumps, even when the tags are fetched from elsewhere.
        "environment": dict(src_env, GRATE_RT4="1", GRATE_REGION=region),
    }
    return entry


def variants():
    fetch_rt5 = SOURCES["rt5"][1]
    out = [
        # The position registers themselves: R = posx - 8192, G = posy - 8192.
        variant("diag-pos", enc=DIAG, b_pattern=False),

        # The reference picture and the two dead-fetch pictures.
        variant("tag-nofetch", b_pattern=False),
        variant("ctl-nofetch"),
        variant("ctl-zeros", enc=ZERO, pseq=fetch_rt5),
        variant("tag-fetch", pseq=fetch_rt5),

        # The surface is overwritten with pattern C after the tag store and
        # before the fetch. C everywhere means the fetch read the surface at
        # the fragment's position; the tags would mean it returned the tag
        # store instead; B would mean it did not read memory at all.
        variant("last-store", pseq=fetch_rt5,
                store_c="store rt5, r2, r3"),

        # Destination pair: the fetch lands in r2/r3, and r0/r1 keep B.
        variant("pair-23", pseq=fetch_rt5 | 0x2, store3=STORE23),

        # Source surfaces: framebuffer (tiled), coverage linear and tiled,
        # then rt5 in its four formats.
        variant("src-rt1", source="rt1", pseq=SOURCES["rt1"][1]),
        variant("src-rt3-linear", source="rt3", pseq=SOURCES["rt3"][1]),
        variant("src-rt3-tiled", source="rt3tiled", pseq=SOURCES["rt3"][1]),
        # The same tiled surface with the quad straddling 16x16 tile
        # boundaries, so the intra-tile offset is exercised too.
        variant("src-rt3-tiled-off", source="rt3tiled",
                pseq=SOURCES["rt3"][1], region="8,8,64,64",
                args=uniform((-0.6875, -0.6875))),
        variant("src-rt5-rgb565", source="rt5rgb565", pseq=fetch_rt5),
        variant("src-rt5-a8-x", source="rt5a8", enc=A8_X, pseq=fetch_rt5),
        variant("src-rt5-a8-y", source="rt5a8", enc=A8_Y, pseq=fetch_rt5),
        variant("src-rt5-l8-x", source="rt5l8", enc=L8_X, pseq=fetch_rt5),
        variant("src-rt5-l8-y", source="rt5l8", enc=L8_Y, pseq=fetch_rt5),

        # Absolute position: the same tags with the quad moved. The dump
        # follows the moved window, so a quad-relative address shows up as a
        # shifted map rather than as an empty rectangle.
        variant("off-32-16", pseq=fetch_rt5, region="32,16,64,64",
                args=uniform((-0.5, -0.625))),
        variant("off-1-0", pseq=fetch_rt5, region="1,0,63,64",
                args=uniform((-0.7421875, -0.75))),
    ]
    return out


CONTROLS = [
    {
        "name": "immediate-operand",
        "vs": "tests/grate/asm/vs_mov.txt",
        "fs": "tests/grate/asm/fs_alu_immediate_control.txt",
        "expected": "0x000000FF",
    },
    {
        "name": "varying-pipeline",
        "vs": "tests/grate/asm/vs_mov.txt",
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
        "name": "pseq-addressing",
        "repeat": 3,
        "timeout": 20,
        "program": {
            "vs": VS,
            "fs": TEMPLATE,
            "lnk": LNK,
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
