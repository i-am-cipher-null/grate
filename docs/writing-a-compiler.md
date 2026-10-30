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
* **Derivatives, and therefore `textureGrad`, are out of reach** unless
  computed per-quad by hand: there is no quad/neighbour data path. Texture LOD
  is chosen by the TEX unit itself (`TEX` uses the unit's own LOD selection,
  `TXB` takes a bias from `r3`), and it can also be forced outright: TEX bit 13
  with bit 12 clear reads an *absolute* LOD from `r3` (section 10.4). So
  `textureLod` with a LOD the shader can compute is expressible; only the
  implicit-gradient form is not.

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
| `textureLod` | `TEX` with bit 13, LOD in `r3` | bit 12 must be clear; only reachable through a raw word today (section 10.4) |
| `store_output` | `DW` | payload must be in `r2/r3` or `r0/r1` |
| `load_input` (varying) | `MFU ipl` | one `var` slot per component; precision decides slot count |
| `load_frag_coord` | `posx`/`posy` + the `r3` z expansion | z needs the documented `1/1000 + max(0,r3.low)/4000 + max(0,r3.high)/4` sequence, i.e. ~6 ALU slots |

The `PRE`/main pairs are one operation in two words and this is now measured
rather than assumed (4.5): the two words may be in the same batch, consecutive
batches, or separated by an empty batch, but they must be given the same
operand, the `PRE` result cannot be captured as a value, and dropping the `PRE`
word silently produces a wrong result. So selection and scheduling have to agree
only on the *pair* - the words are free to move otherwise. The same is true of the barycentric prologue: `RCP r4` + two
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

**Operand rC silently reads zero when rA or rB is a row register.** Measured
with `MAD r3.*h, rA, rB, rC`, which computes `rA*rB + rC`, all rows fed so the
exact answer is `1*1 + (-0.5) = 0.5`:

| rA | rB | rC | result | reading |
|:--|:--|:--|:--|:--|
| `u4`=1.0 | `u5`=1.0 | `u6`=-0.5 | `0x7F` = 0.5 | works |
| `u4`=1.0 | `#1` | `u6`=-0.5 | `0x7F` = 0.5 | works |
| `r2`=1.0 | `u5`=1.0 | `u6`=-0.5 | `0xFF` = 1.0 | **c dropped** |
| `r2`=1.0 | `#1` | `u6`=-0.5 | `0xFF` = 1.0 | **c dropped** |
| `u4`=1.0 | `r2`=1.0 | `u6`=-0.5 | `0xFF` = 1.0 | **c dropped** |

An *immediate* rC is still honoured when a row register is in rA/rB - that is
what `MAD r3.l*, r2, #1, -#1` in `fs_mfu_tests.txt` relies on. The rule for a
backend is therefore: **with a row-register source, the constant addend must be
an immediate (`#0`, `#1`, `-#1`) or an embedded constant, never a uniform.** A
uniform rC is read as 0 and the shader is silently wrong by one addend. Nothing
about it looks wrong at assembly time, which makes this the most dangerous item
found so far.

A row register in rC is not read either: `MAD r3.*h, u4, #0, r2` with `r2 = 0.25`
and `u4 = 1` returns 0 rather than 0.25. So rC must be a uniform or an immediate
whenever a row register is involved; only rA and rB can come from the row file
beside a constant.

**Re-measured with the right value format: rC does work, and the table above is
an artefact.** A clean probe - `MAD r6.lh, r4, u1, u2, #1` with `r4 = 1.0`,
`u1 = 1.0`, `u2 = -0.5` - returns **0.5** (`0x7F`), and with `c = u0 = 1.0` it
returns 1.0 (2.0 clamped). So a uniform rC **is** honoured beside a
row-register rA, and no operand-port restriction exists. The earlier "c dropped"
rows were produced by priming row registers with the wrong value format. Kept
below for the record, but do not act on it. Original note: It was measured
before the `.lh` rule was understood, with row registers primed by half-only
writes - which store a truncated fp20. Under a correct reading of those runs the
"c dropped" rows returned 1.0, and neither model produces 1.0: a correct rA with
c dropped would give about 0.0078 (`0x02`), and a correct rA with c applied would
give 0.5. So those rows are unexplained, and **the rC-with-row-register rule is
unconfirmed** - re-measure it with `.lh`-written operands before a backend acts
on it. What stands from independent evidence: an immediate rC is honoured
(`fs_mfu_tests.txt` relies on `MAD r3.l*, r2, #1, -#1`), and embedded constants
(`imm0..imm2`) are a legal rC value.

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

