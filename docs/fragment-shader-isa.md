# Tegra 2 (AR20) 3D engine - fragment shader ISA

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


The Tegra fragment shader ISA is not as straight forward as [[Vertex Shader ISA]].

The fragment shader is separated into five different instruction streams:

* PSEQ - fetching data from the memory and feeding it to the pipeline as registers data or instructions
* MFU - multi-function unit, varying interpolate and special functions
* TEX - texture lookups
* ALU - arithmetic logic unit
* DW - writing to the output surface / buffers

The different units seems to be synchronized by separate timing streams.

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
| 74 | coverage | documented but not selectable by the assembler |
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
reads `unknown0: 1`, `unknown1: 3`. That is 64 fp20 scalars through a 32-register
window, and how it is legal is **open**: the first batch alone already reads
`c0`, `c2`, `c4`, `c6`, `c8`, `c12`, `c16`, `c20` and `c22`, so a naive
"8 vec4 and reject" rule would refuse shaders the blob happily compiles.

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

What that means for the 32-word window is still not fully settled, but the
important negative result stands: **the blob does not diagnose uniform pressure.**
Both the 8-`vec4` and the 16-`vec4` shaders emit a program that references
exactly `c0..c31` and nothing outside, and neither produced an error, so a
16-`vec4` shader either aliases slots or relies on a reload mechanism that is not
visible in the stream (`cgc -F -R` shows **no `0x820` write at all** - the blob
never uploads constants, so the driver owns the window entirely). Two practical
consequences: do not copy "16 vec4 is supported", and do not assume the `asm.h`
array sizes are a hardware limit either - they are the hand-written assembler's
convenience limit. Reject above 8 `vec4` / 32 live scalar slots until an
end-to-end hardware test says otherwise, and remember the slot map is relocatable
when you build the uniform upload.

Two structural constraints that shape it:

* row registers are *only* addressable as `r0..r3` by TEX and DW, and as
  `r0..r3` by the MFU multiplier. So texture coordinates and store payloads must
  end up in the current row, which forces copies (`r3.lh, r2, #1, #0` style
  moves) around every TEX and DW.
* `alu_buffer_size` (1..4) decides how many rows are in flight. The blob sets it
  from its scheduler; the assembler refuses anything but `ALU0..ALU3` in a
  packet, so 4 rows is the ceiling for the *register* file but the assembler
  cannot populate 8 halves in one packet (see [[Compiler Internals]]).


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

This means we have a minumum value of -4.0, and a maximum value of ~3.996.

### 3.3 Range and precision limits


The encodings of the two formats are in sections 3.1 and 3.2; this
section is about what the hardware does with them.


**Known / inferred:**

* `fp20`: sign, 6-bit exponent (bias 31), 13 stored significand bits. `0x3f`
  exponent is inf/NaN (`float_to_fp20()` maps `0xff` to `0x3f`). Range roughly
  `2^-31 .. 2^32`, 14-bit effective significand - i.e. *between* fp16 and fp32,
  but with no denormals.
* `fx10`: signed 10-bit fixed point, scale 1/256, range `[-4.0, 3.996]`,
  resolution `1/256`. The assembler converts by truncating `f * 256`, which
  independently confirms truncation-toward-zero at least in the tool; the
  hardware's own rounding on `ALU`/`TEX` results is **open** (see [[Writing a Compiler]] 9.3).
* Arithmetic is *always* one of these two; there is no fp32 anywhere.

So a NIR backend needs `nir_lower_fp64`, `nir_lower_int64`, `nir_lower_int`
(all integer ops are illegal), and a precision pass that assigns every
intermediate to fp20 or fx10. `mediump` maps naturally onto fp20 arithmetic and
`lowp` onto fx10 (two components per register), which is also why the MFU packs
a `lowp vec2` into one interpolation slot and a `mediump vec2` into two
([[Fragment Shader ISA]], "MFU interpolation slots depend on precision").


## 4. Execution model
### 4.1 Program sequencer


