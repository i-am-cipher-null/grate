#!/bin/sh
# Cube-coordinate control on the AC100: does R reach the sampler at all?
#
# Two fragment programs share the same uniforms:
#   store-only  writes R and S/T through the ALU, so a varying R is visible
#   sample      samples the bound 2D texture with S/T from the varying and R
#               from posx*rslope+rbias (cube_coord_fs, next to this script)
#
# Each is run with R constant 0 and with R sweeping 0..1 across the frame.  If
# the store frames differ but the sample frames do not, R is ignored by a 2D
# binding.  That is the note-51/note-64 control, not a cube sample.
#
# Usage: ./hw_r_control.sh [<assembler>] [<frame-out-dir>]
#   assembler defaults to $GRATE_ASSEMBLER or $HOME/build/grate-exp/tools/assembler
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)

ROOT=
for d in "$HERE/../.." "$HERE/../../../.."; do
    if [ -f "$d/tests/grate/asm/filter_quad_vs.txt" ]; then
        ROOT=$(cd "$d" && pwd)
        break
    fi
done
[ -n "$ROOT" ] || { echo "cannot find tests/grate/asm above $HERE" >&2; exit 1; }

ASM=${1:-${GRATE_ASSEMBLER:-$HOME/build/grate-exp/tools/assembler}}
OUT=${2:-${TMPDIR:-/tmp}/s64-rcontrol}

VS=$ROOT/tests/grate/asm/filter_quad_vs.txt
LNK=$ROOT/tests/grate/asm/filter_quad_linker.txt
SAMPLE=$HERE/cube_coord_fs.txt
MVP='["mvp"]=(1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1)'

[ -x "$ASM" ] || { echo "no assembler at $ASM" >&2; exit 1; }
mkdir -p "$OUT"

cat > "$OUT/store_fs.txt" <<'EOF'
pseq_to_dw_exec_nb = 1
alu_buffer_size = 1

.uniforms
	[0] = "rslope";
	[1] = "rbias";

.asm
EXEC
	MFU:	ipl: t0.fp20, t0.fp20, NOP, NOP
	ALU:
		ALU0:	MAD r2.lh, posx, u0, u1
		ALU1:	MAD r3.lh, posy, u0, u1
	DW:	store rt1, r2, r3
;
EOF

# signature <log> -> commonest words over the frame
signature() {
    sed -n 's/^[0-9]*: 0x\([0-9A-F]*\) 0x\([0-9A-F]*\) 0x\([0-9A-F]*\) 0x\([0-9A-F]*\)$/\1\n\2\n\3\n\4/p' "$1" |
        sort | uniq -c | sort -rn | head -4 |
        awk '{printf "%s:%s ", $2, $1}'
}

run() { # <fs> <slope> <bias> <tag>
    "$ASM" --vs "$VS" --fs "$1" --lnk "$LNK" --vs_mat4 "$MVP" \
        --fs_uniform="[\"rslope\"]=$2" --fs_uniform="[\"rbias\"]=$3" \
        --texture --expected 0xDEADBEEF --testonly \
        > "$OUT/$4.out" 2> "$OUT/$4.err" || true
    printf '%-14s ' "$4"
    signature "$OUT/$4.err"
    echo
}

run "$OUT/store_fs.txt" 0          0   store_R0
run "$OUT/store_fs.txt" 0.00390625 -32 store_Rvary
run "$SAMPLE"           0          0   sample_R0
run "$SAMPLE"           0.00390625 -32 sample_Rvary

echo
if cmp -s "$OUT/store_R0.err" "$OUT/store_Rvary.err"; then
    echo "store frames identical: R did not vary, control invalid"
else
    echo "store frames differ: R varies per fragment"
fi
if cmp -s "$OUT/sample_R0.err" "$OUT/sample_Rvary.err"; then
    echo "sample frames identical: the 2D binding ignores R"
else
    echo "sample frames differ: R reaches the sampler"
fi
echo "logs: $OUT"
