# Assembler and disassembler audit: raw words first

This audit treats the instruction words as the evidence and both tools as
research instruments with unverified coverage.  Nothing below changes an
experimental conclusion; the point is to establish which fields a probe can
actually reach, which bits a decoder silently loses, and where the tooling
replaces a value without saying so.

It was made after notes 56..63, where every probe was written in the structured
syntax.  The measured results in those notes stand, but the encoding path they
went through is now known in detail, and one property of it matters for
re-reading them: **none of the 109 checked-in probe programs under
`tests/grate/asm/` uses a raw hex form, and none of the 66 generated programs of
note 63's paired run contains an `alu_complement` word**, so every one of those
probes ran with the assembler's default of `0x00000000` in the 0x806 register.

## 1. What the tools are, and how to reproduce this audit

| piece | file |
|:--|:--|
| assembler grammar (fragment) | `src/libgrate/fragment_asm.y` |
| assembler lexer (fragment) | `src/libgrate/fragment_asm.l` |
| field layout (fragment) | `src/libgrate/fragment_asm.h`, `src/libgrate/vpe_vliw.h` |
| emitter (command stream) | `src/libgrate/grate-asm.c` |
| disassembler | `src/libgrate/fragment_disasm.c` |
| harness with the GPU | `tools/assembler.c` |
| new raw dump | `src/libgrate/shader_words.c`, `tools/fsraw.c` |
| new encoding tests | `tests/grate/asm/tooling_test.c` |

Reproduce with:

```
meson setup --reconfigure build && ninja -C build
build/tools/fsraw            tests/grate/asm/<probe>.in   # raw + symbolic
build/tests/grate/asm/tooling_test /tmp                   # 206 checks
meson test -C build asm-tooling
```

The tooling_test does not touch the GPU.  It assembles sources and compares word
streams, so it runs on any host where libgrate builds.

## 2. Assembler coverage, unit by unit

A "structured field" is one the grammar can set by name.  "Raw only" means the
value can be reached only through the `<unit> 0x...` escape, which exists for
PSEQ, MFU, TEX, ALU and DW.

### PSEQ (`fragment_asm.y:486`)

| form | fields set | notes |
|:--|:--|:--|
| `PSEQ: 0xHHHHHHHH` | all 32 bits | raw escape |
| `PSEQ: load rtN, r0, r1` / `r2, r3` | target nibble, dest-pair bit | word is `0x00800008 | (rt << 16) | (pair << 1)`: enable bits and 0x00800008 are fixed |
| `PSEQ: NOP` | none (0) | |

Unreachable structured: the low enable bit alone, the high enable bit alone, any
target/destination combination other than the two register pairs, and every
other bit of the word.

### MFU (`fragment_asm.y:538`, `:544`, `:595`, `:607`, `:665`)

| form | fields set | notes |
|:--|:--|:--|
| `MFU: 0x<up to 16 hex>` | 64 bits | low half first (the literal is a uint64) |
| `MFU: 0xHI, 0xLO` | 64 bits | **high half first** - the opposite order to the single-literal form |
| `MFU: sfu: <op> rN` | `opcode` (4 bits, 0..11 named), `reg` (6 bits) | `reg` is the operand field; bit 3 of it has measured side effects and the grammar cannot name that separately |
| `MFU: mul0: dst, src, src` | `mul0_dst` (3 bits), `mul0_src0/1` (4 bits each) | same for `mul1:` |
| `MFU: ipl: tN.fp20, ...` | 4 x (`saturate`, `opcode` 2 bits, `source` 4 bits) | up to four vars; `t0..t15` checked |

Unreachable structured: the 4-bit padding nibble at bits 60..63, MFU opcodes
12..15, `var*_opcode` value 3, and any `mul` selector value not named by a token
(they are reachable as `src<N>`/`dst<N>` up to the field width).

### TEX (`fragment_asm.y:880`, `:790`-ish)

| form | fields set | notes |
|:--|:--|:--|
| `TEX: 0xHHHHHHHH` | all 32 bits | raw escape |
| `TEX: texN, r0, r1, texS, r0, r1, r2` | sampler (4 bits, checked 0..15), `src_regs_select`, `sample_dst_regs_select`, `enable` | only two register patterns are accepted |
| `TEX: txb ... , r3` / `txl ... , r3` | plus `enable_bias` / `explicit_lod` | same two patterns |
| `TEX: NOP` | none | |

