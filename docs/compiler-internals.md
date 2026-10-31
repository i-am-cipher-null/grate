# Inside libcgdrv.so: how the proprietary compiler encodes instructions

`subprojects/Linux_for_Tegra/libcgdrv.so` is the blob shader compiler that Grate
drives through `libcgc`. This document maps what is known about its internals.
Everything here is either read directly from the disassembly (stated as fact) or
explicitly flagged as inferred.

## Target shape

```
ELF 32-bit LSB shared object, ARM, EABI5, dynamically linked, stripped
sizeof 4276308, 94 dynsym entries, only CgDrv_* exported, no .symtab/.debug_*
```

* **ARM mode, not Thumb.** Decoding the same bytes with `-M force-thumb` yields
  `ldc2`/`<UNDEFINED>` junk. The ARM reading is coherent (`push {r4-r9, sl, fp,
  lr}`, struct initialisation, list walks).
* Every function is `FUN_<address>`; there are no names to recover.

## Disassembly workflow

Linear disassembly with objdump is reliable on this binary and is the fastest
path:

```
objdump -d --start-address=0x2af724 --stop-address=0x2afdbc libcgdrv.so
```

Ghidra 12.2 (headless) works, but note:

* Ghidra 12.2 requires **JDK 25**. A JDK 21 is rejected with `WARNING: JAVA_HOME
  environment specifies unsupported java version`.
* The project directory must already exist or the run aborts with
  `Abort due to error: Directory not found`.
* Invocation used here:

```
JAVA_HOME=<jdk25> ghidra_12.2_PUBLIC/support/analyzeHeadless \
  <projdir> cgdrv -import libcgdrv.so -scriptPath <scripts> \
  -postScript DecompileAt.java 0x2af724 0x2a5834
```

### Two traps that waste hours

1. **Ghidra invents entries mid-function.** On a stripped ARM binary it happily
   creates a function at any branch target. Symptom: decompiled C full of
   `unaff_r4` / `unaff_r5` and implausible sizes (one run reported a 10988-byte
   "function" spanning unrelated code). Anything built from such a function is
   garbage. Force the entry at a real prologue instead, removing the bogus
   enclosing function first.
2. **A leaf function's prologue has no `lr`.** Grepping for `push.*lr` silently
   skips functions that `push {r4-r9, sl, fp}` and return with `bx lr`. Grep for
   plain `push` and check that the matching `pop` exists.

Also: when Ghidra and objdump disagree at an address, check which one has a
sensible decode run. In one case objdump's continuous chain ended exactly at
`pop {r4-r9, sl, fp}` / `bx lr`, proving objdump was aligned and Ghidra had
started mid-instruction.

## Address map

| Address | Role |
|:--|:--|
| `0x2af724` | scheduler / register-allocation pass (the window asked about) |
| `0x2a5834` | instruction dedup: compares a 32-bit word against a 16-entry table |
| `0x2a1d84`, `0x2a1ea4` | patch of bit 15 into a word, then `str` to `[r4+0x58]` |
| `0x1c6648` | 128-bit field packer: builds 4 consecutive words |

### 0x2af724 - the scheduler, not the encoder

`0x2af6f0` is the tail of the *previous* function (`bx lr` at `0x2af720`); the
real entry is `0x2af724`, a full frame (`push {r4-r9, sl, fp, lr}`,
`sub sp, sp, #668`, return at `0x2afdbc`). It does not encode instructions:

* initialises five 48-entry dword arrays at `+8`, `+0xc8`, `+0x188`, `+0x248`,
  `+0x308` (stride `0xc0`, 48 iterations), one of them filled with the sentinel
  `0x1869f` (99999);
* walks three intrusive lists hanging off the context at `+0x10`, `+0x58`,
  `+0x5c`, clearing fields on each node;
* builds 20-byte records (5 words) in that 668-byte frame, one per instruction,
  indexed by node index with an explicit `cmp #31 / bgt` overflow path;
* dispatches on the node opcode at `+0x2c` against `0x39`, `0x42`, `0x7c`,
  `0xa4`, `0xf4`;
* tests a flag word at node `+0x3c` against `0x40000`, `0x4000000`, `0x8000000`,
  `0x40000000`.

So the bit-level encoding happens in its callees. The schedule records are the
input to whatever emits the final words.

### 0x1c6648 - the bit packer

