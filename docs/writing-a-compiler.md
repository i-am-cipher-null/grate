# Writing a fragment-shader compiler for AR20

This document is a gap analysis, not an ISA reference. The ISA encoding itself
lives in [[Fragment Shader ISA]]; the blob compiler's internals live in
[[Compiler Internals]]. The question answered here is: **if you sit down to
write a NIR -> AR20 backend for Mesa, which facts do you need, which of them do
we have, and which are still missing?**

It is written against the driver in `src/gallium/drivers/grate/` (Mesa), where
`grate_program.c` currently converts NIR to TGSI and `fp/pack.c` can already
pack ALU / MFU / DW / schedule words. Everything below is marked as one of:

* **known** - read off the encoding, the blob, or measured on hardware;
* **inferred** - consistent with the evidence but not isolated by an experiment;
* **open** - genuinely unknown, with the experiment that would settle it.

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
  unrolled or rejected.
* **`discard` is not a branch either** - it is an ALU write of a non-zero value
  to register 76. It also has a driver-side effect: the host must switch the
  stencil test to late (`0x40f = 0x60` instead of `0x48`, see
  `grate_3d_set_late_test()`) and set the `discards_fragment` flag.
* **Derivatives, and therefore `textureGrad`/`textureLod` with computed
  gradients, are out of reach** unless computed per-quad by hand: there is no
  quad/neighbour data path. Texture LOD is chosen by the TEX unit itself
  (`TEX` uses the unit's own LOD selection, `TXB` takes an explicit bias from
  `r3`).

The blob behaves the same way: the ISA reference notes that the multi-pass
program-sequencer recirculation is "not used in practice, BLOB driver gives up on
large shaders".

### 2.2 Instruction budget

**Known** (hardware - the schedule word's address field is 6 bits, the count
field 2 bits):

```
max 64 exec batches per program
per batch:  <= 1 PSEQ, <= 3 MFU, <= 1 TEX, <= 3 ALU packets, <= 1 DW
max 64 MFU words, 64 ALU packets (256 ALU slots), 64 TEX, 64 DW, 64 PSEQ
```

`fp/pack.c` already asserts `num_instructions < 4` and `address < 64`, and
`grate_program.c` asserts `num_fp_instrs < 64`.

This is the single hardest constraint on a real shader compiler: a nontrivial
fragment shader is ~20-40 ALU slots per source statement, so **256 ALU slots is
the whole program**. Any NIR that survives legalisation must be counted, and the
backend needs a defined failure mode (Mesa can fall back to `llvmpipe`, or the
shader can be rejected). Unrolling loops makes this worse, not better.

### 2.3 Register file

**Known**, from `asm.h` and the blob:

| Range | Contents | Notes |
|:--|:--|:--|
| 0..15 | row registers `r0..r15` | 4 rows x 4 components: `r(4*row + comp)`. Live only up to `alu_buffer_size` rows |
| 16..23 | global registers `g0..g7` | survive across batches and across the whole program |
| 24..27 | ALU result registers `alu0..alu3` | the ALU pipeline latches, see 4.3 |
| 28..30 | embedded constants `imm0..imm2` | one set per batch, occupies ALU slot 3 |
| 31 | lowp `vec2(0.0, 1.0)` | `.l` = 0.0, `.h` = 1.0 |
| 32..63 | uniforms `u0..u31` | fp20 each, or 64 fx10 halves |
| 64..71 | condition registers, in pairs | 8 CRs, each holding two fx10 `0.0`/`1.0` |
| 72 | `posx` | `8192 + frag.x` |
| 73 | `posy` | `8192 + (height - 1) - frag.y` |
| 74 | coverage | documented but not selectable by the assembler |
| 75 | `pface` | polygon face |
| 76 | kill | write non-zero to discard |

Practical register budget for the allocator: **16 fp20 (32 fx10) row registers,
8 globals, 4 ALU latches, 3 immediates, 32 uniforms.** That is smaller than
typical NIR temp counts by an order of magnitude, so register allocation has to
be the first thing designed, not the last.

Two structural constraints that shape it:

* row registers are *only* addressable as `r0..r3` by TEX and DW, and as
  `r0..r3` by the MFU multiplier. So texture coordinates and store payloads must
  end up in the current row, which forces copies (`r3.lh, r2, #1, #0` style
  moves) around every TEX and DW.
* `alu_buffer_size` (1..4) decides how many rows are in flight. The blob sets it
  from its scheduler; the assembler refuses anything but `ALU0..ALU3` in a
  packet, so 4 rows is the ceiling for the *register* file but the assembler
  cannot populate 8 halves in one packet (see [[Compiler Internals]]).

### 2.4 Precision, and why it is the front-end's problem

**Known / inferred:**

* `fp20`: sign, 6-bit exponent (bias 31), 13 stored significand bits. `0x3f`
  exponent is inf/NaN (`float_to_fp20()` maps `0xff` to `0x3f`). Range roughly
  `2^-31 .. 2^32`, 14-bit effective significand - i.e. *between* fp16 and fp32,
  but with no denormals.
* `fx10`: signed 10-bit fixed point, scale 1/256, range `[-4.0, 3.996]`,
  resolution `1/256`. The assembler converts by truncating `f * 256`, which
  independently confirms truncation-toward-zero at least in the tool; the
  hardware's own rounding on `ALU`/`TEX` results is **open** (see 9.3).
* Arithmetic is *always* one of these two; there is no fp32 anywhere.

So a NIR backend needs `nir_lower_fp64`, `nir_lower_int64`, `nir_lower_int`
(all integer ops are illegal), and a precision pass that assigns every
intermediate to fp20 or fx10. `mediump` maps naturally onto fp20 arithmetic and
`lowp` onto fx10 (two components per register), which is also why the MFU packs
a `lowp vec2` into one interpolation slot and a `mediump vec2` into two
([[Fragment Shader ISA]], "MFU interpolation slots depend on precision").

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
| `tex`/`txb` | `TEX`/`TXB` | coords must be in `r0..r3` |
| `store_output` | `DW` | payload must be in `r2/r3` or `r0/r1` |
| `load_input` (varying) | `MFU ipl` | one `var` slot per component; precision decides slot count |
| `load_frag_coord` | `posx`/`posy` + the `r3` z expansion | z needs the documented `1/1000 + max(0,r3.low)/4000 + max(0,r3.high)/4` sequence, i.e. ~6 ALU slots |

The `PRE`/main pairs are a selection *pattern* obligation: `PREEX2` without
`EX2` in the next MFU word is meaningless, so selection and scheduling have to
agree. The same is true of the barycentric prologue: `RCP r4` + two
`mul`s + the `bar` destination must be the first MFU word of the program, and
`w` arrives in `r4` for that first word only.

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

### 4.1 Batch structure

**Known.** A batch is one row of the schedule streams. One word per stream per
batch, except MFU (a batch may consume 1..3 MFU words) and ALU (1..3 packets).
The schedule word is

```
bits 0..1  count (number of words to execute, 0 = NOP but still 1 cycle)
bits 2..7  address (index of the first word to execute)
```

The assembler computes `address = num_pushed_so_far - count + 1`, i.e. the
address is "the last `count` words pushed", which is why three MFU slots in the
same batch get consecutive addresses.

### 4.2 Per-unit constraints

* ALU packet: 4 slots, each slot is one instruction. Slot 3 can instead carry
  three embedded constants, in which case that packet has only 3 ALU ops.
* `recv`/`send` (bits 60/61) implement a cross-packet accumulator chain; the
  table in [[Fragment Shader ISA]] is the contract. `recv` overrides `rC` with
  the accumulated value.
* MFU consecutive records with equal `sched` share one 64-bit word.

### 4.3 Forwarding: measured, and it is unusual

This is the part a scheduler must get right, and it was previously unknown. All
measurements use the Grate assembler harness (section 10).

The probe writes fx10 `0.25` (= `0x40`) through `u0.l`. The readback word is
little-endian, so the channels decode as
`A << 24 | B << 16 | G << 8 | R` with `R = r2.low`, `G = r2.high`,
`B = r3.low`, `A = r3.high` - the documented order. That was confirmed with a
four-value calibration (`r2.h = 0.25`, `r2.l = 0.5`, `r3.h = 0.75`,
`r3.l = 0.125` -> `0xBF20407F`) before any of the results below were read.

