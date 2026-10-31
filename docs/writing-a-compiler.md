# Writing a fragment-shader compiler for AR20

This document is a gap analysis, not an ISA reference. The ISA encoding itself
lives in [[Fragment Shader ISA]]; the blob compiler's internals live in
[[Compiler Internals]]. The question answered here is: **if you sit down to
write a NIR -> AR20 backend for Mesa: which facts are needed, which are
established, and which are missing?**

It is written against the driver in `src/gallium/drivers/grate/` (Mesa), where
`grate_program.c` currently converts NIR to TGSI and `fp/pack.c` can already
pack ALU / MFU / DW / schedule words. Everything below is marked as one of:

* **known** - read off the encoding, the blob, or measured on hardware;
* **inferred** - consistent with the evidence but not isolated by an experiment;
* **open** - genuinely unknown, with the experiment that would settle it.

### Where each fact lives

This document deliberately holds only backend design and the gap list. The
hardware facts moved out of it, and the section numbers that used to name them
appear all over these notes - so the mapping is:

| was here | now in |
|:--|:--|
| 2.2 Instruction budget | [[Fragment Shader ISA]] - "Instruction budget" |
| 2.3 Register file | [[Fragment Shader ISA]] - "Register map and limits" |
| 2.4 Precision | [[Fragment Shader ISA]] - "Precision limits (measured)" |
| 4.1 Batch structure, 4.2 per-unit constraints | [[Fragment Shader ISA]] - "Batch structure and per-unit limits" |
| 4.3 Forwarding | [[Fragment Shader ISA]] - "Forwarding and the ALU pipeline" |
| 4.5 Arithmetic semantics | [[Fragment Shader ISA]] - "Arithmetic semantics" |
| 4.6 Batch-fill cost | [[Fragment Shader ISA]] - "Performance model" |
| 5 Program layout / uploads | [[Command Stream]] - "Fragment program upload sequence" |
| 5.1 Container header, 5.2 chunks | [[Fragment Shader ISA]] - "Program container, chunks and recirculation" |
| 6 Varyings and the linker | [[Shader Linking]] (rows, interpolation, TRAM) |
| 5.3 and 9.1 driver state (`0xe20`, `0xe21`, per-unit uploads) | [[GR3D Registers]] |
| 10 Measurement recipes | stays here, and the individual probes cite the ISA sections by name |

---

## 1. Where the existing code stops

The driver in the tree is a **TGSI** driver: `nir_to_tgsi()` then `fp/tgsi.c`.
TGSI is Mesa's legacy IR and is not what a future driver will use, so treat that
half as disposable. What *is* worth keeping is IR-agnostic:

* `fp/pack.c` - the bit packers for ALU, MFU, DW and schedule words;
* the command-stream emitter in `grate_create_fs_state()` - all six uploads,
  the register map in section 5, and TRAM setup.

Neither knows anything about TGSI. Everything between `nir_shader` and the
packers has to be written from scratch; the TGSI path is only useful as an
executable reference for the packing and the upload sequence.

What a NIR-native backend needs that does not exist at all today:

| Piece | State today |
|:--|:--|
| NIR traversal | none. `nir_to_tgsi()` is the only consumer of NIR, and the TGSI side then handles **only `MOV`**, with `assert(0)` on everything else |
| `nir_lower_*` pipeline | none: no integer lowering, no `nir_lower_alu` to a legal op set, no IO lowering to a varying map, no precision assignment |
| Instruction selection | `fp_alu_sMOV()` only, i.e. `MAD dst, src, 1, 0, 1` |
| MFU | only the hard-coded barycentric prologue (`RCP r4` + two `MUL`s), injected by a HACK that clobbers real instructions |
| TEX | not emitted at all (`TODO: emit actual instructions here`, words are zero) |
| PSEQ | `TODO: PSEQ`, words are zero |
| Register allocation | none - `TGSI` register indices are used directly as hardware indices, outputs are forced to `r2`/`r3` |
| Scheduling | none - one ALU packet and one MFU word per NIR instruction, `address` = list index |
| Branch/loop lowering | none (and none is possible - see 2.1) |
| Uniforms | vertex constants are uploaded (`emit_vs_uniforms`), fragment constants are **never uploaded** |
| Textures | sampler state is parsed but nothing translates it into TEX words |

So the work is: legalisation, instruction selection, register allocation, and a
list scheduler that understands a real (and unusual) forwarding model.

## 2. The machine as a compiler sees it

### 2.1 There is no control flow at all

**Known.** No branch, jump, call, or predicate-guarded instruction exists in any
of the five streams. The only "conditional" things are:

* ALU condition codes (bits 54..55) which turn the *result* into `0.0`/`1.0`;
* `CSEL` (ALU opcode 3) which selects between `rB` and `rC*rD` on `rA < 0`;
* the kill register (destination 76), which discards the fragment.

Consequences for a NIR backend, and they are not optional:

* **All control flow must be flattened before selection.** `if` becomes `CSEL`
  (or arithmetic masking), loops must be unrolled, `break`/`continue` must be
  unrolled or rejected. Measured on the blob: an `if`/`else` becomes the same
  two-batch shape as straight-line code (no PSEQ word changes), and a
  `for (i = 0; i < 300; i++) s += v;` comes out as **36 batches, 400 ALU
  entries and zero PSEQ words** - it is strength-reduced into repeated adds
  rather than emitted as a loop, which is the only thing the ISA allows. That is
  also where the 64-batch budget bites: the loop bound a backend can afford is a
  function of the ops in the body, not of the trip count alone.
* **`discard` is not a branch either** - it is an ALU write of a non-zero value
  to register 76, with the compare either folded onto that write or computed in
  an earlier batch: a condition register written and read inside one batch reads
  as zero, so a same-batch compare silently never fires (see the ISA document,
  section 5.7). It also has a driver-side effect: the host must switch the
  stencil test to late (`0x40f = 0x60` instead of `0x48`, see
  `grate_3d_set_late_test()`) and set the `discards_fragment` flag.
  **And it obliges the program to write the surfaces itself.** A discard-capable
  fragment program compiled by the blob is four batches, not one:

  ```
  batch 1  mad_le r12.hl, c0, #1, c1.l     ; the condition
           ...                            ; r14/r15 setup (kill + mask)
           DW: store rt3, r0, r1          ; coverage
  batch 2  PSEQ: load rt2 r0, r1          ; fetch the stencil surface
           DW: store rt2, r0, r1          ; write it back
  batch 3  DW: store rt0, r0, r1          ; depth
  batch 4  DW: store rt1, r2, r3          ; colour
  ```

  Measurement refines that reading. A `kill` write makes the driver set the late
  test (`0x40f = 0x60`) and clear `STENCIL_WRITE_EARLY`, and the two surfaces then
  behave differently:

  | surface | program without `kill` | program with `kill` (even `kill = 0`) |
  |:--|:--|:--|
  | depth `rt0`, test `ALWAYS`, write on | `8000` everywhere | `8000` everywhere, killed fragments included |
  | stencil `rt2`, ref `0x7F`, `REPLACE`, `store rt1` only | `7F` everywhere | `00` everywhere |
  | stencil, same but ending in `DW: store stencil` | `7F` everywhere | `7F` everywhere |

  Depth is written **early and unconditionally**, so a discarded fragment still
  stamps its interpolated `z` - a backend does not need to re-emit it. Stencil
  moves behind the shader: the fixed-function update stops as soon as the program
  writes the kill register (a constant-zero `kill` is enough), and `DW: store
  stencil` is what performs it. The blob is therefore right about its batch 2,
  and a backend must emit that store for every discard-capable program that has a
  stencil buffer. The committed value is the test result (the reference value
  under `REPLACE`), and the `PSEQ: 0x00820008` fetch returns the current surface
  value for the ops that need to see it - normalised, so ref `0xC8` reads back as
  `0xC7` through an `RGBA8888` store and `0x7F` as `0x7F`. With the test disabled
  the store commits nothing, and a program that discards every fragment commits
  nothing either. The coverage target (`rt3`) works too: the four-batch shape
  replays end to end, with `rt3` filled (`nz = 65536`) and `rt2` at the reference
  value. `tests/grate/stencil.c` exercises the same shape.
