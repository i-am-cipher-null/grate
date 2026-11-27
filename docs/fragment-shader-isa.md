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

The address/count units are clarified by the assembler's schedule encoding:
ALU addresses and counts use four-slot **packets**, while MFU scheduling uses
MFU instruction **words**. The assembler's `instr_sched` path also schedules
TEX as instruction words, one per `TEX:` line. This is the emitter-facing
encoding model; it is not a separate measurement of the scheduler's internal
hardware units. On AR20, schedule-word bits 8..28 remain unexplained, but the
assembler emits zero there and setting the reachable high bits did not change
the tested schedule shapes. An emitter using the assembler need not construct
those fields manually (experiment notes 30, 34 and 35).

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

| opcode | Mnemonic | Meaning            | pseudo-code                    |
|-------:|:--------:|:-------------------|:-------------------------------|
|      0 |   MAD    | Multiply-Add       | rA * rB + rC * rD              |
|      1 |   MIN    | Minimum            | min(rA * rB, rC)               |
|      2 |   MAX    | Maximum            | max(rA * rB, rC)               |
|      3 |   CSEL   | Conditional select | (rA * rB < 0) ? rB : (rC * rD) |

Hardware tests confirm that CSEL tests the sign of `rA * rB`, returns the
unmultiplied `rB` when that product is negative, and returns `rC * rD`
otherwise. A zero product takes the latter branch.
Negative values in the `rC * rD` candidate and negative zero have not been
tested.

