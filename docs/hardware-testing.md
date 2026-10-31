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

### Block-compressed textures

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

### Timing a draw loop

`GRATE_DRAW_COUNT=n` draws the same quad `n` times and prints
`draws=n elapsed_ms=...`, timed from before the first submit to after the
readback sync. That is wall time including CPU submission, so use it for
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

Note that the **GPU clock cannot be read as non-root on this system**: there is
no `/sys/kernel/debug` at all, no `clk_summary`, no `/sys/devices/*/clocks`, and
`dmesg` has no GPU clock line. Timings therefore stay in milliseconds, and any
cycle figure has to name its assumed clock.

### Dumping the words the assembler emits

`GRATE_DUMP_RAW=1` prints the raw vertex, fragment and linker word streams in
`--testonly` mode. That is how to get the word to base a raw-instruction
experiment on. The TEX word for `txb r2, r3, tex0, r0, r1, r2, r3` is
`0x00001420` - sampler index in bits 3:0, destination select bit 5, enable
bit 10, LOD bias bit 12. The sampler field is the index itself, so a hand-written
`0x...1` samples `tex1` and returns whatever an unbound sampler returns
(constant garbage), not the texture you bound to `tex0`.

### Extra render targets and textures

`GRATE_RT5=1` allocates a linear surface at index 5 (the `gl_FragData[1]`
target), binds it, enables it, and prints its first words as `rt5: ...`.
`GRATE_RT5_FMT=` picks the format - `rgba8888` (default), `rgb565`, `a8` or
`l8` - and the print follows it: `rt5: %08X` x4 for 32-bit, `rt5(16):` eight
16-bit words, `rt5(8):` sixteen bytes. That is how the fx10-to-target-format
conversion table in [[Fragment Shader ISA]] was measured. `GRATE_RT4=1` binds
index 4 the same way with `RGBA8888` and prints `rt4:`. `GRATE_TEX2=1` binds a
second, blue `RGBA8888` texture as `tex1`, which is what proved that TEX bits
0..2 select the sampler.

The `rt4:` line also reports `nz=` (nonzero words in the whole surface) and
`first=` (the first nonzero word index), which is what makes a *spill* write
visible at all - a spill store lands at a slot offset, not at word 0.
`GRATE_RT_LIMIT=1` additionally programs `0xe30 + i` with the end address of
each surface, the companion register ardrv pushes and Grate's XML never named.
The spill sweep that used them is a raw `DW: 0x...` word; the plain
`store rt4, r2, r3` mnemonic emits `0x00028011` (mode 1, index 4), so bits 1:0
choose the spill mode and bits 10..16 the slot.

### Depth buffer readback

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
enables the stencil test with `ALWAYS`/`REPLACE` plus `keep/keep/replace` ops,
and `GRATE_STENCIL_REF=n` sets the reference value. **Set a non-zero ref**: the
patch used 0 at first, and an untouched zero buffer is indistinguishable from a
successful `REPLACE` with ref 0 - ref `0x7F` or `0xC8` is what proves the write.
`GRATE_STENCIL` is the variable that *allocates* - setting only
`GRATE_STENCIL_TEST` prints no `stencil8` line, which reads exactly like "the
stencil path works but writes nothing". With the surface really bound,
`DW: store stencil` writes nothing in any of the four {kill, no kill} x {test
off, test on} combinations, so the readback is sound and the write is what
fails.
