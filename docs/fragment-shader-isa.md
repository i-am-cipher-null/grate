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

The row-register range 0..15 is four rows of four components:
`r(4 * row + component)`. Hardware tests show these registers form one shared
file across batches and groups: a write remains readable by later batches,
including across group boundaries. Batches do not rotate through private row
banks. `alu_buffer_size` controls DW grouping (see DW instruction word
encoding), not row ownership. TEX, DW and the MFU multiplier address only the
current pixel-packet row, `r0..r3`, while the ALU can address the other row
registers. Values needed by TEX or DW must therefore be placed in the current
row.

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
|-------:|:--------------------------------|
| 58..63 | SFU operand register (in-place) |
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

| Value | Meaning |
|------:|:--------|
| 0 | No defined destination behavior established |
| 1..3 | Interpolation-weight destination codes; see below |
| 4 | Row register 0 |
| 5 | Row register 1 |
| 6 | Row register 2 |
| 7 | Row register 3 |

Hardware tests show that destination values 4, 5, 6 and 7 write row
registers 0, 1, 2 and 3, respectively. The row-destination mapping was
tested with `mul0`; `mul1` was also checked at destination 6.

Destination values 1, 2 and 3 all reproduced the reference interpolation
result when used by either multiplier in the tested arrangements. This
establishes their observed effect on interpolation, but does not establish
that the three encodings are aliases or identify separate internal weight
registers. Destination 0 did not produce a defined interpolation weight in
the tested arrangements. Fetches that depended on an unwritten weight
returned state-dependent values, so destination 0 should not be treated as
a zero-valued weight.

##### mul0..1 source registers:

The six-bit field at bits 58..63 selects the SFU operand register. The
register named by `sfu: OPCODE rN` supplies the input and receives the result
in place. Tests with FP20 values in several row registers show that the SFU
operates on the named register and writes the result back to it. The opcode
summaries below therefore use `rA` for this in-place operand.

The SFU result is evaluated before the multiplier result and is available to
MUL through source selector 10. Tests with RCP and SQRT show that selector 10
exposes the SFU result independently of the selected operand register.

| Value | Meaning |
|------:|:--------|
| 0..3 | Row registers 0..3, read as FP20 |
| 4..6 | Unidentified internal values; nonzero and stable in tested probes |
| 7..8 | Unidentified; read back as zero in tested probes |
| 9 | Fetch-associated value; identity unknown |
| 10 | SFU result |
| 11 | First vertex's barycentric coefficient |
| 12 | Second vertex's barycentric coefficient |
| 13 | Constant 1.0 |
| 14..15 | Unidentified; read back as zero in tested probes |

Selectors 4, 5 and 6 returned nonzero values that were unchanged when the
source field or sample position changed. Their identities are not known.
Selector 9 returned zero without an interpolation fetch and changed when a
fetch was present, including with an FX10 fetch; its internal meaning is also
unknown. The zero readbacks for selectors 7, 8, 14 and 15 do not establish
whether those encodings are reserved, unsupported or connected to state that
was not set up by the probes.

Selectors 11 and 12 are fragment-provided barycentric coefficients: source 11
selects the weight of the covering triangle's first vertex and source 12 the
second, in the triangle's index/draw order. Tests compared them with
interpolated unit-vertex colours on both the (0, 1, 2) and (1, 2, 3) triangles
at several sample positions. They do not require a varying fetch; omitting the
interpolation operation leaves the selector readback unchanged.

##### Multiplier execution and interpolation-weight state:

Both multiplier source fields read row registers 0..3 as FP20. A complete
4-by-4 matrix of row-register products, using FP20 primes, matched the
expected products within the precision of the hardware readback. The earlier
zero products came from priming rows with FX10-only writes (`.l*`) and then
reading those bit patterns as FP20: the resulting values were near the FP20
format floor, so their products underflowed. Use FP20 writes (`.lh`) when
priming multiplier row-register inputs.

Additional tests show multiplication with row-register values, the SFU result,
barycentric selectors and constant selector 13 in the tested source-field
combinations. `mul0` and `mul1` can produce independent results in one MFU
word. When both units wrote the same row register in the tested collision
cases, the value observed was the one written by `mul0`.

