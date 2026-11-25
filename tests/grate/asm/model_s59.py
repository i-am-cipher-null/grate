#!/usr/bin/env python3
"""Score the note 59 models against a run.

Usage: model_s59.py <results.tsv>

Reads the harness results directly (no manifest needed: every variant's
parameters are in its name) and prints, per cell, the measured lane codes, the
code the model predicts and the residual in code steps (eight steps per octave).

Models:
  wsel2  selector 4 is the fragment's interpolated 2^16 * D, so `rcp r4` scaled
         by 2^15 reads 0.5/D with D = sum lambda_i / W_i; the lambda table is
         note 55's, whose own readback of the sample points is within one code
         step of (0.875, 0.725), (0.85, 0.325) and (0.4, 0.2) in 0..1
         coordinates, i.e. the weights below carry a few percent of slack
  fams   selector 5 = sign(v) * m * 2^K with m the normalized significand and
         K = 32 for |v| < 2, 16 for |v| >= 2; `rsq` gives m^-1/2 * 2^-K/2 and
         `rcp` m^-1 * 2^-K, before the per-variant read scales
  ipl2   the perspective-corrected row 1 value v = sum (lambda_i A_i / W_i) / D
         feeds the same function, and lane 1 reads v * 0.25

The store truncates, so a cell is scored against +-1 step; 58.3 measured that
truncation as one step on the calibration cells.
"""

import math
import sys

LANE_A = 4096.0       # 2^12, the sfu capture lane
LANE_B = 16.0         # 2^4
ROW_A = 2.0 ** -15    # row read lane
ROW_B = 2.0 ** -9

# note 55's screen-space weights at the three sample points
WEIGHTS = {
    "q1": (0.125, 0.725, 0.15),
    "q2": (0.325, 0.325, 0.35),
    "q3": (0.8, 0.1, 0.1),
}
# vertex order in the lambda table: (v1, v2, v3); the blue vertex v2 carries w
W_BLUE = 1


def code_of(value):
    if value <= 0:
        return 0x00
    c = 0xF7 + 8.0 * math.log2(value)
    if c >= 255:
        return 0xFF
    if c < 0:
        return 0x00
    return int(round(c))


def significand(v):
    a = abs(v)
    e = math.floor(math.log2(a))
    return e, a / 2.0 ** e


def selector_kernel(v, op):
    """The sfu result of `rcp` or `rsq` on selector 5 for a row value v.

    The low branch's `rcp` result is 2^-32-ish and reaches the multiplier as
    zero: scaling it by 2^12 still stores `00` (the note 58 underflow cell, now
    with a gain), so the model returns 0 there rather than a 2^-20 value.
    """
    if v == 0:
        return 0.0 if op == "rcp" else float("inf")
    e, m = significand(v)
    k = 16 if abs(v) >= 2 else 32
    if op == "rcp":
        if abs(v) < 2:
            return 0.0
        return m ** -1.0 * 2.0 ** -k
    return m ** -0.5 * 2.0 ** (-k / 2.0)


def d_of(point, w):
    lam = WEIGHTS[point]
    ws = [1.0, 1.0, 1.0]
    ws[W_BLUE] = float(w)
    return sum(l / x for l, x in zip(lam, ws))


def load(path):
    rows = []
    with open(path) as fp:
        header = fp.readline().rstrip("\n").split("\t")
        for line in fp:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != len(header):
                continue
            row = dict(zip(header, fields))
            if row["phase"] == "variant" and row["status"] == "pass":
                word = int(row["result"], 16)
                rows.append((row["name"], (word >> 8) & 0xFF,
                             (word >> 24) & 0xFF))
    return rows


def parse_fams(name):
    # fams-<family>-<value>-<op>, or fams-neg-<value>-rcp
    parts = name.split("-")
    op = parts[-1]
    value = parts[-2].replace("p", ".").replace("n", "-")
    return float(value), op, parts[1] == "neg"


def predict_fams(name):
    v, op, neg = parse_fams(name)
    result = selector_kernel(v, op)
    if neg:
        gain = result * 4096.0
        return (code_of(abs(gain)), 0x00)
    return (code_of(result * LANE_A), code_of(result * LANE_B))


def predict_wsel2(name, wsel2_scale):
    parts = name.split("-")
    point, w = parts[1], int(parts[2][1:])
    if wsel2_scale == "row":
        # the control lane reads row 1 * row 0 = 2^-15 whatever the geometry
        return (None, code_of(2.0 ** -15))
    return (None, code_of(0.5 / d_of(point, w)))


def predict_ipl2(name):
    parts = name.split("-")
    point, setname, w = parts[1], parts[2], int(parts[3][1:])
    lam = WEIGHTS[point]
    sets = {"a": (1.0, 0.5, 0.25), "b": (4.0, 2.0, 1.0)}
    ar, ag, ab = sets[setname]
    # varying per vertex: v1 = ag (green), v2 = ab (blue), v3 = ar (red)
    vals = (ag, ab, ar)
    ws = [1.0, float(w), 1.0]
    num = sum(l * a / x for l, a, x in zip(lam, vals, ws))
    d = sum(l / x for l, x in zip(lam, ws))
    v = num / d
    return (code_of(v * 0.25), code_of(selector_kernel(v, "rsq") * LANE_A))


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    rows = load(sys.argv[1])
    seen = {}
    for name, b1, b3 in rows:
        seen.setdefault(name, set()).add((b1, b3))
    rows = [(name, sorted(seen[name])[0][0], sorted(seen[name])[0][1])
            for name in seen]
    fetch = " ".join(rows[i][0] for i in range(min(2, len(rows))))
    section = "fams" if "fams-" in fetch else "wsel2" if "wsel2-" in fetch \
        else "ipl2"
    print("section %s: %d cells" % (section, len(rows)))
    spread = [k for k in seen if len(seen[k]) > 1]
    if spread:
        print("  %d cells do not repeat identically: %s"
              % (len(spread), ", ".join(sorted(spread))))
    worst = 0
    inside = 0
    scored = 0
    for name, b1, b3 in rows:
        if section == "fams":
            p1, p3 = predict_fams(name)
        elif section == "ipl2":
            p1, p3 = predict_ipl2(name)
        else:
            p1, p3 = predict_wsel2(name, "row" if "ctl" in name else "")
        deltas = []
        for meas, pred in ((b1, p1), (b3, p3)):
            if pred is None:
                continue
            deltas.append(meas - pred)
        if not deltas:
            print("  %-34s %02X/%02X   (no prediction)" % (name, b1, b3))
            continue
        scored += 1
        step = max(abs(x) for x in deltas)
        worst = max(worst, step)
        if step <= 1:
            inside += 1
        print("  %-34s %02X/%02X  pred %s  %s" % (
            name, b1, b3,
            "/".join("--" if p is None else "%02X" % p for p in (p1, p3)),
            " ".join("%+d" % x for x in deltas) + ("  ok" if step <= 1
                                                   else "  OFF")))
    print("scored %d cells, %d within one step, worst %d steps"
          % (scored, inside, worst))


if __name__ == "__main__":
    main()