A leaf function (no calls), `push {r4-r9, sl, fp}` at `0x1c664c`, `bx lr` at
`0x1c67f8`. It reads a struct at `r1` (fields at 4-byte offsets `0x00..0xc4`)
and writes **four consecutive words** at `[r0]`, `[r0+4]`, `[r0+8]`, `[r0+12]`,
stored in the order `+12, +8, +4, +0`.

The packing is a chain of `orr acc, acc, field, lsl #N`. Each store both ends one
accumulator chain and reloads the accumulator for the next word, so the four
words form one 128-bit value written lowest word last.

Word 0, reconstructed from the actual shifts:

| Bits | Source field |
|-----:|:-------------|
|  0..1 | `+0x8c` |
|  2..3 | `+0x90` |
|  4..5 | `+0x94` |
|  6..7 | `+0x98` |
|  8..9 | `+0x9c` |
| 10..12 | `+0xa0` |
|    13 | `+0xa4` |
|    14 | `+0xa8` |
| 15..20 | `+0x10` |
|    21 | `+0x60` |
|    22 | `+0x40` |
|    23 | `+0x20` |
|    24 | `+0xac` |
|    25 | `+0xb0` |
|    26 | `+0xb4` |
|    27 | `+0xb8` |
|    28 | `+0xbc` |
|    29 | `+0xc0` |
| 30..31 | `+0xc4` |

Words 1..3 are built identically with different source offsets. Bit widths in
this table are *inferred from the gap to the next field*, which is only valid if
fields do not overlap - treat widths as a hypothesis, the shift amounts as fact.

**This layout matches neither documented unit.** It is not the ALU layout
(`0..5` rD, `6..18` rC, `19..31` rB, `32..44` rA) and it is not DW (render target
`2..5`, stencil `10`, `src_regs_select` `15`). Since it emits four words it is a
128-bit encoding, and no documented unit is 128-bit wide. Either it encodes a
unit not yet documented, or it serialises a *descriptor* rather than an
instruction.

### 0x2a5944 - why the compiler always sets bit 17

The loop keeps 16 candidate slots (stride `0x10`) and compares a freshly built
word against them:

```
orr  r7, ip, #0x20000      @ candidate key, bit 17 forced
ldr  r2, [r3]              @ existing entry
cmpne r2, ip
cmp  r7, r2
```

The same word is what gets stored. That is why every non-depth store in compiler
output carries bit 17: it is part of the compiler's instruction identity, used
for deduplication, not a hardware requirement. Hardware ignores the bit - see
the on-GPU verification in `fragment-shader-isa.md`.

### 0x2a1d84 and 0x2a1ea4 - src_regs_select is a patch

Both sites assemble a word first, then conditionally set bit 15 and store the
result:

```
orrne r5, r5, #0x8000      @ only when the flag is non-zero
str   r5, [r4, #0x58]
```

So bit 15 is not built as part of the field layout; it is applied afterwards as
a patch on a word that was packed elsewhere.

## The encoder entry point

A prior manual decompile (kept in the tree) reconstructs the dispatcher as
`encode_ar20_shader_instruction_record()`. It is called once per IR record and
dispatches on `(info->sched & 0xff) >> 5`:

| value | unit |
|------:|:-----|
| 0 | PSEQ |
| 1 | MFU |
| 2 | TEX |
| 3 | ALU |
| 4 | DW |
| 5 | an undocumented unit (4 packed relative slot offsets) - **no host upload register on AR20**, so the stream is unreachable; see [writing-a-compiler.md](writing-a-compiler.md) 9.4 |

Stream layout: the 32-bit streams (PSEQ, TEX, DW, schedule words) have one
header dword, so record `i` lives at index `i + 1`. The 64-bit streams (MFU, ALU)
have a 4-byte header and then 8-byte words stored **high dword first**. ALU
embedded-constant words are the exception: low dword first.

Helper map, which is what makes this address range (the one originally asked
about) legible:

| Symbol | Address |
|:--|:--|
| `dest_code` | `0x2aed6c` |
| `tex_coord_code` | `0x2aede4` |
| `alu_enc_operand` | `0x2af078` |
| `alu_enc_rd_unary` | `0x2af190` |
| `alu_enc_rd` | `0x2af284` |
| `dw_enc_src` | `0x2af330` |
| `dw_enc_src_spill1` | `0x2af4d8` |
| `dw_enc_src_spillN` | `0x2af5c4` |
| `spill_force_single` | `0x2af6a0` |
| `alu_flush_batch` | `0x2af6f0` |
| `release_alu_insn` | `0x2b6720` |
| `pack_alu_const` | `0x2e6490` |
| `alu_rc_rd_only` | `0x2a1ed8` |