Allows to execute large fragment shaders in multiple passes. This feature not used in practice, BLOB driver gives up on large shaders.

Controls recirculation of pixel over the fragment shader pipeline.

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

The assembler computes `address = num_pushed_so_far - count + 1`, i.e. the
address is "the last `count` words pushed", which is why three MFU slots in the
same batch get consecutive addresses.

### 4.3.1 Per-unit constraints

* ALU packet: 4 slots, each slot is one instruction. Slot 3 can instead carry
  three embedded constants, in which case that packet has only 3 ALU ops.
* `recv`/`send` (bits 60/61) implement a cross-packet accumulator chain; the
  table in [[Fragment Shader ISA]] is the contract. `recv` overrides `rC` with
  the accumulated value.
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
|     61 | send                     |
|     60 | recv                     |
|     59 | addition disable         |
| 57..58 | scale result             |
|     56 | saturate result          |
| 54..55 | condition code           |
| 47..53 | destination register     |
|     46 | write high subregister   |
|     45 | write low subregister    |
| 32..44 | operand rA               |
| 19..31 | operand rB               |
|  6..18 | operand rC               |
|   0..5 | operand rD               |

Two independent decoders disagree about the top of the destination field, and
both readings are in use:

* the encoder reconstruction in [[Compiler Internals]] builds the destination as
  a 7-bit value `(hwreg << 1) | half` at bits 47..53, which is what makes the
  kill register (76) and the condition registers (64..71) addressable;
* Grate's `libcgc/shader.c`, which was written from the blob's own dumps, reads
  bits 47..51 as a 5-bit register, **bit 53 as "a condition code is present"**
  and bits 55:54 as the condition.

For destinations below 64 the two agree on bits 47..51 and bit 53 is clear, so
the difference only shows up for a conditional packet or a write to 64..76. One
`MAD r3.*h, ... (eq)` probe with bit 53 forced to 0 and to 1 is all that is
needed to settle it; until then, treat bit 53 as part of the destination and set
it only when the destination is >= 64.

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
between the mnemonic and the destination (`mad_le r12.hl, c0, #1, c1.l`, seen in
the blob's discard path). The two readings describe the same bit pairs with a
shifted naming: a `<= 0` test is the complement of `> 0`, so they agree on
behaviour and disagree on labels. Follow the left column (what the assembler and
all measurements use) and note that the right column is only a pretty-printer.

Bit 53 is the "condition present" flag in `libcgc/shader.c`; see the note under
the ALU word table.

### 5.6 Condition registers

The condition register comprises two fixed10 values, 0.0 or 1.0. In order to write a value to the condition register, the destination register should be selected to the condition register 64..71. To write to the higher part of the condition register, "write high subregister" bit needs to be set; otherwise lower part will be written regardless of the "write low subregister" bit state.

    Condition register stored value = !!(ALU result)

Looks like ALU can address up to 2 source CR's in one ALU[0-3] sub-instruction.

### 5.7 Kill register

Non-zero value written to the KILL register discards the fragment, zero keeps it alive and doesn't resurrect the killed fragment. Destination register write mask (low/high halves) is ignored.

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
before rewriting it. So a program that can discard must re-emit coverage, depth
and stencil itself; with the late test enabled the rasterizer's copies are gone.

### 5.8 Position registers

The returned fragment position X register value is accumulated by 8192.0, fragment position Y value is negative and accumulated by 8192.0 plus the render target height - 1.

    pos.x = 8192 + frag.x
    pos.y = 8192 + (target_height - 1) - frag.y

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
|     74 | primitive coverage      |
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

Measured on AR20 with `MAD` probes: the handoff is **pairwise and adjacent**. A
packet with `send` set (assembler `(other)`, bit 61) publishes its result to the
next packet, and a packet with `recv` (`(this)`, bit 60) uses it *in place of*
rC: `MAD r3.*h, #0, #1, 0.1` with recv returns the sender's value (0.25 and 0.5
were measured), never 0.1. Reversing the order - recv on the earlier packet -
transfers nothing, and a recv in the following batch gets no value either. Read
the table above as "the current packet's rC is overridden by the previous
packet's sent result", not as a per-slot sum into ALU0.

