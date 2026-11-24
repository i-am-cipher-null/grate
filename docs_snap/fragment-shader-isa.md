# Tegra 2 (AR20) - fragment shader ISA

**Status.** Working reference. This document is the encoding and behaviour of the
fragment-shader pipeline as established on Tegra 2 (AR20) hardware. Statements
are one of: *known* (read off an encoding), *measured* (verified by executing on
the GPU with the Grate assembly harness), or *inferred* (consistent with the
proprietary compiler but not isolated).

**Scope.** The five fragment-shader instruction streams (PSEQ, MFU, TEX, ALU,
DW), the register file and data formats, the execution model, the program
container, and the measured arithmetic and performance behaviour. The
vertex-shader ISA, the command stream and the vertex/fragment link program are
separate documents.

**Conventions.** Bits are numbered from 0 (least significant). Fields are written
`hi..lo` and are inclusive. A 64-bit instruction word is stored in memory and in
the command stream as two dwords with the **high** dword first; a 32-bit word is
one dword. Values in a register file may be read as one `fp20` value or as two
`fx10` halves (section 3). "Packet" means one 4-slot ALU group, "batch" one row of
the schedule streams (section 4.3), "chunk" one complete program in the container
(section 10).

**Related documents.** [[Command Stream]], [[Vertex Shader ISA]],
[[Shader Linking]], [[Writing a Compiler]], [[Compiler Internals]].

**Relationship to the concise specification.** `docs/fragment-shader-isa.md` is the
concise, canonical ISA description maintained alongside the implementation. This file
is the extended AR20 research reference: it retains detailed experiment tables, probe
interpretations, and the history of claims later narrowed or corrected. It is not a
frozen historical snapshot and is not intended to be a line-for-line copy of the concise
document. Keep every overlapping current claim consistent between the two; additional
evidence and unresolved details may live here. Measured statements cite the experiment
notes (`tests/grate/asm_characterize.experiments.md`) by section.

## Contents

- 1. Overview
- 2. Register file
- 3. Data formats
- 4. Execution model
- 5. ALU
- 6. MFU
- 7. TEX
- 8. DW
- 9. PSEQ
- 10. Program container
- 11. Performance
- 12. Verification against the proprietary compiler
- Appendix A. Patents

---

## 1. Overview

The fragment ISA is split across five independent instruction streams, which
makes it more involved than the single-stream [[Vertex Shader ISA]]:

* PSEQ - fetching data from the memory and feeding it to the pipeline as registers data or instructions
* MFU - multi-function unit, varying interpolate and special functions
* TEX - texture lookups
* ALU - arithmetic logic unit
* DW - writing to the output surface / buffers

Each unit is paced by its own timing stream; the scheduling consequences are in
section 4.

### 1.1 Instruction flow

| Sequence: |   1  |  2  |  2  |  2  |  3  |  4  |  4  |  4  |  5 |
|----------:|:----:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:--:|
|  Stage 1: | PSEQ |     |     |     |     |     |     |     |    |
|  Stage 2: |      | MFU | MFU | MFU |     |     |     |     |    |
|  Stage 3: |      |     |     |     | TEX |     |     |     |    |
|  Stage 4: |      |     |     |     |     | ALU | ALU | ALU |    |
|  Stage 5: |      |     |     |     |     |     |     |     | DW |

## 2. Register file



**Known**, from `asm.h` and the blob:

| Range | Contents | Notes |
|:--|:--|:--|
| 0..15 | row registers `r0..r15` | 4 rows x 4 components: `r(4*row + comp)`. Live only up to `alu_buffer_size` rows |
| 16..23 | global registers `g0..g7` | survive across batches (measured, see below) |
| 24..27 | ALU result registers `alu0..alu3` | the ALU pipeline latches, see "Forwarding and the ALU pipeline" |
| 28..30 | embedded constants `imm0..imm2` | one set per batch, occupies ALU slot 3 |
| 31 | lowp `vec2(0.0, 1.0)` | `.l` = 0.0, `.h` = 1.0 |
| 32..63 | uniforms `u0..u31` | fp20 each, or 64 fx10 halves |
| 64..71 | condition registers, in pairs | 8 CRs, each holding two fx10 `0.0`/`1.0` |
| 72 | `posx` | `8192 + frag.x` |
| 73 | `posy` | `8192 + (height - 1) - frag.y` |
| 74 | `cov` | primitive coverage; fx10 low half only - the fp20 read returns 0 (section 5.8) |
| 75 | `pface` | polygon face |
| 76 | kill | write non-zero to discard |

Practical register budget for the allocator: **16 fp20 (32 fx10) row registers,
8 globals, 4 ALU latches, 3 immediates, 32 uniforms.** That is smaller than
typical NIR temp counts by an order of magnitude, so register allocation has to
be the first thing designed, not the last.

**A `mediump vec4` uniform occupies four consecutive registers** - a shader that
sums eight of them reads `c0..c31` and nothing above - and the operand field's
constant range is exactly that window (`c0..c31` = register numbers 32..63, with
64..71 already taken by the condition registers), fed by a 32-word `0x820` push.

**But 32 scalars is not the shader-visible limit: the blob accepts 16 `vec4`
uniforms.** A shader summing `m0..m15` compiles with `0 lines, 0 errors`, emits
7 batches and 60 ALU words, sets no embedded constants at all, and its header
reads `unknown0: 1`, `unknown1: 3`. That is 64 fp20 scalars in total, and the
mechanism that makes it legal is the **chunk split of section 10.2**: this
program is two chunks, each uploaded with its own constant window, so no single
chunk needs more than 32 live scalars. Inside each chunk the addresses stay
within `c0..c31` (the first chunk already reads `c0`, `c2`, `c4`, `c6`, `c8`,
`c12`, `c16`, `c20` and `c22`). A naive "8 vec4 and reject" rule would refuse
shaders the blob compiles, so for a chunk-splitting backend the split, not the
window, is the limit that matters.

**Uniform slots are assigned densely by use, not by declaration.** Two shaders
that differ only in which uniforms they touch show it:

| shader | uniform symbols (from `cgc -F --uniforms`) | ALU reads |
|:--|:--|:--|
| `m0 + m8` | `m0.xyzw @ 127,0`, `m8.xyzw @ 127,1` | `c0..c3` and `c4..c7` |
| `m8` alone | `m8.xyzw @ 127,1` | **`c0..c3`** |

The same uniform moves from `c4..c7` to `c0..c3` depending on whether `m0` is used
before it, so the constant slot is a compiler-chosen property of the *use*, and
the symbol table's `location` field is where the driver finds it. Components are
pruned as well - a 16-uniform shader reports `m5.xz` and `m6.x` for uniforms whose
other components are dead - so the slot pressure is live-value pressure, not
declaration size.

**The blob does not diagnose uniform pressure, and the window is per chunk.**
Both the 8-`vec4` and the 16-`vec4` shaders emit programs whose constant
addresses stay inside `c0..c31`, and neither produced an error; the extra
uniforms are accommodated by the chunk split of section 10.2, which gives every
chunk its own window. `cgc -F -R` shows **no `0x820` write at all** - the blob
never uploads constants itself, so the driver owns the window and must upload it
once per chunk. Consequences for a backend: one that emits a single chunk can
reject above 8 `vec4` / 32 live scalar slots, while one that mirrors the split
accepts what the blob accepts; in both cases the `asm.h` array sizes are the
hand-written assembler's convenience limit, not a hardware one. The slot map is
relocatable, so the uniform upload has to be built from the symbol table rather
than from declaration order.

Two structural constraints that shape it:

* row registers are *only* addressable as `r0..r3` by TEX and DW, and as
  `r0..r3` by the MFU multiplier. So texture coordinates and store payloads must
  end up in the current row, which forces copies (`r3.lh, r2, #1, #0` style
  moves) around every TEX and DW.
* `alu_buffer_size` (1..4) decides how many rows are in flight, one row per
  *batch*: at size N batch `i` executes on row `i mod N` and each row holds its
  own `r0..r3`. The blob sets the size from its scheduler, and the assembler
  cannot fill more than one row group per packet (`ALU0..ALU3`). Running at size
  N also requires the `0x520` count to be `batches * N` - without that the engine
  hangs - so a wider buffer is legal but not free ([[GR3D Registers]] 14.2). AR20
  has run size 2 correctly with the count multiplied; sizes 3 and 4 run but have
  not returned a correct value yet. `r4..r15` are the rows the ALU can address
  directly; TEX, DW and the MFU multiplier read only the current row's
  `r0..r3`, which is why every store and every texture fetch goes through them.


## 3. Data formats


Registers / embedded constants can either be treated as one FP20 register, or two FX10 values. Their encoding is like so:

### 3.1 FP20