| Producer -> consumer | Distance | Result | Meaning |
|:--|:--|:--|:--|
| ALU (packet 1) -> DW (same batch) | 0 | `0x40004000` | works |
| ALU0 -> ALU1, same packet (`alu0` as source) | 0 | `0x40004000` | works |
| ALU (packet 1) -> ALU (packet 2, same batch), reading **`r3.h`** | 1 packet | `0x40000000` | read is fresh, but the **earlier write is lost** |
| ALU (packet 1) -> ALU (packet 2, same batch), reading **`alu0.h`** | 1 packet | `0x40004000` | works |
| ALU (batch N) -> ALU (batch N+1), reading **`r3.h`** | 1 batch | `0x40004000` | works |
| TEX -> ALU, same batch | 0 | `0x7F007F00` | works - the sample is already visible |
| TEX -> ALU, next batch | 1 batch | `0x7F007F00` | works |
| TEX -> DW, same batch | 0 | `0x40404040` | works |
| ALU -> TEX coordinates, fp20 in `r0` | 1 batch | `0xFFFFFFFF` / `0x00000000` | works - right half vs left half of a two-tone texture |
| ALU -> TEX coordinates, fx10 half only | 1 batch | `0x00000000` either way | **not used** - see below |

So:

> **Intra-batch, the row/global register file is not a safe data path; the
> `alu0..alu3` result registers are. Between batches, register writes do commit.**

The `0x40000000` case is worth stating precisely, because a first reading of it
is misleading. Packet 1 writes `r3.h = 0.25`; packet 2 reads `r3.h` and writes
`r2.h`. The framebuffer then shows `G = 0x40` (so packet 2's read **did** see the
new value - the read is forwarded) and `A = 0x00` (so `r3.h` is *zero* by the
time the DW stage reads it). In other words the reader gets the value through a
bypass, and the pending register-file write of that same register is suppressed.
The read is not stale; the **writeback is dropped**.

That reproduces identically on two consecutive runs, so it is deterministic, not
noise. Whether it is exactly "bypass cancels the writeback" or something subtler
in the ALU writeback arbitration is unresolved, but the compiler rule is the
same either way: **never read, in one packet, a row/global register written by an
earlier packet in the same batch** - chain through `aluN` instead, and let the
chain tail write the register.

`aluN` cannot be a *destination* in the assembler grammar; the ALU slot number
implicitly names the latch, so a probe writes a row register and reads the
slot's latch by number. That is also how a backend must model it: the latch is a
property of the slot, not of an operand.

**Texture results are the exception: they are visible to the ALU in the batch
that issued the fetch.** A probe that samples a 4x4 grey texture
(`0x40404040`, i.e. 0.251) and doubles one component in the same batch returns
`0x7F` per channel (0.5), and the same probe with the ALU moved to the next
batch returns the identical word. So a scheduler does **not** have to pad
distance after a TEX, which removes the largest suspected source of stall
padding from the design.

Two further facts about the batch, both structural rather than measured:

* the stage order inside one batch is fixed - PSEQ, MFU, TEX, ALU, DW - and the
  assembler grammar enforces it (writing `TEX:` after `ALU:` is a syntax error).
  Consequently **ALU -> TEX within one batch is impossible**, and computed
  texture coordinates always cost a batch boundary;
* TEX and DW address the pixel-packet row registers only (`r0..r3`), so a
  sampled value that a later stage needs must be live in the current row.

**Texture coordinates are read as `fp20`, and ALU-written coordinates do reach
the unit.** With a two-tone 4x4 texture (left half black, right half white),
writing `s = 0.875` into `r0` as fp20 in one batch and sampling in the next
returns `0xFFFFFFFF`; writing `0.125` returns `0x00000000`. The same writes done
as an fx10 half (`r0.l*` or `r0.*h`) sample the same texel regardless of the
value, which is what the ISA reference says: the coordinate registers are
consumed as fp20.

