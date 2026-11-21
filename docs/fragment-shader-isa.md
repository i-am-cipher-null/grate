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

ALU instructions execute in packets of up to four scalar slots (the fourth slot can be traded for embedded constants). Hardware tests show that row-register writes are not forwarded between scalar slots in the same packet: a consumer reads the value held before the packet, regardless of whether its producer is in an earlier or later slot. The written value is visible to a consumer in the next packet. The separate `recv` accumulation path does carry a previous slot's result; see Result accumulation below.

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

Hardware tests confirm that CSEL tests the sign of `rA * rB`, returns the
unmultiplied `rB` when that product is negative, and returns `rC * rD`
otherwise. A zero product takes the latter branch. The assembler accepts only
`#1` or an omitted fourth operand for `rD`; omitting it uses `rC` as-is.
Negative values in the `rC * rD` candidate and negative zero have not been
tested.

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

Condition-register destinations are selected from registers 64..71. Hardware
tests show that a write stores a Boolean 0.0 or 1.0 derived from the ALU
result; applying a condition modifier first replaces that result with the
condition's Boolean value. The low and high sub-registers were independently
addressable in the tested cases, and writes to different condition registers
did not interfere.

A read in the same ALU packet as the write returned the old value (zero in the
test), while a read in the next packet observed the updated value. Thus the
tested condition-register write is not forwarded to another scalar slot in
the same packet. The tests establish reads from condition registers as ALU
sources, but do not fully map all source-selector combinations.

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

Hardware tests with all twelve producer/consumer slot orderings found that a
row-register consumer reads the pre-packet value, not the value written by
another slot in that packet. This held for the tested MAD, MUL, MIN and MAX
producer/consumer combinations and for low-half, high-half and FP20 writes.
Moving the consumer to the next packet made the producer's write visible.
Intervening MFU or TEX stages did not disturb the value unless they wrote the
same row register.

Reads through `alu0..3` returned zero at the tested same-packet and later-packet
separations. The assembler has no `aluN` destination syntax, and a raw-word
destination test has not yet been made, so it is not established whether these
registers can be written.

With `recv` (`this`), a scalar instruction can add an accumulator value
carried from another slot to its own result. In the established non-send
arrangements, the receiver gets the immediately preceding slot's **raw
result**, not that slot's accumulated destination value. For example, a 0.5
result after a 0.25 result reads as 0.75. The producer does not need `recv`
set, and the tested accumulation did not cross a packet boundary.

The additional `send` probes show that a receiver can observe a sender's
result in several arrangements, but do not yet define a general slot-order
rule. With senders in slots 0 and 1 and a receiver in slot 2, the receiver
added the slot-1 result. A receiver in slot 0 with a sender in slot 1 also
observed that sender in one test, conflicting with an older arrangement that
reported no transfer when the order was reversed. The operand and destination
arrangements differed, so that discrepancy remains unresolved. The send bit
also changed the sender's own destination in the tested cases: a sent product
and its stored destination did not always match. Do not infer a general
`send` write rule from these examples.

When multiple slots wrote the same FX10 half in one packet, the observed
destination contained the bitwise OR of their FX10 encodings rather than the
last write or their arithmetic sum. The same OR behavior was observed for
multiple FP20 (`.lh`) writes: for example, FP20 words `0xEB00` and
`0xF300` produced `0xFB00`. Mixed FP20/FX10 writes produced values not
explained by either format's standalone behavior and remain unresolved.

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

**PREEX2/EX2, PRESIN/SIN and PRECOS/COS are paired operations.** The PRE
operation prepares an internal intermediate for the corresponding second-step
operation; it is not an ordinary result to store as the function value. Both
operations must name the same SFU operand register. A write to that register
between the two operations, through either the multiplier or ALU in the tested
cases, destroys the pending pair; a write to another register does not.

The pair survived separations of up to 17 MFU words and five batch boundaries
in the tested sequences. This is the largest tested separation, not an
established lifetime limit. The second-step operation does not consume the
intermediate: repeating the same main operation reuses it. A second PRE on the
same register does not simply replace the pending intermediate and instead
corrupts the result. Two pairs on different registers were successfully
interleaved.

Without the corresponding PRE, the tested EX2, SIN and COS controls did not
produce their normal functions (for example, EX2 of -1 read as 1.0 and SIN of
1 read as the input 1.0). Intervening SFU operations can disturb a pending pair;
SIN and COS were exceptions in the tested PRESIN sequence, with the COS
behavior still unexplained. The behavior of every possible intervening
operation has not been characterized.

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