### Correction to the earlier notes in this file

`0x2af6f0` is **`alu_flush_batch`, a real function**, not the tail of its
predecessor. An earlier revision of this document claimed otherwise and had the
Ghidra database entry deleted as "mis-detected" - that was wrong, and the
decompile of it (writing per-row flags from the `read_mask`/`write_mask` at
context `+0x6c`/`+0x6e`) confirms the helper's role.

### DW drains `[31:10]` from a helper

`encode_dw` writes only the low ten bits itself:

```
[1:0]   mode: 1 = render target write, 2 = spill write (single reg),
              3 = spill write (multi reg)   <- bit 0 is NOT a plain enable
[5:2]   render target index
[6]     set for spill modes, and for RT writes given last-operand flag 0x200
[7]     derived: set iff render target index == 3 (coverage)
[8]     spill modes
[31:10] returned by dw_enc_src() / dw_enc_src_spill1() / dw_enc_src_spillN()
```

The spill half of that word is now placed on hardware: modes 2/3 always target
render target 4, whose address comes from `RT_PTR(4)` = `0xe04` (with the
companion limit `0xe34` and enable bit 4 of `0x903`), and bits 10..16 of the word
are a selection/slot field that must be non-zero or the store does nothing. See
[[Fragment Shader ISA]] for the measured table. That is also why the encoder has
separate `dw_enc_src_spill1`/`dw_enc_src_spillN` helpers producing bits 31:10.

`0x2af330` starts by `memset`-ing `0xc0` bytes at context `+0x4dc` and walking
`prog->slots` (`prog_t+0x60`), testing `slot->index == 4` (`res_desc+4`, render
target 4 = spill) and reading `desc->type` (`res_desc+0xc`). That independently
corroborates the `res_desc`/`prog_t` offsets used by the reconstruction.

Decoding `dw_enc_src()`'s return value `r` (DW bit n is `r` bit n-10):

| DW bits | source in the decompile |
|:--|:--|
| 10 | `r` bit 0 - stencil-ish flag |
| 11 | `r` bit 1 - `(operand2 >> 17) & 3` |
| 14 | `r` bit 4 - set when `(opcode & 0xffffefff) == 0xf4` |
| 15 | `r` bit 5 - `src_regs_select` |
| 16..17 | `r` bits 6..7 - render target class: `1` if `desc->index == 0`, else `2` |
| 18,19,23,25 | `r` bits 8,9,13,15 - slices of an operand word |

Bits 18/19/23/25 are rendered by the decompiler as `(w2 & mask) >> shift`, which
would always be zero; the intended form is almost certainly `(w2 >> shift) &
mask`, so treat those four as "some operand bits" rather than as exact fields.

Hardware confirms 16..17 is a 2-bit class field (1 = depth, 2 = everything else,
3 invalid), and that on a colour store bits 10..14 and 18..31 of the payload have
no observable effect - they exist for the stencil/spill/other paths.

## Scheduling

Two different things get called "scheduling" here and they live in different
places: `0x2af724` does **register lifetime bookkeeping**, while the `sched`
word itself is assigned by the pass that builds the IR bins. The rules below are
read from the disassembly - the decompiler output for `0x2af724` is not usable
(forcing a 1692-byte body makes it lose the prologue and emit `unaff_r4`/`unaff_r5`
for most of it).

### The sched word

```
sched = (bin << 8) | (unit << 5) | row        row: 0..31, unit: 0..5
```

`row` is 5 bits and `unit` 3 bits, so a bin holds at most 32 slots per unit. The
encoder recovers them as `(sched & 0xff) >> 5` and `(sched - bin_base) >> 8`, and
the record index inside a bin is `slots_per_bin * bin + row` (`slots_per_bin` at
`layout+0x18`).

Bins are a linked list of `{next@0, ?@4, base@8, limit@0xc}`. A record advances
to the next bin when `bin->limit < sched`.

### 0x2af724 - register lifetime release

Frame: `push {r4-r9, sl, fp, lr}`, `sub sp, sp, #668`, return at `0x2afdbc`.

Setup:

* five parallel 48-entry dword arrays at context `+8`, `+0xc8`, `+0x188`,
  `+0x248`, `+0x308` (loop counter `cmp lr, #48`, base `add r0, r0, #4`). All are
  zeroed except the third, which is filled with `0x1869f` (99999) - an
  unset/infinite marker rather than a count;
* a 32-entry table of 20-byte (5-word) records inside the frame at `sp+0x20`,
  indexed by node index, with an explicit `cmp r7, #31` / `bgt` overflow guard;
* walks three intrusive lists hanging off `+0x10`, `+0x58`, `+0x5c`.

The release walk (`0x2afbd0`-`0x2afd10`) goes over the operand array **backwards
in 12-byte steps**, which confirms `sizeof(ir_operand) == 12`:

```
ldr   r3, [r5, #-12]        @ operand w0
ubfx  r2, r3, #24, #4       @ kind = w0[27:24]
cmp   r2, #1                @ OPD_KIND_VREG
bic   r0, r3, #0xff000000   @ index = w0[23:0]
ldr   r1, [prog, #4]        @ prog->regs
ldr   r4, [r1, r0, lsl #2]  @ -> ir_reg
ldr   r1, [r4, #0x4c]
cmp   r1, #5                @ only kind == 5 tracks a hardware register
ldr   r3, [r4, #0x1c]
sub   r3, r3, #1
str   r3, [r4, #0x1c]       @ use counter--, free hwreg when it hits 0
```

On reaching zero it consults `hwreg` (`ir_reg+0x58`) and updates tables at
`base+0xcc` and `base+0x30c`. Two guards skip work: `tst w1, #0xc000000` on the
operand, and `tst [ir_reg+0x3c], #0x40000`. A second counter lives at
`ir_reg+0x68` and is drained the same way.

So the ordering rule the scheduler enforces is a last-use rule: a hardware
register slot is only returned when the backward walk reaches the instruction
that consumes it last.

### 0x2af6f0 - alu_flush_batch

Called when an ALU batch ends, with a relative bin number. It writes one
schedule word per ALU row at `slots_per_bin*rel_bin + 1 + row`, and only handles a
second row when `slots_per_bin > 1`.

The 16-bit `read_mask` (`ctx+0x6c`) and `write_mask` (`ctx+0x6e`) are split into
two bytes covering two consecutive ALU rows: the first row is written from the
high byte, the second from the low byte. Per row:

```
bit 29 = (read_mask_byte != 0) || (write_mask_byte != 0 && write_mask_byte != 0xff)
bit 30 = (write_mask_byte != 0)
```

The `!= 0xff` special case means "writes some but not all of the bank" is
tracked separately from "writes everything".

### Per-unit grouping

* **ALU** - four slots per packet (`info->slot` 0..3), `info->pkt_minus1` is the
  packet count minus one, and `info->link` lets two records share one packet. The
  schedule word layout depends on `hw->wide_alu_sched`: normal is
  `count[1:0]` / `addr[7:2]`, wide is `count[10:8]` / `addr[22:16]`. Queued
  instructions are released through `release_alu_insn` when `sched` changes.
* **MFU** - consecutive records whose `sched` word is equal share a single 64-bit
  word; the schedule word carries enable in bit 0 and the running count in
  `[7:2]`.
* **DW / TEX / PSEQ** - one 32-bit word per record, no grouping.

### What is *not* established

No latency or stall model was recovered. Nothing found so far encodes "N cycles
between issue of X and Y"; the only ordering constraints visible are the last-use
rule, the four-slot ALU packet limit and the per-bin capacity of 32 slots per
unit. Treat any claim about actual hardware timing as still open.

## The 128-bit packer at 0x1c6648

The whole binary contains exactly **one** call to it, at `0x1c6d1c`:

```
1c6d0c: mov  r0, r8            @ dst
1c6d10: mov  r1, sl            @ src
1c6d14: add  r7, r7, #1
1c6d18: add  sl, sl, #200      @ src stride 0xc8
1c6d1c: bl   1c6648
1c6d20: cmp  r4, r7
1c6d24: add  r8, r8, #16       @ dst stride 16 = 4 words
1c6d28: bgt  1c6d0c
```

So it turns a **200-byte node into a 16-byte (128-bit) record**. Right after the
loop the caller makes an indirect call through a vtable (`ldr r3,[r5,#0xb0];
blx r3`) and returns 0 or 1.

