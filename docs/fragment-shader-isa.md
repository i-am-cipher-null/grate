## Overview

The Tegra fragment shader ISA is not as straight forward as [[Vertex Shader ISA]].

The fragment shader is separated into five different instruction streams:
* PSEQ - fetching data from the memory and feeding it to the pipeline as registers data or instructions
* MFU - multi-function unit, varying interpolate and special functions
* TEX - texture lookups
* ALU - arithmetic logic unit
* DW - writing to the output surface / buffers

The different units seems to be synchronized by separate timing streams.

Each behaviour below carries one of four labels. **Confirmed** means measured by a
controlled experiment or unambiguous existing evidence, cited by note number.
**Inferred** means strongly supported but not isolated. **Unknown** means
unresolved. **Not required for GLES2 scope** marks deliberately excluded
behaviour. Measured statements cite the experiment that established them, by
section, in
`tests/grate/asm_characterize.experiments.md` (for example "notes 24"). This file is the
canonical ISA description and the one to edit. A longer version of the same document,
carrying the full evidence tables and the retraction history, is maintained in the
documentation snapshot (`docs_snap/fragment-shader-isa.md`) in this repository; the
two have to agree on every claim both make.

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

### Compiler-facing constraints

The emitter needs the following rules; all are **Confirmed** unless marked.

* A batch holds 1..3 ALU packets of four slots (12 instructions). The `0x801`
  word's low two bits are the **packet** count, not an instruction count, and a
  fourth packet is refused by the assembler (note 32).
* The address fields are unit-local: the ALU address counts **packets** and the
  MFU address counts MFU words, not batches. An MFU batch is up to three words
  in series; an ALU batch is up to three packets.
* One DW position exists per batch; additional ALU packets do not add DW slots
  (note 42). Two DWs in one batch cannot be encoded with this assembler.
* Stage order is PSEQ, MFU, TEX, ALU, DW. An ALU result written in a batch is
  **not** available to a TEX instruction of that same batch; texture
  coordinates must come from the row registers the earlier group wrote
  (note 11).
* Within a packet, slots are independent: there is no same-packet bypass in a
  multi-packet batch, and a slot reading its own result gets zero (notes 28,
  32). In a one-packet batch, a slot can read another slot's result from any
  later slot (note 28).
* Packing more than one packet per batch makes dependencies packet edges: a
  packet sees its predecessor, and packet 0 sees the group's last packet in the
  tested arrangements (notes 36, 39, 43-47). Rows are the only storage that
  survives a group boundary (notes 28, 31).
* The assembler limits a program to 64 exec batches and 64 ALU packets; the
  four slots of a packet count once (note 7).
* The `alu_buffer_size` group size is driver state, not encoded in the program.
  Cross-group visibility therefore cannot be chosen by the compiler; it is
  fixed by the draw state the program ships with.
* **Unknown:** whether the MFU/TEX per-batch counts use the same packet unit as
  ALU, and what schedule-word bits 8..28 mean. **Not required for GLES2:** MRT,
  multi-chunk execution and render-target formats beyond colour.

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
|     56 | saturate result, clamp to 0..1 (note 48) |
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

Non-zero value written to the KILL register discards the fragment, zero keeps it alive and doesn't resurrect the killed fragment. Destination register write mask (low/high halves) is ignored. **Confirmed** (note 18): a zero result with an `(eq)` condition discards (the condition turns it into 1.0), an unconditional non-zero write also discards, and the condition may be on the kill instruction itself - it does not need its own packet. A condition register written earlier in the **same batch** is not visible to that instruction (it reads zero), so a compare has to live in an earlier batch or be folded into the kill write.

The blob's discard path does not use the `kill` register at all: it writes coverage (rt3), stencil (rt2), depth (rt0) and colour (rt1) in four batches, with the condition on a row register. On AR20 the measured effect of a shader kill write is that **depth is still written by the rasterizer** (killed fragments included), while the fixed-function stencil `REPLACE` stops and the program's own `DW: store stencil` performs the late update (note 18). A discard-capable GLES2 program with a stencil buffer must therefore emit the stencil store; coverage is a scratch surface and is not consumed by the fixed function (note 24).

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