The FP20 format is similar to [IEEE 754 FP32](https://en.wikipedia.org/wiki/Single-precision_floating-point_format) and [IEEE 754 FP16](https://en.wikipedia.org/wiki/Half-precision_floating-point_format), but with both range and precision somewhere in the middle of the two.

|   Bits | Meaning     |
|-------:|:------------|
|     19 | Sign        |
| 13..18 | Exponent    |
|  0..12 | Significand |

This means that there's a 14 bit significand, with 13 bits explicitly stored. A 6 bit exponent gives an exponent bias of 31, with a minimum exponent value of -31, and a maximum exponent value of 32.

### 3.2 FX10

The FX10 format is similar to [most signed fixed-point formats](https://en.wikipedia.org/wiki/Fixed-point_arithmetic), using 10 bits of storage, and a scaling-factor of 1/256.

The representable range is therefore [-4.0, 3.996] in steps of 1/256.

### 3.3 Range and precision limits


The encodings of the two formats are in sections 3.1 and 3.2; this
section is about what the hardware does with them.


**Known / inferred:**

* `fp20`: sign, 6-bit exponent (bias 31), 13 stored significand bits. `0x3f`
  exponent is inf/NaN (`float_to_fp20()` maps `0xff` to `0x3f`). Range roughly
  `2^-31 .. 2^32`, 14-bit effective significand - i.e. *between* fp16 and fp32,
  but with no denormals.
* `fx10`: signed 10-bit fixed point, scale 1/256, range `[-4.0, 3.996]`,
  resolution `1/256`. The assembler converts by truncating `f * 256`, and the
  hardware does the same: ALU results truncate toward zero at fp20 writeback
  (1.5 ulp reads back as 1 ulp, section 5.16), so tool and hardware agree and
  every intermediate value is predictable from the emitted constants.
* Arithmetic is *always* one of these two; there is no fp32 anywhere.

So a NIR backend needs `nir_lower_fp64`, `nir_lower_int64`, `nir_lower_int`
(all integer ops are illegal), and a precision pass that assigns every
intermediate to fp20 or fx10. `mediump` maps naturally onto fp20 arithmetic and
`lowp` onto fx10 (two components per register), which is also why the MFU packs
a `lowp vec2` into one interpolation slot and a `mediump vec2` into two
([[Fragment Shader ISA]], "MFU interpolation slots depend on precision").


## 4. Execution model
### 4.1 Program sequencer


PSEQ controls recirculation of a fragment through the fragment-shader pipeline. The
blob emits sufficiently large shaders as multiple complete program chunks and the
driver uploads those chunks in separate passes; the measured example and its chunk
table are documented in section 10.2. Row registers, globals and pixel state survive
between the chunks in the observed program. The mechanism by which the same fragment
is parked and re-entered between uploads is not established. The earlier reading that
the feature was unused and that the blob simply gave up on large shaders was incorrect.

Patent: https://www.google.com/patents/US8411096<br>
Patent: https://www.google.com/patents/US8659601

### 4.2 Instruction scheduling


Patent: https://www.google.com/patents/US8856499

Instructions schedule specifies the number of MFU instructions or ALU instruction packets (with an ALU packet encoding up to 4 scalar instructions, see below) executed by the respective stage, each from 0 to 3 per fragment pipeline instructions batch, with 0 being a NOP (see below).

| Bits | Meaning                           |
|-----:|:----------------------------------|
| 2..7 | Address                           |
| 0..1 | Number of instructions to execute |

If the "number of instructions to execute" is 0, then the pipeline stage is NOP, however still takes 1 clock cycle. The "address" is the number of pushed instructions before the instruction to execute, so unit[Address] ... unit[Address + Number of instructions to execute] instructions (or instruction packets) will be executed, where unit stands for MFU or ALU.

### 4.3 Batches and per-unit limits



**Known.** A batch is one row of the schedule streams. One word per stream per
batch, except MFU (a batch may consume 1..3 MFU words) and ALU (1..3 packets).
The schedule word is

```
bits 0..1  count (number of words to execute, 0 = NOP but still 1 cycle)
bits 2..7  address (index of the first word to execute)
```

**For ALU the counted word is a packet, not an instruction** (notes 32). A batch of
four instructions emits `count = 1` and `address = 0`, the next batch `1` and `1`, and a
batch with three `ALU:` blocks emits `count = 3` with `0x804` uploading 24 instruction
words - twelve instruction slots in one batch, all of which the hardware executes. A
fourth block is refused by the assembler (`Over 3 ALU instructions per exec batch`),
which is the 2-bit count field's limit.

The assembler computes `address = num_pushed_so_far - count + 1`, i.e. the
address is "the last `count` words pushed", which is why three MFU slots in the
same batch get consecutive addresses.

**Bits 8..31 of the 0x801 word are not reachable from a program (notes 30).**
The assembler emits them as zero on AR20; for T114 it writes `0xe0` into bits
29..31 (`to_114_alu_sched`), which is the later chips' wide layout. Setting bit
29, bit 30 or both on AR20 through a test hook changes neither forwarding, the
ALU result, nor whether a DW stores, in four shapes that would each notice a
different failure. Masking **one batch at a time** - the producer, the consumer
and a batch that takes no part - gives the same result across all three batch
positions and five shapes (notes 34, 35), so the inertness is not an average over
batches. The hops are read back from the run's own dump, which shows the mask on
exactly one 0x801 word and the 0x806 complement stream untouched. What those bits
mean on a T114 is untested here.

### 4.3.1 Per-unit constraints

* ALU packet: 4 slots, each slot is one instruction. Slot 3 can instead carry
  three embedded constants, in which case that packet has only 3 ALU ops.
* `recv`/`send` (bits 60/61) implement an accumulator chain **inside one packet**;
  the table in [[Fragment Shader ISA]] is the encoding, and `recv` overrides
  `rC` with the accumulated value. Despite the names, the handoff does not cross
  a packet boundary: measured in a two-packet batch, a send in packet 0 slot 3
  into a recv in packet 1 slot 0 transfers nothing (notes 37).
* MFU consecutive records with equal `sched` share one 64-bit word.


### 4.4 Instruction budget



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


## 5. ALU


The ALU instructions comes in packets of 3 or 4 scalar instructions (the fourth instruction can be traded for embedded constants). Each ALU instruction package seems to run pipelined, and each instruction in a package can use partial results from the previous instruction.

|   Bits | Meaning                  |
|-------:|:-------------------------|
| 62..63 | opcode                   |
|     61 | accumulate other (no measured arithmetic effect) |
|     60 | accumulate this (add the previous slot's result) |
|     59 | addition disable         |
| 57..58 | scale result             |
|     56 | saturate result, clamp to 0..1 (note 48) |
| 54..55 | condition code           |
| 47..53 | destination register     |
|     46 | write high subregister   |
|     45 | write low subregister    |
| 32..44 | operand rA               |
| 19..31 | operand rB               |
|  6..18 | operand rC               |
|   0..5 | operand rD               |

**Bit 56 clamps the result to 0.0 .. 1.0 (measured, note 48).** The clamp is
applied after the result scale and before the condition code, for `MAD`, `MUL`
and `MAX`, in both the fp20 (`.lh`) and fx10 (`.l*`) write formats: 2.0 stores
as 1.0, -0.5 as 0.0, and an in-range value is unchanged. `MIN` shares the path
by inference only; the accumulate and condition-register write paths were not
combined with the bit. A backend can spell a clamp with `(sat)` instead of a
`MIN`/`MAX` pair, but GLES2 has no saturate operation and the bit is optional.
The ordering rows are in section 48.

**Bits 60 and 61 are the accumulate pair (measured).** `(this)` (bit 60) makes
the instruction add the *previous slot's* result and ignore its own `rC`
addend; clear, the instruction honours `rC`. `(other)` (bit 61) produced no
arithmetic difference in any shape tested and is the "send" bit of the
encoder's register tracking. Measurement table in "Forwarding and the ALU
pipeline".

**The destination is 7 bits, 47..53, and bit 53 is its top bit (measured).**
Grate's `libcgc/shader.c` reads only bits 47..51 and treats bit 53 as "a condition
code is present"; that is a display artefact of its 5-bit read, not the encoding.
The blob's own conditional word settles it - high dword `0x00e000c2` decodes to
destination **64** and condition **3**, so the instruction writes condition
register 0, and `libcgc` prints it as `mad_lt r0.__` only because it drops bit
53. Nothing needs to be set for a condition to apply; see "Condition codes".
The four control-flow shaders checked later write their conditions into row
registers (`mad_le`/`mad_lt` with a `rN` destination) and never into a `crN`, so
this particular word is rare rather than typical in blob output.

### 5.1 Instruction packet

Patent: https://www.google.com/patents/US7710427

### 5.2 Embedded constants

Patent: https://www.google.com/patents/US8775777

Unlike a regular ALU instruction, the ALU3 instruction words, constituting immediate constants, shouldn't be swapped. Constant is either one fp20 or two fx10.

|   Bits | Meaning              |
|-------:|:---------------------|
| 44..63 | Immediate constant 2 |
| 24..43 | Immediate constant 1 |
|  4..23 | Immediate constant 0 |

### 5.3 Opcodes

| opcode | Mnemonic | Meaning            | pseudo-code                |
|-------:|:--------:|:-------------------|:---------------------------|
|      0 |    MAD   | Multiply-Add       | rA * rB + rC * rD          |
|      1 |    MIN   | Minimum            | min(rA * rB, rC * rD)      |
|      2 |    MAX   | Maximum            | max(rA * rB, rC * rD)      |
|      3 |    CMP   | Compare and select | (rA < 0) ? rB : (rC * rD)  |

The `MIN` and `MAX` rows were measured with `rD` fixed to `#1`, the only form
the assembler emits; both products multiply and the second term is `rC` in
that form. See note 49 in
`tests/grate/asm_characterize.experiments.md`. `rD` is a one-bit constant (`rD_reg_select:1`), not a register; `CMP` and `rD != 1` are not
measured.

### 5.4 Result scaling

| Value |  Meaning |
|------:|:---------|
|   0   | No scale |
|   1   |    x2    |
|   2   |    x4    |
|   3   |    / 2   |

### 5.5 Condition codes

The conditional operation is applied to the ALU's result, so the final result is either 0.0 (false) or 1.0 (true).

| Value | Meaning (this document)  | Meaning (`libcgc/shader.c`) |
|------:|:-------------------------|:----------------------------|
|     0 | no comparison            | `_z`  - result is 0.0       |
|     1 | zero                     | `_nz` - result is not 0.0   |
|     2 | greater than zero        | `_le` - result is <= 0.0    |
|     3 | greater or equal to zero | `_lt` - result is < 0.0     |

`libcgc/shader.c` names the codes `_z _nz _le _lt` for 0..3 and prints them
between the mnemonic and the destination (`mad_le r12.hl, c0, #1, c1.l`). Those
are names only: the measured semantics below are authoritative, and the labels
do not describe them (code 3 measures as `>= 0`, which `libcgc` would call `_lt`).

Measured, on a row-register destination, so with bit 53 clear throughout:

| code | mnemonic | operand result | stored word | meaning |
|--:|:--|:--|--:|:--|
| 0 | none | 1.0 | `0x000000FF` | result unchanged |
| 1 | `eq` | 1.0 | `0x00000000` | 1.0 -> false |
| 1 | `eq` | 0.0 | `0x000000FF` | 0.0 -> true |
| 2 | `gt` | 1.0 | `0x000000FF` | positive -> true |
| 2 | `gt` | 0.0 | `0x00000000` | zero -> false |
| 3 | `ge` | 0.0 | `0x000000FF` | zero -> true |

So the condition tests the ALU result and produces 0.0 or 1.0, and **no enable
bit is involved**: there is no bit anywhere in the word that has to accompany a
non-zero condition for it to take effect. Bit 53 belongs to the destination
field, which is why the blob's conditional packets (writing `cr0..cr7` or the
kill register) appear to "set a condition flag" in `libcgc`'s 5-bit decoder.

A second consequence for code generators: the blob's compare is expressed by
negating an operand and testing `>= 0` (`mad_lt r0.__, -r3, #1, ec0.l` in its
raw output), not by a dedicated compare instruction.

### 5.6 Condition registers

The condition register comprises two fx10 values, 0.0 or 1.0. In order to write a value to the condition register, the destination register should be selected to the condition register 64..71. To write to the higher part of the condition register, "write high subregister" bit needs to be set; otherwise lower part will be written regardless of the "write low subregister" bit state.

    Condition register stored value = !!(ALU result)

Measured readback, the independence of all 16 entries and visibility across
packets are in 5.15.

Up to two condition-register sources per `ALU[0-3]` sub-instruction have been
observed.

### 5.7 Kill register

Non-zero value written to the KILL register discards the fragment, zero keeps it alive and doesn't resurrect the killed fragment. Destination register write mask (low/high halves) is ignored.

Both halves of that are measured. `MAD kill, u0, #1, #0, #1 (eq)` with `u0 = 0`
discards (the target keeps its clear value `0x4C4C4C4C` while the same packet's
`r2`/`r3` stores are dropped), and `MAD kill, #1, #1, #0, #1` with no condition
also discards - any non-zero result kills, the condition only decides when. The
predicate does **not** need its own packet: a same-packet condition is enough,
which means a kill costs one ALU slot and nothing else.

A condition *on the instruction itself* is the reliable form. A condition
register written earlier in the **same batch** is not visible to a later
instruction in that batch - it reads as zero. Measured with a per-pixel band
(`cr0 = posx/256 - 32 > 0`, `kill = cr0`, stencil store at ref `0x7F`):

| arrangement | row 0 (`x = 0..3`) | mid (`x = 128,129`) |
|:--|:--|:--|
| `cr0` written in batch 1, `kill = cr0` in batch 2 | `7F 00 00 00` | `00 00` |
| `cr0` written and read in the same batch | `7F 7F 7F 7F` | `7F 7F` |
| condition on the kill write itself, no CR | `7F 00 00 00` | `00 00` |

So a compare has to live in an earlier batch than its use, or be folded into the
consuming instruction. The same-packet forwarding that exists for `aluK` does not
extend to condition registers.

The blob does not use the `kill` register for `discard` at all: its discard
shader writes the coverage, stencil and depth targets as well as colour (this
section's four-store sequence), with no `kill` instruction in the dump.

A kill is only half of the mechanism. The blob's own discard path is four
batches, and it writes the surfaces the rasterizer would otherwise have written:

```
batch 1  mad_le r12.hl, c0, #1, c1.l   ; the condition, then r14/r15 setup
         DW: store rt3, r0, r1         ; coverage - carries the kill decision
batch 2  PSEQ: load rt2 r0, r1         ; fetch stencil
         DW: store rt2, r0, r1         ; write stencil back
batch 3  DW: store rt0, r0, r1         ; depth
batch 4  DW: store rt1, r2, r3         ; colour
```

`PSEQ: load rt2 r0, r1` is the framebuffer-fetch form (`0x00820008`-style word,
render target index 2 in bits 16..19) used here to read the current stencil value
before rewriting it. The fetch works - it lands the surface value, normalised, in
`r0/r1` (ref `0xC8` reads back as `0xC7` through an `RGBA8888` store, ref `0x7F`
as `0x7F`) - and the `DW: store stencil` beside it commits the late update, so the
pair is a working read-modify-write (section 8.2).

What the late test actually changes on AR20, measured with a `kill` write in the
program: **depth is still written by the rasterizer** (`0x8000` across the frame,
killed fragments included, whether the gate holds `0x48` or `0x60`), while the
**stencil update moves into the fragment program** - the fixed-function `REPLACE`
stops as soon as the program writes the kill register (a constant-zero `kill` is
enough), and `DW: store stencil` is what performs it. The blob's batch 2 is
therefore not redundant: it is the late stencil update, and a discard-capable
program with a stencil buffer has to emit it.

### 5.8 Position registers

The returned fragment position X register value is accumulated by 8192.0, fragment position Y value is negative and accumulated by 8192.0 plus the render target height - 1.

    pos.x = 8192 + frag.x
    pos.y = 8192 + (target_height - 1) - frag.y

Measured: both are fx10 pairs whose value sits in the **high** half - `r72.l`
and `r73.l` read `0x00`, `r72.h` and `r73.h` read `0xFF` - and the fp20 read of
`posx`/`posy` also returns `0xFF`. `0xFF` is saturation, not a reading: the value
is around 8192, far outside the 8-bit output range. The formula is therefore
consistent with the hardware but the conversion cannot confirm the offset; a
probe has to subtract 8192 in the ALU first.

**Polygon face is register 75, and the blob consumes it as an ALU operand.**
`libcgc`'s disassembler prints `vFace` for register 75, so `vFace` and the
assembler's `pface` name the same register - the blob reads it directly rather
than through a varying:

```
mad_nz r0.__, vFace, #1, #0     ; no write mask: sets a condition code
gmad r3.h_, -r64.l, #1, #0      ; the next batch reads the flag from cr0
```

so a backend reads the value in the first batch, materialises the boolean with
the `mad_nz` form ("multiply, set condition on non-zero") and consumes the
condition code afterwards instead of multiplying the value itself.

**Register 75 is readable only through the fx10 low half.** It carries the
screen-space winding bit:

| access | clockwise primitive | counter-clockwise primitive |
|:--|:--|:--|
| `r75.l` (`pface.l`) | `0x00` | `0xFF` |
| `r75.h` | `0x00` | `0x00` |
| `r75` / `pface` (fp20 read) | `0x00` | `0x00` |

Measured with a single-instruction batch (`MAD r2.l*, r75.l, #1, #0, #1`,
`DW: store rt1, r2, r3`), the two orientations produced by drawing the same
quad through `mvp = diag(-1,1,1,1)`, which reverses the winding while covering
the same pixels. The saturated form returns the same `0xFF`, so the delivered
value is positive: **+1.0 for one winding and 0.0 for the other**, not -1.0.

**`FRONT_CW` (`0x343` bit 15) does not change it.** Both windings return the
same values with the bit clear and set, so the register reports the raw winding
and not "is this fragment front-facing". Selecting which winding counts as front
is therefore the driver's and the compiler's job: compare this bit against the
front-direction state, it is not pre-applied. `TGR3D_SU_PARAM` and
`CULL_FACE_LINKER_SETUP` are the same register (`0x343`), so this is the register
the blob's Mesa path programs as well.

**The earlier "reads as zero in every configuration" result was an access-form
artifact.** The fp20 read of register 75 returns zero whatever the geometry, and
the probes used exactly that: the bare `pface` mnemonic and `r75` with no
suffix. Coverage behaved the same way - `cov` and `r74` read `0`, while
`r74.l` returns `0x33` - so registers 74 and 75 are fx10-only specials, unlike
72 and 73, whose fp20 read works and saturates. The assembler now emits the low
half for a bare access to those two registers and rejects `.h`, so the fp20 form
is no longer reachable from source. Two further harness bugs hid the effect: the
driver's vertex program (`vs_mov`) copies attributes to the exports and
**ignores `mvp`**, so a "mirrored" run drew the identical quad, and the
assembler's own quad is clockwise in screen space, so before the geometry could
be mirrored only one winding had ever been sampled.

**Coverage is a constant on AR20.** `cov.l` / `r74.l` reads `0x33` - exactly 51
in the fx10 low half, since `(x2)` returns `0x66` and `(/2)` returns `0x1A`
(25.5, rounded) - for every fragment in every shape tried:

| variation | coverage byte |
|:--|:--|
| full-frame quad, both windings | `0x33` |
| quad translated by 0.25, 0.5 and 0.75 px | `0x33`; at 0.5 px and above the boundary column drops to the clear colour instead of being shaded partially |
| quad scaled to a quarter of the frame | `0x33` |
| quad rotated 20, 30 and 45 degrees | `0x33`, with 8088 / 10124 / 11246 pixels left uncovered |

AR20 therefore does not expose a per-fragment coverage mask through register 74:
a primitive edge that crosses a pixel centre either shades the fragment with
`0x33` or does not shade it at all. The value behaves like a fixed constant the
shader unit supplies, so nothing that wants a real coverage mask - alpha to
coverage, per-sample effects - has a source for it. The earlier "coverage reads
0" was the fp20 access form, not the hardware.

### 5.9 Operands rA, rB and rC

|  Bits | Meaning               |
|------:|:----------------------|
| 12..6 | register selector     |
|     5 | sub-register selector |
|     4 | fixed10 minus one     |
|     3 | fixed10               |
|     2 | absolute value        |
|     1 | negate                |
|     0 | scale by two          |

Two measured restrictions that the bit layout does not show:

* a bare fp20 read of a row register takes the **high** half of the pair. The low
  half is only addressable as fx10 (`rN.l` / `rN.h`, read as 1.10 fixed point).
  Writing a destination as `.l*` alone therefore produces a value a later fp20
  operand cannot see; the working test shaders all write `.lh`, which writes both
  halves (`.l*` = low only, `.*h` = high only).
* `-#1` in rC is a working minus-one addend: `MAD r2.l*, u0, #1, -#1, #1` with
  `u0 = 1.5` returns `0.5`. In rA/rB the same spelling is a *multiplier*
  (`1.5 * -1`), which an early probe misread as a broken modifier.
* there is **no rC port restriction**: a uniform in rC is honoured beside a
  row-register rA or rB, and a row register in rC is honoured beside a uniform
  rA. `MAD r2.l*, r1, #1, u1, #1` with `r1 = 0.5`, `u1 = 0.25` returns `0.75`,
  exactly like `MAD r2.l*, u0, #1, u1, #1` (no row operand) and
  `MAD r2.l*, u1, #1, r0, #1` (row in rC); all three read back `0xBF` as
  RGBA8888. The earlier "rC reads as zero" table was an artefact of priming row
  registers with half-only destination writes. An immediate rC works too
  (`MAD r2.l*, r1, #1, #1, #1` gives `1.5`, saturating to `0xFF`).

### 5.10 Operand rD

|   Bits | Meaning                      |
|-------:|:-----------------------------|
|      5 | rD selector (0 = rB, 1 = rC) |
|      4 | sub-register selector        |
|      3 | fixed10 minus one            |
|      2 | enable rD (scale rC by rD)   |
|      1 | absolute value               |
|      0 | fixed10                      |

### 5.11 Operand register numbering

Patents:

* https://www.google.com/patents/US7659909
* https://www.google.com/patents/US8599208

|  Value | Meaning                 |
|-------:|:------------------------|
|  0..15 | row registers           |
| 16..23 | global registers        |
| 24..27 | ALU result registers    |
| 28..30 | embedded constants      |
|     31 | lowp vec2(0, 1)         |
| 32..63 | uniform registers       |
| 64..71 | condition registers     |
|     72 | fragment x-position     |
|     73 | fragment y-position     |
|     74 | primitive coverage, readable as `cov.l` only (section 5.8) |
|     75 | polygon face            |
|     76 | kill (discard) fragment |

### 5.12 Result accumulation

Patent: https://www.google.com/patents/US8521800

When the "recv" bit is set, operand rC is overridden with the accumulation value.

|       | recv            | send             |
|-------|-----------------|------------------|
| ALU0: | ALU0 += ALU3    | No action        |
| ALU1: | ALU1 += ALU0    | ALU0 += ALU1     |
| ALU2: | ALU2 += ALU1    | ALU0 += ALU2     |
| ALU3: | ALU3 += ALU2    | ALU2 += ALU3     |

Measured on AR20 with `MAD` probes: the handoff is **pairwise, between slots of
one packet**. A slot with `send` set (assembler `(other)`, bit 61) publishes its
result and a slot with `recv` (`(this)`, bit 60) uses it *in place of* rC: `MAD
r3.*h, #0, #1, 0.1` with recv returns the sender's value (0.25 and 0.5 were
measured), never 0.1. Reversing the order - recv on the earlier slot - transfers
nothing, and a recv in the following batch gets no value either. Read the table
above as "the receiving slot's rC is overridden by a sending slot's result in the
same packet", not as a per-slot sum into ALU0.

The scope was measured directly in a two-packet batch (notes 37): the markers
work in either packet (`send-p1-recv-p1` = `0x50`, `send-only-p0` = `0x40`,
`recv` alone = `0x10`), but a send in packet 0 slot 3 into a recv in packet 1
slot 0 gives `0x10`, the same as its unmarked twin, as does the tested reverse
direction; two senders in one packet also do not produce a transfer. These negative results cover the tested pairings, not every slot combination.
Note 41 tested the previously outstanding pairing too: packet 0 slot 0 send
to packet 1 slot 1 recv returns only packet 1's own previous-slot contribution
(0x50), and with that previous slot disabled the receiver returns its
unaccumulated value (0x10). The reverse direction and an alternate packet-0
slot-3 sender also do not transfer. Thus the specifically singled-out (0,1)
pairing is now closed; other cross-packet slot mappings remain untested.
The "packet" in this section's name is a single ALU packet of four slots, and
the accumulator does not ride the latch rotation in the pairings tested.

### 5.13 Addition disable

When bit "addition disable" is set, the Multiply-Add operation turns into two multiplies. The fx10 result of each multiply goes to the low/high subregisters of the destination register.

| write high subregister | write low subregister | destination low | destination high |
|:----------------------:|:---------------------:|:---------------:|:----------------:|
|            1           |           0           |      rC\*rD     |       rA\*rB     |
|            0           |           1           |      rA\*rB     |       rC\*rD     |

### 5.14 Pipeline and forwarding



The behaviour below is measured on AR20 hardware. Where it is incomplete the text says so. All
measurements use the Grate assembler harness (section 10).

**Current forwarding model (synthesis of notes 28, 31, 32, 36, 38, 39, 43, 44, 45, 46 and 47).**
A read of aluN in slot S of packet k is served in the complete tested rotations
by the slot-N result from packet `(k-1) mod M`, where M is the total number of
ALU packets in the group. The reading slot can be any of the four slots.
A one-packet group has its same-packet cross-slot path; in a multi-packet group
a packet does not see its own results.

| arrangement | source of the read |
|:--|:--|
| one-packet group | that packet's slot-N result, from any other slot; a read in slot N itself is 0 |
| group with M=2 or 3 packets, one packet per batch | packet (k-1) mod M's slot-N result, for any reading slot, in the tested arrangements |
| M=4, size 2 packed 3+1 or 1+3 | full cycle for all four latches, including every packet-3-to-packet-0 wrap reader slot |
| M=4, size 3 packed 2+1+1, 1+2+1 or 1+1+2 | full cycle for all four latches, including every packet-3-to-packet-0 wrap reader slot |
| M=4, size 4 packed 1+1+1+1 | full cycle for all four latches and every wrap reader slot |
| M=4, size 2 packed 2+2 | full cycle for all four latches, including every packet-3-to-packet-0 wrap reader slot (note 47; notes 38-39's "alu3 only" came from probes that wrote packet 2's slots) |
| M=5, size 2 or 3, eight tested distributions | full cycle for all four latches and every packet-4-to-packet-0 wrap reader slot |
| M=5, size 4, four tested distributions (2+1+1+1, 1+2+1+1, 1+1+2+1, 1+1+1+2) | full cycle for all four latches and every packet-4-to-packet-0 wrap reader slot |
| M=6, size 2 or 3, four tested distributions (2+2+2, 3+3, 1+2+3, 3+2+1) | full cycle for all four latches and every packet-5-to-packet-0 wrap reader slot |

The read is group-scoped. M=2 and M=3 form complete rotations in the
arrangements measured, and M=4 is complete in every tested packing. At size 2,
3+1 and 1+3 have all three forward edges for all four latches, and every wrap
latch can be read from every reader slot (note 39). At size 3, the 2+1+1,
1+2+1 and 1+1+2 distributions have the same complete four-latch cycle
(note 43). The size-4 one-packet-per-batch reference has also been completed:
all four forward edges, all four wrap latches and all four wrap reader slots
work (note 43). Notes 38 and 39 reported that the 2+2 split wrapped only through
packet 3 slot 3 to alu3, but their probes wrote `B0`, `B1` and `B2`, which are
packet 2's slots. Note 47 varies packet 3's writer slot, the read latch and
packet 0's reader slot independently: all sixteen diagonal cells return 0x60
for all four reader slots and all 48 cross-latch cells return 0. The writer slot
selects the latch, so the wrap cannot move a value to another latch index.

**Five-packet groups.** Note 44 tested eight distributions at sizes 2 and 3:
2+3, 3+2, 1+1+3, 1+2+2, 1+3+1, 2+1+2, 2+2+1 and 3+1+1. Each has all four
forward edges for all four latches and all sixteen packet-4-to-packet-0
wrap-latch/reader-slot combinations. Own-packet reads and all fifteen
non-predecessor source/reader pairs per distribution return 0. Conflict tests
also show the immediate predecessor supplying the read when multiple packets
write the same latch. The fifth packet's marker value was checked by direct
row readback in each manifest.

These five-packet results rule out several simple explanations for the
restriction once reported at M=4 2+2: it is not reproduced by having two
multi-packet batches, no singleton batch, both wrap endpoints in multi-packet
batches, or the tested mirrored batch orders. Note 47 later showed that the
restriction itself was a slot-addressing error, so these shapes are complete
for the same reason 2+2 is. They do not prove a general rule for all group
sizes or cover every possible distribution.

**Six-packet groups.** Note 45 tested 2+2+2 at size 3, 3+3 at size 2, and
1+2+3 with its batch-order reverse 3+2+1 at size 3. All four have the complete
cycle: five forward edges for all four latches and all sixteen
packet-5-to-packet-0 wrap-latch/reader-slot combinations, with the six
own-packet negatives and all twenty-four non-predecessor pairs returning 0
and the conflict cases showing the immediate predecessor. The sixth packet's
marker value (0x08) was checked by direct row readback in each manifest. In
the same runs the M=4 2+2 control still returns 0 for the alu0 wrap and 0x60
for alu3, and the M=5 wrap still returns 0x70. Because 2+2+2 is the closest
six-packet analogue of the M=4 2+2 shape and is complete, uniform two-packet
occupancy does not by itself restrict the wrap. The four-packet 2+2 shape is
complete too once its packet-3 slots are addressed correctly (note 47), so no
tested four-, five- or six-packet arrangement has a restricted wrap.

**Four-batch five-packet groups.** Note 46 tested the four size-4 splits:
2+1+1+1, 1+2+1+1, 1+1+2+1 and 1+1+1+2. A four-batch group with five packets
has exactly one two-packet batch, so these keep the packet and batch counts
fixed and move the pair from first to last. All four have the complete cycle:
all four forward edges for all four latches and all sixteen
packet-4-to-packet-0 wrap-latch/reader-slot combinations, with the five
own-packet negatives and all fifteen non-predecessor pairs returning 0. The
same runs keep the M=4 2+2 control at 0 for the alu0 wrap and 0x60 for alu3,
and the five-packet 2+3 wrap at 0x70. Because 2+1+1+1 puts packet 0 in the
pair's batch and 1+1+1+2 puts packet 4 in the last batch with packet 3, and
neither restricts the wrap, the pair's position is not sufficient either. All
twelve tested five-packet distributions (eight at sizes 2 and 3, four at size
4) have the complete cycle, and the M=4 size-2 2+2 shape is complete as well
(note 47). Every tested four-, five- and six-packet arrangement now has the
full cycle; the only coupling measured is that the writer slot selects the
latch index.

For the five-packet wrap matrix, the producer slot is coupled to the latch
index: packet 4 writes through slot N to test aluN, while the consumer slot is
varied independently across all four slots. Thus all four corresponding
latch-write paths and every reader slot are measured, but arbitrary
writer-slot/latch cross-combinations are not.

The group-flush carry is separate from this within-group rotation. At
alu_buffer_size=1, the previous group's last packet's slot-0 result can be
read by slot 0 of the next group, one group deep. No cross-group carry was
seen at sizes 2 or 3 in the tested shapes (notes 31, 32, 36). At size 4, the
follow-up tested alu0 writes from each of the four prior-group packets to
next-group slot 0, plus a packet-3 alu0 write read in slot 1 and selected
packet-3 alu1 reads in slots 0 and 2; all returned 0 (note 40). This is
negative evidence for those combinations, not a complete test of every latch
and reader slot. The carry is a forward, not saved latch storage.

Format remains part of the lookup: .l* writes feed fx10 .l reads, .lh writes
feed bare fp20 reads, and high-half-only writes were not observed in either
view. Row registers, not latches, remain the proven storage path across groups.

The recv/send accumulator is a separate packet-local path (notes 13, 16, 37,
41). It accumulates from earlier slots in the same packet. The tested
cross-packet pairings do not transfer, including packet 0 slot 0 send to
packet 1 slot 1 recv, even though an ordinary alu0 read crosses that packet
boundary. The receiver in slot 1 receives only its own packet's previous-slot
contribution; with that previous slot disabled it gets its unaccumulated
value. The reverse direction and an alternate packet-0 slot-3 sender also
show no transfer; a same-packet positive control still works (note 41). A
send does not disturb ordinary latch forwarding. The specifically outstanding
(0,1) pairing is therefore closed, but other cross-packet slot mappings remain
untested.

**Readback channel order (re-measured, and it corrects this document).** Fill all
four slots with one value, then overwrite exactly one slot and see which byte
moves. With every slot at `0.25` the word is `0x40404040`, and overwriting a
single slot with `0.75` gives:

| slot overwritten | word | byte that moved |
|:--|:--|:--|
| `r2.l*` | `0x404040BF` | byte 3 |
| `r2.*h` | `0x4040BF40` | byte 2 |
| `r3.l*` | `0x40BF4040` | byte 1 |
| `r3.*h` | `0xBF404040` | byte 0 |

So the 32-bit word is, from MSB to LSB, **`r3.hi`, `r3.lo`, `r2.hi`, `r2.lo`** -
i.e. byte 0 (least significant) = `r2.lo`, byte 1 = `r2.hi`, byte 2 = `r3.lo`,
byte 3 = `r3.hi`. An independent shape confirms it: a packet that leaves `r2.lo`
at 0.25 and `r3.lo` at 1.0 stores `0x00FF0040`, and a packet leaving `r2.lo` at
0.75 stores `0x00FF00BF` - the `r2` value is always the low byte. The table of
overwrites above is correct in its values but its "byte that moved" column is
labelled from the wrong end; read it as byte 0 for `r2.l*`, byte 1 for `r2.*h`,
byte 2 for `r3.l*` and byte 3 for `r3.*h`.

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

The `0x40000000` case needs precision, because an obvious reading of it
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

**The latch map, measured with one reader per run.** Four distinct writers in
one packet (`r0.lo = 0.25`, `r0.hi = 0.5`, `r1.lo = 0.75`, `r1.hi = 1.0`) and a
single reader `MAD r2.l*, aluN.l` in the next packet give:

| latch read | value | looks like |
|:--|--:|:--|
| `alu0` | 0.25 | slot 0's result |
| `alu1` | 1.0 | slot 3's result |
| `alu2` | 0 | nothing |
| `alu3` | 0 | nothing |

The `alu1` row is misleading: the reader packet's own `ALU1` computed `1.0`
into `r3.lo`, and repeating the probe with different writer values still returns
`1.0`. This probe did not isolate the source of that value; later controlled
tests show that a read in the reading slot's own latch returns zero. Same-packet
shapes show where cross-slot forwarding works:

| shape (writers `r0.lo = 0.25`, `r0.hi = 0.5`) | read | value |
|:--|:--|--:|
| reader in slot 2 | `alu0` | 0.25 - slot 0, same packet |
| reader in slot 2 | `alu1` | 0 - slot 1 wrote only the **high** half |
| reader in slot 3 | `alu0` | 0.25 |
| reader in slot 3 | `alu1` | 0 (same reason) |
| reader in slot 2 / slot 3 | `r0.l` / `r0.h` | 0 - row-register writes are not visible in the same packet |

**Initial size-1 flush probe: which latch survives what.** Reading one latch
from one specific slot, with a single writer in the previous packet, separates
"inside this packet" from "carried over" only for the original
`alu_buffer_size=1` arrangement:

| read from | latch | previous packet wrote | result |
|:--|:--|:--|--:|
| slot 0 | `alu0` | slot 0, `r0.lo = 0.25` | **0.25** - carried over |
| slot 2 | `alu0` | slot 0, `r0.lo = 0.25` | 0 |
| slot 0 | `alu1` | slot 1, `r0.lo = 0.25` | 0 |
| slot 2 / slot 3 | `alu1` | slot 1, `r1.lo = 0.75` | 0 |
| slot 2 | `alu0` | slot 1, slot 2 or slot 3 only | 0 |
| any slot | `alu2`, `alu3` | slot 2 / slot 3 low write | 0 |

In these size-1 probes, exactly one value crosses the **group flush**:
**`alu0`, read from slot 0, holds the previous group's last packet's slot-0
result.** This is not the general within-group packet-forwarding rule measured
later. The same-packet path is cross-slot: a read of `aluK` is satisfied by
the packet's slot-K **low-half** write when a different slot performs the read,
whether slot K comes before or after the reader. A read in slot K itself is
zero. A high-half-only (`.*h`) or fp20 (`.lh`) destination leaves the fx10
latch view at zero.

All four latches are live **inside their own packet**, including 2 and 3: with
`ALU2: MAD r1.l*, u0` and the reader in slot 1 or slot 0, `alu2` returns the
writer's value (`0.75` -> `0xBF`), and the same holds for `alu3` from slot 3.
The earlier "`alu2`/`alu3` never carry a value" reading came from probes whose
readers sat in the *next* packet, where only slot 0's latch survives.

**Re-measured, and the previous paragraph needs the other half of the story
(notes 28).** A packet boundary and a group boundary are not the same thing.
Everything above was measured at `alu_buffer_size` 1, where every batch is its
own group, so "the next packet" was also "the next group" and came with a full
ALU buffer flush. Inside a group of two batches (`alu_buffer_size` 2) a reader
one packet after the producer sees *all four* latches, from any slot:

| read one packet later, same group | word | latch |
|:--|--:|--:|
| `pkt-alu0-s0` / `pkt-alu0-s1` | `0x00000010` | `alu0`, from slot 0 or slot 1 |
| `pkt-alu1-s0` | `0x00000040` | `alu1` |
| `pkt-alu2-s0` | `0x0000007F` | `alu2` |
| `pkt-alu3-s0` | `0x00000020` | `alu3` |
| `pkt-two-latches` | `0x00007F40` | `alu1` from slot 0 and `alu2` from slot 1 in one packet |
| `pkt3-alu0-s0`, `pkt3-alu1-s0` (size 3) | `0x00000000` | two packets later: nothing |

The group flush is what keeps only `alu0`-from-slot-0, so the earlier "only one
value crosses a packet boundary" is a statement about the flush, not about the
pipeline stage - and that one value is a **forward, not a saved register**
(notes 31): it is the previous group's slot-0 result, an all-NOP group in
between loses it, two groups back it is gone, and writing through r5 or r7
instead of r2 changes nothing, so it is not a row alias either. A compiler
cannot stash a value in a latch across a group; it needs a row register.

**A batch with more than one packet changes the rules inside the batch**
(notes 32). With one packet the within-packet forwarding above applies. With
three packets (twelve instructions) it does not: a read is served by another
packet's slot-N result instead - all four latches, any reading slot - so the
same chain that works in a one-packet batch dies in a three-packet one:

| same program | one-packet batch | three-packet batch |
|:--|--:|--:|
| two-link chain inside one packet | `0x00100010` | `0x00000000` |
| packet 0 writes, next packet reads | - | `0x00100000` (every latch) |
| two packets apart | - | `0x00000000` |
| one hop per packet, three deep | - | `0x00100010` |

**The rule is circular, not forward-only (notes 36).** For tested groups of
M=2 or M=3 packets, a latch read in slot S of packet k is served by packet
**(k-1) mod M**, for every latch index and any reading slot. M=1 is the
separately measured one-packet case, with same-packet cross-slot forwarding
and own-slot exclusion. M=4 is complete in every tested packing: the size-2
splits 3+1 and 1+3, the size-3 splits and the size-4 one-packet-per-batch
reference all have the four-latch cycle, and size-2 2+2 does too once packet
3's slots are addressed correctly (note 47; notes 38-39 wrote packet 2's
slots). With one packet per batch at size 4, note 43 completed all four
latches and all four reader slots. That is why the three-packet "two packets apart" row
reads 0 - packet 2's source is packet 1, not packet 0 - and why the backward
row of the table below is nonzero. The earlier "previous packet, forward only /
packet 0 sees nothing" wording came from a backward probe that moved latch
index and the read slot at the same time; a two-packet batch makes the wrap
visible in both directions:

| two-packet batch | word | reading |
|:--|--:|:--|
| `xf-alu0`..`xf-alu3` | `0x00000010` | packet 1 reads each latch packet 0 wrote |
| `xf-read-s1`, `xf-read-s3` | `0x00000010` | the reading slot does not matter |
| `xf-bare` / `xf-fp20` / `xf-hi` | `0` | the format rules are unchanged |
| `back`, `back-s1`..`back-s3` | `0x00000040` | **packet 0 reads packet 1's latches** |
| `back-cross-a/b`, `back-value` | `0x40`, `0x7F` | the latch index decides; the value is the writer's |
| `back-bare`, `back-fp20` | `0` | format rules hold in this direction too |
| `intra-p1`, `chain-2pkt` | `0`, `0x10` | a packet's own slots do not forward; one hop crosses, the next dies |
| `chain-1pkt` | `0x00100010` | one-packet control: both hops work |
| `carry-p1` / `carry-p0` / `carry-both` | `0x10` / `0` / `0x40` | the carry is the last packet's slot-0 result |
| `carry-p1-s1` | `0` | the carry is read by slot 0 only |

With three packets the cycle closes: `back-from-last` (`0x40`) is packet 0
reading packet 2, while `back-same`, `back-cross-a/b` and `back-two-apart`
(`0`) are reads of the "next" packet, which is not where packet 0 looks.
`both-read-p0` (`0`) and `both-read-p0-last` (`0x40`) show the source is
fixed by the packet index and ignores what the reader itself wrote.

**The rotation is group-scoped, not batch-scoped.** With one packet per batch,
`alu_buffer_size` 2 rotates over the group's two batches (forward `0x10`,
backward `0x40`) and size 3 over its three, including the 0<-2 wrap (`0x40`).
At size 1 the group contains only the single-packet case. Thus M is the total
ALU packet count in the group in the arrangements measured. For M=4, the
size-2 3+1 and 1+3 shapes, the size-3 splits and the size-4
one-packet-per-batch test all have the full four-latch cycle, and size-2 2+2
does too with correctly addressed packet-3 slots (notes 39, 43, 47). The
section above's "one packet later, same group" rows are the M=2/M=3 case of
the same rule.

