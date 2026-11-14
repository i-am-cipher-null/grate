# Tegra 2 (AR20) - hardware testing

**Status.** Working notes and procedures. Every table was produced on a Tegra 2
device with the Grate assembler; results that turned out to be probe artefacts are
kept with the reason, because the same mistakes recur.

**Scope.** How to run assembled programs on the hardware, the environment
variables and register pokes, and the traps that have produced wrong conclusions.
The instruction-level results live in [[Fragment Shader ISA]] and
[[GR3D Registers]].

## Running the assembler on hardware

`tests/grate/asm_tests.sh` drives `tools/assembler`, which assembles a test
program and actually runs it on the GPU, then compares the first framebuffer
pixel against `--expected`. This makes the assembler usable as an experiment
platform: change an instruction, observe what the hardware does.

### Non-root access

The assembler opens a DRM node in `src/libhost1x/host1x-drm.c`. It only tried
`/dev/dri/card0` (`root:video 0660`), so it needed root or membership in `video`.
For headless work the render node `/dev/dri/renderD128` (`root:render 0660`) is
sufficient: buffers and submits use GEM ioctls
(`DRM_IOCTL_TEGRA_GEM_CREATE`, `DRM_IOCTL_TEGRA_GEM_MMAP`), which render nodes
allow. `DRM_IOCTL_MODE_CREATE_DUMB` appears only in the L4T/nvhost back end, not
in the DRM one.

Two changes are needed to run without root:

1. Fall back to `/dev/dri/renderD128` when `card0` cannot be opened. Without
   this, Grate falls back to the dummy interface and reports
   "Kernel driver interface undetected".
2. Skip `framebuffer_init` when no display was created. `drm_framebuffer_init()`
   calls `drmModeAddFB2WithModifiers()`/`drmModeAddFB2()`, which are mode-setting
   ioctls and fail with `EACCES` (`-13`) on a render node. A DRM framebuffer is
   only needed for scanout, so registration can be skipped when
   `host1x->display` is NULL - which is what the X11 back end already does by
   setting `framebuffer_init = NULL`.

Without (1) and (2) the run dies with
`host1x_framebuffer_create: framebuffer_init failed: -13`.

### UAPI detection is not reliable

`host1x_drm_open()` sets `drm->new_uapi` from `ver->version_major == 1`. That is
the generic DRM version, and the tegra driver reports `1.0.0` (see the `tegra
1.0.0` line in dmesg and `drm 1.1.0` in `/sys/class/drm/version`), so the test is
true for every tegra DRM release and carries no information about whether the
channel UAPI is implemented. It happens to select the new path on this kernel,
but it would also select it on a kernel that only has the downstream UAPI.

### The new UAPI on an upstream 7.2.0 kernel

`7.2.0-gd1efa725955b` implements the new channel UAPI in full, at the documented
numbers: `CHANNEL_OPEN` 0x10, `CHANNEL_CLOSE` 0x11, `CHANNEL_MAP` 0x12,
`CHANNEL_UNMAP` 0x13, `CHANNEL_SUBMIT` 0x14. `OPEN` returns the expected
`version` (0x20 for both gr2d and gr3d) and `capabilities`.

What breaks submission on this kernel is that Grate's bundled
`src/libhost1x/tegra_drm.h` is an *older revision of that same UAPI*. The ioctl
numbers only encode struct sizes, and map/unmap kept theirs, so nothing fails at
the ioctl-number level - the kernel simply reads the fields at the wrong offsets:

| struct | old (Grate) | current (kernel / Mesa) |
|:--|:--|:--|
| `channel_open` | 5 fields | 6: `context`, `version`, `capabilities`, `padding` |
| `channel_map` | `channel_ctx, flags, handle, mapping_id` | `context, handle, flags, mapping` |
| `channel_unmap` | `channel_ctx, mapping_id` | `context, mapping` |
| `channel_submit` | `syncpt_incr{id, num_incrs, fence_value}` | `syncpt{id, increments, value}` |

The fix is to replace Grate's copy with the current UAPI - Mesa's
`include/drm-uapi/tegra_drm.h` is byte-identical to the installed
`/usr/include/drm/tegra_drm.h` on this kernel - and rename the users. Only
`src/libhost1x/host1x-drm.c` references the old names.

A second, independent bug produced the `EINVAL` that looked like an ABI
mismatch: `drm_bo_map()` never set `args.flags`, so it passed zero, and the
kernel rejects a mapping that requests neither read nor write access. It needs

```c
args.flags = DRM_TEGRA_CHANNEL_MAP_READ_WRITE;
```

A third bug hid all of this: `drm_channel_submit2()` computed an error from the
submit ioctl and then returned 0 unconditionally, so submit failures were
swallowed. It now returns the real error.

With those changes the whole assemble-and-run suite exercises the GPU:

```
$ cd ~/build/grate && sh tests/grate/asm_tests.sh
All tests passed
```

Every run still logs `****nvrm_init failed****` and
`ERROR: host1x_drm_display_init: drm_display_create() failed: -19`. Both are
harmless: the blob compiler is unavailable, and there is no X server, so display
init fails and the assembler continues headless. `tools/assembler` has no
`--nodisplay` option of its own; `--testonly` is enough.

`asm_tests.sh` resolves the assembler as `$DIR/../../tools/assembler` relative
to its own directory, so it must be run from - or copied into - the build tree
where `tools/assembler` exists.

The build tree keeps its **own copy** (`~/build/grate/tests/grate/asm_tests.sh`),
so an edit under `src/grate/tests/` changes nothing until it is copied over. That
is worth remembering because the stale copy cheerfully reports `All tests passed`
for a file that no longer parses, or for a case that was never added: copy first,
then `sh -n` the result. To prove a new case actually executes, break its
`--expected` on purpose - the suite must start failing with `exit=1` - then
restore it. A suite that passes both ways is not running the case.

### Stage order inside an `EXEC` block is enforced

The grammar only accepts the units in pipeline order - PSEQ, MFU, TEX, ALU, DW.
Writing the `ALU:` block before `TEX:` is a parse error (`fs: line N: syntax
error`), even though they are separate hardware units. Two consequences:

* a probe that puts `ALU:` first never runs at all: the assembler exits 1 and
  `--result` prints nothing, which is very easy to mistake for "the shader ran
  and rendered black". Always check the exit status, not just the pixel;
* it corroborates the scheduling finding in
  [writing-a-compiler.md](writing-a-compiler.md) - a TEX cannot be fed by the
  ALU in the same batch, because the ALU stage runs after the TEX stage. Texture
  coordinates have to come from the previous batch or from an MFU `ipl` fetch,
  which is exactly the shape of `filter_quad_fs.txt`.

## Raw instruction-word experiments

The bit fields no mnemonic can reach are swept one bit at a time with raw words.
Three sweeps close the remaining "unknown" ranges of the fragment ISA, and each
is only trusted together with its controls, because a bit sweep on an insensitive
sample reports every bit as inert:

| probe | word | control that proves the sample is sensitive | result |
|:--|:--|:--|:--|
| `probe_pseq_fetch.sh` | PSEQ fetch `0x00810008` | batch 0 and batch 1 leave different values in `r0/r1` | bits 3 and 23 both required, bit 24 must be clear; 20..22, 25..31 inert |
| `probe_pseq_bits.sh` | PSEQ fetch | same round trip | bits 4..15 inert |
| `probe_tex_unknown.sh` | TEX sample `0x00001420` | bit 4 and bit 5 flips, and `s = 0.1`, all change the result | bits 6..9, 11, 14..31 inert for a 2D colour texture |
| `probe_dw_high.sh` | DW `0x00028001` | the payload reads back as `0x4C4C4C4C` | bits 18..31 inert on the colour target |
| `probe_dw_high_surfaces.sh` | DW depth `0x00010001`, stencil `0x00020409` | the base words store `0x300C` and `0x7F` | bits 18..31 inert on both |
| `probe_mfu_unknown.sh` | MFU word `0x104006D0`/`0x00000000` | mul0 sources 0..3 reproduce `r0..r3` (`0x1A`, `0x33`, `0x4D`, `0x66`) | pad bits 28..31 inert; sources 4..9/14/15 are neither row nor global registers |
| `probe_mfu_globals.sh` | same | globals primed and cleared give different source values if 4..7 alias them | 4..7 are byte-identical either way, so they do not |

The MFU and payload probes also show two traps. Priming with `.lh` leaves a
value-dependent residue byte, which made a first MFU run report a constant
`0xEB` in a byte that had nothing to do with the multiplier - prime with `.l*`
and store single halves when the question is *where* a value lands. And the
colour payload order is `r2.lo`, `r2.hi`, `r3.lo`, `r3.hi` in bytes 0..3, which
an earlier `store rt3` run had backwards because the coverage target drops that
register pair entirely.