### 4.4 What is still missing for a scheduler

**Open:**

1. ~~**TEX result -> ALU distance.**~~ - **closed**, see 4.3: the sample is
   visible to the ALU in the same batch and in the next one, and ALU-computed
   fp20 coordinates reach the unit one batch later. The only remaining TEX
   question is `LOD` selection itself, not scheduling - partly closed since:
   the bias operand works and computed LOD clamps at 0, and explicit LOD is
   TEX bit 13 with bit 12 clear (section 10.4).
2. ~~**Cycle cost of a partially filled batch.**~~ - **measured**, see 4.6: a
   batch costs about 14x an extra ALU packet, so how full it is barely
   matters and the scheduler does *not* gain a second dimension.
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

### 4.6 Batch-fill cost, measured

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

#### Which unit costs: the MFU does

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

The chain is verified end to end at least for two components: `filter_quad`
renders its texture's two-tone split exactly (section 10.3), so the linked
varying reaches `ipl`, and `ipl` reaches the pixel packet.

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

1. **Scheduling/forwarding model** - mostly closed (sections 4.3, 4.6). Still
   needed: `pseq_to_dw_exec_nb` semantics (4.4).
2. **Register allocation over a 16-fp20 file with ALU-latch-side chaining** -
   the encoding is known, the *policy* is not. Nothing is known about whether
   `g0..g7` have any cost penalty, or whether row registers beyond
   `alu_buffer_size` alias. **Partly measured**: at `alu_buffer_size = 1`, all of
   `r4..r15` are distinct from `r0..r3` and from each other, so the file is not
   silently remapped onto four registers. But `alu_buffer_size = 2` **hangs the
   GPU** for a shader that touches only `r0..r3`, reproducibly, while 1 and 3
   run - so size 2 is unusable and size 3 is the alternative to size 1.
3. ~~TEX operand plumbing~~ - **settled**: coordinates must be in `r0..r3`, and
   an ALU in the *previous* batch can write them, which is how
   `tests/grate/asm/tex_cross_batch.txt` feeds `s` and `t` from uniforms. The
   same batch cannot, because the ALU stage runs after TEX (section 10.3). Still
   open: the cycle cost of the copy, and whether the copy can share its batch
   with the ALU work that produces it.

### 9.2 Needed for correct code, unknown today

4. ~~`MIN`/`MAX`/`CMP` semantics~~ - partly settled, see 4.5. Finite `MIN`/`MAX`
   order correctly and `CMP` compares the **first product**, not `rA`. **Signed
   zero is not reachable**, so there is no tie-breaking rule to find: building
   `-0` as `0 * -1` produces something `rcp` maps to +infinity exactly like
   `+0` (`0xFF`, the same as `rcp(+0)`), and `MIN`/`MAX` of that pair return it
   under either operand order. Values above `2^33` clamp (see below), which is
   what "the saturated fp20 exponent" turned out to mean.
5. ~~Whether `saturate` clamps before or after the result scale~~ - **settled,
   and it is the unusual order: the scale is applied first.** `(sat)(/2)` on
   `1.5` gives `0.75` (scale -> 0.75, then saturate), not `0.5`. `(/2)` alone
   works (`0.5 -> 0.25`). **Saturate clamps fx10 writes too**: `1.25` written
   with `.l* (sat)` reads back through the `.l` source form as 1.0 (halved, it
   stores `0x7F`), while the same write without `sat` reads back 1.25 (halved,
   `0x9F`), and a 0.75 control stores `0xBF` either way.