The group carry follows the group's **last** packet: a write in the last packet
of a three-packet batch carries (`0x10`), the same write in its first packet
does not (`0`) because the batch's last packet wrote nothing through slot 0. A slot reading its **own** latch reads 0, in the same packet and
one packet later (`same-self`, `pkt-bare` shapes), which the "satisfied by the
reading packet's own slot" sentence above was reaching for. That carry is
measured **only when a group is one batch** (`alu_buffer_size` 1): the same
flush shape at sizes 2 and 3 returns 0 from every batch of the previous group
(notes 36), which narrows notes 31 rather than contradicting them - notes 31
measured exactly the size-1 configuration.

**The format decides which read sees the value.** A `.l*` write fills the
`aluK.l` (fx10 low) view and leaves the bare `aluK` (fp20) read at zero; an
`.lh` write fills the bare read. Section 9's thirteen variants read `alu1`
*bare*, in packets whose slot 1 wrote `.l*`, one batch away - three reasons for
the same zero, and none of them a property of the register.

**Direct writes to 24..27 (notes 28).** The assembler refuses `aluN` as a
destination, so the word was patched: `MAD r2.l*, u0, #1, #0` with part1 bits
15..21 rewritten. The hardware accepts the code and writes no row register, but
the code does not select the latch - a write issued from slot 1 is readable as
`alu1` whichever of 24..27 the field names (`dist-slot1-dest24-alu1` and
`dist-slot1-dest25-alu1` both `0x10`, `dist-slot1-dest24-alu0` `0`).

The scheduler rule supported by the complete tests is: use same-packet
cross-slot forwarding with own-slot exclusion for a one-packet group; for
multi-packet groups use packet (k-1) mod M only in the tested complete
arrangements. At M=4, every tested packing has the full four-latch cycle:
size-2 3+1, 1+3 and 2+2, the three size-3 splits, and size-4 1+1+1+1
(note 47). The writer slot still selects the latch index, so a wrap edge is
only available for the latch that the group's last packet writes in that same
slot. Do not treat this rotation as storage across a group boundary.

The separate flush carry is established only for a one-batch group: the
previous group's last packet's slot-0 result into slot 0 of the next group.
At sizes 2 and 3 the tested shapes carry nothing. At size 4 the tested
alu0/alu1 combinations carry nothing, but not every latch/reader-slot
combination was probed. Land values that must survive a group in row registers.

The blob itself relies on the chain heavily: for eight summed `vec4` uniforms it
emits one packet of `mad r3.hl, c0, #1, c4` ... `mad r3.hl, c8, #1, d0`, reading
the latches by number *inside* the batch. Whatever the exact numbering is, the
`slot k writes -> slot k+1 reads` form is what a scheduler should emit.

Earlier attempts disagreed because the probe shape matters: writes done as `.lh`
(fp20) and read back through a `DW` store do not carry the value, so a control
built that way is invalid. Outside those shapes the chain itself is measured:
with `ALU0` writing `r0.l*` and `ALU1`/`ALU2` chaining through `alu0.l`/`alu1.l`,
both destinations end up holding 0.25.

**Several writers into one half OR their codes, in whichever format they
wrote.** The fp20 codes are `0.25 = 0xE8`, `0.5 = 0xF0`, `0.75 = 0xF4`, so two
writers of 0.25 and 0.5 leave `0xF8` - **1.0**, not 0.75 - and three writers
leave `0xFC` = 1.5, which the 8-bit store then saturates. Identical values are
idempotent, the order does not matter, and one fx10 writer beside one fp20
writer leaves a value neither wrote (`0x42` where either alone reads `0x40`), in
both orders. The merge is a bitwise OR of the destination half's own code and it
does not depend on the format.

**Accumulate: `(this)` adds the previous slot's result and discards `rC`.**
ALU bits 60 and 61 are named "accumulate this" and "accumulate other" in the
assembler. One packet per row, `u0 = 0.25`, `u1 = 0.5`, `u2 = 0.125`:

| instruction | result | reading |
|:--|--:|:--|
| `ALU0: MAD r2.l\*, u1, #1, #0, #1 (this)` | 0.5 | no predecessor, so 0 is added |
| `ALU0: MAD r2.l\*, u1, #1, u2, #1 (this)` | 0.5 | **the `rC` addend is discarded** |
| `ALU0: MAD r0.l\*, u0 ..` then `ALU1: MAD r2.l\*, u1, #1, #0, #1 (this)` | **0.75** | adds slot 0's 0.25 |
| `ALU0: u0`, `ALU1: NOP`, `ALU2: MAD r2.l\*, u1, #1, #0, #1 (this)` | 0.5 | only the *immediately* preceding slot counts |
| `ALU0: u0`, `ALU1: MAD r1.l\*, u2 ..`, `ALU2: MAD r2.l\*, u1, #1, #0, #1 (this)` | 1.0 | 0.75 + 0.5 = 1.25, stored clamped |
| any row with `(other)` instead of `(this)` | as without it | **no arithmetic effect measured** |

Two consequences: `(this)` chains a `MAD` into the next slot without spending an
operand, and because it *replaces* `rC` an instruction cannot have both a real
addend and `(this)`. The accumulator source is the previous slot, not a
persistent register, so it does not cross a packet boundary. Bit 61 (`(other)`)
produced no observable arithmetic difference in any shape; the encoder in
[[Compiler Internals]] reads it as "send" in its register tracking, so it is a
pipeline bit rather than an operand source.

### 5.15 Condition file in practice

Registers 64..71 are the condition file, and `crN` addresses **16 scalar
booleans**: `crN` is register `64 + (N >> 1)` with the sub-register bit set to
`N & 1`, which is why the disassembler prints `cr%d` as `(reg - 64) * 2 + id`.

They are readable as ALU sources (fx10 half, one `crN` per half), and measure as
pure booleans:

| write | read back |
|:--|--:|
| never written | 0.0 |
| `MAD cr0, u0, #1, #0, #1`, `u0 = 0.75` | 1.0 |
| same, `u0 = 0.5` | 1.0 |
| same, `u0 = 0.25` | 1.0 |
| same, `u0 = 0.0` | 0.0 |
| `.. (eq)`, `u0 = 0.75` | 0.0 |
| `.. (eq)`, `u0 = 0.0` | 1.0 |
| `.. (gt)`, `u0 = 0.75` | 1.0 |

So a write without a condition stores `result != 0` (any non-zero value, not the
value itself), and a write with a condition stores the condition's 0/1 - the
same 0.0 / 1.0 a row-register destination receives. `cr0` and `cr1` are
independently addressable (writing `cr1` leaves `cr0` at 0) and `cr14`/`cr15`
work, so all 16 are real. A read in the **same packet** as the write returns 0:
the condition file needs a packet boundary, unlike the `aluN` latches.

**The blob does not use the condition file.** Twelve control-flow shaders - a
`discard`, a ternary select, `&&`/`||`, `for`/`while` loops with a comparison and
with `break`, nested `if`/`else`, an early `return`, MRT output, multiple
discards, and two comparison-only shaders - dump **zero** `crN` references when
the destination field of every ALU word is decoded (13 shaders counting the
earlier sweep). The blob expresses each predicate as a condition on a
**row-register** destination instead - `mad_le r12.hl, -r5, #1, c0.l` is its
comparison idiom - where the result is the same 0.0 / 1.0 and can be multiplied
in where needed. So a backend needs no CR at all; the file is only an extra
boolean scratch, and the one advantage it has is that a value survives a packet
boundary.

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

An earlier conclusion is retracted: an early probe reported
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

**Half selection is the part that bites.** A bare `fp20` source read takes the
**high** half of the pair: with `r2.lo = 0.25` and `r2.hi = 1.0`,
`MAD r3.*h, r2, #1, #0` returns `1.0`. The low half is only addressable as an
*fx10* operand - `r2.l` / `r2.h` read the halves as 1.10 fixed point, and the
same probe reads 0.25 through `r2.l` and 1.0 through `r2.h`. The destination side
is independent: `.l*` writes the low half only, `.*h` the high half only, and
`.lh` writes **both** (`ALU_DST` in `fragment_asm.y` computes
`low = l1||l2`, `high = h1||h2`). So a value a later batch must read as fp20 has
to be written in the high half - `.*h` or, as every working test shader does,
`.lh`. A `.l*`-only write is invisible to a later fp20 operand read. That one
rule explains a whole family of probes that appeared to "lose" values.

**Refined later, and the mechanism is the value format, not the width.** The ISA
has exactly two formats, `fx10` and `fp20`, and the destination suffix selects
which one is written: `.l*` / `.*h` write an **fx10** value into the low/high
slot (the ALU performs the conversion), while `.lh` writes the **fp20** result.
The two are not interchangeable. A register written as fx10 and then read back as
fp20 decodes to ~0.0078 (`0x02` at 8-bit output) - that is the whole `0x02`
mystery, and it is a format mismatch, not truncation. In the other direction the
conversion is exact: 0.75 written `.lh` (fp20), copied with `.l*` (fp10
conversion) and stored reads `0xBF` = 0.75.

Consequence for probes and for a backend: keep computations in fp20 and only
convert to fx10 at the point where a value is consumed as fx10 (a render-target
store, a texture coordinate). Mixing them silently produces plausible-looking
wrong numbers, which is exactly how the earlier `0x02` readings were
misdiagnosed as a forwarding hazard. The controlled
comparison is one 3-batch copy chain, changing nothing but the destination
suffix:

| destination suffix | result word | reading |
|:--|:--|:--|
| `.lh` (both halves) | `0xEF00F300` | the value propagates |
| `.*h` (high only) | `0x00000000` | nothing propagates |
| `.l*` (low only) | `0x00000000` | nothing propagates |

So the operative rule is: **any value a later batch must read has to be written
with `.lh`.** That single line accounts for the `0x02` readings that were
wrongly attributed to forwarding or operand ports before the format difference was understood.

Even so the value is not exact through a multi-hop chain - 0.75 written once and
copied twice comes back as `0xF3` = 0.953 - so a quantitative probe must write a
value once and let the DW stage read that same register, never copy it through
intervening ALU packets.

**There is no rC operand-port restriction - settled.** `MAD r2.l*, rA, #1, rC,
#1` computes `rA + rC` and is read out as fx10, so a `DW` sees it. With
`u0 = 0.5`, `u1 = 0.25` and row registers primed with `.lh` (the only write that
stores a usable fp20), all three arrangements return the exact answer:

| rA | rC | result | reading |
|:--|:--|:--|:--|
| `r1` (row, 0.5) | `u1` (uniform) | `0xBF` | 0.75 - both honoured |
| `u0` (uniform) | `u1` (uniform) | `0xBF` | 0.75 - control |
| `u1` (uniform) | `r0` (row, 0.5) | `0xBF` | 0.75 - row in rC is read |

An immediate rC beside a row rA saturates as expected
(`MAD r2.l*, r1, #1, #1, #1` = 1.5 -> `0xFF`). So a backend may put any uniform,
row register, immediate or embedded constant in rC.

**Obsolete reading, retained as a caution:** rC was measured
as reading zero when it named a uniform beside a row-register rA/rB - five rows
computed `1*1 + (-0.5)` as `1.0` instead of `0.5` - and a row register in rC was
measured as unread. Both came from priming row registers with `.l*` / `.*h`,
which store a truncated fp20 that reads back as ~0.0078, so neither operand ever
held the value the probe assumed. It is the same trap as the `0x02` readings
above, and the reason every measurement now writes `.lh` and reads
back `.l*`.

**`(other)` sends, `(this)` receives, and only to the adjacent packet.**
`(other)` is bit 61 on packet *k*, `(this)` is bit 60 on packet *k+1* of the same
batch. The receiver computes `rA*rB + acc`, so the accumulator **replaces** rC: a
receiver written `MAD r3.*h, #0, #1, 0.1` returns the sender's result (measured
0.25 and 0.5), never 0.1. Reversing the order transfers nothing (0.1 survives),
and a receiver in the next batch gets no handoff. It is a within-batch chaining
mechanism that complements `aluN` rather than replacing it.

Other forwarding facts, from existing working tests rather than new probes:

* MFU interpolation -> ALU, same batch: works (`fs_vs_tests.txt`).
* MFU interpolation (batch 1) -> ALU (batch 2): works (`sky_fs.txt`).
* TEX -> DW, same batch: works (`filter_quad_fs.txt`) - TEX in batch 2 and the
  store of its result in batch 2 produce the correct filtered quad.
* ALU -> DW, same batch: works (`sky_fs.txt`).


### 5.16 Arithmetic semantics



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

**Constant encoding.** A wrong constant produces plausible but incorrect
results: reading `0x3E000` as 0.25 instead of 1.0
makes `MIN`/`MAX` look broken. `fp20` is
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

Two related points are settled. Signed zero
is not reachable at all (9.2 item 4), so a `-0.0`/`+0.0` comparison cannot be
constructed - there is no tie rule to find. And the saturation point is measured
to 0.125 binades by reading the exponent back with `lg2`: `2^33` reads 33, and
so does every larger constructed value, while `2^41` halved eight times reads
25. That puts the largest finite value at `2^33` (consistent with
`2^32 * (2 - 2^-12)`, which is the same number to within one binade step); a
finer figure would need a different amplifier than `lg2`.

**fp20 writes truncate; there is no round-to-nearest anywhere in the ALU.**
Measured with `x = 1 + eps` then `4096*(x-1)` (embedded constant for the offset):

| eps | result | reading |
|:--|:--|:--|
| 0 | `0x00` | 0 |
| 1 ulp (`2^-13`) | `0x7F` = 0.5 | 1 ulp survives |
| 1.5 ulp | `0x7F` = 0.5 | **truncated to 1 ulp**; rounding would give `0xFF` |
| 2 ulp | `0xFF` = 1.0 | exact |

The assembler's `fp20` packing truncates too, so assembler and hardware agree and
a backend can predict every intermediate exactly from the emitted constants.

**The SFU is accurate to within the format, and its output is full range.**
Amplified with `K*(y - expected) + 0.5` at K = 256 (one fp20 ulp maps to ~0.25
of output range, 0x7F = exact):

| op | input | expected | measured |
|:--|:--|:--|:--|
| `rcp` | 3.0 | 0.333333 | `0x7F` |
| `rcp` | -2.0 | -0.5 | `0x7F` (sign preserved) |
| `ex2` (with `preex2`) | -1 | 0.5 | `0x7F` |
| `ex2` | -0.5 | 0.707107 | `0x7F` |
| `ex2` | -2 | 0.25 | `0x7F` |
| `sin` (with `presin`) | pi/6 | 0.5 | `0x78` -> 0.49989 |
| `sin` | 7pi/6 | -0.5 | `0x87` -> -0.49989 |
| `sin` | 2pi+pi/6 | 0.5 | `0x78` -> 0.49989 |
| `sin` | -pi/6 | -0.5 | `0x87` -> -0.49989 |
| `cos` (with `precos`) | 0 | 1.0 | `0x7F` |
| `cos` | 2pi/3 | -0.5 | `0x87` -> -0.49989 |
| `cos` | 2pi | 1.0 | `0x7F` |
| `frc` | -0.25 | 0.75 | `0x7F` |
| `lg2` | 0.5 | -1.0 | `0x7F` (negative results are produced) |

The `sin`/`cos` rows are **not** an SFU error: the argument itself is truncated to
fp20 on the way in. `pi/6 = 0.5235988` has an fp20 ulp of `2^-12` there, so the
register holds 0.5234375, and `sin(0.5234375) = 0.4998605` - which is the
measured 0.499885 to within 2.5e-5. Likewise `7pi/6` truncates to 3.6650391 and
`sin` of that is -0.4998676 against a measured -0.499885. So the SFU itself
contributes less than ~0.1 ulp: **every op is exact to the precision of its
input, including arguments past `2pi`** (`sin(2pi+pi/6)` and `cos(2pi)` are as
accurate as their small-angle counterparts, so `PRE*` really does do range
reduction). A backend needs no polynomial refinement for any of them.

**The SFU does not clamp to [0,1].** The 8-bit output stage is what saturates, so
the question only looks ambiguous until the value is halved - storing both `y`
(byte3) and `y/2` (byte1) in one run separates a true 2.0 from a clamped 1.0:

| op | input | true value | `y` | `y/2` |
|:--|:--|:--|:--|:--|
| `rcp` | 0.5 | 2.0 | >=1 | >=1 (`0xFF`) |
| `rcp` | 0.25 | 4.0 | >=1 | >=1 |
| `sqrt` | 2 | 1.41421 | >=1 | `0xB4` = 0.706 -> **1.412** |
| `sqrt` | 4 | 2.0 | >=1 | >=1 |
| `rsq` | 0.25 | 2.0 | >=1 | >=1 |
| `ex2` | 1 | 2.0 | >=1 | >=1 |

So the MFU carries values above 1.0 and a shader may use them; only the store
clamps. That matters for `rsq`/`sqrt` chains, where an intermediate above 1.0 is
perfectly legal.

