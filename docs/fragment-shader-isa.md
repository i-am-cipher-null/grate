## Overview

The Tegra fragment shader ISA is not as straight forward as [[Vertex Shader ISA]].

The fragment shader is separated into five different instruction streams:

* PSEQ - fetching data from the memory and feeding it to the pipeline as registers data or instructions
* MFU - multi-function unit, varying interpolate and special functions
* TEX - texture lookups
* ALU - arithmetic logic unit
* DW - writing to the output surface / buffers

The different units seems to be synchronized by separate timing streams.

#### Instructions flow

| Sequence: |   1  |  2  |  2  |  2  |  3  |  4  |  4  |  4  |  5 |
|----------:|:----:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:--:|
|  Stage 1: | PSEQ |     |     |     |     |     |     |     |    |
|  Stage 2: |      | MFU | MFU | MFU |     |     |     |     |    |
|  Stage 3: |      |     |     |     | TEX |     |     |     |    |
|  Stage 4: |      |     |     |     |     | ALU | ALU | ALU |    |
|  Stage 5: |      |     |     |     |     |     |     |     | DW |

## Program sequencer

Allows to execute large fragment shaders in multiple passes. This feature not used in practice, BLOB driver gives up on large shaders.

Controls recirculation of pixel over the fragment shader pipeline.

Patent: https://www.google.com/patents/US8411096<br>
Patent: https://www.google.com/patents/US8659601

## Instructions scheduling

Patent: https://www.google.com/patents/US8856499

Instructions schedule specifies the number of MFU instructions or ALU instruction packets (with an ALU packet encoding up to 4 scalar instructions, see below) executed by the respective stage, each from 0 to 3 per fragment pipeline instructions batch, with 0 being a NOP (see below).

| Bits | Meaning                           |
|-----:|:----------------------------------|
| 2..7 | Address                           |
| 0..1 | Number of instructions to execute |

If the "number of instructions to execute" is 0, then the pipeline stage is NOP, however still takes 1 clock cycle. The "address" is the number of pushed instructions before the instruction to execute, so unit[Address] ... unit[Address + Number of instructions to execute] instructions (or instruction packets) will be executed, where unit stands for MFU or ALU.

## Registers

Registers / embedded constants can either be treated as one FP20 register, or two FX10 values. Their encoding is like so:

### FP20