#### ALU result registers (`alu0..alu3`)

Registers 24..27 are the result latches of the four scalar slots, and they are readable
as operands (notes 28). A read is not a read of a retired register:

| where the reader sits | `aluK` returns |
|:--|:--|
| same packet, group has one packet | that packet's slot-K result, from any writer slot; a read in slot K itself returns 0 |
| same packet, group has more than one packet | 0; a packet does not see its own results |
| packet k in a group of M=2 or 3 packets, one packet per batch | packet `(k-1) mod M`'s slot-K result, for any reading slot, in the tested arrangements |
| M=4, size 2, packed 3+1 or 1+3 | full cycle for all four latches, including packet 3 to packet 0 from every reader slot |
| M=4, size 3, packed 2+1+1, 1+2+1 or 1+1+2 | full cycle for all four latches, including packet 3 to packet 0 from every reader slot |
| M=4, size 4, one packet per batch (1+1+1+1) | full cycle for all four latches and every wrap reader slot |
| M=4, size 2, packed 2+2 | full cycle for all four latches, including packet 3 to packet 0 from every reader slot (note 47; the earlier "only alu3 wraps" report came from probes that wrote packet 2's slots) |
| M=5, size 2 or 3, eight tested distributions | full cycle for all four latches and every packet-4-to-packet-0 wrap reader slot |
| M=5, size 4, four tested distributions (2+1+1+1, 1+2+1+1, 1+1+2+1, 1+1+1+2) | full cycle for all four latches and every packet-4-to-packet-0 wrap reader slot |
| M=6, size 2 or 3, four tested distributions (2+2+2, 3+3, 1+2+3, 3+2+1) | full cycle for all four latches and every packet-5-to-packet-0 wrap reader slot |
| across a group boundary (`alu_buffer_size=1`) | the previous group's last packet's slot-0 result, readable only from slot 0 |
| across a group boundary (`alu_buffer_size=2` or `3`) | 0, from any batch of the previous group, in the shapes tested |
| across a group boundary (`alu_buffer_size=4`) | 0 in tested cases: alu0 written by each of the four packets, plus selected alu1 cases from packet 3; not a complete latch/slot matrix |

The value is format-specific: a `.l*` (fx10 low-half) write fills the `aluK.l` view and
leaves a bare `aluK` (fp20) read at zero, while an `.lh` write fills the bare read. A
`.*h`-only write fills neither.

The across-group row is a forward of the *previous group's last packet's* slot-0 result,
not a saved register (notes 31, 36): a group whose last packet's slot 0 wrote nothing
carries nothing, the value is one group deep, and the writer's row destination does not
matter. This carry was measured for a group of one batch (`alu_buffer_size` 1); no
cross-group forward was seen at sizes 2 or 3 in the tested shapes (note 36).
At size 4, no carry was seen for the tested alu0 writer positions and selected
alu0/alu1 reader-slot combinations, but that was not a complete latch/slot
matrix (note 40). A latch therefore cannot be treated as storage across a
group boundary, though a row register can, which is what the assembler emits
when it needs one. That is why an earlier
reading of "the result registers always read zero" was wrong - those probes read
`aluK` bare from packets whose slot K wrote a low half, one batch away.

Destination codes 24..27 are accepted by the hardware and write no row register, but they
do not choose the latch: a write issued from slot K appears on slot K's latch whichever
of 24..27 the destination field names (notes 28). The assembler refuses `aluN` as a
destination, so this was measured with the destination field patched into an assembled
word.

The per-batch ALU schedule word (uploaded at `0x801`) carries a **packet count** in bits
0..1 and the packet address in bits 2..7; a packet is four instruction slots and a batch
may hold up to three of them, so twelve instructions per batch (notes 32). The assembler
sets the count from the number of `ALU:` blocks the batch has and refuses a fourth emit
(`Over 3 ALU instructions per exec batch`).

Nothing above bit 7 is emitted by the assembler. Setting bits 29 or 30 globally, or
masking either bit (or both) on one batch at a time, changes neither forwarding nor the
DW result in the tested AR20 cases (notes 30, 34, 35). The per-batch tests mask each of
the three batch positions in turn across five shapes and confirm the uploaded `0x801`
words; they do not establish that these bits are inert for every possible schedule.

The latch forwarding rule is group-scoped. In the complete rotations measured,
a read of aluK in slot S of packet k is served by the slot-K result from packet
`(k-1) mod M`, where M is the total number of ALU packets in the group; the
reading slot may be any slot. A one-packet group has the same-packet cross-slot
path described above, with a read in the reader's own slot returning 0. In a
multi-packet group a packet does not see its own results.

M=2 and M=3 form complete rotations in the tested arrangements. M=4 is also
complete in every tested packing. At size 2, the 3+1 and 1+3 distributions have
all three forward edges for all four latches, and all four
packet-3-to-packet-0 wrap latches are readable from every reader slot (note 39).
At size 3, the 2+1+1, 1+2+1 and 1+1+2 distributions have the same complete
four-latch cycle (note 43). At size 4, the 1+1+1+1 arrangement has been measured
across all four latches and all four wrap reader slots, not just alu0
(note 43). Notes 38 and 39 reported that size-2 2+2 wrapped only through alu3,
but those probes wrote `B0`, `B1` and `B2`, which are packet 2's slots, not
packet 3's. Note 47 varies packet 3's writer slot, the read latch and packet
0's reader slot over all 64 combinations and finds the full four-latch cycle:
all sixteen writer-slot/reader-slot diagonal cells return the writer's value
and all 48 cross-latch cells return 0. The writer slot still selects the latch,
so the wrap cannot deliver a value to a different latch index.

The five-packet follow-up tested all eight legal two- and three-batch
distributions: 2+3, 3+2, 1+1+3, 1+2+2, 1+3+1, 2+1+2, 2+2+1 and 3+1+1.
Every one has all four forward edges for all four latches and the complete
packet-4-to-packet-0 wrap from every reader slot (note 44). In that wrap matrix,
packet 4 writes through slot N to test aluN, while packet 0's reader slot is
varied independently; other writer-slot/latch cross-combinations were not
tested. Own-packet reads and every non-predecessor source/reader pairing tested
return 0. These results show that two multi-packet batches, the absence of a
singleton batch, both wrap
endpoints being in multi-packet batches, and the tested batch-order mirrors are
not sufficient to reproduce the restriction once reported for the four-packet
2+2 shape, which note 47 shows was a slot-addressing artifact. They do not
establish the behaviour of every possible group size or distribution.

The six-packet follow-up tested 2+2+2 at size 3, 3+3 at size 2, and 1+2+3 and
its batch-order reverse 3+2+1 at size 3. All four have the complete cycle: five
forward edges for all four latches and every packet-5-to-packet-0 wrap
latch/reader-slot combination (note 45). Own-packet reads, the twenty-four
non-predecessor source/reader pairs per shape and the conflict cases all agree
with the same cyclic rule, and the M=4 2+2 and M=5 controls in the same runs
reproduce their earlier words. In particular 2+2+2 is the closest six-packet
analogue of the M=4 2+2 shape - uniform two-packet batches at a different
packet count - and it is complete. The four-packet 2+2 shape is complete as
well once its packet-3 slots are addressed correctly (note 47), so no tested
four-, five- or six-packet arrangement has a restricted wrap. These
measurements do not establish a general rule for untested group sizes or
distributions.

Five packets over four batches force exactly one two-packet batch, so the four
size-4 distributions move that batch from first to last while holding the
packet and batch counts fixed: 2+1+1+1, 1+2+1+1, 1+1+2+1 and 1+1+1+2. All four
have the complete cycle, with the four forward edges for all four latches and
all sixteen packet-4-to-packet-0 wrap latch/reader-slot cells returning the
predecessor's value (note 46). 2+1+1+1 puts packet 0 in the pair's batch, and
1+1+1+2 is the only one where packet 4 shares the last batch with packet 3;
neither placement restricts the wrap. The M=4 size-2 2+2 shape, formerly the
one apparent exception, is complete as well (note 47). Every tested four-,
five- and six-packet arrangement therefore has the full wrap. No mechanism
beyond the measured slot/latch coupling is claimed.

Dependent operations can chain one hop per packet in these complete
arrangements. Packet 0 can read packet 2 in a three-packet group, packet 0 can
read packet 3 in every tested four-packet arrangement, packet 0 can read
packet 4 in all twelve tested five-packet
arrangements, and packet 0 can read packet 5 in all four tested six-packet
arrangements. This revises section 32's "previous packet, forward only": its
backward probe changed the latch index and reading slot at once, so it did not
reveal the wrap (note 36).

The rotation crosses batch boundaries inside a group. The tested M=2 and M=3
one-packet-per-batch cases form complete cycles; the M=4, M=5 and M=6
distributions and their coverage are listed above. Do not infer a complete
cycle for an untested packing from the total packet count alone.

The group-boundary carry is separate. The positive path is the previous group's
last packet's slot-0 result, read from slot 0 in the next group, and it was
established at alu_buffer_size=1 (notes 31, 32, 36). No carry was seen at
sizes 2 or 3 in the tested shapes. At size 4, the follow-up tested alu0 writes
from each of the four prior-group packets to next-group slot 0, plus a packet-3
alu0 write read in slot 1 and selected packet-3 alu1 reads in slots 0 and 2;
all returned 0 (note 40). That is a negative result for those combinations,
not a complete test of all four latches and all four reader slots. A latch is
not established as storage across any group flush; row registers remain the
proven storage path.

The recv/send accumulator is a separate, packet-local path (notes 13, 16, 37,
41). It accumulates from earlier slots in the same packet; the tested
cross-packet pairings do not transfer, even when the receiver is in slot 1 and
the ordinary latch path can see the sender's packet. In particular, packet 0
slot 0 send to packet 1 slot 1 recv returns only packet 1's own previous-slot
contribution; with that previous slot disabled it returns the receiver's
unaccumulated value. The reverse direction and an alternate packet-0 slot-3
sender also show no transfer. A same-packet positive control still works, and
an ordinary alu0 read still crosses the packet boundary (note 41). A send does
not disturb the ordinary latch path. This closes the previously singled-out
(0,1) pairing, but not every possible cross-packet slot mapping; the general
cross-packet marker path remains unproven.

#### Addition disable

When bit "addition disable" is set, the Multiply-Add operation turns into two multiplies. The fx10 result of each multiply goes to the low/high subregisters of the destination register.

| write high subregister | write low subregister | destination low | destination high |
|:----------------------:|:---------------------:|:---------------:|:----------------:|
|            1           |           0           |      rC\*rD     |       rA\*rB     |
|            0           |           1           |      rA\*rB     |       rC\*rD     |

#### Result saturation

Bit 56 clamps the result to 0.0 .. 1.0. Measured on AR20 with `(sat)` (note 48):
the clamp is applied after the result scale and before the condition code, on
`MAD`, `MUL` and `MAX`, in both the fp20 (`.lh`) and fx10 (`.l*`) write formats.
2.0 stores as 1.0, -0.5 as 0.0, and an in-range value is unchanged. `MIN` shares
the path by inference only; the accumulate and condition-register write paths
were not combined with the bit.

A backend can spell a clamp with `(sat)` instead of a `MIN`/`MAX` pair. GLES2
has no saturate operation, so nothing in the required subset depends on the
bit; it is available and measured rather than required.

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

The three `PRE*` opcodes are a hidden first step: they do not produce a value,
and the matching `EX2`/`SIN`/`COS` word consumes their intermediate. The two
words may be split across batches in any way, must be given the **same operand**,
and omitting the `PRE` word silently returns a wrong answer (`ex2(-1)` returns
1.0 instead of 0.5, `cos(0)` returns 0 instead of 1.0) - confirmed, note 7.
The `PRE` result read through the `sfu` source is not a reusable function of the
argument. Row 4 supplies the fragment's reciprocal w on a 2^16 scale, and the
multipliers accept only rows 0..3 as row sources (note 6).

#### var0..3:

The varying id corresponds to the TRAM component: var0 is TRAM.x, var1 is TRAM.y, var2 is TRAM.z, var3 is TRAM.w.

| Bits | Meaning                  |
|-----:|:-------------------------|
| 3..6 | TRAM row index to fetch from, 0..15 |
| 1..2 | opcode                   |
|    0 | saturate                 |

The 7-bit slot holds a **4-bit** row index (bits 3..6), not a 5-bit one. The
index is dense and assigned by the compiler (the blob's two linked varyings are
rows 0 and 1), not the GLSL `location`; the linker's own row field appears only
on the vertex side (confirmed, note 10). Rows 0 and 7 fetch different data, so
the rows do not alias. Multiple `LINK` words compose per component: writing `x`
from one line and `y` from another renders the same frame as one line writing
both (confirmed, extended document 12.9).

The `.fx10` fetch's half layout is **Unknown** in the tested words: `fx10.l`
gives `(value, 0)` while `fx10.h` gives `(value, value)`, and a component written
in the other format reads zero (note 10). `VAR2` is therefore usable only where
the ordinary fp20 path is not needed, and a backend should prefer `VAR1` for
GLES2 varyings.

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
|     0 | writes no row register and supplies no weight (confirmed, note 4) |
|     1 | barycentric weight |
|  2..3 | also supply the weight; indistinguishable from 1 from outside the unit (confirmed, note 4) |
|     4 | row register 0     |
|     5 | row register 1     |
|     6 | row register 2     |
|     7 | row register 3     |

Destinations 4..7 are a plain row index: each writes exactly one register and
leaves the other three alone (confirmed, note 4).

##### mul0..1 source registers:

The SFU result is evaluated first and is available to use by MUL's via "SFU result" register.

|  Value | Meaning                 |
|-------:|:------------------------|
|   0..3 | row register 0..3       |
|   4..6 | internal state of the unit, not a row or global register; deterministic (unknown what it is; note 4) |
|      7 | zero (confirmed, note 4) |
|      8 | zero (confirmed, note 4) |
|      9 | zero in a word without a varying fetch, `0x4100` in any word that fetches one (partially identified; note 4) |
|     10 | SFU result              |
|     11 | barycentric coef 0      |
|     12 | barycentric coef 1      |
|     13 | 1.0                     |
| 14..15 | zero (confirmed, note 4) |

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

There is also dependency on the linker: it should perform the "magic" write to the TRAM0.w with "VEC4 select = VEC4.z". **Unknown:** what supplies that VEC4.z input. Every `(z)` link word tested (all columns fp20, different swizzles, exports and rows) read zero, while the same linker without `(z)` read the export, so the bit switches the link's source to an input these programs do not populate (note 10). `gl_FragCoord.z` depends on this and is not established end to end.

## TEX instruction word encoding

The TEX instruction takes the texture coordinates and LOD bias from the first row of the pixel packet (i.e. R0-R3 registers) and writes the sampled data to that first row as well. 

|   Bits | Meaning                               |
|-------:|:--------------------------------------|
| 14..31 | bit 14 is not a projective divide; 15..31 inert (confirmed, note 11) |
|     13 | explicit LOD                          |
|     12 | enable bias                           |
|     11 | inert (confirmed, note 11)            |
|     10 | enable                                |
|   6..9 | inert (confirmed, note 11)            |
|      5 | sampled data destination regs select  |
|      4 | texcoords / lod regs select           |
|   0..3 | sampler index                         |

"Inert" is scoped to what the harness can bind: `RGBA8888`, `dxt1`, `etc1` and
`d16` surfaces, all linear 2D. Setting bits 6, 7, 8, 9, 11, 15, 16, 20, 24, 28
or 31 on a sample that demonstrably moves with bit 5, bit 4 and the coordinate
leaves the result byte-identical (note 11). Cube maps and shadow samplers have
no binding path in the test driver, so nothing here covers them. Bit 14 does not
halve `s` when R is 2.0, so it is not the NV30 projective divide. R has no
observable effect on these descriptors (note 19).

A `D16` descriptor samples as zero in every component rather than reading the
surface's 16-bit texels; the RGBA path over the same coordinates and program
returns four distinct colours, so this is the depth-format path, not the
coordinates or destination registers. **Unknown** whether another descriptor
reaches the texels (note 19).


#### Texture coordinates and LOD registers select:

The texture coordinate components (S, T, R) and level-of-detail bias are loaded from the row registers as fp20's.

|  Value | Meaning (S, T, R, LOD order) |
|-------:|:-----------------------------|
|      1 | R2, R3, R0, R1               |
|      0 | R0, R1, R2, R3               |

The fourth register from that select (R3, or R1 when the select is 1) carries
the level of detail. Which kind depends on bits 12 and 13, and they do not
combine (confirmed, note 15):

| bit 13 | bit 12 | meaning |
|-------:|-------:|:--------|
| 0 | 0 | LOD from the unit's own derivative estimate; the register is ignored |
| 0 | 1 | **bias**: the estimate plus the register, clamped at 0 (what `TXB` emits) |
| 1 | 0 | **explicit**: the register is the LOD, no estimate involved |
| 1 | 1 | bias wins - bit 13 is ignored |

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
row registers) cannot be populated because `alu_buffer_size = 2` hangs the draw. A class-1 store with no depth surface bound, and write modes 0, 2 and 3, leave the colour
target at whatever it already held.

