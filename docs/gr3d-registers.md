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
| `0x101 + 2i` | `ATTRIB_MODE(i)` | V | `type` 0..3, `size` 4..7 (**component count**, so a `vec4` writes 4), `stride` 8..31 (**bytes**). Measured from a `vec4` float32 attribute that renders correctly: `0x0000104d` = type `0xd`, size `4`, stride `16`. |
| `0x120` | `VP_ATTRIB_IN_OUT_SELECT` | V | `out_mask` 0..15, `in_mask` 16..31: one bit per export the vertex program may write and per attribute it may read. `0x00030081` = exports 0 and 7, attributes 0 and 1 - exactly what the program in `host1x_gr3d_triangle()` declares (`gl_Position`, `vcolor`, two attributes), which is what makes it a measurement rather than a guess. |
| `0x121` | `INDEX_PTR` | D | byte address of the index buffer (relocated). |
| `0x122` | `DRAW_PARAMS` | V | `primitive_type` 24..26, `provoking_vertex` 27, `index_mode` 28..29, `first` 0..23; bits 30..31 are always set (`value \|= 0xC0000000`) - clearing them is untested, but every working stream has them |
| `0x123` | `DRAW_PRIMITIVES` | V | `index_count - 1` at 20..31, `offset` (first index) at 0..19. Writing it starts the draw: three indices are pushed as `0x00200000` |
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

`VP_ATTRIB_IN_OUT_SELECT` is computed, not constant: Grate takes the program's
`attributes_use_mask`, splits it into an output mask (low 16 bits) and an input
mask (high 16 bits), intersects the input half with the attributes the context
has buffers for, drops the attributes whose buffer is missing, and writes
`in_mask << 16 | out_mask`. A shader using attribute 2 and exporting to 3 would
write `0x00040008`. The header itself has a defect worth knowing about: `IN12`
is defined as `0x04000000`, the same value as `IN10`.

Two further constraints come from the vertex ISA rather than from this register:
attribute and export indices are 4 bits each, and the *program* also declares
which attributes and constants it reads, so a mismatch between the mask and the
program leaves the program reading whatever the mask disabled (usually zero).

**The attribute `size` field is the component count, not count minus one.** The
old AR20 notes in [[Geometry Submission]] give `size` as bits 4..6 and `stride`
as bits 8..19; the measured value contradicts both - `0x0000104d` for a `vec4`
float32 attribute only decodes with `size` at 4..7 = 4 and `stride` at 8..31 = 16
bytes. Strides above 64 KiB are therefore expressible in principle.

## 3. Vertex program engine, VPE (`0x200`..`0x20b`)

| Addr | Name | Status | Notes |
|--:|:--|:--:|:--|
| `0x200`..`0x204` | — | ? | reset writes `0x11`, `0xffff`, `0xff0000`, `0`, `0` |
| `0x205` | `VP_UPLOAD_INST_ID` | V | upload pointer, `IMM 0x205 0` before every program |
| `0x206` | `VP_UPLOAD_INST` | V | instruction FIFO: `NONINCR 0x206 4*n`, then `4*n` words, one 128-bit VLIW instruction per four dwords, **high dword first** (`part3, part2, part1, part0`) |
| `0x207` | `VP_UPLOAD_CONST_ID` | V | constant-file pointer, `IMM 0x207 0` before every upload |
| `0x208` | `VP_UPLOAD_CONST` | V | `NONINCR 0x208 1024` = `256 * 4` words = the whole `vec4[256]` constant file, uploaded in full on every draw |
| `0x209`, `0x20a` | — | ? | cleared at reset |
| `0x20b` | — | ? | reset writes 3 |

`0x206` is the only path by which vertex instructions reach the engine; the
words are in [[Vertex Shader ISA]]. The constant window is `0x208` and is
independent of the fragment-side `FP_CONST` window at `0x820`.

### 3.1 What the vertex program may use

Limits that a vertex-side compiler or a hand-written probe has to respect
([[Vertex Shader ISA]]):

