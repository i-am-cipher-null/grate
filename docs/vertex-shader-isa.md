# Tegra 2 (AR20) - vertex shader ISA

**Status.** Reference. The encoding is checked against a command-stream dump and
against words the blob emits (section 14). Fields and opcodes marked `unknown`
have no established meaning.

**Scope.** The NV30-derived vertex VLIW: instruction word, vector and scalar
opcodes, swizzles and masks, predicates, address registers, branching and export.
Related documents: [[Shader Linking]] for what happens to the exports,
[[GR3D Registers]] §3 for the VPE registers, [[Fragment Shader ISA]] for the
other half of the pipeline.

## 1. Overview

The Tegra vertex shader ISA is a close implementation of the Shader Model 2
instruction set, and a strict subset of the NV30 vertex shader as far as the
disassembly and the blob's output show.

Each instruction contains up to two operations; one 4-component vector [ALU](https://en.wikipedia.org/wiki/Arithmetic_logic_unit) operation, and one scalar SFU (special function unit) operation. The result of both units is a 4 component-vector, limited by a write-mask.

There are five operands: one destination register per unit (rD) and three source
operands (rA, rB and rC). The ALU can use all three sources, while the SFU only
operates on rC.

The vertex processor has 32 local vec4 registers, 16 input vec4 attribute
registers, 256 input vec4 constant registers, 16 export vec4 registers, 2
condition registers and 4 address registers. A program is at most 256 VLIW
instructions.

### 1.1 See also

**Nouveau (Mesa).**

http://cgit.freedesktop.org/mesa/mesa/tree/src/gallium/drivers/nouveau/nv30/nv30_vertprog.h  
http://cgit.freedesktop.org/mesa/mesa/tree/src/gallium/drivers/nouveau/nv30/nv30_vertprog.c  
http://cgit.freedesktop.org/mesa/mesa/tree/src/gallium/drivers/nouveau/nv30/nvfx_vertprog.c  

**Instruction-set specifications.**

https://www.opengl.org/registry/specs/NV/vertex_program.txt  
https://www.opengl.org/registry/specs/NV/vertex_program2.txt  
https://www.opengl.org/registry/specs/NV/vertex_program3.txt  

**Patents.**

https://www.google.com/patents/US7755634  

## 2. Instruction word encoding

|     Bits | Meaning                     |
|---------:|:----------------------------|
|      127 | dummy / unused              |
|      126 | export vector write enable  |
|      125 | condition flags write enable|
|      124 | export relative addressing enable |
|      123 | attribute relative addressing enable |
|      122 | saturate result             |
|      121 | condition register index    |
|      120 | unknown                         |
|      119 | rC absolute value           |
|      118 | rB absolute value           |
|      117 | rA absolute value           |
| 111..116 | vector destination register |
|      110 | condition set               |
|      109 | condition check             |
|      108 | predicate - greater than 0  |
|      107 | predicate - equal to 0      |
|      106 | predicate - less than 0     |
|  98..105 | predicate swizzle           |
|   96..97 | address register select     |
|   91..95 | scalar opcode               |
|   86..90 | vector opcode               |
|   76..85 | constant fetch index        |
|   72..75 | attribute fetch index       |
|       71 | rA negate                   |
|   63..70 | rA swizzle                  |
|   57..62 | rA register                 |
|   55..56 | rA type                     |
|       54 | rB negate                   |
|   46..53 | rB swizzle                  |
|   40..45 | rB register                 |
|   38..39 | rB type                     |
|       37 | rC negate                   |
|   29..36 | rC swizzle / IADDR          |
|   23..28 | rC register                 |
|   21..22 | rC type                     |
|   17..20 | scalar op write-mask        |
|   13..16 | vector op write-mask        |
|    7..12 | scalar destination register |
|     2..6 | export write index          |
|        1 | constant relative addressing enable |
|        0 | end of program              |

### 2.1 Vector opcodes

|  Value | Mnemonic | Meaning                                 |
|-------:|:---------|:----------------------------------------|
|      0 | NOP      |                                         |
|      1 | MOV      | rD = rA                                 |
|      2 | MUL      | rD = rA * rB                            |
|      3 | ADD      | rD = rA + rC                            |
|      4 | MAD      | rD = rA * rB + rC                       |
|      5 | DP3      | rD = dot(rA.xyz, rB.xyz)                |
|      6 | DPH      | rD = dot(rA, vec4(rB.xyz, 1.0))         |
|      7 | DP4      | rD = dot(rA, rB)                        |
|      8 | DST      | rD = vec4(1.0, rA.y * rB.y, rB.z, rA.w) |
|      9 | MIN      | rD = min(rA, rB)                        |
|     10 | MAX      | rD = max(rA, rB)                        |
|     11 | SLT      | rD = lessThan(rA, rB)                   |
|     12 | SGE      | rD = greaterThanEqual(rA, rB)           |
|     13 | ARL      | A0 = floor(rA)                          |
|     14 | FRC      | rD = fract(rA)                          |
|     15 | FLR      | rD = floor(rA)                          |
|     16 | SEQ      | rD = equal(rA, rB)                      |
|     17 | SFL      | rD = bvec4(false, false, false, false)  |
|     18 | SGT      | rD = greaterThan(rA, rB)                |
|     19 | SLE      | rD = lessThanEqual(rA, rB)              |
|     20 | SNE      | rD = notEqual(rA, rB)                   |
|     21 | STR      | rD = bvec4(true, true, true, true)      |
|     22 | SSG      | rD = sign(rA)                           |
|     23 | ARR      | A0 = round(rA)                          |
|     24 | MVA      | A0.x = A0.z = A0.x + A0.z<br>A0.y = A0.w = A0.y + A0.w |
|     25 | TXL      | aborts the program, see 5.1                 |
|     26 | PSH      | push(A0)                                |
|     27 | POP      | A0 = pop()                              |
| 28..31 | unknown      | unknown                                     |

### 2.2 Scalar opcodes

|  Value | Mnemonic | Meaning                                                               |
|-------:|:---------|:----------------------------------------------------------------------|
|      0 | NOP      |                                                                       |
|      1 | MOV      | rD = rC                                                               |
|      2 | RCP      | rD = 1.0 / rC                                                         |
|      3 | RCC      | rD = clamp(1.0 / abs(rC), pow(2.0, -64.0), pow(2.0, 64.0)) * sign(rC) |
|      4 | RSQ      | rD = 1.0 / sqrt(rC)                                                   |
|      5 | EXP      | rD = vec4(pow(2.0, floor(rC.x)), fract(rC.x), pow(2.0, rC.x), 1.0)    |
|      6 | LOG      | rD = vec4(floor(log2(abs(rC.x))), abs(rC.x) / pow(2.0, floor(log2(rC.x))), log2(abs(rC.x)), 1.0) |
|      7 | LIT      | rD = vec4(1.0, max(rD.x, 0.0), rD.x > 0.0 ? pow(max(rC.y, 0.0), clamp(rC.w, -128.0, 128.0) : 0.0, 1.0) |
|      8 | BRA      | jump(IADDR) if the predicate test passes, see 10                          |
|      9 | BRI      | jump(IADDR)                                                           |
|     10 | CLA      | push(IP); jump(IADDR), see 11                                              |
|     11 | CLI      | push(IP); jump(IADDR)                                                 |
|     12 | RET      | jump( pop() )                                                         |
|     13 | LG2      | rD = log2(rC)                                                         |
|     14 | EX2      | rD = exp2(rC)                                                         |
|     15 | SIN      | rD = sin(rC)                                                          |
|     16 | COS      | rD = cos(rC)                                                          |
| 17..18 | unknown      | unknown                                                                   |
|     19 | PUSHA    | push(A0)                                                              |
|     20 | POPA     | A0 = pop()                                                            |

### 2.3 Swizzle

| Bits | Meaning            |
|-----:|:-------------------|
| 6..7 | select x-component |
| 4..5 | select y-component |
| 2..3 | select z-component |
| 0..1 | select w-component |

| Value | Meaning            |
|------:|:-------------------|
|     0 | source x-component |
|     1 | source y-component |
|     2 | source z-component |
|     3 | source w-component |

### 2.4 Write mask

| Bits | Meaning           |
|-----:|:------------------|
|    3 | write x-component |
|    2 | write y-component |
|    1 | write z-component |
|    0 | write w-component |

### 2.5 Source operand type

| Value | Meaning            |
|------:|:-------------------|
|     0 | invalid/attribute  |
|     1 | temporary          |
|     2 | attribute          |
|     3 | constant           |

## 3. Predicates

There are 2 condition registers, bit "condition register index" selects register to use. Condition register state is stored per .xyzw component, it is set to "equal to 0" on start of vertex program execution, it is altered accordingly to the rD.xyzw of the executed instruction (per-component) to one of the following states:

1. rD.c less than 0.0
2. rD.c equal to 0.0
3. rD.c greater than 0.0

To update the content of the condition register, bits "condition set" and "condition flags write enable" must be set and resultant register component must be enabled in the op write-mask. Result of a vector operation takes precedence.

To execute instruction conditionally: "condition check" bit needs to be enabled combined with the "predicate - *" bits. The resultant register component will be updated if corresponding condition register component state, selected by predicate swizzle, satisfies the tested predicate.

As the result of a predicate vector instruction, corresponding components of the destination register are set to 1.0f (true) or 0.0f (false).

### 3.1 The predicate field, measured

Bits 106..108 are a three-bit mask over the three comparison results: bit 108
selects "greater than 0", bit 107 "equal to 0" and bit 106 "less than 0". A
destination component is written when the mask bit matching the condition
register state it tests is set, and the test is per component - destination
component `c` reads condition register component `p.c`, so the swizzle picks the
state, not the result. All eight values were run on hardware; see
[[Hardware Testing]] "Vertex predicates" for the programs.

| Field | Options in the assembler | Components written | Reading |
|------:|:--|:--|:--|
| 0 | none | none | never |
| 1 | `(lt)` | `<0` | less than |
| 2 | `(eq)` | `=0` | equal |
| 3 | `(eq)(lt)` | `<0`, `=0` | less or equal |
| 4 | `(gt)` | `>0` | greater |
| 5 | `(gt)(lt)` | `<0`, `>0` | not equal |
| 6 | `(gt)(eq)` | `=0`, `>0` | greater or equal |
| 7 | `(gt)(eq)(lt)` | all four | always |

Two consequences:

* the field is a mask, not a comparison code, so every one of the six useful
tests is expressible directly; there is no separate "not equal" opcode;
* the field alone does nothing. With `condition check` (bit 109) clear the
predicate bits are ignored and the whole write happens (measured: `0xFFFFFFFF`
with the field at 0), so a conditional write always needs bit 109. The branch
instructions are the exception, see section 10.

## 4. Scalar instructions
If vector opcode isn't NOP and rD is same as scalar's, then vector result takes precedence.

First component of swizzled source register rC determines actual source for scalar operation and result of that operation is repeated to .xyzw of destination register rD, taking write mask into account.

### 4.1 MOV instruction
Scalar's MOV acts as vector's MOV, i.e. it fetches and writes all .xyzw components.

## 5. Execution abortion
Program execution aborts if:

  1. rD, rA, rB or rC is set to an invalid value, even if it's not used by a particular instruction. For rA, rB and rC the invalid range is 32-63, for rD it is 32-62.
  2. Export register number is invalid value 16-30.
  3. On stack overflow/underflow.

### 5.1 Vector opcode 25 stops the program

An instruction carrying vector opcode 25 (`TXL`) has no effect on its
destination and no later instruction runs. The vertex is still emitted, with the
exports written before it. Measured three ways, each a full-frame run with the
varying-only fragment shader:

| program | observation | reading |
|:--|:--|:--|
| `TXLv r63.xyzw` first, the position export after it | clear colour `0x4C4C4C4C` | no vertex at all, so the export after it never ran |
| `TXLv r0.xyzw` in the middle, an export of a known constant after it | `0xFF000000` | the later export never reached the framebuffer; `0xFF000000` is what an unwritten varying reads as |
| `TXLv r63.xyzw` with `export[7]`, after the export was preloaded with `(0.25, 0.5, 0.75, 1.0)` | `0xFFBF7F40` | the preloaded value survives, so the instruction is dropped before it can write |

`TXLv` takes only a destination in the assembler, so its two source slots stay
at type 0, which section 2.5 lists as "invalid/attribute" - the abort is
therefore consistent with case 1 above. Whether a fully specified opcode 25
faults the same way cannot be tested until the grammar can express its operands.
The blob never emits it, so what a compiler needs to know is that it cannot be
used as a texture fetch.

## 6. Export

In order to use result of scalar or vector operation further in shader pipeline, it needs to be stored in the export register.

To write to the export:

1. "export write index" bitfield selected to the required export valid register number (0-15, 31).
2. To write the result of the vector instruction, bit "export vector write enable" needs be set and rD must be either a valid destination register 0-31 or a dummy 63, which is used when only write out is desired without clobbering some of the local registers.
3. To write the result of the scalar instruction, bit "export vector write enable" needs be unset.

When export register number is 31, export is disabled.

The respective components of the resultant vector of the executed instruction, enabled by the vector/scalar write-mask, will be written to the export register, so consecutively executed instructions may alter only required export register components.

To use relative addressing:

  1. Bit "export relative addressing enable" needs to be set.
  2. Bitfield "address register select" selected to the required address register.

Out-of-bounds export indices are not checked:

    export write index = A0.c + export write index

Export index 0 is used for `gl_Position` by both the blob and the assembler.

## 7. Address registers
There are 4 relative base address registers (A0.xyzw). The ARL (address register load, rA floored) and ARR (address register load, rA rounded) vector operations are altering content of the address registers, so that each component of source register rA.xyzw represents the corresponding address register. The ARA (address register addition) adds 2 address register components together, so that A0 = (A0.x + A0.z, A0.y + A0.w, A0.x + A0.z, A0.y + A0.w).

Destination vector register write mask enables write to the address register component, the actual destination vector register isn't getting affected (like nv30). The address register value can be negative.

Note on ARR/ARL/ARA instructions: the destination vector register (rD) should be even value, otherwise address register isn't updated.

Note on a bit 120: when it is set, the fetched address register is overridden as A0.xyzw = (0.0f, 0.0f, 0.0f, 0.0f).

| Address register select | Component |
|:-----------------------:|:---------:|
|                       0 | A0.x      |
|                       1 | A0.y      |
|                       2 | A0.z      |
|                       3 | A0.w      |

Address register A0.xyzw can be pushed/popped to the stack with a PUSHA/POPA scalar and vector operations. Simultaneous and identical scalar-vector push/pop operation within one instruction results into only one push/pop actually being performed, while simultaneous and non-identical into the no-op.

## 8. Constant registers
To multiplex source register rA/rB/rC to constant, its type needs to be set to "constant" and bitfield "constant fetch index" (in range of 0..1023) pointed to the required constant.

To use relative addressing:
  1. Bit "constant relative addressing enable" needs to be set.
  2. Bitfield "address register select" selected to the required address register.

Since the range of "constant fetch index" is 0..1023, the valid address register range is -1023..1023.

When constant index is out of range, the fetched constant value is assigned to vec4(0.0f, 0.0f, 0.0f, 0.0f) if fetched constant index > 1023 and to constant[1] if fetched constant index < 0.

    fetched constant index = A0.c + constant fetch index

## 9. Attribute registers
To multiplex source register rA/rB/rC to attribute, its type needs to be set to "attribute" and bitfield "attribute fetch index" pointed to the required vertex attribute.

To use relative addressing:
  1. Bit "attribute relative addressing enable" needs to be set.
  2. Bitfield "address register select" selected to the required address register.

Out-of-bounds attribute indices are not checked:

    fetched attribute index = A0.c + attribute fetch index

## 10. Branching
Scalars BRA operation is used to jump to an arbitrary instruction. Following nouveau terminology, an argument of the BRA operation is the destination instruction ID (IADDR), it is embedded into the rC swizzle bitfield.

Branching is performed only if the instruction predicate test is positive. So use of "predicate - *" bits is mandatory for the branching instruction.

Measured on hardware, with `BRAs 3` as the first instruction and an
end-of-program instruction at 1 and 2:

| predicate | result | reading |
|:--|:--|:--|
| `(eq)`, condition register 0 still at its start state (`=0`) | a vertex is drawn | the branch is taken: instructions 1 and 2 are skipped |
| `(gt)`, same start state | `0x4C4C4C4C` (clear colour) | the branch falls through, the end-of-program instruction at 1 runs, nothing is drawn |

So the branch is conditional on the predicate field, and it needs neither
`condition check` (bit 109) nor a source operand. The predicate field keeps the
meaning given in section 3.1: `(gt)(lt)` is the "not equal to 0" test the blob
uses on its loop edges.

## 11. Functions
Scalar instructions CAL and RET are used for the function calling and returning. Function call is essentially a branching operation with an instruction pointer being pushed to the stack. Return from the function is a branching to the address popped from the stack. Requirement of the predicate use is the same as for branching operation for both of CAL and RET operations, CAL's IADDR is embedded to the rC swizzle as well.

Measured: a `CALs`/`RETs` pair - the callee writes a varying and returns - works
on hardware (`tests/grate/asm/vs_function`), as does pushing and popping the
address register around a call (`vs_stack`). Both are part of the regression
suite.

## 12. Stack
Stack is shared by the PUSHA/POPA and CAL/RET operations. Maximum depth of the stack is 8.

## 13. Clamping
If bit "saturate result" is set, both vector and scalar operation results are clamped to [0.0f - 1.0f].

## 14. Verification against a command-stream dump

The vertex program is uploaded inside the draw job, so when a channel times out
the kernel echoes the packet back and the field table above can be checked
without any guessing. `tests/grate/asm/vs_mov.txt` is

```
EXEC(export[0]=vector) MOVv r0.xyzw, a[0].xyzw ; NOPs
EXEC(export[7]=vector) MOVv r0.xyzw, a[1].xyzw ; NOPs
```

and the posted packet is two instructions of four dwords, **high dword first**:

```
NONINCR(offset=206, [4000006c, 0040000d, 8106c003, 6001ff80,
                     4000006c, 0040010d, 8106c003, 6001ff9d])
```

Decoding the first one against the table gives every field at its documented
place:

| Field | Bits | Value | Reading |
|:--|--:|--:|:--|
| export vector write enable | 126 | 1 | vector export |
| vector destination register | 111..116 | 0 | `r0` |
| rA type | 55..56 | 2 | attribute |
| rA register | 57..62 | 0 | `a0` |
| rA swizzle | 63..70 | `0x1B` | `.xyzw` |
| attribute fetch index | 72..75 | 0 | `a[0]` |
| vector op write-mask | 13..16 | `0xF` | `.xyzw` |
| scalar destination register | 7..12 | 63 | unused (dirty) |
| export write index | 2..6 | 0 | export 0 |
| end of program | 0 | 0 | not the last instruction |

The second instruction is identical except for `attribute fetch index` = 1 and
`end of program` = 1, exactly as the source says.

**The blob and Grate's assembler agree on every semantic field.** Compiling
`gl_Position = aPos` gives `401f9c6c 0040000d 8106c083 6041ff81`; compiling the
two-output shader (`tc = aTC; gl_Position = aPos`) gives that plus
`401f9c6c 00400108 0106c083 60419f9d` for `mov o7.xy, v1.xyxx` (`export index`
7, write mask `0xC` = `.xy`, attribute index 1).

| Field | Grate assembler | Blob |
|:--|:--|:--|
| vector destination | `r0` | **63** (write-export-only) |
| export write index | 0, 7 | 0, 7 |
| vector op write-mask | `0xF`, `0xF` | `0xF`, `0xC` |
| attribute fetch index | 0, 1 | 0, 1 |
| rA swizzle | `0x1B` (`.xyzw`) | `0x1B` (`.xyzw`) |
| scalar destination | 63 | 63 |
| predicate bits 106..108 | 0 | all three set |
| unused operand type bits 22, 39 | 0 | 2 (`attribute`) |
| end of program | last instruction | last instruction |

Both differences are legal, not a disagreement about the field map. Destination
63 is the documented "write out without clobbering a local register" form, and
the predicate bits do nothing while condition-check (bit 109) is clear. A
compiler that wants the blob's exact word shape should use 63 as the vector
destination whenever the result is only exported, and may set the three
predicate bits as a conservative default.

## 15. Opcode usage observed in compiler output

Decoding blob output for single-construct shaders shows which opcode numbers are
real. Vector opcodes seen: `1` MOV, `2` MUL, `3` ADD, `4` MAD, `9` MIN, `10` MAX,
`11` SLT, `14` FRC, `15` FLR, `16` SEQ, `18` SGT.

| Construct | Emitted as |
|:--|:--|
| `gl_Position = v` | `MOV`, `rD = 63`, export 0 |
| `dot(a.xy, a.zw)` | `MUL` into a temporary, then `ADD` |
| `min`, `max` | `MIN` (9), `MAX` (10), one component each |
| `floor`, `fract` | `FLR` (15), `FRC` (14) |
| `sign(x)` | `SGT` (18) and `SLT` (11) against a constant |
| `equal`, `lessThan` | `SEQ` (16), `SLT` (11), summed with `ADD` |
| `clamp(x, 0.0, 1.0)` | one `MOV` with **saturate** (bit 122) - no MIN/MAX pair |
| `if (x > c) p = 1.0; else p = 0.0;` | one `SGT` writing the whole export; the boolean *is* the answer, so no branch appears |
| `for (i = 0; i < 4; i++)` | unrolled, no branch |

Scalar side: `0` NOP, `1` MOV (with `condition flags write enable` set, which is
how the compiler materialises a condition), and **`9` = BRI**. In a loop with a
uniform bound the blob emits two BRI words, one per edge, with the destination
instruction index embedded in the `rC` swizzle field exactly as described under
"Branching":

```
0x001f88004800000d8106c08100401ffc   scalop=9  preds=eq     rC=..xxzx
0x001f94004800000d8106c08080401ffc   scalop=9  preds=gt|lt  rC=..xxyx
```

Never observed *in compiler output*: vector `25` and `28..31`, scalar `17..18`,
and `BRA`/`CAL`/`RET` - the compiler used BRI for both loop edges and called
nothing. Of those, `BRA`, `CAL` and `RET` do work when the assembler emits them
(sections 10 and 11), and vector `25` stops the program (section 5.1); vector
`28..31` and scalar `17..18` have no spelling in the grammar and remain
untested.

Two mechanics the output settles:

* **`condition check` (bit 109) is clear in both branch words** while the
  predicate bits are set, so a scalar branch appears to test bits 106..108
  directly and bit 109 is the gate for ordinary conditional writes, not for the
  branch itself.
* **The condition register is really used.** The compare feeding the branch
  carries `condition set` (bit 110) with `condition flags write enable`
  (bit 125) and a scalar write-mask, and the branch reads it back.

The predicate field is now measured rather than guessed: `gt|lt` is a real "not
equal to zero" test (section 3.1), which is what the blob's loop edges use it
for. Whether the blob ever emits a `CAL` for a GLSL function is still open - it
inlined everything tried so far - but a compiler does not need it: inlining
avoids the 8-entry stack (section 12) and the hardware call path offers nothing
that a branch does not.