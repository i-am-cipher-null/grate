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

Controls recirculation of a pixel over the fragment-shader pipeline, allowing a program to execute in multiple passes. The exact conditions under which the BLOB driver uses this mechanism are not established.

Patent: https://www.google.com/patents/US8411096<br>
Patent: https://www.google.com/patents/US8659601

## Instructions scheduling

Patent: https://www.google.com/patents/US8856499

Instructions schedule specifies the number of MFU instructions or ALU instruction packets (with an ALU packet encoding up to 4 scalar instructions, see below) executed by the respective stage, each from 0 to 3 per fragment pipeline instructions batch, with 0 being a NOP (see below).

| Bits | Meaning                           |
|-----:|:----------------------------------|
| 2..7 | Address                           |
| 0..1 | Number of instructions to execute |

If the "number of instructions to execute" is 0, then the pipeline stage is NOP, however still takes 1 clock cycle. The "address" is the index of the first instruction or packet to execute.

The current fragment assembler enforces a maximum of 64 execution batches, 64
MFU words and 64 ALU packets in total, with at most three MFU words and three
ALU packets in one batch. Each ALU packet contains up to four scalar slots, so
the 64-ALU limit counts packets, not individual slot operations. These are
limits enforced by Grate's assembler and schedule fields; they should not be
read as proof of every possible hardware limit.

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

FX10 is a signed 10-bit two's-complement value with eight fractional bits. Its representable range is -2.0 through 511/256 (approximately 1.9961).

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
|-------:|:--------:|:-------------------|:------------------------------|
|      0 |    MAD   | Multiply-Add       | rA * rB + rC * rD          |
|      1 |    MIN   | Minimum            | min(rA * rB, rC * rD)      |
|      2 |    MAX   | Maximum            | max(rA * rB, rC * rD)      |
|      3 |   CSEL   | Conditional select | (rA * rB < 0) ? rB : (rC * rD) |

#### Scale result:

| Value |  Meaning |
|------:|:---------|
|   0   | No scale |
|   1   |    x2    |
|   2   |    x4    |
|   3   |    / 2   |

#### Condition code:

The conditional operation is applied to the ALU's result, so the final result is either 0.0 (false) or 1.0 (true).

| Value | Meaning                  | Assembler spelling |
|------:|:-------------------------|:-------------------|
|     0 | no comparison            | none               |
|     1 | result equals zero       | eq                 |
|     2 | result greater than zero | gt                 |
|     3 | result >= zero           | ge                 |

A non-zero condition code replaces the arithmetic result with a Boolean 0.0
or 1.0 result. No additional condition-enable bit is required.

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

#### Addition disable

When bit "addition disable" is set, the Multiply-Add operation turns into two multiplies. The fx10 result of each multiply goes to the low/high subregisters of the destination register.

| write high subregister | write low subregister | destination low | destination high |
|:----------------------:|:---------------------:|:---------------:|:----------------:|
|            1           |           0           |      rC\*rD     |       rA\*rB     |
|            0           |           1           |      rA\*rB     |       rC\*rD     |

For ordinary ALU destinations, the write mask also determines the stored
format: writing both halves (`.lh`) stores an FP20 value, while writing only
the low or high half (`.l*` or `.*h`) stores an FX10 value in that half.
A later unqualified FP20 read uses the high half, so a low-half-only FX10
write is not a suitable producer for such a read.

## MFU instruction word encoding