MIN and MAX were measured in the form emitted by the assembler, with the
fourth source set to `#1`: `min/max(rA * rB, rC)`. Both terms participate;
the first term is multiplied by `rB`, and the second term is `rC` because
the fourth source is one. Tests with negative inputs showed signed-value
selection for both operations, and a MIN result-scale probe confirmed that
scaling follows the MIN/MAX selection. The assembler rejects `#0` in this
operand position. Other fourth-source encodings were not established, so do
not generalize the measured MIN/MAX semantics beyond this form (experiment
[note 49](https://github.com/i-am-cipher-null/grate/blob/code/fragment-isa-experiments/tests/grate/asm_characterize.experiments.md#L7048)).

#### Scale result:

| Value |  Meaning |
|------:|:---------|
|   0   | No scale |
|   1   |    x2    |
|   2   |    x4    |
|   3   |    / 2   |

#### Result saturation

ALU bit 56 (`sat`) clamps the result to the inclusive range 0.0..1.0.
The measured result path is:

`operation -> result scale -> saturation -> condition code -> write`

The clamp is therefore applied after result scaling and before condition-code
evaluation. A value of 2.0 becomes 1.0, -0.5 becomes 0.0, and an in-range
value is unchanged. The behavior was measured for MAD, MUL and MAX in both
FP20 (`.lh`) and FX10 (`.l*`) destinations, and for MIN in an FP20
destination. MIN saturation was measured separately; it is not merely inferred
from MAX (experiment [note 48](https://github.com/i-am-cipher-null/grate/blob/code/fragment-isa-experiments/tests/grate/asm_characterize.experiments.md#L6972)
and [note 49](https://github.com/i-am-cipher-null/grate/blob/code/fragment-isa-experiments/tests/grate/asm_characterize.experiments.md#L7048)).

Saturation combined with the accumulation path (`this`/`other`) or a
condition-register destination was not tested. These combinations should
remain unspecified.



#### Condition code:

The conditional operation is applied to the ALU's result, so the final result is either 0.0 (false) or 1.0 (true).

| Value | Meaning                  |
|------:|:-------------------------|
|     0 | no comparison            |
|     1 | result equals zero       |
|     2 | result greater than zero |
|     3 | result >= zero           |

A non-zero condition code replaces the arithmetic result with a Boolean 0.0
or 1.0 result. No additional condition-enable bit is required.

#### Condition registers:

Condition-register destinations are selected from registers 64..71. Each
condition register has low and high FX10 sub-registers holding Boolean values
0.0 or 1.0. Hardware tests show that a write stores a Boolean derived from the
ALU result; applying a condition modifier first replaces that result with the
condition's Boolean value. The low and high sub-registers were independently
addressable in the tested cases, and writes to different condition registers
did not interfere. The high sub-register is selected with the write-high bit.

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

The row-register range 0..15 is four groups of four components:
`r(4 * group + component)`. Hardware tests show these registers form one
shared file across batches and groups: a write remains readable by later
batches, including across group boundaries. Batches do not rotate through
private row banks. `alu_buffer_size` controls DW grouping (see DW instruction
word encoding), not row ownership.

The fixed primary four-register interface is `r0..r3`, not a rotating
"current row". MFU multipliers read and write this interface. TEX also uses
these four entries, but bit 4 independently selects the coordinate order
(`r0,r1,r2,r3` or `r2,r3,r0,r1`) and bit 5 selects the destination pair
(`r0-r1` or `r2-r3`). DW selects its source pair independently with bit 15.
The ALU can address all sixteen row registers.

#### Result accumulation:

Patent: https://www.google.com/patents/US8521800

When the "recv" bit is set, operand rC is overridden with the accumulation value.

|       | recv         | send         |
|-------|--------------|--------------|
| ALU0: | ALU0 += ALU3 | No action    |
| ALU1: | ALU1 += ALU0 | ALU0 += ALU1 |
| ALU2: | ALU2 += ALU1 | ALU0 += ALU2 |
| ALU3: | ALU3 += ALU2 | ALU2 += ALU3 |

Hardware tests with all twelve producer/consumer slot orderings found that a
row-register consumer reads the pre-packet value, not the value written by
another slot in that packet. This held for the tested MAD, MUL, MIN and MAX
producer/consumer combinations and for low-half, high-half and FP20 writes.
Moving the consumer to the next packet made the producer's write visible.
Intervening MFU or TEX stages did not disturb the value unless they wrote the
same row register.

Reads through `alu0..3` returned zero at the tested same-packet and later-packet
separations. A raw-word destination test has not yet been made, so write behavior for
these registers remains unknown.

The `recv` marker (`this`) and `send` marker (`other`) were tested
independently across all twelve ordered pairs of ALU slots, with controls for
neither marker, each marker alone, and both markers.

With `recv` (`this`) on slot j, that slot adds the immediately preceding
slot's **raw result** to its own result, wrapping at the packet boundary:
slot 0 receives from slot 3. The sender does not need `send` set. The value
received is not the sender's accumulated destination value.

With `send` (`other`) on a slot, its raw result is delivered to another
slot's accumulator in the same packet. The measured destinations are slot 0
for senders in slots 1 and 2, and slot 2 for a sender in slot 3. Slot 0 with
`send` set produced no observable delivery to another slot in the packet.
The mapping for senders 1 through 3 fits the tested `(i - 1) & ~1` pattern,
but slot 0 is an exception, so this is not a complete routing formula. A
receiving slot accumulates delivered values even when it does not have
`recv` set.

The two markers act independently. A slot with both markers receives the
preceding slot's raw result and sends its own raw result. In a chaining test,
the middle slot's destination contained its accumulated value (0.75), while
the value it forwarded was its unaccumulated result (0.5).

Multiple sends can contribute to the same accumulator. When slots 1 and 2
both sent to slot 0, slot 0 accumulated its own 0.0625 result plus 0.25 and
0.5, producing 0.8125. In a separate arrangement, a `recv` contribution
from slot 3 raised that result to 0.875. An overflow test produced 1.5 before
accumulation saturation and read back as 1.0. Thus, in the tested cases,
multiple incoming sends to the target slot's accumulator are added, with the
observed sum saturating at 1.0.

An earlier two-sender arrangement, where the observed receiver was distinct
from the send target, produced only the preceding slot's value. That result
is specific to that arrangement; it does not show that multiple sends to a
slot's own accumulator are discarded. The tests also found no delivery from
a send in one packet to a candidate receiver in the following packet.

Slot 0's `send` result was not observed in any other slot or in the following
packet. The tests cannot distinguish a dropped value from a value routed to
an unobserved destination. Hardware write-back tests confirmed that
destinations `r0` and `r1`, like `r2` and `r3`, can be written from each
of the four ALU slots.

The routing observations are limited to the tested packet arrangements.
The reason for the routing pattern, the destination (if any) of slot 0's
send, delivery across other packet arrangements, fan-out from one send to
multiple receivers, negative-value accumulation, and the saturation behavior
outside the tested positive overflow case remain unresolved.

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

| opcode | Mnemonic | Meaning                      | pseudo-code          |
|-------:|:--------:|:-----------------------------|:---------------------|
|      0 |   NOP    | No operation                 |                      |
|      1 |   RCP    | Reciprocal                   | rA = 1.0 / rA        |
|      2 |   RSQ    | Reciprocal square root       | rA = 1.0 / sqrt(rA)  |
|      3 |   LG2    | Logarithm base 2             | rA = log2(rA)        |
|      4 |   EX2    | Exponent base 2, second step | rA = pow(2.0, rA)    |
|      5 |   SQRT   | Square root                  | rA = sqrt(rA)        |
|      6 |   SIN    | Sine, second step            | rA = sin(rA)         |
|      7 |   COS    | Cosine, second step          | rA = cos(rA)         |
|      8 |   FRC    | Fractional value             | rA = rA - floor(rA)  |
|      9 |  PREEX2  | Exponent base 2, first step  | prepares EX2 from rA |
|     10 |  PRESIN  | Sine, first step             | prepares SIN from rA |
|     11 |  PRECOS  | Cosine, first step           | prepares COS from rA |

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
draws; TRAM should not be assumed to be cleared between draws.
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

| Value | Meaning            |
|------:|:-------------------|
|     0 | ???                |
|  1..3 | barycentric weight |
|     4 | row register 0     |
|     5 | row register 1     |
|     6 | row register 2     |
|     7 | row register 3     |

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

##### SFU operand and multiplier source selectors

**SFU operand selector (six-bit field, bits 58..63).** In
`sfu: OPCODE rN`, the `rN` token names this selector. It is not necessarily
a row-register reference. For example, the `r10` in `sfu: rcp r10` is
**SFU operand selector 10**, not ordinary row-register 10.

A full RCP sweep of all 64 encodings found that bits 5:4 are ignored by the
tested RCP decode: selector values `n`, `n+16`, `n+32` and `n+48` produce
the same result for each low-nibble value `n`. This is a hardware decode
property, not assembler truncation. The assembler and disassembler preserve
the six-bit field (for example, `rcp r50` round-trips as `rcp r50`), so the
instruction encoding must remain six bits wide.

For RCP, the low-nibble classes are:

| Low nibble | Tested meaning |
|-----------:|:---------------|
| 0..3 | Ordinary row operands 0..3; the reciprocal is written back to the selected row. |
| 4..7 | Internal operands independent of the seeded row-register contents; exact sources remain unknown. |
| 8..15 | Normalization-family operand; returns `1/M`, where `M = max(|r0|, |r1|, |r2|)`. |

For the other tested single-step SFU opcodes (`RSQ`, `SQRT`, `LG2` and
`FRC`), the SFU operand/result calculation uses the low three bits
(`reg & 7`): for example, `rsq r10` computes the same SFU value as
`rsq r2`. However, the whole MFU word is **not** equivalent to the index-2
word: bit 3 remains set and triggers additional row writes and multiplier
source-slot injections described below. The `1/M` result is specific to RCP;
the bit-3 side effects are not. The paired operations (`PREEX2/EX2`,
`PRESIN/SIN` and `PRECOS/COS`) were not included in this selector sweep.

**MFU multiplier input selector (separate four-bit field).** The source token
in a `mul0` or `mul1` operand selects an input through a different mux.
In this field, the token `sfu` is encoded as value **10** and means "use the
SFU result as the multiplier input." It does **not** mean SFU operand
selector 10, and it does not invoke the RCP normalization operation. For
example, `mul0: r3, sfu, #1` captures the current SFU result in row 3.

These are two independent fields that happen to use the numeric encoding
10 for different meanings:

| Field and assembly location | What its value 10 selects |
|:--|:--|
| **SFU operand selector**, `sfu: rcp r10` | The RCP normalization-family input: `1/M`, where `M = max(|r0|, |r1|, |r2|)`. |
| **MFU multiplier input selector**, source token `sfu` in `mul0` or `mul1` | The output produced by the SFU operation in that MFU word. |

The two meanings remain independent when both sub-units are active in the
same MFU word. In particular, the special routing measured when the **SFU
operand selector** is 10 makes the multiplier's ordinary source token
`r2` deliver `1/M`; it does not change the meaning of multiplier input
selector 10 (`sfu`).

| Value | Meaning |
|------:|:--------|
| 0..3 | row register 0..3 |
| 4..6 | unknown sources; tested values were nonzero and unchanged when the source field or sample position changed |
| 7..8 | unknown; zero readback in the tested arrangements does not establish that these are reserved |
| 9 | fetch-associated value; zero without a fetch and changed when a fetch was present |
| 10 | SFU result (`sfu`) |
| 11 | barycentric coefficient 0 |
| 12 | barycentric coefficient 1 |
| 13 | constant 1.0 (`#1`) |
| 14..15 | unknown; zero readback in the tested arrangements does not establish that these are reserved |

Selectors 11 and 12 are fragment-provided barycentric coefficients: source 11
selects the weight of the covering triangle's first vertex and source 12 the
second, in the triangle's index/draw order. They do not require a varying
fetch; omitting the interpolation operation leaves the selector readback
unchanged.

The explicit multiplier input token `sfu` remains distinct from the
bit-3-controlled routing into the multiplier's row-source slots described
below. Selecting `sfu` directly reads the SFU result; selecting `r0`..`r3`
reads one of the four multiplier row-source slots.

##### Normalization-family reciprocal: RCP selectors 8..15

On Tegra 20 / AR20, every low-nibble selector from 8 through 15 invokes the
same normalization-family reciprocal when the SFU opcode is RCP:

$$
M = \max(|r0|, |r1|, |r2|), \qquad \mathrm{SFU} = \frac{1}{M}.
$$

The result is positive: the maximum is selected by absolute magnitude and
the selected component's sign is discarded. Axis permutations, mixed signs,
ties, zero and several magnitudes were tested. The RCP operand selector does
not read one of the sixteen row registers in this mode.

The bit-3 effect is broader than the RCP reciprocal. For the other tested
single-step SFU opcodes (`RSQ`, `SQRT`, `LG2` and `FRC`), the low three
bits select the ordinary SFU operand: for example, `rsq r10` computes the
same SFU value as `rsq r2`. But bit 3 still triggers the row writes and
multiplier source-slot routing below. The `1/M` result is RCP-specific;
the side effects are not. The paired operations
(`PREEX2/EX2`, `PRESIN/SIN` and `PRECOS/COS`) have not been tested for
these bit-3 effects.

Ordinary RCP selectors 0..3 read a row and write their reciprocal back into
that same row. Selectors 4..7 are different internal inputs; notes 58..62
subsequently characterized their observable behavior:

- **Selector 4** supplies a per-fragment quantity proportional to
  (2^{16}D), where
  (D = \sum_i \lambda_i/W_i) is the perspective denominator. It is not a
  constant (2^{16}), and it is not simply (2^{16}/W) for a single clip-W
  value.
- **Selectors 5, 6 and 7** follow row registers 1, 2 and 3 respectively.
  They are row-indexed, not component-indexed. For an ALU-seeded row value
  (v \ne 0), the measured input behavior follows its sign-preserving
  normalized significand
  (m = |v|/2^{\lfloor\log_2 |v|\rfloor}), with
  (1 \le m < 2), combined with a piecewise exponent:
  (K=32) for (|v|<2), and (K=16) for (|v|\ge 2). The transition at
  magnitude 2 is measured. The exact internal implementation of this
  piecewise behavior is not established.
- These selectors do not write rows 0..3 in the tested arrangements.

The perspective-interpolator fetch path is not fully explained by the
ALU-visible row value alone. With the same ALU-visible value, varying clip W
changes the selector 5..7 result. Tests ruled out the simple candidate inputs
(N), (D), and (T_i), and a general function of (D) alone. The remaining
fetch-associated contribution is bounded to about half an octave, but its
source and mechanism remain unresolved (notes 59..63).

##### Multiplier row-source slots when SFU operand bit 3 is set

The multiplier has four row-source inputs, named `r0`..`r3`. When SFU
operand bit 3 is set (selectors 8..15 in the tested low-nibble family), two
values are injected into these source slots in addition to the row-file
readback:

- The major magnitude `M = max(|r0|, |r1|, |r2|)` is injected into source
  slot `r2`.
- The SFU result is injected into source slot `reg & 7`, when that slot is
  one of the multiplier's four slots (0..3).
- If both injections target slot `r2` (SFU operand selector 10), the SFU
  result wins and `M` is not visible in that slot.
- The underlying row-source slots read the row file **after** the SFU
  operation's row writes. The injected values then override the corresponding
  slots.

The observed routing is:

| SFU operand selector | SFU-result slot | value in multiplier source slot `r2` |
|---:|:--:|:--|
| 8 | `r0` | `M` |
| 9 | `r1` | `M` |
| 10 | `r2` | SFU result (collision; result wins over `M`) |
| 11 | `r3` | `M` |
| 12..15 | `r4`..`r7` (not addressable by the multiplier's row-source inputs) | `M` |

The numerical value injected as the SFU result depends on the opcode and the
low three operand bits. For RCP in the normalization family it is `1/M`;
for the other tested single-step operations it is the corresponding SFU
operation on row `reg & 7`. This slot injection is separate from the explicit
multiplier input token `sfu` (selector value 10 in the multiplier's own
four-bit source field), which directly selects the SFU result rather than a
row-source slot.

For example, with selector 8 and the vector `(r0,r1,r2,r3) = (1.5,4.0,0.75,0.5)`,
the SFU result `1/M = 0.25` appears in source slot `r0`, `M = 4.0` appears
in source slot `r2`, and the written row 1 value `-0.75` is visible in
source slot `r1`. With selector 10 the SFU result is instead injected into
slot `r2`, replacing `M`. For selectors 12..15 the SFU-result slot is outside
the four multiplier row-source inputs, but `M` remains visible through
source `r2`.

This explains the captured compiler word:

~~~text
sfu:  rcp r10
mul0: r0, r2, r0
mul1: r1, r2, r1
ipl:  nop, nop, nop, nop
~~~

With selector 10, the multiplier's `r2` source supplies `1/M`. Its `r0`
source reads the post-write row 0, not the original coordinate. For the tested
vector `(r0,r1,r2) = (4,2,0.5)`, the RCP row write leaves row 0 at -0.5, so
the first product is `-0.5 * 0.25 = -0.125`; the second is
`2 * 0.25 = 0.5`. A plain positive-only store readback can show the first
result as zero, but it is negative, not a zeroed multiplier.

##### Row writes triggered by SFU operand bit 3

For the tested single-step SFU opcodes (`RCP`, `RSQ`, `SQRT`, `LG2` and
`FRC`), operand bit 3 triggers the same row-write operation. This is not
RCP-specific: the row writes were reproduced with `RSQ`, `SQRT`, `LG2`
and `FRC` as well as `RCP`. The paired operations
(`PREEX2/EX2`, `PRESIN/SIN` and `PRECOS/COS`) have not been tested for
this behavior.

Let `d = argmax(|r0|, |r1|, |r2|)`, with ties going to the highest
component index, and let `s = sign(r_d)`. Row 2 receives the floating-point
integer descriptor `2*d + (s < 0)`, in the range 0..5. The measured
coordinate-row writes are:

1. **If `d = 0` or `d = 1`:** row `d` is written with
   `-s * (row 2 before the word)`; the other of rows 0 and 1 retains its
   pre-word value.
2. **If `d = 2`:** row 0 is written with
   `s * (row 0 before the word)`; row 1 retains its pre-word value.

Rows 3..15 retain their values. The all-zero vector is a stable special case:
it produces descriptor 4 and leaves rows 0 and 1 at zero. The `d = 2` rule
is genuinely asymmetric with the `d = 0/1` rules: it uses the old value of
row 0 rather than the old value of row 2. The reason for this asymmetry is
unresolved.

The row writes are register contents, not a read-port or render-target
artifact. They remain stable through the tested intervening MFU NOP words
and later ALU batch boundaries. The old note-55 `abs()` discrepancy is
resolved: for example, the positive-x case leaves row 0 at -0.5. A plain
readback stores `00` for that negative value, while `abs()` and a negated
read expose +0.5. Eleven calibrated read forms agreed once the sign was
accounted for. Do not treat the old `abs()` observation as evidence of a
special read path.

Avoid relying on rows 0..2 as the original input coordinates after an
SFU instruction with operand bit 3 set: row 2 holds the descriptor and one
coordinate row is modified. The major magnitude `M` is injected into
multiplier source slot `r2`, while the SFU result is injected into slot
`reg & 7` when that maps to `r0`..`r3`; the result wins if both target
slot `r2`. The direct multiplier source token `sfu` remains a separate
way to obtain the SFU result.

##### Cube/TEX status of the compiler word

The captured compiler word above is consistent with a shader-side
major-axis normalization step, but it does not establish cube-face selection
or face-coordinate orientation. The corresponding cube TEX word is
unavailable. The proprietary compiler is still absent from the AC100 build
(`tools/cgc` reports that the BLOB compiler is unavailable and falls back to
the dummy implementation), and the repository has no cube-map binding path.
The tested TEX instruction and 2D sampler descriptors expose no demonstrated
dimension or face selector. Face selection therefore remains unresolved;
these measurements do not show whether it is fixed-function or shader-side.

See [experiment notes 56..58](https://github.com/i-am-cipher-null/grate/blob/code/fragment-isa-experiments/tests/grate/asm_characterize.experiments.md)
for the selector sweep, multiplier source-slot routing, row-write measurements
and the characterization of selectors 4..7. Notes 59..63 in the same file
cover the perspective-interpolator input path and its repeatability caveats.
The cube/TEX face-selection question remains unresolved.

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

Multiplying a selector by `sfu: rcp r4` gives the corresponding
perspective-correct weight, `(lambda_i / W_i) / D`, within the precision of
the hardware readback. Here `r4` names **SFU operand selector 4**, not an
ordinary ALU row-register read.
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

##### IPL row mapping and same-word visibility

The note 55 probes establish the following mapping for the tested FP20
interpolator path: IPL operand slot k fetches varying component k into row k.
For example, fetching x, y and z in the first three slots populates r0, r1
and r2 respectively.

There is also a same-word visibility constraint. If an IPL fetch and
`sfu: rcp r10` occur in the same MFU word, the SFU uses the row values
that were present before that word's fetch; the newly fetched values are
visible to subsequent MFU words. This was distinguished with pre-seeded rows
whose major-axis reciprocal differed from the reciprocal of the values being
fetched. Treat this as a measured dependency for the tested FP20 IPL and
special-RCP arrangement, not as a complete ordering specification for every
MFU operation.

#### SFU selector 4 and the perspective denominator

The token `r4` in `sfu: rcp r4` is the six-bit **SFU operand selector 4**;
it does not identify an ordinary ALU row-register input. On AR20, this
selector supplies a per-fragment quantity proportional to

$
Q_4 = 2^{16} D, \qquad
D = \sum_i \frac{\lambda_i}{W_i}.
$

Thus `sfu: rcp r4` produces approximately
(1/(2^{16}D)), the common normalization factor for the barycentric
selectors. When clip W is constant across the primitive, (D=1/W), so
(Q_4=2^{16}/W). With unequal vertex W values, it follows the per-fragment
perspective denominator instead of a single vertex's reciprocal clip W.
A fit to the selector-4 measurements held over the tested range
(D=0.125..3.3); the exact internal precision and implementation remain
unresolved (notes 59 and 60).

This result must not be confused with an ALU-visible row register `r4`.
The tests did not find this per-fragment quantity captured in the ALU row
file. The MFU multiplier-source field is another independent mux again:
its source value 4 is not SFU selector 4 and did not expose (Q_4) in the
tested source sweep. The three uses of the spelling `r4` therefore must not
be treated as interchangeable.

##### Fetch-path and run-history caveats (notes 60..63)

For selectors 5..7, an interpolator-fed value is not interchangeable with an
ALU-seeded value that has the same ordinary row readback. The fetched path
carries a small additional W-dependent contribution: in the paired note-63
measurements the paths agreed at (W=1), while at (W=4) the fetched result
was 1..4 capture codes below the seeded result for the four tested magnitudes.
The capture-path gain pair remained exactly 16 codes per octave. The additional
contribution is bounded to about half an octave, but its source is unknown.
It has not been shown to be a simple function of (D), (N), or (T_i).

There is a separate repeatability hazard. Note 63 produced different codes
(212 and 219) for byte-identical seeded cells within one manifest. The
difference followed run history, not the shader sources, arguments, paths,
variant names or cell positions. In one sequence the value changed after a
row-readback variant; a separate bridge sequence did not reproduce that
specific transition, so the trigger is not isolated. The evidence indicates
that relevant device state outlives the assembler process, but does not identify
the state or explain the fetched-path contribution.

Consequently, comparisons between fetched and seeded inputs should be adjacent
and made within the same manifest, with duplicate cells and standard controls.
Preserve the complete source context and raw assembled stream for each run.
Do not interpret a cross-manifest code shift as an operand property without
a within-manifest replication.

#### Fragment z component

**Measured AR20 path (experiment note 52):** `gl_FragCoord.z` is derived from
the ordinary link copy of the vertex position export, not from the LINK
`(z)` modifier or the earlier `sat(NOP)` / r3 recipe.

The vertex shader writes `gl_Position` to export 0. The linker copies that
export into a TRAM row with an ordinary four-lane instruction such as
`LINK fp20, fp20, fp20, fp20, tramN.xyzw, export0` (no `(z)`). The fragment
shader fetches the row's z and w lanes as ordinary FP20 interpolants with
`ipl`. The z lane follows controlled clip-space z values and changes across
the framebuffer when position z slopes across the primitive.

The raw store keeps only a coarse part of the FP20 value. Its high byte was
calibrated for positive normal values as `(fp20 >> 10) - 1`:

| clip-space z | 0.125 | 0.25 | 0.375 | 0.5 | 0.625 | 0.75 | 0.875 | 1.0 |
|:--|--:|--:|--:|--:|--:|--:|--:|--:|
| fetched z high byte | `DF` | `E7` | `EB` | `EF` | `F1` | `F3` | `F5` | `F7` |

With clip z = 0.5 and uniform clip w = 2, the ordinary link fetch reads z =
0.5 and w = 2.0. The D16 depth surface reads `0x9FFF`, corresponding to
window z = 0.625 with the tested viewport scale and bias of 0.5. This supports
the reconstruction:

`window_z = (clip_z / clip_w) * viewport_scale + viewport_bias`

The viewport scale and bias are render state supplied by the driver. The
fragment shader obtains the clip-space inputs from the normal TRAM link and
performs the divide and viewport transform; these values are not supplied by
the LINK `(z)` select. The `r4` perspective-interpolation denominator
described above is a separate value, not the raw clip-w lane of the position
export.

The old r3 expansion is not the measured path: `sat(NOP)` reads zero at the
tested z values, including with the depth pipeline enabled. The one-lane
`(z)` form writes a near-zero code (`0x2000`) to TRAM0.w rather than the
position z, and replaces the ordinary position copy. Do not use it to obtain
fragment z.

The raw readback resolves only a few percent of the FP20 value; it cannot
recover exact lane values or distinguish negative z from zero. The w = 2
measurement used uniform w, so varying-w interpolation is not characterized
here. See the [fragment hardware experiment notes](https://github.com/i-am-cipher-null/grate/blob/code/fragment-isa-experiments/tests/grate/asm_characterize.experiments.md),
note 52, for the probe setup and results.

## Experimental evidence and tooling

For fragment-ISA experiments, preserve the complete generated source context
and the raw assembled command stream alongside the hardware result. The
`tools/fsraw` diagnostic prints the Host1x stream with word offsets and the
per-batch PSEQ, MFU schedule, TEX, ALU schedule, complement and DW words; it
also prints each MFU word and all eight words of each ALU packet. Its symbolic
disassembly is deliberately labelled a hypothesis. The raw dump describes
what the assembler generated for submission; it is not a GPU readback.

Neither the assembler nor the disassembler is yet a complete, lossless ISA
interface. In particular, the existing raw ALU syntax does not author all
eight packet words, and schedule words are generated by the assembler rather
than directly specified in source. The disassembler still omits some fields,
so its symbolic text must not replace the raw words. The separate
[assembler/disassembler audit](https://github.com/i-am-cipher-null/grate/blob/code/fragment-isa-experiments/docs/assembler-audit.md)
records the measured coverage and remaining limitations.

## TEX instruction word encoding

TEX reads S, T, R and LOD from the first row of the pixel packet, using the
register group selected by bit 4, and writes the sampled RGBA result to the
register pair selected by bit 5. The tested fetch writes the four components as
FX10 halves.

|   Bits | Meaning                              |
|-------:|:-------------------------------------|
|     13 | explicit LOD                         |
| 14..31 | unknown                              |
|     12 | LOD bias                             |
|     11 | no effect in tested fetches          |
|     10 | fetch enable                         |
|   6..9 | no effect in tested fetches          |
|      5 | destination pair select              |
|      4 | coordinate/LOD register group select |
|   0..3 | sampler index                        |

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

|  Value | Meaning (S, T, R, LOD order) |
|-------:|:-----------------------------|
|      1 | R2, R3, R0, R1               |
|      0 | R0, R1, R2, R3               |

Bit 5 selects the destination pair:

|  Value | Meaning |
|-------:|:--------|
|      1 | R2-R3   |
|      0 | R0-R1   |

The RGBA channel order was confirmed through both destination pairs,
r0-r1 and r2-r3. The four components retain RGBA order in the tested
readback paths.

Wrapping and filtering modes are configured in sampler state, separately
from the TEX instruction word. They are not encoded by the fields documented
here.

### LOD selection

Bit 12 selects LOD bias. When bit 12 is clear, bit 13 selects explicit LOD.
With both bits clear, the LOD register is ignored and the texture unit uses a
derivative-based estimate. When both bits are set, bias takes precedence in
the tested hardware.

The derivative-based path is handled by the fixed-function texture/sampler
hardware. The exact derivative reduction, mip-level thresholds, and filtering
policy are outside the scope of this instruction-word reference.

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
or inert in every texture mode. Bits 14..31 remain undocumented; their
behavior has not been established by the current reproducible measurements.

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


| Render target | Usage                    |
|--------------:|:-------------------------|
|             0 | Depth buffer             |
|             1 | Color buffer             |
|             2 | Stencil buffer           |
|             3 | Auxiliary shader surface |
|             4 | Register-spill surface   |
|             5 | Additional color surface |
|         6..12 | MRT encodings            |

In the tested AR20 path, RT3 was an auxiliary shader-accessible surface, not a
fixed-function coverage target. An AR20 write to RT5 was tested when explicitly
bound (`GRATE_RT5=1`); working AR20 writes to RT6..12 were not demonstrated.

## PSEQ instruction word encoding

PSEQ (Program Sequencer) fetches a selected render target into one of the
two row-register pairs. The observed fetch encoding is
`0x00800008 | (N << 16) | (pair << 1)`, where N is the render-target index
and pair is 0 for r0/r1 or 1 for r2/r3. For example, `0x00820008` selects
RT2 and r0/r1.

|   Bits | Meaning                 |
|-------:|:------------------------|
| 25..31 | unknown                 |
|     24 | partial-write path      |
|     23 | fetch enable            |
| 20..22 | unknown                 |
| 16..19 | render-target index     |
|  4..15 | unknown                 |
|      3 | fetch enable            |
|      2 | unknown                 |
|      1 | destination pair select |
|      0 | unknown                 |

Both bits 3 and 23 must be set for the ordinary fetch. Clearing either one
suppressed the fetch and left the destination registers at their previous
values.

#### Destination registers select:

| Value | Meaning   |
|------:|:----------|
|     1 | R2 - R3   |
|     0 | R0 - R1   |

Bits 16..19 select the render target. Successful round-trips were measured
for RT1, RT3, RT4 and RT5 when those surfaces were bound. Unbound target
indexes returned unstable partial values and should not be treated as defined
results.

### Render-target formats

The format tests used RT5. RGBA8888 preserved all four components in R, G, B,
A order. The tested narrower formats converted as follows:

| Format   | Result             |
|:---------|:-------------------|
| RGBA8888 | RGBA               |
| A8       | (0, 0, 0, A)       |
| L8       | (L, L, L, 1.0)     |
| RGB565   | RGB 5/6/5, A = 1.0 |

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
the other fields in this form is unknown.

### Addressing limits

Position-dependent tests used a genuine 64x64 source surface. One draw
wrote a distinct X/Y tag at every pixel; a later draw fetched the surface
without writing to it and stored the fetched value to a separate observation
target. All 4096 pixels matched the expected map. A source map shifted by 16
columns was read back with the same shift, showing that the fetch uses the
fetching fragment's own window position rather than an address left by the
preceding DW store.

The identity map was reproduced through the tiled framebuffer, linear and
tiled RT3, and a linear source allocation with 16 texels of row-pitch padding.
A further test used a tiled RT3 source allocated 16 texels wider than the
64x64 target: it retained the identity map, and its shifted-source control
retained the same 16-column shift measured with the linear padded source.
In these tested layouts, increasing the tiled allocation width did not change
the observed row mapping. This does not establish behavior for other tile
layouts or padding amounts.

A later draw read the earlier draw's writes without an explicit flush between
them, provided both draws were in the same submission. The complete output
map matched the flushed control, and a no-fetch control showed that the fetch
had actually run. Three ordered draws in one submission also showed that the
fetch read the most recent preceding write. Within the fetching draw, a store
to the source before the fetch was visible to it, while a store after the fetch
did not change the value already fetched. These results establish ordering
for the tested instruction and draw sequences, not a general synchronization
rule.

The address-generation mechanism is not established: the tests do not
distinguish position registers from the fragment's own target coordinate.
Cross-job visibility, a source that is also the observation target, and other
write-after-read arrangements remain untested. Targets larger than the tested
64x64 region, and tiled layouts with different dimensions or padding, also
remain untested. The RGB565 control is not an address-map proof because its
reduced precision loses the X coordinate byte.

See [fragment hardware experiment notes, sections 17 and 23](https://github.com/i-am-cipher-null/grate/blob/code/fragment-isa-experiments/tests/grate/asm_characterize.experiments.md#23-pseq-visibility-the-flush-boundary-write-ordering-and-a-padded-tiled-source)
for the test arrangements and recorded results.

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