6. ~~The exact `CMP` predicate~~ - **settled**: it is
   `(rA*rB < 0) ? rB : (rC*rD)`. The reference's `(rA < 0)` reading is wrong on
   hardware, and the true branch is `rB`, not the product. See 4.5.
7. ~~`RECIP`/`RSQ`/`LG2`/`EX2`/`SQRT`/`SIN`/`COS` accuracy, input ranges and the
   `PRE*` pair~~ - **settled**, details in 4.5. Every op is exact to the
   precision of its fp20 input; the output is full range (the clamp lives in the
   output stage); `PRE*` + main must be emitted as one pair with the same
   operand, and the `PRE` result cannot be stored or reused.

   **Plumbing, now known and previously misread here: `sfu: OP rN` does not
   write rN.** A survey of 14 operations (rcp, rsq, sqrt, ex2, sin, cos, frc,
   lg2, presin, precos, preex2) returned *the input, unchanged*, in every case -
   sqrt(0.25) read back 0.25, rcp(2.0) read back 2.0, cos(0) read back 0. So the
   `reg` field names the operand, and the result is only available as the `sfu`
   source of an MFU multiplier: `mul0: r2, sfu, #1`. The multiplier destination
   is restricted to `r0..r3` (the assembler rejects anything higher with
   "Invalid row register number, r3 max"), so the result lands in the pixel
   packet and is read from there.

   **Working recipe, verified on hardware.** Park the operand in `r0..r3` (the
   pixel packet - `r4..r15` are not readable by the unit: with the operand in
   `r4`, `rcp` returned the same 0.47 for inputs 2, 4 and 0.25, and the
   multipliers reject `r4` outright as a source), run `sfu: OP rN`, take the
   result with `mul0: r2, sfu, #1`, and convert with `.l*` for the store. That
   returns `rcp(2) = 0.5`, `rcp(4) = 0.25`, `sqrt(0.25) = 0.5` and
   `frc(1.75) = 0.75` exactly, and `r1` behaves the same as `r0` as the operand.
   `ex2(-1)` returned 1.0 instead of 0.5, which matches the reference calling
   EX2 / SIN / COS "second step" operations - they need PREEX2 / PRESIN / PRECOS
   first, so a single-op probe of those three is not meaningful.

   **Accuracy, measured.** With `K*(y - expected) + 0.5` and `K = 4096` (one
   fp20 ulp at 0.5 maps to 0.25 of output range), `rcp(2)`, `rcp(4)`, `rcp(3)`,
   `rsq(4)`, `sqrt(0.25)` and `frc(1.75)` all read back `0x7F` = 0.5 - an error
   below the probe's resolution (~1.2e-4 absolute, about one fp20 ulp). The SFU
   is therefore as accurate as the ALU's own format and needs no polynomial
   refinement at fp20 precision. Still unmeasured: EX2 / SIN / COS (they need
   their PRE stage, which a single-op probe does not provide) and edge inputs
   (zero, negative, very small).

   That also means the earlier "SFU costs 3.4 ns/word" timing work measured the
   cost of an SFU *word*, which is unaffected, but no accuracy claim in this
   document yet rests on a real measurement - accuracy and input ranges remain
   open, now with a working way to observe them.
8. ~~fp20 conversion rounding on ALU results~~ - **settled: the hardware
truncates toward zero, exactly like the assembler.** `x = 1 + eps`, then
`4096*(x-1)` with an embedded constant for the offset:

   | eps | result | reading |
   |:--|:--|:--|
   | 0 | `0x00` | 0 |
   | 1 ulp (`2^-13`) | `0x7F` = 0.5 | 1 ulp survives |
   | 1.5 ulp | `0x7F` = 0.5 | **truncated to 1 ulp** - rounding would give `0xFF` |
   | 2 ulp | `0xFF` = 1.0 | exact |
   | 0.25 (control) | `0xFF` | chain saturates as expected |

   There is no round-to-nearest anywhere in the ALU path, and the assembler's