The assembler provides one DW instruction slot per exec batch; additional ALU
packets in that batch do not add DW slots. On AR20, earlier class-2
colour/spill/MRT tests found that a first-batch DW can write its target with
the group's final row contents while a competing class-2 target remains at its
prior value in the distinguishable cases (notes 29, 33). The later position
and three-word tests refine that rule: a lone DW in a later batch did not
produce its expected row payload, but its RT1 readback changed from the
nonzero clear to zero; and a three-word sequence also produced zero on RT1
where the no-DW baseline was the nonzero clear (note 42). Zero-cleared RT4/RT5
readbacks cannot distinguish an untouched target from a zero write.

Mixed class-1 depth and class-2 colour pairs are clearer: in both instruction
orders and in both tested packings, the first word's target receives its
expected group-row payload and the second word's target is written with zero
(depth and colour readbacks both distinguish zero from their untouched
baselines). The three-word same-target case likewise reads zero on RT1 rather
than its no-DW clear, so it is not evidence that the store was suppressed.
Thus a universal "first DW wins and every later DW is dropped" rule is not
established; behaviour depends on target class and placement (note 42). Two
DWs in one batch/packet cannot be tested through this assembler: its
exec-batch grammar has one DW instruction position. For a backend, one DW
per group remains the conservative choice for a predictable nonzero payload,
not a claim that later DWs are universally discarded.

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