##### LINK export and TRAM mapping

Hardware tests show that the four component positions in a LINK export map
independently to the four TRAM destinations named by the LINK swizzle. The
k-th exported component is written to the TRAM component named by the k-th
swizzle letter. Repeated destination letters overwrite that component, with
the later export component winning in the tested cases. `exportM` selects the
M-th enabled export, not the vertex register number: when exports 0 and 7 were
enabled, `export1` selected the value exported from register 7. The `tN`
prefix in the fragment fetch selects TRAM row N.

A LINK column set to NOP does not update its destination component. In the
tested setup, that component retained its previous value across consecutive
assembler invocations; TRAM should not be assumed to be cleared between draws.
At the fetch stage, an `ipl` operand of NOP leaves its row register unchanged,
whereas `sat(NOP)` writes zero. Applying saturate to a fetch clamped a value of
1.5 to 1.0 in the FP20 row register; the ordinary FX10 byte readback alone
cannot distinguish the clamped and unclamped values above 1.0.

The tested `(dis)` modifier disabled interpolation for its component and
exposed a vertex value instead. The selected vertex was not identified by the
single-sample test. Format conversion also remains partly unresolved: tested
FX10 low-format fetches produced a value in the low half and zero in the high
half, while high-format fetches duplicated the value into both halves. Reading
an FX10-low component as FP20, or an FP20 component through the tested FX10
fetch, returned zero. These observations are specific to the tested formats
and encodings and do not establish a general conversion rule.

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

The SFU is evaluated before the multipliers in its MFU word. Hardware tests
of all eleven decoded SFU opcodes show that each operation writes its named
operand register in place, and that selector 10 exposes the SFU result to the
multipliers in that same word. Both multipliers can read that result, including
when one of them also writes the SFU operand register.

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

The interpolation fetch consumes barycentric-weight state that has been
written before the fetch. The weights need not be calculated in the same MFU word as the fetch:
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

A proposed linker dependency is the LINK `(z)` modifier, previously described as a "magic" write of VEC4.z to TRAM0.w. This behavior has not been confirmed. Hardware tests show that `(z)` sets bit 0 of the first LINK instruction word, but the tested programs read zero from every FP20 TRAM component when it is set. Changing the swizzle, enabled export or TRAM row did not change that result, and a control without a varying fetch still executed the draw normally. A two-LINK control also read the ordinary LINK values regardless of whether the `(z)` LINK came before or after it. The identity of the VEC4.z input and how it is populated remain unknown; the special linker write must not be treated as an established way to supply fragment Z.

## TEX instruction word encoding

TEX reads S, T, R and LOD from the first row of the pixel packet, using the
register group selected by bit 4, and writes the sampled RGBA result to the
register pair selected by bit 5. The tested fetch writes the four components as
FX10 halves.

|   Bits | Meaning |
|-------:|:--------|
|     13 | explicit LOD select |
| 14..31 | unknown |
|     12 | LOD bias select |
|     11 | no effect in tested fetches |
|     10 | fetch enable |
|   6..9 | no effect in tested fetches |
|      5 | destination register pair select |
|      4 | coordinate/LOD register group select |
|   0..3 | sampler index |

### Sampler and enable

Bits 0..3 select the sampler. Sampler 0 and sampler 1 were both tested with
bound textures and selected the corresponding surfaces. Readbacks from
unbound sampler indexes were unstable and are not a defined fallback value.

Clearing bit 10 suppresses the fetch and leaves the destination registers
unchanged. A control with bit 10 set overwrites the same registers, confirming
that the disabled case is not a fetch that writes zero.

### Coordinate and destination registers

The coordinate components S, T, R and the LOD value are read as FP20 from the
row registers:

| Value | Meaning (S, T, R, LOD order) |
|------:|:-----------------------------|
| 0 | r0, r1, r2, r3 |
| 1 | r2, r3, r0, r1 |

Bit 5 selects the destination pair:

| Value | Destination |
|------:|:------------|
| 0 | r0-r1 |
| 1 | r2-r3 |

The RGBA channel order was confirmed through the r2-r3 destination pair.
The tested texture did not distinguish the channel order through r0-r1, so
that path remains uncharacterized.

The default wrapping observed for both S and T is repeat: coordinates such as
1.25 and -0.25 wrap to 0.25 and 0.75, respectively. The sampler follows the
texture's dimensions rather than a fixed 4x4 size.

