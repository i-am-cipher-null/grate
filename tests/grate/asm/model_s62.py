#!/usr/bin/env python3
"""Score the note 62 cells against the candidate operands.

Usage: model_s62.py <results.tsv> [<results.tsv> ...]

Every variant name carries its parameters, so the scorer needs no manifest.
Lane 1 (byte 1) is an ALU row readback scaled by 2^-15; lane 2 (byte 3) is the
sfu capture.  Selector 5..7 cells capture at 2^4, so

    code = 151 - 4*log2(m)   for |v| < 2
    code = 215 - 4*log2(m)   for |v| >= 2

with m the significand in [1, 2).  Selector-4 cells (`rcp r4` at 2^12) read
`code = 215 - 8*log2(D)`, which is how D is measured rather than assumed.  The
gain-pair cells at 2^6 are 16 codes lower than their 2^4 twins.

Candidates use note 60's weights (blue 0.70, green 0.13, red 0.15, sum 0.98)
with no per-cell fit:

    D = sum lambda_i/W_i     N = sum lambda_i A_i/W_i     V = N/D
    T_i = lambda_i A_i / W_i
    rowD = V*D/LSUM          the candidate factor from note 61, LSUM = 0.98

`rowD` is V*D/LSUM rather than "row times a factor" because the row value at
the fixed sample is V.  A cell matches a candidate when |residual| <= 1 step.
"""

import math
import sys

LAM = {"red": 0.15, "green": 0.13, "blue": 0.70}
LSUM = sum(LAM.values())

COLS = ("V", "N", "D", "Tr", "Tg", "Tb", "rowD")


def cap(v, gain=16.0):
    """Store code of the selector-5..7 transfer function for operand v."""
    if v <= 0:
        return 0x00
    m = v / 2.0 ** math.floor(math.log2(v))
    c = (215.0 if abs(v) >= 2 else 151.0) - 4.0 * math.log2(m)
    c += 8.0 * math.log2(gain / 16.0)
    if c >= 255:
        return 0xFF
    if c < 0:
        return 0x00
    return int(c)


def decode_row(code):
    if code <= 0 or code >= 255:
        return None
    return 2.0 ** ((code - 0xF7) / 8.0 + 15.0)


def num(text):
    return float(text.replace("p", ".").replace("n", "-"))


def load(paths):
    rows = []
    for path in paths:
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
                rows.append((row["name"], (word >> 8) & 0xFF,
                             (word >> 24) & 0xFF))
    seen = {}
    for name, b1, b3 in rows:
        seen.setdefault(name, set()).add((b1, b3))
    spread = sorted(k for k, v in seen.items() if len(v) > 1)
    return ([(n, sorted(v)[0][0], sorted(v)[0][1]) for n, v in seen.items()],
            spread)


# --- configurations --------------------------------------------------------

def cand_scalar(w, ar, ag, ab, uniform=False):
    if uniform:                     # vs_scalar_u: D = 1/w exactly
        d = 1.0 / w
        n = (LAM["red"] * ar + LAM["green"] * ag + LAM["blue"] * ab) * d
    else:                           # clip w on one vertex
        d = LAM["red"] + LAM["green"] + LAM["blue"] / w
        n = LAM["red"] * ar + LAM["green"] * ag + LAM["blue"] * ab / w
    v = n / d
    return {"V": v, "N": n, "D": d, "Tr": LAM["red"] * ar,
            "Tg": LAM["green"] * ag, "Tb": LAM["blue"] * ab / w,
            "rowD": v * d / LSUM}


P1B_SETS = {"s1": (1.0, 1.0, 1.0), "s2": (0.5, 1.5, 1.0),
            "s3": (2.0, 0.0, 1.0)}
SEED_BY_LABEL = {"a": 1.0, "b": 0.5625, "c": 3.0}


def cell(name):
    """(candidates dict with either the columns or 'seed', gain) or None."""
    parts = name.split("-")

    if parts[0] == "lk":
        return {"seed": num(parts[2][1:])}, 16.0

    if parts[0] == "g":
        w = num(parts[2][1:])
        gain = 64.0 if parts[3] == "x64" else 16.0
        if parts[1] == "seed":
            return {"seed": 1.0}, gain
        return cand_scalar(w, 1.0, 1.0, 1.0, uniform=True), gain

    if parts[0] == "p1a":
        return cand_scalar(num(parts[1][1:]), 1.0, 1.0, 1.0), 16.0

    if parts[0] == "p1b":
        ar, ag, ab = P1B_SETS[parts[1]]
        return cand_scalar(1.0, ar, ag, ab), 16.0

    if parts[0] == "p1c":          # superseded green shader, row value unknown
        return None

    if parts[0] == "w":
        w = num(parts[2][1:])
        return cand_scalar(w, 1.0, 1.0, 1.0), 16.0

    if parts[0] in ("cp", "t4") and parts[1].startswith("seed"):
        label = parts[1].split("-")[-1]
        return {"seed": SEED_BY_LABEL[label]}, 16.0

    if parts[0] in ("cp", "t4"):
        return None                # compared by code in the note, not scored

    if parts[0] == "q":
        return {"seed": 3.0 if "-v3-" in name else 1.0}, 16.0

    if parts[0] == "dup":
        return {"seed": 3.0}, 16.0

    return None


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    rows, spread = load(sys.argv[1:])
    if spread:
        print("repeat spread on %d cells: %s"
              % (len(spread), ", ".join(spread)))

    print("\n%-18s %-9s %s" % ("cell", "measured", "residual per candidate"))
    tally = {c: [0, 0] for c in COLS}
    seeds = []
    for name, b1, b3 in sorted(rows):
        got = cell(name)
        if got is None:
            continue
        cands, gain = got
        if "seed" in cands:
            pred = cap(cands["seed"], gain)
            seeds.append((name, b3, pred))
            print("  %-16s cap %02X  seeded %-7g -> %02X%s" % (
                name, b3, cands["seed"], pred,
                "" if abs(pred - b3) <= 1 else "   off %+d" % (pred - b3)))
            continue
        deltas = {}
        for col in COLS:
            deltas[col] = cap(cands[col], gain) - b3
            tally[col][0] += abs(deltas[col]) <= 1
            tally[col][1] += 1
        best = min(COLS, key=lambda c: abs(deltas[c]))
        print("  %-16s cap %02X  %s  [%s]" % (
            name, b3, " ".join("%s%+d" % (c, deltas[c]) for c in COLS), best))

    print("\ncells within one step / scored cells")
    for col in COLS:
        ok, total = tally[col]
        if total:
            print("  %-5s %d/%d" % (col, ok, total))
    if seeds:
        print("  seed  %d/%d" % (sum(1 for _, b3, p in seeds if abs(p - b3) <= 1),
                                 len(seeds)))


if __name__ == "__main__":
    main()