`fp20` packing truncates identically, so a backend can emit truncated constants
and predict every intermediate exactly. Earlier failed attempts, kept for the
record: The probe that would settle it is designed
   and its mechanism is confirmed working - `4096*1.0 + (-4095.5)` returns
   exactly `0x7F`, so an amplified 1-ulp difference is visible at 8-bit output
   precision - but the tie value cannot be routed through a row register at
   `alu_buffer_size = 1`: a 3-batch chain (`x = 1+eps`, `x-1`, `(x-1)*4096`)
   returns 0 even for its own control (`eps = 0.25`, which should be `0xFF`),
   and the store sees the first batch's register as 0. So the blocker is the
   register round-trip across two batch boundaries, not the arithmetic. Note
   that rC must be an immediate here (see 4.3), so the subtract needs `-#1` and
   the multiply needs the uniform in rB. Next step: chain through `aluN`
   lookalikes via `recv`/`send` instead of the register file, or retry at a
   larger `alu_buffer_size` (but 2 hangs - see 9.1).
   Also tried, also failing: chaining through the `aluN` latches inside one batch,
   and padding every batch to four packets (no change at all). The latch probe
   turned up something worth flagging on its own: with
   `ALU0: MAD r2.*h, u0`, `ALU1: MAD r3.*h, alu0`, `ALU2: MAD r3.l*, alu1`,
   `ALU3: MAD r2.l*, alu2`, the value written to `r3.lo` by ALU2 was `u0` - the
   result of **packet ALU0** - while ALU1's read of `alu0` returned 0. So the
   latch name appears to be offset by one slot from the packet that writes it.
   A second oddity: `MAD r3.*h, r2, #1, -#1` left the value unchanged (0.75
   stayed 0.75) *and* landed it in `r3.lo` instead of `r3.hi`, i.e. the `-#1`
   spelling perturbs more than the addend. Both need their own probe before the
   chain can be trusted - see the caution in 4.3.

### 9.3 Needed for performance only

9. ~~Whether the 4 ALU slots of a packet may include a NOP cheaply~~ -
   **settled**: `count` barely matters, the batch itself is what costs
   (section 4.6).
10. ~~Whether `MFU` and `ALU` really run in parallel for the same batch~~ -
    **settled**: MFU, ALU and TEX all overlap and a batch costs the maximum over
    them; every SFU op costs the same (~3.4 ns/fragment beyond the first in its
    batch); and the limit is per batch rather than per word (section 4.6).
11. ~~Whether a TEX and a DW in the same batch can be independent~~ -
    **settled: yes.** A batch holding a `tex` into `r2/r3` plus a store of
    ALU-produced values returns exactly the ALU values (`0x7F407F40`), so a DW
    can run alongside a TEX as long as it does not consume the sample.

### 9.4 Documentation debt that blocks nothing

12. ~~The undocumented unit 5 (four packed relative slot offsets)~~ - **it has
    no host upload register on AR20, so it is not a gap that can be closed.**
    The FP class exposes a register for exactly six streams - PSEQ
    (`0x540`/`0x541`), MFU schedule (`0x600`/`0x601`), MFU (`0x603`/`0x604`), TEX
    (`0x700`/`0x701`), ALU schedule (`0x800`/`0x801`), ALU (`0x803`/`0x804` plus
    the complement at `0x806`), DW (`0x900`/`0x901`) - plus `0xe22`, and there
    is **no register at all between `0x901` and `0xe22`** (the complete list is
    in `include/tgr_3d.xml.h`). The blob's own fragment dump (`cgc -F -R`)
    uploads those same six streams and nothing else. So the encoder's `sync` and
    `sync_pre` buffers are dead code on this chip - the shared encoder most
    likely serves a later chip that does have the register. Nothing to probe
    until such a chip turns up.