### LOD selection

Bit 12 selects LOD bias. When bit 12 is clear, bit 13 selects explicit LOD.
With both bits clear, the LOD register is ignored and the sampler uses a
derivative-based estimate. When both bits are set, bias takes precedence in
the tested hardware.

Explicit LOD values below zero and above the available mip range clamp to the
end levels. Fractional LOD selected the nearest mip level in the tested
configuration. Bias and explicit LOD produced different results on a 16x16
mip chain; the exact derivative-based LOD calculation and the behavior of
fractional bias were not established.

The experiments used nearest magnification filtering and
NEAREST_MIPMAP_NEAREST minification filtering. They therefore do not establish
linear texel filtering or mip-level blending.

### Formats and addressing

RGBA8888 fetches preserved the sampled channel bytes through the tested
r2-r3 readback path. DXT1 and ETC1 block-compressed surfaces also produced
readable block rows in the tested cases. DXT1's one-bit alpha decoded as
opaque in the probes; the ETC1 readback differed slightly from the DXT1
control and that decoder difference is not explained here.

The sampler stepped through rows at width times four bytes in the tested
RGBA surface. When the allocation used a larger row pitch, the sampled row
was instead displaced into padding. The observed TEX path therefore did not
honor the tested non-default surface pitch.

Bits 6..9 and bit 11 were toggled individually on a bound-sampler fetch and
on explicit-LOD fetches at three mip levels. They did not change the result in
those configurations. This does not establish that the fields are reserved
or inert in every texture mode. Bits 14..31 remain undocumented here; the
older unknown-bit sweep is not reproduced by the current committed experiment
set.

## PSEQ instruction word encoding

PSEQ (Program Sequencer) can fetch a selected render target into one of the
two row-register pairs. The observed fetch encoding is
`0x00800008 | (N << 16) | (pair << 1)`, where N is the render-target index
and pair is 0 for r0/r1 or 1 for r2/r3. For example, `0x00820008` selects
RT2 and r0/r1.

|   Bits | Meaning |
|-------:|:--------|
| 24..31 | unknown, except bit 24 spill behavior below |
|     23 | required fetch enable |
| 20..22 | unknown |
| 16..19 | render-target index |
|  4..15 | mostly unknown; selected bits tested inert |
|      3 | required fetch enable |
|      2 | unknown |
|      1 | destination pair select |
|      0 | unknown |

Both bits 3 and 23 must be set for the ordinary fetch. Clearing either one
suppressed the fetch and left the destination registers at their previous
values. Bit 1 selects the destination pair: clear selects r0/r1, set selects
r2/r3. The `load rtN, r0, r1` mnemonic assembles to the same word as the
measured ordinary fetch.

Bits 16..19 select the render target. Successful round-trips were measured
for RT1, the coverage surface (RT3), RT4 and RT5 when those surfaces were
bound. Unbound target indexes returned unstable partial values and should not
be treated as defined results.

### Render-target formats

The format tests used RT5. RGBA8888 preserved all four components in R, G, B,
A order. The tested narrower formats converted as follows:

| Format | Observed fetch result |
|:-------|:---------------------|
| RGBA8888 | All four components preserved |
| A8 | Alpha component in the fourth destination component; other components zero |
| L8 | First component replicated to RGB; alpha set to 1.0 |
| RGB565 | R, G and B quantized to 5/6/5 bits and expanded; alpha set to 1.0 |

The round-trip establishes these mappings for the tested formats and RT5
configuration, not for every target class or surface layout.

### Other tested bits and spill path

On the A8 target, toggling bits 4, 5, 6, 7, 8, 11, 15, 20, 22, 25 and 31
individually did not change the measured fetch result. These observations do
not establish the meaning of untested bits in those ranges or prove that the
tested bits are reserved in every mode.

With bit 23 set, bit 24 selects a different observed path: the normal fetch
does not occur, but the fourth component of the low destination word is
cleared. This is a partial write, not ordinary fetch suppression. The role of
the other fields in this spill form is unknown.

### Addressing limits

The current round-trip writes the same pattern for every fragment. It
establishes the target selection, enable bits, destination pair and tested
format conversions, but does not establish which pixel address PSEQ reads.
Position-dependent addressing, offsets, alignment, tiled layouts and
non-default pitches remain untested. The experiment also does not establish a
frame-to-frame visibility guarantee; it only shows that a store earlier in
the tested fragment program is visible to a later PSEQ fetch.

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