**PRE ops and their main op are one instruction in two words.** All three ways of
splitting them agree, and leaving the `PRE` out silently gives a wrong answer:

| variant | `ex2(-1)` |
|:--|:--|
| `preex2` in one batch, `ex2` in the next | 0.5 - correct |
| both `PRE` and main in the same batch (two MFU words) | 0.5 - correct |
| `preex2`, one empty batch, `ex2` | 0.5 - correct |
| `ex2` with no `preex2` | **1.0 - wrong** |
| `cos(0)` with no `precos` | **0 - wrong** |
| `sin(pi/6)` with no `presin` | 0.5216 - wrong |

The two words must also be given the **same operand**. Overwriting the operand
between them produces garbage rather than either answer: `preex2` of -1 then
`ex2` of 0 returns **0** (not 0.5, not `ex2(0) = 1.0`), and `precos` of 0 then
`cos` of 3 returns **0.706** (not 1.0, not `cos(3) = -0.99`). The `PRE` result is
not a usable value either - it cannot be captured and reused: read through the
`sfu` source, `preex2(-1)` gives 0.5, `preex2(0)` gives 0 and `preex2(2)`,
`presin(pi/6)` and `precos(0)` all saturate at >=1, which is not a coherent
function of the argument. So a backend must emit the pair atomically, with the
same register and value, in the same batch or any later one; the only thing it
can schedule around is unrelated work.

**Degenerate inputs** (all measured directly, no amplification needed):

| op | input | result | reading |
|:--|:--|:--|:--|
| `rcp` | 0 | `0xFF` | +maximum, no NaN |
| `rsq` | 0 | `0xFF` | +maximum |
| `rsq` | -4 | `0xFF` | +maximum - *not* -0.5 and not 0 |
| `sqrt` | -1, -4 | `0x00` | 0 |
| `lg2` | 0 | `0x00` | minimum |
| `lg2` | 0.25, 0.125 | `0x00` | negative results, clamped only at the store |

The `rsq` row is explained by modelling `RSQ = RCP(SQRT(x))`: `sqrt(-4) = 0` and
`rcp(0)` is +maximum, which is exactly what `rsq(-4)` returns. That model also
predicts `rsq(0) = +maximum`, and it does.

**The `PRE` intermediate is per operand register, and it lives long.** It
survives at least four intervening batches - 1, 2, 3 and 4 empty batches all
return the correct `ex2(-1) = 0.5` - two chains interleave (`preex2` of `r0` and
of `r1`, then `ex2` of both, returns 0.5 and 0.25 side by side, both with and
without a gap batch), and unrelated SFU work in between does not disturb it:
`rcp`, `frc`, and even another `preex2` on a *different* register all leave the
`ex2` result at 0.5. So several `PRE`/main pairs can be in flight at once and a
scheduler may separate the two words as far as it likes, the only constraint
being that nothing writes the operand register in between.

**An ALU write to the operand kills the pair - even the same value.** Rewriting the
operand with a *different* value between the two words yields `ex2(-1) = 1.0`,
the same wrong answer as omitting the `PRE` entirely; rewriting it with the *same*
value gives 1.0 as well. Writing some other register in between is harmless (0.5,
correct). So the rule is not "the operand must hold the same value" but "nothing
may write that register at all": a scheduler has to treat the pair as clobbering
its operand register, and a register allocator must not reuse it in between.

**Row-register values clamp at 2^33, and `ex2` underflows at -31.** Built by
doubling `2.0` in the ALU and reading the exponent back with `lg2` (resolution
0.125 binades):

| constructed | `lg2` reads |
|:--|:--|
| `2^31` | 31.06 |
| `2^32` | 31.94 |
| `2^33` | 32.94 |
| `2^34`, `2^35`, `2^41` | 32.94 - clamped |

That the *value* is clamped, rather than `lg2` refusing a large input, is shown
by halving the `2^41` chain eight times: it reads **25**, i.e. `2^33 / 2^8`, not
33. At the bottom, `ex2(-30)` is exact while `ex2(-31)` and below return 0, so
the usable window is roughly `2^-31 .. 2^33` - about 64 binades, well short of
what the 8-bit exponent field in the encoding would allow (`2^224`), which means
the limits are in the datapath rather than the format. Range analysis has to stay
inside that window: above it clamps silently, below it collapses to zero.

**There are two separate hard limits: 64 exec batches and 64 ALU packets.** A
65-batch program is refused with `fs: line N: Over 64 exec batches` regardless of
how little work those batches carry - 65 batches holding only 8 ALU packets fails
the same way - and a 65-packet program is refused with `fs: line N: Over 64 ALU
instructions in total`. The ALU count is per **packet**, not per slot: 20 batches
holding 4 ALU slots each (80 instructions) assembles and runs, so the four slots
inside a packet are free against the budget. Both are assembly-time checks, so
they bound what a backend may emit rather than what the hardware could execute,
and the 64-batch limit is the one that bites first whenever a program has
non-ALU batches at all.

**8-bit output conversion is `round(x * 255)` with halves rounding down.**
Calibration values `0.5 -> 0x7F` (127, not 128) and `0.125 -> 0x20` (32, not 31)
rules out both `trunc(x*256)` and plain round-half-up. Relevant to a backend only
because it means a shader cannot rely on `1.0` landing on `0xFF` via `x*256`.


## 6. MFU


The MFU unit can fetch and interpolate 4 component vectors per instruction and/or evaluate scalar special functions. Based on [this design](http://pctuning.tyden.cz/ilustrace3/soucek/g80/paper-164.pdf).

|   Bits | Meaning  |
|-------:|:---------|
| 58..63 | register |
| 54..57 | opcode   |
| 43..53 | mul1     |
| 32..42 | mul0     |
| 28..31 | unknown      |
| 21..27 | var3     |
| 14..20 | var2     |
|  7..13 | var1     |
|   0..6 | var0     |

Each 7-bit `varN` slot is `[0]` saturate, `[2:1]` opcode, `[6:3]` a **4-bit row
index** into TRAM. `libcgc/shader.c` prints exactly that field as `v<n>`, and the
blob's output for two linked varyings is `ipl: var1 v0, var1 v0, var1 v1, var1
v1` - dense row indices 0 and 1, unrelated to the GLSL `location`s the varyings
were declared with (those only appear on the vertex side, in the linker
instruction at `0x300`). The index is *not* a straight copy of the linker's
`tram_row_index` field when rows are sparse, and only the components the shader
reads get a slot.

| opcode | Mnemonic | Meaning                      | pseudo-code         |
|-------:|:--------:|:-----------------------------|:--------------------|
|      0 |    NOP   | No operation                 |                     |
|      1 |    RCP   | Reciprocal                   | rD = 1.0 / rA       | 
|      2 |    RSQ   | Reciprocal square root       | rD = 1.0 / sqrt(rA) |
|      3 |    LG2   | Logarithm base 2             | rD = log2(rA)       |
|      4 |    EX2   | Exponent base 2, second step | rD = pow(2.0, rA)   |
|      5 |   SQRT   | Square root                  | rD = sqrt(rA)       |
|      6 |    SIN   | Sine, second step            | rD = sin(rA)        |
|      7 |    COS   | Cosine, second step          | rD = cos(rA)        |
|      8 |    FRC   | Fractional value             | rD = rA - floor(rA) |
|      9 |  PREEX2  | Exponent base 2, first step  | rD = pow(2.0, rA)   |
|     10 |  PRESIN  | Sine, first step             | rD = sin(rA)        |
|     11 |  PRECOS  | Cosine, first step           | rD = cos(rA)        |

### 6.1 SFU semantics

The pseudo-code column above is misleading for the three `PRE*` ops: they do not
produce `rD = pow(2, rA)` as a value, and their result is not reachable as a
value at all. They compute a hidden intermediate that the *second step* op
consumes, so `PREEX2`+`EX2`, `PRESIN`+`SIN` and `PRECOS`+`COS` are one operation
in two words:

* the pair may be split across batches **in any way** - same batch, next batch,
  or with an empty batch in between all return the same correct value, so the
  intermediate survives at least one intervening batch;
* both words must be given the **same operand**. Overwriting the operand
  register between them yields garbage rather than either answer (`preex2` of
  -1 then `ex2` of 0 returns 0, not 0.5 and not `ex2(0) = 1.0`);
* the `PRE` result read through the `sfu` source is not a coherent function of
  the argument (`preex2(-1)` -> 0.5, `preex2(0)` -> 0, `preex2(2)` -> >=1), so
  it cannot be stored and reused;
* omitting the `PRE` word silently gives a wrong answer: `ex2(-1)` returns 1.0
  instead of 0.5, `cos(0)` returns 0 instead of 1.0, `sin(pi/6)` returns 0.5216.

The operand is normally a "pixel packet" row register (`r0..r3`, fp20). Row 4 is
readable too and does not hold an fp20 fraction - see 6.5. Rows above 4 are not
established, and the multipliers reject 4..9 as a source (6.3.2). The output
is **full range**: `rcp(0.5)` really is 2.0 and `sqrt(4)` really is 2.0, checked
by storing both the value and half of it in one run. Only the 8-bit output
conversion saturates. Accuracy is within the fp20 format for every op measured
(see section 6.1), and `PRESIN`/`PRECOS` do proper range
reduction, so arguments beyond `2pi` are as accurate as small ones.

One caveat on reading these numbers as ISA behaviour: a probe whose operand
never arrives computes on zero, and the results then look like opcode
semantics. `ex2` on a zero operand returns 1.0, `sin` returns 0, `rcp` and
`rsq` return +maximum and `sqrt` returns 0, which is exactly the pattern a
broken constant fetch produces. The failure mode and the controls that catch
it are in [[Hardware Testing]], "Fragment constants can read zero".

Degenerate inputs: `rcp(0)` and `rsq(0)` return +maximum, `sqrt` of a negative
returns 0, `lg2(0)` returns the minimum, and `rsq` of a negative returns
+maximum - consistent with `RSQ = RCP(SQRT(x))`, since `sqrt(-4) = 0` and
`rcp(0)` is +maximum. `FRC` is floor-based (`frc(-0.25) = 0.75`, not -0.25).

Row-register values **clamp at `2^33`** and `ex2` **underflows at `-31`**, so the
usable numeric window is about `2^-31 .. 2^33`. Halving a clamped `2^41` chain
eight times reads `2^25`, which shows the clamp is in the value rather than in
`lg2`'s input range.

Program size is bounded twice over: at most **64 exec batches** and at most **64
ALU packets**, both enforced by the assembler at parse time (`Over 64 exec
batches`, `Over 64 ALU instructions in total`). The four ALU slots inside one
packet count once, so 20 batches of 4 slots each is fine.

### 6.2 Varying fetch slots

The varying id corresponds to the TRAM component: var0 is TRAM.x, var1 is TRAM.y, var2 is TRAM.z, var3 is TRAM.w.

| Bits | Meaning                  |
|-----:|:-------------------------|
| 3..6 | TRAM index to fetch from |
| 1..2 | opcode                   |
|    0 | saturate                 |

Measured: a first probe that linked one export to `tram0`, `tram3`, `tram7` or
`tram15` and fetched the matching `tN` returned the same data for all four - but
that is an artefact: the linker row and the fetch index were moved **together**,
so every run read a row that had just been written. A two-row probe that writes
rows 0 and 7 at once and then fetches both returns **different** data at the two
fetches (`0x7F` at row 0, `0x28` at row 7), so the rows are separately
addressable and do not alias.

Caveat on that probe: its control, which feeds both rows from the *same* export
and should therefore read equal, also returned those two different values. So
the two rows demonstrably hold different data, but exactly how the second `LINK`
line's component mapping interacts with the first is not yet pinned down - a
clean test needs two genuinely different vertex exports.

What *is* settled is that **the same varying can be interpolated again in a later
batch**: an `ipl` of `t0` in batch 1 and again in batch 2 returns identical
values in both halves, so re-reading a varying costs a fetch but breaks nothing.
| opcode | Mnemonic | Meaning                        |
|-------:|:--------:|:-------------------------------|
|      0 |   NOP    | No operation                   |
|      1 |   VAR1   | Interpolate one float20 value  |
|      2 |   VAR2   | Interpolate two fixed10 values |

### 6.3 Multipliers

| Bits  | Meaning              |
|------:|:---------------------|
| 8..10 | destination register |
|  4..7 | source register 1    |
|  0..3 | source register 0    |

### 6.3.1 Destination registers

| Value | Meaning            |
|------:|:-------------------|
|     0 | nothing observable: writes no row register and supplies no weight (measured) |
|     1 | barycentric weight (the value the blob uses) |
|  2..3 | barycentric weight as well - indistinguishable from 1 (measured) |
|     4 | row register 0     |
|     5 | row register 1     |
|     6 | row register 2     |
|     7 | row register 3     |

**Measured.** `mul0: dst4, src3, #1` (0.375) puts 0x60 in `r0.lo` with `r1.lo`
still 0x20, `dst5` does the mirror image, `dst6` puts 0x10 in `r2.lo` and `dst7`
0x10 in `r3.lo`, and in each case the other store pair returns exactly its
no-MFU baseline - so one destination writes one register. Destinations 0..3
were run with 1.0 as both operands (whose product carries 0xF7 in the `.hi`
byte, see 6.3.2) and left all four row registers at their primes; they write some
other state, not a row register.

That other state is the interpolation weight, and 1, 2 and 3 all supply it.
Writing a varying to a row register needs the weight, so substituting each
destination for `bar` in the word that fetches and storing the interpolated
colour shows which numbers the interpolator accepts. At two sample points with
different colours, `dst1`, `dst2` and `dst3` all reproduce the `bar` control
byte for byte (`0xD300F200` and `0xCB00F400`), and the same three numbers do it
with `mul1` as the producer of the second weight (`mul1-dst1`, `-dst2`, `-dst3`,
6.4) - so the observation holds for both units. `dst0` returns something else
entirely (`0xF300AF00` at the first point) and differs in its low byte between
runs of the same program - state nothing in the program wrote.

The three numbers behaving identically does not establish that they decode to
one register. The blob hardwires `mul0` to `w0`, `mul1` to `w1` and derives the
third weight as `1 - w0 - w1` (6.4), so a destination field that named the whole
weight and one that named `w0` would be indistinguishable from outside the unit;
what is measured is the equivalence, not the encoding.

### 6.3.2 Source registers

The SFU result is evaluated first and is available to use by MUL's via "SFU result" register.

|  Value | Meaning                 |
|-------:|:------------------------|
|   0..3 | row register 0..3       |
|   4..7 | not global registers, not row registers (measured); 7 reads zero |
|   8..9 | not row registers (measured); 8 reads zero |
|     10 | SFU result              |
|     11 | barycentric coefficient 0: the weight of the covering triangle's first vertex (measured, see below) |
|     12 | barycentric coefficient 1: the weight of its second vertex (measured, see below) |
|     13 | 1.0                     |
| 14..15 | both read zero (measured)   |

**Measured source values 4..9 and 14..15.** With `r0..r3 = 0.1, 0.2, 0.3, 0.4`
and `g0..g3 = 0.55, 0.65, 0.85, 0.95`, `mul0: r2, <src>, #1` gives (`r2.lo` is
byte 0 of the result, see section 8.1):

| src | `r2` after the multiply | src | `r2` after the multiply |
|--:|:--|--:|:--|
| 0..3 | the matching row register: `0x1A`, `0x33`, `0x4D`, `0x66` | 7 | `0x0000` |
| 4 | `0x7F56` | 8 | `0x0000` |
| 5 | `0x0089` | 9 | `0x4100` |
| 6 | `0x00A9` | 14, 15 | `0x0000` |

The same four candidates were run twice, once with the global registers primed
and once with them cleared, and the results were **byte-identical** in both
runs. So 4..7 do not alias `g0..g3` - the previous "possibly global registers"
guess is wrong - and 7, 8, 14 and 15 read as zero.

Sources 4, 5, 6 and 9 also ignore the row registers. Running each twice, once
with `r0..r3 = 0.1, 0.2, 0.3, 0.4` and once with `0.6, 0.65, 0.7, 0.75`, gives
the *same* two result bytes (`0x7F56`, `0x0089`, `0x00A9`, `0x4100`); the only
byte that moves is the untouched `r3.lo` prime. They are therefore neither row
nor global registers, and they read no value the program can set - what they do
read is deterministic, so it is internal state of the unit rather than stale
register content. Identifying it needs a probe that varies the automatic inputs
(fragment position, interpolation weights) rather than the register file.

That probe has been run (`--set matrix`, same generator). None of 4..9, 14 and
15 changes when the sample pixel moves from the quad's corner to a point inside
the triangle, and none changes between the two source fields, so they read
neither per-pixel geometry nor anything field-specific. Selector 9 is the one
that moves with the *word*: it reads `0x0000` in every program without a
varying fetch and `0x4100` in every program containing one, including an `fx10`
fetch, so it follows the fetch happening rather than the data it brings. What
that number is has not been identified.

**Isolated selector sweep.** A repeatable probe
(`tests/grate/asm/make_mfu_selector.py`, manifest
`asm_characterize.mfu_selector_src.json`) removes the interpolation and keeps
exactly one MFU word per variant: batch 0 primes `r0..r3` with 0.0625, 0.125,
0.25 and 0.375 at `alu_buffer_size = 1`, batch 1 runs one `mul0: r2, <src>, #1`,
batch 2 stores. Measured on the Toshiba AC100 (`7.2.0-gd1efa725955b`), three
repeats agreeing in both a pin run and a verification run of 76 rows each:

| src | result word | reading |
|--:|:--|:--|
| 0..3 | `0x00600010`, `0x00600020`, `0x00600040`, `0x00600060` | the matching row register (0.0625, 0.125, 0.25, 0.375) |
| 4..6 | `0x00607F56`, `0x00600089`, `0x006000A9` | the hand probe's values again, so not row state |
| 7, 8 | `0x00600000` | zero, as before |
| 9 | `0x00600000` | zero without a fetch; `0x4100` in any word that fetches a varying |
| 10 | `0x00600000` | the SFU result with no SFU word in the program |
| 11, 12 | `0x0060FF00` | identical with no varying fetch at all, consistent with both naming a barycentric coefficient |
| 13 | `0x0060F700` | the 1.0 constant, see below |
| 14, 15 | `0x00600000` | zero, as before |

The `0x60` in byte 2 is `r3.lo`, primed 0.375, and marks that the prime arrived.
`none`, a variant with no MFU word, returns the primed `0x00600040`, and
`noprime-src0` returns zero, so the non-zero src rows are products rather than
surviving row state.

Source 13 is the `#1` constant: `const13-times-src0` (`mul0: r2, src13, src0`)
returns `0x00600010`, the src0 value unchanged, so it multiplies as one. The
product 1.0 x 1.0 itself (`src-13`) reads `0x0060F700`, i.e. `r2.lo` = `0x00`
and `r2.hi` = `0xF7`, so the product's 16 bits are `0xF700` rather than a byte
payload: on the linear scale the primes follow, 0.875 already stores as 0xDF, so
1.0 would be 0xFF in `r2.lo`. That is 12.7's fp20 readback rather than an
unexplained value: the multiplier writes fp20, whose readback byte is
`8*exponent + top 3 mantissa`, and 1.0 reads as `F7` with residue `00`. The
primes are fx10 writes (`.l*`), which is why they show up in the low byte as
`0x10`..`0x60`.

**Every pair of row registers multiplies.** Two row-register operands appeared
to read zero in the first sweep. That was the prime format, not the source
fields: `.l*` writes fx10 and the multiplier reads the same 16 bits as fp20,
where `0x0010` (0.0625 as fx10) is 2^-31. Two such primes multiply to about
2^-60, below the floor of the format. Primed `.lh` instead, the whole 4 x 4
table multiplies, and where the destination sits - on one operand or on neither
- changes nothing:

| first / second | r0 0.0625 | r1 0.125 | r2 0.25 | r3 0.375 |
|:--|:--|:--|:--|:--|
| **r0** 0.0625 | `0xB7` | `0xBF` | `0xC7` | `0xCB` |
| **r1** 0.125 | `0xBF` | `0xC7` | `0xCF` | `0xD3` |
| **r2** 0.25 | `0xC7` | `0xCF` | `0xD7` | `0xDB` |
| **r3** 0.375 | `0xCB` | `0xD3` | `0xDB` | `0xE0` |

Each cell is the fp20 byte of the product, to within the readback's three
mantissa bits (one step is 12.5% in value): 0.0625 x 0.125 = 0.0078 = 2^-7
lands exactly on `0xBF`, and 0.375 x 0.375 = 0.1406 reads `0xE0` (0.136). The
`.l*` twins of the same 16 programs return `0x0000` in the destination every
time, which is that underflow and not a refused combination. Measured on the
Toshiba AC100 with `--set matrix` of tests/grate/asm/make_mfu_selector.py.

**No pair of source classes is refused.** A row against the SFU result, the SFU
result against itself, a row against a coefficient, a coefficient against the
other coefficient and 13 against a coefficient all multiply; the coefficient
cases saturate, since an unscaled coefficient is at least 2.0. The two
multipliers are independent - separate source and destination fields, two
products in one word - and when both name the same row register the value of
`mul0` is the one left behind.

**Both source fields select row registers.** With 13 in the other slot,
`mul0: r2, src13, srcN` and `mul0: r2, srcN, src13` return the same word for
every row N (0x10, 0x20, 0x40, 0x60 for r0..r3), so the two fields are
symmetric and the 1.0 constant works in either.

**Source 10 is the SFU result, from the same word.** One MFU word holds an sfu
sub-instruction and both multipliers, so `sfu: rcp r3 mul0: r2, src10, #1`
returns 0xFF00FF00: `r3` = 0xFF00 is what the SFU wrote and `r2` = 0xFF00 is what
the multiply returned, while the same multiply without the sfu sub-instruction
(`nosfu-src10`) returns 0x00600000. Naming a different register for the SFU
(`sfu: rcp r1 ...`) returns 0x0060FF00 - `r3` keeps its 0x60 prime and `r2` is
still 0xFF00 - so src10 is SFU state, not the content of a row register.
`sfu: rcp r3 mul0: r2, src0, #1` returns 0xFF000010, so an sfu
sub-instruction in the word does not disturb a multiply of a row register.

**The SFU reads and writes the register it names.** `sfu: rcp r3` alone returns
0xFF000040 (`r3` rewritten, `r2` untouched) and `sfu: rcp r1` alone returns
0xFF000010, so the operation is in place, as `docs/assembler-syntax.md`
describes it (`sfu: RCP r1` means r1 = 1.0 / r1). Priming the rows as fp20
(`.lh`) makes the operand visible: `sqrt r0` of 0.0625 returns 0.25, `sqrt r2`
of 0.25 returns 0.5 and `sqrt r3` of 0.375 returns 0.61, read back as 12.7's
bytes `E7`, `EF` and `F0` - the last being 0.6124 with its mantissa truncated to
the three bits the readback resolves. An fx10 prime (`.l*`) writes only the low half and
leaves the fp20 field zero, so the unit computes on the floor of the format:
`sqrt` then reads `0x7B`, which is sqrt(2^-31) = 2^-15.5, and `rcp` saturates to
`0xFF`. Those two values are therefore not the root and reciprocal of the
primed 0.375.

**Row 4 reads the fragment's w, scaled.** Pointing the SFU at r4 and scaling the
vertex clip x, y, z and w together - which leaves the projected quad unchanged -
makes `rcp r4` move two octaves per fourfold w (`0x68`, `0x78`, `0x87` for
w = 0.25, 1, 4), while `sqrt r4` and `lg2 r4` saturate at every w and `rsq r4`
lands on 1/256 at w = 1 and 1/128 at w = 4. The operand is therefore the
reciprocal w as a number of order 2^16/w, not a fraction: r4 supplies 1/w in a
16.16-style fixed scale, so the unit's result is scaled the same way (`rcp r4`
is w/2^16). Measured on the Toshiba AC100, three repeats per variant, with the
`--set fp20` and `--set w` probes in `tests/grate/asm/make_mfu_selector.py`.

