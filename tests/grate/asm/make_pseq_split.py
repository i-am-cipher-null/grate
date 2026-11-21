#!/usr/bin/env python3
"""Generate the PSEQ separate-draw characterization manifest.

The addressing set (section 14) wrote the tag and fetched it back in the same
draw, so every fetch could have read the value the very same fragment had just
stored. This set separates the two operations:

    draw A   asm/pseq_split_a.in   tag(x, y) -> the source surface, no fetch
    draw B   asm/pseq_split_b.in   fetch the source, store to rt4, no store to
                                   the source

Both draws are the same full-screen quad over a genuine 64x64 target
(GRATE_SIZE=64), so the whole surface is written by A and the whole surface is
observed through rt4, whose 64x64 region the runner digests.

The tag is the same one the same-draw set uses and stores as

    0xBF9F <y+1> <x+1>

so a fetched tag names the pixel it came from and every pixel's expected value
is known exactly: word(x, y) must be 0xBF9F0000 | ((y+1) << 8) | (x+1).

`tests/grate/asm/split/` holds the concrete extra programs; `--write-extras`
regenerates them from the two templates. They exist because the runner can
substitute placeholders in the main program only, while the second and third
draws have to be files on disk.

The competing explanations for the fetch address:

  position   the fragment's own window position in draw B, then the map is the
             identity and does not depend on what draw A did last;
  dw-state   the address the DW last used in draw A, then the map is one
             repeated word or a picture anchored at draw A's last pixel;
  surface    memory addressed some other way (wrong pitch, wrong tile), then
             the map is shifted or scrambled rather than identity.

Variants:

  same-draw            the section 14 reference: store and fetch in one draw;
  two-draw             draw A then draw B, the case this set is about;
  two-draw-nofetch     same, with the fetch replaced by NOP inside draw B, so
                       the observation surface shows the preloaded pattern B;
  drawb-only           draw B alone, so the source was never written and reads
                       back as whatever the allocation held;
  changed              pattern C written between draw A and draw B, so a fetch
                       that returns C read memory and one that returns tags
                       kept the earlier contents;
  shift-a              draw A writes tag(x+16, y); an identity fetch then
                       returns that shifted picture, while an address carried
                       over from draw A's last store cannot produce it;
  pair-23              the fetch lands in r2/r3 and rt4 is stored from r2/r3;
  src-rt1              the source is the tiled framebuffer;
  src-rt3-linear       the source is linear rt3;
  src-rt3-tiled        the source is tiled rt3;
  src-rt5-rgb565       the source is a 16-bit rgb565 surface;
  pitch-pad-16         the source is allocated 16 texels wider than the target
                       (GRATE_PITCH_PAD), so a fetch that assumes the unpadded
                       pitch walks sideways.

`--results DIR` pins each variant to the digest its repeats agreed on.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE_A = "tests/grate/asm/pseq_split_a.in"
TEMPLATE_B = "tests/grate/asm/pseq_split_b.in"
TEMPLATE_C = "tests/grate/asm/pseq_split_c.in"
SAME_DRAW = "tests/grate/asm/pseq_addr.in"
EXTRA_DIR = "tests/grate/asm/split"
OUT = "tests/grate/asm_characterize.pseq_split.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE4 = "store rt4, r0, r1"
STORE23 = "store rt4, r2, r3"
REGION = "0,0,64,64"

TAG = [
    "\t\tALU0:\tMAD  r0.l*,  r2,  u19,  u19",
    "\t\tALU1:\tMAD  r0.*h,  r3,  u19,  u19",
    "\t\tALU2:\tMAD  r1.l*,  u3,  #1,  #0",
    "\t\tALU3:\tMAD  r1.*h,  u21,  #1,  #0",
]
# The same tag with x moved 16 columns right: u22 = 17/256.
TAG_SHIFT = [
    "\t\tALU0:\tMAD  r0.l*,  r2,  u19,  u22",
    "\t\tALU1:\tMAD  r0.*h,  r3,  u19,  u19",
    "\t\tALU2:\tMAD  r1.l*,  u3,  #1,  #0",
    "\t\tALU3:\tMAD  r1.*h,  u21,  #1,  #0",
]
PATTERN_B = [
    "\t\tALU0:\tMAD  r0.l*,  u0,  #1,  #0",
    "\t\tALU1:\tMAD  r0.*h,  u1,  #1,  #0",
    "\t\tALU2:\tMAD  r1.l*,  u2,  #1,  #0",
    "\t\tALU3:\tMAD  r1.*h,  u3,  #1,  #0",
]

FETCH_RT1 = 0x00810008
FETCH_RT3 = 0x00830008
FETCH_RT5 = 0x00850008


def substitute(path, mapping):
    text = pathlib.Path(path).read_text()
    for key, value in mapping.items():
        if text.count(key) != 1:
            raise SystemExit(f"{path}: {key} occurs {text.count(key)} times")
        text = text.replace(key, value, 1)
    return text


def draw_a(store, enc=TAG):
    return substitute(TEMPLATE_A, {
        "ENC0_SLOT": enc[0], "ENC1_SLOT": enc[1],
        "ENC2_SLOT": enc[2], "ENC3_SLOT": enc[3],
        "STORE_A_SLOT": store,
    })


def draw_b(fetch, store):
    return substitute(TEMPLATE_B, {
        "B0_SLOT": PATTERN_B[0], "B1_SLOT": PATTERN_B[1],
        "B2_SLOT": PATTERN_B[2], "B3_SLOT": PATTERN_B[3],
        "FETCH_WORD": f"0x{fetch:08X}" if isinstance(fetch, int) else fetch,
        "STORE_B_SLOT": store,
    })


def draw_c():
    return substitute(TEMPLATE_C, {"STORE_A_SLOT": "store rt5, r0, r1"})


def same_draw_replacement(fetch, store):
    return {
        "ENC0_SLOT": TAG[0], "ENC1_SLOT": TAG[1],
        "ENC2_SLOT": TAG[2], "ENC3_SLOT": TAG[3],
        "STORE0_SLOT": "store rt5, r0, r1",
        "B0_SLOT": PATTERN_B[0], "B1_SLOT": PATTERN_B[1],
        "B2_SLOT": PATTERN_B[2], "B3_SLOT": PATTERN_B[3],
        "PSEQ_SLOT": f"0x{fetch:08X}",
        "STORE_C_SLOT": NOP,
        "STORE3_SLOT": store,
    }


HEADER = ("/* Generated by tests/grate/asm/make_pseq_split.py from {}.\n"
          " * The placeholders are already substituted, because extra draws are\n"
          " * given to the assembler as files and cannot be rewritten by the\n"
          " * runner. */\n\n")

# name -> (template it came from, text)
EXTRAS = {
    "b-rt1.in": (TEMPLATE_B, lambda: draw_b(FETCH_RT1, STORE4)),
    "b-rt3.in": (TEMPLATE_B, lambda: draw_b(FETCH_RT3, STORE4)),
    "b-rt5.in": (TEMPLATE_B, lambda: draw_b(FETCH_RT5, STORE4)),
    "b-rt5-pair23.in": (TEMPLATE_B, lambda: draw_b(FETCH_RT5 | 0x2, STORE23)),
    "b-rt5-nofetch.in": (TEMPLATE_B, lambda: draw_b(NOP, STORE4)),
    "c-rt5.in": (TEMPLATE_C, draw_c),
}


def write_extras(root):
    out = pathlib.Path(root) / EXTRA_DIR
    out.mkdir(parents=True, exist_ok=True)
    for name, (template, build) in EXTRAS.items():
        text = HEADER.format(pathlib.Path(template).name) + build()
        (out / name).write_text(text)
        print(f"wrote {EXTRA_DIR}/{name}")


def variant(name, main_fs, main_replace, extras, environment_extra,
            expected_note):
    env = {
        "GRATE_SIZE": "64",
        "GRATE_RT4": "1",
        "GRATE_REGION": REGION,
    }
    env.update(environment_extra)
    if extras:
        env["GRATE_EXTRA_FS"] = ":".join(f"{EXTRA_DIR}/{name}"
                                         for name in extras)
    return {
        "name": name,
        "fs": main_fs,
        "replace": {"fs": main_replace},
        "environment": env,
        "expected_note": expected_note,
    }


def main_replace_for(store, enc=TAG):
    return {
        "ENC0_SLOT": enc[0], "ENC1_SLOT": enc[1],
        "ENC2_SLOT": enc[2], "ENC3_SLOT": enc[3],
        "STORE_A_SLOT": store,
    }


A_RT5 = main_replace_for("store rt5, r0, r1")
A_RT5_SHIFT = main_replace_for("store rt5, r0, r1", TAG_SHIFT)


def variants():
    out = []

    # The same-draw reference: the section 14 picture, at 64x64.
    out.append({
        "name": "same-draw",
        "fs": SAME_DRAW,
        "replace": {"fs": same_draw_replacement(FETCH_RT5, STORE4)},
        "environment": {"GRATE_SIZE": "64", "GRATE_RT4": "1",
                        "GRATE_RT5": "1", "GRATE_REGION": REGION},
        "expected_note": "identity map, the same picture as two-draw",
    })

    out.append(variant("two-draw", TEMPLATE_A, A_RT5, ["b-rt5.in"],
                       {"GRATE_RT5": "1"},
                       "identity map: draw B fetches the tags draw A wrote"))
    out.append(variant("two-draw-nofetch", TEMPLATE_A, A_RT5,
                       ["b-rt5-nofetch.in"], {"GRATE_RT5": "1"},
                       "pattern B everywhere, the fetch did not happen"))
    out.append(variant("drawb-only", TEMPLATE_B,
                       {"FETCH_WORD": f"0x{FETCH_RT5:08X}",
                        "STORE_B_SLOT": STORE4,
                        "B0_SLOT": PATTERN_B[0], "B1_SLOT": PATTERN_B[1],
                        "B2_SLOT": PATTERN_B[2], "B3_SLOT": PATTERN_B[3]},
                       [], {"GRATE_RT5": "1"},
                       "zero map: the source was never written"))
    out.append(variant("changed", TEMPLATE_A, A_RT5,
                       ["c-rt5.in", "b-rt5.in"], {"GRATE_RT5": "1"},
                       "pattern C everywhere: the fetch read the current surface"))
    out.append(variant("shift-a", TEMPLATE_A, A_RT5_SHIFT, ["b-rt5.in"],
                       {"GRATE_RT5": "1"},
                       "tag(x+16, y) at pixel (x, y): the fetch is position based"))
    out.append(variant("pair-23", TEMPLATE_A, A_RT5, ["b-rt5-pair23.in"],
                       {"GRATE_RT5": "1"},
                       "the same map through the r2/r3 destination pair"))
    out.append(variant("src-rt1", TEMPLATE_A,
                       main_replace_for("store rt1, r0, r1"), ["b-rt1.in"],
                       {}, "identity map through the tiled framebuffer"))
    out.append(variant("src-rt3-linear", TEMPLATE_A,
                       main_replace_for("store rt3, r0, r1"), ["b-rt3.in"],
                       {"GRATE_COVERAGE": "1"},
                       "identity map through linear rt3"))
    out.append(variant("src-rt3-tiled", TEMPLATE_A,
                       main_replace_for("store rt3, r0, r1"), ["b-rt3.in"],
                       {"GRATE_COVERAGE": "1", "GRATE_COVERAGE_TILED": "1"},
                       "identity map through tiled rt3"))
    out.append(variant("src-rt5-rgb565", TEMPLATE_A, A_RT5, ["b-rt5.in"],
                       {"GRATE_RT5": "1", "GRATE_RT5_FMT": "rgb565"},
                       "16-bit source: the tag's coordinate bytes are truncated"))
    out.append(variant("pitch-pad-16", TEMPLATE_A, A_RT5, ["b-rt5.in"],
                       {"GRATE_RT5": "1", "GRATE_PITCH_PAD": "16"},
                       "identity map through a source 16 texels wider than the target"))
    return out


CONTROLS = [
    {
        "name": "immediate-operand",
        "vs": "tests/grate/asm/vs_mov.txt",
        "fs": "tests/grate/asm/fs_alu_immediate_control.txt",
        "lnk": "tests/grate/asm/linker_vs_tests.txt",
        "expected": "0x000000FF",
    },
    {
        "name": "varying-pipeline",
        "vs": "tests/grate/asm/vs_mov.txt",
        "fs": "tests/grate/asm/fs_vs_tests.txt",
        "lnk": "tests/grate/asm/linker_vs_tests.txt",
        "expected": "0xFF01FF00",
    },
]


def build_manifest():
    entries = variants()
    names = [entry["name"] for entry in entries]
    if len(names) != len(set(names)):
        raise SystemExit("variant names are not unique")
    for entry in entries:
        entry.setdefault("fs", TEMPLATE_A)
    return {
        "name": "pseq-split",
        "repeat": 3,
        "timeout": 20,
        "program": {"vs": VS, "fs": TEMPLATE_A, "lnk": LNK},
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
    parser.add_argument("--write-extras", action="store_true",
                        help="write the concrete extra-draw programs")
    args = parser.parse_args()

    root = pathlib.Path(__file__).resolve().parents[3]
    if args.write_extras:
        write_extras(root)

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
