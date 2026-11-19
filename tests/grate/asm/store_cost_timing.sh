#!/bin/sh
# Does storing once per group instead of once per batch cost less time?
#
# Four variants of the shipped filter_quad_fs are timed: a 256x256 textured quad,
# two batches, one sample.
#
#   base  the shipped shader: one store, in the second batch
#   alu   base plus three more ALU packets in the first batch - four times the
#         ALU work and the same stores, so it doubles as the sensitivity control
#   st2   base plus a store in the first batch: two stores at size 1
#   n2    st2 assembled at alu_buffer_size = 2, where only the group's first
#         batch can store, so this is the one-store shape the claim is about
#
# st2 against n2 is the answer; base against alu says whether the harness can
# resolve per-fragment work at all. Every variant is checked with --result before
# it is timed: 0x00000000 is the black texel a store wrote, while 0x4C4C4C4C is
# the clear colour a draw that rasterised nothing leaves behind, and such a draw
# times exactly like a real one.
#
# Usage: store_cost_timing.sh [rounds] [draws]
H=$HOME
B=$H/build/grate
D=$B/tests/grate/asm
A=tools/assembler
M='["mvp"]=(1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1)'
ROUNDS=${1:-6}
DRAWS=${2:-400}
LOG=/tmp/store_cost.log
cd "$B" || exit 1

# The four variants differ only in the first batch and in the buffer size.
mk() { # name size extra-alu first-store
	{
		printf 'alu_buffer_size = %s\npseq_to_dw_exec_nb = 1\n\n.uniforms\n' "$2"
		printf '\t[0] = "lod_bias";\n\n.asm\n'
		printf 'EXEC\n\tMFU:\tsfu: rcp r4\n'
		printf '\t\tmul0: bar, sfu, bar0\n\t\tmul1: bar, sfu, bar1\n'
		printf '\t\tipl: t0.fp20, t0.fp20, NOP, NOP\n\n'
		printf '\tALU:\n\t\tALU0:\tMAD r3.lh, u0, #1, #0\n'
		if [ "$3" = yes ]; then
			printf '\t\tALU1:\tMAD r4.lh, u0, #1, #0\n'
			printf '\t\tALU2:\tMAD r4.lh, u0, #1, #0\n'
			printf '\t\tALU3:\tMAD r4.lh, u0, #1, #0\n'
		fi
		if [ "$4" = yes ]; then
			printf '\tDW:\tstore rt1, r2, r3\n'
		fi
		printf ';\n'
		printf 'EXEC\n\tTEX:\ttxb r2, r3, tex0, r0, r1, r2, r3\n'
		printf '\tDW:\tstore rt1, r2, r3\n;\n'
	} > "/tmp/sc_$1.txt"
}

mk base 1 no  no
mk alu  1 yes no
mk st2  1 no  yes
mk n2   2 no  yes

# 2 batches, so the count is 2 at both sizes: one count is one batch.
one() { # name draws -> assembler output
	GRATE_POKE="0x520=0x20006002" GRATE_DRAW_COUNT=$2 timeout 90 \
		meson devenv -C "$B" $A \
		--vs $D/filter_quad_vs.txt --fs "/tmp/sc_$1.txt" \
		--lnk $D/filter_quad_linker.txt --testonly --texture --result \
		--vs_mat4 "$M" --fs_uniform '["u0"]=0.5' 2>&1
}

for f in base alu st2 n2; do
	r=$(one "$f" 1 | sed -n 's/^result \(0x[0-9a-fA-F]*\).*/\1/p' | head -1)
	if [ "$r" != "0x00000000" ]; then
		echo "$f: drew nothing (result ${r:--}), refusing to time it" >&2
		exit 1
	fi
done

# The order rotates by one config per round: with a fixed order the last config
# always measures warmest, which is worth several ms and reads like a win.
CFGS="base alu st2 n2"
: > "$LOG"
r=1
while [ "$r" -le "$ROUNDS" ]; do
	i=0
	while [ "$i" -lt 4 ]; do
		idx=$(( (i + r - 1) % 4 ))
		f=$(echo $CFGS | cut -d' ' -f $((idx + 1)))
		ms=$(one "$f" $DRAWS | \
			sed -n 's/.*elapsed_ms=\([0-9.]*\).*/\1/p' | head -1)
		if [ -z "$ms" ]; then
			echo "$f round $r: no elapsed_ms, run failed" >&2
			exit 1
		fi
		echo "$f $ms" >> "$LOG"
		i=$((i + 1))
	done
	r=$((r + 1))
done

median() { # name
	sort -n | awk '{ a[NR] = $1 } END {
		if (NR == 0) exit 1
		if (NR % 2) print a[(NR + 1) / 2]; else print (a[NR / 2] + a[NR / 2 + 1]) / 2
	}'
}

for f in base alu st2 n2; do
	printf '%-5s %s\n' "$f" \
		"$(awk -v n="$f" '$1 == n { print $2 }' "$LOG" | median)"
done | tee /tmp/store_cost.median

md() { awk -v n="$1" '$1 == n { print $2 }' /tmp/store_cost.median; }
awk -v a="$(md st2)" -v b="$(md n2)" -v c="$(md base)" -v d="$(md alu)" 'BEGIN {
	printf "store win (st2-n2):    %+.1f ms  (%+.1f %%)\n", b - a, (b - a) * 100 / a
	printf "extra store (st2-base): %+.1f ms\n", a - c
	printf "sensitivity (alu-base): %+.1f ms\n", d - c
}'
echo "raw rounds in $LOG"