* **Derivatives, and therefore `textureGrad`, are out of reach** unless
  computed per-quad by hand: there is no quad/neighbour data path. Texture LOD
  is chosen by the TEX unit itself (`TEX` uses the unit's own LOD selection,
  `TXB` takes a bias from `r3`), and it can also be forced outright: TEX bit 13
  with bit 12 clear reads an *absolute* LOD from `r3` (section 10.4). So
  `textureLod` with a LOD the shader can compute is expressible; only the
  implicit-gradient form is not.

  The blob *accepts* `#extension GL_OES_standard_derivatives` and `dFdx`/`dFdy`/
  `fwidth`, which is misleading. Its output for `dFdx(v)`, `dFdy(v)`, `fwidth(v)`
  on a float varying is three batches and contains **no neighbour fetch, no new
  opcode and no `PSEQ: load`**: batch 1 interpolates the varying and stores it to
  `rt1` (`DW: store rt1, r0, r1`), batches 2 and 3 then do arithmetic on the
  fragment constants (`c0`, `c8`, `c16`, `c24`), the row registers and the ALU
  latches (`d0`, `d1`, `d2`). Since nothing in that stream reads another
  fragment's value, the derivative is not actually computed - treat the
  extension as unsupported and reject it rather than copy the blob's sequence.

  The odd operand names in that dump are now fully explained; they are suffixes
  of Grate's `libcgc` disassembler (`src/libcgc/shader.c`), not unnamed register
  classes:

  * `d0`, `d1`, `d2` are register numbers 24..27, the ALU result latches, which
    are readable as ordinary operands;
  * `r7_div2` is row register 7 written with result scale `3` (`/2`, bits
    57..58); `_mul2`/`_mul4` are scales 1 and 2;
  * `r2+`, `r3+`, `r31+` are destinations with bit 61 set, which `libcgc` calls
    "accumulate" and prints as a `+`; it is the same bit the assembler exposes
    as `(other)` and this document calls "send". `r31` is the lowp `vec2(0,1)`
    register, so `r31+` writes nowhere.

The blob behaves the same way: the ISA reference notes that the multi-pass
program-sequencer recirculation is "not used in practice, BLOB driver gives up on
large shaders".


### 2.2 Hardware facts (moved to the ISA reference)

The budget, register file and precision sections that used to be here describe the
machine, so they now live in [[Fragment Shader ISA]], at:

* "Instruction budget" - 64 batches, 64 ALU packets, per-batch unit limits;
* "Register file" - 16 fp20 / 32 fx10 row registers, globals, ALU latches,
  embedded constants, condition registers, position/face/kill;
* "Precision and format limits" - fp20 and fx10 ranges and where they clamp.

What a backend needs from them in one line each: **64 batches and 64 ALU packets
per chunk**; **16 fp20 (32 fx10) row registers, 8 globals, 4 ALU latches, 3
embedded constants per batch, 32 uniform registers**; and **no fp32 and no
integer anywhere**, so `nir_lower_int`/`nir_lower_fp64` and a precision pass are
mandatory before selection.
## 3. Instruction selection

### 3.1 What maps cleanly

| NIR | AR20 | Notes |
|:--|:--|:--|
| `fadd`, `fsub`, `ffma`, `fmul` | `MAD` | `x*y + z*w`; `fmul` = `MAD` with addition disable (bit 59), gives two fx10 multiplies |
| `fmin`, `fmax` | `MIN`/`MAX` | `min(rA*rB, rC*rD)` |
| `fneg`, `fabs` | operand bits 1 / 2 | free modifiers on rA/rB/rC |
| `x * 2` on a source | operand bit 0 | free |
| result `*2`, `*4`, `/2` | dst bits 57..58 | free |
| `saturate` | dst bit 56 | free |
| `fsqrt` | `SQRT` (MFU) | |
| `frsq` | `RSQ` | |
| `frcp` | `RCP` | |
| `flog2` | `LG2` | |
| `fexp2` | `PREEX2` then `EX2` | two MFU ops, back to back in consecutive batches |
| `fsin`, `fcos` | `PRESIN`+`SIN`, `PRECOS`+`COS` | same pairing rule |
| `ffract` | `FRC` | |
| `fcmp`/`slt`/`sge` | `CMP` + condition code | result is `0.0`/`1.0`, not a mask |
| `bcsel`/`select` on `a < 0` | `CSEL` | only for `< 0`; other comparisons need a compare first |
| `tex`/`txb` | `TEX`/`TXB` | coords must be in `r0..r3`; implicit LOD costs **no** ALU/MFU work (measured, see 4.4) |
| `textureLod` | `TEX` with bit 13, LOD in `r3` | bit 12 must be clear; only reachable through a raw word today (section 10.4) |
| `store_output` | `DW` | payload must be in `r2/r3` or `r0/r1`; **one DW per batch**. Index 1 is the only target AR20 writes |
| `store_output` to `gl_FragData[1]` | `DW: store rt5` | render target **5**, not 2. The spelling is right and the slot is not implemented on AR20, so the write reaches nothing (see the MRT note below) |
| `load_input` (varying) | `MFU ipl` | one `var` slot per component; precision decides slot count |
| `load_frag_coord` | `posx`/`posy` + the `r3` z expansion | z needs the documented `1/1000 + max(0,r3.low)/4000 + max(0,r3.high)/4` sequence, i.e. ~6 ALU slots |

**Multiple render targets: the encoding is measured, the hardware is not there.**
`gl_FragData[0] = v0` emits one batch with `DW: store rt1, r2, r3`. Writing
`gl_FragData[1]` as well adds a *second batch* whose only content is
`DW: store rt5, r2, r3` (`0x00028015`), with the second buffer's value
re-materialised into `r6/r7` from a constant:

```
batch 1   (varying fetch, ALU)   DW: store rt1, r2, r3
batch 2                          DW: store rt5, r2, r3
```

Four things a backend has to know:

* the second colour target is render target **5**, not 2 (the index space is
  0 = depth path, 1 = colour 0, 2 = stencil, 3 = coverage, 4 = spill,
  5 = colour 1, the blob's `SURF_OUT1`);
* **one DW per batch**, so each extra target costs a whole batch against a
  64-batch budget;
* **on AR20 index 5 accepts the store and writes nothing.** All five targets can
  be enabled and parameterised at once (`RT_ENABLE` = `0x0001002f`), and the
  index-5 surface still stays zero over six variants, including one with
  `RT_PARAMS(5)` copied from a working colour word ([[GR3D Registers]] 14.3,
  [[Fragment Shader ISA]] 8.2). AR20 is a GL ES 2.0 part, where `gl_FragData`
  with an index above 0 does not exist, so a backend for it should refuse MRT
  rather than spend a batch emitting the word;
* **the blob compiles the write anyway, with 0 errors**, so a driver must gate
  `gl_FragData[n]` itself instead of assuming the compiler reports it.

The `PRE`/main pairs are one operation in two words and this is now measured
rather than assumed (4.5): the two words may be in the same batch, consecutive
batches, or separated by an empty batch, but they must be given the same
operand, the `PRE` result cannot be captured as a value, and dropping the `PRE`
word silently produces a wrong result. So selection and scheduling have to agree
only on the *pair* - the words are free to move otherwise. The same is true of the barycentric prologue: `RCP r4` + two
`mul`s + the `bar` destination must be the first MFU word of the program, and
`w` arrives in `r4` for that first word only.

**The prologue is conditional, and the condition is "anything reads a varying".**
A shader with no varyings at all (`gl_FragColor = vec4(1.0, 0.0, 0.0, 1.0)`) gets
no MFU word whatsoever, and its whole body is four ALU slots in one batch:

```
PSEQ:001    nop
TEX :001    nop
ALU :001    mad r2._l, #1, #1, #0
ALU :001    mad r2.h_, #0, #1, #0
ALU :001    mad r3._l, #0, #1, #0
ALU :001    mad r3.h_, #1, #1, #0
DW  :001    store rt1, r2, r3
```

So a constant-colour program is one batch, one ALU packet, and a backend can
omit the `RCP r4`/`bar` prologue entirely when the shader interpolates nothing
(which matters because the prologue otherwise costs the program's first MFU
word and a dependency on `r4`). Note also that this dump shows the constant
being written straight into `r2/r3` in fx10 halves (`. _l` / `.h_`) - the DW
stage consumes fx10, so an fp20-only ALU result must be converted before the
store unless it is written with `.lh`.

### 3.2 What has no encoding at all

These have to be lowered or emulated, and their cost is what usually blows the
256-slot budget:

* **integer ops** (add/mul/and/shift/compare) - no integer ALU exists. Emulate
  in fp20, or require them constant-folded.
* **bitwise ops on anything not constant** - impossible in general.
* **`ffloor`, `ftrunc`, `froundeven`, `fceil`** - only `FRC` (fract) exists.
  `floor(x) = x - fract(x)` works for positive `x`; for negative `x` it needs a
  sign fixup. Round-to-even is a composition.
* **`fmod`, `fpow`, `fexp`, `flog`, `fsinh`, `fcosh`, `fatan`, `fdiv`** -
  compositions (`fdiv` = `RCP` + two `MUL`s via the documented projective divide
  pattern, which the blob emits as `IR_PDIV`).
* **anything needing more than 3 immediates per batch** - immediates are
  per-batch and steal ALU slot 3 of that batch.
* **uniform array indexing / relative addressing** - the *fragment* unit has no
  relative addressing (the vertex unit does). Every uniform access must be a
  constant index, so dynamic array indices must be specialised per draw
  (uniform remap) or rejected.
* **memory access of any kind** - no load/store, no SSBO, no images.
* **derivatives, `interpolateAt*`** - see 2.1.

## 4. Scheduling


### 4.1-4.3 Hardware behaviour (moved to the ISA reference)

The batch layout, the per-unit constraints and the whole forwarding model are
properties of the machine and now live in [[Fragment Shader ISA]]:

* "Batch structure and per-unit limits" - the schedule word (`count[1:0]`,
  `address[7:2]`) and what each unit can issue per batch;
* "Forwarding and the ALU pipeline" - the measured readback byte order, which
  producer/consumer pairs work, the `aluN` latches, the fp20-halves rule and
  `(other)`/`(this)`.

The compiler-facing consequence is the scheduler rule: **build the intra-batch
dependency graph over `aluN`, not over virtual registers**, chain
`slot k writes -> slot k+1 reads`, and write the chain tail back to a row
register with `.lh`. A value a later batch must read has to be written `.lh`
(fp20), never `.l*`/`.*h` (fx10).
### 4.4 What is still missing for a scheduler

**Open:**

1. ~~**TEX result -> ALU distance.**~~ - **closed**, see [[Fragment Shader ISA]], "Forwarding and the ALU pipeline": the sample is
   visible to the ALU in the same batch and in the next one, and ALU-computed
   fp20 coordinates reach the unit one batch later. The only remaining TEX
   question is `LOD` selection itself, not scheduling - partly closed since:
   the bias operand works and computed LOD clamps at 0, and explicit LOD is
   TEX bit 13 with bit 12 clear (section 10.4).

   Measured on the blob, and this is the answer a backend needs: a shader whose
   whole body is `gl_FragColor = texture2D(tex, v_uv)` compiles to **one batch
   with no ALU packet and no MFU arithmetic** - just the varying fetch, `TEX`
   with bit 10 set and the store. So implicit LOD is entirely the TEX unit's
   work (`bit 13` clear, `bit 12` clear): the compiler emits nothing for it, and
   `textureGrad` is the only form that would need per-quad data.

   The same two shaders also pin the pair-select bits of the TEX word, which the
   ISA table leaves as "???". Raw words (`cgc -F -s -r`):

   | shader | TEX word | set bits |
   |:--|:--|:--|
   | `texture2D(tex, v_uv)` | `0x00000430` | 4, 5, 10 |
   | `texture2D(tex, v_uv, b)` | `0x00001400` | 10, 12 |

   Bit 10 is enable and bit 12 is the bias request, as documented. Bits 4 and 5
   are the **coordinate pair** and **destination pair** selectors (`1` = the
   `r2/r3` pair, `0` = `r0/r1`), and they differ between the two dumps because
   the bias variant had already fetched its coordinates into the low pair: in the
   plain sample both the coordinates and the destination are in `r2/r3` (hence
   `DW: store rt1, r2, r3`), in the bias variant both are in `r0/r1` (hence
   `DW: store rt1, r0, r1`). So a backend choosing where to put coordinates and
   where to receive the sample must set these two bits to match - the unit does
   not take the pair from the schedule word.
2. ~~**Cycle cost of a partially filled batch.**~~ - **measured**, see [[Fragment Shader ISA]], "Performance model": a
   batch costs about 14x an extra ALU packet, so how full it is barely
   matters and the scheduler does *not* gain a second dimension.
3. **`pseq_to_dw_exec_nb`** (register `0x546`, bits 6..25) - **not compiler
derived**, which closes the question in the useful direction. Dumping every
   register write in the blob's fragment command stream (`cgc -F -R`) shows only
   `0x500` (PSEQ flush) and `0x806` (ALU complement) besides the six data
   uploads: the blob **never writes `0x546`**, nor `0xe20`/`0xe21`. So
   `pseq_to_dw_exec_nb`, `ALU_BUFFER_SIZE` and `TRAM_SETUP` are driver state, set
   per draw outside the shader binary, not values a compiler computes.

   Sweeping it on hardware (`1, 3, 8, 16`) produces bit-identical, correct output
   for a single-batch shader (`0xBF004000` every time), so it is a pipeline-depth
   tuning knob rather than an exact requirement - consistent with the assembler
   defaulting to 1 while `sky_fs.txt` uses 3. A scheduler does not need it to be
   *correct*; only a performance model would care.
4. **`alu_buffer_size` interaction**: how many rows may be live simultaneously
   and whether the row registers beyond the configured size alias or are
   undefined.
5. **`recv`/`send` exact behaviour across batches** (the table is documented, but
   the accumulate timing across a packet boundary was never measured).


## 5. Program layout, container and chunks (moved)

The upload sequence (which register gets which stream, the `0x820` constants, the
`0xe20`/`0xe21` words) is driver-level command-stream material and now lives in
[[Command Stream]], "Fragment program upload sequence". The container header and
the chunked/multi-pass behaviour are ISA facts and live in [[Fragment Shader ISA]],
"Program container, chunks and recirculation".

Three points a backend still has to act on:

* the stream counts must agree - PSEQ, MFU schedule, TEX, ALU schedule,
  complement and DW all have one word per batch, and `grate_shader_disasm_fs()`
  treats a mismatch as malformed;
* **ALU_BUFFER_SIZE and TRAM_SETUP are driver state**, not derived from the
  shader: the blob never writes `0xe20`/`0xe21` itself;
* when the constants do not fit, the blob re-emits the program as several
  **chunks** that are recirculated over the same fragment, with the constant
  window reloaded per chunk and only the last chunk storing. The compiler may
  instead reject over-budget shaders - that is a design decision, documented in
  the ISA section rather than here.

Arithmetic semantics and the performance model are measured hardware facts and
have likewise moved to [[Fragment Shader ISA]] ("Arithmetic semantics" and
"Performance model"): fp20 truncation, saturation instead of infinity, the
`PRE`/main pairing rules, SFU accuracy, the 8-bit output conversion, and the
per-batch cost model with the MFU as the scarce resource. The compiler-relevant
excerpts are in sections 3.1 and 4 below where they change a decision.
## 6. I/O: varyings and the linker program

**Known** ([[Shader Linking]]): each *linked* varying is two words at `0x300` -
source export index and TRAM row, plus per-component interpolation flags and
destination type (`fx10 low/high`, `fp20`, or skip). The fragment shader then
reaches them through MFU `ipl` slots, one slot per component (or per packed pair
of components for fx10).

**How many varyings fit?** At least nine `vec4` (36 components): a shader summing
`v0..v8` compiles with no error, gets **locations 7..15** and rows 0..8, so the
first varying is always row 0 and the row count simply follows the used varying
count. The MFU costs one slot per *used component*, so the real budget is the
TRAM row limit (`0xe21` bits 8..14) crossed with the per-program MFU word budget
(64 words over all batches) - and every varying the fragment shader reads must
have its own `ipl` slot in some batch.

**Row indices are dense and have nothing to do with GLSL locations.** With
`varying vec2 v_uv` at location 7 and `varying vec4 v_col` at location 8, the
blob's fragment program dumps `ipl: var1 v0, var1 v0, var1 v1, var1 v1` - i.e.
the MFU varying operand carries a 5-bit **row index** assigned in linker order
(row 0 for the first linked varying, row 1 for the second), and the GLSL
location only matters on the *vertex* side when the linker instruction is built.
A backend therefore cannot use `var.location` as the MFU operand; it must keep
the row map that the linker program was built from. Components that the shader
does not read are not fetched at all, which is why a 4-component varying can
show up in fewer than four `ipl` slots.

What a backend must therefore produce per varying:

1. a TRAM row assignment, and the `TRAM_SETUP` row count (`64 / rows`, so rows
   must divide 64);
2. interpolation flags: `dis` (constant across the primitive) for flat
   qualifiers, otherwise interpolated;
3. the matching `ipl` slot in the first MFU word that touches it, with the
   precision deciding whether one or two slots are consumed.

**Settled: rows 0..15 only, and that is not a limitation in practice.** The
MFU's `var` field carries the TRAM index in **4 bits**, so only rows 0..15 are
reachable from the fragment program whatever the linker writes, and the
assembler refuses to emit more: `linker_asm.y` errors with "Invalid TRAM row
index, 15 maximum" (that parse error is why the old `tram20` probe printed
nothing at all, not a hardware limit). The linker's row field is 6 bits and
`TRAM_SETUP` allows up to 127, so the encoding could express rows 16+, but no
fragment program can name them. A backend assigns 0..15 and sets
`used_tram_rows_nb` to `max_row + 1` (`grate_3d_set_used_tram_rows_nb` writes
rows and `64 / rows`, so the row count must divide 64).