This retracts an earlier conclusion in this document. A first probe reported
that coordinates "never took effect", but it was writing the coordinate as an
fx10 half *and* using `r2` as both the third coordinate and the TEX destination;
both mistakes make the coordinate read as 0. So the coordinate path works, and
since ALU cannot precede TEX within a batch (stage order), the compiler must
assume **one batch of latency between computing a coordinate and using it** - a
scheduling constraint, not a hazard.

This is consistent with the ALU being a 4-stage pipeline where each packet is
one cycle and `alu0..alu3` are the actual pipeline latches: within a batch,
packet *k+1* is issued before packet *k* retires, so only the latches carry
data forward. Across a batch boundary enough cycles pass for the register file
write to land.

**Practical rule for the backend:** the intra-batch dependency graph must be
built over `aluN`, not over virtual registers. That is a strong constraint on
register allocation - a value with a same-batch consumer must be bound to an
ALU latch, and only one consumer per latch per packet boundary. In practice this
means scheduling ALU into packets as *chains* (`alu0 -> alu1 -> alu2 -> alu3`)
with the chain tail written back to a row/global register.

Other forwarding facts, from existing working tests rather than new probes:

* MFU interpolation -> ALU, same batch: works (`fs_vs_tests.txt`).
* MFU interpolation (batch 1) -> ALU (batch 2): works (`sky_fs.txt`).
* TEX -> DW, same batch: works (`filter_quad_fs.txt`) - TEX in batch 2 and the
  store of its result in batch 2 produce the correct filtered quad.
* ALU -> DW, same batch: works (`sky_fs.txt`).

### 4.4 What is still missing for a scheduler

**Open:**

1. ~~**TEX result -> ALU distance.**~~ - **closed**, see 4.3: the sample is
   visible to the ALU in the same batch and in the next one, and ALU-computed
   fp20 coordinates reach the unit one batch later. The only remaining TEX
   question is `LOD` selection itself (see 9.2), not scheduling.
2. **Cycle cost of a partially filled batch.** The schedule word count field is
   2 bits and `count = 0` still takes one cycle, so a batch with fewer than 3
   ALU packets probably still costs the same. Unverified. If it does *not*, the
   scheduler gains a second dimension (fill vs latency).
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

## 5. Program layout (what the backend must emit)

**Known**, from `grate-asm.c` and `include/tgr_3d.xml.h`; this is the definitive
map and was not previously written down anywhere in `docs/`.

| Host1x push | Register | Contents |
|:--|:--|:--|
| `IMM 0x500 0` | `FP_PSEQ_UPLOAD_INST_BUFFER_FLUSH` | flush before upload (assembler emits this, Mesa does not) |
| `IMM 0x520 <0x20006000 \| n>` | `FP_PSEQ_ENGINE_INST` | start PSEQ, `n` = batch count |
| `IMM 0x540 0` | `FP_PSEQ_UPLOAD_INST_ID` | upload pointer reset |
| `NONINCR 0x541 n` | `FP_PSEQ_UPLOAD_INST` | PSEQ words, one per batch |
| `IMM 0x545 0` | `FP_PSEQ_QUAD_ID` | |
| `INCR 0x546 1` | `FP_PSEQ_DW_CFG` | `pseq_to_dw_exec_nb` at bit 6 |
| `IMM 0x600 0` | `FP_UPLOAD_MFU_SCHED_ID` | |
| `NONINCR 0x601 n` | `FP_UPLOAD_MFU_SCHED` | MFU schedule, one per batch |
| `IMM 0x603 0` | `FP_UPLOAD_MFU_INST_ID` | |
| `NONINCR 0x604 2*m` | `FP_UPLOAD_MFU_INST` | MFU words, **high dword first** |
| `IMM 0x700 0` | `FP_UPLOAD_TEX_INST_ID` | |
| `NONINCR 0x701 n` | `FP_UPLOAD_TEX_INST` | TEX words, one per batch |
| `IMM 0x800 0` | `FP_UPLOAD_ALU_SCHED_ID` | |
| `NONINCR 0x801 n` | `FP_UPLOAD_ALU_SCHED` | ALU schedule, one per batch |
| `IMM 0x803 0` | `FP_UPLOAD_ALU_INST_ID` | |
| `NONINCR 0x804 8*p` | `FP_UPLOAD_ALU_INST` | ALU packets, 4 insns x 2 dwords, **high first** |
| `NONINCR 0x806 n` | `FP_UPLOAD_ALU_INST_COMPLEMENT` | the `ALU_COMPLEMENT` word per batch (zero in practice) |
| `INCR 0x820 32` | `FP_CONST(0..31)` | fragment uniforms, 32 words |
| `IMM 0x900 0` / `NONINCR 0x901 n` | `FP_UPLOAD_DW_INST` | DW words, one per batch |
| `INCR 0x300 2*k` | linker program | 2 words per varying ([[Shader Linking]]) |
| `IMM 0xe20 <(alu_buffer_size-1) \| ...>` | `ALU_BUFFER_SIZE` | bits 0..1 = size-1, bits 2..9 = `(300-1)/(size*4)` |
| `IMM 0xe21 <rows \| 64/rows<<8>` | `TRAM_SETUP` | used TRAM rows |

