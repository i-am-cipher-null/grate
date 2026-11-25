#!/usr/bin/env python3
"""Print note 58 (or note 57) results as codes and nominal values.

Usage: analyze.py <results.tsv> [name substring ...]

One line per variant: name, the raw store word, the two lane codes and the
nominal value each code stands for (code = 0xF7 + 8*log2(value), so the value is
2^((code-0xF7)/8); the code truncates, so a quoted value is the top of a
one-step-wide band).  `--` separates the two lanes when they differ.
"""

import sys


def load(path):
    rows = []
    with open(path) as fp:
        header = fp.readline().rstrip("\n").split("\t")
        for line in fp:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != len(header):
                continue
            row = dict(zip(header, fields))
            if row["phase"] != "variant":
                continue
            rows.append(row)
    return rows


def code_to_value(code):
    if code in (0x00, 0xFF):
        return None
    return 2.0 ** ((code - 0xF7) / 8.0)


def main():
    path = sys.argv[1]
    needles = sys.argv[2:]
    rows = load(path)
    seen = {}
    for row in rows:
        name = row["name"]
        word = int(row["result"], 16)
        b1 = (word >> 8) & 0xFF
        b3 = (word >> 24) & 0xFF
        if any(n in name for n in needles) or not needles:
            key = name
            seen.setdefault(key, []).append((b1, b3))
    for name in sorted(seen):
        codes = seen[name]
        uniq = sorted(set(codes))
        def fmt(pair):
            b1, b3 = pair
            v1 = code_to_value(b1)
            v3 = code_to_value(b3)
            s1 = "%.4g" % v1 if v1 is not None else ("2+" if b1 == 0xFF else "0/neg")
            s3 = "%.4g" % v3 if v3 is not None else ("2+" if b3 == 0xFF else "0/neg")
            txt = "%02X/%02X %s" % (b1, b3, s1)
            if b1 != b3:
                txt += " | %s" % s3
            return txt
        if len(uniq) == 1:
            print("%-42s %s" % (name, fmt(uniq[0])))
        else:
            good = [fmt(u) for u in uniq if codes.count(u) == 3] or [fmt(u) for u in uniq]
            print("%-42s UNSTABLE %s" % (name, " ; ".join(good)))


if __name__ == "__main__":
    main()