13. ~~DW bit 14~~ - **confirmed on hardware from a word the assembler itself
    emits**: it forces an all-zero payload (`0x007F007F` -> `0x00000000`)
    whichever register pair holds the payload, which is exactly the "always a
    zero payload" the compiler corpus showed. The target-class field is measured
    too: bits 16..17 = 2 is the colour target, 1 with index 0 is the depth
    surface, 3 writes nothing. Still open: the 128-bit packer at `0x1c6648`, and
    the scaling that turns a depth-class payload into `D16` - a `0.25` payload
    lands as `0x0004` rather than the `0x4000` a plain copy would give.

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
* use a texture at least 16 texels wide, or bind a mip chain; narrower ones are
  misaddressed by the sampler and read mostly black whatever the coordinates
  are, which looks exactly like a coordinate bug (section 10.3);
* put `TEX:` before `ALU:` in the `EXEC` block. The grammar only accepts pipeline
  order, and `ALU:` first is a parse error that exits 1 without printing
  `--result` - see [hardware-testing.md](hardware-testing.md).

The destination/coordinate overlap is not a problem: `filter_quad_fs.txt` uses
`txb r2, r3, tex0, r0, r1, r2, r3`, with `r2` as the third coordinate and half
the destination, and it samples correctly.

### 10.3 Why `filter_quad` rasterised nothing

The `filter_quad_*` assets were unusable for two harness reasons, neither of
which is about the ISA:

1. **The attribute guard was wrong.** The `if (location >= 0)` test for `color`
   had been inserted *before* `location` was reassigned, so it tested the
   *position* attribute's location. `filter_quad_vs.txt` declares no `color`
   attribute, so the draw still got
   `vertex_attrib_float_pointer(ctx, -1, ...)`. Each binding is now guarded after
   its own lookup.
2. **`mvp` was never set.** `filter_quad_vs.txt` computes `gl_Position` as
   `DP4 a[0], c[0..3]` and the harness supplies no vertex constants, so the
   exported `w` was 0: a degenerate triangle, which rasterises nothing and
   leaves the framebuffer at the clear colour. `tools/assembler` now takes
   `--vs_mat4 '["mvp"]=(16 floats)'`, which writes all 16 floats through
   `grate_3d_ctx_set_vertex_uniform` (that call already accepted `nb = 16`).

With both fixed the shipped trio works:

| probe | result | reading |
|:--|:--|:--|
| `filter_quad_vs` + `filter_quad_fs`, no `mvp` | `0x4C4C4C4C` | clear colour: nothing rasterised |
| same, `mvp` = identity | `0x00000000` | rasterised, TEX ran, result reached DW |
| `vs_mov` + `filter_quad_fs` (no uniform at all) | `0x00000000` | the fragment half needs no vs uniforms |

`asm_tests.sh` now carries the identity-`mvp` case as a regression: any value
differing from the `0x4C4C4C4C` clear proves the triangle reached the DW unit.

**The coordinates were never the problem.** An earlier round concluded otherwise
from a handful of framebuffer reads, but both reasons were artefacts of the
probe. The decisive measurement is the whole frame with a mip chain bound
(`--mipmap`, `lod_bias` 0): **32768 white and 32764 black pixels and nothing
else** - exactly the two-tone split of the bound texture. So the interpolated
`texcoord` does reach TEX and the sample does reach DW.

What made every probe read black was the texture setup:

| setup | whole frame |
|:--|:--|
| mip chain bound, `lod_bias` 0 | 32768 white / 32764 black - correct two-tone |
| mip chain bound, `lod_bias` 8 | 65532 x `0x00545454` - a small mip |
| **no** mip chain, `lod_bias` 0 | 57340 black / 8192 white - wrong |

A 4x4 texture with no mip chain bound samples through this harness as *mostly
black* (1/8 white instead of 1/2) whatever the coordinates are, so a black
result says nothing about the coordinate path. The cause is texture row
addressing, a driver bug rather than a probe artefact: `grate_3d_set_texture_desc()`
writes no pitch at all, so the texture unit derives the row stride itself and
always uses `width * bpp`. `grate_create_texture()` allocated a pitch aligned up
to 64 bytes, so for a 4-texel-wide texture the sampler read row `y` at offset
`y * 16` while the data sat at `y * 64`. Colouring rows separately shows it
directly (16-high texture, `s = 0.7`, pitch forced per run):