**Selectors 11 and 12 are the per-pixel weights.** Read on their own they
saturate, because they carry a 2^16 scale; multiplied by `rcp(r4)`, which is
w/2^16, they come back as coefficients in 0..1. In a quad translated by a vertex
uniform, so that the sample point moves off the corner pixel into the interior,
`src11` reads 0.84-0.92 at the corner pixel - a near-corner sample, not an exact
vertex, because the rasteriser covers that pixel with an interior fragment - and
0.60-0.65 and 0.71-0.77 at two interior points, while `src12` reads 0.002, 0.17
and 0.09. Removing the fetch from the word leaves both byte-identical, and
exchanging the two multipliers exchanges the two values with them, so these are
per-pixel state delivered with the fragment rather than anything the `ipl`
computes.

Exporting data back through the same fetch names them. One caveat first: with
`store rt1, r0, r1` the low half of the result word carries the varying's `y`
and the high half its `x` (measured with a constant varying of
`(0.25, 0.5, 0.75, 1.0)`, which reads back as `(0.5, 0.25, 0.75, 1.0)`; a
two-component export of the same constant reads identically, so this is not the
export's mask, and `store rt1, r1, r0` does not assemble - the store only
encodes the pairs `r0,r1` and `r2,r3`).

Decoded with that exchange, the interpolated colour is the weights themselves,
because the harness's vertex colours are the unit vectors `(1,0,0)`, `(0,1,0)`,
`(0,0,1)`, `(1,0,0)`: in a triangle drawn as v1, v2, v3 the colour comes back as
`(w3, w1, w2)`. Measured against it, `src11` is the barycentric weight of the
covering triangle's **first** vertex and `src12` that of its **second**, in the
index order the triangle is drawn with:

| sample | triangle | colour | selectors |
|:--|:--|:--|:--|
| near-corner, p1 | (1,2,3) | 0.04, 0.65, 0.18 | 0.65, 0.18 |
| quad centre | (1,2,3) | 0, 0.50, 0.46 | 0.50, 0.46 |
| single triangle, `s1a` | (1,2,3) | 0.42, 0.46, 0.05 | 0.46, 0.05 |
| single triangle, `s1b` | (1,2,3) | 0.42, 0.18, 0.33 | 0.18, 0.33 |
| p3 | (0,1,2) | 0.18, 0.65, - | 0.18, 0.65 |

Both triangles follow the same rule, and the (1,2,3) case is measured at four
separate sample points, two of them after the vertex shader collapses the other
triangle so that the sample has no diagonal to fall on. With all three weights
read from the colour, their sum is 0.87..0.96 across those points; each channel
is truncated to three mantissa bits, so the deficit is the size of the readback's
own step and is consistent with exact normalization without proving it.

**The selectors carry `w_i` in the denominator, and the multiplier supplies the
rest.** A probe that sets a per-vertex clip w but premultiplies the clip position
by it, so the projected triangle is identical for every w and only the weights
move, measures (`w = 1, 2, 4` at one vertex, three fixed samples):

* the ratio of the two selectors moves as 1 : 2 : 4 with that w, so
  `src11 / src12 = (lambda_1/W_1) / (lambda_2/W_2)` - the selectors are
  perspective-weighted, not screen-space, coefficients;
* `src13 x rcp(r4)` moves with `D = sum lambda_i/W_i`, reading
  `1/(65536 D)`, so row 4 holds the fragment's interpolated `1/w`;
* the coefficient `selector x rcp(r4)` is then `(lambda_i/W_i)/D`, the
  normalized perspective weight, and the interpolated varying follows the same
  quantity: at `w = 4` the scalar reads 0.545-0.59 where perspective predicts
  0.565 and screen space 0.394, and an asymmetric value set
  `(0.375, 1.5, 0.0625)` reproduces it without reference to the channel order.

An earlier probe that changed w *without* premultiplying moved the projected
triangle as well, which is why its two samples appeared to disagree; with the
projection held fixed they agree. As everywhere here the readback carries three
mantissa bits, so each prediction is a one-step bracket and exact normalization
is still not separated from a common factor on all three weights.

**The destination table is confirmed.** `mul0` destination 6 puts the value in
`r2.lo` and 7 puts it in `r3.lo`, while 4 and 5 write a register this store does
not read (`r0`, `r1`) and leave the result word unchanged. The 3-bit field is
therefore a plain row-register index 4..7 = `r0..r3`, as listed above, and 1 is
the barycentric weight.

**Bits 28..31 of the MFU word are inert.** Setting each on its own, and all four
together, leaves the result word byte-identical (`probe_mfu_unknown.sh`).

### 6.4 Interpolation

The barycentric interpolation weights are the MUL's results written to the "barycentric weight" destination register. The weight w0 (related to the first triangle vertex - the "barycentric coef 0" source register) is hardwired to the result of the mul0, the second vertex w1 ("barycentric coef 1" source register) to the mul1. Again, only destination "barycentric weight" registers are hardwired, "barycentric coef" sources are not. The third vertex weight is derived from the w0 and w1 as "1.0 - w0 - w1". The SFU operation should be set to "rcp r4".

	barycentric weight = barycentric coef * 1.0 / w

Measured: the coefficients arrive already scaled by 2^16, and r4 holds 1/w on
the same 2^16 scale, so multiplying a coefficient by the SFU's reciprocal
cancels the scale; at w = 1 the product is the coefficient itself (6.3.2). The
two coefficient selectors can be read directly with
`mul0: <dst>, src11, sfu`, and the fetch does not change them. Which w factor
the product carries (w or 1/w) is separated by a probe that premultiplies the
clip position by a per-vertex w, so the projection is identical for every w:
the ratio of the two selectors then moves as 1 : 2 : 4 with that w and
`src13 x rcp(r4)` moves with `D = sum lambda_i/W_i`, so the selectors carry
`lambda_i/W_i` and the coefficient is its normalized form (6.3.2).

**A weight does not have to be in the word that fetches, only earlier in the
batch - or in an earlier batch.** The schedule runs up to three MFU
sub-instructions in series inside a batch, and with both weights written two of
them before the fetch the interpolated colour is byte-identical to the
same-word control (`0xD300F200`), as it is with a multiply in the word between
them. A previous-batch pair also works: both weights written as 1.0 in batch 0
and fetched in batch 1 return `0x0001F701`, exactly what writing the same pair
in the fetch word returns, and 1.0 for `w0` in batch 0 with `w1` from the
multiplier returns `0x0000F701` either way. Even the blob's own weight word
repeated in both batches returns the same-word word (`0xD300F200`). Order is
what the batch enforces: a fetch in the first word with the weights in the
second reads something else (`0xF600B800`), so the fetch uses what the batch has
written before its slot, not after it.

What the program does have to do is write *both* weights somewhere. A fetch
whose `w0` or `w1` no instruction wrote interpolates with undefined state, which
is neither zero nor stable: the same `w1`-only words at two MFU slots read
`0xF300AF00` and `0xBD00AF00`, and a program that writes no weight at all reads
`0xB400BB00`. An earlier version of this section read such a word as "a weight
written one batch earlier is not used"; the comparison behind that had an
unwritten weight on both sides, and the claim is withdrawn - the missing weight
was the cause, not the position.

Destinations 1, 2 and 3 all supply the weight, from either multiplier
(6.3.1); destination 0 supplies none.

**An interpolation-disabled column supplies the provoking vertex's value.** With
`(dis)` on every link column and a varying whose per-vertex vectors are all
distinct, the stored sample is exactly the **last** vertex's vector - it matches
a reference that stores that vector directly, and the covering triangle is
(1,2,3), so vertex 0 is not a candidate. The flat value is the provoking vertex,
not the first vertex and not a blend. With a single column disabled the sample
matched no one vertex's vector, so the per-component rule is not established by
that probe.

### 6.5 Fragment w component

The w component is stored in the r4 and available to the first instruction of the scheduled MFU instructions sequence.

Measured: it is the reciprocal w on a 16.16-style fixed scale, about 2^16 / w,
and the SFU takes it as a plain number instead of a fraction. Scaling clip x, y,
z and w together leaves the projected quad where it was, but moves the operand:
`rcp r4` reads `0x68`, `0x78` and `0x87` for w = 0.25, 1 and 4, `rsq r4` reads
`0xB7` (1/256) and `0xBF` (1/128) at w = 1 and 4, and `sqrt r4` and `lg2 r4`
saturate at every w because the operand is at least 2^14. The readback resolves
eight steps per octave, so the constant is pinned to 2^16 only to within one
step.

### 6.6 Fragment z component

The z component comes in some form via r3. The fetch operation of the r3 should be set to NOP with "saturation" being enabled for r3. After the r3 has been populated, the following expansion should be performed:

	gl_FragCoord.z = 1/1000 + max(0.0, r3.low) * 1/4000 + max(0.0, r3.high) * 1/4

There is also dependency on the linker: it should perform the "magic" write to the TRAM0.w with "VEC4 select = VEC4.z".

Measured on the AR20 (note 50): `(z)` does not put position z into TRAM. With a
controlled clip z and an fp20-calibrated readback, z = 0.75 reads `0x0000` in x and
`0x2000` in y, z and w; the w column reads 0 for z = 0.5, 0.8, 0.875 and 1.0 while it
reads `0x1800`/`0x1C00`/`0x2000` for 0.25, 0.625, 0.7 and 0.75. That is not a
monotonic function of z and does not match the calibrated fp20 codes (0.875 would be
`0xF500`). The all-NOP link with `sat(NOP)` reads 0 for z = 0, 0.5, 0.75 and 1.0, so
the reconstruction above still has no measured input.

## 7. TEX


The TEX instruction takes the texture coordinates and LOD bias from the first row of the pixel packet (i.e. R0-R3 registers) and writes the sampled data to that first row as well. 

|   Bits | Meaning                               |
|-------:|:--------------------------------------|
| 14..31 | bit 14 is not a projective divide; 15..31 inert (measured) |
|     13 | explicit LOD, see below               |
|     12 | enable bias                           |
|     11 | inert (measured)                      |
|     10 | enable                                |
|   6..9 | inert (measured)                      |
|      5 | sampled data destination regs select  |
|      4 | texcoords / lod regs select           |
|   0..3 | sampler index                         |

"Inert" here means one bit at a time on a sample that is demonstrably
sensitive: the sample word `0x00001420` reads the white texel and returns
`0xFFFFFFFF`, while clearing bit 5 or setting bit 4, or moving the coordinate to
`s = 0.1`, returns `0x00000000`. With those three controls fixed, setting bit 6,
7, 8, 9, 11, 15, 16, 20, 24, 28 or 31 leaves the result byte-identical, so none
of them changes filtering, wrapping, the coordinate registers or the destination
pair. Bit 14 is not the projective divide of NV30 either: with the R coordinate
at 2.0 the result is unchanged whether bit 14 is set or clear, whereas a divide
would halve `s`. The sweep is `probe_tex_unknown.sh`.

"Inert" is scoped to what the driver can bind: `GRATE_TEX_FORMAT` accepts
`RGBA8888`, `dxt1`, `etc1` and `d16`, all as linear 2D surfaces, so nothing here
covers a cube map or a shadow sampler. Those two have no binding path in the test
driver, which is a limit of the harness rather than a statement about the bits.

**GLES2 scope correction:** cube-map sampling is required by core GLES2, so it is
an outstanding ISA characterization item, not an excluded feature. The tests
above establish only the behaviour of the available linear 2D descriptors.
Neither the observed lack of an R-coordinate effect nor the inert-bit sweep can
be generalized to cube-face selection or cube-map sampling. The current test
driver has no cube-map binding path; this is a harness limitation, not evidence
that AR20 lacks cube-map support. Concretely, `TEXTURE_DESC1`/`TEXTURE_DESC2` have
no dimension or face-enable field, `grate_create_texture` allocates one 2D surface,
and there is no cube option in the assembler (note 51).

What *is* covered is the coordinate side of the question. Feeding R from the ALU
so that it sweeps `0..1` across the frame - a different fragment programme, but
the same descriptor, output and texture - gives a frame byte-identical to the
`R = 0` run, and the descriptor sweep repeats exactly: bits 5 and 13 read zero,
bit 7 collapses the row walk, and the other twenty-two bits leave the frame
unchanged. So no descriptor field turns face selection on, and **R has no
observable effect on a sample taken through these descriptors**. The measurement
and its controls are in [[Hardware Testing]] "Cube coordinates: the third
component".

A depth surface *can* be bound (`GRATE_TEX_FORMAT=d16`, a 16-bit linear surface
filled from the CPU with four bands `0x0000`, `0x4000`, `0x8000`, `0xFFFF`), and
it samples as **zero in every component**: the whole frame reads `0x00000000`,
and so does an all-zero fill of the same surface. The RGBA path over the same
coordinates and the same fragment program returns four distinct row colours, so
the coordinates, the filter settings and the destination registers are not the
cause. Whatever the sampler does with a `D16` descriptor, it is not reading the
surface's 16-bit texels; whether some other descriptor combination reaches them
is open. The measurement is in [[Hardware Testing]] "Depth-format textures".

### 7.1 Coordinate and LOD register selection

The texture coordinate components (S, T, R) and level-of-detail bias are loaded from the row registers as fp20's.

|  Value | Meaning (S, T, R, LOD order) |
|-------:|:-----------------------------|
|      1 | R2, R3, R0, R1               |
|      0 | R0, R1, R2, R3               |

### 7.2 Bias versus explicit LOD

The fourth register from that select (R3, or R1 when the select is 1) carries a
level of detail. Which kind depends on bits 12 and 13, and they do not combine:

| bit 13 | bit 12 | meaning |
|-------:|-------:|:--------|
| 0 | 0 | LOD comes from the unit's own derivative estimate, the register is ignored |
| 0 | 1 | **bias**: the estimated LOD plus the register, clamped at 0 (what `TXB` emits) |
| 1 | 0 | **explicit**: the register *is* the LOD, no estimate involved |
| 1 | 1 | bias wins - bit 13 is ignored |

Measured on hardware with a 4x4 two-tone texture magnified 64x (estimated LOD
-6) and the register set to 2: with bit 13 clear the frame is the level-0
two-tone image, with bit 13 set and bit 12 clear it is the flat 1x1 mip
(`0x00545454`). Full table in
[writing-a-compiler.md](writing-a-compiler.md) section 10.4.

### 7.3 Destination register selection

The sampled RGBA data is stored in the two registers as four fx10's.

|  Value | Meaning |
|-------:|:--------|
|      1 | R2-R3   |
|      0 | R0-R1   |

## 8. DW


Data write instruction controls write of values contained in the row registers R0-R1 / R2-R3 to the destination render target.

Measured payload order for the R2-R3 form: the 32-bit result word is, from MSB
to LSB, **`r2.lo`, `r2.hi`, `r3.lo`, `r3.hi`** (byte 0 = `r3.hi`, byte 1 =
`r3.lo`, byte 2 = `r2.hi`, byte 3 = `r2.lo`). That was established by filling all
four slots with `0.25` (`0x40404040`) and then overwriting exactly one slot with
`0.75`, four separate runs - see section 8.1 for the table.

|   Bits | Meaning                 |
|-------:|:------------------------|
| 16..31 | 16..17 = render target class (1 = depth, 2 = other); 18..31 inert (measured) |
|     15 | source registers select |
| 11..14 | bit 14 writes a zero payload; 11..13 inert (measured) |
|     10 | stencil write           |
|   6..9 | inert (measured)        |
|   2..5 | render target index     |
|      1 | write mode bit 1 (see 0..1) |
|      0 | write mode bit 0 (1 = render target write) |

**There is no per-byte write mask in the word.** Measured with rt1 prefilled to
`0x44332211` by an earlier batch and a payload of four distinct bytes
(`0xFFBF7F40`), so that "wrote nothing" and "wrote the same value" are
different readbacks: setting bits 6..9, 18..19, 23..25 or every bit 18..31 as
*groups* leaves the whole payload written. The write mask the decompile shows in
the blob's context is not carried by the instruction. Bits 11, 12 and 13 are
inert on their own and bit 14 is not: bit 14 replaces the payload with
`0x00000000` whichever pair bit 15 names, and the store still commits - it is a
source that reads zero, not a disable. What it reads is unresolved because the
obvious candidate, the second row of row registers, cannot be populated:
`alu_buffer_size = 2` hangs the draw even for a one-`MAD` program - that program
is a single batch, so the group is incomplete, and the same hang reproduces at
size 3 with four batches (notes 28). Programs with whole groups run normally at
size 2 and 3, so the second row is reachable and bit 14's source is untested
rather than unreachable. A class-1 store with no depth surface bound and write
modes 0, 2 and 3 all leave the colour target at the prefill value.

**DW words in a group: target class and position matter (notes 29, 33, 42).**
The assembler grammar provides one DW instruction position per exec batch;
multiple ALU packets in that batch do not create additional DW positions, and
a source file with two DW lines in one batch is rejected. Thus two DW
instructions in the same packet cannot be tested with this assembler.

The earlier class-2 colour/spill/MRT measurements (notes 29, 33) found that,
for the tested distinct-target pairs, the first-batch DW wrote its target with
the group's final row contents and a competing class-2 target retained its
prior content in the cases with a distinguishable nonzero baseline. The
same-target and zero-cleared-target readbacks did not always distinguish a
dropped store from a zero write. In particular, a zero readback on RT4/RT5,
whose baseline is zero, is inconclusive.

The later position and three-word tests (note 42) sharpen that limitation.
A lone DW in a later batch returned zero on RT1, rather than RT1's nonzero
no-DW clear. The three-word target order 4/5/1 also returned zero on RT1
when RT1 was a later target; the two-RT1 sequence returned zero rather than
the clear as well. Those are changed surface contents, not proof that the
store was suppressed. Conversely, zero readbacks on RT4/RT5 cannot tell
whether those targets were left untouched or written with zero. The three-word
tests establish the first target's final row payload, but they do not establish
that all later targets were untouched.

Mixed class-1 depth and class-2 colour tests are distinguishable in both
directions and in two packet packings (notes 42): the first word's target
receives its expected group-row payload, while the second word's target is
written with zero. For depth-then-colour, depth receives its payload and colour
becomes zero; for colour-then-depth, colour receives its payload and depth
becomes zero. The depth and colour controls distinguish these zero writes from
their untouched baselines. The mechanism is unresolved: the tests do not say
whether the first store consumes a row resource or whether the subsequent
store independently reads a zeroed payload.

These results do not support a universal "first DW wins and every later DW is
dropped" rule. A single DW in the group's first batch is the measured way to
get the expected group-final payload. For a compiler, limiting output to one
DW per group remains the conservative way to obtain a predictable nonzero
store; that is a backend constraint, not a claim that the hardware
universally discards all later DWs.

### 8.1 Source register selection

Seems have no effect when the depth or stencil write enabled.

|  Value | Meaning |
|-------:|:--------|
|      1 | R2-R3   |
|      0 | R0-R1   |

**Each source register feeds one 16-bit field of the destination word, so an
`RGBA8888` pixel needs both registers.** Measured one register at a time on the
colour target (`store rt1, r2, r3`, `u0 = 0.75`, so `0.75` converts to `0xBF`):

| program | result word | reading |
|:--|--:|:--|
| `MAD r2.l*, u0, ...` | `0x000000BF` | `r2.lo` is byte 0 |
| `MAD r2.*h, u0, ...` | `0x0000BF00` | `r2.hi` is byte 1 |
| `MAD r3.l*, u0, ...` | `0x00BF0000` | `r3.lo` is byte 2 |
| `MAD r3.*h, u0, ...` | `0xBF000000` | `r3.hi` is byte 3 |

So the word is `r3.hi << 24 | r3.lo << 16 | r2.hi << 8 | r2.lo`: **`r2` supplies
the low 16 bits and `r3` the high 16**, each half landing in its own byte with
nothing left over. That is the reverse of the order an earlier revision of this
document gave, which came from a `store rt3, r2, r3` run - the pair the coverage
target drops (section 8.2) - so the coverage measurement, not the colour one,
was the one to distrust. A second earlier run that mixed `.lh` writes produced
residue bytes that masked the order completely; single-half writes remove that
noise and are the way to measure payload order.

### 8.2 Render target usage

Bits 18..31 are inert on the colour target: setting 18, 19, 20, 21, 22, 23, 24,
25, 26, 30 or 31 on its own leaves the readback at `0x4C4C4C4C`, the value the
base word stores, with no hang and no dropped write (`probe_dw_high.sh`). The
same sweep on the two other words that accept a store - the class-1 depth word
`0x00010001` and the `store stencil` word `0x00020409` - also leaves every
readback identical (`0x300C` for every depth sample, `0x7F` for every stencil
sample, `probe_dw_high_surfaces.sh`). Those bits are therefore inert on all
three store forms, not just on colour. Bits
6..9 and 11..14 behave the same way in the sweep under "DW field sweep across the
surfaces" in [[Hardware Testing]], leaving only 0..5, 10 and 15..17 with a
measured function.

**The index (bits 2..5) selects the surface; the class field (bits 16..17) only
gates the store.** The blob's discard path shows which classes it emits:

| word | index | class | surface |
|:--|--:|--:|:--|
| `0x00010001` | 0 | **1** | depth |
| `0x0002008d` | 3 | 2 | coverage |
| `0x00020409` | 2 | 2 | stencil |
| `0x00028005` | 1 | 2 | colour 0 |

