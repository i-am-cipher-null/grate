#!/usr/bin/env python3
"""Generate the PSEQ visibility manifest: the flush boundary, write ordering and
a padded tiled source.

Section 17 established that a fetch in a later draw reads the surface the earlier
draw wrote, at the fetching fragment's own pixel, through linear and tiled
targets and a pitch-padded linear one. Four things were left open, and this set
asks them one at a time:

  flush     the harness flushes after every draw, so nothing was said about a
            fetch that runs before the earlier draw's writes are flushed.
            `GRATE_EXTRA_BEFORE_FLUSH` issues the extra draws before that flush,
            so both draws share one submission and no explicit flush separates
            them. The fetched word is either the tag (visible anyway) or pattern
            B (the fetch found nothing), and the no-fetch control separates
            "nothing visible" from "no fetch issued";
  order     a draw B that also writes the source, on either side of its own
            fetch, which the split set could not do because draw B wrote only
            to rt4. `asm/pseq_split_bw.in` stores to the source before the
            fetch in one variant and after it in another;
  padded    a *tiled* source allocated wider than the target. Section 17 padded
            only the linear rt5 source; `GRATE_PITCH_PAD` now applies to rt3 as
            well, so the tiled case can be built;
  changed   the pattern C write between the two draws, with and without a flush,
            so the same question is asked of data written by a middle draw.

The source and the observation surface stay separate everywhere (draw A writes
rt5 or rt3, draw B observes through rt4), so a word in rt4 is always a fetched
word and never a local store.

Note on the address-source question of section 17: the only way the two
candidates (the position registers and the fragment's own target coordinate)
could disagree is if a fetch could be given an offset, and the fetch word has no
offset field in this harness. What can be varied - the surface's base (through
the index field), its pitch and its tile layout - is varied here and in section
17; the pixel coordinate itself cannot be made to differ from the store's.

Variants:

  flush-two-draw        draw A then draw B with the harness's flush between;
  flush-nofetch         the same with the fetch replaced by NOP;
  noflush-two-draw      the same with no flush between them;
  noflush-nofetch       its control;
  flush-changed         A, then C, then B, each flushed;
  noflush-changed       A, then C, then B, one submission;
  prewrite              draw B stores pattern B to the source, then fetches;
  postwrite             draw B fetches, then stores pattern B to the source;
  tiled-pad             the source is tiled rt3 allocated 16 texels wider;
  tiled-pad-shift       the same with draw A's tag moved 16 columns right;
  tiled-pad-nofetch     its control.

`--results DIR` pins each variant to the digest its repeats agreed on, and
`--write-extras` regenerates the extra-draw files under asm/vis/.
"""

import argparse
import csv
import json
import pathlib

TEMPLATE_A = "tests/grate/asm/pseq_split_a.in"
TEMPLATE_B = "tests/grate/asm/pseq_split_b.in"
TEMPLATE_BW = "tests/grate/asm/pseq_split_bw.in"
TEMPLATE_C = "tests/grate/asm/pseq_split_c.in"
EXTRA_DIR = "tests/grate/asm/vis"
OUT = "tests/grate/asm_characterize.pseq_vis.json"
VS = "tests/grate/asm/vs_mov.txt"
LNK = "tests/grate/asm/linker_vs_tests.txt"

NOP = "NOP"
STORE4 = "store rt4, r0, r1"
REGION = "0,0,64,64"

TAG = [
    "\t\tALU0:\tMAD  r0.l*,  r2,  u19,  u19",
    "\t\tALU1:\tMAD  r0.*h,  r3,  u19,  u19",
    "\t\tALU2:\tMAD  r1.l*,  u3,  #1,  #0",
    "\t\tALU3:\tMAD  r1.*h,  u21,  #1,  #0",
]
# the same tag with x moved 16 columns right: u22 = 17/256
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


def draw_b(fetch, store=STORE4):
    return substitute(TEMPLATE_B, {
        "B0_SLOT": PATTERN_B[0], "B1_SLOT": PATTERN_B[1],
        "B2_SLOT": PATTERN_B[2], "B3_SLOT": PATTERN_B[3],
        "FETCH_WORD": f"0x{fetch:08X}" if isinstance(fetch, int) else fetch,
        "STORE_B_SLOT": store,
    })


def draw_bw(fetch, pre=NOP, post=NOP):
    return substitute(TEMPLATE_BW, {
        "B0_SLOT": PATTERN_B[0], "B1_SLOT": PATTERN_B[1],
        "B2_SLOT": PATTERN_B[2], "B3_SLOT": PATTERN_B[3],
        "PRE_STORE_SLOT": pre,
        "FETCH_WORD": f"0x{fetch:08X}" if isinstance(fetch, int) else fetch,
        "POST_STORE_SLOT": post,
        "STORE_B_SLOT": STORE4,
    })


def draw_c():
    return substitute(TEMPLATE_C, {"STORE_A_SLOT": "store rt5, r0, r1"})


HEADER = ("/* Generated by tests/grate/asm/make_pseq_vis.py from {}.\n"
          " * The placeholders are already substituted, because extra draws are\n"
          " * given to the assembler as files and cannot be rewritten by the\n"
          " * runner. */\n\n")