### 5.13 Addition disable

When bit "addition disable" is set, the Multiply-Add operation turns into two multiplies. The fx10 result of each multiply goes to the low/high subregisters of the destination register.

| write high subregister | write low subregister | destination low | destination high |
|:----------------------:|:---------------------:|:---------------:|:----------------:|
|            1           |           0           |      rC\*rD     |       rA\*rB     |
|            0           |           1           |      rA\*rB     |       rC\*rD     |

### 5.14 Pipeline and forwarding



This is the part a scheduler must get right, and it was previously unknown. All
measurements use the Grate assembler harness (section 10).

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

So the 32-bit word is, from MSB to LSB, **`r2.lo`, `r2.hi`, `r3.lo`, `r3.hi`** -
i.e. byte 0 = `r3.hi`, byte 1 = `r3.lo`, byte 2 = `r2.hi`, byte 3 = `r2.lo`.
That is the *reverse* of the guess recorded here earlier (`R = r2.low`...), which
came from a single calibration shader; four one-slot overwrites agree with each
other and with the fill, so this table is the one to use. Any earlier reading of
"byte N" in these notes should be re-checked against it.

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

**Caution on the latch names.** A direct probe in this session did *not*
reproduce the `alu0 -> ALU1` hop: reading `alu0` from ALU1 returned 0, while
reading `alu1` from ALU2 returned the value packet **ALU0** had written. The
forwarding fact - same-batch chaining through latches works - still holds, but
the assembler mnemonic `aluN` seems to be offset by one slot from the packet
whose result it names. Treat the `N` as unverified until a dedicated probe
settles the mapping.

The blob itself relies on the chain heavily: for eight summed `vec4` uniforms it
emits one packet of `mad r3.hl, c0, #1, c4` ... `mad r3.hl, c8, #1, d0`, reading
the latches by number *inside* the batch. Whatever the exact numbering is, the
`slot k writes -> slot k+1 reads` form is what a scheduler should emit.

**Second attempt, still not settled.** Priming all four slots of one packet with
four distinct uniform values and reading `alu0/alu1` (then `alu2/alu3`) from the
next packet returns `0xE300DB00` for the first pair and `0xDB00DB00` for both the
second pair and a single-value control - so the four latches do **not** carry
four distinguishable values at the point they are read, and the numbers do not
match the 8-bit calibration those uniforms would give. Either the latches are
consumed by the *issue* of the following packet (that is, they hold the in-flight
results of the packet being executed, not the retired ones) or the read is one
slot off, as above. The safe compiler rule is unchanged and does not depend on
resolving it: use `aluN` only for an immediate same-batch chain of the form
`slot k writes, slot k+1 reads`, which is what every working test shader does,
and write the chain tail to a row register.

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
previously, and wrongly, attributed to forwarding or to operand ports.

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

Original, incorrect note (kept as a warning, do not act on it): rC was measured
as reading zero when it named a uniform beside a row-register rA/rB - five rows
computed `1*1 + (-0.5)` as `1.0` instead of `0.5` - and a row register in rC was
measured as unread. Both came from priming row registers with `.l*` / `.*h`,
which store a truncated fp20 that reads back as ~0.0078, so neither operand ever
held the value the probe assumed. It is the same trap as the `0x02` readings
above, and the reason every probe in this document now writes `.lh` and reads
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


### 5.15 Arithmetic semantics



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

Both of the previously open points here are now answered elsewhere. Signed zero
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
| 28..31 | ???      |
| 21..27 | var3     |
| 14..20 | var2     |
|  7..13 | var1     |
|   0..6 | var0     |

Each 7-bit `varN` slot is `[0]` saturate, `[2:1]` opcode, `[7:3]` a **5-bit row
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