The stream **counts must all agree**: PSEQ, MFU schedule, TEX, ALU schedule,
complement and DW all have exactly one word per batch, so `n` is the batch count
everywhere. `grate_shader_disasm_fs()` treats a mismatch as "malformed", which
is a good sanity check to copy into the driver.

`ALU_BUFFER_SIZE` also needs a second magic write (`0x501`), and T114 needs the
`0x547` pair; both are already in `grate_3d_set_alu_buffer_size()`.

### 4.5 Arithmetic semantics, measured

Same harness, same calibration as 4.3. Two probes per question, with the
operands swapped, so that "returns source 0" and "returns the special value"
can be told apart.

**`CMP` (assembler mnemonic `CSEL`) is `(rA*rB < 0) ? rB : (rC*rD)`.**

| rA | rB | rC | rD | result | reading |
|:--|:--|:--|:--|:--|:--|
| -0.25 | -0.25 | 0.25 | 1 | `0x40` (0.25) | product is +, so the false branch ran - `rA < 0` would have been true |
| 0.25 | -0.25 | 0.25 | 1 | `0x00` (-0.25) | product is -, true branch ran |
| -0.25 | 0.5 | 0.25 | 1 | `0x7F` (0.5) | true branch yields **`rB`**, not `rA*rB` (which would be -0.125 -> 0) |

So the reference's `(rA < 0) ? rB : (rC*rD)` is wrong in the *condition*, and
right in the *result*: the sign tested is the one of `rA*rB`, which is what makes
`CMP` symmetric with `MIN`/`MAX` (`min(rA*rB, rC*rD)`).

**Finite `MIN`/`MAX` order correctly.** `MIN(0.75, 0.25) = 0.25`,
`MAX(0.75, 0.25) = 0.75`.

**`fp20` has no infinity and no NaN. The maximum exponent (`0x3F`) is the
finite value `2^32`, and arithmetic on it is ordinary arithmetic.**

That was measured with a chain rather than a single operation, because one
multiply cannot tell "finite" from "saturating": start at the value, multiply
by `0.25` repeatedly across batches, and see whether it lands on an exact power
of two.

| start | steps x 0.25 | predicted if finite | measured |
|:--|:--|:--|:--|
| `0x7C000` (exp 62 = `2^31`) | 16 | `2^31 * 2^-32 = 0.5` | `0x7F` = 0.5 |
| `0x7E000` (exp 63) | 17 | `2^32 * 2^-34 = 0.25` | `0x40` = 0.25 |

An infinity or a NaN would not survive seventeen multiplies and arrive exactly
on `0.25`; a saturating format would have stuck at its maximum. Control chains
from `0.25` (1, 2, 3 steps -> `0x10001000`, `0x04000400`, `0x01000100`) confirm
the chain mechanism itself.

`MIN`/`MAX` therefore order correctly at the top of the format:
`MIN(2^32, 0.25) = 0.25` and `MAX(2^32, 0.25) = 2^32`, identical in both
argument orders.

**Beware the constants.** Getting this wrong is easy and produces convincing
nonsense - the first attempt at this experiment used `0x3E000` as "0.25" and
concluded, wrongly, that `MIN`/`MAX` were broken. `fp20` is
`(1 + mantissa/8192) * 2^(exp-31)`, so:

| value | fp20 |
|:--|:--|
| 0.125 | `0x38000` |
| 0.25 | `0x3A000` |
| 0.5 | `0x3C000` |
| 1.0 | `0x3E000` |
| 2.0 | `0x40000` |
| `2^31` | `0x7C000` |
| `2^32` (max exponent) | `0x7E000` |

**Overflow saturates; it does not wrap and it does not clamp at `2^32`.**
Products that exceed the range behave like a value at the top of the format:

| chain | exact would give | measured |
|:--|:--|:--|
| `2^31 * 4 = 2^33`, then 17 x 0.25 (`/2^34`) | 0.5 | `0x7F` = 0.5 |
| `2^32`, then 16 x 0.25 (`/2^32`) | 1.0 | `0xFF` = 1.0 (control) |
| `2^31 * 2^31 = 2^62`, then 31 x 0.25 (`/2^62`) | 1.0 | `0x00` |

A clamp at `2^32` would have given `0.25` in the first row; a wrap to zero would
have given `0x00` there. The third row rules out exact continuation. The only
model consistent with all three is saturation at the largest finite value, which
is `2^32 * (2 - 2^-12)`, i.e. just below `2^33` - that is why the first row is
numerically indistinguishable from exact arithmetic at 8-bit output precision.
For a compiler this is the friendly behaviour: no NaN, no wrap, so `min`/`max`
clamps behave as written.

Still open: whether `-0.0` and `+0.0` compare equal, which the 8-bit output path
cannot observe, and the exact saturation value to more than 8 bits of precision.

**8-bit output conversion is `round(x * 255)` with halves rounding down.**
Calibration values `0.5 -> 0x7F` (127, not 128) and `0.125 -> 0x20` (32, not 31)
rules out both `trunc(x*256)` and plain round-half-up. Relevant to a backend only
because it means a shader cannot rely on `1.0` landing on `0xFF` via `x*256`.

## 6. I/O: varyings and the linker program

**Known** ([[Shader Linking]]): each *linked* varying is two words at `0x300` -
source export index and TRAM row, plus per-component interpolation flags and
destination type (`fx10 low/high`, `fp20`, or skip). The fragment shader then
reaches them through MFU `ipl` slots, one slot per component (or per packed pair
of components for fx10).

What a backend must therefore produce per varying:

1. a TRAM row assignment, and the `TRAM_SETUP` row count (`64 / rows`, so rows
   must divide 64);
2. interpolation flags: `dis` (constant across the primitive) for flat
   qualifiers, otherwise interpolated;
3. the matching `ipl` slot in the first MFU word that touches it, with the
   precision deciding whether one or two slots are consumed.