EXTRAS = {
    "b-rt5.in": (TEMPLATE_B, lambda: draw_b(FETCH_RT5)),
    "b-rt5-nofetch.in": (TEMPLATE_B, lambda: draw_b(NOP)),
    "b-rt3-nofetch.in": (TEMPLATE_B, lambda: draw_b(NOP)),
    "b-rt3.in": (TEMPLATE_B, lambda: draw_b(FETCH_RT3)),
    "bw-pre-rt5.in": (TEMPLATE_BW,
                      lambda: draw_bw(FETCH_RT5, pre="store rt5, r0, r1")),
    "bw-post-rt5.in": (TEMPLATE_BW,
                       lambda: draw_bw(FETCH_RT5, post="store rt5, r0, r1")),
    "c-rt5.in": (TEMPLATE_C, draw_c),
}


def write_extras(root):
    out = pathlib.Path(root) / EXTRA_DIR
    out.mkdir(parents=True, exist_ok=True)
    for name, (template, build) in EXTRAS.items():
        (out / name).write_text(HEADER.format(pathlib.Path(template).name)
                                + build())
        print(f"wrote {EXTRA_DIR}/{name}")


def variant(name, main_fs, replace, extras, env_extra, note):
    env = {"GRATE_SIZE": "64", "GRATE_RT4": "1", "GRATE_REGION": REGION}
    env.update(env_extra)
    if extras:
        env["GRATE_EXTRA_FS"] = ":".join(f"{EXTRA_DIR}/{n}" for n in extras)
    return {
        "name": name,
        "fs": main_fs,
        "replace": {"fs": replace},
        "environment": env,
        "expected_note": note,
    }


def a_rt5(enc=TAG, store="store rt5, r0, r1"):
    return {"ENC0_SLOT": enc[0], "ENC1_SLOT": enc[1],
            "ENC2_SLOT": enc[2], "ENC3_SLOT": enc[3],
            "STORE_A_SLOT": store}


def a_rt3(enc=TAG):
    return a_rt5(enc, store="store rt3, r0, r1")


def variants():
    out = []
    out.append(variant("flush-two-draw", TEMPLATE_A, a_rt5(),
                       ["b-rt5.in"], {"GRATE_RT5": "1"},
                       "draw A flushed before draw B: the identity map"))
    out.append(variant("flush-nofetch", TEMPLATE_A, a_rt5(),
                       ["b-rt5-nofetch.in"], {"GRATE_RT5": "1"},
                       "its control: pattern B everywhere"))
    out.append(variant("noflush-two-draw", TEMPLATE_A, a_rt5(),
                       ["b-rt5.in"],
                       {"GRATE_RT5": "1", "GRATE_EXTRA_BEFORE_FLUSH": "1"},
                       "no flush between the draws, one submission"))
    out.append(variant("noflush-nofetch", TEMPLATE_A, a_rt5(),
                       ["b-rt5-nofetch.in"],
                       {"GRATE_RT5": "1", "GRATE_EXTRA_BEFORE_FLUSH": "1"},
                       "its control"))
    out.append(variant("flush-changed", TEMPLATE_A, a_rt5(),
                       ["c-rt5.in", "b-rt5.in"], {"GRATE_RT5": "1"},
                       "A, then C, then B, each flushed"))
    out.append(variant("noflush-changed", TEMPLATE_A, a_rt5(),
                       ["c-rt5.in", "b-rt5.in"],
                       {"GRATE_RT5": "1", "GRATE_EXTRA_BEFORE_FLUSH": "1"},
                       "A, then C, then B, one submission"))
    out.append(variant("prewrite", TEMPLATE_A, a_rt5(),
                       ["bw-pre-rt5.in"], {"GRATE_RT5": "1"},
                       "draw B stores B to the source, then fetches"))
    out.append(variant("postwrite", TEMPLATE_A, a_rt5(),
                       ["bw-post-rt5.in"], {"GRATE_RT5": "1"},
                       "draw B fetches, then stores B to the source"))
    out.append(variant("tiled-pad", TEMPLATE_A, a_rt3(),
                       ["b-rt3.in"],
                       {"GRATE_COVERAGE": "1", "GRATE_COVERAGE_TILED": "1",
                        "GRATE_PITCH_PAD": "16"},
                       "tiled source allocated 16 texels wider"))
    out.append(variant("tiled-pad-shift", TEMPLATE_A, a_rt3(TAG_SHIFT),
                       ["b-rt3.in"],
                       {"GRATE_COVERAGE": "1", "GRATE_COVERAGE_TILED": "1",
                        "GRATE_PITCH_PAD": "16"},
                       "the same, draw A shifted 16 columns"))
    out.append(variant("tiled-pad-nofetch", TEMPLATE_A, a_rt3(),
                       ["b-rt3-nofetch.in"],
                       {"GRATE_COVERAGE": "1", "GRATE_COVERAGE_TILED": "1",
                        "GRATE_PITCH_PAD": "16"},
                       "its control"))
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
        "name": "pseq-vis",
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
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", help="run directory to pin from")
    parser.add_argument("--out", help="manifest to write (default: the set's)")
    parser.add_argument("--write-extras", action="store_true",
                        help="regenerate the extra-draw files")
    args = parser.parse_args()

    if args.write_extras:
        write_extras(".")

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
        print(f"{entry['name']:22} {entry.get('expected', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