The operand is a "pixel packet" row register (`r0..r3`, fp20); `r4..r15` are not
readable by the unit, and the multipliers reject them as a source too. The output
is **full range**: `rcp(0.5)` really is 2.0 and `sqrt(4)` really is 2.0, checked
by storing both the value and half of it in one run. Only the 8-bit output
conversion saturates. Accuracy is within the fp20 format for every op measured
(see [[Writing a Compiler]] 4.5), and `PRESIN`/`PRECOS` do proper range
reduction, so arguments beyond `2pi` are as accurate as small ones.

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

#### 6.3.1 Destination registers

| Value | Meaning            |
|------:|:-------------------|
|     0 | ???                |
|     1 | barycentric weight |
|  2..3 | ???                |
|     4 | row register 0     |
|     5 | row register 1     |
|     6 | row register 2     |
|     7 | row register 3     |

#### 6.3.2 Source registers

The SFU result is evaluated first and is available to use by MUL's via "SFU result" register.

|  Value | Meaning                 |
|-------:|:------------------------|
|   0..3 | row register 0..3       |
|   4..7 | ??? (global registers?) |
|   8..9 | ???                     |
|     10 | SFU result              |
|     11 | barycentric coef 0      |
|     12 | barycentric coef 1      |
|     13 | 1.0                     |
| 14..15 | ???                     |

### 6.4 Interpolation

The barycentric interpolation weights are the MUL's results written to the "barycentric weight" destination register. The weight w0 (related to the first triangle vertex - the "barycentric coef 0" source register) is hardwired to the result of the mul0, the second vertex w1 ("barycentric coef 1" source register) to the mul1. Again, only destination "barycentric weight" registers are hardwired, "barycentric coef" sources are not. The third vertex weight is derived from the w0 and w1 as "1.0 - w0 - w1". The SFU operation should be set to "rcp r4".

	barycentric weight = barycentric coef * 1.0 / w

### 6.5 Fragment w component

The w component is stored in the r4 and available to the first instruction of the scheduled MFU instructions sequence.

### 6.6 Fragment z component

The z component comes in some form via r3. The fetch operation of the r3 should be set to NOP with "saturation" being enabled for r3. After the r3 has been populated, the following expansion should be performed:

	gl_FragCoord.z = 1/1000 + max(0.0, r3.low) * 1/4000 + max(0.0, r3.high) * 1/4

There is also dependency on the linker: it should perform the "magic" write to the TRAM0.w with "VEC4 select = VEC4.z".

## 7. TEX


The TEX instruction takes the texture coordinates and LOD bias from the first row of the pixel packet (i.e. R0-R3 registers) and writes the sampled data to that first row as well. 

|   Bits | Meaning                               |
|-------:|:--------------------------------------|
| 14..31 | ???                                   |
|     13 | explicit LOD, see below               |
|     12 | enable bias                           |
|     11 | ???                                   |
|     10 | enable                                |
|   6..9 | ???                                   |
|      5 | sampled data destination regs select  |
|      4 | texcoords / lod regs select           |
|   0..3 | sampler index                         |

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
`0.75`, four separate runs - see [[Writing a Compiler]] 4.3 for the table.

|   Bits | Meaning                 |
|-------:|:------------------------|
| 16..31 | 16..17 = render target class (1 = depth, 2 = other); 18..31 unknown |
|     15 | source registers select |
| 11..14 | ??? - bit 14 is functional, see below |
|     10 | stencil write           |
|   6..9 | ??? - no effect on hardware |
|   2..5 | render target index     |
|      1 | write mode bit 1 (see 0..1) |
|      0 | write mode bit 0 (1 = render target write) |

### 8.1 Source register selection

Seems have no effect when the depth or stencil write enabled.

|  Value | Meaning |
|-------:|:--------|
|      1 | R2-R3   |
|      0 | R0-R1   |

### 8.2 Render target usage

Some of the render targets have an additional special purpose, like depth/stencil store. They are hardwired and their special purpose is active under certain conditions, like when depth/stencil test is enabled.

| Render target | Usage             |
|--------------:|:------------------|
|             0 | Depth buffer      |
|             1 | Color buffer      |
|             2 | Stencil buffer    |
|             3 | Coverage buffer ? |
|             4 | Spill buffer      |
|         5..12 | MRT n+1           |

