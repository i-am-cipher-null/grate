#!/usr/bin/env python3
"""Check the note 56 screen: operand indices that differ only in bits 5:4 must
give the same result, and print one row per residue for the write-up.

Usage: check_s56.py <results.tsv>...
"""

import sys


def load(path):
    with open(path) as fp:
        header = fp.readline().rstrip("\n").split("\t")
        idx = {n: i for i, n in enumerate(header)}
        out = {}
        for line in fp:
            row = line.rstrip("\n").split("\t")
            if row[idx["name"]] != "-":
                out[row[idx["name"]]] = row[idx["result"]]
    return out


def main():
    results = {}
    for path in sys.argv[1:]:
        results.update(load(path))

    axes = ("X4", "Y4", "Z4")
    mismatch = 0
    checked = 0
    table = {}
    for residue in range(16):
        for axis in axes:
            codes = []
            for high in (0, 16, 32, 48):
                name = "sel%d-%s" % (residue + high, axis)
                if name in results:
                    codes.append(results[name])
            if len(codes) < 4:
                print("missing: residue %d axis %s -> %r" % (residue, axis, codes))
                continue
            checked += 1
            if len(set(codes)) != 1:
                mismatch += 1
                print("MISMATCH residue %d %s: %r" % (residue, axis, codes))
            table[(residue, axis)] = codes[0]

    print("residue  X4        Y4        Z4")
    for residue in range(16):
        print("%2d       %-9s %-9s %-9s" % (
            residue,
            table.get((residue, "X4"), "-"),
            table.get((residue, "Y4"), "-"),
            table.get((residue, "Z4"), "-")))
    print("residue groups checked: %d, mismatching groups: %d" % (checked, mismatch))


if __name__ == "__main__":
    main()