The PSEQ sweep needs a round trip rather than a readback, because a fetch that
does nothing and a fetch that returns zero both leave zeros in the destination
registers: the program stores a known value in batch 0, overwrites the registers
in batch 1 without storing, then fetches and stores in batch 2. A working fetch
reproduces batch 0's byte pattern, a dead word reproduces batch 1's.

`scripts/grate-probe.sh` is the one-draw driver these probes share. It ships the
command as a script instead of pasting it into the ssh line, since argument
values like `--vs_mat4 '["mvp"]=(1,0,...)'` do not survive two layers of
quoting. It takes `VS_FILE=<path>` to override the vertex program, and
`--expected 0x...` to force a full frame dump instead of one pixel.

**`--vs_mat4` does nothing with the default vertex program.** `vs_mov` copies the
attribute to the export and never touches the matrix, so a probe that mirrors or
scales the draw silently runs the default geometry; only `filter_quad_vs` (which
multiplies by `c[0..3]`) applies it. Combined with `--expected`, that is how to
tell "the shader produced this" from "the geometry never covered the pixel".

**`GRATE_FRONT_CW=1`** calls `grate_3d_ctx_set_front_direction_is_cw(ctx, true)`
instead of `false`. It changes the *state*, so it only exercises things that read
the state - one-sided stencil, culling - and **it does not change what the
fragment program sees in register 75**; that register carries the raw winding.

To exercise the opposite winding, mirror the geometry: `VS_FILE` set to
`filter_quad_vs` (the only vertex program that applies `mvp`) with
`--vs_mat4 '["mvp"]=(-1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1)'`. The same pixels are then
covered by an oppositely wound triangle, which is the only way to observe a bit
that follows the primitive rather than the state.

**Read special registers through the half they live in.** Registers 74 and 75
have no fp20 read - `pface`, `cov`, `r74` and `r75` all return `0` - and must be
written `pface.l` / `cov.l`. Registers 72 and 73 have an fp20 read, but the value
sits in the high half (`r72.h`, not `r72.l`). A probe that reads the wrong half
reports a constant zero, which looks exactly like a register the hardware does
not drive.

`tools/assembler` takes `--result`, which always prints the result word:

```
result 0xFF00FF00
```

Without it you only see the word when `--expected` mismatches, and that path also
dumps the whole framebuffer.

The `DW` unit accepts a raw 32-bit word, so single-bit experiments are a text
edit rather than binary patching. The lexer token is `DW:` **including the
colon** - the colon is part of the mnemonic, so the line is:

```
	DW:	0x00028005
```

Writing `DW 0x...` fails with `fs: line N: syntax error`, because a bare `DW`
is not a token. Results are deterministic: the same shader run three times in a
row returns the same word.

## Texture row addressing: the sampler has no pitch field

`grate_3d_set_texture_desc()` writes the format, the filter bits, `MAX_LOD` and
`WIDTH_LOG2`/`HEIGHT_LOG2` - and **no pitch**. The texture unit derives the row
stride as `width * bpp`, so the allocation has to be tightly packed.

Seeing this needs a texture whose rows differ: with a two-tone image every wrong
row still reads the right colour. `tools/assembler` colours rows `B, G, R, W` in
a 4-cycle when `GRATE_TEX_ROWCOLOR` is set, `GRATE_TEX_SIZE=WxH` overrides the
size, `GRATE_TEX_PITCH=n` overrides the allocated pitch, and
`GRATE_TEX_ROWCHECK` gives white/black stripes instead.

Sampling the centre of each row, `s = 0.7`:

| texture | width*bpp | allocated pitch | rows read |
|:--|--:|--:|:--|
| 4x16  | 16 | 16 | `B G R W B G R W` - correct |
| 4x16  | 16 | 32 | `B . G . R . W .` - row `y` returns row `y/2` |
| 4x16  | 16 | 64 | rows 0 and 4 only - row `y` returns row `y/4` |
| 8x16  | 32 | 32 | correct |
| 8x16  | 32 | 64 | row `y` returns row `y/2` |
| 16x16 | 64 | 64 | correct |
| 16x16 | 64 | 128 | row `y` returns row `y/2` |

Doubling the pitch halves the sampled row index, so the stride is `width * bpp`
and any padding in the pitch is invisible to the sampler. `grate_create_texture()`
used to allocate `ALIGN(width * bpp, PIX_BUF_FORMAT_ALIGNMENT(format))` - 64 bytes
for `RGBA8888` - so a 4x4 texture had its 64 bytes of data read as if the rows
were 16 bytes apart. That is why the shipped 4x4 `filter_quad` texture only ever
showed its first row and the rest of the frame came out black.

The fix is to allocate a tight pitch. With it, 4x4, 4x16, 8x16 and 16x16 read
every row correctly, and `filter_quad` renders the exact two-tone frame at 4x4
with no mip chain. A bound mip chain is laid out by `setup_lod_pixbuf()` with
`ALIGN(width * bpp, 16)` per level, which is tight for the sizes that matter, so
`--mipmap` hid this bug rather than working around anything about mip selection.

## Block-compressed textures

`GRATE_TEX_FORMAT=dxt1` or `=etc1` binds a block-compressed texture instead of
`RGBA8888`. The harness fills block row `b` with one solid colour, cycling red,
green, blue and white like `GRATE_TEX_ROWCOLOR`, so block-row addressing is
directly readable. Dimensions must be multiples of 4 and `--mipmap` is refused
(gr2d cannot filter a compressed surface). `GRATE_TEX_PITCH` still applies, and
the block data is laid out at the pixelbuffer's pitch, so a pitch override is a
clean comparison instead of a gr2d blit of compressed data.

A 16x16 texture, `s = 0.1`, one sample per texel row:

| t | texel row | rgba8888 | dxt1 | etc1 |
|:--|--:|:--|:--|:--|
| 0.03125 | 0 | red | red | red |
| 0.09375 | 1 | green | red | red |
| 0.15625 | 2 | blue | red | - |
| 0.21875 | 3 | white | red | - |
| 0.34375 | 5 | - | green | green |

The block formats repeat every four rows because one block covers 4x4 texels,
and the derived stride `width * bpp / 4` is exactly the tight block pitch, so
compressed textures needed no driver change. DXT1 samples return alpha `0xFF`;
ETC1 is off by one or two in a channel. `GRATE_TEX_ZERO=1` uploads an all-zero
surface instead, which is how a read into the padding can be identified: DXT1
decodes a zero block to (0,0,0) and ETC1 to (2,2,2) (`0xFF020202`).

With the sampler striding 32 bytes regardless of the pitch, block row 3 is at
byte 96, so at pitch `p` it reads data block row `96/p`:

| pitch | 32 | 48 | 64 | 96 |
|:--|:--|:--|:--|:--|
| dxt1 | white (row 3) | blue (row 2) | black (padding) | green (row 1) |
| etc1 | white (row 3) | blue (row 2) | `0xFF020202` (padding) | green (row 1) |

## Timing a draw loop

`GRATE_DRAW_COUNT=n` draws the same quad `n` times and prints
`draws=n elapsed_ms=...`, timed from before the first submit to after the
readback sync. The line only appears for `n > 1`, so a timing sweep that forgets
the variable produces no numbers at all - and the `nvrm_init failed` / `Cannot
open display` chatter every run prints is not an error, the tool falls back to
the DRM path. That is wall time including CPU submission, so use it for
differences between two shaders, not absolutes: at 100 draws of a 256x256 quad
the floor is ~1.25 ms/draw of driver cost, and 12 ALU packets add 0.83 ms as 4
batches or 3.0 ms as 12. Empty batches are not free - 8 batches holding only a
disabled TEX word cost the same as 8 batches with an ALU packet each, which is
what `fill_4b_12p_8nop.txt` next to `fill_12b_12p.txt` measures
(`tests/grate/asm/perf/`).

Per-unit cost falls out of the same setup, by giving the first 12 of 13 batches
different work. Against an empty baseline of 4.67 ms/draw: 3 ALU packets 4.37,
1 SFU word 4.50, 2 SFU words 7.26, 3 SFU words 10.31, 1 SFU + 3 ALU 4.52,
1 TEX sample 4.45. Two things follow: the units overlap, so a batch costs the
maximum over them rather than the sum; and the *first* SFU word in a batch is
free while each further one costs ~3.4 ns per fragment. An MFU word carrying no
SFU op (`ipl` only) is free, so it is the SFU pipeline that costs.

All eleven SFU ops measured (`rcp`, `rsq`, `lg2`, `ex2`, `sqrt`, `sin`, `cos`,
`frc`, `preex2`, `presin`, `precos`, three words per batch) land within 1.5% of
each other, so a `PRE`+main pair costs two slots.