## 9. PSEQ


PSEQ stands for Program Sequencer. It fetches raw data from a selected render target, converts that data into FX10 [FP20(?)] format and loads it into registers.

|   Bits | Meaning                 |
|-------:|:------------------------|
| 24..31 | ???                     |
|     23 | enable (?)              |
| 20..22 | ???                     |
| 16..19 | render target select    |
|  4..15 | ???                     |
|      3 | enable (?)              |
|      2 | ???                     |
|      1 | dest registers select   |
|      0 | ???                     |

Note: bit 3 and bit 23 seems to usually be set together. It's not known which one is the actual enable-bit.

### 9.1 Destination register selection

| Value | Meaning   |
|------:|:----------|
|     1 | R2 - R3   |
|     0 | R0 - R1   |

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

**Both are the exec-batch count**, which is why they are worth knowing: a
one-batch shader dumps `1, 1`, a two-batch shader `2, 2`, the three-batch
`dFdx` shader `3, 3`. The stream words that follow have exactly that many
entries per unit, so this is the same number as the `0x520` count and as every
`NONINCR` length in section 5. A driver that fills these from its own batch
count is consistent with the blob; a compiler's output is just the chunk body.

`libcgc` treats the container as "one or more chunks", iterates them and
reassembles each with `fragment_shader_disassemble()`; for AR20 every blob
output seen so far is a single chunk.