Unreachable structured: `unk_6_9`, `unk_11`, `unk_14_31`, and any `src_regs_select`
pattern outside the two encoded pairs; non-`NOP` TEX with no destination pair is
also not expressible.

### ALU (`fragment_asm.y:928`, `:1563`)

| form | fields set | notes |
|:--|:--|:--|
| `ALU0..ALU3: 0xW0, 0xW1` | **words 0 and 1 of the packet only** | words 2..7 stay at the NOP filler `0x000FE7E8 / 0x3E41F200` |
| `ALU0..3: MAD/MUL/MIN/MAX/CSEL dst.*, src, src, src[, rB|rC]` | all four instructions' fields | `dst` needs an explicit `.l`, `.h` or `.*` suffix |
| `ALU3: imm0..2 = <fp20>` or `.l`/`.h = <fx10>` | immediate words | ALU3 slot only; the emitter swaps part6/part7 to compensate for its reversed word order |
| `ALU_COMPLEMENT: 0xHHHHHHHH` | 0x806 word | **absent means 0**, silently |

Measured (fsraw on a one-ALU-packet program):

```
ALU0: 0xAABBCCDD, 0x11223344
  [ 13] 0xAABBCCDD  data      <- word 0
  [ 14] 0x11223344  data      <- word 1
  [ 15] 0x000FE7E8  data      <- filler, not settable from hex
  [ 16] 0x3E41F200  data
  ...
```

So an eight-word ALU packet cannot be reproduced from raw hex; only its first
pair can.  Words 2..7 can only be reached through the structured grammar, and
`part6/part7` only through an ALU3 immediate - which is why the emitter and the
disassembler both apply a swap there.

### DW (`fragment_asm.y:1590`)

| form | fields set | notes |
|:--|:--|:--|
| `DW: 0xHHHHHHHH` | all 32 bits | raw escape |
| `DW: store rtN, r0, r1 [class N]` | `enable`, `render_target_index` (4 bits, checked 0..15), `src_regs_select`, `render_target_class` (2 bits, checked 0..3) | class defaults to 2 |
| `DW: store stencil` | `enable`, `stencil_write`, index 2, class 2 | |
| `DW: NOP` | none | |

Unreachable structured: `unk_1`, `unk_6_9`, `unk_11_14`, `unk_18_31`; spill modes
2 and 3 (the disassembler prints them, the grammar cannot write them).

### Program-level words

| directive | field | check |
|:--|:--|:--|
| `alu_buffer_size = N` | 2 bits at 0xe20 | now 1..4 (see hazard 1) |
| `pseq_to_dw_exec_nb = N` | 7 bits at 0x546 | now 0..127 (see hazard 2) |

The MFU and ALU schedule words (`0x601`, `0x801`) have **no syntax at all**.
They are computed from the instruction count and packet address, so a probe
cannot express a schedule word except through the `GRATE_ALU_SCHED_OR` env hook
in `grate-asm.c:406`.  Anything a probe claims about schedule words is therefore
a claim about the assembler's choice, not about a field it varied.

## 3. Silent truncation and normalization hazards

These are the places where the grammar accepted a value the destination could
not hold, or replaced one.  Hazards 1..4 were fixed in this change; the rest are
recorded and left alone, because fixing them would change encodings that existing
probes depend on.

1. **`alu_buffer_size = 0` became 4.** The check was `if ($3 != 0 && $3 > 4)`, so
   0 passed; `grate-3d.c:335` then writes `alu_buf_size - 1` into a 2-bit field
   (0xFFFFFFFF -> 3) and `grate-3d.c:337` divides by `alu_buf_size * 4` = 0.
   *Fixed:* 0 is rejected.
2. **`pseq_to_dw_exec_nb` had no bound.** The field is read back as `7 bits` at
   `grate-asm.c:596`, so 128..255 wrapped onto 0..127.  *Fixed:* 0..127.
3. **`mfu mul0: dstN` accepted any number** into a 3-bit field: `dst8` == `dst0`,
   `dst9` == `dst1`.  *Fixed:* 0..7.