**Order drift fakes register effects.** Runs get faster as a sweep proceeds, so a
sequential poke sweep reports every later value as "faster than base". The FDC
block ([[GR3D Registers]] 11.1) first looked like a 12% win on the ALU path for
exactly this reason; repeating base at the end of that sweep moved it by as much
as any poke did. Time it A/B/A/B: alternate base and the suspect value four
times, take medians, and repeat base last. A difference counts only if it is
larger than base's own spread, which was 10-28 ms across 100 draws here.

Note that the **GPU clock cannot be read as non-root on this system**: there is
no `/sys/kernel/debug` at all, no `clk_summary`, no `/sys/devices/*/clocks`, and
`dmesg` has no GPU clock line. Timings therefore stay in milliseconds, and any
cycle figure has to name its assumed clock.

## Dumping the words the assembler emits

`GRATE_DUMP_RAW=1` prints the raw vertex, fragment and linker word streams in
`--testonly` mode. That is how to get the word to base a raw-instruction
experiment on. The TEX word for `txb r2, r3, tex0, r0, r1, r2, r3` is
`0x00001420` - sampler index in bits 3:0, destination select bit 5, enable
bit 10, LOD bias bit 12. The sampler field is the index itself, so a hand-written
`0x...1` samples `tex1` and returns whatever an unbound sampler returns
(constant garbage), not the texture you bound to `tex0`.

## Extra render targets and textures

`GRATE_RT5=1` allocates a linear surface at index 5 (the `gl_FragData[1]`
target), binds it, enables it, and prints its first words as `rt5: ...`.
`GRATE_RT5_FMT=` picks the format - `rgba8888` (default), `rgb565`, `a8` or
`l8` - and the print follows it: `rt5: %08X` x4 for 32-bit, `rt5(16):` eight
16-bit words, `rt5(8):` sixteen bytes. That is how the fx10-to-target-format
conversion table in [[Fragment Shader ISA]] was measured. `GRATE_RT4=1` binds
index 4 the same way with `RGBA8888` and prints `rt4:`. `GRATE_TEX2=1` binds a
second, blue `RGBA8888` texture as `tex1`, which is what proved that TEX bits
0..2 select the sampler.

The `rt4:` line also reports `nz=` (nonzero words in the whole surface),
`first=` (the first nonzero word index) and `wf=`/`wf1=` (the words at `first`
and `first+1`), which is what makes a *spill* write visible at all - a spill
store lands at a slot/offset, not at word 0.
`GRATE_RT_LIMIT=1` additionally programs `0xe30 + i` with the end address of
each surface, the companion register ardrv pushes and Grate's XML never named.
The spill sweep that used them is a raw `DW: 0x...` word; the plain
`store rt4, r2, r3` mnemonic emits `0x00028011` (mode 1, index 4), so bits 1:0
choose the spill mode and bits 10..16 the slot.

## Depth buffer readback

**Resolved: `store rt0` was a colour store, and the pair field does not name the
depth payload.** A plain `store rt0, r2, r3` emits `0x00028001` - mode 1, index
0, pair `r2/r3`, class bits 16..17 = 2, i.e. the *colour* class - which is why
the depth surface used to read back zeros. The assembler now takes a class
suffix, so `DW: store rt0, r0, r1 class 1` emits `0x00010001` and does write
depth. Two things to keep straight when re-running it:

* a class-1 store reads its payload from the register of the pair that bit 15
  does **not** select, and only from that pair's high register: `store rt0, r0,
  r1 class 1` reads `r3`, `store rt0, r2, r3 class 1` reads `r1`. Putting the
  depth in `r3` and using the `r0, r1` spelling is the combination that works;
* the payload is scaled: `r3.lo` contributes `value * 0x10`, `r3.hi` contributes
  `value * 0x4000`, superposed. `r3.lo = 1.0, r3.hi = 0.5` reads back `0x2010`.

See the fragment shader ISA document, section 8.2, for the measurement table.

`GRATE_DEPTH=1` makes `tools/assembler` allocate a `D16_LINEAR` depth surface the
size of the framebuffer, bind it, enable the depth test and the depth write with
`DEPTH_FUNC_ALWAYS`, and print a few words of it as
`depth16 row0: 8000 8000 8000 8000 mid: ... last: ...`. It is env-gated, so the
suite is unaffected. Without it the tool binds only the colour target, which is
why the depth side of `DW` stayed unmeasured for so long.

Two things to know before using it:

* the depth surface **is** written by the rasterizer even when the fragment
  program has no `DW` at all - a shader that stores nothing still leaves it at the
  interpolated `z` (`0x8000` right across the frame for a full-screen quad at
  `z = 0.5`) while the colour target keeps the clear value. Depth writes are not
  gated by the shader's store;
* so a shader-side depth write is only visible with a payload that is not
  `0x8000`. `0.25` works: it encodes to `0x4000` as `D16` and to `0x00400040` as
  `RGBA8888`, so one run can tell which surface was touched.

`GRATE_STENCIL=1` does the same for an `S8` stencil surface, printing
`stencil8 row0: ... mid: ... last: ...`, and `GRATE_STENCIL_TEST=1` additionally
enables the stencil test on **both faces** with `ALWAYS`/`REPLACE` plus
`keep/keep/replace` ops, and `GRATE_STENCIL_REF=n` sets the reference value.
**Set a non-zero ref**: an untouched zero buffer is indistinguishable from a
successful `REPLACE` with ref 0 - ref `0x7F` or `0xC8` is what proves the write.
`GRATE_STENCIL` is the variable that *allocates* - setting only
`GRATE_STENCIL_TEST` prints no `stencil8` line, which reads exactly like "the
stencil path works but writes nothing".

With the surface really bound, the result depends on whether the program can
discard:

| program | test | `stencil8` |
|:--|:--|:--|
| `DW: store rt1`, no kill write | `ALWAYS`, ref `0x7F` | `7F` - fixed-function update |
| `DW: store rt1`, `kill = 0` | `ALWAYS`, ref `0x7F` | `00` - fixed-function update gone |
| `DW: store stencil`, `kill = 0` | `ALWAYS`, ref `0x7F` | `7F` - the store commits it |
| `DW: store stencil`, `kill = 0` | off | `00` - nothing to commit |
| `DW: store stencil`, `kill = 1` | `ALWAYS`, ref `0x7F` | `00` - no fragment survives |

The readback is sound and `DW: store stencil` (`0x00020409`) is the *late*
stencil update: it is required, and it does the work, as soon as the program
writes the kill register. The `kill = 0` rows are the ones that isolate it -
nothing is discarded, yet the fixed-function update has already stopped. A tiled
stencil surface (`GRATE_STENCIL_TILED=1`, `PIX_BUF_LAYOUT_TILED_16x16`) behaves
identically to a linear one, and the coloured-output column is independent: the
control shaders read `0x00400040` in every row.

**Late test with `discard`: depth still happens, stencil moves into the shader.**
A `kill` write sets `discards_fragment`, which flips `0x40f` from `0x48` to `0x60`
and clears `STENCIL_WRITE_EARLY`. From the `depth16` / `stencil8` readouts:

| program | `depth16` | `stencil8`, ref `0x7F` |
|:--|:--|:--|
| colour store only | `8000` everywhere | `7F` everywhere |
| colour store + `kill = 0` | `8000` everywhere | `00` everywhere |
| `kill = 0` + `store stencil` | `8000` everywhere | `7F` everywhere |
| `kill = 1` (discards all) + `store stencil` | `8000` everywhere | `00` everywhere |

Depth is written early in every case, so a discarded fragment still stamps its
interpolated `z`. Stencil moves behind the shader: a kill write - even a constant
zero - stops the fixed-function update, and `DW: store stencil` is what performs
it. The blob's four-batch shape replays end to end once the coverage target is
bound: batch 1 fills `rt3` (`nz=65536`), batch 2 leaves `rt2` at the reference
value, so the sequence is both required and sufficient.

**The coverage target works and has a readout.** `GRATE_COVERAGE=1` binds an
`RGBA8888` surface at render target 3 (`GRATE_COVERAGE_TILED=1` for
`TILED_16x16`) and prints `cov3: ... nz=... first=...` like the `rt4` readout. A
`DW: store rt3, r0, r1` with `r0 = r1 = 0.5` fills all 65536 words with
`EF00EF00`; the store is dropped for a fragment the shader discards, exactly like
the colour store. That closes the last unmeasured surface of the discard path.

**The coverage target has no value format of its own.** Same shader, same
uniform, differing only in the target index, over 20 values and both write forms:
`cov3` equals the colour word in every run. The `EF00EF00` above is not a
coverage encoding - it is what an `.lh` (raw fp20) write looks like on *any*
target, colour included:

| x | `store rt1` (`result`) | `store rt3` (`cov3`) |
|--:|:--|:--|
| 0.01 | `C1F4C1F4` | `C1F4C1F4` |
| 0.25 | `E700E700` | `E700E700` |
| 0.5 | `EF00EF00` | `EF00EF00` |
| 0.9 | `F5FFF5FF` | `F5FFF5FF` |
| 3.0 | `FF00FF00` | `FF00FF00` |
| 0.25, `.l*` | `00400040` | `00400040` |
| 0.5, `.l*` | `007F007F` | `007F007F` |

So `rt3` is an ordinary target: bind any format, store to it with `.l*`, and it
behaves like colour. The decode of the raw-fp20 byte is in [[Fragment Shader ISA]]
section 12.7. Two more facts come out of the same runs: the value is
deterministic (the same `0.7` written by `MAD`, by `MAD` with a second uniform and
by `MUL` gives the same word, repeatably, over the whole frame - `nz = 65536`),
and a store writes **16 bits per source register** - `r2 = 0.5`, `r3 = 0.125`
stores `DF00EF00`, so the first register lands in `R`/`G` and the second in
`B`/`A`.

**Nothing reads the coverage target back.** The surface is straightforward to
fill and just as straightforward to prove dead. Source operands accept any
hardware register number, so the coverage special - hardware register 74, which
`fragment_asm.h` has no name for - is reachable as `r74`:

```
ALU0: MAD r2.l*, r74, #1, #0      ; coverage, converted to an 8-bit channel
ALU1: MAD r3.l*, r74, #1, #0
DW:   store rt1, r2, r3
```

| configuration | `r74` read as |
|:--|:--|
| full-screen quad, three read scales | `0` |
| quad scaled to half the frame (edge pixels present) | `0` |
| after the program stores `0.5` into `rt3` | `0` |
| after the program stores `1.0` into `rt3` | `0` |
| `posx` through the same operand (control) | `0x7F .. 0x83` gradient |

`posx` through the identical shader shape reads a proper per-fragment gradient
(`0.5` at the frame origin, rising by one LSB per 256 columns at a `2^-14`
scale), so the operand path and the scaling are sound and the zeros are real. A
program that stores nothing leaves a bound `rt3` at `nz = 0` as well, so the
rasterizer does not fill it either. Conclusion: `rt3` is a write-only record of
the discard decision, and AR20 exposes no primitive/sample coverage to the
shader.

**The coverage target can be read back with `PSEQ`.** `PSEQ: 0x00830008` (fetch
form, render target 3 in bits 16..19; the word is `0x00800008 | (index << 16)`)
loads the surface into `r0/r1`, and a following `DW: store rt1, r0, r1`
reproduces it byte for byte. Storing `0.5` via `.lh` leaves `cov3 = EF00EF00` and
the colour target reads `EF00EF00`; the same program with the `PSEQ` line removed
reads `0`, so the fetch is the only thing putting data in those registers. The
index selects the surface, and values and write formats pass through unchanged:

| program | surface | fetched word |
|:--|:--|:--|
| store rt3 `.lh` `0.25` | `E700E700` | `E700E700` |
| store rt3 `.lh` `0.75` | `F300F300` | `F300F300` |
| store rt3 `.l*` `0.25` | `00400040` | `00400040` |
| store rt4 `.lh` `0.25`, fetch 4 | `E700E700` (rt4) | `E700E700` |
| store rt5 `.lh` `0.25`, fetch 5 | `E700E700` (rt5) | `E700E700` |

In every run the surfaces that were *not* being fetched stayed zero, so the index
really selects the target. The store is visible to the fetch even within one
draw, so no extra pass is needed to read back what the program wrote.

Read the pair with `DW: store rt1, r0, r1`, not through ALU operands: reading
`r0` as an operand returned nothing usable in the forms tried (an fp20 operand
reads the high half of the pair, which a fetch does not appear to fill), and
that dead end is what made the first attempt look like a failed fetch.

**The stencil store's "old" value comes from the fetched registers.**
`GRATE_STENCIL_OP=incr|keep|replace|zero|invert|incr_wrap|decr_wrap` selects the
z-pass op. With `INCR`, five draws of a `kill = 0` program ending in
`store stencil` give:

| program | `stencil8` after 5 draws |
|:--|:--|
| `PSEQ 0x00820008` + `store stencil` | `05` everywhere - one increment per draw |
| `store stencil`, no fetch | `01` at row 0, `41` at mid, `80` at last - position-shaped garbage |
| `store rt1` (control) | `00` |
| `KEEP` + `store stencil`, no fetch | `00` at row 0, `40` at mid, `7F` at last - the same garbage |

So the store commits `op(old, ref, mask)` with `old` taken from `r0/r1`, and only
the `PSEQ` fetch puts the surface value there. `REPLACE` hides all of this
because it ignores `old`, which is why every earlier probe that used `REPLACE`
saw a constant value and concluded the store was doing nothing useful.

**Per-pixel kill bands work, and need the compare in an earlier batch.** With a
threshold on `posx` (`cr0 = posx/256 - 32 - k/256 > 0`, stencil store at ref
`0x7F`, `REPLACE`), the buffer shows the band exactly:

| threshold | row 0 (`x = 0..3`) | mid (`x = 128,129`) | last (`x = 254,255`) |
|:--|:--|:--|:--|
| kill `x > 0` | `7F 00 00 00` | `00 00` | `00 00` |
| kill `x > 128` | `7F 7F 7F 7F` | `7F 00` | `00 00` |
| kill every fragment | `00 00 00 00` | `00 00` | `00 00` |

The compare has to sit in a batch *before* the kill: a condition register written
and read inside one batch reads as zero, so the same program with everything in
one batch writes `7F` everywhere. Putting the condition directly on the kill
write (`MAD kill, posx, u0, u1, #1 (gt)`) also works within one batch. These rows
also confirm the sampled-pixel note below: at `kill x > 0` the first pixel of
row 0 is kept and the rest are not.

**Two argument traps.** `--fs_uniform` takes a bare float, not an expression:
`'["d"]=-32-0.5'` parses as `-32`, so a threshold sweep built that way silently
tests the same threshold every time. And `.lh` writes fp20 rather than fx10, so
a colour readback of a `.lh` write is not a normalised byte - it is a log-scaled
reading of the fp20 bit pattern (decoded below, and in [[Fragment Shader ISA]]
section 12.7):

| value | `.lh` readback | `.l*` readback |
|:--|:--|:--|
| 0.25 | `E700E700` | `00400040` |
| 0.5 | `EF00EF00` | `007F007F` |
| 0.75 | `F300F300` | `00BF00BF` |
| 1.0 | `F700F700` | `00FF00FF` |

Only the `.l*` (low half) form maps a value to `round(255 v)`. A `.lh` readback is
*not* an fp20 value either - it is `8*exponent + top 3 mantissa bits`, monotone
but logarithmic (8 steps per octave, saturating at `FF` above `2.0`), plus a
value-dependent residue in the second byte. Decode it with the table in
[[Fragment Shader ISA]] section 12.7, or better, do not produce it.

Two traps this cost, both about register halves and readout width: `MAD r3.*h`
followed by an fp20 read of `r3` returns nothing usable - write `.lh` when a
later operand has to see the value - and a per-pixel kill band has to be
confirmed by dumping the frame (`--expected` with a value the run will not
produce prints every pixel), because `--result` only ever reports one pixel.

One harness detail that costs a probe if it is missed: `--testonly` prints its
`got 0x...` result line on **stderr**, so a probe that captures only stdout looks
like the assembler produced nothing. Redirect `2>&1` and filter the driver noise
(`nvrm_init failed`, `x11_display_create`) rather than discarding stderr.

**Resolved: `GRATE_STENCIL_TEST` used to discard every fragment.** With
`GRATE_DEPTH=1 GRATE_STENCIL=1 GRATE_STENCIL_TEST=1` set, the colour target read
`0x4C4C4C4C` (the clear value) for **every** probe, including a no-`kill` control
that stores `1.0` and otherwise reads `0x00400040`. Depth was innocent: the same
shader under `GRATE_DEPTH=1` alone reads `0x00400040`. The culprit is face
selection in the harness. It programmed the stencil function and ops for `FRONT`
only, while the quad in `tools/assembler.c` (`0,1,2` over
`(-1,1) (-1,-1) (1,1)`, and `front_direction_is_cw = false`) is **back-facing**
in screen space. An unprogrammed face does not mean "no test": the back-face
state resets to `NEVER` with mask 0, so every fragment failed the stencil test
before the colour write and the target kept its clear value. In a readback that
is indistinguishable from a shader-side `kill`, which is how it was first
mis-filed.

`tools/assembler.c` now programs `GRATE_3D_CTX_STENCIL_TEST_BOTH`. After the
change, `GRATE_DEPTH=1 GRATE_STENCIL=1 GRATE_STENCIL_TEST=1` reads `0x00400040`,
and a shader whose `kill` is unconditionally taken still reads `0x4C4C4C4C`.
Two habits worth keeping:

* program two-sided rasterizer state on both faces even for a single-quad probe,
  because the untouched face is `NEVER`, not "disabled";
* when a probe loses *all* colour at once, bypass the stage under suspicion
  rather than the whole set: `GRATE_STENCIL=1` without `GRATE_STENCIL_TEST=1`
  isolates the surface from the test, and `GRATE_DEPTH=1` alone isolated depth
  in one round. Dropping exactly one variable per run is what found this.

**Three probe traps that each cost a round.**

* A uniform bound as `[n].l` lives in an **fx10** slot, so a scale like `1/8192`
  quantises to 0 - `posx * (1/8192)` then reads 0 for every pixel, no matter what
  `posx` holds. Bind `[n]` as full fp20 when the value is small.
* A `kill` probe has to write the register it also stores. With no `kill` the
  shader stored only `r3`, so both the "kept" and the "discarded" runs read
  `0x00FF0000` and looked identical.
* `posx` **is** biased by 8192 (`posx = 8192 + frag.x`), confirmed with a
  validated control: `posx * (1/256)` reads `0xFF` (>= 1.0, so `posx >= 256`)
  while `128 * (1/256)` reads `0x7F`.
* **`--result` reports framebuffer pixel (0,0), so `posx` is 8192 there.**
  `grate_framebuffer_data()` returns the raw mmap of a `TILED_16x16` surface and
  does not untile it, so word 0 is the first pixel of tile (0,0). The frame dump
  that a failing `--expected` prints is in that same tiled order: consecutive
  words walk *y* inside a 16x16 tile (256 words per tile), then jump to the next
  tile, which is why an apparent linear ramp read off that dump has the wrong
  slope. The word for pixel `(x, y)` is
  `(y / 16 * (width / 16) + x / 16) * 256 + (y % 16) * 16 + x % 16`.

## Behaviour tests (`tests/grate/asm_behavior.sh`)

`asm_tests.sh` asserts expected pixels; this runner is the characterisation tool
next to it. It changes one variable per draw and records what came back, because
the question it answers is "what does the hardware do if I change this" rather
than "does it still match".

```
$ cd ~/build/grate
$ GRATE_ASSEMBLER=$HOME/build/grate/tools/assembler \
  GRATE_ASM_DATA=$HOME/build/grate/tests/grate/asm \
      meson devenv -C . sh asm_behavior.sh -l /tmp/behavior.log
```

From inside the tree (`tests/grate/asm_behavior.sh`) the two variables are
unnecessary - it resolves the assembler exactly like `asm_tests.sh`. `-l -`
prints without logging, and naming a case limits a run to one experiment
(`sh asm_behavior.sh dw_class`), so a follow-up question costs one draw per
variant instead of the whole set.

Every row is `case`, `variant`, `observation`, `interpretation` in separate
columns, and the log opens with what is needed to reproduce the run: date,
`uname`, the SoC line the assembler reports, the assembler's build date and the
source revision. A new log starts with a **control** run of the shipped
`fs_vs_tests.txt` (must print `0xFF01FF00`); without that row the rest cannot be
told apart from a board whose constant path has died, which is a failure mode
that already cost a day here.

Cases: `dw_index`, `dw_class`, `dw_class_bind`, `dw_bit14`, `dw_spill`,
`dw_spill_payload`, `dw_spill_wide`, `dw_spill_slot`, `dw_mrt`, `pseq_fetch`,
`alu_cond`, `alu_precision`. The DW cases bind depth,
coverage and stencil together (`GRATE_DEPTH`, `GRATE_COVERAGE`, `GRATE_STENCIL`,
`GRATE_STENCIL_TEST`) so one run says which target a store reached instead of
needing one run per surface; `dw_class_bind` instead varies *which* surfaces are
bound, and `dw_spill` / `dw_mrt` bind only index 4 or 5 (`GRATE_RT4`,
`GRATE_RT5`).

### Baseline from the first run

`store rtN, r0, r1` with `r0 = 0.25`, `r1 = 0.5`, all surfaces bound:

| variant | colour | depth | coverage | stencil |
|:--|:--|:--|:--|:--|
| `rt0` | clear `4C4C4C4C` | `2000` | `00000000` | `7F` |
| `rt1` | `007F0040` | `8000` | `00000000` | `7F` |
| `rt2` | clear | `8000` | `00000000` | `40` |
| `rt3` | clear | `8000` | `007F0040` | `7F` |

`rt2` writing `40` rather than `7F` is the expected result of not using the
`stencil write` bit (bit 10): the surface keeps the position-shaped value the
stencil section describes, so the row displays a trap rather than a stored value.

Other rows from the same run, and what they pin down: class 3 drops on colour and
coverage while 0, 1 and 2 write; bit 14 turns the payload into zeros on colour
and coverage and leaves depth at `8000`; `PSEQ: load rt3, r0, r1` returns
`00FF00BF` (the stored surface) where the un-fetched control returns `007F0040`
(the primed registers); `(gt)` leaves `r2` at the earlier batch's value for
`u0 = 0` and takes `u0` for `u0 = 1`; `.l*` versus `.lh` on the same `0.25` gives
`00400040` versus `E700E700`.

**Class 1 is enabled by a bound depth surface**, and `dw_class_bind` is the
matrix that says so - `store rt3, r0, r1 class 1` on the coverage target with
everything but the binding held fixed:

| bound | `cov3` |
|:--|:--|
| coverage | `00000000` - dropped |
| coverage + depth | `007F0040` - written |
| coverage + stencil | `00000000` - dropped |
| coverage + depth + stencil | `007F0040` - written |
| class 2 control, both bindings | `007F0040` |

So the field is neither a type tag (the write lands on rt3) nor an index filter
(it is the *binding*, not the target, that decides). That is what the class field
is written up as in [[Fragment Shader ISA]] 8.2.

Two design traps this cost, both worth remembering when adding a case:

* **a fetch test must make the surface differ from the registers.** The first
  version stored the primed registers to `rt3` and fetched them back, so fetch
  and no-fetch printed the same word and the test proved nothing. It now stores
  `0.75 / 1.0` to the surface in one batch and re-primes `r0/r1` with
  `0.25 / 0.5` before the fetch;
* **a conditional write needs a value already in the destination.** Writing `r2`
  under `(gt)` says nothing unless an earlier batch put a different value there,
  otherwise "false" and "true" both read whatever the register held;
* **a zero surface cannot show a write of zeros.** The first `dw_spill` rows
  called mode 3 bit 10 inert because the spill surface read `nz = 0`; on a
  freshly allocated surface that means "no nonzero data anywhere", not "no
  write". Filling the surface with a nonzero word in an earlier batch turned the
  same word into `wf = EF000000` - the low half overwritten with zero. The cost
  of the fix is that `nz` then saturates at 65536, so the prefill rows cannot see
  a *nonzero* write away from word 0.

### Second run: spill halves, index 5 and the class matrix

| case / variant | readback |
|:--|:--|
| `dw_spill` mode 1 `0x00028011`, `0.75/0.5` | `rt4 = EF00F300`, `nz = 65536` |
| prefill + mode 2 slot 0 `0x00028012` | `wf = EF00F300` - untouched |
| prefill + mode 2 bit 12 `0x00029002` | `wf = EF00F300` - untouched, with and without `GRATE_RT_LIMIT=1` |
| prefill + mode 3 bit 10 `0x00028403` | `wf = EF000000` - low half zeroed |
| prefill + mode 3 bit 11 `0x00028803` | `wf = 00F4F300` - high half written |
| prefill + mode 3 bits 10+11 `0x00028C03` | `wf = 00F40000` - both halves |
| `dw_mrt` control `store rt1` | `rt5 = 00000000` |
| `dw_mrt` `store rt5, 0.75/0.5` | `rt5 = EF00F300` |
| `dw_mrt` `store rt5, 0.25/0.5` | `rt5 = EF00E700` - follows the payload |
| `dw_mrt` `store rt5 ... class 1` | `rt5 = 00000000` |
| `dw_mrt` store, re-prime, `PSEQ load rt5` | colour `F700F300` = `rt5`, not the primed `00400040` |

Two of these retract statements in [[Fragment Shader ISA]] 8.2, and both are
cases where the earlier probe's readout could not see the answer:

* **index 5 does receive a store.** Bound and enabled through the path a draw
  uses, `store rt5` fills the surface and a `PSEQ` fetch of index 5 returns those
  words. The earlier negative poked `RT_PARAMS`/`RT_ENABLE` by hand; the store
  path was never the problem.