The FP20 format is similar to [IEEE 754 FP32](https://en.wikipedia.org/wiki/Single-precision_floating-point_format) and [IEEE 754 FP16](https://en.wikipedia.org/wiki/Half-precision_floating-point_format), but with both range and precision somewhere in the middle of the two.

|   Bits | Meaning     |
|-------:|:------------|
|     19 | Sign        |
| 13..18 | Exponent    |
|  0..12 | Significand |

This means that there's a 14 bit significand, with 13 bits explicitly stored. A 6 bit exponent gives an exponent bias of 31, with a minimum exponent value of -31, and a maximum exponent value of 32.

### FX10

The FX10 format is similar to [most signed fixed-point formats](https://en.wikipedia.org/wiki/Fixed-point_arithmetic), using 10 bits of storage, and a scaling-factor of 1/256.

This means we have a minumum value of -4.0, and a maximum value of ~3.996.

## ALU instruction word encoding

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

#### ALU buffer and pixel packet:

Patent: https://www.google.com/patents/US7710427

#### ALU embedded constants:

Patent: https://www.google.com/patents/US8775777

Unlike a regular ALU instruction, the ALU3 instruction words, constituting immediate constants, shouldn't be swapped. Constant is either one fp20 or two fx10.

|   Bits | Meaning              |
|-------:|:---------------------|
| 44..63 | Immediate constant 2 |
| 24..43 | Immediate constant 1 |
|  4..23 | Immediate constant 0 |

#### Opcodes:

| opcode | Mnemonic | Meaning            | pseudo-code                |
|-------:|:--------:|:-------------------|:---------------------------|
|      0 |    MAD   | Multiply-Add       | rA * rB + rC * rD          |
|      1 |    MIN   | Minimum            | min(rA * rB, rC * rD)      |
|      2 |    MAX   | Maximum            | max(rA * rB, rC * rD)      |
|      3 |    CMP   | Compare and select | (rA < 0) ? rB : (rC * rD)  |

#### Scale result:

| Value |  Meaning |
|------:|:---------|
|   0   | No scale |
|   1   |    x2    |
|   2   |    x4    |
|   3   |    / 2   |

#### Condition code:

The conditional operation is applied to the ALU's result, so the final result is either 0.0 (false) or 1.0 (true).

| Value | Meaning                  |
|------:|:-------------------------|
|     0 | no comparison            |
|     1 | zero                     |
|     2 | greater than zero        |
|     3 | greater or equal to zero |

#### Condition registers:

The condition register comprises two fixed10 values, 0.0 or 1.0. In order to write a value to the condition register, the destination register should be selected to the condition register 64..71. To write to the higher part of the condition register, "write high subregister" bit needs to be set; otherwise lower part will be written regardless of the "write low subregister" bit state.

    Condition register stored value = !!(ALU result)

Looks like ALU can address up to 2 source CR's in one ALU[0-3] sub-instruction.

#### Kill register:

Non-zero value written to the KILL register discards the fragment, zero keeps it alive and doesn't resurrect the killed fragment. Destination register write mask (low/high halves) is ignored.

#### Position registers:

The returned fragment position X register value is accumulated by 8192.0, fragment position Y value is negative and accumulated by 8192.0 plus the render target height - 1.

    pos.x = 8192 + frag.x
    pos.y = 8192 + (target_height - 1) - frag.y

#### Operands (rA, rB, rC):

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
* if rA or rB names a row register, **rC reads as zero when it names a uniform**.
  Immediates (`#0`, `#1`, `-#1`) are still honoured. **Correction**: those
  probes primed their row registers with half-only destination writes, which
  store a truncated fp20 (20-bit value, 16-bit halves), so the rule is
  unconfirmed and has to be re-measured. A value a later operand must read has
  to be written with `.lh`; `.*h` or `.l*` alone store a corrupted fp20 that
  reads back as ~0.0078.

#### Operand rD:

|   Bits | Meaning                      |
|-------:|:-----------------------------|
|      5 | rD selector (0 = rB, 1 = rC) |
|      4 | sub-register selector        |
|      3 | fixed10 minus one            |
|      2 | enable rD (scale rC by rD)   |
|      1 | absolute value               |
|      0 | fixed10                      |

#### Registers:

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

#### Result accumulation:

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

#### Addition disable

When bit "addition disable" is set, the Multiply-Add operation turns into two multiplies. The fx10 result of each multiply goes to the low/high subregisters of the destination register.

| write high subregister | write low subregister | destination low | destination high |
|:----------------------:|:---------------------:|:---------------:|:----------------:|
|            1           |           0           |      rC\*rD     |       rA\*rB     |
|            0           |           1           |      rA\*rB     |       rC\*rD     |

## MFU instruction word encoding

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

#### SFU semantics (measured)

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

#### var0..3:

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

#### mul0..1

| Bits  | Meaning              |
|------:|:---------------------|
| 8..10 | destination register |
|  4..7 | source register 1    |
|  0..3 | source register 0    |

##### mul0..1 destination registers:

| Value | Meaning            |
|------:|:-------------------|
|     0 | ???                |
|     1 | barycentric weight |
|  2..3 | ???                |
|     4 | row register 0     |
|     5 | row register 1     |
|     6 | row register 2     |
|     7 | row register 3     |

##### mul0..1 source registers:

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

#### Interpolation

The barycentric interpolation weights are the MUL's results written to the "barycentric weight" destination register. The weight w0 (related to the first triangle vertex - the "barycentric coef 0" source register) is hardwired to the result of the mul0, the second vertex w1 ("barycentric coef 1" source register) to the mul1. Again, only destination "barycentric weight" registers are hardwired, "barycentric coef" sources are not. The third vertex weight is derived from the w0 and w1 as "1.0 - w0 - w1". The SFU operation should be set to "rcp r4".

	barycentric weight = barycentric coef * 1.0 / w

#### Fragment w component

The w component is stored in the r4 and available to the first instruction of the scheduled MFU instructions sequence.

#### Fragment z component

The z component comes in some form via r3. The fetch operation of the r3 should be set to NOP with "saturation" being enabled for r3. After the r3 has been populated, the following expansion should be performed:

	gl_FragCoord.z = 1/1000 + max(0.0, r3.low) * 1/4000 + max(0.0, r3.high) * 1/4

There is also dependency on the linker: it should perform the "magic" write to the TRAM0.w with "VEC4 select = VEC4.z".

## TEX instruction word encoding

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

#### Texture coordinates and LOD registers select:

The texture coordinate components (S, T, R) and level-of-detail bias are loaded from the row registers as fp20's.

|  Value | Meaning (S, T, R, LOD order) |
|-------:|:-----------------------------|
|      1 | R2, R3, R0, R1               |
|      0 | R0, R1, R2, R3               |

#### LOD bias vs. explicit LOD (bits 12 and 13)

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

#### Sampled data destination registers select:

The sampled RGBA data is stored in the two registers as four fx10's.

|  Value | Meaning |
|-------:|:--------|
|      1 | R2-R3   |
|      0 | R0-R1   |

## DW instruction word encoding

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

#### Source registers select:

Seems have no effect when the depth or stencil write enabled.

|  Value | Meaning |
|-------:|:--------|
|      1 | R2-R3   |
|      0 | R0-R1   |

#### Render targets usage

Some of the render targets have an additional special purpose, like depth/stencil store. They are hardwired and their special purpose is active under certain conditions, like when depth/stencil test is enabled.

| Render target | Usage             |
|--------------:|:------------------|
|             0 | Depth buffer      |
|             1 | Color buffer      |
|             2 | Stencil buffer    |
|             3 | Coverage buffer ? |
|             4 | Spill buffer      |
|         5..12 | MRT n+1           |

## PSEQ instruction word encoding

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

#### Destination registers select:

| Value | Meaning   |
|------:|:----------|
|     1 | R2 - R3   |
|     0 | R0 - R1   |

## More patents

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

## Verification against the proprietary compiler

The observations below were obtained by using NVIDIA's own compiler as an
oracle rather than by re-deriving the encoding from Grate's tables. `tools/cgc`
links against `libcgdrv.so` when the blob is available (`src/libcgc/dummy.c` is
only linked in its absence), so the emitted bits are authoritative. Raw bit
dumps come from `cgc -F -r -U`.

#### Instruction word order (important)

For the 64-bit ALU and MFU words, the **first** 32-bit halfword printed by
`cgc -r` is the *high* half (bits 63..32) and the second is the low half
(bits 31..0), so the value is `(word0 << 32) | word1`. The 32-bit PSEQ, TEX and
DW words are a single low word.

Confirmed by `exp2()` emitting `0e400000 00000000`, which decodes to opcode 9
(PREEX2) and register 3: `preexp r3`.

#### MFU interpolation slots depend on precision

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

#### TEX

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

#### DW

The render target field (bits 2..5) is confirmed, as is the hardwiring of the
special targets (rt0 depth, rt1 color, rt2 stencil). `gl_FragData[1]` compiles
to render target **5**, which confirms the documented `5..12 = MRT n+1` mapping
and shows MRT outputs are offset past the fixed depth/color/stencil slots.

#### DW - verified by executing on the GPU

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

#### PSEQ

A framebuffer fetch (`GL_NV_shader_framebuffer_fetch`) compiles to
`load rt2` = `0x00820008`: bits 16..19 hold the render target index (value 2,
matching the disassembler's `rt2`), `dest_regs_select` (bit 1) is 0 meaning
R0-R1, and bits 3 and 23 are both set - consistent with the note above that they
usually travel together. No other bits are set.

#### Still open

- how a second `LINK` line's component mapping interacts with the first. A
two-row probe shows rows 0 and 7 hold different data, but its same-source
  control did not read equal.
- why rC reads as zero for a uniform beside a row-register rA/rB. Either the
  operand-read port allocation or an assembler encoding quirk; disassembling a
  packet that does it would separate the two.
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
- **The stencil store wrote nothing.** With an `S8` surface bound
  (`GRATE_STENCIL=1`), `DW: store stencil` (`0x00020409`, index 2 with bit 10)
  left the stencil bytes at `00` and the colour target at its clear value, and
  the same was true with bit 10 flipped, with the write pointed at index 3, and
  in all four combinations of {`kill`, no `kill`} x {stencil test off, on}
  (`GRATE_STENCIL_TEST=1`). So the obvious gate - the driver's *late* stencil
  write, which is only enabled when the shader discards (`STENCIL_WRITE_EARLY =
  !discards_fragment`) - is **ruled out**: the store does nothing either way.
  What is left: a combined depth-stencil surface (the bare `S8` buffer may have
  to be the stencil half of a packed surface at the depth slot), or a stencil
  value that does not come from the `DW` payload at all. Blocked there.
- DW modes 2/3 with the store pointed at index 0 write neither the colour nor
  the depth surface.

- TEX bits 6..9, 11 and 14..31 (zero in every sample gathered so far; bit 13 is
  explicit LOD)
- `sampler_index` behaviour with several samplers used simultaneously