Measured on hardware the class field is **partly a validity tag**: 0 and 2 always
write and 3 never does, but 1 is not "the depth class" either - it writes on
indices 0 and 1 and does not write on 2 or 3. Payload `0.75` in both halves of
all four row registers, three repeats of every cell:

| index | class 0 | class 1 | class 2 | class 3 |
|--:|:--|:--|:--|:--|
| 0 (depth) | `0x300C` | `0x300C` | `0x300C` | dropped - rasterizer `z`, `0x8000` |
| 1 (colour 0) | `0xBFBFBFBF` | `0xBFBFBFBF` | `0xBFBFBFBF` | dropped - clear colour |
| 2 (stencil) | `0x7F` | `0x00` | `0x7F` | `0x00` - dropped |
| 3 (coverage) | written | dropped | written | dropped |

So the index decides the surface and 0/2 behave alike, but class 1 is not a
type tag and not an index filter either: **it is enabled by a depth surface being
bound, and then writes whatever index it names.** `store rt3, r0, r1 class 1` on
the coverage target, varying only what else is bound
(`tests/grate/asm_behavior.sh dw_class_bind`, six cells, class 2 as the control):

| bound | `cov3` |
|:--|:--|
| coverage | `00000000` - dropped |
| coverage + depth | `007F0040` - written |
| coverage + stencil | `00000000` - dropped |
| coverage + depth + stencil | `007F0040` - written |
| coverage, class 2 (control) | `007F0040` |
| coverage + depth, class 2 (control) | `007F0040` |

So a class-1 row measured with no depth surface bound reports "dropped" on every
other target, which is how the stencil and coverage rows above should be read;
the depth target accepts class 1 because it *is* the depth datapath, and the
write still lands on the index named, so the field gates the store rather than
selecting a surface. A plain `store` emits class 2
(`0x00028001`), which is why a depth write needs the explicit suffix - `store
rt0, r0, r1 class 1` emits `0x00010001`, and `store rt0, r2, r3 class 1` emits
`0x00018001`. A second colour target is spelled as index 5 with class 2
(`store rt5`) and does receive a store - see the target-5 note below.

**A class-1 store reads its payload from the pair that bit 15 does *not* select,
and only from that pair's high register.** With `r1.lo = 1.0`, `r1.hi = 0.5` and
`r3.lo = 1.0` all live in one batch:

| word | bit 15 | pair named by bit 15 | payload actually read | depth written |
|:--|--:|:--|:--|--:|
| `0x00010001` | 0 | `r0,r1` | `r3` - `r1` ignored | `0x0010` |
| `0x00018001` | 1 | `r2,r3` | `r1` - `r3` ignored | `0x2010` |

So the register pair named by bit 15 is ignored outright for a depth store, and
the value comes from the other pair - `r3` when bit 15 is clear, `r1` when it is
set. The partner register (`r2` or `r0`) is never read. `store rt0, r0, r1 class
1` with the depth in `r3` is therefore the spelling that matches the datapath;
`store rt0, r2, r3 class 1` silently reads `r1` instead. The `D16` encoding of
that register is under "Depth payload" in section 12.

**The coverage target (index 3) only accepts one of the two source pairs.** Both
stores below come from a two-batch program whose first batch writes
`r0.lo = r2.lo = r2.hi = r3.lo = 0.5`, with a coverage target bound; `cov3` is the
raw readback of that surface:

| word | bit 15 | `cov3` readback |
|:--|--:|:--|
| `store rt3, r2, r3` (`0x00028201`) | 1 | all zero - nothing written |
| `store rt3, r0, r1` (`0x00020201`) | 0 | `0x200` per covered pixel |

`0x200` is `0.5` in the coverage format's 10-bit fixed point, so the value is the
`r0` a coverage store is expected to read; with the `r2/r3` pair selected the
store is dropped instead of reading `r2`. A coverage test that uses `store rt3,
r2, r3` therefore measures nothing at all, which is how this was found. A `DW`
store to index 3 is also suppressed by bit 12 of the `0x520` start word, like
every other store ([[GR3D Registers]] 6.1).

**The two `rt3` words quoted above cannot both be assembler output, and which
bits a mnemonic store can reach matters for every probe in this section.**
Grate lays the DW word out as a bitfield (`dw_instr` in `fragment_asm.h`):
`enable:1`, `unk_1:1`, `render_target_index:4`, `unk_6_9:4`, `stencil_write:1`,
`unk_11_14:4`, `src_regs_select:1`, `render_target_class:2`, `unk_18_31:14`.
Only the named fields are assigned by the grammar, so `store rt3, r0, r1` emits
**`0x0002000D`** and `store rt3, r2, r3` emits **`0x0002800D`** - index 3, class
2, pair select in bit 15. The `0x00020201` / `0x00028201` quoted in the table
above decode as index **0** with bit 9 set instead, so they are either a
transcription error or raw words; the `cov3 = 0x200` readback is unaffected
either way. Two consequences: **bits 6..9 and 11..14 are zero in every mnemonic
store**, so measuring them needs a raw `DW: 0x...` word; and the blob's
`0x0002008d` - which does set bit 7 - is a blob-only encoding that Grate cannot
produce, so bit 7's "set iff index 3" rule is not something the assembler's
output can confirm.

The colour target 1 accepts both pairs and simply shows the different values,
because `r1` is zero. Target 2 is different again: neither pair lands unless the
`stencil write` bit (bit 10) is set, so only the `store stencil` spelling writes
that surface.

**Target 5 (MRT) does receive a store on AR20 - the earlier retraction was an
artefact of hand-poking the target registers.** With index 5 bound and enabled
through Grate's own path (`GRATE_RT5=1` in `tools/assembler`, the same
`bind_render_target` + `enable_render_target` pair a draw uses), `DW: store rt5,
r0, r1` fills the surface and it follows the payload: `rt5 = EF00F300` for
`r0 = 0.75, r1 = 0.5` and `EF00E700` for `0.25/0.5`. A later `PSEQ: load rt5,
r0, r1` fetches exactly those words into the colour target (`F700F300` where the
re-primed pair reads `00400040`), so the slot is both written and readable.
`store rt5 ... class 1` writes nothing, exactly like class 1 on indices 2 and 3
without a depth surface. Reproduced by `tests/grate/asm_behavior.sh dw_mrt`.

The earlier negative wrote `RT_PARAMS(5)` (`0xe15`), `RT_ENABLE` and the limit by
hand from a command-stream dump and never saw a write. The register dump shows the
index-5 slot live and parameterised like the four that work
([[GR3D Registers]] 14.3), so the conclusion drawn from it was that the datapath
was missing. It is not: the difference between that program and this one is the
values in those registers, not the store. Treat "5..12 are addressable but not
implemented" as withdrawn and re-derive it from the binding rather than citing it.

**The encoding for a second colour target does exist, and the blob emits it.**
Compiling `gl_FragData[0]`/`gl_FragData[1]` to a blob program gives two `DW`
words, `0x00028005` (`store rt1, r2, r3`) and `0x00028015` (`store rt5, r2, r3`) -
`SURF_OUT0` and `SURF_OUT1` from the blob's own enum, both class 2, with the
second buffer's value re-materialised into `r6/r7` from a constant instead of
reusing `r2/r3`. The same output appears with and without
`GL_ARB_draw_buffers`, so the extension has no bearing on the encoding. The blob
is one compiler shared across chips, so this fixes the *spelling* of a second
colour target; it does not show that AR20 has the datapath, and AR20 is a GL ES
2.0 part, where a `gl_FragData` index above 0 does not exist at all.

Some of the render targets have an additional special purpose, like depth/stencil store. They are hardwired and their special purpose is active under certain conditions, like when depth/stencil test is enabled.

| Render target | Usage             |
|--------------:|:------------------|
|             0 | Depth buffer      |
|             1 | Color buffer      |
|             2 | Stencil buffer    |
|             3 | Coverage buffer (ordinary target, colour value format) |
|             4 | Spill buffer      |
|         5..12 | Encoded as MRT n+1; **not implemented on AR20** (see below) |

**Render target 3 carries no coverage - it is a scratch surface the program may
write and fetch back.** Three measurements fix its role. The rasterizer never
writes it on its own: a program that stores nothing leaves a bound `rt3`
all-zero while the colour target is filled. The program's own `rt3` store does
not reach register 74 - that reads `0` after a `0.5` store and after a `1.0`
store, in a plain full-screen quad and in a quad shrunk to half the frame, at
three read scales. And a `PSEQ` fetch of index 3 (`0x00830008`) does return the
surface into `r0/r1`, byte for byte. So AR20 has no shader-visible primitive or
sample coverage: the surface records the program's own decision, nothing in the
fixed function consumes it, and a backend can leave `rt3` alone unless it is
reproducing the blob's exact four-batch discard shape.

Register 74 is reachable as `r74`: source operands accept any hardware register
number from 16 to 75, while destinations stay limited to `r0..r15`. The zero is
thus a measured value, not missing syntax.

## 9. PSEQ


PSEQ stands for Program Sequencer. It fetches raw data from a selected render target, converts that data into FX10 [FP20(?)] format and loads it into registers.

**The fetch is a byte-exact round trip and respects the index.** The word is
`0x00800008 | (index << 16)`, so index 2 gives `0x00820008` and index 3
`0x00830008`. Storing a value to a target in one batch, fetching that same index
in a later batch of the same program, and then storing `r0/r1` to colour
reproduces the surface word exactly - including for the coverage target, which
has no other reader:

| target | surface word | fetched word |
|--:|:--|:--|
| 3, `.lh` value `0.25` | `E700E700` | `E700E700` |
| 3, `.lh` value `0.75` | `F300F300` | `F300F300` |
| 3, `.l*` value `0.25` | `00400040` | `00400040` |
| 4, `.lh` value `0.25` | `E700E700` | `E700E700` |
| 5, `.lh` value `0.25` | `E700E700` | `E700E700` |

Each run fetched the index it had written while the two other surfaces stayed
zero, so the index field selects the surface. The 8-bit round trip is exact, so
the `0xC8 -> 0xC7` seen on the stencil path is the fixed-function stencil write,
not the fetch. The program's own store is visible to a later batch's fetch in the
same draw, with one draw and with two.

|   Bits | Meaning                 |
|-------:|:------------------------|
| 24..31 | bit 24 selects the destination-byte form (9.2); 25..31 inert |
|     23 | fetch enable, required; in the bit-24 form it moves the write into `r1` |
| 20..22 | inert                                  |
| 16..19 | render target select    |
|  4..15 | inert in the fetch form; nothing even as a group in the bit-24 form |
|      3 | fetch enable, required; in the bit-24 form it writes `r0.hi` |
|      2 | in the bit-24 form it writes `r0.lo` |
|      1 | dest registers select   |
|      0 | in the bit-24 form it moves the write into `r1` |

**Both bits 3 and 23 are required for a fetch.** The blob always
sets both enable bits, which makes them look like one enable flag with a spare
bit next to it; the hardware needs the pair. Measured with a round trip that
distinguishes "fetched" from "did nothing" (see below): clearing bit 3 or
clearing bit 23 suppresses the fetch completely, setting bit 24 replaces the
fetch with the byte-write form of 9.2, and setting any of bits 20..22, 25, 26,
27, 28 or 31 on its own changes nothing - same fetched word, no hang, no side
effect on the destination registers.

Bit 24 changes what the word does rather than suppressing it: the form it
selects writes into selected bytes of the destination pair, and which bytes are
chosen by bits the render-target form uses for enables (9.2). A set bit 24 is
therefore not an error but a different instruction.

Bits 4..15 are inert in the render-target form: setting each one on its own
leaves the round trip byte-exact (`probe_pseq_bits.sh`), so the fetch has no
write mask, sub-word select or format field below the index. Those bits do not
select the byte in the bit-24 form either (9.2).

The round trip: batch 0 writes a known value to the target and to `r0/r1`; batch
1 overwrites `r0/r1` with a second known value and does not store; batch 2
fetches and stores. A working fetch stores batch 0's value, a word that does
nothing stores batch 1's value, so "nothing happened" cannot be mistaken for
"fetched zero". `probe_pseq_fetch.sh` runs the fourteen cases.

The assembler spells the fetch `PSEQ: load rtN, r0, r1` (or `r2, r3` for the
other destination pair), which emits `0x00800008 | (N << 16) | (pair << 1)`; any
other word is written in hex. The disassembler decodes the index, the
destination pair and the enable bits, and prints the word in a trailing comment
whenever a bit it does not decode is set.

### 9.1 Destination register selection

| Value | Meaning   |
|------:|:----------|
|     1 | R2 - R3   |
|     0 | R0 - R1   |

### 9.2 The bit-24 form writes selected bytes of the destination pair

Measured with the round trip above (batch 0 stores a tag to rt4, batch 1 issues
the word, batch 2 stores `r0/r1` to colour): a word with bit 24 set never
delivers any part of the tagged surface - and a render-target fetch of rt4
afterwards still returns the tag, so the word commits nothing to the surface
either. What it does instead is write into the destination pair. Bits 3, 2, 0
and 23 choose the bytes, and all sixteen combinations were run with `r0` primed
`(0x11, 0x22)` and `r1` `(0x33, 0x44)`, so the idle readback is `0x44332211` and
each write shows in place:

| bits beside 24 | word | bytes written | readback |
|:--|--:|:--|--:|
| none | `0x01000000` | none | `0x44332211` |
| 0 | `0x01000001` | none | `0x44332211` |
| 23 | `0x01800000` | none | `0x44332211` |
| 3 | `0x01000008` | `r0.hi` | `0x44330011` |
| 2 | `0x01000004` | `r0.lo` | `0x44332200` |
| 3, 2 | `0x0100000C` | `r0.lo`, `r0.hi` | `0x44330000` |
| 0, 23 | `0x01800001` | `r0.lo` | `0x44332200` |
| 2, 0 | `0x01000005` | `r1.lo` | `0x44002211` |
| 2, 23 | `0x01800004` | `r1.lo` | `0x44002211` |
| 3, 0 | `0x01000009` | `r1.hi` | `0x00332211` |
| 3, 23 | `0x01800008` | `r1.hi` | `0x00332211` |
| 2, 0, 23 | `0x01800005` | `r0.lo`, `r1.lo` | `0x44002200` |
| 3, 0, 23 | `0x01800009` | `r0.lo`, `r1.hi` | `0x00332200` |
| 3, 2, 0 | `0x0100000D` | `r1.lo`, `r1.hi` | `0x00002211` |
| 3, 2, 23 | `0x0180000C` | `r1.lo`, `r1.hi` | `0x00002211` |
| 3, 2, 0, 23 | `0x0180000D` | `r0.lo`, `r1.lo`, `r1.hi` | `0x00002200` |

Bit 3 writes `r0.hi` and bit 2 writes `r0.lo`, independently of each other. With
exactly one of bits 0 / 23 set, the same bytes of `r1` are written instead of
`r0`; with both set, the `r1` write happens *and* `r0.lo` is written as well.
The extra `r0.lo` write in the both-set rows is measured, not explained.

**Corrected.** An earlier revision of this section said the form writes `0.0`
into one or both *high* halves and that the low halves are never touched, and
reached that from eight cells that came off a template which primed `r0` and
`r1` with the same pair. `store rt1, r0, r1` reads `[r1.hi][r1.lo][r0.hi][r0.lo]`,
so with both registers alike a byte could not be attributed to a register: two
of those cells were read as high halves where the bytes are `r0.lo`, `r0.hi`
and `r0.lo`. The grid above primes all four halves differently and also covers
the combinations the earlier table generalised from, including the two bits
that the "redirect" wording treated as one selector. It is a byte map, not a
half map: ten of the sixteen rows write a low byte.

What lands in those bytes is a value the form produces rather than a constant
`0.0`: it reads back as zero in almost every draw, but a few repeats of the
cells that write a low byte read `0x50` instead, in one to five draws of
eighty depending on the run, in `r0.lo`, `r1.lo` or both. `0x50` is the tag
batch's `r2.lo` and the low byte of the stored tag, so which of the two it
comes from is not separated, and the byte is not the cell's own prime - the
write happened and landed something else. A surface read does not produce it: the surface held
a tag in every word a fetch can reach, so a read of it would have shown the tag.
The source of the value is open, and so is why the both-set rows write `r0.lo`
a second time.

Bits 4..15 are inert even as a group, so the "write mask and slot" the earlier
text predicted is not there; bit 1 suppresses the write in the two cells tried,
and no consumer of the form is established. A spill/restore sequence at a chunk
boundary is the plausible context and nothing measured here tests it.

## 10. Program container
### 10.1 Container header