* **mode 3 bits 10 and 11 are the two halves of one spill word**, bit 10 the low
  half and bit 11 the high half, each holding a different representation of the
  register - see the third run below. Bit 10 commits zero for an fp20 payload, so
  only the prefill rows can see it.

### Third run: the two halves of a spill word, and where it lands

`dw_spill_payload` holds the prefill fixed and sweeps priming precision against
value; `dw_spill_wide` dumps the whole 65536-word surface, so it needs no prefill
and reports how many words were written and where.

| case / variant | readback |
|:--|:--|
| `dw_spill_payload` `.lh` `0.25`, bit 10 | `wf = EF000000` - low half zero |
| `dw_spill_payload` `.l*` `0.25`, bit 10 | `wf = EF000040` |
| `dw_spill_payload` `.l*` `1.5`, bit 10 | `wf = EF000180` - no clamp at 1.0 |
| `dw_spill_payload` `.l*` `2.0`, bit 10 | `wf = EF0001FF` - saturated |
| `dw_spill_payload` `.lh` `0.25`, bit 11 | `wf = 00E8F300` |
| `dw_spill_payload` `.lh` `2.0`, bit 11 | `wf = 0100F300` |
| `dw_spill_payload` `.l*` `0.5`, bit 11, prefill `0.25` | `wf = 00E8E700` |
| `dw_spill_payload` `.l*` `0.5`, bit 11, prefill `1.0` | `wf = 00F8F700` |
| `dw_spill_wide` mode 1 control | 65536 words, `0..65535` |
| `dw_spill_wide` `0x00028403` | 38 words, `0..148` |
| `dw_spill_wide` `0x00068403` (bit 18) | 38 words, `128..276` |
| `dw_spill_wide` `0x000A8403` (bit 19) | 38 words, `256..404` |
| `dw_spill_wide` `0x00228403` (bit 21) | 38 words, `1024..1172` |
| `dw_spill_wide` `0x00029002` (mode 2 bit 12) | 152 words, `1280..1431` |
| `dw_spill_wide` `0x00028012` (mode 2 bit 4) | 0 words |

What that settles:

* the spill word holds **two representations of one register**: fp20 in the high
  half, 8-fractional-bit fixed point in the low half. Writing a register in one
  precision leaves the other half stale, which is why an `.lh` payload writes zero
  low and an `.l*` payload leaves the high half at the last fp20 value - `0.75`
  in the first runs, `0.25` and `1.0` when the prefill was changed to match;
* the fixed-point half saturates at `0x1FF` (`2.0`), so "no clamp at 1.0" is right
  only up to `1.5`;
* mode 2 bit 12 (`152 words from word 1280`) **does** reproduce, contiguously.

The cost of that last point is the same trap in a new dress: the prefill that
makes a zero write visible also fills all 65536 words, so `nz` saturates and
`first` becomes 0 - the row then says "written" but not *where*. The
whole-surface dump answers "where" and cannot see a zero write. Neither readout is
the general one: prefill for zeros, dump for offsets, and keep both cases. The
first `dw_spill_wide` attempt did the opposite and measured `written=0` on every
row, because the `.lh` payload writes zero and a nonzero scan cannot see that.

### Fourth run: the slot field, and what a spill word keeps

`dw_spill_slot` primes all four row registers as fx10 and walks the even slot
bits against the whole-surface dump; `dw_spill_payload` gained the values that
separate the fp20 fields.

| case / variant | readback |
|:--|:--|
| `dw_spill_slot` mode 1 control | 65536 words, `0..65535` |
| slot 0 (bit 10) `0x00028403` | 38 words, `0..148`, `word0 = 00000040` (r0) |
| slot 2 (bit 12) `0x00029003` | 38 words, `1..149`, `word0 = 00000080` (r1) |
| slot 4 (bit 14) `0x0002C003` | 38 words, `2..150`, `word0 = 000000C0` (r2) |
| slot 6 (bit 16) `0x00038003` | 38 words, `3..151`, `word0 = 00000100` (r3) |
| `dw_spill_payload` `.lh` `0.5625` | `wf = 00F1F300` |
| `dw_spill_payload` `.lh` `-0.25` | `wf = 02E8F300` |
| `dw_spill_payload` `.lh` `3.0` | `wf = 0104F300` |
| `dw_spill_payload` `.l*` `2.0` | `wf = EF0001FF` - saturated |
| `dw_spill_payload` `.l*` `-0.25` | `wf = EF0003C0` - `1024 - 64` |

What that settles:

* **a spill packet is four words, one per row register, and a mode-3 spill fills
  one half-word of it** - bits 10/12/14/16 are the low halves of words 0..3 and
  11/13/15/17 the high halves. That is where the stride of four comes from;
* **the low half is the register's fx10 content**, which the negative value pins
  down: `-0.25` writes `0x3C0 = 1024 - 64`, a 10-bit two's-complement field, and
  that is also why `2.0` saturates at `0x1FF` instead of `0x200`;
* **the high half is not the fp20 word**: it is `8*exponent + top 3 mantissa
  bits` with the sign in bit 9 (`0.5625` -> `0x71`, `0.625` -> `0x72` via
  `0x00F1`/`0x00F2`, `3.0` -> `0x104`, `-0.5` -> `0x02F0`). Ten significand bits
  are dropped, so the previous "exactly the fp20 encoding" was one step too
  strong.

Two more traps, both the same family as the earlier ones:

* **an undefined half looks like data.** After an `.l*` write the fp20 half is
  read back as `0xFA`..`0xFE` depending on the fragment - nonzero, varying, and
  entirely meaningless. Any high-half value measured after an fx10 write has to
  be discarded, which is why `dw_spill_slot` sweeps only the even bits;
* **a slot index is not an offset.** Mode 3 writes one half-word of the packet
  and strides by four; mode 2 writes the same half-word four times contiguously
  and starts at word 1280. The two modes agreeing on the value but not on the
  layout is what made the first, prefill-based sweep read as noise.

## DW field sweep across the surfaces

`probe_dw_fields.sh` runs the single-bit experiment on five destinations at once -
colour with each source pair, depth, the stencil late-update word and coverage -
flipping bits 6..14 and 18..31 and setting the class field to 0..3. The DW word is
found by diffing the program's raw stream against the same program with `DW: nop`,
so the probe never depends on a hand-decoded field position, and every row is
written to a TSV as `target, base_word, op, value, word, readback`.

Base words measured on the board (payload `r0 = 0.25, r1 = 0.5, r2 = 0.75`,
`r3 = 1.0`, all four written to both halves in two ALU batches, store in a third):

| target | base word |
|:--|:--|
| colour 0, `r2/r3` | `0x00028005` |
| colour 0, `r0/r1` | `0x00020005` |
| depth, `class 1` | `0x00010001` |
| stencil | `0x00020409` |
| coverage | `0x0002000D` |

Results: bits 6..9, 11..13 and 18..31 are inert on all five; bit 14 zeroes the
payload on colour and coverage and does nothing on depth; class 3 drops the store
on all five, class 0 and 2 always write, and class 1 writes on depth and colour
but drops on stencil and coverage (one coverage run with a depth surface also
bound accepted it, so that row is the one loose end). On index 2 the
`stencil write` bit (bit 10) is what commits a real stencil value: without it the
surface reads `40`, the position-shaped garbage that section
"Depth buffer readback" already warns about, for every class but 3. The depth payload reads
`0x300C` for `0.75`, which is the documented `lo * 0x10 | hi * 0x4000`
superposition. The full tables are in [[Fragment Shader ISA]] 8.2 and 12.5.

Two things this probe cost, both worth checking in any future sweep:

* **`grep ... | sed ...` reports a missing match as an empty field.** The first
  run's coverage block produced no `cov3:` line at all (that block's rows are
  blank in the TSV) and the probe recorded it as an empty readback instead of a
  failure, because the pipeline's exit status is `sed`'s and `sed` succeeds on
  empty input. Capture the match in a variable and test it, or a block that never
  ran is indistinguishable from a block that wrote nothing;
* **a single-run class column is not data.** One sweep row reported `class 1` on
  rt1 as untouched while the same cell wrote on two later repeats, which is how
  the "class 1 = depth" reading survived as long as it did. Repeat any cell whose
  result contradicts the model before recording either outcome.

## `alu_buffer_size`: the count has to be multiplied, and batches rotate over rows

`ALU_BUFFER_SIZE` is `0xe20` - `(size - 1)` in bits 0..1, an undocumented
`SIZEx4` byte in bits 24..31, plus a companion write to `0x501`. Grate programs
`SIZEx4` as `(300 - 1) / (size * 4)`, which makes size 1 `0x4a000000`, size 2
`0x25000001`, size 3 `0x18000002` and size 4 `0x12000003`.

Sizes above 1 hang because the **`0x520` count has to be
`batches * alu_buffer_size`**: the sequencer counts execution slots, and one
batch occupies `alu_buffer_size` of them. Every earlier sweep assembled and ran
the program with the count equal to the batch count, which is correct only at
size 1.

