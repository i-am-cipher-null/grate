#!/usr/bin/env python3
"""Score the note 61 cells against the candidate operands.

Usage: model_s61.py <results.tsv> [<results.tsv> ...]

Every variant name carries its own parameters, so the scorer needs no manifest.
Two things are measured per cell: lane 1 (byte 1) is the ALU readback of row 1
scaled by 2^-15, lane 2 (byte 3) is the sfu result scaled by 2^4 through the
explicit `sfu` multiplier token.  The store truncates, so

    code = floor(0xF7 + 8*log2(value))

and one code step is 1/8 octave; a cell counts as matched at +-1 step.

Candidates for the numeric sections are the corrected varying V, the
interpolated numerator N = sum lambda_i A_i / W_i, the perspective denominator
D = sum lambda_i / W_i, and the three per-vertex terms T_i = lambda_i A_i / W_i.
Note 60's q1 weights are reused: lambda = (blue 0.70, green 0.13, red 0.15),
so D(w) = 0.28 + 0.70/w for a clip w on the blue vertex.

The selector-5..7 transfer function is the one note 58.1 calibrated and note 60
confirmed: `rsq` returns m^-1/2 * 2^-K/2 with K = 32 below |v| = 2 and 16 at or
above it, K = 16 being exactly the branch that lands an octave higher.  At the
2^4 capture gain that is

    |v| < 2    code = 151 - 4*log2(m)
    |v| >= 2   code = 215 - 4*log2(m)

so the code gives the branch and the significand, and the two branches are 64
codes apart.

The component sections (`comp`, `swz`) are reported rather than predicted: the
base arm and the two one-component-changed arms are compared by code, which
identifies the followed component without a model, and the row readbacks give
the order the interpolator word fills rows 0..3 in.
"""

import math
import sys

LAM = {"red": 0.15, "green": 0.13, "blue": 0.70}
LSUM = sum(LAM.values())

COLS = ("V", "N", "D", "Tr", "Tg", "Tb")

# component values per arm, mirroring make_s61.py
ARM_VALUES = {
    "base": (0.25, 0.5, 1.0, 1.5),
    "x": (3.0, 0.5, 1.0, 1.5),
    "w": (0.25, 0.5, 1.0, 3.0),
    "sig": (0.3, 0.6, 3.0, 0.25),
    "sigp": (0.6, 0.3, 3.0, 0.25),
    "dsc": (1.0, 1.5, 3.0, 0.5625),
}

D4 = LAM["red"] + LAM["green"] + LAM["blue"] / 4.0

SEEDS = {
    "V-b1p9": 1.9 * LSUM / D4,
    "N-b1p9": 1.9 * LSUM,
    "V-a1p9": 1.9,
    "N-a1p9": 1.9 * D4,
    "D": D4,
    "V-a0p5": 0.5,
    "N-a0p5": 0.5 * D4,
    "T-r": LAM["red"],
    "T-g": LAM["green"],
    "T-b": LAM["blue"] / 4.0,
    "V-r": LAM["red"] / D4,
    "V-g": LAM["green"] / D4,
    "V-b": LAM["blue"] / 4.0 / D4,
    "sum-V": 1.0,
}


def d_of(w):
    return LAM["red"] + LAM["green"] + LAM["blue"] / w


def store(value):
    if value <= 0:
        return 0x00
    c = 0xF7 + 8.0 * math.log2(value)
    if c >= 255.0:
        return 0xFF
    return int(math.floor(c))


def kernel(v):
    """`rsq` of one selector value, before the 2^4 capture gain."""
    if v == 0:
        return 0.0
    a = abs(v)
    e = math.floor(math.log2(a))
    m = a / 2.0 ** e
    k = 16.0 if a >= 2.0 else 32.0
    return m ** -0.5 * 2.0 ** (-k / 2.0)


def cap(v):
    """Store code of the capture lane for a selector operand v."""
    return store(kernel(v) * 16.0)


def rowv(v):
    """Store code of the row readback lane for a row value v."""
    return store(v * 2.0 ** -15)


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
    return rows