The instruction words above are wrapped in a container the blob emits, and one
of its header fields is a value a backend has to compute. Dumped with
`cgc -F -H` (field names are Grate's `struct cgc_header` / `cgc_bar`):

```
fragment shader:
  type: 0x00001b5e          class of binary, constant for fragment programs
  unknown00: 0x0000000d
  size: 0x000002a8          total container size
  num_symbols: 5            attribute/uniform/varying descriptors
  bar_size: 48              size of the chunk table
  bar_offset: 0x00000160
  binary_size: 184          == the chunk length below
  binary_offset: 0x000001f0
  unknown01..04: 0           always zero in every shader seen
```

followed by `bar->chunk_count` entries, each a `{offset, length}` pair, and each
chunk starting with:

```
unknown0: 0x00000002
unknown1: 0x00000002
```

**Both words carry instruction counts:** a
one-batch shader dumps `1, 1`, a two-batch shader `2, 2`, the three-batch
`dFdx` shader `3, 3`. The stream words that follow have exactly that many
entries per unit, so this is the same number as the `0x520` count and as every
`NONINCR` length in section 5. A driver that fills these from its own batch
count is consistent with the blob; a compiler's output is just the chunk body.

`libcgc` treats the container as "one or more chunks", iterates them and
reassembles each with `fragment_shader_disassemble()`; for AR20 every blob
output seen so far is a single chunk.

**Only the second word is the batch count.** The two words coincide for
small shaders, which invites reading both as the batch count; counting the
streams of six blob outputs (`cgc -F -s -H`, one
PSEQ/TEX/DW line per batch):

| shader | `unknown0` | `unknown1` | batches | ALU words | MFU words |
|:--|--:|--:|--:|--:|--:|
| constant colour | 1 | 1 | 1 | 4 | 0 |
| one `texture2D` | 1 | 1 | 1 | 0 | 1 |
| one varying | 1 | 1 | 1 | 4 | 1 |
| `gl_FragData[0..1]` | 2 | 2 | 2 | 8 | 1 |
| `dFdx`/`dFdy` | 3 | 3 | 3 | 24 | 1 |
| nine `vec4` varyings | 3 | **12** | **12** | 32 | 12 |

So `unknown1` is the exec-batch count in all six cases, while `unknown0` follows
it only while the ALU work is dense. In the varying-heavy shader `unknown0` is 3
while there are 12 batches - and 3 is not the ALU packet count either (32 words =
8 packets), so **`unknown0` is still open**. It has to be filled consistently
with the body, but a driver that derives `unknown1` from its batch count and
leaves `unknown0` at its observed small-shader value is no worse off than the
blob until this is settled. The batch count itself is unambiguous elsewhere: the
number of entries in every schedule stream and the value in the `0x520` start
word. That count is also what the sequencer executes, so it cannot be rounded:
poking it below the real batch count drops the missing batches silently, above it
runs past the end of the program, and at 128 and beyond it hangs the engine
([[GR3D Registers]] 6.1). A dropped batch is dropped completely: its `DW` side
effects do not happen either, a stencil store included.

A varying-count sweep confirms the split and shows the shape of `unknown0`
(same shader, only the number of summed `vec4` varyings changed):

| varyings | `unknown0` | `unknown1` | batches | ALU words | MFU words |
|--:|--:|--:|--:|--:|--:|
| 2 | 2 | 2 | 2 | 4 | 2 |
| 3 | 3 | 3 | 3 | 8 | 3 |
| 4 | 4 | 4 | 4 | 12 | 4 |
| 5 | 2 | 8 | 8 | 16 | 8 |
| 6 | 3 | 9 | 9 | 24 | 8 |
| 7 | 4 | 8 | 8 | 24 | 8 |
| 8 | 4 | 12 | 12 | 32 | 11 |
| 9 | 3 | 12 | 12 | 32 | 12 |

`unknown1` matches the batch count in all fourteen shaders measured so far.

**`unknown0` is the number of 4-register row groups the chunk touches:**
`ceil((highest row register used + 1) / 4)`. Measured against the registers the
chunk's own disassembly mentions (`cgc -F -s`, ignoring the `lp` register 31):

| shader | rows touched | highest | `unknown0` | `ceil((max+1)/4)` |
|:--|:--|--:|--:|--:|
| one uniform, straight to the output | `r2, r3` | 3 | 1 | 1 |
| 2 summed `vec4` varyings | `r0..r7` | 7 | 2 | 2 |
| 3 varyings | `r0..r11` | 11 | 3 | 3 |
| 4 varyings | `r0..r15` | 15 | 4 | 4 |
| 6 varyings | `r0..r11` | 11 | 3 | 3 |
| 9 varyings | `r0..r11` | 11 | 3 | 3 |
| 8 summed `vec4` uniforms | `r2, r3, r6, r7, r10, r11` | 11 | 3 | 3 |

That fits all seven, and it explains the two things that looked arbitrary before:
the field is bounded by 4 because there are only 16 row registers, and it is not
monotonic in the varying count because the blob reuses low rows whenever it can
(6 and 9 varyings both stop at `r11`). It is **not** the `alu_buffer_size` the
driver writes to `0xe20`, and it is not the batch count; a chunk that needs
rows 8..11 reports 3 regardless of how many batches it has. A driver that
computes it from its own highest row register reproduces the blob's value.

The chunk-scaling test below is consistent with that: a shader with no varyings
can report 1 (when its whole body fits in `r0..r3`) but reports 3 as soon as its
uniform sum reaches `r11` - the row-group rule above predicts both. `unknown1`
behaves differently: it is the batch count *of its own chunk*, so a multi-chunk
program has several different values (16 `vec4` -> chunks of 3 and 4 batches,
summing to the 7 the stream contains).

### 10.2 Chunks and recirculation

This is the mechanism behind "the fragment shader split in two" and it changes
what the unit of a program is. Same shader, only the number of summed `vec4`
uniforms changed, counted with `cgc -F -s`:

| `vec4` uniforms | chunks | batches (total) | ALU words | per-chunk `unknown1` |
|--:|--:|--:|--:|:--|
| 8 | 1 | 4 | 32 | 4 |
| 16 | **2** | 7 | 60 | 3, 4 |
| 24 | **3** | 10 | 96 | 3, 3, 4 |
| 32 | **4** | 13 | 128 | 3, 3, 3, 4 |

Each `*** chunk N/M` block is a **complete program**: its own chunk header, its
own PSEQ / MFU / TEX / ALU / DW words, its own batch count, and its own render
target stores. The ALU uploads add up (`120` words for the 16-uniform shader at
"ALU words: 60" per chunk), and the driver's upload sequence in section 5 is run
**per chunk**, which is what makes 16 `vec4` legal at all: each chunk gets its own
32-word constant window (`0x820`), so the pressure per chunk stays inside the
window even though the whole shader exceeds it.

So the answer to "what happens when the uniforms do not fit" is: the blob does
not fail and does not reload mid-program, it **re-emits the program as several
independent chunks** and lets the container's chunk table carry them. The split
point is roughly eight `vec4` per chunk, i.e. one full constant window.

What a backend should do about it is a design decision, not a documented rule:
emitting a single chunk and rejecting over-budget shaders is the conservative
choice; mirroring the split is what the blob does and is the only way to accept
them. **How the chunks are chained: the fragment is recirculated.** The chunks do not
communicate through the render target. In a 4-chunk program, chunks 1, 2 and 3
contain `DW: nop` in every batch and only the **last** chunk stores, so the
earlier chunks deliberately write nothing - their results have to survive in the
pixel's own state while the *same* fragment is fed through the pipeline again.
That is the program-sequencer recirculation described in section 4.1; it is
the mechanism behind the measured uniform-heavy shader. The consequences for
a backend that mirrors the split:

* row registers, globals and the pixel packet carry **across chunks** - a value a
  later chunk reads must be in a place the next pass still sees;
* the **64-batch budget and the 64-instruction budgets apply per chunk**, not per
  shader (16 `vec4` measured 3, 5 and 1 batches over three chunks, comfortably
  inside the limit, where a single pass would have needed 9);
* the constant window is reloaded per chunk, which is the whole point of the
  split - so a chunk must not need more than 32 live scalar constants;
* only the final chunk may store colour/depth/stencil/coverage, so the store
  schedule in [[Writing a Compiler]] 2.1 applies to it alone.

**Each chunk is emitted as its own upload pass.** `cgc -F -R`, which prints the
compiler's own command stream, shows three passes for the 16-`vec4` shader, one
per chunk, and every pass starts by writing `0` to `0x500`
(`FP_PSEQ_UPLOAD_INST_BUFFER_FLUSH`, "write 0 to flush before uploading PSEQ
words") and ends with the ALU-complement writes at `0x806`. So the per-chunk words
are uploaded into the same six buffers in turn and are never resident together;
the chunk table in the container exists so the loader knows where each chunk body
starts. The stream contains no `0x520` start word - that is the driver's - so at
least one sequencer start per pass is implied.

**The chunks share the register file, and the bodies prove it.** Chunk 2 of the
same program opens with `mad r2.hl, c3, #1, r2`, accumulating onto the `r2` chunk
1 wrote, and chunk 3 converts `r0`/`r1` with `.l*` and stores them. A chunk
boundary therefore does not reset row registers, globals or the pixel packet,
which is what recirculation means operationally. The three chunk headers are
`unknown0 = 1, unknown1 = 3`, `1, 5` and `1, 1`: nine batches over three chunks,
and `unknown0` = 1 in each because no chunk leaves `r0..r3`. An earlier dump of
the same shader recorded two chunks of 3 and 4 batches for seven batches in
total, so the split is not fixed by the uniform count - the compiler's scheduler
decides it.

Not established: how the fragment survives the reload between passes. Nothing in
the container names a mechanism, and the only register that could gate it,
`0xe21` (`FP_PSEQ_DW_CFG`), is inert at `alu_buffer_size = 1` on both program
shapes measured. Whether the pixel is parked in the ALU buffer while the streams
are re-uploaded, or the driver re-issues the draw (which would need intermediate
state to survive a draw boundary), is the question a driver port has to answer
first. The same is true of the "only the last chunk stores" convention: an
earlier chunk storing to `rt1` would simply be overwritten, so the convention may
be cosmetic.

The stream **counts must all agree**: PSEQ, MFU schedule, TEX, ALU schedule,
complement and DW all have exactly one word per batch, so `n` is the batch count
everywhere. `grate_shader_disasm_fs()` treats a mismatch as "malformed", which
is a good sanity check to copy into the driver.

`ALU_BUFFER_SIZE` also needs a second magic write (`0x501`), and T114 needs the
`0x547` pair; both are already in `grate_3d_set_alu_buffer_size()`.

## 11. Performance
### 11.1 Batch-fill cost



**Known.** `GRATE_DRAW_COUNT` draws the same full-screen quad n times and
reports the wall time of the draw loop plus the readback sync. Seven shaders in
`tests/grate/asm/perf/` differ only in batch structure, and they share one fx10
epilogue that stores `0x40404040` for `u0 = 0.25`, so every run also proves the
shader itself executed. Median of three runs, 100 draws of a 256x256 quad
(65536 fragments):

| shader | batches | ALU packets | ms / draw |
|:--|--:|--:|--:|
| `fill_1b_1p` | 1 | 2 | 1.25 |
| `fill_4b_4p` | 4 | 4 | 2.01 |
| `fill_4b_12p` | 4 | 12 | 2.08 |
| `fill_12b_12p` | 12 | 12 | 4.24 |
| `fill_4b_12p_8nop` | 12 | 12 (+8 empty) | 4.29 |
| `cost_texnop_12b` | 12 | 2 | 4.25 |
| `cost_mfu_12b` | 12 | 2 | 4.31 |

Subtracting pairs (run-to-run spread is about +/-10%, so these are
order-of-magnitude terms rather than precise constants):

| term | cost per draw | per fragment |
|:--|--:|--:|
| one extra batch | 0.27 ms | 4.1 ns |
| one extra ALU packet | 0.006-0.02 ms | 0.1-0.3 ns |

> **A batch is charged whether it is full, half full or empty, and it costs the
> same whichever unit fills it.** Three comparisons say so:
>
> * `fill_4b_12p` and `fill_12b_12p` execute the *same 12 ALU packets*: 2.08 vs
>   4.24 ms/draw. The only difference is the grouping.
> * `fill_4b_12p_8nop` adds 8 batches whose ALU schedule `count` is 0 (they hold
>   nothing but a disabled TEX word): 4.29 ms/draw, indistinguishable from
>   `fill_12b_12p`'s 4.24. **`count = 0` is not cheaper than `count = 1`.**
> * `cost_texnop_12b` (11 idle batches) and `cost_mfu_12b` (11 MFU batches) land
>   at 4.25 and 4.31 against the ALU version's 4.24, all within 1.6%. The cost
>   is the batch pass itself - one PSEQ step over the fragments - not the work
>   inside it.

Consequences for the scheduler:

* packing ALU to three packets per batch is the single biggest win available,
  well ahead of any operand-level trick: a 12-packet shader is twice as fast in
  4 batches as in 12;
* splitting a batch to gain latency (say, to pass a value through the register
  file instead of an ALU latch) costs a *whole* batch, so it only pays when it
  removes at least one other batch;
* padding a short batch with NOPs is free, but *merging* two short batches is
  what saves time - which makes the 64-batch budget a harder limit than the 256
  ALU slots.

At the ~300 MHz this GPU runs at, 4.1 ns is ~1.2 cycles per fragment per batch,
about 5 cycles per fragment across four pixel pipes: a fixed per-batch pipeline
cost. That also fits the two neighbouring observations - `pseq_to_dw_exec_nb`
changes nothing about correctness (4.4 item 3), because the batch pass is a
fixed cost regardless of how the pipeline is tuned, and the blob "gives up on
large shaders" instead of recirculating them.

The clock itself is **not measurable** here: this system mounts no debugfs at
all (`/sys/kernel/debug` does not exist), there is no `clk_summary`, no
`/sys/devices/*/clocks` and no GPU clock line in `dmesg`, and the account is
non-root. So the cycle figures assume the 300 MHz T20 nominal; across the
plausible 250-333 MHz range the batch costs 1.1-1.5 cycles per fragment.

### 11.2 Cost per unit

Same harness, 13 batches each with the work in the first 12, medians of three
runs of 100 draws:

| work per batch | ms/draw |
|:--|--:|
| nothing (epilogue only) | 4.77 |
| 3 ALU packets | 4.37 |
| 1 MFU word | 4.63 |
| 3 MFU words | 10.18 |
| 3 MFU + 3 ALU | 10.10 |
| 1 TEX sample | 4.45 |
| 3 MFU + 1 TEX sample | 10.06 |

> **A batch costs the maximum over its units, not the sum.** Adding 33 ALU
> packets to an MFU-bound batch changes nothing (10.18 -> 10.10) and adding 11
> TEX samples to one changes nothing either (10.18 -> 10.06). Both hide under
> the MFU completely.

> **The MFU is the scarce resource, and only from the second word per batch.**
> One SFU word per batch is fully hidden (4.50 against 4.67 empty), two cost
> +2.59 ms/draw and three +5.64. So **every SFU op beyond the first in its batch
> costs ~3.4 ns per fragment** - about one cycle at 300 MHz, i.e. one issue
> slot.
>
> The cost is the same for every op: `rcp`, `rsq`, `lg2`, `ex2`, `sqrt`, `sin`,
> `cos`, `frc`, `preex2`, `presin` and `precos` at three words per batch all land
> between 10.02 and 10.31 ms/draw, a 1.5% spread. A `PRE`+main pair therefore
> costs two slots and neither half is cheap. An MFU word with no SFU op in it
> (`ipl` only) is free, so the pipeline that costs is the SFU, not the MFU word
> itself.
>
> Two consequences that are easy to get backwards:
>
> * **packing SFU ops together beats spreading them.** A second SFU op costs
>   ~3.4 ns/fragment while an extra batch costs ~4.1 ns/fragment, so splitting
>   three transcendentals across three batches is *slower* than keeping them in
>   one - capped, of course, at three MFU words per batch;
> * **emulating one of them in ALU is a real win.** `1 SFU + 3 ALU` measures at
>   the empty baseline (4.52), so going from two SFU words per batch to one
>   saves the whole 2.6 ms/draw, provided the replacement fits in three ALU
>   packets. What can be emulated is limited: `fract` and `exp2` have no ALU
>   equivalent at all (no floor/trunc, no bit operations), so the candidates are
>   `sin`/`cos`/`rcp`-style polynomial chains.

Resolution caveat: the ALU and TEX terms sit at the noise floor (+/-0.4
ms/draw over 100 draws), so "free" means "under 0.02 ms/draw per packet", not
exactly zero. The SFU numbers are well clear of that floor.

The per-unit shaders are generated rather than committed, because they differ
only in which body fills the first 12 of 13 batches:

```
hdr() { printf 'alu_buffer_size = 1\npseq_to_dw_exec_nb = 1\n\n.uniforms\n\t[0].l = "u0";\n\n.asm\n'; }
body() { for i in $(seq 12); do printf 'EXEC\n'; eval "$1"; printf ';\n'; done; }
mfu3() { printf '\tMFU:\tsfu: frc r3\n\tMFU:\tsfu: frc r3\n\tMFU:\tsfu: frc r3\n'; }
```

appended to the thirteen-batch epilogue used by `tests/grate/asm/perf/`; the
TEX variants take the coordinates from fp20 uniforms in batch 1 and end with a
plain `DW: store rt1, r2, r3`.


## 12. Verification against the proprietary compiler


The observations below were obtained by using NVIDIA's own compiler as an
oracle rather than by re-deriving the encoding from Grate's tables. `tools/cgc`
links against `libcgdrv.so` when the blob is available (`src/libcgc/dummy.c` is
only linked in its absence), so the emitted bits are authoritative. Raw bit
dumps come from `cgc -F -r -U`.

### 12.1 Instruction word order

For the 64-bit ALU and MFU words, the **first** 32-bit halfword printed by
`cgc -r` is the *high* half (bits 63..32) and the second is the low half
(bits 31..0), so the value is `(word0 << 32) | word1`. The 32-bit PSEQ, TEX and
DW words are a single low word.

Confirmed by `exp2()` emitting `0e400000 00000000`, which decodes to opcode 9
(PREEX2) and register 3: `preexp r3`.

### 12.2 MFU interpolation slots and precision

The opcode (54..57) and register (58..63) fields are confirmed. What the
existing tables don't capture is that the number of `var0..3` interpolation
slots consumed depends on the declared precision of the varying:

| varying declaration   | interpolation slots                              |
|:----------------------|:-------------------------------------------------|
| `mediump vec2 v`      | two fp20 slots (`t0.fp20` in var2 *and* var3)     |
| `lowp vec2 v`         | one packed fx10 slot (`t0.fx10` in var2 only)     |

So a lowp varying packs two components into a single slot while a mediump one
does not. Anything sizing TRAM rows or counting interpolation slots needs to
account for this.

The barycentric wiring is also confirmed by the same dumps: mul0 is
(dst=1 barycentric weight, src0=10 sfu result, src1=11 barycentric coef 0) and
mul1 is (dst=1, src0=10, src1=12 barycentric coef 1).

### 12.3 TEX

For `texture2D(s, uv)` the emitted word is `0x00000430` with
`sampler_index=0`, `src_regs_select=1`, `sample_dst_regs_select=1` and
`enable=1` (bit 10). Every bit in the documented unknown ranges - 6..9, 11,
14..31 - as well as the bias bit (12) was **zero**. These ranges therefore appear to be
reserved rather than carrying undocumented flags for a plain 2D fetch without
bias. Bit 13 is no longer in that set: it is explicit LOD (see the TEX section),
and the blob emits it for `textureLod`.

Note when probing samplers: `sampler_index` is the *linked* sampler slot, not
source declaration order. Declaring two samplers and using only the second still
emits `s0`, because the unused sampler is optimized away and the survivor is
renumbered.

### 12.4 DW

The render target field (bits 2..5) is confirmed, as is the hardwiring of the
special targets (rt0 depth, rt1 color, rt2 stencil). `gl_FragData[1]` compiles
to render target **5**, which confirms the `5..12 = MRT n+1` index mapping and
shows MRT outputs are offset past the fixed depth/color/stencil slots. The index
is the right one, and the slot does take the store - the earlier "hardware slot is
missing" reading came from a hand-poked binding and does not reproduce (8.2).

### 12.5 DW verified on hardware

Everything below was measured by assembling single-bit variants of a known-good
word and running them on the Tegra 20, rather than by reading compiler output.
The probe writes `r2/r3` as `(1, 0, 1, 0)`, so a correct rt1 colour store yields
`0xFF00FF00`; the clear colour reads back as `0x4C4C4C4C`; and a store whose
payload is all zero yields `0x00000000`. The textual `store rt1, r2, r3`
assembles to `0x00028005`, and injecting that word as raw data reproduces the
textual result exactly - so the field model above is confirmed end to end.

| flip        | result     | meaning |
|:------------|:-----------|:--------|
| bit 0 clear | `4C4C4C4C` | mode 0 - nothing is written |
| bits 2..5   | `4C4C4C4C` | `render target index` - the write goes to another target |
| bit 15 clear| `00000000` | `src_regs_select` works: R0-R1 (zero here) are stored |
| bit 14 set  | `00000000` | functional, but not a register-pair select (below) |
| bit 1 set   | `4C4C4C4C` | mode 3 = spill store; colour untouched |
| bits 16..17 | see below | render target class; 3 is invalid |
| 6..9, 11..13, 18..31 set | `FF00FF00` | no observable effect |

**Bit 17 is the low bit of the 16..17 class field, not an independent flag.**
Compiler output sets it on every non-depth store because that field carries `2`
for non-depth targets, and Grate's assembler hardcodes `unk_16_31 = 2`, which is
exactly that value. Setting it on an otherwise valid word changes nothing.

**Bits 1 and 0 are a mode field, not an enable.** The compiler decompile
(`docs/compiler-internals.md`) shows DW `[1:0]` is a 2-bit write mode: 1 = render
target write, 2 = spill write of one register, 3 = spill write of several. Setting
bit 1 turns the store into a *spill* store aimed at the spill buffer, so the colour
surface stays at the clear colour; it is not a disable. Bit 0 alone (mode 1) is the
ordinary render-target write, and mode 0 writes nothing. Colour readback cannot
tell modes 0, 2 and 3 apart, because all three leave the colour buffer untouched,
so this split was invisible to the bit-flip sweep and only became visible through
the decompile.

**Bits 16..17 gate the store rather than select the surface.** The compiler's
`dw_enc_src()` returns `1` when the render-target index is 0 (depth) and `2`
otherwise, at payload bits 6..7, which are DW bits 16..17, so 1 and 2 are the only
values it emits. On hardware 0 and 2 always write and 3 never writes; 1 writes on
indices 0 and 1 but drops on 2 and 3. Both source pairs, three repeats per cell,
payload `r0 = 0.25, r1 = 0.5, r2 = 0.75, r3 = 1.0` (so the stored word also names
which register landed):

| index | class 0 | class 1 | class 2 | class 3 |
|--:|:--|:--|:--|:--|
| 0 (depth) | `300C` | `300C` | `300C` | dropped - rasterizer `z` `8000` |
| 1 (colour 0), `r0,r1` | `007F0040` | `007F0040` | `007F0040` | dropped - clear colour |
| 1 (colour 0), `r2,r3` | `00FF00BF` | `00FF00BF` | `00FF00BF` | dropped - clear colour |
| 2 (stencil, with bit 10) | `7F` | `00` | `7F` | `00` - dropped |
| 3 (coverage) | `7F7F4040` | `00000000` - dropped | `7F7F4040` | dropped - all zero |

The earlier reading of this as "1 = depth, 2 = everything else, 3 invalid" is
right about 2 and 3 and wrong about 1: class 1 is accepted by the depth *and*
colour targets, which is why a colour-only single-bit sweep could only ever see
part of it - setting bit 16 alone gives 3, which does drop. One earlier colour
row also reported class 1 as untouched and did not repeat, which is why a class
column from a one-shot sweep is not worth recording.
Bit 10 (stencil write) set on an rt1 colour store changes nothing, so it only
matters for the stencil target.

**Bit 14 is functional but is not a wider `src_regs_select`.** With either
R0-R1, R2-R3, R4-R5 or R6-R7 holding the payload, setting bit 14 makes the store
write an all-zero payload. So bits 14..15 are not a straightforward pair index
over the low row registers.

Bit 7 is not a general target-class flag: the compiler sets it on the rt3 store,
but setting it on an rt1 store has no effect at all.

**The other datapaths are swept too, and their unknown bits are inert.**
`probe_dw_fields.sh` repeats the single-bit experiment on five words, locating
each DW word by diffing the raw stream against `DW: nop` rather than by decoding
a field position, and recording base word, injected word and readback in a TSV:

| target | base word |
|:--|:--|
| colour 0, `r2/r3` | `0x00028005` |
| colour 0, `r0/r1` | `0x00020005` |
| depth, `class 1` | `0x00010001` |
| stencil (late update) | `0x00020409` |
| coverage | `0x0002000D` |

That settles the coverage-word question above in the assembler's favour: the
mnemonic emits `0x0002000D` (bit 9 clear), so the `0x00020201` in the earlier
table was a transcription error.

Flipping bits 6..9, 11..13 and 18..31 one at a time changes nothing on any of the
five destinations: on depth the readback stays at the shader's `0x300C`, on
stencil at the reference `0x7F`, on coverage at `0xBFBFBFBF`. Bits 18..31 are
therefore dead outside the spill modes, where they act as the slot/offset field.
Bit 14 is the one functional unknown bit outside the mode and class fields: on
colour and coverage it makes the store commit an all-zero payload, and on depth
it does nothing at all.

One stencil row is left open: flipping bit 6 printed `last: 40 7F` once, a single
sample that did not reappear in any repeat, so it is recorded as unreproduced
rather than attributed to a field.

### 12.6 Target format conversion

Each source register contributes one 16-bit field, which the render-target unit
converts to the surface's format from `RT_PARAMS.FORMAT`; on `RGBA8888` the
first register's field lands in `R`/`G` and the second's in `B`/`A`. With fx10
payloads in `r2.lo` and `r3.lo` the field's first byte is the channel and its
second byte is 0, so red and blue get the value, green and alpha are 0:

| x | RGBA8888 | RGB565 | L8 | A8 |
|--:|:--|:--|:--|:--|
| 0.25 | `0x00400040` | `0x4008` | `0x40` | `0x00` |
| 0.5  | `0x007F007F` | `0x780F` | `0x7F` | `0x00` |
| 0.75 | `0x00BF00BF` | `0xB817` | `0xBF` | `0x00` |
| 1.0  | `0x00FF00FF` | `0xF81F` | `0xFF` | `0x00` |

So 8-bit channels are `round(x * 255)` with ties rounding **down** (`0.5` ->
`0x7F`), 5-bit channels `round(x * 31)` (`0.5` -> 15, `0.75` -> 23), `L8` is the
red channel converted identically, and `A8` takes the alpha channel only (0
here, because nothing was written to the high half of the pair). `RGB565` packs
`R` in bits 15..11, `G` in 10..5 and `B` in 4..0.

### 12.7 Payload format

`DW` converts its payload fields from whatever format the destination left in
the register, and it only handles fx10 correctly. An fx10 destination (`.l*` /
`.*h`) gives the converted value, so the field's first byte is `round(x*255)` and
its second byte is `00`. An fp20 destination (`.lh`) does not: the field is then
a reading of the fp20 bit pattern, monotone but logarithmic, 8 steps per octave:

| x | `.lh` field bytes (`R`,`G`) | `.l*` field bytes (`R`,`G`) |
|--:|:--|:--|
| 0.0625 | `D7 00` | `10 00` |
| 0.125 | `DF 00` | `20 00` |
| 0.25 | `E7 00` | `40 00` |
| 0.375 | `EB 00` | `60 00` |
| 0.5 | `EF 00` | `7F 00` |
| 0.75 | `F3 00` | `BF 00` |
| 1.0 | `F7 00` | `FF 00` |
| 1.5 | `FB 00` | `FF 00` |
| 2.0 | `FF 00` | `FF 00` |
| -0.5 | `00 00` | `00 00` |

The `.lh` byte is `8*exponent + top 3 mantissa bits`, i.e. `247 + 8*log2 x` over
the useful range, saturated at `0xFF` above `2.0` - the fp20 word taken as a
10-bit unsigned fraction rather than decoded. The second byte of an `.lh` field
is a value-dependent residue, usually `00` but measured as `CB`, `C3`, `F4` and
`FF` for mantissas `0x...CCD`, `0x...0C5`, `0x...8F6` and `0x...199A`; it is not
modelled. When an `.l*` write targets the very pair the store consumes
(`r0`/`r1`), that byte reads `02` from pixel 2 onward instead of `00` -`007F027F`
rather than `007F007F` for `0.5` - a stale-slot artefact of writing only the low
half of the registers being read. Writing `.lh`, or storing from `r2`/`r3`, keeps
it `00`. Materialise the store payload with `.l*` / `.*h`, never with `.lh`.
Both halves matter: `.l*` writes only the low fx10, so the high field keeps
whatever was there before.

This also explains the apparent value drift through a `.lh` copy chain: `0xF3` is
exactly the raw-fp20 byte for `0.75`, so the `0.953` that the second hop reads
back is that byte reappearing, not a loss of precision in fp20.

### 12.8 PSEQ

A framebuffer fetch (`GL_NV_shader_framebuffer_fetch`) compiles to
`load rt2` = `0x00820008`: bits 16..19 hold the render target index (value 2,
matching the disassembler's `rt2`), `dest_regs_select` (bit 1) is 0 meaning
R0-R1, and bits 3 and 23 are both set - consistent with the note above that they
usually travel together. No other bits are set.

### 12.9 Claims settled since earlier revisions

The points below were open in the first revision of this document. They are kept
with the evidence that closed them, because several retractions were caused by
probe bugs rather than by hardware behaviour ([[Hardware Testing]]).

- **Multiple `LINK` lines and multiple rows** - settled: the words compose, per
  component. Each `LINK` line becomes one
  word at `0x300` and the next at `0x302` (`grate-asm.c`: `1 + n*2` words, one
  `INCR(0x300, n*2)`), and a linker that writes `x` from one line and `y` from a
  second renders the same frame as a single line writing both. Rows behave the
  same way: reading `t1` with row 1 linked reproduces the frame `t0` gives with
  row 0 linked, and adding row 1 leaves row 0 untouched. The old "same-source
  control did not read equal" was a probe artefact - see the `ipl` note below.
- why rC reads as zero for a uniform beside a row-register rA/rB -
  **settled: it does not, and there is no operand-port restriction.** See the
  note under "Operands (rA, rB, rC)" above; the three arrangements (row rA with
  uniform rC, uniform rA with row rC, and no row operand at all) all return
  `0.75` for `1*0.5 + 0.25`.
- `ipl` results cannot be read back through an ALU operand - **retracted: they
  can, with a plain fp20 read.** Slot 0 lands in `r0`, slot 1 in `r1`, a leading
  `NOP` slot leaves its register alone, and `MAD rX.l*, r0, #1, #0, #1` returns
  the interpolated value exactly. Measured with a vertex shader that exports a
  *constant* varying from a uniform, so the read has a value it can be right
  about (`k` is the varying, read from `r0`/`r1` and stored to `rt1`):

  | varying `k` | stored word (`r2` from `r0`, `r3` from `r1`) |
  |:--|:--|
  | (0.5, 0.25) | `0x0040007F` - `r2` = `0x7F`, `r3` = `0x40` |
  | (0.25, 0.75) | `0x00BF0040` |
  | (1.0, 0.0) | `0x000000FF` |
  | (0.125, 0.5) | `0x007F0020` |

  Every stored value is the constant itself, and the TEX consumer of the same
  slots fetches the matching texel (`k.t = 0.25` reads row 1 of a 4x4
  row-coloured texture, which is green). The `.h` form reads the same value in
  fx10 form (`0x00E700EF` for 0.5/0.25) and the **`.l` form reads zero** - which
  is what made the earlier probes look like leftover content, because they read
  the low half. The old contradictions (identical words for every slot pair, two
  runs disagreeing) were artefacts of that read form. So `ipl` output is ordinary
  fp20 data and a compiler may do arithmetic on varyings directly; there is no
  need to launder them through the texture unit.
- fp20 rounding of ALU results - **settled: the ALU truncates toward zero**
  at fp20 writeback (1.5 ulp reads back as 1 ulp), the same as the assembler's
  own packing. See [[Writing a Compiler]] 9.2 item 8. Historical note: the
  amplification probe (`4096*(x-1)`) is what settles it: 1.5 ulp reads back as
  1 ulp, so writes truncate.
- `PRE` intermediate lifetime - **settled**: at least four intervening
  batches, per operand register, undisturbed by unrelated SFU ops. See
  section 6.1.
- fp20 exponent extremes - **settled**: values clamp at `2^33` and `ex2`
  underflows at `-31`, so the usable window is about `2^-31 .. 2^33`. Same
  reference.
- what DW bit 14 does - **settled**: with the payload in any register pair,
  setting bit 14 makes the store write an all-zero payload (`0x007F007F` ->
  `0x00000000` against a `0x4C4C4C4C` clear). Re-measured from a word the
  assembler itself emits, so the bit position is not in doubt. Bits 14 and 15 are
  independent: bit 15 selects the pair (set = `r2/r3`, clear = `r0/r1`, which is
  what the assembler emits for each spelling), and bits 16..17 are the target
  class where `3` is invalid and writes nothing.
- where a DW store points - **measured with a `D16` depth surface bound**
  (`GRATE_DEPTH=1` in `tools/assembler`, see [[Hardware Testing]]): the **render
  target index (bits 2..5) is the selector**, not the class field. Index 0 is the
  depth surface (because `bind_depth_buffer` puts it there), index 1 is the colour
  target and index 2 is the stencil surface; class 2 is the normal value and class
  3 is invalid (writes nothing). Classes 0 and 1 are *also* accepted for index 0,
  which is what made the class field look like a depth selector at first - it is
  not, the index is. The rasterizer writes depth on its own, so a shader with no
  `DW` still leaves the depth surface at the interpolated `z` (`0x8000` for a
  full-screen quad at `z = 0.5`) while the colour target keeps the clear value:
  depth writes are not gated by the shader's store.
- **Depth payload: one register, scaled, and it is not the register the pair
  field names.** The `D16` value comes from the two fx10 halves of the *other*
  pair's high register (section 8.2): `r3` with `store rt0, r0, r1 class 1`,
  `r1` with `store rt0, r2, r3 class 1`. Low half gives `value * 0x10`
  (`0.125`->`0x0002`, `0.25`->`0x0004`, `0.5`->`0x0008`, `1.0`->`0x0010`); high
  half gives `value * 0x4000` (`0.25`->`0x1000`, `0.5`->`0x2000`,
  `1.0`->`0x4000`); the two superpose (`lo = 1.0` with `hi = 0.5` gives
  `0x2010`). Re-measured through the mnemonic: `store rt0, r0, r1 class 1` with
  `r3.lo`/`r3.hi` at `1.0`/`0.5` reads back `0x2010`, `0.25`/`0` reads `0x0004`
  and `0`/`0.25` reads `0x1000`. So even with both halves at 1.0 a shader
  reaches only `0x4010` of the `D16` range - this is not a plain depth write and
  the payload has to be pre-scaled deliberately.
- **The stencil surface is updated by `DW: store stencil` once the program is
  discard-capable.** With an `S8` surface bound and the stencil test enabled
  (`ALWAYS`, `keep/keep/REPLACE`): a program that never writes the kill register
  gets the update from the fixed-function stage (ref `0x7F` reads back `7F`, ref
  `0xC8` reads back `C8`); a program that *does* write the kill register - even
  with a constant zero, so nothing is actually discarded - gets no
  fixed-function update at all, and it is the `DW: store stencil` word
  (`0x00020409`) that performs it. The same program with `store rt1` instead
  leaves the surface at `00`, which is the control that isolates the store. The
  committed value is the test's result, so under `REPLACE` it is the reference
  value. A tiled stencil surface (`PIX_BUF_LAYOUT_TILED_16x16`) behaves
  identically to a linear one.
- the `DW` stencil store writes nothing - **retracted: it is the mechanism.**
  The earlier negative came from two configurations that cannot show a commit:
  the stencil test disabled (there is no update to commit) and a program that
  discards every fragment (no fragment survives to commit it). The isolating
  probe is a kill write of a *constant zero* with the test enabled: `store
  stencil` then leaves the surface at the reference value and `store rt1` leaves
  it at `00`. Note that a zero kill value is enough - the switch is the program
  being discard-capable, not a fragment actually dying.
- **The stencil path, measured end to end on `rt2`.** The `PSEQ` fetch
  `0x00820008` reads the surface into `r0/r1`, normalised (ref `0xC8` -> `0xC7`
  through an `RGBA8888` store, `0x7F` -> `0x7F`); with the test enabled and
  `REPLACE`, a program without a kill write gets the reference value from the
  fixed-function stage, and a program with one gets it only if it ends in `DW:
  store stencil`. Test disabled -> nothing is committed; every fragment discarded
  -> nothing is committed. Depth is unaffected and is written early for every
  fragment. The blob's four-batch discard shape replays end to end in this
  harness, including the coverage target at `rt3`.
- **The `PSEQ` fetch supplies the "old" value the stencil op works from.** With
  the test enabled and `INCR` selected, five draws of a program that ends in `DW:
  store stencil` leave the surface at `05` when the program fetches `rt2` first,
  and at a position-shaped `01 .. 80` ramp when it does not - un-fetched `r0/r1`
  do not hold the surface value, so the op computes from whatever is there.
  `KEEP` shows the same split (with the fetch: nothing changes, which is correct;
  without it: the same position-shaped values). `REPLACE` is indifferent because
  it ignores the old value, which is why probes that used `REPLACE` could not see
  any of this. The store therefore commits `op(old, ref, mask)` with `old` in
  `r0/r1`, and that is why the blob's discard path is `PSEQ: 0x00820008` followed
  by `DW: store stencil` rather than the store alone.
  So a backend should do stencil updates through the stencil ops (ref +
  `REPLACE`) and ignore the `DW` stencil store on AR20.
- **The spill destination is render target 4, base register `0xe04`.** The blob
  names its surfaces in index order - `SURF_DEPTH` 0, `SURF_OUT0` 1,
  `SURF_STENCIL` 2, `SURF_COVERAGE` 3, **`SURF_REGSPILL` 4**, `SURF_OUT1` 5 - so
  the spill surface is target 4 like the table above says. ardrv
  (`NvArdStreamBeginAndPushRelocs`) then pushes, for every valid surface,
  `INCR(0xe00 + i, 1)` followed by a *relocated* address, plus a companion
  `INCR(0xe30 + i, 1)` holding the end of the surface when its context flag says
  so. So the binding is `0x903` bit 4 (enable), `0xe04` (address) and `0xe34`
  (limit) - and Grate only ever wrote the first two. `grate-3d.c` now pushes the
  limit under `GRATE_RT_LIMIT=1`.

  The store is **inert while its selection field is zero**: DW bits 10..16 are
  the spill slot/selection, so a spill word with them clear writes nothing
  anywhere. Measured with an `RGBA8888` surface bound at index 4 and a `0.75`
  payload:

  | DW word | mode | writes into rt4 |
  |:--|:--|:--|
  | `0x00028011` | 1 (colour, control) | `0x00BF00BF` at word 0, all 65536 words |
  | `0x00028012` | 2, selection 0 | nothing |
  | `0x00028013` | 3, selection 0 | nothing |
  | `0x00028403` | 3, bit 10 | `0x0000007B` at word 0, 38 words |
  | `0x00028803` | 3, bit 11 | `0x00FE0000` at word 0, 38 words |
  | `0x0002C003` | 3, bit 14 | `0x000000C0` at word 2, 38 words |
  | `0x00038003` | 3, bit 16 | `0x000000C0` at word 3, 38 words |
  | `0x00029002` | 2, bit 12 | 152 words from word 1280 |
  | `0x0002A002` | 2, bit 13 | 152 words from word 1280 |

  Mode 1 at the same index is the control that proves the base binding, the
  enable bit and the readback are sound.

  Re-running the slot rows through the behaviour runner changed three of them, and
  the reason matters more than the rows: a spill surface starts at zero, so
  `nz = 0` says "no *nonzero* data anywhere", not "no write". Filling the surface
  with a nonzero word in an earlier batch (`dw_spill` prefill rows) gives:

  | word | after prefill + word | reading |
  |:--|:--|:--|
  | `0x00028012` mode 2 slot 0 | `EF00F300` untouched | no write |
  | `0x00029002` mode 2 bit 12 | `EF00F300` untouched | no write (also with `0xe34` programmed) |
  | `0x00028403` mode 3 bit 10 | `EF000000` | low half written with **zero** |
  | `0x00028803` mode 3 bit 11 | `00F4F300` | high half written |
  | `0x00028C03` mode 3 bits 10+11 | `00F40000` | both halves |

  So bits 10/11 are the two halves of one spill word after all, but bit 10 commits
  zero for an fp20 payload - which is why a zero-based readout saw nothing, and why
  the `0x0000007B` above is leftover rather than a value. The two halves then
  turned out to hold *different representations of the same register*, which the
  sweep below separates. The `152 words from word 1280` row also reproduced, once
  it was measured without the prefill: the prefill that makes a zero write visible
  also fills all 65536 words, so `nz` saturates and `first` becomes 0 and the row
  hides *where* the write went. Each fix here trades one blindness for another,
  which is why the payload case and the whole-surface case are kept separate.

  **The spill address is `RT_PTR(4) + offset`, and the offset comes from the DW
  word itself.** Bits 18, 19, 20, 21 and 23 each move the write by a power of
  two in 128-word steps: `+128`, `+256`, `+512`, `+1024`, `+4096` words, measured
  one bit at a time with `0xC0` in r0 (`first` in the readback). Bit 16 doubles
  the number of words written (`nz` 76 instead of 38), bit 25 collapses it to
  one, and bits 17/20..22/24/26..31 have no effect on their own. The slot field
  (bits 10..16) is not an offset in the same units: mode 3 with slot 1 writes at
  word 0, slot 2 puts `0x00FE0000` in the *high* half of the same word, slot 3
  sets both halves, and slot 4 in mode 2 writes from word 1280. So bits 10/11
  address the two 16-bit halves of a spill word, and mode 2 writes four times as
  many words as mode 3 (152 vs 38 nonzero).

  The whole surface confirms the offsets (`dw_spill_wide` dumps the entire
  65536-word buffer instead of reading `first`, so it needs no prefill and uses a
  nonzero `0xC0` payload as the marker):

  | word | mode | words written | first | last |
  |:--|:--|--:|--:|--:|
  | `0x00028011` (`store rt4`) | 1, control | 65536 | 0 | 65535 |
  | `0x00028403` | 3, bit 10 | 38 | 0 | 148 |
  | `0x00068403` | 3, bit 10 + 18 | 38 | 128 | 276 |
  | `0x000A8403` | 3, bit 10 + 19 | 38 | 256 | 404 |
  | `0x00228403` | 3, bit 10 + 21 | 38 | 1024 | 1172 |
  | `0x00029002` | 2, bit 12 | 152 | 1280 | 1431 |
  | `0x00028012` | 2, bit 4 | 0 | - | - |

  38 words spanning `0..148` is only consistent with a stride of four, so a
  mode-3 spill writes one word every four from its base; bit 18 adds 128 words and
  bit 19 adds 256. Mode 2 bit 12 writes **152 contiguous words from word 1280**,
  four times as much data, which is the "spill write of several" reading - and it
  means the earlier "did not reproduce" verdict was a readout artefact, not a
  hardware difference. Mode 2 with bit 4 set writes nothing, so the "slot 0 writes
  nothing" row holds.

  **The stride of four is the slot field.** Bits 10..17 index the half-words of
  the 4-word packet - one packet word per row register - and a mode-3 spill
  fills exactly one half-word of it. Measured with `r0..r3 = 0.25, 0.5, 0.75,
  1.0` primed as fx10, so each packet word carries a recognisable value
  (`tests/grate/asm_behavior.sh dw_spill_slot`):

  | word | written | at | first word |
  |:--|--:|:--|:--|
  | `0x00028403` (bit 10, slot 0) | 38 | `0`, stride 4 | `0x00000040` = r0 |
  | `0x00029003` (bit 12, slot 2) | 38 | `1`, stride 4 | `0x00000080` = r1 |
  | `0x0002C003` (bit 14, slot 4) | 38 | `2`, stride 4 | `0x000000C0` = r2 |
  | `0x00038003` (bit 16, slot 6) | 38 | `3`, stride 4 | `0x00000100` = r3 |

  so bits 10/12/14/16 are the **low (fx10) halves of packet words 0..3** and
  11/13/15/17 the high halves. Two limits on that model: after an `.l*` write the
  fp20 half reads back **undefined** (`0xFA`..`0xFE`, varying per fragment), so a
  high-half value taken after an fx10 write is not a measurement; and bits 15 and
  17 did not land where the index predicts (15 wrote packet word 3's low half, 17
  wrote at `+130`), so the mapping is established for 10..14 and open above that.

  **Mode 2 replicates instead of indexing.** It writes the *same* half-word to
  all four words of the packet - 152 words as 38 contiguous 4-word groups, every
  word identical - and the half is chosen by bit 12 (low) or bit 13 (high),
  while bits 10/11 and 14..17 write nothing. It also starts at word 1280 rather
  than 0, which no other configuration does and nothing measured explains, so the
  base for that mode is recorded as unexplained rather than derived.

  **Payload: `r0`, and the two halves hold two different representations of it.**
  Sweeping the priming precision and the value with the prefill held at
  `EF00F300` (`tests/grate/asm_behavior.sh dw_spill_payload`; `wf` is that prefill
  word with the half under test replaced):

  | value | low half, fx10 `.l*` (bit 10) | high half, fp20 `.lh` (bit 11) |
  |--:|:--|:--|
  | 0.125 | `0x0020` | `0x00E0` |
  | 0.25 | `0x0040` | `0x00E8` |
  | 0.5 | `0x0080` | `0x00F0` |
  | 0.75 | `0x00C0` | `0x00F4` |
  | 1.0 | `0x0100` | `0x00F8` |
  | 1.5 | `0x0180` | `0x00FC` |
  | 2.0 | `0x01FF` - saturated | `0x0100` |

  * the **low half** is the register's **fx10** content, 8 fractional bits: no
    clamp at 1.0 (`1.5` gives `0x180`), saturating at `0x1FF` for `2.0` because
    fx10 is a 10-bit two's-complement field - `-0.25` writes `0x3C0`, which is
    `1024 - 64`. So the `24.8` payload the earlier table described is really the
    fx10 slot, and that table was measured through `.l*` priming even though it
    was recorded as `.lh`;
  * the **high half is not the fp20 word**: it is `8 * exponent + top 3 mantissa
    bits`, with the sign in bit 9. Values that separate the fields give `0.5625`
    -> `0x00F1`, `0.625` -> `0x00F2`, `1.0` -> `0x00F8`, `2.5` -> `0x0102`,
    `3.0` -> `0x0104`, `4.0` -> `0x0108`, `-0.25` -> `0x02E8`, `-0.5` ->
    `0x02F0`: exponent in bits 8..3 (bias 31, so `1.0` is 31), three mantissa
    bits in 2..0. Ten of the thirteen significand bits are gone, so even for an
    fp20 payload a spill word is a **lossy** copy of the register;
  * **a write in one precision does not update the other half.** An fp20 payload
    writes `0x0000` into the low half at every value, and an fx10 payload writes
    a *constant* into the high half - `0x00F4` while the prefill is `0.75`, and
    `0x00E8` / `0x00F8` when the prefill's `u2` is set to `0.25` / `1.0`. So the
    high half is fp20 state left by the last fp20 write, not a conversion of the
    fx10 payload, and `.l*` does not touch it.

  A spill buffer therefore holds **both format copies of the register**: a
  reduced fp20 (exponent plus three mantissa bits) in the high half and the
  fx10 value in the low half. A consumer has to know which precision produced
  the register it is spilling, because the half that write did not touch is
  stale rather than derived.
- DW modes 2/3 with the store pointed at index 0 write neither the colour nor
  the depth surface (the index is ignored for spills - they always go to target
  4). Mode 1 pointed at an **unbound** index is worse than a no-op:
  the colour target then holds a descending per-pixel ramp (`0x0000FD7F`, then
  `FD7E`, `FD7D`, ..., 16384 distinct values over 65532 pixels, four pixels per
  step) instead of a clear or shader-written colour. So a store whose target is
  not bound leaves the framebuffer in a state no shader produced - do not read it
  back, and treat any index without a surface as "no target".
- MRT stores work - **reinstated.** The retraction came from poking the target
  registers by hand. Bound and enabled through the driver's own path
  (`GRATE_RT5=1`), `store rt5, r0, r1` fills the index-5 surface, follows the
  payload (`EF00F300` for `0.75/0.5`, `EF00E700` for `0.25/0.5`), and a later
  `PSEQ: load rt5` fetches those exact words into the colour target, where the
  control storing to index 1 leaves the surface `00000000` (8.2). Class 1 on
  index 5 writes nothing. Reproduced by `tests/grate/asm_behavior.sh dw_mrt`.

- TEX bits 6..9, 11 and 14..31 - **swept and inert on hardware**: with a
  two-tone texture and `s = 0.7` (the white half), setting bits 6, 7, 8, 9, 11,
  14, 16, 20, 24, 28 and 31 one at a time leaves the sample at `0xFFFFFFFF` in
  every case, identical to the base word `0x00001420`. Readers that change the
  result are bit 12 (bias), bit 13 (explicit LOD) and bits 0..5 (sampler /
  destination / coordinate selects).
- `sampler_index` with several samplers - **settled**: with tex0 cleared to
  white and tex1 to blue, a TEX word with sampler index 0 samples white
  (`0xFFFFFFFF`) and index 1 samples blue (`0x000000FF`), so bits 0..2 select the
  sampler directly and two textures can be used in one shader.

---

## Appendix A. Patents


The following appear to cover the Tegra pipeline:

- [US8314803: "Buffering deserialized pixel data in a graphics processor unit pipeline"](https://patents.google.com/patent/US8314803)
- [US9183607: "Scoreboard cache coherence in a graphics pipeline"](https://patents.google.com/patent/US9183607)
- [US7808512: "Bounding region accumulation for graphics rendering"](https://patents.google.com/patent/US7808512)
- [US8441497: "Interpolation of vertex attributes in a graphics processor"](https://patents.google.com/patent/US8441497)

The following predate Tegra and appear to cover GoForce (by filing date):

- [US7969446: "Method for operating low power programmable processor"](https://patents.google.com/patent/US7969446)
- [US8749576: "Method and system for implementing multiple high precision and low precision interpolators for a graphics pipeline"](https://patents.google.com/patent/US8749576)
- [EP1759380: "Low power programmable processor"](https://patents.google.com/patent/EP1759380)
- [US7298375: "Arithmetic logic units in series in a graphics pipeline"](https://patents.google.com/patent/US7298375)
- [US7724263: "System and method for a universal data write unit in a 3-D graphics pipeline including generic cache memories"](https://patents.google.com/patent/US7724263)
- [EP1665165: "Pixel processing system and method"](https://patents.google.com/patent/EP1665165)
- [US8711155: "Early kill removal graphics processing system and method"](https://patents.google.com/patent/US8711155)
- [US7199799: "Interleaving of pixels for low power programmable processor"](https://patents.google.com/patent/US7199799)