| texture | width*bpp | allocated pitch | rows read |
|:--|--:|--:|:--|
| 4x16 | 16 | 16 | `B G R W B G R W` - correct |
| 4x16 | 16 | 32 | `B . G . R . W .` - row `y` returns row `y/2` |
| 4x16 | 16 | 64 | row `y` returns row `y/4` |
| 8x16 | 32 | 32 | correct |
| 8x16 | 32 | 64 | row `y` returns row `y/2` |
| 16x16 | 64 | 64 | correct |
| 16x16 | 64 | 128 | row `y` returns row `y/2` |

Doubling the pitch halves the row index, so the stride really is `width * bpp`
and the padding is invisible to the sampler. Fixed by allocating a tight pitch
in `grate_create_texture()`; 4x4 through 16x16 then read every row correctly and
`filter_quad` renders the two-tone frame at 4x4 with no mip chain.

**Block-compressed formats follow the same law.** For DXT1/ETC1 the derived
stride is `width * bpp / texel_width` = one 8-byte block per four texels, i.e.
`width / 4` blocks per row, which is exactly what a tight allocation provides.
With `GRATE_TEX_FORMAT=dxt1` a 16x16 texture whose block rows are red, green,
blue and white samples it row for row:

| t | texel row | rgba8888 | dxt1 | etc1 |
|:--|--:|:--|:--|:--|
| 0.03125 | 0 | `0x000000FF` red | `0xFF0000FF` red | `0xFF0000FD` red |
| 0.09375 | 1 | `0x0000FF00` green | red (same block row) | red |
| 0.15625 | 2 | `0x00FF0000` blue | red | - |
| 0.21875 | 3 | `0xFFFFFFFF` white | red | - |
| 0.34375 | 5 | - | green | `0xFF02FF02` green |

DXT1 returns alpha `0xFF` (the block format has no alpha channel) and ETC1 is
off by one or two in a channel - both as expected from the codecs. A wider
pitch misaddresses it identically: at pitch 48 block row 3 lands on block row
2's data (blue), at pitch 64 it lands in the zero padding (black). One loose
end: ETC1 at pitch 64 returned white rather than the black DXT1 gets from the
same padding, so an all-zero block is not neutral: DXT1 decodes it to (0,0,0)
and ETC1 to (2,2,2) (`0xFF020202`).

With the sampler still striding 32 bytes, block row 3 sits at byte 96, so at
pitch `p` it reads whatever data block row `96/p` holds:

| pitch | 32 | 48 | 64 | 96 |
|:--|:--|:--|:--|:--|
| dxt1 | white (row 3) | blue (row 2) | black (padding) | green (row 1) |
| etc1 | white (row 3) | blue (row 2) | `0xFF020202` (padding) | green (row 1) |

Both formats follow that arithmetic exactly, which is the cleanest confirmation
that the row stride is `width * bpp / 4` and that any pitch in the descriptor is
ignored. An earlier run reported ETC1 as white at pitch 64; that was a harness
bug - `etc1_encode_image()` writes its rows contiguously, so the upload left the
block rows at a 32-byte stride while claiming the pixelbuffer's pitch. A bound mip
chain is laid out with `ALIGN(width * bpp, 16)` per level, which is tight for
the sizes that matter, so `--mipmap` hid the bug rather than working around
anything about mip *selection*.

For the record, cross-batch delivery is fine: `tex_cross_batch.txt` writes the
coordinates with the ALU in one batch and samples in the next, and the sampled
texel follows `s` and `t` exactly. What a shader cannot do is write coordinates
and sample them in the *same* batch - the `ALU:` stage runs after `TEX:`, which
the assembler grammar enforces.

