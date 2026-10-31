# Tegra 2 (AR20) — GR3D register reference

Register-level reference for the 3D engine, block by block. It collects what is
known about every GR3D register that appears in `include/tgr_3d.xml.h`, in
Grate's driver (`src/libgrate/`, `src/libhost1x/host1x-gr3d.c`) or in the blob's
command streams, and marks what is not known.

Related documents: [[Command Stream]] for the CDMA encoding and the fragment
program upload sequence, [[Fragment Shader ISA]] for the words those uploads
carry, [[Shader Linking]] for the linker program, [[Vertex Shader ISA]] for the
VPE words, and [[GR2D Registers]] for the 2D engine.

## 0. How to read this

Each register has an address, a name, its fields, a status and a note.

| Mark | Meaning |
|:--|:--|
| **V** | verified by an experiment on hardware, with the measurement recorded in these documents |
| **D** | read off a working command stream (Grate's driver, which renders correctly) |
| **B** | read off the blob's command stream or disassembly |
| **?** | written somewhere, meaning unknown |

Field names and bit positions in the tables come from `tgr_3d.xml.h` (generated
from envytools' `tgr_3d.xml`) where the register is in that file; the ones that
are not are marked as such, and several of those are the ones Grate writes with
literal numbers.

A register that is not listed is not necessarily absent: the engine's address
space below `0x1000` is sparsely decoded, and the gaps are collected in section
16. Grate's reset sequence (`host1x_push_gr3d_reset()` in
`src/libhost1x/host1x-gr3d.c`) writes many registers purely to clear them, which
is the only evidence that they exist.

## 1. Host1x synchronisation (`0x000`..`0x02d`)

| Addr | Name | Status | Fields / notes |
|--:|:--|:--:|:--|
| `0x000` | `INCR_SYNCPT` | D | `syncpt_index` 0..7, `condition` 8..10 (`IMMEDIATE`, `OP_DONE`, `RD_DONE`, `WR_SAFE`). Writing it increments the syncpoint; this is how every 3D job signals completion. |
| `0x008` | `WAIT_SYNCPT` | - | declared, never used by Grate |
| `0x009` | `WAIT_SYNCPT_BASE` | - | `offset` 0..15, `base_idx` 16..23, `idx` 24..31 |
| `0x00b` | `LOAD_SYNCPT_BASE` | - | not used |
| `0x00c` | `INCR_SYNCPT_BASE` | - | `offset` 0..23, `base_idx` 24..31 |
| `0x02c` | `INDOFF2` | ? | indirect write offset, declared only |
| `0x02d` | `INDOFF` | ? | indirect write offset, declared only |
| `0x00d`, `0x00e`, `0x00f`, `0x010`, `0x011`, `0x012`, `0x013`, `0x014`, `0x015` | — | ? | cleared by the reset sequence; no other use |

The syncpoint increment is the *only* handshake between the command stream and
the CPU side; there is no interrupt from the 3D engine itself.

**Strided rows are individual registers.** Wherever a row says `0xNNN + k*i`, every
address in the range is a separate register and must be programmed separately:
`ATTRIB_PTR` covers `0x100, 0x102, ... 0x11e` and `ATTRIB_MODE` the odd addresses
`0x101, 0x103, ... 0x11f`; `LINKER_INSTRUCTION` covers `0x300, 0x302, ... 0x33e`;
`TEXTURE_POINTER` `0x710..0x71f`, `TEXTURE_DESC1` `0x720, 0x722, ... 0x73e` and
`TEXTURE_DESC2` `0x721, 0x723, ... 0x73f`; `RT_PTR` `0xe00..0xe0f`, `RT_PARAMS`
`0xe10..0xe1f` and the target limits `0xe30..0xe3f`.

## 2. Index / geometry submission (`0x100`..`0x126`)

Vertex attributes are described by 16 pointer/mode pairs, and a draw is issued
by writing the index pointer and the two draw registers.

| Addr | Name | Status | Fields / notes |
|--:|:--|:--:|:--|
| `0x100 + 2i` | `ATTRIB_PTR(i)`, i = 0..15 | D | 32-bit byte address of attribute `i` (relocated). |
| `0x101 + 2i` | `ATTRIB_MODE(i)` | D | `type` 0..3 (see below), `size` 4..7 (components - 1, so 0 = 1 component), `stride` 8..31. |
| `0x120` | `VP_ATTRIB_IN_OUT_SELECT` | D | one bit per vertex-program output (`n`) and input (`16 + n`); Grate writes `0x00030081`, i.e. `OUT0`, `OUT7`, `IN0`, `IN1`. See the note below. |
| `0x121` | `INDEX_PTR` | D | byte address of the index buffer (relocated). |
| `0x122` | `DRAW_PARAMS` | D | `primitive_type` 24..26, `provoking_vertex` 27, `index_mode` 28..29, `first` 0..23. |
| `0x123` | `DRAW_PRIMITIVES` | D | `index_count - 1` at 20..31, `offset` (first index) at 0..19. Writing it starts the draw. |
| `0x124`..`0x126` | — | ? | reset writes `0x124 = 7`, `0x125 = 0`, `0x126 = 0` |

`ATTRIB_MODE.type` (from the header, the same numbering the vertex ISA uses):

| Value | Type | Value | Type |
|--:|:--|--:|:--|
| 0 | ubyte | 8 | uint |
| 1 | ubyte normalized | 9 | uint normalized |
| 2 | sbyte | 10 | sint |
| 3 | sbyte normalized | 11 | sint normalized |
| 4 | ushort | 12 | fixed 16 |
| 5 | ushort normalized | 13 | float32 |
| 6 | sshort | 14 | float16 |
| 7 | sshort normalized | | |

`INDEX_MODE` is 0 = non-indexed, 1 = u8 indices, 2 = u16 indices;
`PRIMITIVE_TYPE` is 0 points, 1 lines, 2 line loop, 3 line strip, 4 triangles,
5 triangle strip, 6 triangle fan - the same numbering as GL.

`DRAW_PRIMITIVES.index_count` is stored **minus one**, so a 3-index triangle
writes 2. Grate's `host1x_gr3d_triangle()` programs `0x00200000` for three
indices and `0xec000000` for the pointer word, i.e. `index_count - 1 = 2`,
`first = 0`.

**Not settled:** the exact meaning of the `VP_ATTRIB_IN_OUT_SELECT` bits as used
by Grate. The header names them `OUT0..OUT15` (bits 0..15) and `IN0..IN15`
(bits 16..31), and the header itself has a defect - `IN12` is defined as
`0x04000000`, the same value as `IN10`. Grate's vertex-shader path writes
`0x00030081`, which sets `OUT0`, `OUT7`, `IN0` and `IN1`; which attributes those
map onto is determined by the vertex program's own input/output mask rather than
by this register alone. The value in `host1x_gr3d_triangle()` was copied from a
working blob stream, so the encoding is only as good as that example.

## 3. Vertex program engine, VPE (`0x200`..`0x20b`)

| Addr | Name | Status | Notes |
|--:|:--|:--:|:--|
| `0x200`..`0x204` | — | ? | reset writes `0x11`, `0xffff`, `0xff0000`, `0`, `0` |
| `0x205` | `VP_UPLOAD_INST_ID` | D | upload pointer, reset to 0 before every program |
| `0x206` | `VP_UPLOAD_INST` | D | instruction FIFO: `4 * n` words, one 128-bit VLIW instruction per four dwords |
| `0x207` | `VP_UPLOAD_CONST_ID` | D | constant (uniform) window pointer |
| `0x208` | `VP_UPLOAD_CONST` | D | `256 * 4` words = the whole 256-register constant file |
| `0x209`, `0x20a` | — | ? | cleared at reset |
| `0x20b` | — | ? | reset writes 3 |

`0x206` is the only path by which vertex instructions reach the engine; the
words are in [[Vertex Shader ISA]]. The constant window is `0x208` and is
independent of the fragment-side `FP_CONST` window at `0x820`.

## 4. Setup unit and linker (`0x300`..`0x364`)

| Addr | Name | Status | Fields / notes |
|--:|:--|:--:|:--|
| `0x300 + 2i` | `LINKER_INSTRUCTION(i)`, i = 0..31 | V | two words per linked varying: source export index and TRAM row, plus per-component interpolation and destination-format flags. See [[Shader Linking]]. |
| `0x343` | `CULL_FACE_LINKER_SETUP` | D | `linker_inst_count` 5..9 (number of `LINKER_INSTRUCTION`s), `front_cw` bit 15, `cull_face` 16..17 (`NONE`/`CCW`/`CW`/`BOTH`), bits 18..31 unknown (`0xb8e00000` is written by reset and by every draw) |
| `0x344` | `POLYGON_OFFSET_UNITS` | D | float |
| `0x345` | `POLYFON_OFFSET_FACTOR` | D | float (name misspelt in the header: `POLYFON`) |
| `0x346` | `POINT_PARAMS` | D | point sprite control; Grate writes `0x00001401` |
| `0x347` | `POINT_SIZE` | D | float |
| `0x348` | `POINT_COORD_RANGE_MAX_S` | D | float |
| `0x349` | `POINT_COORD_RANGE_MAX_T` | D | float |
| `0x34a` | `POINT_COORD_RANGE_MIN_S` | D | float |
| `0x34b` | `POINT_COORD_RANGE_MIN_T` | D | float; the four are written with one `INCR(0x348, 4)` |
| `0x34c` | `LINE_PARAMS` | D | line mode; Grate writes 2 |
| `0x34d` | `HALF_LINE_WIDTH` | D | float |
| `0x350` | `SCISSOR_HORIZ` | D | `min` 16..31, `max` 0..15 |
| `0x351` | `SCISSOR_VERT` | D | `min` 16..31, `max` 0..15 |
| `0x352`/`0x353`/`0x354` | `VIEWPORT_{X,Y,Z}_BIAS` | D | float32. Grate writes `0x352`, `0x353`, `0x355`, `0x356` with one `MASK(0x352, 0x1b)`; the reset sequence writes `0x354` and `0x357` with `MASK(0x354, 9)` |
| `0x355`/`0x356`/`0x357` | `VIEWPORT_{X,Y,Z}_SCALE` | D | float32 |
| `0x358`/`0x359`/`0x35a` | `GUARDBAND_{WIDTH,HEIGHT,DEPTH}` | D | float32 |
| `0x363`, `0x364` | — | ? | cleared at reset |

Notes:

* the viewport is programmed in *window* units, not normalised units: Grate
  writes `width * 8.0, height * 8.0, width * 8.0, height * 8.0` into
  `0x355/0x356` and the same into `0x352/0x353`, i.e. bias = scale, which is the
  GL `glViewport` mapping for a full-screen quad;
* the reset sequence sets `0x354` (Z bias) and `0x357` (Z scale) to
  `0x3efffff0` = 0.49999994 rather than 0, so a shader that writes `z = 0.5`
  lands mid-depth without any viewport programming;
* `SCISSOR_HORIZ.min/max` and `SCISSOR_VERT.min/max` are 16-bit each and are
  written one register per axis (`INCR(0x350, 2)`);
* `0x343` bits 30..31 and 21..18 are stable at `0xb8e00000` in both Grate and
  the reset sequence, but no experiment has isolated them.

## 5. Quad rasterizer, QRast (`0x400`..`0x41a`)

| Addr | Name | Status | Fields / notes |
|--:|:--|:--:|:--|
| `0x400` | `STENCIL_FRONT1` | D | `mask` 0..7, `func` 8..10 (`COMPARE_FUNC_*`); reset = `0x07ff` (mask `0xff`, `ALWAYS`) |
| `0x401` | `STENCIL_BACK1` | D | same layout for back faces |
| `0x402` | `STENCIL_PARAMS` | V | `stencil_test` bit 5, `stencil_write_early` bit 6 |
| `0x403` | `DEPTH_TEST_PARAMS` | V | `depth_test` bit 3, `func` 4..7, `depth_write` bit 8; bit 9 (`0x200`) is always set by Grate and its meaning is not isolated |
| `0x404` | `DEPTH_RANGE_NEAR` | D | float |
| `0x405` | `DEPTH_RANGE_FAR` | D | float |
| `0x40c` | — | ? | Grate writes 6 |
| `0x40f` | — | V | Late/early test control: `0x48` normally, `0x60` when the fragment program can discard and a stencil test is active. Bit 6 (`STENCIL_WRITE_EARLY`) clears in the second case; see [[Fragment Shader ISA]] §8.2 |
| `0x416` | — | ? | Tegra 114 only, `0x140` |
| `0x41a` | — | ? | Tegra 114 only, `0xa00` |

The stencil reference value and the three stencil ops are **not** here; they are
in `STENCIL_FRONT2`/`STENCIL_BACK2` at `0xe28`/`0xe29` (section 14).

Depth writes happen early and unconditionally once the depth test is enabled -
a fragment the shader discards still stamps its interpolated `z` - whereas the
stencil update moves behind the shader as soon as the program writes the kill
register. Both behaviours are measured, see [[Fragment Shader ISA]] §8.2.

## 6. Program sequencer, PSEQ (`0x500`..`0x547`)

| Addr | Name | Status | Fields / notes |
|--:|:--|:--:|:--|
| `0x500` | `FP_PSEQ_UPLOAD_INST_BUFFER_FLUSH` | D | write 0 to flush before uploading PSEQ words |
| `0x501` | — | ? | second half of the `ALU_BUFFER_SIZE` programming: Grate writes `(0x2200 << 16) \| (0x0032 << 16) \| (0x12C << 4) \| 0xF`. Reset writes 7. Not in `tgr_3d.xml.h` |
| `0x502` | — | ? | cleared at reset |
| `0x503` | — | ? | cleared before every draw |
| `0x520` | `FP_PSEQ_ENGINE_INST` | D | `0x20006000 \| batch_count`. Starts the sequencer; `batch_count` is the number of schedule rows |
| `0x540` | `FP_PSEQ_UPLOAD_INST_ID` | D | upload pointer |
| `0x541` | `FP_PSEQ_UPLOAD_INST` | D | PSEQ word, one per batch |
| `0x542`..`0x544` | — | ? | cleared at reset |
| `0x545` | `FP_PSEQ_QUAD_ID` | ? | written 0 before every draw |
| `0x546` | `FP_PSEQ_DW_CFG` | D | `pseq_to_dw_exec_nb` 6..31 |
| `0x547` | — | ? | Tegra 114 only: `INCR(0x547, 2)` then `0xc0000000`, `0`. Not in `tgr_3d.xml.h` |

`pseq_to_dw_exec_nb` is driver state, not a compiler output: dumping the blob's
command stream shows it never writes `0x546` itself, and sweeping it on hardware
(1, 3, 8, 16) leaves a single-batch program's output bit-identical. It is a
pipeline-depth knob.

The `0x501` word is the other half of the ALU buffer programming and is written
unconditionally next to `0xe20`; because `0xe20 = 2` and `4` hang the engine (see
14.1), the only `0x501`/`0xe20` pairs that have ever run here are the size 1 and
size 3 ones.

## 7. Attribute interpolation and SFU, MFU (`0x600`..`0x60e`)

| Addr | Name | Status | Fields / notes |
|--:|:--|:--:|:--|
| `0x600` | `FP_UPLOAD_MFU_SCHED_ID` | D | upload pointer |
| `0x601` | `FP_UPLOAD_MFU_SCHED` | D | `count` 0..1 (words to execute this batch), `offset` 2..7 (index of the first word) |
| `0x603` | `FP_UPLOAD_MFU_INST_ID` | D | upload pointer |
| `0x604` | `FP_UPLOAD_MFU_INST` | D | `2 * m` dwords = `m` MFU words, **high dword first** |
| `0x60e` | — | ? | cleared at reset |

The schedule word's `count` field is `words - 1`, so a batch that runs one MFU
word writes 1 and a batch that runs none writes 0. The word layout is in
[[Fragment Shader ISA]] §6.

## 8. Texture unit, TEX (`0x700`..`0x79f`)

| Addr | Name | Status | Fields / notes |
|--:|:--|:--:|:--|
| `0x700` | `FP_UPLOAD_TEX_INST_ID` | D | upload pointer |
| `0x701` | `FP_UPLOAD_TEX_INST` | D | TEX word, one per batch |
| `0x702` | — | ? | cleared at reset |
| `0x710 + i` | `TEXTURE_POINTER(i)`, i = 0..15 | D | 32-bit address of texture `i` (relocated) |
| `0x720 + 2i` | `TEXTURE_DESC1(i)` | V | `wrap_t` bit 0, `wrap_s` bit 1, `wrap_t_mirrored` bit 2, `wrap_s_mirrored` bit 3, `compressed` bit 4, `format` 8..12, `minfilter_linear_between` bit 27, `minfilter_linear_within` bit 28, `magfilter_linear` bit 29 |
| `0x721 + 2i` | `TEXTURE_DESC2(i)` | V | `not_pow2_dimensions` bit 6, `mipmap_disable` bit 7, `max_lod` 12..15, `height` 8..19, `width` 20..31, `height_log2` 24..27, `width_log2` 28..31 |
| `0x740` | — | ? | Grate writes `0x035` before every draw |
| `0x741`, `0x742` | — | ? | cleared |
| `0x750`..`0x75f` | — | ? | 16 words, cleared by the reset sequence. Must be written **after** `0xb00 = 3`, otherwise non-power-of-two textures are corrupted; the values survive a soft reboot and are garbage after power-on |
| `0x770`..`0x79f` | — | ? | Tegra 114 additional texture descriptors: 48 words, cleared |
| `0x7e0`, `0x7e1` | — | ? | reset writes 1 and 0 |

**There is no pitch field.** The sampler derives the row stride as
`width * bpp` (for block formats `width * bpp / 4`), so a surface must be tightly
packed; a larger allocation pitch is silently ignored and rows are read from the
wrong offsets. That is measured and is the reason `grate_create_texture()`
allocates a tight pitch.

`TEXTURE_DESC2` carries both the exact dimensions and their log2 values; Grate
programs `WIDTH_LOG2`/`HEIGHT_LOG2` and `MAX_LOD` and leaves the `WIDTH`/
`HEIGHT` fields zero, and the sampler is fine with that - so at least on AR20 the
log2 fields are the ones that matter for addressing.

The `format` field uses the `PIXEL_FORMAT_*` values:

| Value | Format | Value | Format |
|--:|:--|--:|:--|
| `0x01` | A8 | `0x0b` | D16 linear |
| `0x02` | L8 | `0x0c` | D16 nonlinear |
| `0x03` | S8 | `0x0d` | RGBA8888 |
| `0x04` | LA88 | `0x0e` | BGRA8888 |
| `0x06` | RGB565 | `0x12` | RGBA fp32 |
| `0x09` | RGBA5551 | `0x16` | DXT1 |
| `0x0a` | RGBA4444 | `0x18` | DXT3 |
| | | `0x19` | DXT5 |
| | | `0x1a` | ETC1 |

The same table is used by `TEXTURE_DESC1.format` for sampling and by
`RT_PARAMS.format` (`0xe10 + i`) for rendering, with one difference: D16 linear
(`0x0b`) and nonlinear (`0x0c`) are render-target formats only, and `R8`/`RG8`
style formats have no entry at all.

## 9. Fragment ALU (`0x800`..`0x807`)

| Addr | Name | Status | Fields / notes |
|--:|:--|:--:|:--|
| `0x800` | `FP_UPLOAD_ALU_SCHED_ID` | D | upload pointer |
| `0x801` | `FP_UPLOAD_ALU_SCHED` | D | `count` 0..1, `offset` 2..7; the *wide* schedule layout (used by later chips) puts `count` at 8..10 and `offset` at 16..22 of the same word |
| `0x803` | `FP_UPLOAD_ALU_INST_ID` | D | upload pointer |
| `0x804` | `FP_UPLOAD_ALU_INST` | D | `8 * packets` dwords: four 64-bit slots per packet, **high dword first** |
| `0x806` | `FP_UPLOAD_ALU_INST_COMPLEMENT` | D | the `ALU_COMPLEMENT` word per batch; zero in every program measured |
| `0x807` | — | ? | cleared at reset |

`ALU_COMPLEMENT` is accepted by the assembler (`ALU_COMPLEMENT: <hex>`) and
uploaded at `0x806`, but no value has been found that changes the result: it is
not the per-slot opcode complement one might expect from the name.

## 10. Data write, DW (`0x900`..`0x90b`)

| Addr | Name | Status | Fields / notes |
|--:|:--|:--:|:--|
| `0x900` | `FP_UPLOAD_DW_INST_ID` | D | upload pointer |
| `0x901` | `FP_UPLOAD_DW_INST` | D | DW word, one per batch |
| `0x902` | — | ? | cleared before every draw |
| `0x903` | `RT_ENABLE` | V | one bit per enabled render target (`0..15`) plus `DEPTH_BUFFER` bit 16. The spill target 4 and the coverage target 3 are enabled through this word; a target whose bit is clear is written to by nobody |
| `0x907`..`0x90b` | — | ? | Tegra 30 only, cleared |

The render-target addresses are not here; they are at `0xe00 + i`, and the
surfaces' formats and pitches at `0xe10 + i`.

## 11. Fragment data cache, FDC (`0xa00`..`0xa0c`)

| Addr | Name | Status | Fields / notes |
|--:|:--|:--:|:--|
| `0xa00` | `FDC_CONTROL` | D | bit 0 = invalidate. Grate also writes larger values (`0xe00`, `0xe01`) as a control word; the upper bits are not decoded |
| `0xa01` | — | D | second control word, written with the `0xa02` block |
| `0xa02`..`0xa07` | — | ? | six threshold/counter words, `0x1ff, 0x1ff, 0x1ff, 0x30, 0x20, 0x30` in every Grate stream |
| `0xa08` | — | ? | written `0x100` |
| `0xa09`..`0xa0c` | — | ? | cleared at reset |

The FDC is the unified cache used by QRast, PSEQ and DW. The `0xa02` block is
programmed identically at init and before every draw, which suggests
flush/threshold configuration rather than per-draw state, but no experiment has
separated the six words.

## 12. GSHIM (`0xb00`..`0xb14`)

| Addr | Name | Status | Notes |
|--:|:--|:--:|:--|
| `0xb00` | — | ? | stage selector. The reset sequence writes 1, 2, 3 in sequence, pairing each with a write to `0xe41` (1, 3, then nothing) |
| `0xb01` | — | ? | cleared at reset |
| `0xb02`, `0xb03` | — | ? | never written by Grate or the blob's observed streams |
| `0xb04`, `0xb06`, `0xb07`, `0xb08`, `0xb09`, `0xb0a`, `0xb0b`, `0xb0c`, `0xb0d`, `0xb0e`, `0xb0f` | — | ? | cleared at reset |
| `0xb05` | — | ? | never written |
| `0xb10`, `0xb11`, `0xb12` | — | ? | cleared at reset |
| `0xb13` | — | ? | never written |
| `0xb14` | — | ? | cleared at reset |

`0xb00` is the only register in this block whose value has been varied
deliberately, and the only reason it appears at all is the ordering constraint
with `0x750`: writing the texture descriptors before `0xb00 = 3` corrupts
non-power-of-two textures. The block behaves like a per-stage alias window, which
is what the `PIPEALIAS` block at `0xc00` also looks like.

## 13. PIPEALIAS (`0xc00`..`0xc53`)

Sixteen registers in four groups of four, all cleared by Grate's reset sequence
and never used otherwise:

| Addresses | Status |
|:--|:--:|
| `0xc00`, `0xc01`, `0xc02`, `0xc03` | ? |
| `0xc04`..`0xc2f` | ? - not even cleared; presumably a hole |
| `0xc30`, `0xc31`, `0xc32`, `0xc33` | ? |
| `0xc34`..`0xc3f` | ? - not cleared |
| `0xc40`, `0xc41`, `0xc42`, `0xc43` | ? |
| `0xc44`..`0xc4f` | ? - not cleared |
| `0xc50`, `0xc51`, `0xc52`, `0xc53` | ? |

Four-address groups at `0x30` stride suggest four pipeline slots with four
registers each - most plausibly the four ALU slots' per-pipe configuration,
aliased into the main register space. No experiment exists.

## 14. Global / cross-engine (`0xe00`..`0xe87`)

### 14.1 Render targets and pipeline setup

| Addr | Name | Status | Fields / notes |
|--:|:--|:--:|:--|
| `0xe00 + i` | `RT_PTR(i)`, i = 0..15 | V | render target address (relocated). Index 0 = depth, 1 = colour 0, 2 = stencil, 3 = coverage, 4 = register spill, 5..15 = further colour targets |
| `0xe10 + i` | `RT_PARAMS(i)` | V | `dither_enable` bit 0, `format` 2..6 (`PIXEL_FORMAT_*`), `pitch` 8..24 (in bytes), `tiled` bit 26 (`TILED_16x16`) |
| `0xe20` | `ALU_BUFFER_SIZE` | V | `size - 1` at 0..1, `SIZEx4` at 24..31. **Only `size = 1` is usable**; see below |
| `0xe21` | `TRAM_SETUP` | V | `div64` 0..6 (`64 / rows`), `used_tram_rows_nb` 8..14 |
| `0xe22` | `FP_UPLOAD_INST_ID_COMMON` | D | resets the upload pointers that the per-unit `*_ID` registers do not cover |
| `0xe25` | — | ? | written 0 |
| `0xe26` | `DITHER` | ? | `0x779` in Grate's draw path, `0` at reset; the bit layout is not decoded |
| `0xe27` | — | ? | `1` at init and before the main draw, `2` per sub-draw; not in `tgr_3d.xml.h` |
| `0xe28` | `STENCIL_FRONT2` | V | `ref` 17..25, `op_fail` 0..2, `op_zfail` 3..5, `op_zpass` 6..8 (`STENCIL_OP_*`) |
| `0xe29` | `STENCIL_BACK2` | V | same, back faces. **An unprogrammed face is `NEVER` with mask 0, not "disabled"** - a single-quad probe that programs only the front face discards every fragment |
| `0xe30 + i` | — | B | render-target *limit* address (`ptr + pitch * height`), pushed by ardrv for every valid surface as a companion to `0xe00 + i`; AR20's name for it is not in `tgr_3d.xml.h`. Grate writes it only under `GRATE_RT_LIMIT=1` |
| `0xe40`, `0xe41` | — | ? | reset writes 0; `0xe41` is also written 1 and 3 in the `0xb00` sequence |
| `0xe44`, `0xe45` | — | ? | Tegra 114 only: `0xe45` mirrors the `DEPTH_TEST_PARAMS` value |
| `0xe70`..`0xe7f` | — | ? | 16 words, cleared |
| `0xe80` | — | ? | reset writes `0x0f00` |
| `0xe81`..`0xe84`..`0xe87` | — | ? | cleared (`0xe84`, `0xe85`, `0xe86`, `0xe87`) |

The target index table is the one the blob uses, in its own words:
`SURF_DEPTH` 0, `SURF_OUT0` 1, `SURF_STENCIL` 2, `SURF_COVERAGE` 3,
`SURF_REGSPILL` 4, `SURF_OUT1` 5. Indices 6..15 are declared by the hardware
(16 enable bits) but no blob path was observed using them; Grate binds 5 as
`gl_FragData[1]`.

### 14.2 `ALU_BUFFER_SIZE` (`0xe20`): only size 1 works

The word is `(size - 1)` in bits 0..1 and an undocumented `SIZEx4` byte in bits
24..31 that Grate programs as `(300 - 1) / (size * 4)` (74 for size 1, 37 for
size 2, 24 for size 3, 18 for size 4). Measured on AR20:

| size | result |
|--:|:--|
| 1 | runs; the shader's row registers behave as documented |
| 2 | **hangs the engine**, reproducibly, for any program |
| 3 | runs *some* programs, but hangs others and can return zero where size 1 returns the value |
| 4 | **hangs the engine** |

Three experiments narrow it down:

* **The hang is not the `SIZEx4` byte.** With `GRATE_ALU_BUF_WORD` forcing the
  whole `0xe20` word, size 2 hangs for `SIZEx4` = `0x24` (the "correct" quotient
  `300/(size*4) - 1`), `0x25` (Grate's value), `0x4a` (size 1's value), `0x18`,
  `0x12`, `0x26` and `0x00`. Size 4 hangs for `0x11`, `0x12`, `0x24`, `0x18` and
  `0x00`. So the failing value is the size field itself.
* **The hang is not the shader's ALU work.** A program consisting of a single
  `DW: store rt1, r2, r3` batch - no ALU, no MFU, no texture - also hangs at
  size 2.
* **Size 3 is not a usable substitute.** At size 3 the same program that returns
  `0x00000040` at size 1 returns `0x00000000`, and the two simplest variants -
  `ALU0: MAD r2.l*, u0, ...` followed by a store, and the same with the store in
  the same batch - **hang**. Only the two-batch shape with an intermediate row
  register completes, and it completes with the wrong value.

Consequences for a backend: emit every program at `alu_buffer_size = 1` and treat
the register file as four fp20 row registers (`r0..r3`) plus globals and ALU
latches. There is no known way to make more rows live.

`GRATE_ALU_BUF_WORD=<hex>` overrides the whole word for experiments; with a
single-batch control the override produces bit-identical output for size 1, so the
pipeline config is inert while it is legal.

## 15. Registers Grate writes that are *not* in `tgr_3d.xml.h`

These are the ones a second implementation has to know about because no header
names them:

| Addr | Written value | Purpose |
|--:|:--|:--|
| `0x00d`..`0x015` | 0 | reset |
| `0x200`..`0x204` | `0x11`, `0xffff`, `0xff0000`, 0, 0 | VPE reset |
| `0x209`, `0x20a`, `0x20b` | 0, 0, 3 | VPE reset |
| `0x363`, `0x364` | 0 | reset |
| `0x40c` | 6 | per-draw |
| `0x40f` | `0x48` / `0x60` | early/late test |
| `0x416`, `0x41a` | `0x140`, `0xa00` | Tegra 114 only |
| `0x501` | `0x22000000 \| 0x00320000 \| (0x12c << 4) \| 0xf` | `ALU_BUFFER_SIZE` companion |
| `0x502`, `0x503` | 0 | reset / per-draw |
| `0x542`..`0x544` | 0 | reset |
| `0x547` | two words | Tegra 114 only |
| `0x60e` | 0 | reset |
| `0x702` | 0 | reset |
| `0x740`, `0x741`, `0x742` | `0x035`, 0, 0 | before every draw |
| `0x750`..`0x75f` | 0 | reset, ordering-sensitive with `0xb00` |
| `0x770`..`0x79f` | 0 | Tegra 114 |
| `0x7e0`, `0x7e1` | 1, 0 | reset |
| `0x807` | 0 | reset |
| `0x902` | 0 | per-draw |
| `0x907`..`0x90b` | 0 | Tegra 30 reset |
| `0xa00`..`0xa0c` | see §11 | cache control |
| `0xb00`..`0xb14` | see §12 | stage aliases |
| `0xc00`..`0xc53` | 0 | pipe aliases |
| `0xe25`, `0xe27` | 0 / 1, 2 | unknown |
| `0xe30`..`0xe3f` | target limits | from ardrv |
| `0xe40`, `0xe41` | 0, 1/3 | unknown |
| `0xe44`, `0xe45` | 0 / depth params | Tegra 114 |
| `0xe70`..`0xe87` | see §14 | reset |

## 16. Gaps

* **Blocks 0xb00 (GSHIM) and 0xc00 (PIPEALIAS)** are only known to exist: every
  register in them is cleared at reset and two of them (`0xb00`, `0xe41`) are
  varied in a fixed sequence whose effect is only visible through the `0x750`
  ordering constraint.
* **`0xa02`..`0x0a08`**: six threshold words plus one more, programmed twice per
  draw with identical values. Flush thresholds, counters or both - not separated.
* **`0x501`**: the companion write to `0xe20`. Its high bits look like two packed
  constants (`0x2200`, `0x0032`) and the low half contains `0x12C << 4 | 0xF`, but
  no field has been isolated.
* **`0x40f`**: only two values are known (`0x48`, `0x60`) and only bit 6 has been
  tied to a behaviour (`STENCIL_WRITE_EARLY`).
* **`0xe26` (DITHER) and `0xe27`**: written per draw, effect not measured. A dither
  matrix per target would be expected to live around here, given
  `RT_PARAMS.dither_enable`.
* **Indices 6..15 of the render-target arrays** (`0xe06`.., `0xe16`..) are
  addressable and enabled-able but never used by Grate or the blob's observed
  paths; whether AR20 has 16 or fewer physical targets is unknown.
* **`TEXTURE_DESC1` bit 5**, bits 13..26 and bits 30..31, and `TEXTURE_DESC2`
  bits 0..5: the wrap/filter/format fields are measured, the rest are not.
