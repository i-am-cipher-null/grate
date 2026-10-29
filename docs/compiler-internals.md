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

## Open questions

* which unit the `0x1c6648` 128-bit packer belongs to;
* the encoder for the fragment DW instruction proper - only the patch sites above
  are located so far;
* the meaning of the `0x1869f` sentinel and the five 48-entry arrays in the
  scheduler.
