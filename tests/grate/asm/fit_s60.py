#!/usr/bin/env python3
"""Fit the interpolation weights from a note 60 selector-4 run.

Usage: fit_s60.py <results.tsv>

The store's code is `0xF7 + 8*log2(value)` and the calibration of note 58.3
puts a positive value about one step above its code, so the model used here is

    code = 0x70 + 8*log2(1/D) + o

with `0x70` the code of the value the test word stores when `D = 1`
(`0.5 * 2^-16`), `o` a single global offset fitted with the weights, and

    D = 1 - b + b/w        (w on one vertex, the other two at 1)
    D = 1/w                (uniform w, no free parameter)

Each sweep depends on one weight only, so the weights are fitted one at a time
by a grid search and `o` is chosen over the whole run.  The uniform cells are
pure predictions: they contain no weight at all.
"""

import math
import sys

BASE = 0x70          # code of 0.5 * 2^-16
STEPS = 8.0          # code steps per octave


def load(path):
    rows = []
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
            # lane 2 (r1, byte 3) is the selector capture in the prec section;
            # lane 1 is the row readback used as a control
            rows.append((row["name"], (word >> 24) & 0xFF))
    return rows


def parse(name):
    """(mode, vertex, w) from names like prec-blueq1-w8 or prec-uni-w0p5."""
    parts = name.split("-")
    mode = parts[0]
    who = parts[1]
    w = float(parts[2][1:].replace("p", ".").replace("n", "-"))
    if who.startswith("blueq1"):
        return "sweep", "blue", w
    if who == "uni":
        return "uni", None, w
    if who in ("blue", "green", "red"):
        return "sweep", who, w
    if who.startswith("blue"):
        return "sweep", "blue", w
    return None, None, w


def d_of(mode, b, w):
    if mode == "uni":
        return 1.0 / w
    return 1.0 - b + b / w


def predict(mode, b, w, o):
    return BASE + STEPS * math.log2(1.0 / d_of(mode, b, w)) + o


def group_key(name):
    parts = name.split("-")
    if parts[-1] == "again":
        return group_key("-".join(parts[:-1]))
    who = parts[1]
    if who == "uni":
        return "uniform"
    if len(parts) > 3:
        # e.g. prec-blue-w8-q2: a second sample point, its own weight
        return "other sample"
    if who.startswith("blueq1"):
        return "blue@q1"
    if who.startswith("blue"):
        return "blue@far"
    return who


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    rows = load(sys.argv[1])
    seen = {}
    for name, code in rows:
        seen.setdefault(name, set()).add(code)
    spread = {k: v for k, v in seen.items() if len(v) > 1}
    if spread:
        print("repeat spread:", ", ".join(sorted(spread)))
    cells = [(n, sorted(c)[0]) for n, c in sorted(seen.items())]
    groups = {}
    for name, code in cells:
        mode, who, w = parse(name)
        if mode is None or group_key(name) == "other sample":
            # a second sample point has its own weights; it is reported, not
            # fitted, so it cannot drag a weight that belongs to another sample
            continue
        groups.setdefault(group_key(name), []).append((mode, w, code, name))

    best = None
    for oi in [x * 0.1 for x in range(-15, 16)]:
        fits = {}
        for gname, items in groups.items():
            if gname in ("uniform",):
                fits[gname] = (None, 0.0)
                continue
            best_b, best_r = None, 1e9
            for bi in range(0, 101):
                b = bi / 100.0
                worst = max(abs(predict(m, b, w, oi) - c)
                            for m, w, c, _ in items)
                if worst < best_r - 1e-9:
                    best_b, best_r = b, worst
            fits[gname] = (best_b, best_r)
        total = max(max(r[1], 0.0) for r in fits.values())
        if best is None or total < best[0]:
            best = (total, oi, fits)

    total, oi, fits = best
    print("global store offset o = %+.1f steps; worst residual %.2f steps"
          % (oi, total))
    for gname in ("uniform", "blue@q1", "green", "red", "blue@far"):
        if gname not in fits:
            continue
        b, worst = fits[gname]
        items = groups[gname]
        if b is None:
            worst = max(abs(predict(m, 0.0, w, oi) - c) for m, w, c, _ in items)
            print("  %-9s no free weight (D = 1/w): %d cells, worst %.2f steps"
                  % (gname, len(items), worst))
        else:
            print("  %-9s b = %.2f  (%d cells, worst %.2f steps)"
                  % (gname, b, len(items), worst))
    ws = [fits[g][0] for g in ("blue@q1", "green", "red")
          if g in fits and fits[g][0] is not None]
    if len(ws) == 3:
        print("  at q1: blue %.2f + green %.2f + red %.2f = %.2f"
              % (ws[0], ws[1], ws[2], sum(ws)))
    print()
    for name, code in cells:
        mode, who, w = parse(name)
        if mode is None:
            print("  %-24s %02X   (not a prec cell)" % (name, code))
            continue
        gname = group_key(name)
        if gname == "other sample":
            print("  %-24s %02X   (other sample: not fitted)" % (name, code))
            continue
        b = fits.get(gname, (None, 0))[0] or 0.0
        p = predict(mode, b, w, oi)
        print("  %-24s %02X  pred %6.2f  %+4.1f steps" % (name, code, p, code - p))


if __name__ == "__main__":
    main()