The MFU unit can fetch and interpolate 4 component vectors per instruction and/or evaluate scalar special functions. Based on [this design](http://pctuning.tyden.cz/ilustrace3/soucek/g80/paper-164.pdf).

|   Bits | Meaning  |
|-------:|:------------------------|
| 58..63 | SFU destination register |
| 54..57 | opcode   |
| 43..53 | mul1     |
| 32..42 | mul0     |
| 28..31 | ???      |
| 21..27 | var3     |
| 14..20 | var2     |
|  7..13 | var1     |
|   0..6 | var0     |

| opcode | Mnemonic | Meaning                      | pseudo-code                    |
|-------:|:--------:|:-----------------------------|:-------------------------------|
|      0 |    NOP   | No operation                 |                                |
|      1 |    RCP   | Reciprocal                   | rA = 1.0 / rA                  |
|      2 |    RSQ   | Reciprocal square root       | rA = 1.0 / sqrt(rA)            |
|      3 |    LG2   | Logarithm base 2             | rA = log2(rA)                  |
|      4 |    EX2   | Exponent base 2, second step | rA = pow(2.0, rA)              |
|      5 |   SQRT   | Square root                  | rA = sqrt(rA)                  |
|      6 |    SIN   | Sine, second step            | rA = sin(rA)                   |
|      7 |    COS   | Cosine, second step          | rA = cos(rA)                   |
|      8 |    FRC   | Fractional value             | rA = rA - floor(rA)            |
|      9 |  PREEX2  | Exponent base 2, first step  | prepares EX2 from rA           |
|     10 |  PRESIN  | Sine, first step             | prepares SIN from rA           |
|     11 |  PRECOS  | Cosine, first step           | prepares COS from rA           |

**PREEX2/EX2, PRESIN/SIN and PRECOS/COS are paired operations.** Hardware
tests show that the PRE operation prepares an internal intermediate consumed
by the corresponding second-step operation; it is not a normal result that can
be stored and reused. Both words must use the same operand register, and that
register must not be written between the two operations. The pair can be
separated by intervening batches (at least four were tested), but omitting the
PRE operation produces incorrect results. The maximum lifetime of the
intermediate is not known.

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
|     0 | nothing observable: writes no row register and supplies no weight (measured) |
|     1 | barycentric weight (measured) |
|  2..3 | supplies the weight as well; from outside the unit indistinguishable from 1 (measured) |
|     4 | row register 0     |
|     5 | row register 1     |
|     6 | row register 2     |
|     7 | row register 3     |

Measured on a Toshiba AC100 (Tegra 2 / AR20) with the Grate assembly harness,
three repeats per variant: values 4..7 write row registers 0..3 and leave the
store pair that does not contain the register at its primed value, so the field
is a plain row index there. Values 0..3 write no row register at all.

Values 1, 2 and 3 supply the interpolation weight instead. Substituting each for
the weight destination in a word that fetches a varying, and storing the
interpolated colour, reproduces the control word byte for byte at two sample
points with different colours, and the same three values reproduce it with either
multiplier as the producer. Value 0 supplies none: its word differs and its
low byte is not reproducible between runs. Whether 2 and 3 decode to the same
register as 1 is not established - the hardware hardwires mul0 to w0 and mul1 to
w1 and derives the third weight as 1.0 - w0 - w1 (see Interpolation), so a field
that named the whole weight and one that named w0 would look identical from
outside the unit.

##### mul0..1 source registers:

The six-bit field at bits 58..63 selects the SFU destination register.
The opcode summaries below retain the working assumption that `rA` is both
the SFU input and destination. Hardware tests identify the destination
selector, but have not independently verified the input source or confirmed
that input and destination are the same register; that relationship remains
unclear.

The SFU result is evaluated before the multiplier result and is available to
MUL through source selector 10. Tests with RCP and SQRT, and with different
SFU destination-register selections, show that selector 10 exposes the SFU
result independently of the selected destination register.

|  Value | Meaning                                  |
|-------:|:-----------------------------------------------|
|   0..3 | row register 0..3 (measured)             |
|   4..7 | internal state, not a row register: 4 reads 0x7F56, 5 reads 0x0089, 6 reads 0x00A9 and 7 reads zero, independent of the source field and of the sample position (measured, identity unknown) |
|   8..9 | 8 reads zero; 9 reads zero until the program fetches an interpolated varying, and 0x4100 in any program whose interpolation fetch runs (measured, identity unknown) |
|     10 | SFU result                               |
|     11 | barycentric coefficient 0 (measured: the per-pixel weight) |
|     12 | barycentric coefficient 1 (measured: the per-pixel weight) |
|     13 | 1.0                                      |
| 14..15 | zero (measured, identity unknown)        |

**Both source fields read row registers, and every pair of sources multiplies.**
An earlier round of tests reported that two row-register operands return zero.
Those variants primed the rows with `.l*` constants, which write the fx10 half,
while the multiplier reads a row register as fp20: the same 16 bits are 0.0625
in fx10 and 2^-31 in fp20, so two of them multiply to about 2^-60 and underflow.
Primed as `.lh` (fp20), all sixteen source pairs return the product of the two
row registers - 0.0625 x 0.125 reads 0xBF, 0.25 x 0.375 reads 0xDB - and moving
the destination onto one of the operands changes nothing. The zero results are
withdrawn.

Measured with the same setup: a row register multiplies a row register, the SFU
result, either barycentric coefficient and 1.0; the SFU result multiplies itself
and a coefficient; the two coefficients multiply each other and saturate,
because an unscaled coefficient is at least 2.0. The two multipliers are
independent: `mul1` reads and writes the same register numbers as `mul0`, and
with the two units writing different row registers both results arrive. When
both units write the same row register, the value that survives is mul0's,
measured through both store pairs.

Selectors 11 and 12 are the per-pixel barycentric weights: they carry a 2^16
scale, so read on their own they saturate, and multiplied by `rcp(r4)` they come
back as coefficients in 0..1 that follow the interpolated colour and move with
the per-vertex w. Exchanging the two multipliers exchanges the two values.

#### Interpolation

The MUL results written to the "barycentric weight" destination are the first
two vertex weights: mul0 supplies w0, mul1 supplies w1, and the third is derived
as 1.0 - w0 - w1. Measured: writing one weight with each multiplier and fetching
returns the interpolated varying, and with either weight left unwritten the fetch
interpolates against state no instruction in the program defined, so a program
has to write both. The third weight is derived rather than written, which is the
compiler's model and not something these probes separate.

The proposed calculation is:

	barycentric weight = barycentric coef * 1.0 / w

Measured: the coefficients carry a 2^16 scale, and r4 holds the fragment's
reciprocal w on the same scale, about 2^16 / w - `rcp r4` reads w / 2^16 for
w = 0.25, 1 and 4. Multiplying a coefficient by the SFU result therefore
cancels the scale, and at w = 1 the product is the coefficient itself. The SFU
named register is both input and output: RCP and SQRT rewrite the register they
name.

**A weight does not have to be in the word that fetches.** The schedule runs up
to three MFU sub-instructions in series inside a batch. Measured: both weights
written two sub-instructions before the fetch interpolate exactly as the word
that computes them and fetches in one does, and a pair written in an earlier
batch is used as well - both weights written as 1.0 in the previous batch return
the same word as writing them in the fetch word, as does 1.0 for w0 a batch
earlier with w1 from the multiplier. What the batch does enforce is order: a
fetch in the first word with the weights in the second reads something else, so
the fetch uses what the batch has written before its slot and not after it. An
earlier version of this document stated that the weight had to be computed in
the word that fetches; both words behind that comparison had one of the two
weights unwritten, and the claim is withdrawn.

#### Fragment w component

The existing pipeline model places the fragment W component in r4 and makes
it available to the first scheduled MFU instruction. The relationship between
that value and the SFU input is still to be verified.

#### Fragment z component

The z component comes in some form via r3. The fetch operation of the r3 should be set to NOP with "saturation" being enabled for r3. After the r3 has been populated, the following expansion should be performed:

	gl_FragCoord.z = 1/1000 + max(0.0, r3.low) * 1/4000 + max(0.0, r3.high) * 1/4

There is also dependency on the linker: it should perform the "magic" write to the TRAM0.w with "VEC4 select = VEC4.z".

## TEX instruction word encoding

The TEX instruction takes the texture coordinates and LOD bias from the first row of the pixel packet (i.e. R0-R3 registers) and writes the sampled data to that first row as well. 

|   Bits | Meaning                               |
|-------:|:--------------------------------------|
|     13 | explicit LOD                         |
| 14..31 | ???                                   |
|     12 | enable bias                           |
|     11 | ???                                   |
|     10 | enable                                |
|   6..9 | ???                                   |
|      5 | sampled data destination regs select  |
|      4 | texcoords / lod regs select           |
|   0..3 | sampler index                         |


#### Texture coordinates and LOD registers select:

The texture coordinate components (S, T, R) and LOD value are loaded from the
row registers as fp20's.

|  Value | Meaning (S, T, R, LOD order) |
|-------:|:-----------------------------|
|      1 | R2, R3, R0, R1               |
|      0 | R0, R1, R2, R3               |

Bit 12 enables LOD bias (TXB); bit 13 selects explicit LOD (TXL) when bit 12 is
clear. With both bits set, bias mode takes precedence in the tested hardware.
With neither bit set, the sampler uses its derivative-based LOD estimate.

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
| 16..17 | render-target class     |
|     15 | source registers select |
| 11..14 | unknown / special path  |
|     10 | stencil write           |
|   6..9 | unknown                 |
|   2..5 | render target index     |
|   0..1 | write mode              |

The low two bits select the operation: 0 is a no-op, 1 is an ordinary
render-target write, and 2 or 3 select the single-register and multi-register
spill paths. They are a mode field, not a simple enable bit. Bits 16..17 are a
render-target class, separate from the target index. In the tested AR20 paths,
class 1 is used for depth and class 2 for ordinary target stores. Class 0 also
worked in some tested contexts; class 3 did not work in the tested path. The
full meaning of the class field is not established.

#### Source registers select:

Seems have no effect when the depth or stencil write enabled.

|  Value | Meaning |
|-------:|:--------|
|      1 | R2-R3   |
|      0 | R0-R1   |

#### Render targets usage

Some of the render targets have an additional special purpose, like depth/stencil store. They are hardwired and their special purpose is active under certain conditions, like when depth/stencil test is enabled.

#### Observed DW position dependence

In AR20 tests, a DW instruction produced a visible store only when it occupied
the first batch of its instruction group. For groups of sizes 2, 3 and 4, the
first-batch probes produced distinct visible results; probes placing DW in
later batches produced zero in the tested output. The first batch is relative
to the group, not to the overall program. The tests establish this
position-dependent behavior, but do not establish the underlying hardware
mechanism or a general rule for every DW mode.


| Render target | Usage             |
|--------------:|:-------------------------------------------------------------------------------------|
|             0 | Depth buffer      |
|             1 | Color buffer      |
|             2 | Stencil buffer    |
|             3 | Auxiliary shader-accessible surface (not fixed-function coverage in tested AR20 path) |
|             4 | Register-spill surface |
|             5 | Additional color surface; AR20 write tested when explicitly bound (`GRATE_RT5=1`) |
|         6..12 | MRT encodings emitted by the compiler; working AR20 writes not demonstrated |

## PSEQ instruction word encoding

PSEQ fetches data from a selected render target and loads it into a register
pair. The observed ordinary fetch encoding is
`0x00800008 | (N << 16) | (pair << 1)`, where N is the render-target index
and pair is 0 for r0/r1 or 1 for r2/r3. For example, `0x00820008` selects
RT2 and r0/r1.

|   Bits | Meaning |
|-------:|:--------|
| 24..31 | bit 24 selects the destination-byte form below; 25..31 not established in that form |
|     23 | required ordinary-fetch enable; participates in byte redirection in the bit-24 form |
| 20..22 | no effect in tested fetches |
| 16..19 | render-target index |
|  4..15 | no effect as a group in the bit-24 form; bits 2 and 3 are described separately |
|      3 | required ordinary-fetch enable; selects `r0.hi` in the bit-24 form |
|      2 | selects `r0.lo` in the bit-24 form |
|      1 | ordinary-fetch destination pair select |
|      0 | participates in byte redirection in the bit-24 form |

Both bits 3 and 23 are required for an ordinary fetch; clearing either
suppresses it. Bit 1 selects the ordinary-fetch destination pair: clear
selects r0/r1 and set selects r2/r3.

### The bit-24 form

Bit 24 does **not** select a spill fetch. No tested word with bit 24 set
delivers data from the tagged render target, and a subsequent fetch still
reads that surface unchanged. Instead, the form writes selected bytes in
r0/r1. The complete tested destination map is below. The registers were
primed distinctly: r0 with low/high bytes `(0x11, 0x22)`, and r1 with
`(0x33, 0x44)`. The idle readback is therefore `0x44332211`.

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

Bit 3 selects `r0.hi`, and bit 2 selects `r0.lo`. With neither of bits
0/23 set, the selected byte(s) are written to r0; with exactly one set, those
same selected byte(s) are redirected to r1. With both set, the selected
byte(s) are written to r1 and r0.lo is also written. Thus the `{0, 23}`
combination, with neither byte selected by bit 3 nor bit 2, writes only
r0.lo. This describes the tested sixteen combinations, not a general rule
for untested fields or destination pairs. The additional r0.lo write when
both redirect bits are set is measured but unexplained.

The written value is not reliably a constant zero. Most draws read zero in
the selected bytes, but some repeated draws read `0x50` in a low byte
instead. The source of that value is unknown. Bit 1 suppressed writes in
the two combinations tested with it set; other bit-1 combinations were not
tested. Bits 4..15 had no observed effect in the bit-24 form, including when
set together. Bits above 23 were not varied in the destination-grid
experiment.

For the full test setup, results and limitations, see [experiment notes,
section 25](https://github.com/i-am-cipher-null/grate/blob/code/fragment-isa-experiments/tests/grate/asm_characterize.experiments.md#25-the-pseq-form-bit-24-selects).
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
