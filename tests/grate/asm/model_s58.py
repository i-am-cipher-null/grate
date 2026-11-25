#!/usr/bin/env python3
"""Score note 58's transfer-function model against a manifest's results.

Usage: model_s58.py <results.tsv> <manifest.json> [name substring]

The model under test, for the sfu selectors 5, 6 and 7 (row 1, 2 and 3):

    S = sign(v) * m * 2**K,   m = |v| / 2**floor(log2 |v|)   (so 1 <= m < 2)
    K = 32 if |v| < 2 else 16

i.e. the row's significand is kept and its exponent is replaced by a fixed
2^32 or 2^16, the choice being the single bit "|v| >= 2".  The predicted store
byte is 0xF7 + 8*log2(result), with rcp -> 1/S, rsq -> S**-0.5, a negative
result storing 00, 2.0 or more clipping to 0xFF, and rsq of a negative operand
storing FF (measured: the invalid result is not the 00 a negative stores as).
The store truncates, so a residual of one code step is the quantisation of the
readback, not a model error.

For every variant whose sfu field names r5, r6 or r7 the row value comes from
the manifest's own replacement (`@V1@` for r5, `@V2@` for r6, `@V3@` for r7),
so the script cannot drift from the generator. Variants whose sfu field names
anything else, and variants with a multiplier gain, are skipped.
"""

import json
import math
import sys


def load_results(path):
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
            rows.setdefault(row["name"], set()).add(int(row["result"], 16))
    return rows


def predict(v, op):
    if v == 0.0:
        return None
    m = abs(v) / 2.0 ** math.floor(math.log2(abs(v)))
    scale = 32.0 if abs(v) < 2.0 else 16.0
    s = math.copysign(m * 2.0 ** scale, v)
    if op == "rcp":
        r = 1.0 / s
    else:
        # rsq of a negative is not a real result: the unit stores FF, not 00
        if s < 0.0:
            return 0xFF
        r = s ** -0.5
    if r == 0.0 or r < 0.0:
        return 0x00
    code = 0xF7 + 8.0 * math.log2(r)
    return max(0, min(0xFF, int(code)))


def main():
    results_path, manifest_path = sys.argv[1], sys.argv[2]
    needles = sys.argv[3:]
    manifest = json.load(open(manifest_path))
    results = load_results(results_path)
    scored = exact = within = 0
    for v in manifest["variants"]:
        name = v["name"]
        if needles and not any(n in name for n in needles):
            continue
        fs = v["replace"]["fs"]
        sfu = fs["@SFU@"].split()
        if len(sfu) != 2 or sfu[1] not in ("r5", "r6", "r7"):
            continue
        row = int(sfu[1][1:]) - 4
        value = float(fs["@V%d@" % row])
        want = predict(value, sfu[0])
        words = results.get(name)
        if want is None or not words or len(words) != 1:
            continue
        code = (next(iter(words)) >> 8) & 0xFF
        scored += 1
        if code == want:
            exact += 1
            within += 1
        elif abs(code - want) <= 1:
            within += 1
        else:
            print("%-34s row %d = %-18s %s want %02X got %02X (%+d)"
                  % (name, row, fs["@V%d@" % row], sfu[0], want, code,
                     code - want))
    print("scored %d cells: %d exact, %d within one code step"
          % (scored, exact, within))


if __name__ == "__main__":
    main()
