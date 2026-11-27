#!/usr/bin/env python3
"""Compile the cube-coordinate matrix with NVIDIA's blob compiler and record it.

Six component permutations times eight sign combinations for a uniform and a
varying coordinate, plus axis/global scaling and a normalize control.  Every
source is written under shaders/, every unmodified cgc dump under words/, and
manifest.tsv ties the two together with SHA-256 hashes and a field-decoded MFU
summary.

The MFU decoder here is deliberately independent of libcgc's disassembler: a
packet holds two 64-bit instructions (two 32-bit words each), while the shader
dump indexes the second instruction one 32-bit word early.  See note 65.
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys

PERMS = ["xyz", "xzy", "yxz", "yzx", "zxy", "zyx"]
SIGNS = ["+++", "++-", "+-+", "+--", "-++", "-+-", "--+", "---"]

MUL_SRC = {0: "r0", 1: "r1", 2: "r2", 3: "r3", 10: "sfu",
           11: "bar0", 12: "bar1", 13: "#1"}
MUL_DST = {1: "bar", 4: "r0", 5: "r1", 6: "r2", 7: "r3"}
SFU_OP = {0: "nop", 1: "rcp", 2: "rsq", 3: "lg2", 4: "ex2", 5: "sqrt",
          6: "sin", 7: "cos", 8: "frc", 9: "preex2", 10: "presin",
          11: "precos"}
VAR_OP = {0: "nop", 1: "var1", 2: "var2"}

UNIFORM = """precision mediump float;
uniform samplerCube tex;
uniform vec3 dir;
void main()
{{
    gl_FragColor = textureCube(tex, {expr});
}}
"""

VARYING = """precision mediump float;
varying vec3 vdir;
uniform samplerCube tex;
void main()
{{
    gl_FragColor = textureCube(tex, {expr});
}}
"""

VARYING4 = """precision mediump float;
varying vec4 vdir;
uniform samplerCube tex;
void main()
{{
    gl_FragColor = textureCube(tex, {expr});
}}
"""


def operand(var, sign):
    return ("-" if sign == "-" else "") + var


def matrix_expr(var, perm, signs):
    comps = []
    for dst, src in enumerate(perm):
        comps.append(operand("%s.%s" % (var, src), signs[dst]))
    return "vec3(%s)" % ", ".join(comps)


def variants():
    out = []
    for perm in PERMS:
        for signs in SIGNS:
            out.append(("u_%s_%s" % (perm, signs), "uniform", perm, signs,
                        matrix_expr("dir", perm, signs)))
    for perm in PERMS:
        for signs in SIGNS:
            out.append(("v_%s_%s" % (perm, signs), "varying", perm, signs,
                        matrix_expr("vdir", perm, signs)))

    extra = [
        ("u_scale_x", "uniform", "vec3(2.0 * dir.x, dir.y, dir.z)"),
        ("u_scale_y", "uniform", "vec3(dir.x, 2.0 * dir.y, dir.z)"),
        ("u_scale_z", "uniform", "vec3(dir.x, dir.y, 2.0 * dir.z)"),
        ("u_scale_all2", "uniform", "dir * 2.0"),
        ("u_scale_all4", "uniform", "dir * 4.0"),
        ("u_scale_all05", "uniform", "dir * 0.5"),
        ("u_norm", "uniform", "normalize(dir)"),
        ("v_scale_x", "varying", "vec3(2.0 * vdir.x, vdir.y, vdir.z)"),
        ("v_scale_y", "varying", "vec3(vdir.x, 2.0 * vdir.y, vdir.z)"),
        ("v_scale_z", "varying", "vec3(vdir.x, vdir.y, 2.0 * vdir.z)"),
        ("v_scale_all2", "varying", "vdir * 2.0"),
        ("v_norm", "varying", "normalize(vdir)"),
        ("v4_xyz", "varying4", "vec3(vdir.xyz)"),
        ("v4_zyx", "varying4", "vec3(vdir.zyx)"),
        ("v4_wzy", "varying4", "vec3(vdir.wzy)"),
    ]
    for name, base, expr in extra:
        out.append((name, base, "-", "-", expr))
    return out


def decode_mfu(w0, w1):
    mul0 = (w0 & 0xf, (w0 >> 4) & 0xf, (w0 >> 8) & 7)
    mul1 = ((w0 >> 11) & 0xf, (w0 >> 15) & 0xf, (w0 >> 19) & 7)
    op = (w0 >> 22) & 0xf
    reg = (w0 >> 26) & 0x3f
    var = []
    for i in range(4):
        f = (w1 >> (7 * i)) & 0x7f
        var.append((f & 1, (f >> 1) & 3, (f >> 3) & 0x1f))

    def mul(m):
        return "%s=%s*%s" % (MUL_DST.get(m[2], "dst%d" % m[2]),
                             MUL_SRC.get(m[0], "s%d" % m[0]),
                             MUL_SRC.get(m[1], "s%d" % m[1]))

    ipl = ",".join("%s:v%d%s" % (VAR_OP.get(o, "op%d" % o), s,
                                 "_sat" if sat else "")
                   if o else "nop" for sat, o, s in var)
    return "sfu=%s r%d; %s; %s; ipl=%s" % (
        SFU_OP.get(op, "op%d" % op), reg, mul(mul0), mul(mul1), ipl)


def parse(text):
    d = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].startswith("shader binary:"):
        i += 1
    words = []
    if i < len(lines):
        m = re.search(r"shader binary: (\d+) bytes", lines[i])
        d["bin_bytes"] = int(m.group(1)) if m else -1
        for line in lines[i + 1:]:
            m = re.match(r"^\s+[0-9a-f]{8}: ([0-9a-f]{8}) \|", line)
            if not m:
                break
            words.append(int(m.group(1), 16))
    d["bin_sha256"] = hashlib.sha256(
        b"".join(w.to_bytes(4, "little") for w in words)).hexdigest()

    regs = {}
    code = []
    for m in re.finditer(r"^GR3D: (MFU|TEX|ALU|DW)\[([0-9a-f]{3})\]: ([0-9a-f]{8})$",
                         text, re.M):
        regs.setdefault(m.group(1), []).append(int(m.group(3), 16))
        code.append(m.group(1) + m.group(2) + m.group(3))
    d["code_sha256"] = hashlib.sha256("\n".join(code).encode()).hexdigest()
    mfu = regs.get("MFU", [])
    d["mfu_pairs"] = [decode_mfu(mfu[k], mfu[k + 1])
                      for k in range(0, len(mfu) - 1, 2) if mfu[k] or mfu[k + 1]]
    tex = regs.get("TEX", [])
    d["tex"] = " ".join("%08x" % w for w in tex if w)
    d["alu_text"] = [m.group(1).strip() for m in
                     re.finditer(r"\|    ((?:mad|min|max|csel|nop)[^|]*)$",
                                 text, re.M)]

    # Row writes emitted by the blob before the MFU word (uniform path).
    seeds = {}
    for m in re.finditer(r"mad r([0-3])\.hl, ([^,]+), #1, #0", text):
        seeds[int(m.group(1))] = m.group(2)
    d["seeds"] = ";".join("r%d<-%s" % (k, seeds[k]) for k in sorted(seeds))
    m = re.search(r"0: (?:highp|mediump|lowp) vec3 (\w+).*?\n(?:.*\n){0,3}?"
                  r"\s+location: (0x[0-9a-f]+)", text, re.S)
    d["var_location"] = m.group(2) if m else "-"
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cgc", default=os.path.expanduser("~/l4t/build/tools/cgc"))
    ap.add_argument("--libs", default=os.path.expanduser(
        "~/src/grate-exp/subprojects/Linux_for_Tegra"))
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)))
    args = ap.parse_args()

    shadir = os.path.join(args.out, "shaders")
    worddir = os.path.join(args.out, "words")
    os.makedirs(shadir, exist_ok=True)
    os.makedirs(worddir, exist_ok=True)

    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = args.libs + (
        ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")

    rows = []
    for name, base, perm, signs, expr in variants():
        if base == "uniform":
            template = UNIFORM
        elif base == "varying4":
            template = VARYING4
        else:
            template = VARYING
        src = template.format(expr=expr)
        path = os.path.join(shadir, name + ".glsl")
        with open(path, "w") as f:
            f.write(src)
        sha = hashlib.sha256(src.encode()).hexdigest()

        p = subprocess.run([args.cgc, "-F", "-v", path], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True)
        dump = p.stdout
        with open(os.path.join(worddir, name + ".cgc.txt"), "w") as f:
            f.write(dump)
        want = "compiling fragment shader"
        if p.returncode != 0 or want not in dump or "0 errors" not in dump:
            print("FAILED %s (status %d)" % (name, p.returncode),
                  file=sys.stderr)
            rows.append((name, base, perm, signs, sha, "FAIL", "", "", "",
                         "", ""))
            continue

        d = parse(dump)
        rows.append((name, base, perm, signs, sha, d["bin_sha256"],
                     d["code_sha256"], "%d" % d.get("bin_bytes", -1),
                     d["seeds"], " | ".join(d["mfu_pairs"]), d["tex"]))

    hdr = ["variant", "base", "perm", "signs", "src_sha256", "bin_sha256",
           "code_sha256", "bin_bytes", "seed_rows", "mfu", "tex_nonzero"]
    with open(os.path.join(args.out, "manifest.tsv"), "w") as f:
        f.write("\t".join(hdr) + "\n")
        for r in rows:
            f.write("\t".join(r) + "\n")

    failed = [r[0] for r in rows if r[5] == "FAIL"]
    print("%d variants, %d failed" % (len(rows), len(failed)))
    if failed:
        print("failed: " + " ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
