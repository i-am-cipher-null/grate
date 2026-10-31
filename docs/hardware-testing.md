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

The `rt4:` line also reports `nz=` (nonzero words in the whole surface),
`first=` (the first nonzero word index) and `wf=`/`wf1=` (the words at `first`
and `first+1`), which is what makes a *spill* write visible at all - a spill
store lands at a slot/offset, not at word 0.
`GRATE_RT_LIMIT=1` additionally programs `0xe30 + i` with the end address of
each surface, the companion register ardrv pushes and Grate's XML never named.
The spill sweep that used them is a raw `DW: 0x...` word; the plain
`store rt4, r2, r3` mnemonic emits `0x00028011` (mode 1, index 4), so bits 1:0
choose the spill mode and bits 10..16 the slot.

### Depth buffer readback

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

## `alu_buffer_size`: only size 1 runs

`ALU_BUFFER_SIZE` is `0xe20` - `(size - 1)` in bits 0..1, an undocumented
`SIZEx4` byte in bits 24..31, plus a companion write to `0x501`. Grate programs
`SIZEx4` as `(300 - 1) / (size * 4)`, which makes size 1 `0x4a000000`, size 2
`0x25000001`, size 3 `0x18000002` and size 4 `0x12000003`.

Measured with a three-batch shader (`u0 -> r0.lh`, `r0 -> r2.l*`, store) at
256x256:

| size | outcome |
|--:|:--|
| 1 | `result 0x00000040` - expected value |
| 2 | hangs; no result, the process has to be killed |
| 3 | `result 0x00000000` - completes, wrong value |
| 4 | hangs |

Three probes separate the cause:

* **not the `SIZEx4` byte.** With `GRATE_ALU_BUF_WORD=<hex>` forcing the whole
  word, size 2 hangs for every `SIZEx4` tried (`0x00`, `0x12`, `0x18`, `0x24`,
  `0x25`, `0x26`, `0x4a`), and size 4 hangs for `0x00`, `0x11`, `0x12`, `0x18`,
  `0x24`. The failing value is the two-bit size field.
* **not the shader.** At size 2 a program whose only batch is
  `DW: store rt1, r2, r3` - no ALU, no MFU, no TEX - hangs as well.
* **size 3 is not a substitute.** At size 3 the two-batch shape returns 0, and
  both `ALU0: MAD r2.l*, u0, ...` followed by a store in a later batch and the
  same pair inside one batch **hang**.

So there is never more than one row in flight, and the register file a backend
can use is `r0..r3` plus `g0..g7`, the four ALU latches and the three immediates.

`GRATE_ALU_BUF_WORD` overrides `0xe20` verbatim. At size 1 every value tried
(`0x4a000000`, `0x3f000000`, `0x00000000`) produced the expected result, so the
word is inert while the size field is legal - which is also why it cannot be the
cause of the size-2 hang.

To reproduce: generate a shader with the wanted `alu_buffer_size`, run
`tools/assembler --testonly --result`, and wrap every run in `timeout` with a
`pkill -f tools/assembler` afterwards - a hang wedges the channel until the
process is killed, and the next run recovers on its own.

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
