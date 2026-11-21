#!/usr/bin/env python3
"""Generate the linker/TRAM probe manifest.

The varyings path has three programs: the vertex shader exports a vector, the
linker copies its components into TRAM rows with a chosen format and swizzle,
and the fragment's `ipl` fetches and interpolates them into r0..r3. This set
keeps the vertex side constant - vs_tram.txt exports (0.25, 0.5, 0.75, 0.875)
from every vertex - so interpolation is the identity and any change in the
word is the linker, the fetch, or the conversion, not the sample position.

  map-*     the four-letter swizzle is the destination component of each export
            component in order, and `tN` picks the TRAM row
  fetch-*   an ipl NOP leaves its row register at the prime; sat(NOP) is legal
  sat-*     vs_tram_hi.txt makes blue 1.5, so sat() has something to clamp
  fmt-*     the linker's fx10.l/fx10.h and the fetch's .fx10 read, raw
  dis-*     the linker's (dis) modifier on a constant and on the colour quad
  z-*       the optional (z) modifier, documented as the magic TRAM0.w write

Every variant uses the same fragment template (asm/tram_fs.in) and linker
template (asm/tram_linker.in) and replaces only the LINK instruction, the MFU
word and the readout, so the programs differ in the field under test.

    make_tram_probe.py                       # no expectations yet
    make_tram_probe.py --results DIR         # pin to the words measured in DIR
"""

import argparse
import csv
import json
import pathlib

NOP = "NOP"
VS = "tests/grate/asm/vs_tram.txt"
VS_HI = "tests/grate/asm/vs_tram_hi.txt"
VS_COLOR = "tests/grate/asm/vs_mov.txt"
VS_B = "tests/grate/asm/vs_tram_b.txt"
LINKER = "tests/grate/asm/tram_linker.in"
FS = "tests/grate/asm/tram_fs.in"
OUT = "tests/grate/asm_characterize.tram.json"

# 0x520's low field is the batch count: four EXEC blocks, one row.
START_WORD = 0x20006000
BATCHES = 4

PROLOGUE = "sfu: rcp r4 mul0: bar, sfu, bar0 mul1: bar, sfu, bar1"

# The primes make an untouched register read as 0x10/0x20/0x60/0xA0 (fp20
# variants, through the `.l*` conversion) or as those same bytes (raw fx10
# variants), so an ipl NOP and a half-written register are both identifiable.
PRIME_FP20 = ["MAD  r0.lh,  u0,  #1,  #0",
              "MAD  r1.lh,  u1,  #1,  #0",
              "MAD  r2.lh,  u3,  #1,  #0",
              "MAD  r3.lh,  u5,  #1,  #0"]
PRIME_FX10 = ["MAD  r0.l*,  u0,  #1,  #0",
              "MAD  r0.*h,  u1,  #1,  #0",
              "MAD  r1.l*,  u3,  #1,  #0",
              "MAD  r1.*h,  u5,  #1,  #0"]
PRIME_FX10_23 = ["MAD  r2.l*,  u0,  #1,  #0",
                 "MAD  r2.*h,  u1,  #1,  #0",
                 "MAD  r3.l*,  u3,  #1,  #0",
                 "MAD  r3.*h,  u5,  #1,  #0"]

LINK_Z = "LINK fp20, fp20, fp20, fp20, tram0.xyzw, export1"
LINK = LINK_Z


def read_xy(ipl, primes=PRIME_FP20):
    """Fetch operands 0 and 1, convert r0/r1 to fx10, store r2/r3."""
    return {
        "mfu": ipl,
        "c0": "MAD  r2.l*,  r0,  #1,  #0",
        "c1": "MAD  r3.l*,  r1,  #1,  #0",
        "store": "store rt1, r2, r3",
        "primes": primes,
    }


def read_zw(ipl, primes=PRIME_FP20):
    """Fetch operands 2 and 3, convert r2/r3 in place, store r2/r3."""
    return {
        "mfu": ipl,
        "c0": "MAD  r2.l*,  r2,  #1,  #0",
        "c1": "MAD  r3.l*,  r3,  #1,  #0",
        "store": "store rt1, r2, r3",
        "primes": primes,
    }


