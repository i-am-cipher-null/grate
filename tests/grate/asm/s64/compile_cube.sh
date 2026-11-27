#!/bin/sh
# Rebuild tools/cgc against NVIDIA's blob compiler and dump the cube shaders.
#
# The blob is not in the repository.  subprojects/cgdrv.wrap points at NVIDIA's
# Tegra20 L4T R16.5 package; this script fetches it, checks the wrap's SHA-256,
# extracts libcgdrv/libnvrm/libnvos into subprojects/Linux_for_Tegra, and
# configures a separate build with -Dcgdrv=enabled.  The main build is untouched.
#
# Usage: ./compile_cube.sh [<work-dir>]
#   <work-dir> defaults to $HOME/cgdrv (download, build and dumps)
#
# On the AC100 the compiler runs without the vendor kernel: CgDrv_Create prints
# "nvrm_init failed" (no /dev/nvmap) but fragment compilation still succeeds.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)

# This file lives either in probe_mfu/s64 or in tests/grate/asm/s64.
ROOT=
for d in "$HERE/../.." "$HERE/../../../.."; do
    if [ -f "$d/meson.build" ] && [ -d "$d/src/libgrate" ]; then
        ROOT=$(cd "$d" && pwd)
        break
    fi
done
[ -n "$ROOT" ] || { echo "cannot find the grate tree above $HERE" >&2; exit 1; }

WORK=${1:-${GRATE_CGDRV_WORK:-$HOME/cgdrv}}
URL=https://developer.download.nvidia.com/mobile/tegra/l4t/r16.5.0/ventana_release_armhf/Tegra20_Linux_R16.5_armhf.tbz2
SHA=02b7a83b29cec59cf275b98f0de0fc87a24ffa25593af6af729cd2e881c41587
TARBALL=$WORK/Tegra20_Linux_R16.5_armhf.tbz2
SP=$ROOT/subprojects/Linux_for_Tegra
BUILD=$WORK/build

mkdir -p "$WORK" "$SP" "$WORK/words"

if [ ! -f "$TARBALL" ]; then
    echo "downloading $URL"
    curl -fsSL -o "$TARBALL" "$URL"
fi

got=$(sha256sum "$TARBALL" | cut -d' ' -f1)
if [ "$got" != "$SHA" ]; then
    echo "$TARBALL: sha256 $got, expected $SHA" >&2
    exit 1
fi

# nvidia_drivers.tbz2 holds usr/lib/libcgdrv.so and the two libs it needs.
tar -xjf "$TARBALL" -C "$WORK" Linux_for_Tegra/nv_tegra/nvidia_drivers.tbz2
tar -xjf "$WORK/Linux_for_Tegra/nv_tegra/nvidia_drivers.tbz2" -C "$SP" \
    --strip-components=2 usr/lib/libcgdrv.so usr/lib/libnvrm.so usr/lib/libnvos.so

cat > "$SP/meson.build" <<'EOF'
project('cgdrv_prebuilt', 'c')
lib_cgdrv = meson.get_compiler('c').find_library('cgdrv',
    dirs : [meson.current_source_dir()])
cgdrv_dep = declare_dependency(dependencies : lib_cgdrv)
meson.override_dependency('cgdrv', cgdrv_dep)
EOF

meson setup --wipe "$BUILD" "$ROOT" -Dcgdrv=enabled
ninja -C "$BUILD" tools/cgc

export LD_LIBRARY_PATH=$SP${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
for src in "$HERE"/shaders/*.glsl; do
    name=$(basename "$src" .glsl)
    out=$WORK/words/$name.cgc.txt
    "$BUILD/tools/cgc" -F -v "$src" > "$out" 2>&1 || true
    printf '%-22s ' "$name"
    grep -E 'GR3D: TEX\[[0-9a-f]{3}\]:' "$out" | tr '\n' ' '
    echo
done

echo "dumps: $WORK/words"