def dedup(rows):
    """One entry per name; a name whose repeats disagree is reported."""
    seen = {}
    for name, b1, b3 in rows:
        seen.setdefault(name, set()).add((b1, b3))
    spread = sorted(k for k, v in seen.items() if len(v) > 1)
    out = [(n, sorted(v)[0][0], sorted(v)[0][1]) for n, v in seen.items()]
    return out, spread


# --- candidates ------------------------------------------------------------

def candidates(name):
    """{column: value} for a numeric cell, or None if the cell is not scored."""
    if name.startswith("num-seed-") or name.startswith("vtx-seed-"):
        label = name.split("-seed-")[1].rsplit("-w", 1)[0]
        return {"seed": SEEDS[label]}

    parts = name.split("-")
    if parts[0] == "num":
        arm = parts[1]
        if arm.startswith("lad"):
            w = num(parts[3][1:])
            c = num(parts[2][1:])
            return config_a(c, w)
        if arm.startswith("a"):
            w = num(parts[2][1:])
            return config_a(num(arm[1:]), w)
        if arm.startswith("b"):
            w = num(parts[2][1:])
            return config_b(num(arm[1:]), w)
        if arm.startswith("ua"):
            w = num(parts[2][1:])
            return uniform(0.5, 0.5, w)
        if arm.startswith("ub"):
            w = num(parts[2][1:])
            return uniform(0.5 * w, 0.5, w)
        return None
    if parts[0] == "vtx":
        label = parts[1]
        w = num(parts[2][1:])
        sets = {"r": (1.0, 0.0, 0.0), "g": (0.0, 1.0, 0.0),
                "b": (0.0, 0.0, 1.0), "all": (1.0, 1.0, 1.0)}
        if label not in sets:
            return None
        return per_vertex(sets[label], w)
    if parts[0] == "p3":
        return {"seed": 3.0} if "seed" in parts[1] else None
    if parts[0] == "ws":
        return {"seed": 1.0} if parts[1] == "seed" else None
    if parts[0] == "bnd":
        w = num(parts[-1][1:])
        if parts[1] in ("ipl", "rw"):
            return config_a(0.5, w)
        if parts[1] == "s7":
            return config_a(0.5, w)
        if parts[1] in ("cp", "cp-row3"):
            return config_a(0.5, w)
        if parts[1] == "seed":
            return {"seed": 0.5}
        return None
    return None


def config_a(c, w):
    d = d_of(w)
    return {"V": c, "N": c * d, "D": d, "Tr": LAM["red"] * c,
            "Tg": LAM["green"] * c, "Tb": LAM["blue"] * c / w}


def config_b(c, w):
    d = d_of(w)
    return {"V": c * LSUM / d, "N": c * LSUM, "D": d,
            "Tr": LAM["red"] * c, "Tg": LAM["green"] * c,
            "Tb": LAM["blue"] * c}


def uniform(a, n, w):
    """Uniform clip w: D = 1/w exactly and every T_i shares it."""
    return {"V": a, "N": n / w, "D": 1.0 / w, "Tr": LAM["red"] * a / w,
            "Tg": LAM["green"] * a / w, "Tb": LAM["blue"] * a / w}


def per_vertex(a, w):
    ar, ag, ab = a
    d = d_of(w)
    n = LAM["red"] * ar + LAM["green"] * ag + LAM["blue"] * ab / w
    return {"V": n / d, "N": n, "D": d, "Tr": LAM["red"] * ar,
            "Tg": LAM["green"] * ag, "Tb": LAM["blue"] * ab / w}


# --- component sections ----------------------------------------------------

def comp_table(rows):
    """Print the one-component-at-a-time matrix, model-free."""
    groups = {}
    for name, b1, b3 in rows:
        parts = name.split("-")
        prefix, arm = parts[0], parts[1]
        if parts[2].startswith("rows"):
            which = parts[2]
            w = num(parts[3][1:])
            groups.setdefault((prefix, arm, w), {})[which] = (b1, b3)
        elif parts[2].startswith("w"):
            w = num(parts[2][1:])
            groups.setdefault((prefix, arm, w), {})[parts[3]] = (b1, b3)
        elif parts[-1] == "again":
            continue
    for key in sorted(groups):
        prefix, arm, w = key
        cells = groups[key]
        rows_txt = ""
        for which in ("rows01", "rows23"):
            if which in cells:
                a, b = cells[which]
                va, vb = decode_row(a), decode_row(b)
                rows_txt += "  %s %s" % (
                    which, " ".join("%.4g" % v if v else "--"
                                    for v in (va, vb)))
        print("  %-5s %-4s w%-3g%s" % (prefix, arm, w, rows_txt))
        for sel in ("s5", "s6", "s7"):
            if sel in cells:
                print("        %s -> %02X" % (sel, cells[sel][1]))
        values = ARM_VALUES.get(arm)
        if values:
            print("        vals x=%.3g y=%.3g z=%.3g w=%.3g  pred %s"
                  % (values + ("/".join("%d" % cap(v) for v in values),)))


