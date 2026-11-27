#!/bin/sh
# Run one identical fragment program from three different file paths, with the
# same vertex program, link and uniforms that produced the 212/219 split.
#
# Note 63: `cp-seed-c-s5` (path A) read 219 in three runs and the byte-identical
# `br-b1`/`q-dsc-v3-w4` (path B) read 212 in five runs.  The source hash, the
# uniforms and the vertex program are identical, so this script keeps everything
# fixed and varies only the path of the fs file.
set -e
A=/home/freebuff/build/grate-exp/tools/assembler
V=/home/freebuff/src/grate-exp/tests/grate/asm/vs_persp_quad.txt
L=/home/freebuff/src/grate-exp/tests/grate/asm/linker_vs_tests.txt
CP=/home/freebuff/src/grate-exp/characterization/s62-cp-20261127T202513Z/generated/variant-cp-seed-c-s5-1-fs.asm
BR=/home/freebuff/src/grate-exp/characterization/s63-bridge-20261127T202505Z/generated/variant-br-b1-1-fs.asm

run() {
    "$A" --vs "$V" --fs "$1" --lnk "$L" \
        --vs_uniform '["offset"]=(-1.75,-1.45,0.0,0.0)' \
        --vs_uniform '["wdelta"]=(3.0,0.0,0.0,0.0)' \
        --vs_uniform '["cred"]=(1,1.5,3,0.5625)' \
        --vs_uniform '["cgreen"]=(1,1.5,3,0.5625)' \
        --vs_uniform '["cblue"]=(1,1.5,3,0.5625)' \
        --result --testonly 2>&1 | grep -o '0x[0-9A-Fa-f]\{8\}' | head -1
}

echo "same content (sha256 $(sha256sum "$CP" | cut -d' ' -f1)), three paths:"
for i in 1 2 3; do
    printf '  cp-path   %s\n' "$(run "$CP")"
done
for i in 1 2 3; do
    printf '  br-path   %s\n' "$(run "$BR")"
done
cp "$CP" /tmp/s63_pathcopy-fs.asm
for i in 1 2 3; do
    printf '  /tmp copy %s\n' "$(run /tmp/s63_pathcopy-fs.asm)"
done
mkdir -p /home/freebuff/s63tmp
cp "$CP" /home/freebuff/s63tmp/other-name-fs.asm
for i in 1 2 3; do
    printf '  other dir %s\n' "$(run /home/freebuff/s63tmp/other-name-fs.asm)"
done