This does not look like a per-unit instruction encoder: there is no batching, no
slot/packet state and no write into any of the context streams (`pseq`/`mfu`/
`alu`/`dw`). It reads a flat struct and emits a fixed-size serialisation. Given
that, the most plausible reading is a *descriptor* record (vertex input /
attribute / stream descriptor) rather than a unit opcode, but that remains a
guess.

### Ghidra cannot currently decompile this region

`0x1c6648` decompiles to nonsense - a switch on a stack byte,
`switchD_001c6714_default`, calls to `FUN_001c5950` - that disagrees with objdump
at every address. Cause: an earlier run created a bogus function entry at
`0x1c6650` (mid-function); the instruction objects decoded from that offset stayed
in the listing, and the later `createFunction` reused them. The fix is to
`clearListing()` over the range before re-disassembling.

objdump is authoritative here: its chain starts at `0x1c6648`, which is exactly
where the single `bl` lands, and ends at `pop {r4-r9, sl, fp}` / `bx lr` at
`0x1c67f4`. The field table derived from it stands.

## Assembler limitation

The fragment grammar only accepts `ALU0..ALU3`, so `alu_buffer_size = 2` cannot be
used to get eight ALU slots:

```
fs: line 11: syntax error     # ALU4
```

One ALU instruction writes a single half-register per slot, so four slots cannot
populate all eight halves of `r0..r3`. That blocks the experiment needed to pin
down DW bit 14 - which still always yields a zero payload and is not a pair
select over `r0..r7`.

## The 0x1869f sentinel is general, not scheduler-specific

`0x1869f` (99999) is materialised at **31 sites** in `.text` as
`movw rX, #34463` + `movt rX, #1`, always as a *value to be stored*; there are
**zero** immediate compares against it (`grep 'cmp.*#34463'` finds nothing - the
comparisons are register-to-register). Representative users:

* `0x23f528` - loaded once and copied into several stack slots that initialise a
  local structure;
* `0x25995c` - after `(x + 7) >> 3` (round up, divide by 8) it does
  `movwne r2, #0x869f` / `movtne r2, #1`, i.e. "compute an aligned count and, if
  it is non-zero, use 99999";
* `0x1ee8ec`, `0x25a67c` - loaded as a loop initialiser that is then spilled.

So it is the compiler's generic "no value / infinite" constant, used well beyond
the scheduler. That answers half of the open question: the five 48-entry arrays in
`0x2af724` are seeded with it so that unvisited entries read as *unreachable*
rather than as zero, but the sentinel itself carries no scheduler-specific
meaning.

## Still unresolved: the packer's identity

What *is* pinned down about the caller: it is the function at **0x1c67fc** -
neither `0x1c6800` nor `0x1c6498`. A `bl 0x1c67fc` from `0x1c0e68` confirms it,
and the code begins `ldr r3, [r0, #0x348]` *before* `push {r4-r9, sl, fp, lr}` at
`0x1c6800`. This compiler hoists an argument load ahead of the prologue - the
packer does the same at `0x1c6648` (`ldr r2, [r1, #0x2c]`, `push` at `0x1c664c`).
Taking `0x1c6800` as the entry makes `r3` look uninitialised inside the body
(`ldr r3, [r3, #0xb4]` at `0x1c680c`), which is how the mistake was caught.

Decompiling either function still fails, and the failure modes are now understood:

* `0x1c6648` produces C that models the entry with a *conditional predecessor*
  (`in_ZR` as an input parameter, impossible for a real entry). This survives
  `clearCodeUnits()` plus re-disassembly, so Ghidra's flow-based decode really
  does disagree with objdump's linear decode here;
* `0x1c67fc` produces a single call (`FUN_000f0918()`) for a 1360-byte body, in
  the analysed project *and* in a fresh project imported with `-noanalysis`.

objdump therefore remains the only usable view of this code, and the packer's
field table stands on it.

## Open questions

* which unit the `0x1c6648` 128-bit packer belongs to;
* which payload bits of `dw_enc_src()` the hardware consumes, now measured: on a
  colour store, 15 (`src_regs_select`) and 16..17 (the render target class field)
  are used; 10 and 14 belong to the stencil path and IR opcode `0xf4`; 11..13 and
  18..31 showed no effect at all;
* what the five 48-entry arrays in `0x2af724` are indexed by (the sentinel itself
  is now understood - see above);