def raw_01(ipl, primes=PRIME_FX10):
    """Fetch as fx10 and store r0/r1 unmodified, halves as the fetch left them."""
    return {
        "mfu": ipl,
        "c0": NOP,
        "c1": NOP,
        "store": "store rt1, r0, r1",
        "primes": primes,
    }


def raw_23(ipl, primes=PRIME_FX10_23):
    return {
        "mfu": ipl,
        "c0": NOP,
        "c1": NOP,
        "store": "store rt1, r2, r3",
        "primes": primes,
    }


def variant(name, link, read, vs=VS):
    entry = {"name": name, "link": link, "vs": vs, **read}
    return entry


def variants():
    ipl_xy = "ipl: t0.fp20, t0.fp20, NOP, NOP"
    ipl_zw = "ipl: NOP, NOP, t0.fp20, t0.fp20"
    out = [
        # Destination swizzle: the k-th letter is the TRAM component that export
        # component k is copied to, so yxzw swaps x and y and wzyx reverses all
        # four. The vertex vector is (0.25, 0.5, 0.75, 0.875) = (40, 7F, BF, DF).
        variant("map-xyzw-xy", LINK_Z, read_xy(ipl_xy)),
        variant("map-xyzw-zw", LINK_Z, read_zw(ipl_zw)),
        variant("map-yxzw-xy", "LINK fp20, fp20, fp20, fp20, tram0.yxzw, export1",
                read_xy(ipl_xy)),
        variant("map-yxzw-zw", "LINK fp20, fp20, fp20, fp20, tram0.yxzw, export1",
                read_zw(ipl_zw)),
        variant("map-wzyx-xy", "LINK fp20, fp20, fp20, fp20, tram0.wzyx, export1",
                read_xy(ipl_xy)),
        variant("map-wzyx-zw", "LINK fp20, fp20, fp20, fp20, tram0.wzyx, export1",
                read_zw(ipl_zw)),
        # A repeated letter is a repeated destination: the last column that
        # names a component wins. xyxx writes tram.x three times (export.x,
        # export.z, export.w) and tram.y once, so the readback is (w, y) with no
        # unwritten component; xxzw writes x twice and z/w once.
        variant("map-dup-xy", "LINK fp20, fp20, fp20, fp20, tram0.xyxx, export1",
                read_xy(ipl_xy)),
        variant("map-dup-zw", "LINK fp20, fp20, fp20, fp20, tram0.xxzw, export1",
                read_zw(ipl_zw)),
        # Row 1 instead of row 0: the tN prefix in the fetch has to follow.
        variant("map-row1-xy", "LINK fp20, fp20, fp20, fp20, tram1.xyzw, export1",
                read_xy("ipl: t1.fp20, t1.fp20, NOP, NOP")),
        variant("map-row1-zw", "LINK fp20, fp20, fp20, fp20, tram1.xyzw, export1",
                read_zw("ipl: NOP, NOP, t1.fp20, t1.fp20")),
        # export1 is the second enabled export. export0 is gl_Position, so these
        # read the interpolated position instead of the constant and also show
        # that the linker's export number is the enabled-export slot.
        variant("map-export0-xy",
                "LINK fp20, fp20, fp20, fp20, tram0.xyzw, export0",
                read_xy(ipl_xy)),
        variant("map-export0-zw",
                "LINK fp20, fp20, fp20, fp20, tram0.xyzw, export0",
                read_zw(ipl_zw)),

        # Fetch controls: NOP leaves the row register alone, and sat(NOP) is a
        # legal encoding of "no fetch, saturation on" rather than a parse error.
        variant("fetch-nop-x", LINK_Z,
                read_xy("ipl: NOP, t0.fp20, NOP, NOP")),
        variant("fetch-nop-w", LINK_Z,
                read_zw("ipl: t0.fp20, t0.fp20, t0.fp20, NOP")),
        variant("fetch-sat-nop-w", LINK_Z,
                read_zw("ipl: t0.fp20, t0.fp20, t0.fp20, sat(NOP)")),

        # Saturation: 1.5 written to tram.z, fetched with and without sat().
        # In fx10 the value is 0x180, whose stored low byte is 0x80; saturating
        # first gives 1.0 and 0xFF.
        variant("sat-z", LINK_Z,
                read_zw("ipl: NOP, NOP, sat(t0.fp20), t0.fp20"), vs=VS_HI),
        variant("unsat-z", LINK_Z,
                read_zw("ipl: NOP, NOP, t0.fp20, t0.fp20"), vs=VS_HI),
        variant("sat-x", LINK_Z,
                read_xy("ipl: sat(t0.fp20), t0.fp20, NOP, NOP"), vs=VS_HI),

        # Formats. The linker converts each export component to fx10 and the
        # fetch reads a component as two fx10 (VAR2); the raw store shows what
        # lands in the two halves of a row register without a conversion.
        variant("fmt-low-fx10", "LINK fx10.l, fx10.l, fx10.l, fx10.l, tram0.xyzw, export1",
                raw_01("ipl: t0.fx10, t0.fx10, NOP, NOP")),
        variant("fmt-high-fx10", "LINK fx10.h, fx10.h, fx10.h, fx10.h, tram0.xyzw, export1",
                raw_01("ipl: t0.fx10, t0.fx10, NOP, NOP")),
        variant("fmt-low-fx10-zw", "LINK fx10.l, fx10.l, fx10.l, fx10.l, tram0.xyzw, export1",
                raw_23("ipl: NOP, NOP, t0.fx10, t0.fx10")),
        variant("fmt-low-fp20", "LINK fx10.l, fx10.l, fx10.l, fx10.l, tram0.xyzw, export1",
                read_xy(ipl_xy)),
        variant("fmt-fp20-fx10", LINK_Z, raw_01("ipl: t0.fx10, t0.fx10, NOP, NOP")),

        # Interpolation disable. On a constant varying it cannot change the
        # value; on the coloured quad it should replace the interpolated blue.
        variant("dis-const", "LINK fp20 (dis), fp20, fp20, fp20, tram0.xyzw, export1",
                read_xy(ipl_xy)),
        variant("nodis-color", LINK_Z, read_zw(ipl_zw), vs=VS_COLOR),
        variant("dis-color", "LINK fp20, fp20, fp20 (dis), fp20, tram0.xyzw, export1",
                read_zw(ipl_zw), vs=VS_COLOR),

        # (z): the documented magic write to TRAM0.w from VEC4.z. The normal
        # mapping writes export.w = 0.875 to tram0.w; if the magic write wins it
        # reads 0.75 instead.
        variant("z-base", "LINK fp20, fp20, fp20, fp20, tram0.xyzw, export1 (z)",
                read_zw(ipl_zw)),
        variant("z-base-xy", "LINK fp20, fp20, fp20, fp20, tram0.xyzw, export1 (z)",
                read_xy(ipl_xy)),
        # The same (z) linker with a fragment that stores constants and never
        # fetches TRAM: if this is still non-zero, the draw ran and the zero from
        # the other z variants is a TRAM read of zero, not a dropped draw.
        variant("z-alive", "LINK fp20, fp20, fp20, fp20, tram0.xyzw, export1 (z)",
                {"mfu": "ipl: NOP, NOP, NOP, NOP",
                 "c0": "MAD  r2.l*,  u2,  #1,  #0",
                 "c1": "MAD  r3.l*,  u6,  #1,  #0",
                 "store": "store rt1, r2, r3",
                 "primes": PRIME_FP20}),
        # Is the magic write a separate instruction after the normal copy?
        variant("z-two-step",
                "LINK fp20, fp20, fp20, fp20, tram0.xyzw, export1\n"
                "LINK NOP, NOP, NOP, NOP, tram0.xyzw, export1 (z)",
                read_zw(ipl_zw)),
        variant("z-two-step-rev",
                "LINK NOP, NOP, NOP, NOP, tram0.xyzw, export1 (z)\n"
                "LINK fp20, fp20, fp20, fp20, tram0.xyzw, export1",
                read_zw(ipl_zw)),
        variant("z-nop-w", "LINK fp20, fp20, fp20, NOP, tram0.xyzw, export1 (z)",
                read_zw(ipl_zw)),
        variant("z-nop-w-base", "LINK fp20, fp20, fp20, NOP, tram0.xyzw, export1",
                read_zw(ipl_zw)),
        # Does the magic write land in TRAM0.w whatever row the LINK names?
        variant("z-row1", "LINK fp20, fp20, fp20, fp20, tram1.xyzw, export1 (z)",
                read_zw("ipl: NOP, NOP, t1.fp20, t1.fp20")),
        # Row 1 without (z) is the control for the row-1 magic-write question.
        variant("z-row1-base", "LINK fp20, fp20, fp20, fp20, tram1.xyzw, export1",
                read_zw("ipl: NOP, NOP, t1.fp20, t1.fp20")),
        # The swizzle maps export.x to tram0.w; does (z) override that with
        # export.z, or is it an extra component?
        variant("z-swizzle", "LINK fp20, fp20, fp20, fp20, tram0.wzyx, export1 (z)",
                read_zw(ipl_zw)),
        # export0 = position: w is 1.0 and z is 0, so which one lands in tram0.w
        # says which vector the z select reads.
        variant("z-export0", "LINK fp20, fp20, fp20, fp20, tram0.xyzw, export0 (z)",
                read_zw(ipl_zw)),
        variant("z-export0-base", "LINK fp20, fp20, fp20, fp20, tram0.xyzw, export0",
                read_zw(ipl_zw)),

        # Persistence pair, in this order. The first draw fills the whole row
        # with (0.25, 0.5, 0.75, 0.875); the second exports the reversed vector
        # (0.875, 0.75, 0.5, 0.25) but its LINK writes only tram.y. Reading x
        # and y then says whether the NOP column keeps the previous draw's x
        # (0.25) or the row was cleared (0.0).
        variant("persist-first", LINK_Z, read_xy(ipl_xy)),
        variant("persist-keep", "LINK NOP, fp20, NOP, NOP, tram0.xyzw, export1",
                read_xy(ipl_xy), vs=VS_B),
    ]
    return out