**Open:** how many TRAM rows exist in total (16 are addressable by the MFU, the
linker's row field is 6 bits, `TRAM_SETUP` allows up to 127 rows), and what
happens when a varying is read in two different batches (whether `ipl` may be
repeated, or whether the value must be kept in a row register).

## 7. Uniforms and constants

**Known.** 32 words at `0x820..0x83f`, uploaded per draw. Each word is either
one fp20 uniform (`u0..u31` read as fp20) or two fx10 halves (`.l`, `.h`), and
the ALU selects 32..63 for them. The depth of the file is fixed - 32 fp20 / 64
fx10 - so the backend must pack uniform vectors into those, and a
`vec4` mediump uniform consumes 4 words (half the file).

Consequences:

* `nir_lower_uniforms_to_ubo` style indirection is impossible; indices must be
  compile-time.
* The blob *also* uses embedded constants (3 per batch, ALU slot 3) for values it
  cannot afford to park in the uniform file, and `pack_alu_const()` in the blob
  packs them - the backend should do the same for literals.
* Immutable constants that appear in the shader can be baked into the uniform
  upload at draw time (`symbols[]` in `grate_program.c` already does this for the
  assembler path).

## 8. Fragment outputs

**Known.** `DW` stores four fx10 values from `r2/r3` (or `r0/r1`) to a render
target. Targets are hardwired: 0 depth, 1 colour, 2 stencil, 3 coverage,
4 spill, 5..12 MRT `n+1`. So `gl_FragData[1]` is render target 5.

Missing for a complete backend:

* **format conversion** - the store is always four fx10; conversion to
  RGBA8888/RGB565/etc. is done by the render-target unit from `RT_PARAMS.FORMAT`.
  The rounding/clamping applied at that point is **open** (an 8-bit surface sees
  `0x40` for `0.25`, i.e. truncation or round-to-nearest both fit the data so
  far).
* **blending** - there is no blend instruction; blending is fixed-function and
  lives in the 3D state, so `nir_lower_blend` style needs are a state question,
  not a shader question.
* **MRT limits**: `max_dual_source_render_targets = 0` in the driver, and 12
  targets are addressable, but nothing has verified more than one MRT store per
  batch.
* **alpha-to-coverage, dithering per fragment** - no encoding.

## 9. The gap list, ordered by what blocks a compiler

### 9.1 Blocks the backend from being written

1. **Scheduling/forwarding model** - partly closed (section 4.3). Still needed:
   TEX->ALU latency, cycle cost of a partially filled batch, and
   `pseq_to_dw_exec_nb` semantics (4.4).
2. **Register allocation over a 16-fp20 file with ALU-latch-side chaining** -
   the encoding is known, the *policy* is not. Nothing is known about whether
   `g0..g7` have any cost penalty, or whether row registers beyond
   `alu_buffer_size` alias.
3. **TEX operand plumbing** - coordinates must be in `r0..r3`; what happens if a
   shader needs a texture fetch whose coordinates currently live in a global
   register (copy cost, and whether the copy can be in the same batch)? Not
   measured.

### 9.2 Needed for correct code, unknown today

4. ~~`MIN`/`MAX`/`CMP` semantics~~ - partly settled, see 4.5. Finite `MIN`/`MAX`
   order correctly and `CMP` compares the **first product**, not `rA`. Still
   open: signed zero, and values with the saturated `fp20` exponent.
5. Whether `saturate` on the destination clamps before or after the result
   scale, and whether it works on fx10 halves.
6. ~~The exact `CMP` predicate~~ - **settled**: it is
   `(rA*rB < 0) ? rB : (rC*rD)`. The reference's `(rA < 0)` reading is wrong on
   hardware, and the true branch is `rB`, not the product. See 4.5.
7. `RECIP`/`RSQ`/`LG2`/`EX2`/`SQRT`/`SIN`/`COS` accuracy and their input ranges;
   `PRE*` + main is a two-op sequence, so what the `PRE` result means (and
   whether it may be stored and reused) matters for selection.
8. fp20 conversion rounding on ALU results: the assembler truncates; the
   hardware is **not** verified to.

### 9.3 Needed for performance only

9. Whether the 4 ALU slots of a packet may include a NOP cheaply, i.e. whether
   `count` in the schedule word even matters for ALU (section 4.4 item 2).
10. Whether `MFU` and `ALU` really run in parallel for the same batch (the
    pipeline diagram says yes, stage 2 and stage 4).
11. Whether a TEX and a DW in the same batch can target different pixels safely
    when the DW payload comes from the ALU rather than from the TEX.

### 9.4 Documentation debt that blocks nothing

12. The undocumented unit 5 (four packed relative slot offsets, see
    [[Compiler Internals]]); the compiler emits words for it, and nothing in the
    driver or the assembler produces them. Its absence has not broken anything,
    so it is presumably a sync/timing stream.
13. DW bit 14 (always a zero payload when set) and the packer at `0x1c6648`.

## 10. Reproducing the measurements in 4.3

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
* do not use the TEX destination register as a coordinate. `r2` is both the third
  coordinate and half of the sample destination, and folding the two together
  makes the coordinate read as 0.

Also note `tools/assembler` now guards its attribute bindings: a shader with no
`color` attribute used to get `grate_3d_ctx_vertex_attrib_float_pointer(ctx, -1,
...)`, which corrupts the draw. With `filter_quad_*.txt` the harness still
rasterises nothing (the framebuffer stays at the clear colour) even with the
`texcoord` attribute bound, so the `filter_quad` route is not usable yet - the
standalone probes above are.