4. **`mfu ... srcN` accepted any number** into a 4-bit field: `src16` == `r0`,
   `src26` == `sfu` (10).  The alias onto `sfu` is the dangerous one, because
   `sfu` is a distinct encoded value that a typo could produce.  *Fixed:* 0..15.
5. **`mfu sfu: <op> rN` had no bound** on the 6-bit operand field: `r64` == `r0`.
   *Fixed:* 0..63.
6. **MFU raw halves are ordered one way and the single literal the other.**
   `mfu 0x1122334455667788` sets part0 = 0x55667788; `mfu 0x11223344, 0x55667788`
   sets part0 = 0x55667788, part1 = 0x11223344 - consistent - but
   `mfu 0xHI, 0xLO` reads as high-first while the literal reads low-first.  A
   probe that copies a pair from a stream dump in stream order is writing
   high-first, which is correct for the two-argument form only.
7. **A missing `alu_complement` silently writes 0x00000000** to 0x806 for every
   batch.  All 66 generated programs of note 63's paired run are in this state,
   and their ALU writes take effect, so 0 is not "no writes"; the word's role is
   simply not established.  A probe that varies it must say so explicitly.
8. **ALU raw words 2..7 are unreachable** (section 2).  A probe that reports an
   ALU packet from a hex pair is reporting the filler for six of the eight words.
9. **The ALU3 immediate word order is swapped before emit.**  `part6` and
   `part7` trade places in `fragment_asm.y`, so the stream word that holds the
   immediate fields is not `part6` of the struct.  Measured: `ALU3: imm0 = 0.5`
   gives `0x3E3C0000` in word 6 of the packet and the filler in word 7, while the
   struct names them the other way round.  The new dump and the disassembler's
   raw line print stream order to avoid this trap.
10. **`float_to_fp20` truncates** the float mantissa from 23 to 13 bits and
    `float_to_fx10` masks `f * 256` to 10 bits (`fragment_asm.y:130`, `:146`).
    Neither warns; `[i].l = 1.5` below 1/256 or above 4.0 saturates silently.
11. **Uniform names are `strcpy`'d** into a 256-byte field after a length check of
    the whole quoted string (`fragment_asm.l`), and `asm_fs_uniforms` is indexed
    `2 * index` with index <= 31, so the last pair is 62/63 of 64 - in bounds, but
    only just.

## 4. Disassembler coverage

Legend: *printed* = shown symbolically; *raw* = shown as hex when skies are not
clear; *dropped* = never shown.

| unit | printed | raw fallback | dropped |
|:--|:--|:--|:--|
| PSEQ | `load rtN, rX, rY` or `NOP` | whole word when any bit outside {0x00800008, target nibble, pair bit} is set | nothing silently, but the raw form is only a comment |
| MFU | opcode, reg, both muls, four ipl vars | now: `; raw <HI> <LO>` plus `unk_pad=0xN` when the nibble is set | before this change: **the padding nibble was invisible** (measured below) |
| TEX | tex/txb/txl, registers, sampler | whole word when `unk_6_9`, `unk_11`, `unk_14_31` set | `src_regs_select` bit patterns other than the two printed (they print as the `r0,r1,r2` form) |
| ALU | four operations, modifiers, immediates used | now: `; raw <8 words in stream order>` | `rD_fixed10` (an rD written at low precision prints without `.l`/`.h`), `rD_minus_one`, immediates not referenced by a decoded operand |
| DW | store rtN / stencil / spill / NOP | whole word when unknown bits or spill mode | `unk_1`, `unk_6_9`, `unk_11_14`, `unk_18_31` are a comment, not a decode |
| program | `alu_buffer_size`, `pseq_to_dw_exec_nb` | - | the schedule words are not shown at all |

Measured, padding invisibility (before the change):

```
MFU: 0x00000000, 0xF0000000     ->  sfu: nop r0 / mul0: dst0, r0, r0 / ...
MFU: 0x00000000, 0x00000000     ->  sfu: nop r0 / mul0: dst0, r0, r0 / ...
```

Byte-identical text for two different words.  The raw line now separates them,
and `unk_pad=0xF` names the nibble instead of calling it reserved.