## GLES2 operation checklist

The table answers, for each operation the required subset needs: which
instruction emits it, where its operands come from, what it reads and writes,
where it can run, and how well it is established. Row/column details are in the
sections above.

| GLES2 operation | instruction | operands and format | stage / batch | constraints | status |
|:--|:--|:--|:--|:--|:--|
| varying interpolation | MFU `ipl` (`varN` slots) | TRAM row index bits 3..6, `VAR1` fp20 or `VAR2` two fx10 | MFU; weights earlier in the batch or an earlier batch | both weights must be written; a fetch uses only earlier writes in its batch | **Confirmed** (notes 4, 10) |
| flat varying / provoking vertex | `(dis)` on every link column | linker state, not the shader word | MFU | per-component `(dis)` not established | **Confirmed** for all-columns; **Unknown** for one column (note 26) |
| special function | MFU `RCP/RSQ/LG2/EX2/SQRT/SIN/COS/FRC` plus `PRE*` | one row register, fp20; result in place or via `sfu` source | MFU | `PRE` and main must share the operand and may split across batches | **Confirmed** (note 7) |
| varying/constant read | MFU row source, ALU `uN`/`rN` | uniform 32..63, rows 0..15, row 4 = reciprocal w | MFU/ALU | rows rewritten by the same group read their old value | **Confirmed** (notes 4, 6) |
| add / multiply / multiply-add | ALU `MAD` | `rA*rB + rC*rD`, four operand modifiers | ALU, any packet | result scale 0..3; write masks choose fp20 vs fx10 | **Confirmed** (note 1) |
| multiply without addend | ALU `MUL` (bit 59) | same fields, addend dropped; two products in the two halves | ALU | `.l*`/`.*h` write both halves, `.lh` keeps one | **Confirmed** (note 1) |
| min / max | ALU `MIN`/`MAX` | `min/max(rA*rB, rC*rD)` | ALU | operand behaviour not isolated from the encoder's description | **Inferred** |
| compare / select | ALU condition codes plus `(eq)/(gt)/(ge)` | condition applies to the ALU result, 0.0 or 1.0 | ALU | condition register written in the same batch reads zero | **Confirmed** (notes 13, 18) |
| clamp to 0..1 | ALU `(sat)` (bit 56) | after result scale, before condition | ALU | applies to fp20 and fx10 write formats | **Confirmed** (note 48) |
| texture sample | TEX | coordinates S,T,R + LOD from the selected row pair; result two registers of four fx10 | TEX; coordinates from a prior group | bits 6..9, 11 and 15..31 inert on the tested 2D descriptors; sampler index in bits 0..3 | **Confirmed** (notes 2, 11, 15) |
| explicit / bias LOD | TEX bits 13 / 12 | fourth row register | TEX | bit 12 wins when both set | **Confirmed** (note 15) |
| discard | ALU write to register 76 | any non-zero result discards | ALU | write mask ignored; condition on the instruction or an earlier batch | **Confirmed** (note 18) |
| depth / stencil side effects of discard | blob's four-store sequence: coverage rt3, stencil rt2, depth rt0, colour rt1 | DW per batch | DW | depth still written by the rasterizer; program must write stencil; coverage is scratch | **Confirmed** (notes 18, 24) |
| fragment colour output | DW `store rt1, r0/r1 or r2/r3` | payload `r2.lo, r2.hi, r3.lo, r3.hi`; RGBA8888 conversion | DW | one DW position per batch; one DW per group for a predictable payload | **Confirmed** (notes 24, 29, 42) |
| fragment position | row registers 72/73 | fx10 in the high half; `x + 8192`, `8192 + height - 1 - y` | ALU operand | offset not separable from the 8-bit conversion | **Confirmed** formula, **Inferred** offset (note 8) |
| polygon face | row register 75 | fx10 low half: +1.0 or 0.0 | ALU operand | fp20 and high-half reads are zero | **Confirmed** (note 8) |
| `gl_FragCoord.z` | r3 fetch plus the VEC4.z link write | two fp20 halves recombined by `1/1000 + max(0,r3.lo)/4000 + max(0,r3.hi)/4` | MFU fetch, later ALU | needs a populated VEC4.z input that no tested link produced | **Unknown** (note 10) |
| integer / bitwise / shift | - | - | - | outside GLES2 | **Not required** |
| cube maps, shadow samplers, projective divide | - | - | - | no binding path, bit 14 not a divide | **Not required / Unknown** |