**Correction: only the second word is the batch count.** An earlier revision of
this section recorded "both are the batch count" because the two coincide for
small shaders. Counting the streams of six blob outputs (`cgc -F -s -H`, one
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
word.

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

`unknown1` matches the batch count in all fourteen shaders measured so far. For
`unknown0` the useful observation is the **range**: it is only ever 1, 2, 3 or 4,
never larger, and it is not monotonic in either the batch count or the ALU count
(24 ALU words give 3 in one shader and 4 in another). A value that is bounded by
4 and decided by the scheduler is most plausibly the **number of row registers
in flight**, i.e. the `alu_buffer_size` the blob chose for that program (1 for a
shader with no varyings, rising to 4 when many varyings are live) - the same
quantity the driver writes to `0xe20`. That is a hypothesis rather than a
measurement: `cgc -F -R` shows the blob never writes `0xe20`/`0xe21` itself, so
there is nothing to cross-check against yet. Either way a driver may fill
`unknown0` with its own row count without breaking anything observed so far.

The chunk-scaling test below strengthens it decisively: `unknown0` is **1 for
every shader with no varyings**, whether it has 8, 16, 24 or 32 `vec4` uniforms
and 7, 10 or 13 batches, and only rises to 2..4 when varyings are interpolated.
A field that ignores uniform count and total batch count but tracks varying
pressure is the row count. `unknown1` behaves the opposite way: it is the batch
count *of its own chunk*, so a multi-chunk program has several different values
(16 `vec4` -> chunks of 3 and 4 batches, summing to the 7 the stream contains).

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
That is what the ISA reference calls program-sequencer recirculation and
dismisses as "not used in practice"; it is in fact the mechanism behind a
uniform-heavy shader. The consequences for a backend that mirrors the split:

* row registers, globals and the pixel packet carry **across chunks** - a value a
  later chunk reads must be in a place the next pass still sees;
* the **64-batch budget and the 64-instruction budgets apply per chunk**, not per
  shader (16 `vec4` gives chunks of 3 and 4 batches, comfortably inside the
  limit, where a single pass would have needed 7);
* the constant window is reloaded per chunk, which is the whole point of the
  split - so a chunk must not need more than 32 live scalar constants;
* only the final chunk may store colour/depth/stencil/coverage, so the store
  schedule in [[Writing a Compiler]] 2.1 applies to it alone.

Still unmeasured: whether the driver may submit chunks in any order (the
recirculation order is presumably the chunk-table order), and whether anything
in the hardware enforces the "only the last chunk stores" convention or whether
an earlier chunk storing to `rt1` would simply be overwritten later.

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
`enable=1` (bit 10). Every bit in the documented `???` ranges - 6..9, 11, 14..31

- as well as the bias bit (12) was **zero**. These ranges therefore appear to be
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
to render target **5**, which confirms the documented `5..12 = MRT n+1` mapping
and shows MRT outputs are offset past the fixed depth/color/stencil slots.

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

**Bits 16..17 are a render-target class field, not two flags.** The compiler's
`dw_enc_src()` returns `1` when the render-target index is 0 (depth) and `2`
otherwise, at payload bits 6..7, which are DW bits 16..17. Measured on hardware
with a colour store aimed at rt1:

| field value | compiler emits for | result at rt1 |
|:--|:--|:--|
| 0 | never | `FF00FF00` (written) |
| 1 | depth (rt index 0) | `4C4C4C4C` (colour untouched) |
| 2 | every other target | `FF00FF00` (written) |
| 3 | never | `4C4C4C4C` (colour untouched) |

So the earlier reading of this as "bit 16 kills the write, bit 17 is a don't-care"
was an artefact of single-bit flips on a 2-bit field: setting bit 16 alone
produces value 3, which is invalid, and clearing bit 17 produces value 0, which
the hardware accepts. The compiler only ever emits 1 or 2.
Bit 10 (stencil write) set on an rt1 colour store changes nothing, so it only
matters for the stencil target.

**Bit 14 is functional but is not a wider `src_regs_select`.** With either
R0-R1, R2-R3, R4-R5 or R6-R7 holding the payload, setting bit 14 makes the store
write an all-zero payload. So bits 14..15 are not a straightforward pair index
over the low row registers.

This also corrects the compiler-corpus note that used to be here: bit 7 was seen
on the rt3 store, but setting it on an rt1 store has no effect at all, so it is
not a general target-class flag.

### 12.6 Target format conversion

The store always carries four fx10 values; the render-target unit converts them
to the surface's format from `RT_PARAMS.FORMAT`. With the payload in `r2.lo` and
`r3.lo` (so red and blue get the value, green and alpha are 0):

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

`DW` reads its four payload fields as **fx10 unconditionally**, so a value
destined for a store must be in fx10 form. A `.lh` destination writes fp20, and a
store of that register writes the *bit pattern* instead of the value:
`MAD r2.lh, u0` with `u0 = 0.25` then `store rt1, r2, r3` gives `0xE700E700`,
while `MAD r2.l*, u0` gives `0x00400040`. Both halves matter: `.l*` writes only
the low fx10, so the high field keeps whatever was there before.

### 12.8 PSEQ

A framebuffer fetch (`GL_NV_shader_framebuffer_fetch`) compiles to
`load rt2` = `0x00820008`: bits 16..19 hold the render target index (value 2,
matching the disassembler's `rt2`), `dest_regs_select` (bit 1) is 0 meaning
R0-R1, and bits 3 and 23 are both set - consistent with the note above that they
usually travel together. No other bits are set.

### 12.9 Still open
  **settled: the words compose, per component.** Each `LINK` line becomes one
  word at `0x300` and the next at `0x302` (`grate-asm.c`: `1 + n*2` words, one
  `INCR(0x300, n*2)`), and a linker that writes `x` from one line and `y` from a
  second renders the same frame as a single line writing both. Rows behave the
  same way: reading `t1` with row 1 linked reproduces the frame `t0` gives with
  row 0 linked, and adding row 1 leaves row 0 untouched. The old "same-source
  control did not read equal" was a probe artefact - see the `ipl` note below.
- ~~why rC reads as zero for a uniform beside a row-register rA/rB~~ -
  **settled: it does not, and there is no operand-port restriction.** See the
  note under "Operands (rA, rB, rC)" above; the three arrangements (row rA with
  uniform rC, uniform rA with row rC, and no row operand at all) all return
  `0.75` for `1*0.5 + 0.25`.
- **`ipl` results cannot be read back through an ALU operand**, which is what
  made every earlier TRAM probe look inconsistent. The slot mapping itself is
  solid - slot 0 lands in `r0`, slot 1 in `r1`, and a leading `NOP` slot leaves
  its register alone (`MAD r2.l*, r0` shows the slot-0 value in red and 0 in
  blue, and the `NOP, t0.fp20` form shows 0 in red and the value in blue). But
  the *value* an ALU sees is not the interpolated one: with all four components
  linked, a `MAD rX.l*, rX, #1, #0, #1` readout returns the same words for every
  slot pair and the same words a linker that only writes `x` produces, and two
  runs of the same shader differ (`0xBB02BE02` in one, `0xFFFFFFFF` at pixel 0 in
  the other). An fp20 read takes the high half of the pair, which `ipl` does not
  fill, so the ALU reads leftover register content. Consume TRAM data the way
  `filter_quad_fs.txt` does - as a TEX coordinate - not through ALU arithmetic.
- ~~fp20 rounding of ALU results~~ - **settled: the ALU truncates toward zero**
  at fp20 writeback (1.5 ulp reads back as 1 ulp), the same as the assembler's
  own packing. See [[Writing a Compiler]] 9.2 item 8. Historical note: the
  amplification probe (`4096*(x-1)`) is what settles it: 1.5 ulp reads back as
  1 ulp, so writes truncate.
- ~~`PRE` intermediate lifetime~~ - **settled**: at least four intervening
  batches, per operand register, undisturbed by unrelated SFU ops. See
  [[Writing a Compiler]] 4.5.
- ~~fp20 exponent extremes~~ - **settled**: values clamp at `2^33` and `ex2`
  underflows at `-31`, so the usable window is about `2^-31 .. 2^33`. Same
  reference.
- ~~what DW bit 14 does~~ - **settled**: with the payload in any register pair,
  setting bit 14 makes the store write an all-zero payload (`0x007F007F` ->
  `0x00000000` against a `0x4C4C4C4C` clear). Re-measured from a word the
  assembler itself emits, so the bit position is not in doubt. Bits 14 and 15 are
  independent: bit 15 selects the pair (set = `r2/r3`, clear = `r0/r1`, which is
  what the assembler emits for each spelling), and bits 16..17 are the target
  class where `3` is invalid and writes nothing.
- ~~where a DW store points~~ - **measured with a `D16` depth surface bound**
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
- **Depth payload: `r3` only, and scaled.** The `D16` value is assembled from the
  two fx10 halves of `r3` and ignores `r2` completely. Low half gives
  `value * 0x10` (`0.125`->`0x0002`, `0.25`->`0x0004`, `0.5`->`0x0008`,
  `1.0`->`0x0010`); high half gives `value * 0x4000` (`0.5`->`0x2000`,
  `1.0`->`0x4000`); the two superpose (`lo = 1.0` with `hi = 0.5` gives
  `0x2010`). So even with both halves at 1.0 a shader reaches only `0x4010` of
  the `D16` range - this is not a plain depth write and the payload has to be
  pre-scaled deliberately.
- **The stencil surface is writable, but not from the `DW`.** With an `S8`
  surface bound and the stencil test enabled (`ALWAYS`, `keep/keep/REPLACE`), the
  *rasterizer* stamps the reference value into the buffer: ref `0x7F` reads back
  `7F`, ref `0xC8` reads back `C8`. So binding, the target-enable path and the
  readback are all sound - an earlier run looked negative only because the ref
  was 0 and an untouched buffer is zero too.
- ~~the `DW` stencil store~~ - **ruled out as the mechanism**. `DW: store
  stencil` (`0x00020409`, index 2 with bit 10) writes nothing in all four
  {`kill`, no `kill`} x {test off, on} combinations, with bit 10 flipped, or
  pointed at index 3 - while the ref/REPLACE path writes the same surface fine.
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

  **Payload: `r0` only, and it is `value * 256`, not the RGBA8888 conversion.**
  Priming one row register at a time and reading the spill word back:

  | r0 | with r0 primed | with r1/r2/r3 primed instead |
  |--:|:--|:--|
  | 0.0 | `0x00000000` | `0x0000007B` (r0 untouched - leftover) |
  | 0.25 | `0x00000040` | `0x0000007B` |
  | 0.5 | `0x00000080` | `0x0000007B` |
  | 0.75 | `0x000000C0` | `0x0000007B` |
  | 1.0 | `0x00000100` | `0x0000007B` |
  | 1.5 | `0x00000180` | `0x0000007B` |

  The value tracks r0 exactly through `r1`, `r2` and `r3` staying put, it is
  `x * 256` in a 32-bit word (8 fractional bits), and there is **no clamp at
  1.0** (`1.5` gives `0x180`). So a spill buffer holds 24.8 fixed-point copies
  of the *pixel packet*, not the colour payload the DW word names.
- DW modes 2/3 with the store pointed at index 0 write neither the colour nor
  the depth surface (the index is ignored for spills - they always go to target
  4). Mode 1 pointed at an **unbound** index is worse than a no-op:
  the colour target then holds a descending per-pixel ramp (`0x0000FD7F`, then
  `FD7E`, `FD7D`, ..., 16384 distinct values over 65532 pixels, four pixels per
  step) instead of a clear or shader-written colour. So a store whose target is
  not bound leaves the framebuffer in a state no shader produced - do not read it
  back, and treat any index without a surface as "no target".
- **MRT stores work.** With a linear `RGBA8888` surface bound and enabled at
  index 5, `DW: store rt5, r2, r3` writes `00400040` into it while the index-1
  colour target stays at its clear value, so index 5 is `gl_FragData[1]` and a
  shader can feed more than one target.

- ~~TEX bits 6..9, 11 and 14..31~~ - **swept and inert on hardware**: with a
  two-tone texture and `s = 0.7` (the white half), setting bits 6, 7, 8, 9, 11,
  14, 16, 20, 24, 28 and 31 one at a time leaves the sample at `0xFFFFFFFF` in
  every case, identical to the base word `0x00001420`. Readers that change the
  result are bit 12 (bias), bit 13 (explicit LOD) and bits 0..5 (sampler /
  destination / coordinate selects).
- ~~`sampler_index` with several samplers~~ - **settled**: with tex0 cleared to
  white and tex1 to blue, a TEX word with sampler index 0 samples white
  (`0xFFFFFFFF`) and index 1 samples blue (`0x000000FF`), so bits 0..2 select the
  sampler directly and two textures can be used in one shader.

---

## Appendix A. Patents


These seems to be for Tegra:

- [US8314803: "Buffering deserialized pixel data in a graphics processor unit pipeline"](https://patents.google.com/patent/US8314803)
- [US9183607: "Scoreboard cache coherence in a graphics pipeline"](https://patents.google.com/patent/US9183607)
- [US7808512: "Bounding region accumulation for graphics rendering"](https://patents.google.com/patent/US7808512)
- [US8441497: "Interpolation of vertex attributes in a graphics processor"](https://patents.google.com/patent/US8441497)

These seems to be for GoForce (based on filing dates):

- [US7969446: "Method for operating low power programmable processor"](https://patents.google.com/patent/US7969446)
- [US8749576: "Method and system for implementing multiple high precision and low precision interpolators for a graphics pipeline"](https://patents.google.com/patent/US8749576)
- [EP1759380: "Low power programmable processor"](https://patents.google.com/patent/EP1759380)
- [US7298375: "Arithmetic logic units in series in a graphics pipeline"](https://patents.google.com/patent/US7298375)
- [US7724263: "System and method for a universal data write unit in a 3-D graphics pipeline including generic cache memories"](https://patents.google.com/patent/US7724263)
- [EP1665165: "Pixel processing system and method"](https://patents.google.com/patent/EP1665165)
- [US8711155: "Early kill removal graphics processing system and method"](https://patents.google.com/patent/US8711155)
- [US7199799: "Interleaving of pixels for low power programmable processor"](https://patents.google.com/patent/US7199799)