The disassembler also states the `.fp20`/`.fx10` precision of an ipl source from
the `opcode` field, and names `sfu`, `bar0`, `bar1`, `#1` from the mul-source
field.  Per the research rule these are separate encoded facts - operand selector,
mul-source code, opcode - and after this change they are printed as such: the
symbolic line keeps the reading, and the raw line keeps the word.  The decoder
still does **not** encode the measured selector semantics of notes 56..62; it
prints `srcN` for a value it has no name for rather than guessing.

## 5. Raw-word support

Before this change:

* `tools/assembler.c` with `--testonly GRATE_DUMP_RAW=1` prints vertex/fragment
  constants and every program word of VS, FS and linker (`dump_raw`).  That is
  whole-program only: no per-unit words, no batch position, no ALU packet split.
* `tests/grate/asm/fs_words.c` prints the FS word stream from a source file
  (unbuilt by meson).
* `tests/grate/asm/disasm_fs.c` prints the symbolic disassembly (unbuilt).

After this change:

* `grate_shader_words_dump(shader)` returns the complete stream with the word
  offset of every word, the packet each word belongs to, and then per exec batch
  the PSEQ, MFU-schedule, TEX, ALU-schedule, complement and DW words, followed by
  every MFU pair and every ALU packet with its eight words and a field decode
  that includes the padding nibble, `sub_reg_select_high`, `fixed10` and
  `minus_one` by value.
* `fsraw <file.asm>...` prints that dump and then the symbolic disassembly under
  a `-- disassembly (hypothesis)` heading, so the two never merge.
* The disassembler's MFU and ALU entries now carry `; raw ...` lines in stream
  order.

The dump does not decode immediates, does not name any opcode, and does not
sort or regroup words: it prints them in the order the stream holds them, which
is the property the note-63 investigation needed and lacked.

## 6. Tests

`tests/grate/asm/tooling_test.c` (built by `tests/grate/asm/meson.build`,
registered as `asm-tooling`) checks:

1. a structured MFU instruction and the raw hex halves of the same word assemble
   to **identical** streams;
2. `src0..src15` and `dst4..dst7` each produce a distinct stream (16 and 4
   checks), so no value aliases another;
3. `src16`, `dst8` and `sfu: lg2 r64` are rejected;
4. `pseq_to_dw_exec_nb = 200`, `alu_buffer_size = 5` and `= 0` are rejected;
5. every word of the stream appears in the raw dump, the dump has a unit summary
   and an exec batch, and every `; raw` line of the disassembly is a run of
   stream words in that order - so the reading cannot drift from the evidence.

Result on `kusma-ac100`: **206 checks, 0 failures**, and the six rejection cases
print their parse errors.  `meson test` reports `Ok: 1`.

Not covered, and named so nobody assumes otherwise: the ALU words 2..7 cannot be
round-tripped from hex because there is no hex form for them; TEX and DW hex
forms are exercised only by the field checks above, not by a stream comparison;
the scheduler words have no test because they have no source syntax; and nothing
here tests hardware behaviour.

## 7. What was deliberately not changed

* The existing assembly syntax and every encoding of a valid existing probe: the
  four new range checks reject only inputs that previously aliased silently.
* `docs/fragment-isa-cleanup` and `docs_snap`: the stable ISA documentation is
  untouched, and no conclusion in it was revised from tool behaviour.
* The disassembler's symbolic text: the only additions are the `; raw` lines and
  the `unk_pad` note, both comments on the existing lines.
* `tests/grate/asm/*.in`: they are templates with `@SLOT@` placeholders, not
  runnable programs, and were left as they are.

## 8. Open items

* The ALU packet's words 2..7 have no hex form; a raw-first probe cannot
  reproduce a captured packet.  Adding `aluN <8 words>` would need a new grammar
  rule and a decision about part6/part7 ordering; left for a separate change.
* The schedule words are unreachable except through an environment variable; a
  `mfu_sched`/`alu_sched` hex form would make a scheduling probe expressible.
* `complement = 0` when unspecified is silent.  The honest fix is a printed
  notice, not a default, because no measurement here establishes what 0 means.
* The disassembler still cannot print `rD_fixed10`; the raw line is the workaround.