Measured with the three-batch shape (`u0 -> r0.lh`, `r0.l -> r2.l*` + store), the
program assembled at `alu_buffer_size = 1` with only the `0xe20` register
overridden through `GRATE_ALU_BUF_WORD`, and `0x520` poked through `GRATE_POKE`:

| size | `0x520` count | outcome |
|--:|--:|:--|
| 1 | (driver's 3) | `0x00000040` |
| 2 | 3, 5, 7 | hangs |
| 2 | **6** | `0x00000040` - runs and is correct |
| 3 | 6, 9 | runs, `0x00000000` |
| 4 | 3, 9 | hangs |
| 4 | 12 | runs, `0x00000000` |

A count that is not a multiple of the size hangs, so it is the first thing to
check before anything else about a wider buffer is measured. Nothing else about
the wider buffer is unprogrammed: the hang is identical with a program assembled
at size 1, so it is not the assembler's packet layout, and neither `0xe21`
(`pseq_to_dw_exec_nb` = 1, 2, 3, 4, 8) nor the `0x12C` field of `0x501` (100,
150, 300, 600) changes it.

**Batches rotate over the rows.** At size 2 the four-batch shape returns the
value only when the storing batch has the row the producer ran on:

| store in batch | row at size 2 | size 1 | size 2 (count 8) |
|--:|--:|:--|:--|
| 1 | 1 | `0x00000040` | `0x00000000` |
| 2 | 0 | `0x00000040` | `0x00000040` |
| 3 | 1 | `0x00000040` | `0x00000000` |

Every producer wrote `r0` in batch 0, and the same program at size 1 returns the
value from all three store positions. So each row holds its own `r0..r3`, batch
`i` executes on row `i mod size`, and a value written in one batch is visible to a
later batch only when the two land on the same row. The three-batch shape used
for the count table agrees with the rule (3 batches at size 2 -> rows 0, 1, 0, so
the store in batch 2 lands on row 0), but it is not reproducible: identical
settings returned `0x00000040` in one run and `0x00000000` in another, so the row
assignment rests on the four-batch matrix.

Consequences for a harness or a backend: with `alu_buffer_size = N`, `0x520` must
carry `batches * N`, and a program spanning more than one batch has to keep each
batch's live values within its own row group - or hand them through the ALU
latches - because the row file does not carry from batch `i` to batch `i+1`.
Grate's own driver writes the batch count (`grate_3d_startup_pseq_engine()`), so
as it stands it can only run size-1 programs.

`GRATE_ALU_BUF_WORD` overrides `0xe20` verbatim. At size 1 every value tried
(`0x4a000000`, `0x3f000000`, `0x00000000`) produced the expected result, so the
word is inert while the size field is legal - which is also why it cannot be the
cause of the size-2 hang.

To reproduce: generate a shader with the wanted `alu_buffer_size`, run
`tools/assembler --testonly --result`, and wrap every run in `timeout` with a
`pkill -f tools/assembler` afterwards - a hang wedges the channel until the
process is killed, and the next run recovers on its own.

## Dither: needs a quantising target and `RT_PARAMS` bit 0

The dither question was stuck because the 565 target sat on index 5, which AR20
does not store to. The format field lives in `RT_PARAMS`, so the *working* target
can be reprogrammed instead. Same shader throughout - a constant grey `0.5` store
to `rt1` - with `GRATE_POKE=0xe11=...` rewriting `RT_PARAMS(1)`, compared as
whole-frame hashes (16383 words, 256x256):

| programming | distinct words | frame hash |
|:--|--:|:--|
| RGBA8888 `0x04040034` | 1 | `051d53bcdbc5` |
| RGBA8888 + dither bit `0x04040035` | 1 | `051d53bcdbc5` |
| RGB565 `0x04040018` | 2 | `0136ca65366a` |
| **RGB565 + dither bit `0x04040019`** | **3** | `90eaa0788ea6` |
| RGB565 + dither, `0xe26` = 0 / 2 / 3 / 4 | 2 | `0136ca65366a` |
| RGB565 + dither, `0xe26` = `0xff` | 2 | `1c71244f241d` |
| RGB565 + dither, `0xe26` = `0xffffffff` | 2 | `72d0655c06f1` |

So a reduced-precision target is dithered only when `RT_PARAMS.dither_enable` is
set, `0xe26` (`TGR3D_DITHER`) is consumed by the datapath, and an 8-bit-per-channel
target is never affected (`0xe26` = 0, 1, `0x7` and `0xe27` = 0, 1 all leave the
RGBA8888 frame bit-identical). The 565+dither frame reproduced exactly on a
repeat run, and so did the plain 565 frame; two sweeps that disagree on a single
run should be re-run before being believed.

Grate writes `ctx->dither_unk` to `0xe26` and `1` to `0xe27` unconditionally, so
the dither frames above are what the driver's own values produce - poking `0xe26`
to any small value removes the effect, which is how it was found.

## Fragment constants can read zero

On 2026-11-02 the fragment-constant path stopped working on this board while
everything else kept running. Ten repetitions each, in sequence:

| shader | expected | measured |
|:--|:--|:--|
| `tests/grate/asm/fs_uniform.txt` | `0x19334C66` | `0x00000000` **0/10** |
| one ALU slot, immediate operand (`MAD r2.l*, #1, #1, #0, #1`) | `0x000000FF` | `0x000000FF` 10/10 |
| `tests/grate/asm/fs_vs_tests.txt` (varyings only, no constants) | `0xFF01FF00` | `0xFF01FF00` 10/10 |

So the engine, the store path, the output conversion and the readback were all
healthy; only the constant fetch was broken, and every `uN` operand read 0.

Symptoms to watch for:

* a probe that answers `f(0)` instead of `f(x)`: `ex2` returning 1.0 for every
  input, `sin` returning 0, `rcp`/`rsq` returning +maximum, `sqrt` returning 0,
  `frc` returning 0;
* results that change between runs of the *same* command - the shipped uniform
  case returned `0x19334C66` once and `0x00000000` a few minutes later, which is
  how this was first spotted;
* `cdma_timeout_handler: timeout` lines in `dmesg` naming an earlier
  `assembler` job. Once those appear, treat every later reading as suspect.

State of the board when it happened: 486 MB RAM with 37 MB free, `dmesg`
flooded with `systemd-journald: Failed to send WATCHDOG=1 notification message`,
five host1x channel timeouts (`syncpt_load failed: id=26, min=11, max=0`), no
IOMMU or GPU fault logged, and no root access to reload the module. The
condition did not clear on its own across processes or minutes, so it needs a
reboot rather than a retry.

**Every probe needs two controls in the same run:** an immediate-operand shader
(`MAD r2.l*, #1, #1, #0, #1`, which must store `0xFF`) and the shipped
`fs_uniform.txt` case (which must store `0x19334C66`). A probe that returns a
plausible value with neither control has proved nothing, and a probe whose
operand comes from a uniform cannot be trusted when only the immediate control
was run.

## Reading the command stream from `dmesg`

The kernel prints the whole submitted stream when a channel times out, which is the
only way to see what the driver *actually* programmed rather than what its source
intends. Any wedging program works as the trigger - `alu_buffer_size = 2` is
reliable - and the dump arrives once per hang:

```
[...] 0x1c38563c: 1e110001: INCR(offset=e11, [04040034])
[...] 0x1c385644: 1e010001: INCR(offset=e01, [1c3c0000])
[...] 0x1c38564c: 1e150001: INCR(offset=e15, [00040034])
[...] 0x1c38565c: 19030001: INCR(offset=903, [00000022])
```

Two traps. The `cdma_timeout_handler: timeout` **message** is printk-ratelimited
while the dump is not, so counting messages reports "no new timeouts" when the
dumps are sitting right there. And the dump is the *hanging* job's own stream, so
it shows the context programming of the draw that wedged, not the one before it.

### Reading a whole job out of the dump

The register lines are interleaved with other kernel output, and one hang can
leave more than one job in the ring, so three rules make the dump readable:

* **Group by kernel timestamp.** One dump is written within a few milliseconds,
  so a 50 ms window is one job; taking "the longest contiguous block" instead
  truncates it at every interleaved line (it captured 17 of 61 lines that way).
* **Attribute values by position inside the block.** A multi-register `INCR`
  writes consecutive offsets, so a value belongs to `offset + k`, not to the
  offset that starts the block.
* **A repeated offset means more than one job is in the window.** The last
  occurrence is the live one - in one capture `0x343` and `0x520` each appear
  twice because two jobs were present.

The device script `dumpctx4.sh` does exactly this and resolves names from
`tgr_3d.xml.h`; its output for a fully loaded draw is tabulated in
[[GR3D Registers]] 14.3.

This settled two things pokes could not: `RT_PARAMS(1)` is `0x04040034` (format
`0x0d`, pitch 1024, bit 26) and `RT_PARAMS(5)` is `0x00040034`, so bit 26 is
`RT_PARAMS.TILED` - the framebuffer is tiled and the rt5 surface is linear. Poking
`0xe11` instead cannot isolate its fields: the poke lands *after* the `0x520`
start word and changes the frame even when it rewrites the driver's own value.

### Reading the vertex program out of a dump

The vertex program is uploaded *inside* the draw job, so the dump carries the
`NONINCR 0x206` packet verbatim. This is the cheapest oracle for the 128-bit
field table in [[Vertex Shader ISA]], because the source is known:

```
[...] 22060008: NONINCR(offset=206, [4000006c, 0040000d, 8106c003, 6001ff80,
                                     4000006c, 0040010d, 8106c003, 6001ff9d])
```

Eight words are two instructions of four dwords, high dword first. The first one
decodes to export index 0, vector write-mask `0xF`, `rA` = attribute 0 with
swizzle `0x1B` (`.xyzw`), attribute fetch index 0, scalar destination 63 and
end-of-program clear - which is `MOVv r0.xyzw, a[0].xyzw` in `vs_mov.txt`. The
full comparison, including the blob's version of the same shader, is in
[[Vertex Shader ISA]], "Verification against a command-stream dump".

The same dump shows the rest of the vertex-side state in one glance, and it is a
useful cross-check for the register reference:

* `INCR(offset=300, [00000008, 0000fecd])` is **one 64-bit linker instruction**
  - `0x300` and `0x301` are its low and high half, since `LINKER_INSTRUCTION(i)`
  has stride 2 - for the single `LINK` line of `linker_vs_tests.txt`;
* `NONINCR(offset=604, [])` is an empty MFU upload, and `IMM(offset=500,
  data=000)` the PSEQ flush before the schedule upload;
* `IMM(offset=b00, data=001)`, `IMM(offset=e41, data=001)`, `b00=002`,
  `e41=003`, `b00=003` is the GSHIM/PIPEALIAS sequence of
  [[GR3D Registers]] 12, visible here in its natural position before the draw.

## Register pokes: `GRATE_POKE`

Not every register has an API. `GRATE_POKE="0xb00=3,0xc00=0x1ff"` pushes raw
register writes at the very end of `grate_3d_setup_context()`, i.e. after the
context, attributes, targets, textures and shaders are programmed and before the
draw. Every poke is an `INCR addr 1` pair, so the value is a full 32 bits (`IMM`
carries 16).

The control that proves the path works is `GRATE_POKE="0x903=0"`: clearing
`RT_ENABLE` leaves the colour target untouched, so the readback becomes the clear
colour (`0x4C4C4C4C` instead of `0xFF01FF00`). Any probe that returns the
unmodified value without such a control has proved nothing.

### Poke stages: before the start word

`GRATE_POKE` lands at the end of `grate_3d_setup_context()`, which is *after* the
`0x520` start word, so state that the engine latches when the sequencer starts
could not be reached. There are now three stages:

| Variable | Position |
|:--|:--|
| `GRATE_POKE_EARLY` | after `ALU_BUFFER_SIZE`, **before** `0x520` |
| `GRATE_POKE_MID` | after the targets and textures, before the program upload |
| `GRATE_POKE` | end of setup, immediately before the draw |

Measured on the textured 4x4 frame:

| Poke | EARLY | MID | LATE |
|:--|:--|:--|:--|
| `0x501=0` | `c48f385cdd85` | `c48f385cdd85` | `c48f385cdd85` |
| `0x520=0` | no change | - | - |
| `0x355=0x40000000` | no change | - | frame changes |

So `0x501` is **not** latched at start: zeroing it disables the store no matter
which stage it is written from, including as the last word before the draw. The
two "no change" rows are also the proof that EARLY really is early - the driver
writes `0x520` and `0x355` itself *after* that point, so those pokes are simply
overwritten.

The unnamed register tails were then re-poked from EARLY (`0x130`, `0x20c`,
`0x6ff` with `0xffffffff`) and stayed inert, which rules out "latched before the
poke lands" as the explanation for their inertness.

### A verification trap: a run that never happened reuses the old dump

When a check compares a dumped value with an expectation, guard on the
assembler's own stderr (`linker: line N: ...`, `fs: line N: ...`) and not on a
`dmesg` line counter: the ring buffer rotates, so `grep -c` does not grow and
every case then reports a failure. The opposite mistake is worse - a run that
never produced a job leaves the *previous* job's line as `tail -1`, so a `LINK`
line with bad syntax can look like a successful measurement. Both happened here
while verifying the linker word, before the stderr guard was added;
`linkertest3.sh` on the device is the corrected form.

**Whole frames, not one pixel.** `--result` reports pixel (0,0), which is often
insensitive to a change; run with `--expected 0xDEADBEEF` to force the frame dump
and compare histograms instead. On the 4x4 row-coloured `filter_quad` frame
(65532 pixels, 4 distinct words) the following changed **zero** pixels and never
hung:

| register | values poked |
|:--|:--|
| `0xb00` | 0, 1, 2, 3, 4, 7, 15, `0x1ff` |
| `0xe41` | 0, 1, 2, 3, 4 |
| `0xc00`, `0xc01`, `0xc02`, `0xc03` | `0xffffffff`, `0x1ff`, 1 |
| `0xc30`, `0xc31`, `0xc40`, `0xc50` | `0xffffffff`, `0x1ff`, 1 |
| `0x750` (and after `0xb00=3`) | 0 |

One shell trap: `pkill -f tools/assembler` matches the shell running the probe,
because that command line contains the same string, so it kills the probe itself
and the run looks like a silent success. Use `pkill -x assembler`.

The same method measures the *documented* state registers: scissor bounds,
viewport bias/scale, guardband and the `0x343` linker-count coupling all show up
as geometry changes, and the depth/stencil/line/point words show up as nothing.
The table is in [[GR3D Registers]] 4.1, and it is the way to check any register
claim cheaply - one value, one frame diff.

`GRATE_NO_STORE=1` sets bit 12 of the `0x520` start word, which suppresses every
`DW` store while the whole program still runs ([[GR3D Registers]] 6.1). It is the
control for "did the shader execute at all": with it set the readback must be the
clear colour. It is off when unset, empty or `0` - and an empty value counting as
"on" is precisely the bug that made one sweep report every register as inert,
because a shell passing `GRATE_NO_STORE=` disabled all stores for the whole run.

One more harness limitation, from trying to measure `0xe26`/`0xe27` on a
reduced-precision target: `GRATE_RT5=1 GRATE_RT5_FMT=rgb565` does not give a
writable 16-bit target as the tool currently stands - the `rt5(16):` readback is
all zeros even with no poke set - and target-5 stores into `RGBA8888` only place
`0x200` in two words. Any experiment that depends on reading target 5 back has to
be fixed before its result means anything.

Five gotchas that produced misleading data during those sweeps:

* **A hang wedges the channel.** After a timeout, kill the process and re-run a
  known-good shader (`fs_vs_tests` must return `0xFF01FF00`) before trusting the
  next result; otherwise the batch silently reports "no effect" for everything
  that follows. Two single bits hang it reproducibly: **`0xa00` bit 4** and
  **`0xa08` bit 16** ([[GR3D Registers]] 11.1). The channel comes back after
  `pkill -x assembler`.
* **A frame dump is ~835 KB and `/tmp` is a 244 MB tmpfs.** Roughly 90 dumps fill
  it, and from then on every run writes a **0-byte** log - which reads exactly
  like "that register did nothing". Overwrite a single dump file (or hash it)
  per run instead of keeping them all, and watch `df`.
* **`--expected` mismatch exits 1 by design**, so every successful frame-dump run
  ends with status 1. A sweep that treats non-zero as failure aborts itself.
* **`getenv()` is true for an empty value.** Two sweeps in one session were
  silently invalidated by this: `GRATE_NO_STORE=` (from a shell that always
  passes the variable) suppressed *every* store so every register looked inert,
  and `GRATE_ALU_BUF_WORD=` forced `0xe20 = 0`, reducing an `alu_buffer_size`
  sweep to size 1 and making sizes 2..4 look identical. Both knobs now treat
  unset, empty and `0` as off, and any new knob needs that three-way test.
* **A stencil or coverage test that never writes `kill` is not testing the
  shader.** With the stencil test on but no `kill` write in the program, QRast
  performs the stencil update before the shader tail, so a program truncated by a
  bad batch count still shows the reference value and the test says nothing. Only
  after a `kill` write does the update move behind the shader and depend on the
  `DW: store stencil` batch actually running ([[GR3D Registers]] 6.1).