The interpolation fetch consumes the most recently written barycentric-weight
state. The weights need not be calculated in the same MFU word as the fetch:
tests produced the same interpolation result when both weights were written
one or two MFU words earlier in the batch. A fetch also consumed weights
written in the preceding batch. Conversely, a fetch placed before the weight
writes in the same batch observed different, previously available state; later
writes do not affect an earlier fetch.

These tests establish persistence across the tested MFU-word and batch
boundaries, but do not establish the maximum lifetime of the weight state or
its behavior across all pipeline recirculation conditions. An unwritten weight
is not a defined zero and can produce state-dependent readbacks.

#### Interpolation

Let `lambda_i` be the screen-space barycentric weight of vertex `i`, and
`W_i` its clip-space W. The measured selector values are proportional to
`lambda_i / W_i` for the first and second vertices. Define the perspective
denominator

$$
D = \sum_i \frac{\lambda_i}{W_i}.
$$

Multiplying a selector by `rcp(r4)` gives the corresponding perspective-correct
weight, `(lambda_i / W_i) / D`, within the precision of the hardware readback.
The third normalized weight is the remainder after the first two. An
interpolated varying follows the weighted sum of its vertex values using these
perspective-correct weights, rather than a screen-space barycentric sum.

The unequal-W probe premultiplied clip-space position by each vertex's W, so
the projected triangle stayed fixed while one vertex's W changed through 1, 2
and 4. The selector ratio changed by the corresponding 1:2:4 factors, and an
independently interpolated scalar matched perspective interpolation rather
than screen-space interpolation. The readback has only about three mantissa
bits (roughly 12.5% per step), so the measurements establish the relative
weights and common denominator but do not resolve a common scale or exact
normalization more precisely. The 43-variant barycentric and 38-variant
perspective sweeps passed their pin and verification runs (133/133 and
118/118 rows respectively, including controls) and reproduced from clean
checkouts. See the [fragment hardware experiment notes](https://github.com/i-am-cipher-null/grate/blob/code/fragment-isa-experiments/tests/grate/asm_characterize.experiments.md).

#### Fragment w component

Hardware tests show that `r4` carries the interpolated reciprocal clip W,
not clip W itself. It is approximately `65536 * D` on a 16.16-style scale,
where `D = sum(lambda_i / W_i)` is the perspective denominator. With a
constant clip W across the primitive this reduces to approximately
`65536 / W`; when vertex W values differ, `r4` tracks the per-fragment
perspective denominator. The probe's `src13 x rcp(r4)` anchor matched
`1 / (65536 * D)`. Thus `sfu: rcp r4` supplies the common normalization
factor used with selectors 11 and 12.

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

#### Observed DW execution with `alu_buffer_size`

Tests with `alu_buffer_size` 2, 3 and 4 show that batches form groups of N.
A visible DW store occurs only when a group contains exactly one DW and that
DW is in the group's first batch. The store is deferred until the end of the
group, so it sees row-register writes made by later batches in that group.
A DW in any later batch, or a second DW anywhere in the same group, produced
no observable store in the tested arrangements. The same rule applies
independently to later groups.

The pattern was unchanged when `pseq_to_dw_exec_nb` was set to 1, 2, 3, 4, 6,
8, 16 or 31. The hardware mechanism is unknown, and other group sizes and DW
arrangements have not been exhaustively tested.

The low count field in the fragment start word at `0x520` counts batches,
not execution slots. Use a count equal to the program's batch count; for
`alu_buffer_size` greater than one, keep that count a multiple of the buffer
size. Incomplete final groups were not reliably executable: tested programs
sometimes returned zero and sometimes hung.


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

PSEQ stands for Program Sequencer. It fetches data from a selected render target and loads it into a register pair. In the tested RGBA8 path, a fetch/store round-trip preserved the bytes exactly; conversion behavior for other formats is not established.

|   Bits | Meaning                 |
|-------:|:------------------------------|
| 24..31 | unknown                 |
|     23 | set in observed fetch encoding |
| 20..22 | unknown                 |
| 16..19 | render target select    |
|  4..15 | unknown                 |
|      3 | set in observed fetch encoding |
|      2 | unknown                 |
|      1 | destination pair select |
|      0 | unknown                 |

The observed fetch encoding is `0x00800008 | (N << 16) | (pair << 1)`,
where N is the render-target index and pair is 0 for r0/r1 or 1 for r2/r3.
For example, `0x00820008` selects RT2 and r0/r1. The individual roles of
bits 3 and 23, and the meaning of other PSEQ encodings, remain unknown.

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