| Resource | Size |
|:--|:--|
| VLIW instructions | 256, one end-of-program bit |
| Local `vec4` registers | 32 |
| Input attribute `vec4` registers | 16 |
| Constant `vec4` registers | 256 (`0x207`/`0x208`) |
| Export `vec4` registers | 16 (index 31 = disabled) |
| Condition registers | 2, per-component (less/equal/greater than 0) |
| Address registers | 4 (`A0.xyzw`), with an 8-deep shared push/pop stack |

Register indices are 5 bits; 32..63 for `rA`/`rB`/`rC` and 32..62 for `rD` are
invalid, and **the program aborts if an invalid index appears in an instruction
even when that operand is unused**. Export indices 16..30 abort as well, and so
does a stack over- or underflow. Those are not silent: an aborted vertex
program leaves the primitive undrawn, which looks like an empty frame.

The upload is a full rewrite every draw - there is no dirty tracking - so the
constant file costs 1024 register writes per draw regardless of use. That is
visible in the timing work as part of the fixed per-draw cost.

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

### 4.1 Measured effects

Method: let Grate program the register normally, then override it with
`GRATE_POKE` immediately before the draw ([[Hardware Testing]], "Register
pokes"), run the 4x4 row-coloured `filter_quad` frame, untile it and diff against
the unpoked frame - 65532 pixels, 4 distinct colours.

| poke | differing pixels | reading |
|:--|--:|:--|
| `0x350=0x00000010` (max 16, min 0) | 61440, unchanged strip `x < 16` | `SCISSOR_HORIZ.max` is an **exclusive** upper bound; exactly the 16-column strip survives |
| `0x350=0x000f00ff` (min 15, max 255) | 4092 | `SCISSOR_HORIZ.min` clips from the left and is inclusive; the count is close to the 3840 pixels of a 15-column strip, the rest being edge pixels |
| `0x351=0x00000010` (max 16) | 61440, unchanged strip `y < 16` | same rule on the vertical axis, one register per axis |
| `0x350`+`0x351` | 65280, 256 unchanged | the untouched set is the 16x16 intersection, so the two axes clip independently |
| `0x352=0` (X bias) | 32768, changed region `x < 128` | the primitive moves by half a frame: X bias is a pre-scale offset, and Grate's value (`width * 8`) places the quad at the left edge |
| `0x353=0` (Y bias) | 65532 | same on y; the whole frame changes because the moved edge is the vertical one |
| `0x355=0.5` (X scale) | 65532, one colour | halving the horizontal scale shrinks the quad out of the frame - scale is applied to the post-bias coordinate |
| `0x357=0` (Z scale) | 0 | colour output does not depend on `z` when no depth surface is bound |
| `0x358`,`0x359` = 0.5 | 49148 | guardband width/height clip the primitive, so they are real clip bounds rather than hints |
| `0x358`,`0x359` = 0.0625 | 65528 | a smaller guardband clips more - monotone in the bound |
| `0x343=0` | 1020, scattered | clearing the word also clears `linker_inst_count` (bits 5..9) and `front_cw`, so varying interpolation degrades. The register is **not** a pure cull-face field, and this is why "disable culling" by zeroing it gives a mostly-right frame with 1020 wrong pixels |
| `0x403=0` (depth params) | 0 | no colour effect: with no depth surface bound the depth stage is inert |
| `0x402=0` (stencil params) | 0 | same for stencil |
| `0x34c=0` (line params) | 0 | triangles are unaffected |
| `0x347=1.0` (point size) | 0 | triangles are unaffected |

So of the state registers probed, the scissor pair, the viewport bias/scale and the
guardband all have directly measurable geometry effects, while the depth and
stencil words have none unless the corresponding surface is bound, and the
line/point words only apply to their own primitive types.

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
| `0x501` | — | V | second half of the `ALU_BUFFER_SIZE` programming: Grate writes `0x223212cf`. Bits 4..15 (`0x12C << 4`) must be non-zero, otherwise nothing reaches the target; every other field is cosmetic (6.2). Reset writes 7. Not in `tgr_3d.xml.h` |
| `0x502` | — | ? | cleared at reset |
| `0x503` | — | ? | cleared before every draw |
| `0x520` | `FP_PSEQ_ENGINE_INST` | V | `0x20006000 \| batch_count`. Starts the sequencer; the count is the number of batches **executed**: under-counting silently truncates the program, over-counting runs past it, 0 and >= 128 hang, bits 29 and 14 are mandatory (6.1) |
| `0x540` | `FP_PSEQ_UPLOAD_INST_ID` | D | upload pointer |
| `0x541` | `FP_PSEQ_UPLOAD_INST` | D | PSEQ word, one per batch |
| `0x542`..`0x544` | — | ? | cleared at reset |
| `0x545` | `FP_PSEQ_QUAD_ID` | V | written 0 before every draw; 0..`0xffffffff` all give a bit-identical frame on a single-quad program (6.1) |
| `0x546` | `FP_PSEQ_DW_CFG` | V | `pseq_to_dw_exec_nb` 6..31; inert on both measured program shapes (6.1) |
| `0x547` | — | ? | Tegra 114 only: `INCR(0x547, 2)` then `0xc0000000`, `0`. Not in `tgr_3d.xml.h` |

### 6.1 Measured: the count field is the number of batches executed

`0x520` is `0x20006000 | n`, and `n` is not a hint: it is how many batches the
sequencer runs. Poking it after Grate programmed the context, on a 2-batch
textured program and on the 4-batch ALU program:

| `n` | 2-batch textured program | 4-batch ALU program |
|--:|:--|:--|
| 0 | **hangs** | - |
| 1 | frame loses the TEX/DW tail, no hang | tail lost, no store |
| 2 | **bit-identical to the unpoked frame** | tail lost, same partial frame as `n = 1` |
| 3 | identical | - |
| 4 | different frame | **bit-identical** (the true count) |
| 8 | different frame | identical |
| 16, 32, 64 | different frame, all three the same | - |
| 128, 256, 512, 1024, 2048 | **hangs** | - |

Three consequences for a driver or compiler:

* **Under-counting is silent.** As far as the engine is concerned the program ran
  to completion; the missing batches simply never execute and the picture is
  wrong with no error. The count has to be derived from the schedule streams, not
  left at a constant.
* **Over-counting is undefined, not harmless.** The sequencer keeps reading
  schedule entries past the end of the program, which hold whatever the next
  uploaded stream left there. On the textured program 16 to 64 give a frame that
  differs from both the correct one and each other's, and 128 and above hang.
* **The constant bits are not decoration.** Setting one bit at a time on top of
  the minimum working word `0x20004000 | 2`, same textured frame:

  | bit | mask | result |
  |--:|--:|:--|
  | 12 | `0x1000` | **store lost** - same frame as a count of 1 |
  | 13 | `0x2000` | identical (optional) |
  | 14 | `0x4000` | **mandatory**: clearing it hangs |
  | 15..28 | `0x8000`..`0x10000000` | identical (all fourteen checked) |
  | 29 | `0x20000000` | **mandatory**: clearing it hangs |
  | 30, 31 | `0x40000000`, `0x80000000` | **hang** |

  The minimum working word is `0x20004000 | n` and `0x20006000 | n` is what the
  driver writes. Bit 12 is not part of the count: with it set the frame comes back
  exactly as if the DW batch had been skipped and the count stops mattering - on
  the 4-batch ALU program, count 4 with bit 12 gives the same frame as count 3
  without it - whereas a large count instead changes the picture or hangs.
  The suppression is **global, not colour-only**. With the same bit set, in three
  independently measured programs:

  | store | without bit 12 | with bit 12 |
  |:--|:--|:--|
  | `DW: store stencil` after a `kill` write | stencil `9A` | stencil `00` |
  | `DW: store rt0, r2, r3` with a `D16` depth target | depth `0000` | depth `8000` (the clear value) |
  | `DW: store rt3, r0, r1` with a coverage target | coverage `0x200` per pixel | coverage all zero |

  So bit 12 gates every `DW` store regardless of target, which makes
  `0x520 = 0x20005000 | n` a usable "run the whole program, store nothing" control.
  Grate exposes it as `GRATE_NO_STORE=1` (see [[Hardware Testing]]), and it is the
  check that should precede any claim that a register is inert.

Skipping a batch skips all of it. A two-batch program that writes `kill` in the
first batch (which moves the stencil update behind the shader) and does
`DW: store stencil` in the second writes `0x9A` into the stencil buffer at counts
2 and 3 and leaves it completely clear at count 1: the dropped batch's DW does not
run at all, side effects included. Without the `kill` write the same program
writes `0x9A` at every count, because QRast then updates the stencil before the
shader tail ([[Fragment Shader ISA]] 8.2) - a trap when designing this kind of
test, since the result then says nothing about the batch count.

The count cannot be mis-declared on the assembler path, because it is derived
rather than written out: `shader->pseq_inst_nb` is `asm_fs_instructions_nb`, the
number of `EXEC` blocks the parser produced, and the parser already rejects more
than 64 batches - well inside the field. A compiler has to derive it the same way
from its own schedule.

`0x545` (`FP_PSEQ_QUAD_ID`) is inert in every shape tried: a textured full-frame
draw (thousands of 2x2 quads, so "only one quad in flight" is not the
explanation), the 4-batch ALU program, and a `kill`-writing stencil program with a
coverage target bound - 0, 1, 2, 3, `0xff` and `0xffffffff` all give a
bit-identical frame and identical stencil readbacks. Whatever consumes it is not
reachable with these draw shapes, so the field stays open.

`pseq_to_dw_exec_nb` (`0x546`) is driver state, not a compiler output, and no
value in its field is observable: the blob never writes `0x546` itself, and 0, 1,
3, 8, 16 and 127 leave a 2-batch program's frame bit-identical, as they do the
4-batch ALU program. It is a pipeline-depth knob this experiment cannot see.

### 6.2 Measured: `0x501` has exactly one field that matters

The `0x501` word is written unconditionally next to `0xe20` as `0x223212cf`.
Poking it field by field (same whole-frame test):

| value | frame |
|:--|:--|
| `0x223212cf` (the driver's own value) | identical |
| `0x000012cf`, `0x000012c0` | identical |
| `0x003212cf` | identical |
| `0x2232064f` (`0x12C` field changed to `0x64`) | identical |
| `0x223212ce` (low nibble `0xe`) | identical |
| `0x003212cf` (high `0x2200` field cleared) | identical |
| `0x0000000f` (low nibble only) | **store lost** |
| `0x00320000` | **store lost** |
| `0x22000000` | **store lost** |
| `0x2232000f` | **store lost** |
| 0, 1 | **store lost** |

The only load-bearing part is bits 4..15, the `0x12C << 4` field, and only in
that it must be non-zero: `0x64` in its place is fine, and changing the low nibble
or either high constant field is fine. "Store lost" means the frame came back with
the same hash as an `0x520` count of 1 - the ALU work ran but nothing reached the
render target - which fits the field being the ALU instruction buffer size, the
companion of `0xe20` (`0x12C` = 300). Because `0xe20 = 2` and `4` hang the engine
(see 14.1), the only `0x501`/`0xe20` pairs that have ever run here are the size 1
and size 3 ones.

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
### 8.1 Measured: which TEX registers matter

Same method as 4.1 - poke after Grate programmed the context, diff the whole
textured frame (4x4 power-of-two `RGBA8888`, row-coloured, mip chain unbound).
`0x740`, `0x741`, `0x742`, all sixteen words `0x750..0x75f` (with `1` and
`0xffffffff`) and `0x7e0`/`0x7e1` (with `0`, `1` and `0xffffffff`) each gave
**zero differing pixels out of 65532** and no hang.

So the block is inert for the sampling path on AR20 with a power-of-two texture.
That does not contradict the ordering constraint in section 8 - the only case
where a `0x750` write has ever been seen to matter is a **non-power-of-two**
texture, and the corruption there comes from the write landing in an unexpected
pipe, not from the value itself being used by the sampler.

What *does* control sampling, measured elsewhere: `TEXTURE_POINTER` (`0x710+i`),
`TEXTURE_DESC1` (`0x720+2i`) and `TEXTURE_DESC2` (`0x721+2i`), plus the sampler
index inside the TEX instruction word ([[Fragment Shader ISA]] section 9).

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
| `0xa00` | `FDC_CONTROL` | D | bit 0 = invalidate. Grate also writes larger values (`0xe00`, `0xe01`) as a control word; the upper bits are not decoded. **Bit 4 hangs the engine** (measured, section 11.1) |
| `0xa01` | — | D | second control word, written with the `0xa02` block |
| `0xa02`..`0xa07` | — | ? | six threshold/counter words, `0x1ff, 0x1ff, 0x1ff, 0x30, 0x20, 0x30` in every Grate stream |
| `0xa08` | — | ? | written `0x100`; **bit 16 hangs the engine** (measured, section 11.1) |
| `0xa09`..`0xa0c` | — | ? | cleared at reset |

The FDC is the unified cache used by QRast, PSEQ and DW. The `0xa02` block is
programmed identically at init and before every draw, which suggests
flush/threshold configuration rather than per-draw state.

### 11.1 Measured: none of it affects rendering, and two bits hang the engine

Whole-frame test as in 4.1 (4x4 row-coloured textured `filter_quad`, 65532
pixels compared by hash). Every one of these left the frame **bit-identical**:

| registers | values |
|:--|:--|
| `0xa01` | 0, 1, `0xffffffff` |
| `0xa02`..`0xa07` | 0, `0x1ff`, `0xffffffff` |
| `0xa08` | 0, `0x100`, `0x1ff` |
| `0xa09`..`0xa0c` | 0, `0xffffffff` |
| `0xa00` | 0, 1, 2, `0x100` |

So whatever the FDC block configures, none of it is visible in the colour result
of a single-quad draw - consistent with the block being cache policy rather than
pipeline state.

Timing rules it out as a performance knob as well. A first sweep looked
promising - every poke came out "faster" than the unpoked frame, `0xa02`..`0xa07`
all-ones by 12% on the ALU path - but that was the sweep order, not the register:
repeating base at the end of the same sweep moved it by as much as the pokes did.
Interleaved A/B/A/B, four rounds each, median of 100 draws (11-sample textured
shader and the 4-batch ALU shader):

| shader | base | `0xa02`..`0xa07` = `0xffffffff` | base spread over the sweep |
|:--|--:|--:|--:|
| 11 TEX samples, 100 draws | 447.6 ms | 452.2 ms | 9.5 ms |
| 12 ALU packets, 4 batches, 100 draws | 216.6 ms | 210.8 ms | 28.1 ms |

Both differences sit inside the run-to-run spread, so these words cost nothing
measurable on either path. The drift itself is documented in
[[Hardware Testing]], "Timing a draw loop".

Two single bits **hang the engine**, reproducibly, and the channel recovers once
the offending process is killed:

| register | bit | evidence |
|:--|:--|:--|
| `0xa00` | **4** (`0x10`) | `0x10` hangs; `0x2`, `0x4`, `0x6`, `0x8`, `0x20`, `0x40`, `0x80` run; `0xf8`, `0x1f8`, `0x1ff`, `0xffffffff` hang because they contain it |
| `0xa08` | **16** (`0x10000`) | `0x200`, `0x400`, `0x800`, `0x1000`, `0x8000`, `0x80000000` run; `0x10000` and `0xffffff00` hang |

A driver that pokes this block must keep those two bits clear; nothing in Grate's
steady-state path touches them (`0xa00` is written `0xe00`/`0xe01` and `0xa08` as
`0x100`).

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

**Measured: inert for rendering on AR20.** `0xb00` was poked to 0, 1, 2, 3, 4, 7,
15 and `0x1ff` immediately before the draw, and `0xe41` to 0, 1, 2, 3 and 4, on a
full-screen textured quad (4x4 `RGBA8888`, row-coloured, `filter_quad_fs`). Whole
frames - 65532 pixels each - were compared: **zero differing pixels for every
value**, and no hangs. Neither register changes what the fragment pipeline
produces. A second program shape - four batches holding three ALU packets each
(`tests/grate/asm/perf/fill_4b_12p.txt`) - gave the same zero-diff result, so the
inertness is not an artefact of a single-batch program.

The one effect that has ever been observed for this block is an *ordering*
constraint with `0x750`: writing the texture descriptors before `0xb00 = 3`
corrupts non-power-of-two textures (see section 8). That is consistent with a
per-stage alias window - a write to `0x750` lands in whichever pipe `0xb00`
selects - and it is why the reset sequence walks `0xb00` through 1, 2, 3 with a
matching `0xe41` write. What each stage actually holds is still unknown; what is
known is that none of it feeds the colour path on AR20.

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
aliased into the main register space.

**Measured: inert for rendering on AR20.** `0xc00`, `0xc01`, `0xc02`, `0xc03`,
`0xc30`, `0xc31`, `0xc40` and `0xc50` were each poked with `0xffffffff`,
`0x000001ff` and `0x00000001` before the draw, on the same full-frame textured
quad as section 12, and again with the four-batch/three-packet ALU program. Every
value gave **zero differing pixels** and no hang, so if the block is an alias
window for per-pipe state, none of that state is reachable from a fragment
program of either shape.

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

* **Blocks 0xb00 (GSHIM) and 0xc00 (PIPEALIAS)**: now measured to be inert for
  the fragment path on AR20 (sections 12 and 13) - every register poked with
  several values changed no pixel of a textured full frame. What remains is the
  *stage* semantics of `0xb00` (it selects something that only shows up through
  the `0x750` ordering constraint) and whether the `0xc00` groups need a
  multi-packet ALU program, a second pipe or a non-AR20 chip to do anything.
* **`0xa02`..`0xa08`**: measured to change neither the frame nor the time
  (section 11.1), so the block is policy this chip does not require from
  software. *What* it configures is still unknown, but nothing a compiler or a
  driver does depends on the answer.
* **`0x40f`**: bit 6 (`STENCIL_WRITE_EARLY`) is tied to a behaviour. Of the other
  bits, `0x00`, `0x08`, `0x48`, `0x60` and `0x78` all leave a plain colour draw
  bit-identical, and `0xffffffff` **hangs the engine** - so the word is a trap for
  a "clear it and see" sweep. What the other bits select is still unknown.
* **`0xe26` (DITHER) and `0xe27`**: inert on the 8-bit colour path. With a
  constant-grey store, `0xe26` = 0, 1, `0x7`, `0xff`, `0xffffffff` and `0xe27` = 0,
  1, 2, `0xffffffff` all give a bit-identical frame containing a single distinct
  word, and the store-nothing control was run first so the channel was known
  alive. Reduced precision is the case a dither matrix exists for, and it is
  still untested: the 565 target on index 5 could not be written in this harness
  (its readback is all-zero even with no poke), so `RT_PARAMS.dither_enable`
  stays unexplained.
* **Indices 6..15 of the render-target arrays** (`0xe06`.., `0xe16`..) are
  addressable and enabled-able but never used by Grate or the blob's observed
  paths; whether AR20 has 16 or fewer physical targets is unknown.
* **`TEXTURE_DESC1` bit 5**, bits 13..26 and bits 30..31, and `TEXTURE_DESC2`
  bits 0..5: the wrap/filter/format fields are measured, the rest are not.
