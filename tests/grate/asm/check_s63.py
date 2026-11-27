#!/usr/bin/env python3
"""Print the note 63 paired table and the duplicate agreement for a run.

Usage: check_s63.py <results.tsv> [<results.tsv> ...]

Reads the harness results directly.  Lane codes are byte 1 (row readback, scale
2^-15) and byte 3 (sfu capture); bytes 0 and 2 are never written by
`store rt1, r0, r1` and are ignored.

For every `s63_pair`/`s63_pair4` run it prints fetched against seeded for each
value, with the delta, and how many repeats agreed (`n`).  For any run it prints
the cells whose repeats disagreed, which is the within-manifest repeatability
check the note relies on.
"""

import sys


def load(path):
    rows = {}
    with open(path) as fp:
        header = fp.readline().rstrip("\n").split("\t")
        for line in fp:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != len(header):
                continue
            row = dict(zip(header, fields))
            if row["phase"] != "variant" or row["status"] != "pass":
                continue
            word = int(row["result"], 16)
            rows.setdefault(row["name"], []).append(
                ((word >> 8) & 0xFF, (word >> 24) & 0xFF))
    return rows


def paired(rows, prefix):
    print("\n%s: fetched against seeded" % prefix)
    print("  %-6s %-10s %-10s %s" % ("value", "fetched", "seeded", "delta"))
    for v in ("1", "1p5", "3", "4"):
        f = rows.get("%s-f-%s" % (prefix, v))
        s = rows.get("%s-s-%s" % (prefix, v))
        if not f or not s:
            continue
        fb = sorted({b3 for _, b3 in f})
        sb = sorted({b3 for _, b3 in s})
        print("  %-6s 0x%02X x%-3d  0x%02X x%-3d  %+d"
              % (v.replace("p", "."), fb[0], len(f), sb[0], len(s),
                 sb[0] - fb[0]))
        if len(fb) > 1 or len(sb) > 1:
            print("    repeats disagreed: fetched %s, seeded %s"
                  % (fb, sb))


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for path in sys.argv[1:]:
        rows = load(path)
        print("== %s (%d cells)" % (path, len(rows)))
        spread = {n: v for n, v in rows.items() if len(set(v)) > 1}
        if spread:
            print("  repeat spread on %d cells" % len(spread))
        if any(n.startswith("p4-") for n in rows):
            paired(rows, "p4")
        if any(n.startswith("pr-") for n in rows):
            paired(rows, "pr")
        for n in sorted(rows):
            if n.startswith(("cx-", "sm-", "or-", "br-b", "br-a")):
                print("  %-22s %s" % (n, ", ".join(
                    "0x%02X" % b3 for _, b3 in rows[n])))


if __name__ == "__main__":
    main()
