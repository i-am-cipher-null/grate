## Overview

The Tegra fragment shader ISA is not as straight forward as [[Vertex Shader ISA]].

The fragment shader is separated into five different instruction streams:
* PSEQ - fetching data from the memory and feeding it to the pipeline as registers data or instructions
* MFU - multi-function unit, varying interpolate and special functions
* TEX - texture lookups
* ALU - arithmetic logic unit
* DW - writing to the output surface / buffers

The different units seems to be synchronized by separate timing streams.

Measured statements below cite the experiment that established them, by section, in
`tests/grate/asm_characterize.experiments.md` (for example "notes 24"). This file is the
repository's copy of the ISA description and the one to edit. A longer version of the
same document, carrying the full evidence tables and the retraction history, is
maintained in the documentation snapshot (`docs_snap/fragment-shader-isa.md`, outside
this repository); the two have to agree on every claim both make.

#### Instructions flow

| Sequence: |   1  |  2  |  2  |  2  |  3  |  4  |  4  |  4  |  5 |
|----------:|:----:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:--:|
|  Stage 1: | PSEQ |     |     |     |     |     |     |     |    |
|  Stage 2: |      | MFU | MFU | MFU |     |     |     |     |    |
|  Stage 3: |      |     |     |     | TEX |     |     |     |    |
|  Stage 4: |      |     |     |     |     | ALU | ALU | ALU |    |
|  Stage 5: |      |     |     |     |     |     |     |     | DW |

## Program sequencer

Allows to execute large fragment shaders in multiple passes. It controls recirculation
of the pixel over the fragment shader pipeline.

The earlier reading - "not used in practice, the blob gives up on large shaders" - is
wrong: a uniform-heavy shader is emitted as several *chunks*, each a complete program
with its own batch count and its own constant window, and the fragment is fed through
the pipeline once per chunk. Chunk 2 of a 16-`vec4` shader opens by accumulating onto
the `r2` chunk 1 wrote, and only the last chunk stores, so row registers and globals
survive a chunk boundary. How the fragment is parked between passes is still not
established. Measured from the blob's own output - section 10.2 of the extended
document (`docs_snap/fragment-shader-isa.md`) for the dumps and the chunk table.

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

#### Several writers into one half

When more than one slot of a packet writes the same half-register, the results merge by
a **bitwise OR of the half's own code**, whatever format wrote it (notes 27). In fp20,
0.25 (`0xE8`) and 0.5 (`0xF0`) merge to `0xF8`, which reads back as 1.0 rather than
0.75, and three writers leave `0xFC` (1.5), which the 8-bit output conversion then
saturates. Identical values are idempotent and the order does not matter. One fx10
writer beside one fp20 writer leaves a value neither wrote (`0x42` where either alone
reads `0x40`), in both orders; that case is not explained.

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

#### var0..3:

The varying id corresponds to the TRAM component: var0 is TRAM.x, var1 is TRAM.y, var2 is TRAM.z, var3 is TRAM.w.

| Bits | Meaning                  |
|-----:|:-------------------------|
| 3..6 | TRAM index to fetch from |
| 1..2 | opcode                   |
|    0 | saturate                 |

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

#### Interpolation disable

With the link word's `(dis)` modifier set on every column, the fragment reads one
vertex's vector instead of an interpolated one, and that vertex is the **last** one of
the covering triangle: in the tested four-column case the sample matched the provoking
vertex's vector exactly (notes 26). A single disabled column did not match any one
vertex's vector, so the per-component behaviour is not established by that probe.

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
| 13..31 | ???                                   |
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

#### Sampled data destination registers select:

The sampled RGBA data is stored in the two registers as four fx10's.

|  Value | Meaning |
|-------:|:--------|
|      1 | R2-R3   |
|      0 | R0-R1   |

## DW instruction word encoding

Data write instruction controls write of values contained in the row registers R0-R1 / R2-R3 to the destination render target.

|   Bits | Meaning                 |
|-------:|:------------------------|
| 16..31 | 16..17 = render target class (1 = depth, 2 = other); 18..31 no observable effect |
|     15 | source registers select |
| 11..14 | bit 14 writes a zero payload; 11..13 no effect |
|     10 | stencil write           |
|   6..9 | no effect on hardware   |
|   2..5 | render target index     |
|      1 | write mode, high bit    |
|      0 | write mode, low bit (1 = render target write, 2/3 = spill write) |

Measured on AR20 (notes 24). With rt1 prefilled by an earlier batch and a payload whose
four bytes are distinct, setting bits 6..9, 18..19, 23..25 or every bit 18..31 as
groups leaves all four bytes written, so the instruction carries **no per-byte write
mask**. Bits 11, 12 and 13 are inert on their own and bit 14 is not: it replaces the
payload with `0x00000000` while still committing the write, whichever pair bit 15
names. What bit 14 reads is not established - the candidate source (the second row of
row registers) cannot be populated because `alu_buffer_size = 2` hangs the draw. A
class-1 store with no depth surface bound, and write modes 0, 2 and 3, leave the colour
target at whatever it already held.

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
| 24..31 | bit 24 selects the destination-byte form (below); 25..31 no effect |
|     23 | fetch enable, required; in the bit-24 form it moves the write into `r1` |
| 20..22 | no effect               |
| 16..19 | render target select    |
|  4..15 | no effect in the fetch form; nothing even as a group in the bit-24 form |
|      3 | fetch enable, required; in the bit-24 form it writes `r0.hi` |
|      2 | in the bit-24 form it writes `r0.lo` |
|      1 | dest registers select   |
|      0 | in the bit-24 form it moves the write into `r1` |

Both bit 3 and bit 23 are required for a fetch - clearing either suppresses it - and
bits 20..22 are inert (notes 25). The earlier reading of these two bits as "one
unknown enable" is settled: they are a pair.

#### Destination registers select:

| Value | Meaning   |
|------:|:----------|
|     1 | R2 - R3   |
|     0 | R0 - R1   |

#### The bit-24 form

Bit 24 does **not** select a spill fetch - no word with bit 24 set delivers any part of
a tagged render target, and a fetch issued afterwards still reads the surface unchanged
(notes 25). Instead it writes into the destination pair. Bits 3, 2, 0 and 23 choose the
bytes, and all sixteen combinations were run with `r0` primed `(0x11, 0x22)` and `r1`
`(0x33, 0x44)`, so the idle readback is `0x44332211` and each write shows in place:

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
exactly one of bits 0 / 23 set, the same bytes of `r1` are written instead of `r0`;
with both set, the `r1` write happens *and* `r0.lo` is written as well. Two things
follow from the grid and are worth stating because the earlier table got them wrong:
the low halves *are* written, so this is not a high-half-only clear, and bits 0 and 23
are independent - setting both does something neither does alone. The extra `r0.lo`
write in the both-set rows is measured, not explained.

What lands in those bytes is a value the form produces rather than a constant `0.0`: it
reads back as zero in almost every draw, but a few repeats of the cells that write a low
byte read `0x50` instead (notes 25). It is not a surface read - the surface held a tag in
every word a fetch can reach - and the source stays unidentified. Bit 1 suppresses the
write in the two cells tried, bits 4..15 add nothing even as a group, and no consumer of
the form is identified.

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