Two other suspicions were ruled out by direct comparison. `tram0.xyzw`
(`filter_quad_linker.txt`) versus `tram0.yxzw` (`linker_vs_tests.txt`) only swaps
the two components. And the vertex shader's export form makes no difference at
all: full destination mask, masked source, or exporting from `r0` instead of
`r63` all produce bit-identical frames.

### 10.4 LOD bias is functional, and the computed LOD clamps at 0

`--mipmap` was added (generates the chain, `NEAREST_MIPMAP_NEAREST`, `max_lod` 11)
so `filter_quad_fs`'s `txb` could be driven. Its bias comes from `r3`, fed from
the `lod_bias` fragment uniform. Whole-frame counts, one row per bias:

| bias | black | white | other |
|-----:|------:|------:|------:|
| 0, 2, 4, 5, 6 | 32764 | 32768 | 0 |
| 7 | 0 | 0 | every pixel |
| 8, 12 | 0 | 0 | every pixel |

Rows 0-6 are the *base level*, pixel for pixel - 32764 black and 32768 white is
exactly the texture's two-tone split. The output changes for the first time at
bias 7, and from 8 on it is a single uniform colour.

That transition is where the arithmetic puts it. A 4x4 texture across a 256x256
quad is magnified 64x, so the derivative-based LOD is `log2(4/256) = -6`; adding
the bias and clamping at 0 moves the first level change to bias 7 and to nothing
earlier. Therefore:

* the bias operand is real - bit 12 plus the `r3` source, exactly as the TEX
  word table says;
* the computed LOD is *clamped* at 0 rather than wrapped: biases 0..6 give
  identical frames, not a progression;
* the bias is inert with no mip chain bound (bias 8 with mipmapping off still
  renders the two-tone frame - the "no mip chain" row in 10.3).

Not established: which level is actually reached, and what the small mips
contain. `grate_texture_generate_mipmap` does not produce a plain box average at
this size - bias 7 changes *every* pixel instead of preserving the two-tone
split, and the uniform value at bias 8 is `0x00545454`, not the `0x7F7F7F7F` a
50/50 average would give. Colour alone pins the transition, not the level.

**Explicit LOD is TEX bit 13, and it only works with bit 12 clear.**

`TEX:` accepts a raw word (`TEX: <hex>`), so
`tests/grate/asm/tex_explicit_lod.txt` starts from the word that
`txb r2, r3, tex0, r0, r1, r2, r3` assembles to (`0x1420`) and flips single
bits. The quad magnifies a 4x4 texture 64x, so the computed LOD is
`log2(4/256) = -6` and clamps to 0; the ALU writes `u0` into `r3`.

| word | bit 12 | bit 13 | lod = 0 | lod = 2 | lod = 5 |
|:--|:--:|:--:|:--|:--|:--|
| `0x0420` | - | - | two-tone | two-tone | two-tone |
| `0x1420` (`txb`) | bias | - | two-tone | two-tone | two-tone |
| `0x2420` | - | **LOD** | two-tone | `0x00545454` | `0x00545454` |
| `0x3420` | bias | LOD | two-tone | two-tone | two-tone |

"two-tone" is 8191 black / 8192 white, the level-0 frame; `0x00545454` is the
flat colour of the 1x1 mip.

So bit 13 makes `r3` an **absolute** LOD, bits 12 and 13 are mutually
exclusive, and **bit 12 wins when both are set**. The `txb` row is also the
cleanest confirmation of the clamp: `-6 + 5` is still negative, which is why
bias 5 looks identical to bias 0 here, and why the shipped `filter_quad` probe
needs bias 7 before anything changes.

`lod = 2` and `lod = 5` give the same colour because a 4x4 chain has only
levels 0, 1 and 2 - level 2 is already 1x1, so everything deeper reads it.

For selection: `textureLod` becomes `TEX` with bit 13 and the LOD in `r3`,
`texture` with a bias becomes `TXB`, and `textureGrad` remains impossible
(section 2.1). `asm_tests.sh` now carries the bit-13 case as a regression
(`0x00545454`).