def p3_table(rows):
    print("  %-16s %-26s %s" % ("cell", "lane1 row readback", "lane3 code"))
    for name, b1, b3 in sorted(rows):
        v1 = decode_row(b1)
        print("  %-16s %-26s   %02X" % (
            name.replace("p3-", ""),
            "--" if v1 is None else "%.4g (code %02X)" % (v1, b1), b3))


# --- main -----------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    rows, spread = dedup(load(sys.argv[1:]))
    if spread:
        print("repeat spread on %d cells: %s" % (len(spread),
                                                 ", ".join(spread)))
    comp = [r for r in rows if r[0].split("-")[0] in ("comp", "swz")]
    p3 = [r for r in rows if r[0].split("-")[0] == "p3"]
    rest = [r for r in rows if r not in comp and r not in p3]

    if comp:
        print("\ncomponent sections (no prediction: codes compared directly)")
        by_prefix = {}
        for r in comp:
            by_prefix.setdefault(r[0].split("-")[0], []).append(r)
        for prefix in sorted(by_prefix):
            print(" %s" % prefix)
            comp_table(by_prefix[prefix])

    if p3:
        print("\nboundary cells: lane 1 is a row readback (x 2^-15), lane 3 is")
        print("the sfu capture (x 2^4), listed by cell")
        p3_table(p3)

    scored = []
    for name, b1, b3 in sorted(rest):
        cand = candidates(name)
        if cand is None:
            continue
        scored.append((name, b1, b3, cand))

    print("\nnumeric sections: measured capture code and residual per candidate")
    families = {}
    singles = []
    for name, b1, b3, cand in scored:
        deltas = {col: cap(value) - b3 for col, value in cand.items()}
        inside = [c for c, d in deltas.items() if abs(d) <= 1]
        key = family_key(name)
        families.setdefault(key, []).append((name, deltas, inside))
        if len(inside) == 1:
            singles.append((name, inside[0]))
        v1 = decode_row(b1)
        print("  %-26s r1 %-6s cap %02X  %s  %s" % (
            name, "--" if v1 is None else "%.4g" % v1, b3,
            " ".join("%s%+d" % (c, deltas[c]) for c in sorted(deltas)),
            ("[" + inside[0] + "]") if len(inside) == 1 else
            ("[" + " ".join(inside) + "]") if inside else "[none]"))

    print("\nper family: cells within one step / cells, worst residual")
    for key in sorted(families):
        items = families[key]
        cols = sorted(set.intersection(*[set(d) for _, d, _ in items]))
        line = []
        for col in cols:
            ok = sum(1 for _, d, inside in items if abs(d[col]) <= 1)
            worst = max(abs(d[col]) for _, d, _ in items)
            line.append("%s %d/%d (worst %d)" % (col, ok, len(items), worst))
        print("  %-18s %s" % (key, "  ".join(line)))

    print("\ndiscriminating cells: %d of %d name exactly one candidate"
          % (len(singles), len(scored)))
    counts = {}
    for _, col in singles:
        counts[col] = counts.get(col, 0) + 1
    for col in sorted(counts):
        print("  %-6s %d" % (col, counts[col]))


def family_key(name):
    parts = name.split("-")
    if "-seed-" in name:
        return "seeds"
    if parts[0] == "bnd":
        return "bnd"
    if parts[0] == "vtx":
        return "vtx-" + parts[1]
    return "num-" + parts[1][:1] if parts[1][0] in "ab" else "num-" + parts[1]


if __name__ == "__main__":
    main()
