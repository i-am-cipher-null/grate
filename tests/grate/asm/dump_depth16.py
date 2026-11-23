#!/usr/bin/env python3
"""Extract the depth-surface words a `GRATE_DEPTH=1` run printed.

Usage: dump_depth16.py RUN_DIR [OUT_TSV]

RUN_DIR is a run directory written by asm_characterize.py. `GRATE_DEPTH=1`
makes the assembler print the first words of the D16 depth surface after the
draw, as

    depth16 row0: 4000 4000 4000 4000 mid: 4000 4000 last: 4000 4000

so a depth DW that was dropped leaves the rasterizer's own z (0x8000 for a
full-screen quad at z = 0.5) and one that was written shows its payload. One row
per draw that has the line:

    run  phase  name  iteration  row0_0 row0_1 row0_2 row0_3  mid_0 mid_1  last_0 last_1
"""

import csv
import pathlib
import re
import sys

LINE_RE = re.compile(
    r"^depth16 row0: ([0-9A-F]{4}) ([0-9A-F]{4}) ([0-9A-F]{4}) ([0-9A-F]{4}) "
    r"mid: ([0-9A-F]{4}) ([0-9A-F]{4}) last: ([0-9A-F]{4}) ([0-9A-F]{4})\s*$",
    re.MULTILINE)

FIELDS = ("run", "phase", "name", "iteration", "row0_0", "row0_1", "row0_2",
          "row0_3", "mid_0", "mid_1", "last_0", "last_1")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    run = pathlib.Path(sys.argv[1])
    out = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else run / "depth16.tsv"
    rows = []
    with (run / "results.tsv").open() as stream:
        entries = list(csv.DictReader(stream, delimiter="\t"))
    for entry in entries:
        stem = f"{entry['phase']}-{entry['name']}-{int(entry['iteration']):03d}"
        log = run / "logs" / f"{stem}.stdout"
        if not log.is_file():
            continue
        match = LINE_RE.search(log.read_text(errors="replace"))
        if not match:
            continue
        rows.append({"run": run.name, "phase": entry["phase"],
                     "name": entry["name"], "iteration": entry["iteration"],
                     **dict(zip(("row0_0", "row0_1", "row0_2", "row0_3",
                                 "mid_0", "mid_1", "last_0", "last_1"),
                                match.groups()))})
    with out.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter="\t",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