def build_manifest():
    entries = []
    for entry in variants():
        fs = {
            "@P0@": entry["primes"][0],
            "@P1@": entry["primes"][1],
            "@P2@": entry["primes"][2],
            "@P3@": entry["primes"][3],
            "@MFU@": f"{PROLOGUE} {entry['mfu']}",
            "@C0@": entry["c0"],
            "@C1@": entry["c1"],
            "@STORE@": entry["store"],
        }
        variant_entry = {
            "name": entry["name"],
            "environment": {"GRATE_POKE": f"0x520=0x{START_WORD | BATCHES:08X}"},
            "replace": {"fs": fs, "lnk": {"@LINK@": entry["link"]}},
        }
        if entry["vs"] != VS:
            variant_entry["vs"] = entry["vs"]
        entries.append(variant_entry)
    names = [entry["name"] for entry in entries]
    if len(names) != len(set(names)):
        raise SystemExit("variant names are not unique")
    return {
        "name": "tram-varying",
        "repeat": 3,
        "timeout": 10,
        "program": {
            "vs": VS,
            "fs": FS,
            "lnk": LINKER,
        },
        "controls": CONTROLS,
        "variants": entries,
    }


# The controls must not use the parameterised linker template, which still has
# its placeholder when a control runs.
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


def measured(path):
    """variant name -> the single word every repeat agreed on."""
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
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", help="run directory to pin expectations from")
    parser.add_argument("--out", default=OUT, help="manifest to write")
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
    pathlib.Path(args.out).write_text(json.dumps(manifest, indent="\t") + "\n")
    print(f"wrote {args.out} ({len(manifest['variants'])} variants)")
    for entry in manifest["variants"]:
        print(f"{entry['name']:24} {entry.get('expected', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