**Multiple `LINK` lines and multiple rows both work.** Two lines - one writing
`x`, one writing `y` of the same row - render exactly the frame a single line
writing both does, and a second row does not disturb the first: reading `t1`
with row 1 linked gives the same frame as reading `t0` with row 0 linked.
Reading a TRAM value back through an ALU operand **does work** with a plain fp20
row-register read: `MAD r2.l*, r0, #1, #0, #1` returns the interpolated value
exactly (four different constant varyings all read back as themselves, see
[[Fragment Shader ISA]] 12.9). The `.l` (low fx10) form returns zero, which is
what made earlier probes look like leftover register content. `ipl` output is
ordinary fp20 data, so a backend may do arithmetic on varyings directly instead
of laundering them through the texture unit.

**Settled:** re-reading a varying in a **later batch** works - the same varying
interpolated in two different batches returns identical values - so `ipl` may be
repeated and the value need not be parked in a row register.

The chain is verified end to end at least for two components: `filter_quad`
renders its texture's two-tone split exactly (section 10.3), so the linked
varying reaches `ipl`, and `ipl` reaches the pixel packet.

## 7. Uniforms and constants

**Known.** 32 words at `0x820..0x83f`, uploaded per chunk. Each word is either
one fp20 uniform (`u0..u31` read as fp20) or two fx10 halves (`.l`, `.h`), and
the ALU selects 32..63 for them.

The window is **per chunk, not per program**: 32 fp20 / 64 fx10, re-uploaded for
each chunk of a split program ([[Fragment Shader ISA]], "Program container,
chunks and recirculation"), which is how the blob compiles shaders needing more
than 32 scalars - a 16-`vec4` shader comes out as two chunks of eight. Within a
chunk a `vec4` mediump uniform consumes 4 words (half the window), so a
single-chunk backend is limited to eight `vec4`, while a chunk-splitting one is
not. Slots are also relocatable and assigned by use rather than by declaration
(same ISA section), so the upload has to be built from the symbol table.

Consequences:

* `nir_lower_uniforms_to_ubo` style indirection is impossible; indices must be
  compile-time.
* The blob *also* uses embedded constants (3 per batch, ALU slot 3) for values it
  cannot afford to park in the uniform file, and `pack_alu_const()` in the blob
  packs them - the backend should do the same for literals.
* Immutable constants that appear in the shader can be baked into the uniform
  upload at draw time (`symbols[]` in `grate_program.c` already does this for the
  assembler path).

### 7.1 A schedule that exceeds 64 batches

The batch count is a register field, not a hint ([[GR3D Registers]] 6.1), and two
limits bound it: the assembler refuses more than 64 `EXEC` blocks in one program,
and a count of 128 already hangs AR20, so a backend cannot simply keep emitting
batches. Splitting is the answer, and it is the same mechanism as the uniform
pressure case above:

* cut at batch boundaries into chunks well below the limit, each with its own
  `0x520` count and its own constant window - the window is per chunk, so the
  split is what makes a 16-`vec4` uniform set legal at all;
* the container's second header word carries that chunk's batch count, so it is
  derived per chunk, not per program;
* a chunk has to be self-contained: a value computed in one chunk cannot be read
  by the next except through a render target or the spill buffer, which is what
  the blob's recirculation does ([[Fragment Shader ISA]] 10.2);
* never pad the count upwards to "be safe". An over-count runs the sequencer past
  the end of the schedule and, at 128 and above, wedges the channel.

## 8. Fragment outputs

**Known.** `DW` stores one 16-bit field per source register from `r2/r3` (or
`r0/r1`) to a render target: on `RGBA8888` the first register fills `R` and `G`
and the second `B` and `A` (`r2 = 0.5`, `r3 = 0.125` stores `DF00EF00`), and a
field's first byte is the converted channel. Targets are hardwired: 0 depth,
1 colour, 2 stencil, 3 coverage - an ordinary target with the colour value
format, measured equal to `rt1` at every probe point, and readable only by a
`PSEQ` fetch of that index - register 74 (`primitive coverage`) reads zero in
every configuration tested, so no coverage information reaches the shader - 4
spill, 5..12 the encoding for further colour targets - `gl_FragData[1]` is spelled
as index 5, but AR20 has no datapath for it (retraction below). Target 4 is the
blob's `SURF_REGSPILL`; its base is `RT_PTR(4)` = `0xe04`, with a companion limit
at `0xe34` and enable bit 4 of `0x903`, and a spill store is inert unless DW bits
10..16 select a slot, and bits 18..23 add `128`/`256`/`512`/`1024`/`4096` words to
the address (measured tables in [[Fragment Shader ISA]]). What a spill store
carries is `r0` scaled by 256 in 24.8 fixed point - not the colour pair - so the
spill buffer holds pixel-packet values, and a backend that never spills needs
only targets 0..3.

Missing for a complete backend:

* **format conversion** - the store is always four fx10; conversion to
  RGBA8888/RGB565/etc. is done by the render-target unit from `RT_PARAMS.FORMAT`.
  For the 8-bit path the conversion is settled (4.5): `round(x * 255)` with halves
  rounding down (`0.5` -> `0x7F`, `0.125` -> `0x20`). `RGB565` is
  `round(x * 31)` per 5-bit channel, `L8` takes the red channel and `A8` the
  alpha channel; the measured table is in [[Fragment Shader ISA]].

  **The payload must be fx10.** `DW` converts each field as fx10, so a value
  left in fp20 form stores a reading of its *bit pattern*: `MAD r2.lh, u0` with
  `u0 = 0.25` followed by `DW: store rt1, r2, r3` stores `0xE700E700`, while the
  same value written `.l*` stores `0x00400040`. The `.lh` byte is
  `8*exponent + top 3 mantissa bits` - `247 + 8*log2 v`, saturating above `2.0`
  - so it is monotone but not a colour value at all, and there is no format
  conversion that can rescue it. Materialise the store payload with `.l*` /
  `.*h` (or read it as fp20 and convert), never with `.lh`.
* **blending** - there is no blend instruction; blending is fixed-function and
  lives in the 3D state, so `nir_lower_blend` style needs are a state question,
  not a shader question.
* ~~MRT works, including several stores per program~~ - **retracted: index 5
  never receives a store on AR20.** The *encoding* is real: the blob compiles
  `gl_FragData[0]`/`gl_FragData[1]` to `DW` words `0x00028005` (`store rt1`)
  and `0x00028015` (`store rt5`), both class 2, so index 5 is the spelling for a
  second colour target (`SURF_OUT1`) - but the datapath is absent. A program that
  stores to index 1 in one batch and index 5 in a later one leaves the index-5
  surface all-zero (four words read back `00000000`) in six variants: as-is; with
  `RT_PARAMS(5)` set to a working colour word (`0x04040034`); with `RT_ENABLE` =
  `0x22`; with the limit register at `0xe35` as well; with only the enable bit;
  and with only index 1 enabled as the control. A complete dump of a real draw
  shows the index-5 slot live and parameterised exactly like the four that work -
  `RT_PARAMS(5)` = `0x00040034`, `RT_PTR(5)` set, `RT_ENABLE` = `0x0001002f` -
  and it still receives nothing ([[GR3D Registers]] 14.3). The `00400040` seen in
  an earlier run was stale buffer content, not a write. A backend therefore emits
  exactly one `DW` store per fragment, to index 1; AR20 is a GL ES 2.0 part, where
  a `gl_FragData` index above 0 does not exist.
* **alpha-to-coverage, dithering per fragment** - no encoding.

## 9. The gap list, ordered by what blocks a compiler

### 9.1 Blocks the backend from being written

1. **Scheduling/forwarding model** - closed (sections 4.3, 4.6).
   `pseq_to_dw_exec_nb` turned out to be a **no-op in the assembler**: values 1
   and 4 produce byte-identical 29-word fragments (the two streams diff clean)
   and identical results, so there is no encoding to discover - whatever the blob
   intends it for is not emitted.
2. **Register allocation over a 16-fp20 file with ALU-latch-side chaining** -
   the encoding is known, the *policy* is not. Nothing is known about whether
   `g0..g7` have any cost penalty, or whether row registers beyond
   `alu_buffer_size` alias. **Partly measured**: at `alu_buffer_size = 1`, all of
   `r4..r15` are distinct from `r0..r3` and from each other, so the file is not
   silently remapped onto four registers. But **nothing above 1 is usable**: 2
   and 4 hang the engine for *any* program - including one whose only batch is a
   single `DW: store` - and 3 runs some programs, hangs others and returns zero
   where size 1 returns the value. See [[GR3D Registers]] 14.2; a backend must
   assume four fp20 row registers (`r0..r3`) and nothing more.
3. ~~TEX operand plumbing~~ - **settled**: coordinates must be in `r0..r3`, and
   an ALU in the *previous* batch can write them, which is how
   `tests/grate/asm/tex_cross_batch.txt` feeds `s` and `t` from uniforms. The
   same batch cannot, because the ALU stage runs after TEX (section 10.3).
   **Cost of the copy, measured** on the 13-batch shape at 256x256, 100 draws:
   439.8 ms with no copy at all (TEX straight off the `ipl` coordinates),
   483.2 ms with the copy folded into a batch that already does other ALU work,
   and 502.5 ms with the copy alone in its own batch. Folding the copy in costs
   ~0.43 ms/draw, a separate batch for it another ~0.19 ms/draw - the per-batch
   cost of 4.6, so a backend should always fold the copy into a batch it already
   has.

### 9.2 Needed for correct code, unknown today

4. ~~`MIN`/`MAX`/`CMP` semantics~~ - partly settled, see [[Fragment Shader ISA]], "Arithmetic semantics". Finite `MIN`/`MAX`
   order correctly and `CMP` compares the **first product**, not `rA`. **Signed
   zero is not reachable**, so there is no tie-breaking rule to find: building
   `-0` as `0 * -1` produces something `rcp` maps to +infinity exactly like
   `+0` (`0xFF`, the same as `rcp(+0)`), and `MIN`/`MAX` of that pair return it
   under either operand order. Values above `2^33` clamp (see below), which is
   what "the saturated fp20 exponent" turned out to mean.
5. ~~Whether `saturate` clamps before or after the result scale~~ - **settled,
   and it is the unusual order: the scale is applied first.** `(sat)(/2)` on
   `1.5` gives `0.75` (scale -> 0.75, then saturate), not `0.5`. `(/2)` alone
   works (`0.5 -> 0.25`). **Saturate clamps fx10 writes too**: `1.25` written
   with `.l* (sat)` reads back through the `.l` source form as 1.0 (halved, it
   stores `0x7F`), while the same write without `sat` reads back 1.25 (halved,
   `0x9F`), and a 0.75 control stores `0xBF` either way.
6. ~~The exact `CMP` predicate~~ - **settled**: it is
   `(rA*rB < 0) ? rB : (rC*rD)`. The reference's `(rA < 0)` reading is wrong on
   hardware, and the true branch is `rB`, not the product. See 4.5.
7. ~~`RECIP`/`RSQ`/`LG2`/`EX2`/`SQRT`/`SIN`/`COS` accuracy, input ranges and the
   `PRE*` pair~~ - **settled**, details in [[Fragment Shader ISA]], "Arithmetic semantics". Every op is exact to the
   precision of its fp20 input; the output is full range (the clamp lives in the
   output stage); `PRE*` + main must be emitted as one pair with the same
   operand, and the `PRE` result cannot be stored or reused.

   **`sfu: OP rN` is established, and was long misread:** it does not
   write rN.** A survey of 14 operations (rcp, rsq, sqrt, ex2, sin, cos, frc,
   lg2, presin, precos, preex2) returned *the input, unchanged*, in every case -
   sqrt(0.25) read back 0.25, rcp(2.0) read back 2.0, cos(0) read back 0. So the
   `reg` field names the operand, and the result is only available as the `sfu`
   source of an MFU multiplier: `mul0: r2, sfu, #1`. The multiplier destination
   is restricted to `r0..r3` (the assembler rejects anything higher with
   "Invalid row register number, r3 max"), so the result lands in the pixel
   packet and is read from there.

   **Working recipe, verified on hardware.** Park the operand in `r0..r3` (the
   pixel packet - `r4..r15` are not readable by the unit: with the operand in
   `r4`, `rcp` returned the same 0.47 for inputs 2, 4 and 0.25, and the
   multipliers reject `r4` outright as a source), run `sfu: OP rN`, take the
   result with `mul0: r2, sfu, #1`, and convert with `.l*` for the store. That
   returns `rcp(2) = 0.5`, `rcp(4) = 0.25`, `sqrt(0.25) = 0.5` and
   `frc(1.75) = 0.75` exactly, and `r1` behaves the same as `r0` as the operand.
   `ex2(-1)` returned 1.0 instead of 0.5, which matches the reference calling
   EX2 / SIN / COS "second step" operations - they need PREEX2 / PRESIN / PRECOS
   first, so a single-op probe of those three is not meaningful.

   **Accuracy, measured.** With `K*(y - expected) + 0.5` and `K = 4096` (one
   fp20 ulp at 0.5 maps to 0.25 of output range), `rcp(2)`, `rcp(4)`, `rcp(3)`,
   `rsq(4)`, `sqrt(0.25)` and `frc(1.75)` all read back `0x7F` = 0.5 - an error
   below the probe's resolution (~1.2e-4 absolute, about one fp20 ulp). The SFU
   is therefore as accurate as the ALU's own format and needs no polynomial
   refinement at fp20 precision. Still unmeasured: EX2 / SIN / COS (they need
   their PRE stage, which a single-op probe does not provide) and edge inputs
   (zero, negative, very small).

   That also means the earlier "SFU costs 3.4 ns/word" timing work measured the
   cost of an SFU *word*, which is unaffected, but no accuracy claim in this
   document yet rests on a real measurement - accuracy and input ranges remain
   open, now with a working way to observe them.
8. ~~fp20 conversion rounding on ALU results~~ - **settled: the hardware
truncates toward zero, exactly like the assembler.** `x = 1 + eps`, then
`4096*(x-1)` with an embedded constant for the offset:

   | eps | result | reading |
   |:--|:--|:--|
   | 0 | `0x00` | 0 |
   | 1 ulp (`2^-13`) | `0x7F` = 0.5 | 1 ulp survives |
   | 1.5 ulp | `0x7F` = 0.5 | **truncated to 1 ulp** - rounding would give `0xFF` |
   | 2 ulp | `0xFF` = 1.0 | exact |
   | 0.25 (control) | `0xFF` | chain saturates as expected |

   There is no round-to-nearest anywhere in the ALU path, and the assembler's
`fp20` packing truncates identically, so a backend can emit truncated constants
and predict every intermediate exactly. Earlier failed attempts, kept for the
record: The probe that would settle it is designed
   and its mechanism is confirmed working - `4096*1.0 + (-4095.5)` returns
   exactly `0x7F`, so an amplified 1-ulp difference is visible at 8-bit output
   precision - but the tie value cannot be routed through a row register at
   `alu_buffer_size = 1`: a 3-batch chain (`x = 1+eps`, `x-1`, `(x-1)*4096`)
   returns 0 even for its own control (`eps = 0.25`, which should be `0xFF`),
   and the store sees the first batch's register as 0. So the blocker is the
   register round-trip across two batch boundaries, not the arithmetic. Note
   the subtract there used `-#1` and the multiply took the uniform in rB, though
   rC may equally be a uniform (4.3). Next step: chain through `aluN`
   lookalikes via `recv`/`send` instead of the register file, or retry at a
   larger `alu_buffer_size` (but 2 hangs - see 9.1).
   Also tried, also failing: chaining through the `aluN` latches inside one batch,
   and padding every batch to four packets (no change at all).

   The latch file is now mapped well enough to schedule against, in
   [[Fragment Shader ISA]] "Forwarding and the ALU pipeline": a **low-half**
   write by slot
   `k` is readable as `aluK` from any slot of the same packet (a high-half or
   fp20-only write leaves the latch at zero), all four latches are live inside
   their own packet (`alu2`/`alu3` included), and row-register writes are not
   visible within the packet. The one
   carried-over value is **`alu0` read from slot 0**, which holds the previous
   packet's slot-0 result. So chaining is `slot k writes a low half -> another
   slot reads aluK`, tail to a row register.
   Two side findings from the same probes: the **`(this)` modifier adds the
   previous slot's result and discards `rC`** (a free operand for a chain, but
   it cannot coexist with a real addend), and `(other)` has no measured
   arithmetic effect.

   The `-#1` oddity recorded here is **resolved - it was a byte-order
   misreading, not a modifier problem.** `MAD r3.*h, u0, #1, -#1, #1` with
   `u0 = 1.5` writes `0.5` into `r3.hi` (byte 3 = `0x7F`) and nothing into
   `r3.lo`, and with `u0 = 0.75` it computes `-0.25`, which an fx10 write clamps
   to zero. Both are correct. The note was written while this document still
   labelled the store bytes from the wrong end.

### 9.3 Needed for performance only

9. ~~Whether the 4 ALU slots of a packet may include a NOP cheaply~~ -
   **settled**: `count` barely matters, the batch itself is what costs
   (section "Performance model" of [[Fragment Shader ISA]]).
10. ~~Whether `MFU` and `ALU` really run in parallel for the same batch~~ -
    **settled**: MFU, ALU and TEX all overlap and a batch costs the maximum over
    them; every SFU op costs the same (~3.4 ns/fragment beyond the first in its
    batch); and the limit is per batch rather than per word (section "Performance model" of [[Fragment Shader ISA]]).
11. ~~Whether a TEX and a DW in the same batch can be independent~~ -
    **settled: yes.** A batch holding a `tex` into `r2/r3` plus a store of
    ALU-produced values returns exactly the ALU values (`0x7F407F40`), so a DW
    can run alongside a TEX as long as it does not consume the sample.

**Per-batch cost, re-measured with the chaining rules known.** Seven shapes
differing only in batch count and slots per batch, 100 draws each, minimum of
five runs at 256x256:

| shape | min ms | ms/draw |
|:--|--:|--:|
| 12 empty batches + store | 426.6 | 4.27 |
| 12 batches x 1 slot + store | 445.7 | 4.46 |
| 12 batches x 4 independent + store | 431.9 | 4.32 |
| 4 batches x 3 slots + store | 218.2 | 2.18 |
| 3 batches x 4 independent + store | 207.4 | 2.07 |
| 3 batches x 4 chained `(this)` + store | 190.3 | 1.90 |
| 6 batches x 2 slots + store | 283.7 | 2.84 |

Fitted over all seven: **92.9 ms fixed + 26.3 ms per exec batch**, i.e.
0.263 ms per batch per draw, or **about 4.0 ns per fragment per batch**. Two
consequences for a scheduler: filling a batch is nearly free (36 extra ALU slots
spread over 12 batches moved the time by -13.8 ms, inside the noise), so pack
work into batches rather than spreading packets across them.

`(this)` costs nothing either way. With 12 batches of four slots, the four
operand forms measured within 8 ms of each other (immediate `#0` 426.9, uniform
431.1, `#1` 434.7, `(this)` 431.2 ms) - about 0.05 ns/fragment/batch. An earlier
note here claimed a `(this)` chain was ~17 ms *faster*; repeating that pair with
seven samples instead of three gave **+13.1 ms at 3 batches and -18.0 ms at 12
batches**, i.e. opposite signs, so that reading was noise. Chain for register
pressure, not for speed.

**Packing validated end to end.** A shader with eight `MAD`s behind one texture
sample, emitted two ways: naive (coordinates, then one packet per batch, then
TEX+DW - 10 batches) and packed (coordinates, two 4-slot batches, then TEX+DW -
4 batches). Naive 372.3 ms, packed 196.5 ms per 100 draws at 256x256: a 47%
saving, against a model prediction of 355.9 -> 198.1 ms. Both emitted the
identical `0xFFFFFFFF`, so the packing changes nothing else. A backend should
emit the packed form - the batch count, not the slot count, is the cost.

### 9.4 Documentation debt that blocks nothing

12. ~~The undocumented unit 5 (four packed relative slot offsets)~~ - **it has
    no host upload register on AR20, so it is not a gap that can be closed.**
    The FP class exposes a register for exactly six streams - PSEQ
    (`0x540`/`0x541`), MFU schedule (`0x600`/`0x601`), MFU (`0x603`/`0x604`), TEX
    (`0x700`/`0x701`), ALU schedule (`0x800`/`0x801`), ALU (`0x803`/`0x804` plus
    the complement at `0x806`), DW (`0x900`/`0x901`) - plus `0xe22`, and there
    is **no register at all between `0x901` and `0xe22`** (the complete list is
    in `include/tgr_3d.xml.h`). The blob's own fragment dump (`cgc -F -R`)
    uploads those same six streams and nothing else. So the encoder's `sync` and
    `sync_pre` buffers are dead code on this chip - the shared encoder most
    likely serves a later chip that does have the register. Nothing to probe
    until such a chip turns up.
13. ~~DW bit 14~~ - **confirmed on hardware from a word the assembler itself
    emits**: it forces an all-zero payload (`0x007F007F` -> `0x00000000`)
    whichever register pair holds the payload, which is exactly the "always a
    zero payload" the compiler corpus showed. The target-class field is measured
    too: bits 16..17 = 2 is the colour target, 1 with index 0 is the depth
    surface, 3 writes nothing. The depth payload is now characterised as well
    (the ISA section "Arithmetic semantics"): one register only - the high
    register of the pair bit 15 does *not* name, so `r3` for `store rt0, r0, r1
    class 1` - with low half `value * 0x10` and high half `value * 0x4000`,
    superposed. A backend has to emit the class suffix; a plain `store rt0` is a
    colour store. **The "128-bit packer at `0x1c6648`" does not
    exist**: both `0x1c6648` and its overlapping neighbour `0x1c6650` decompile
    to an item-size/alignment accumulator (`(acc - 1) + align & -align` over a
    list of items with per-kind sizes), not to `orr`/`lsl` word building, so the
    packer still has to be located. Stencil writes are answered differently from
    what was assumed: the `DW` stencil store is dead on this chip, the ref +
    `REPLACE` stencil path works (see the ISA doc).

## 10. Reproducing the measurements

All of these produce facts that now live in [[Fragment Shader ISA]]; the recipes
are kept here so the numbers can be re-derived, and each cites the ISA section it
supports.

The harness is the Grate assembler ([[Hardware Testing]]), run out of the meson
devenv so `libnvrm.so` resolves:

```
cd ~/build/grate
meson devenv -C ~/build/grate tools/assembler \
  --vs tests/grate/asm/vs_mov.txt \
  --fs /tmp/expB.txt \
  --lnk tests/grate/asm/linker_vs_tests.txt \
  --fs_uniform '["u0"]=0.25' --result --testonly
```

`--result` prints the first framebuffer pixel; RT1 is cleared to `0x4C4C4C4C`,
so any non-clear value is the shader's output. The probe shaders used:

```
# expA: baseline - uniform -> r2.high/r3.high, store in the same batch
EXEC
	ALU:
		ALU0:	MAD  r2.*h,  u0.l,  #1, #0, #1
		ALU1:	MAD  r3.*h,  u0.l,  #1, #0, #1
	DW:	store rt1, r2, r3
;
```
```
# expB: cross-batch - batch 1 writes r3, batch 2 reads it
EXEC
	ALU:
		ALU0:	MAD  r3.*h,  u0.l,  #1, #0, #1
;
EXEC
	ALU:
		ALU0:	MAD  r2.*h,  r3.h,  #1, #0, #1
	DW:	store rt1, r2, r3
;
```
```
# expC: same batch, two ALU packets, second packet reads r3.h  -> STALE
EXEC
	ALU:
		ALU0:	MAD  r3.*h,  u0.l,  #1, #0, #1
	ALU:
		ALU0:	MAD  r2.*h,  r3.h,  #1, #0, #1
	DW:	store rt1, r2, r3
;
```
```
# expD2: same batch, two packets, second packet reads alu0.h  -> forwards
EXEC
	ALU:
		ALU0:	MAD  r3.*h,  u0.l,   #1, #0, #1
	ALU:
		ALU0:	MAD  r2.*h,  alu0.h, #1, #0, #1
	DW:	store rt1, r2, r3
;
```

Measured (`u0.l = 0.25` -> fx10 `0x40`; RT is RGBA8888 with `r2.l`=R,
`r2.h`=G, `r3.l`=B, `r3.h`=A):

| shader | result | reading |
|:--|:--|:--|
| calibration (4 distinct values) | `0xBF20407F` | channel order and 8-bit conversion pinned down |
| expA | `0x40004000` | baseline correct |
| expB | `0x40004000` | cross-batch forward works |
| expC | `0x40000000` | read forwarded (`G=0x40`) but `r3.h` writeback lost (`A=0`) |
| expD2 | `0x40004000` | `alu0` forward works across packets |
| expE2 (ALU0 -> ALU1 in one packet via `alu0.h`) | `0x40004000` | same-packet forward works |
| `fs_uniform.txt` control | `0x19334C66` | matches the suite's expected value, validating the model |

Note `aluN` cannot be a *destination* in the assembler grammar
(`ALU_DST_REG` accepts only `rN`, `gN`, `uN`, `lp`, CR and `kill`); the ALU slot
number implicitly names the latch, so the probe writes a row register and reads
the slot's latch by number.

### 10.2 Texture probes

`tools/assembler` had no texture support, which is why TEX was untested. It now
has a `--texture` option that binds a 4x4 `RGBA8888` linear texture to sampler 0
and clears it to a chosen colour; the probes above use it:

```
EXEC
	TEX:	tex r0, r1, tex0, r0, r1, r2
	ALU:
		ALU0:	MAD  r2.*h,  r0.l * 2,  #1,  #0,  #1
		ALU1:	MAD  r3.*h,  r0.l * 2,  #1,  #0,  #1
	DW:	store rt1, r2, r3
;
```

with `--texture --result`. Because the texture is flat, garbage coordinates are
harmless - they clamp to the edge - which is what makes the probe small.

Making it discriminating needs three things, all of which bit me once:

* `grate_texture_clear()` then `grate_texture_clear_rect()` for the two-tone
  variant - this does work; a failed coordinate probe is not the texture's fault;
* the coordinate must be written as **fp20** (`.lh` destination, or a plain
  register source), never as an fx10 half;
* use a texture at least 16 texels wide, or bind a mip chain; narrower ones are
  misaddressed by the sampler and read mostly black whatever the coordinates
  are, which looks exactly like a coordinate bug (section 10.3);
* put `TEX:` before `ALU:` in the `EXEC` block. The grammar only accepts pipeline
  order, and `ALU:` first is a parse error that exits 1 without printing
  `--result` - see [hardware-testing.md](hardware-testing.md).

The destination/coordinate overlap is not a problem: `filter_quad_fs.txt` uses
`txb r2, r3, tex0, r0, r1, r2, r3`, with `r2` as the third coordinate and half
the destination, and it samples correctly.

### 10.3 Why `filter_quad` rasterised nothing

The `filter_quad_*` assets were unusable for two harness reasons, neither of
which is about the ISA:

1. **The attribute guard was wrong.** The `if (location >= 0)` test for `color`
   had been inserted *before* `location` was reassigned, so it tested the
   *position* attribute's location. `filter_quad_vs.txt` declares no `color`
   attribute, so the draw still got
   `vertex_attrib_float_pointer(ctx, -1, ...)`. Each binding is now guarded after
   its own lookup.
2. **`mvp` was never set.** `filter_quad_vs.txt` computes `gl_Position` as
   `DP4 a[0], c[0..3]` and the harness supplies no vertex constants, so the
   exported `w` was 0: a degenerate triangle, which rasterises nothing and
   leaves the framebuffer at the clear colour. `tools/assembler` now takes
   `--vs_mat4 '["mvp"]=(16 floats)'`, which writes all 16 floats through
   `grate_3d_ctx_set_vertex_uniform` (that call already accepted `nb = 16`).

With both fixed the shipped trio works:

| probe | result | reading |
|:--|:--|:--|
| `filter_quad_vs` + `filter_quad_fs`, no `mvp` | `0x4C4C4C4C` | clear colour: nothing rasterised |
| same, `mvp` = identity | `0x00000000` | rasterised, TEX ran, result reached DW |
| `vs_mov` + `filter_quad_fs` (no uniform at all) | `0x00000000` | the fragment half needs no vs uniforms |

`asm_tests.sh` now carries the identity-`mvp` case as a regression: any value
differing from the `0x4C4C4C4C` clear proves the triangle reached the DW unit.

**The coordinates were never the problem.** An earlier round concluded otherwise
from a handful of framebuffer reads, but both reasons were artefacts of the
probe. The decisive measurement is the whole frame with a mip chain bound
(`--mipmap`, `lod_bias` 0): **32768 white and 32764 black pixels and nothing
else** - exactly the two-tone split of the bound texture. So the interpolated
`texcoord` does reach TEX and the sample does reach DW.

What made every probe read black was the texture setup:

| setup | whole frame |
|:--|:--|
| mip chain bound, `lod_bias` 0 | 32768 white / 32764 black - correct two-tone |
| mip chain bound, `lod_bias` 8 | 65532 x `0x00545454` - a small mip |
| **no** mip chain, `lod_bias` 0 | 57340 black / 8192 white - wrong |

A 4x4 texture with no mip chain bound samples through this harness as *mostly
black* (1/8 white instead of 1/2) whatever the coordinates are, so a black
result says nothing about the coordinate path. The cause is texture row
addressing, a driver bug rather than a probe artefact: `grate_3d_set_texture_desc()`
writes no pitch at all, so the texture unit derives the row stride itself and
always uses `width * bpp`. `grate_create_texture()` allocated a pitch aligned up
to 64 bytes, so for a 4-texel-wide texture the sampler read row `y` at offset
`y * 16` while the data sat at `y * 64`. Colouring rows separately shows it
directly (16-high texture, `s = 0.7`, pitch forced per run):

| texture | width*bpp | allocated pitch | rows read |
|:--|--:|--:|:--|
| 4x16 | 16 | 16 | `B G R W B G R W` - correct |
| 4x16 | 16 | 32 | `B . G . R . W .` - row `y` returns row `y/2` |
| 4x16 | 16 | 64 | row `y` returns row `y/4` |
| 8x16 | 32 | 32 | correct |
| 8x16 | 32 | 64 | row `y` returns row `y/2` |
| 16x16 | 64 | 64 | correct |
| 16x16 | 64 | 128 | row `y` returns row `y/2` |

Doubling the pitch halves the row index, so the stride really is `width * bpp`
and the padding is invisible to the sampler. Fixed by allocating a tight pitch
in `grate_create_texture()`; 4x4 through 16x16 then read every row correctly and
`filter_quad` renders the two-tone frame at 4x4 with no mip chain.

**Block-compressed formats follow the same law.** For DXT1/ETC1 the derived
stride is `width * bpp / texel_width` = one 8-byte block per four texels, i.e.
`width / 4` blocks per row, which is exactly what a tight allocation provides.
With `GRATE_TEX_FORMAT=dxt1` a 16x16 texture whose block rows are red, green,
blue and white samples it row for row:

| t | texel row | rgba8888 | dxt1 | etc1 |
|:--|--:|:--|:--|:--|
| 0.03125 | 0 | `0x000000FF` red | `0xFF0000FF` red | `0xFF0000FD` red |
| 0.09375 | 1 | `0x0000FF00` green | red (same block row) | red |
| 0.15625 | 2 | `0x00FF0000` blue | red | - |
| 0.21875 | 3 | `0xFFFFFFFF` white | red | - |
| 0.34375 | 5 | - | green | `0xFF02FF02` green |

DXT1 returns alpha `0xFF` (the block format has no alpha channel) and ETC1 is
off by one or two in a channel - both as expected from the codecs. A wider
pitch misaddresses it identically: at pitch 48 block row 3 lands on block row
2's data (blue), at pitch 64 it lands in the zero padding (black). One loose
end: ETC1 at pitch 64 returned white rather than the black DXT1 gets from the
same padding, so an all-zero block is not neutral: DXT1 decodes it to (0,0,0)
and ETC1 to (2,2,2) (`0xFF020202`).

With the sampler still striding 32 bytes, block row 3 sits at byte 96, so at
pitch `p` it reads whatever data block row `96/p` holds:

| pitch | 32 | 48 | 64 | 96 |
|:--|:--|:--|:--|:--|
| dxt1 | white (row 3) | blue (row 2) | black (padding) | green (row 1) |
| etc1 | white (row 3) | blue (row 2) | `0xFF020202` (padding) | green (row 1) |

Both formats follow that arithmetic exactly, which is the cleanest confirmation
that the row stride is `width * bpp / 4` and that any pitch in the descriptor is
ignored. An earlier run reported ETC1 as white at pitch 64; that was a harness
bug - `etc1_encode_image()` writes its rows contiguously, so the upload left the
block rows at a 32-byte stride while claiming the pixelbuffer's pitch. A bound mip
chain is laid out with `ALIGN(width * bpp, 16)` per level, which is tight for
the sizes that matter, so `--mipmap` hid the bug rather than working around
anything about mip *selection*.

For the record, cross-batch delivery is fine: `tex_cross_batch.txt` writes the
coordinates with the ALU in one batch and samples in the next, and the sampled
texel follows `s` and `t` exactly. What a shader cannot do is write coordinates
and sample them in the *same* batch - the `ALU:` stage runs after `TEX:`, which
the assembler grammar enforces.

Two other suspicions were ruled out by direct comparison. `tram0.xyzw`
(`filter_quad_linker.txt`) versus `tram0.yxzw` (`linker_vs_tests.txt`) only swaps
the two components. And the vertex shader's export form makes no difference at
all: full destination mask, masked source, or exporting from `r0` instead of
`r63` all produce bit-identical frames.

### 10.4 LOD bias is functional, and the computed LOD clamps at 0

`--mipmap` was added (generates the chain, `NEAREST_MIPMAP_NEAREST`, `max_lod` 11)
so `filter_quad_fs`'s `txb` could be driven. Its bias comes from `r3`, fed from
the `lod_bias` fragment uniform. Whole-frame counts, one row per bias:

| bias | black | white | other |
|-----:|------:|------:|------:|
| 0, 2, 4, 5, 6 | 32764 | 32768 | 0 |
| 7 | 0 | 0 | every pixel |
| 8, 12 | 0 | 0 | every pixel |

Rows 0-6 are the *base level*, pixel for pixel - 32764 black and 32768 white is
exactly the texture's two-tone split. The output changes for the first time at
bias 7, and from 8 on it is a single uniform colour.

That transition is where the arithmetic puts it. A 4x4 texture across a 256x256
quad is magnified 64x, so the derivative-based LOD is `log2(4/256) = -6`; adding
the bias and clamping at 0 moves the first level change to bias 7 and to nothing
earlier. Therefore:

* the bias operand is real - bit 12 plus the `r3` source, exactly as the TEX
  word table says;
* the computed LOD is *clamped* at 0 rather than wrapped: biases 0..6 give
  identical frames, not a progression;
* the bias is inert with no mip chain bound (bias 8 with mipmapping off still
  renders the two-tone frame - the "no mip chain" row in 10.3).

Not established: which level is actually reached, and what the small mips
contain. `grate_texture_generate_mipmap` does not produce a plain box average at
this size - bias 7 changes *every* pixel instead of preserving the two-tone
split, and the uniform value at bias 8 is `0x00545454`, not the `0x7F7F7F7F` a
50/50 average would give. Colour alone pins the transition, not the level.

**Explicit LOD is TEX bit 13, and it only works with bit 12 clear.**

`TEX:` accepts a raw word (`TEX: <hex>`), so
`tests/grate/asm/tex_explicit_lod.txt` starts from the word that
`txb r2, r3, tex0, r0, r1, r2, r3` assembles to (`0x1420`) and flips single
bits. The quad magnifies a 4x4 texture 64x, so the computed LOD is
`log2(4/256) = -6` and clamps to 0; the ALU writes `u0` into `r3`.

| word | bit 12 | bit 13 | lod = 0 | lod = 2 | lod = 5 |
|:--|:--:|:--:|:--|:--|:--|
| `0x0420` | - | - | two-tone | two-tone | two-tone |
| `0x1420` (`txb`) | bias | - | two-tone | two-tone | two-tone |
| `0x2420` | - | **LOD** | two-tone | `0x00545454` | `0x00545454` |
| `0x3420` | bias | LOD | two-tone | two-tone | two-tone |

"two-tone" is 8191 black / 8192 white, the level-0 frame; `0x00545454` is the
flat colour of the 1x1 mip.

So bit 13 makes `r3` an **absolute** LOD, bits 12 and 13 are mutually
exclusive, and **bit 12 wins when both are set**. The `txb` row is also the
cleanest confirmation of the clamp: `-6 + 5` is still negative, which is why
bias 5 looks identical to bias 0 here, and why the shipped `filter_quad` probe
needs bias 7 before anything changes.

`lod = 2` and `lod = 5` give the same colour because a 4x4 chain has only
levels 0, 1 and 2 - level 2 is already 1x1, so everything deeper reads it.

For selection: `textureLod` becomes `TEX` with bit 13 and the LOD in `r3`,
`texture` with a bias becomes `TXB`, and `textureGrad` remains impossible
(section 2.1). `asm_tests.sh` now carries the bit-13 case as a regression
(`0x00545454`).
