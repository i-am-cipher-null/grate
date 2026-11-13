# Tegra 2 (AR20) - assembler syntax

**Status.** Reference for `tools/assembler` and the Grate grammar in
`src/libgrate/`. Where a mnemonic's meaning is not the hardware encoding, the
hardware encoding in [[Fragment Shader ISA]] / [[Vertex Shader ISA]] is
authoritative.

**Scope.** The vertex, fragment and linker grammars: sections, parameters,
uniforms, every mnemonic the parser accepts, and the field syntax of each
instruction. This document is the executable reference for the encodings
documented elsewhere.

## 1. Vertex assembler

A vertex program is written as five sections:

### 1.1 .attributes

Defines input vertex attributes that will be enabled in the input attributes mask.

Example:

	.attributes
		[0] = "position";
		[1] = "color";

Here attributes 0 and 1 will be enable in the input mask. 

### 1.2 .exports

Defines output vertex registers that will be enabled in the output attributes mask.

Example:

	.exports
		[0] = "gl_Position";
		[7] = "vcolor";

Here export registers 0 and 7 will be enable in the output mask. 

### 1.3 .constants

Defines 32bit float constants that will be populated into the constant/uniform vec4 vertex registers.

Example:

	.constants
		[0].y  = 1.5;
		[12].z = -100.0;

Here a float value 1.5 will be loaded to the "y" component of the constant register 0 and value -100.0 to the "z" of register 12.

### 1.4 .uniforms

Defines locations of uniforms that will be exposed to a shader user.

Example:

	.uniforms
		[100] = "uniform_100";
		[135] = "uniform_135";

Here two constants locations 100 and 135 will be available for a lookup to a user of the shader program by names "uniform_100" and "uniform_135" respectively.

### 1.5 .asm

Defines the actual vertex assembler. Instruction consists of the "EXEC" preamble, modifiers given in parens, one vector operation and one scalar operation, a termination semicolon.

Preamble:

There are two kinds of the instruction preamble: a regular "EXEC" and "EXEC_END". The latter will set the instructions "end of program" bit. 

Modifiers:
- p.xyzw - predicate swizzle, swizzles the condition register components when performing condition check
- cs - condition set
- eq - condition register state test: equal to 0
- lt - condition register state test: less than 0
- gt - condition register state test: greater than 0
- cc - condition check enable
- cwr - condition register write enable
- cr=n - selects condition register "n" to use, where "n" is either 0 or 1.
- export[n]=src - selects export register "n" and it's "src", where "src" is either "vector" or "scalar"

Operations:

A typical operation takes the following form:

	OPCODE rD.mask, -mod(rA.swizzle), -mod(rB.swizzle), -mod(rC.swizzle)

- OPCODE is one of scalar/vector opcodes.
- rD mask "xyzw" / "*" defines the write-enable mask.
- The source register modifier "mod" is optional, where "mod" is "abs" (absolute).
- The scalar NOPs or vector NOPv operations could be omitted for brevity.
- Source registers (rA, rB, rC) are: general purpose rN, where N is 0-31; constant c[N], where N is 0-255; attribute
a[N], where N is 0-31.
- Destination register rD is rN or address register A0, depending on the operation.
- Relative addressing could be applied to the constant, attribute and export registers, hence they are given in square brackets.
- Source register swizzle "xyzw", swizzles the respective components of the source register.

Example:

	.asm
	EXEC(export[A0.z + 3]=vector)
		MOVv r0.x**w, -abs(a[0].xyzw)
		NOPs
	;

Here scalar operation is NOP, vector operation is MOV: the content of "x" and "w" components of an attribute register a0 (absolute'd and negated) will be written to the respective components of the destination register r0, as well as to the export register, which is addressed relatively to the address register A0.z by +3. 

## 2. Fragment assembler

The fragment assembler is defined by the fragment program parameters, the uniform constants and the instructions.

### 2.1 Parameters

Fragment program parameters must precede the .constants and .asm sections, they are:
- alu_buffer_size = N, where N is 1..4 - how many rows are in flight, one row per exec batch. A packet can only fill one row group (`ALU0..ALU3`), and the harness programs the `0x520` start word from the batch count, so N = 1 is the only value that runs as assembled: at N > 1 the count must be `batches * N`, which nothing in this toolchain writes yet (see [[GR3D Registers]] 14.2).
- pseq_to_dw_exec_nb = N, where N is 0..? - the number of PSEQ instructions executed before the non-NOP DW operation.

### 2.2 Constants

The .constants code section defines the fragment program constants. The postfix of the constant register index defines the format of the float value and it's sub-location within the register value:
- ".l" and ".h" the float is converted to fx10 and stored in low/high halves of the constant register respectively
- no postfix - float is converted to fp20

A 32bit raw hex value could be used as well.

Example:

	.constants
		[0]   = 0.1;
		[1].l = 0.2;
		[1].h = -0.3;
		[2]   = 0x12345678;

Here: 0.1 is stored in the uniform register 0 as fp20, 0.2 is stored in the low halve uniform register 1 as fx10, -0.3 is stored in the high halve uniform register 1 as fx10, a raw hex value 0x12345678 is written to the uniform register 2.

### 2.3 Uniforms

The .uniforms code section defines the fragment program uniforms locations that will be exposed to a shader user. Syntax is akin to the .constants section, a uniform location name is given instead of a constant value.

Example:

	.uniforms
		[0]   = "uniform_0_fp20";
		[1].l = "uniform_1_fx10_low";
		[3].h = "uniform_3_fx10_high;

Here 3 uniforms will be available for a lookup to a shader user:
- fp20 typed "uniform_0_fp20" of constant 0
- fx10 typed "uniform_1_fx10_low" of constant 1 low halve
- fx10 typed "uniform_3_fx10_high" of constant 3 high halve

### 2.4 Instructions

The .asm section code section defines the actual fragment program instructions. The instruction starts with the "EXEC" preamble, consists of 5 sub-instructions, each representing the respective stage of the fragment processor pipeline, and ends with the termination semicolon.

Fragment instruction takes the following form:

	EXEC
		PSEQ:	OPCODE operands
		MFU:	OPCODE operands
		TEX:	OPCODE operands
		ALU:
			ALU0:	OPCODE operands
			ALU1:	OPCODE operands
			ALU2:	OPCODE operands
			ALU3:	OPCODE operands
		ALU_COMPLEMENT: HEX
		DW:	OPCODE operands
	;

For brevity, "OPCODE operands" could be replaced with a "NOP", so the sub-instruction will be set to the respective No-Operation opcode.

Example:

	EXEC
		PSEQ:	NOP
		MFU:	NOP
		TEX:	NOP
		ALU:
			ALU0:	NOP
			ALU1:	NOP
			ALU2:	NOP
			ALU3:	NOP
		DW:	NOP
	;

Here all sub-instructions are NOP's.

The instruction schedule allows up to 3 MFU and 3 ALU sub-instructions to be executed in series, as well as skipping them.

To execute more than one MFU and/or ALU instruction in series, additional sub-instruction simply should be appended to the respective stage.

Example:

	EXEC
		PSEQ:	OPCODE operands

		MFU:	OPCODE operands
		MFU:	OPCODE operands
		MFU:	OPCODE operands

		TEX:	OPCODE operands

		ALU:
			ALU0:	OPCODE operands
			ALU1:	OPCODE operands
			ALU2:	OPCODE operands
			ALU3:	OPCODE operands
		ALU:
			ALU0:	OPCODE operands
			ALU1:	OPCODE operands
			ALU2:	OPCODE operands
			ALU3:	OPCODE operands
		ALU_COMPLEMENT: HEX

		DW:	OPCODE operands
	;

Here three MFU sub-instructions and two ALU sub-instructions will be executed consecutively.

To skip the MFU and/or ALU stages, that is to schedule nothing for them in an exec
batch, omit the sub-instruction.

Example:

	EXEC
		PSEQ:	OPCODE operands
		TEX:	OPCODE operands
		DW:	OPCODE operands
	;

Here no MFU and no ALU sub-instructions will be scheduled-executed.

For brevity, PSEQ/TEX/DW sub-instructions and ALU_COMPLEMENT could be omitted from the instruction exec batch, they will be NOP's effectively.

Example:

	EXEC
		MFU:	OPCODE operands
	;

Here PSEQ/TEX/DW are NOP's, one MFU sub-instruction is scheduled and ALU stage is skipped.

Since not all sub-instructions are known yet, the "OPCODE operands" could be replaced with a raw hex value. The number of the hex words depends on the size of the sub-instruction; for example MFU instruction is 64bit sized, so two hex words, separated by a comma, will be necessary for it.

Example:

	EXEC
		MFU:	0x00000000, 0x12345678
	;

Here the MFU sub-instruction is represented in raw by a two hex values, 0x00000000 is a high 64bit halve and 0x12345678 is low.

### 2.5 PSEQ sub-instruction

The render-target fetch has a mnemonic; every other PSEQ word is written in hex.

	PSEQ:	load rtN, r0, r1

`load` takes the render target index (bits 16..19) and the destination register
pair (bit 1): either `r0, r1` or `r2, r3`. It emits the enable bits 3 and 23, so
`load rt2, r0, r1` produces the stencil fetch word `0x00820008` that the blob
uses, and `load rt3, r0, r1` reads the coverage surface (`0x00830008`). A value
stored to that target in an earlier batch is visible to the fetch, so a
read-modify-write is two batches.

The spill layouts and the format fields are not decoded by the mnemonic, use a
raw word for those:

	EXEC
		PSEQ:	0x00000000
	;

### 2.6 MFU sub-instruction

The MFU sub-instruction consists of four sub-operations: special function, 2 multipliers and a fetch-interpolate. It takes the following form:

	EXEC
		MFU:	sfu: rcp r4
			mul0: dst0, src0, src1
			mul1: dst0, src0, src1
			ipl: var0, var1, var2, var3
	;

The special function is evaluated first, multipliers second and varyings fetch and interpolate the last.

The special function:

	sfu: OPCODE operand

- OPCODE is one of the MFU special function unit operations. See fragment ISA wiki page for the list of opcodes.
- operand is a "pixel packet" row register in fp20 format to which the operation will be applied.

Multipliers:

	mul0: dst0, srcA, srcB
	mul1: dst1, srcC, srcD

Multipliers are evaluated simultaneously: dst0 = srcA * srcB, dst1 = srcC * srcD.

- destination registers:
	- bar - barycentric weight
	- rN - current row register, N is 0..3
	- dstN - raw destination register value, N  is 0..7

- source registers:
	- barN - barycentric coefficient, N is 0..1
	- sfu - result of the SFU operation
	- rN - current row register, N is 0..3
	- srcN - raw source register value, N  is 0..7
	- #1 - constant 1.0

The fetch and interpolate:

	ipl: mod(tN.fmt), mod(tN.fmt), mod(tN.fmt), mod(tN.fmt)

- tN.fmt - TRAM row N will be read in as one fp20 or two fx10 (fmt), or could be a NOP to skip the read of a row component. The first tN.fmt operand is the TRAM's row "x" component and so on.
- an optional sat(tN.fmt) saturate modifier could be applied to the operand

Example:

	EXEC
		MFU:	sfu: rcp r4
			mul0: bar, sfu, bar0
			mul1: bar, sfu, bar1
			ipl: t2.fx10, t7.fp20, NOP, sat(t5.fp20)
	;

Here the SFU performs 1.0 / w, multipliers program barycentric weights using SFU result and barycentric cefficients, IPL performs a read from TRAM, interpolates the read components and stores the result into the "pixel packet" r0, r1, r2, r3 row registers.
- tram2.x read as two fx10 and stored into the r0 of the "pixel packet" row
- tram7.y read as fp20 and stored into the r1 of the "pixel packet" row
- the r2 of the "pixel packet" row is untouched
- tram5.w read as fp20, clamped to 0..1 and stored into the r3 of the "pixel packet" row

Example:

	EXEC
		MFU:	sfu: RCP r1
	;

Here a reciprocal operation is applied to the register r1, r1 = 1.0 / r1. Multipliers and interpolation are NOP's.

### 2.7 TEX sub-instruction

Performs texture sampling, takes the following form:

	EXEC
		TEX:	OPCODE rg, ba, texID, s, t, r, lod_bias
	;

Destination and source registers are current row registers r0-r3. Destination registers "rg" and "ba" receive sampled texture RGBA data in fx10 format, there are two possible variants: "r0, r1" or "r2, r3". Opcodes are TEX or TXB. Sources vary with the opcode, they are: texture sampler ID, texture S, T, R coordinates and LOD bias; there are two possible variants of s,t,r,lod sources: "r0, r1, r2, r3" or "r2, r3, r0, r1".

TEX opcode:

LOD bias source register must be omitted, as LOD biasing is disabled.

Example:

	EXEC
		TEX:	tex r2, r3, tex14, r0, r1, r2
	;

Here texture data is sampled into r2,r3 row registers from texture unit #14, "s t r" coordinates are passed via r0,r1,r2 row registers. In result, r2.fx10_low contains red; r2.fx10_high green; r3.fx10_low blue and r3.fx10_high alpha channel components.

TXB opcode:

LOD bias source register must be provided, as LOD biasing is enabled.

Example:

	EXEC
		TEX:	txb r0, r1, tex3, r0, r1, r2, r3
	;

Here texture data is sampled into r0,r1 row registers from texture unit #3, "s t r" coordinates are passed via r0,r1,r2 row registers, LOD bias is enabled and passed via row register r3. In result, r2.fx10_low contains red; r2.fx10_high green; r3.fx10_low blue and r3.fx10_high alpha channel components.

TXL opcode:

The fourth source register carries an *absolute* LOD instead of a bias (TEX bit
13, which only takes effect while bit 12 is clear). Operands are the same as for
TXB.

Example:

	EXEC
		TEX:	txl r2, r3, tex0, r0, r1, r2, r3
	;

Here the LOD is read from r3 and used directly, ignoring the gradients the
sampler would otherwise compute. `txl r2, r3, tex0, r0, r1, r2, r3` emits
`0x2420`, the word that used to have to be written in raw hex.

### 2.8 ALU sub-instruction

The ALU sub-instruction consists of four ALU instructions, one per ALU, and an ALU_COMPLEMENT hex value (one per exec batch).

ALU instruction takes the following form:

	OPCODE rDst.mask, -mod(rA)*2-1, -mod(rB)*2-1, -mod(rC)*2-1, -mod(rD)*2-1 (modifier)

- OPCODE is one of the ALU operations: MAD, MUL, MIN, MAX or CSEL. The MUL operations is effectively a MAD with an "addition disable" bit set.
- rDst is the destination register; it's mask is 'lh', 'l\*', '\*h' or '\*\*', where 'l' - enabled write to the low halve, 'h' - to the high and '*' disables the write to the respective halve. The write-mask defines the destination format for the MAD/CSEL instructions: both halves - fp20, one halve - fx10 low/high.
- rA, rB, rC and rD are the source registers. The format is defined by a register postfix:
	- ".l" - fx10 low halve of the source register is read
	- ".h" - fx10 high halve of the source register is read
	- no postfix - the source register is read as fp20
- optional operand rX modifier, X is for A-B-C-D:
	- absolute modifier, abs(rX)
	- multiply by 2, rX * 2 (not applicable to rD)
	- decrement by 1.0, rX - 1; only affects the fx10 typed rX
	- negate modifier, -rX (not applicable to rD)
- optional instruction modifiers are given in parens after the operands:
	- this - Sets "accumulate this" ALU bit.
	- other - Sets "accumulate other" ALU bit.
	- eq - rDst result "equal to 0" ? 1.0 : 0.0.
	- gt - rDst result "greater than 0" ? 1.0 : 0.0.
	- ge - rDst result "greater or equal to 0" ? 1.0 : 0.0.
	- x2 - rDst result multiplied by 2.
	- x4 - rDst result multiplied by 4.
	- /2 - rDst result divided by 2.
	- sat - rDst result is clamped to 0..1.

ALU registers:
- rN - ALU buffer row register, N is 0..15 (4 registers per row)
- gN - General purpose register, N is 0..7 (`g8`/`g9` are a parse error, matching the eight registers at 16..23)
- aluN - ALUN result register, N is 0..3
- immN - ALU3 immediate constant, N is 0..2
- lp - the low-precision vec2 register, hardware register 31
- crN - condition register, N is 0..15 (stored in pairs, so `cr0`/`cr1` share one hardware register)
- kill - discard register, hardware register 76; write-only, any non-zero value discards the fragment
- posx / posy - fragment position, hardware registers 72 and 73
- pface - polygon face, hardware register 75
- cov - primitive coverage, hardware register 74; reads 0 on AR20

`rN` with N above 15 names the register file by hardware number as well, so
`r16`..`r23` are the general purpose registers, `r24`..`r27` the ALU result
registers, `r64`..`r71` the condition registers and `r72`..`r75` the specials.
Only sources accept that range; a destination above `r15` is a parse error. This
is what makes `MAD r2.l*, r74, #1, #0` - reading the coverage register - possible
without a mnemonic, and `cov` is simply a spelling of `r74`.
- lp - Low precision register, the "lp" form is used only for the destination register, while source registers uses "#0" or "#1" form. When rD is set to #1, it's enable bit is unset, making rD 1.0 effectively.
- uN - Uniform register, N is 0..31
- crN - Condition register, N is 0..15
- posx - Fragment position X + 8192.0
- posy - 8192.0 + (target height - 1) - Fragment position Y
- pface - Polygon face direction
- rB / rC - Only applicable to the rD. The rD source is a copy of either ALU source register rB or rC.
- kill - discard the fragment

Example:

	EXEC
		ALU:
			ALU0:	MUL  u3.lh,  r3,         posx,       r1.l - 1,   rB        (x2)(this)
			ALU1:	CSEL cr6,    r2,         u5.h,       #0,         #1
			ALU2:	MIN  r2.*h,  r1,         #1,         r2 * 2,     abs(rC)
			ALU3:	MAD  r2.l*,  r0,         #1,         alu2,       #1
	;

The ALU3 could be traded for the immediate constants, it takes the following form:

	imm0 = float, imm1 = float, imm2 = float

If immN is postfix'ed with ".l" / ".h", the fx10 representation of a float value will be loaded into the respective halve of the immediate constant. When not prefix'ed, the fp20 representation is loaded. 

Example:

	EXEC
		ALU:
			ALU0:	MAD  cr0,    imm0.h,     #1,         imm1,       rB   (gt)
			ALU1:	MAD  lp,     -posx,      #1,         #0,         #1   
			ALU2:	NOP 
			ALU3:	imm0.h = -0.187500, imm1 = 8192.000000
	;

### 2.9 DW sub-instruction

Performs data store to a specified render target. There is only one STORE opcode which takes the following form:

	EXEC
		DW:	store rtX, rA, rB
	;

The content of the source registers rA-rB will be stored to the render target X. The registers values are interpreted as four fx10, i.e. RGBA color. The "rA,rB" are either "r0,r1" or "r2,r3" row registers, render target X is 0-15.

Example:

	EXEC
		DW:	store rt1, r2, r3
	;

Here render target 1 will be populated with the values carried in the r2-r3 row registers.

A store may name the render target class explicitly. The class is bits 16..17 of the word; the default is 2, which covers the colour, stencil and coverage targets. Class 1 is the depth surface, so a depth write needs the suffix:

	EXEC
		DW:	store rt0, r0, r1 class 1
	;

The suffix goes after the source-register list and takes 0..3. The register pair is written in the instruction as usual, but a class-1 store reads its payload from the *other* pair's high register (r3 for the form above), so the value goes in r3 - see the fragment shader ISA document, section 8.2.

There is a special case of writing to the stencil buffer. The write to the stencil buffer is necessary in case of a late stencil test, i.e. when fragment program performs pixel discard. Instruction takes the following form:

	EXEC
		DW:	store stencil
	;

The operation doesn't take any source registers. The stencil data will be stored to the "stencil" render target 2.

Measured behaviour on AR20: this store commits the **late** stencil update, so it matters exactly when the program also writes the kill register. With `kill = 0` (discard-capable, nothing discarded) and the stencil test enabled at ref `0x7F`, a program ending in `store stencil` leaves the surface at `7F`, while the same program ending in `store rt1` leaves it at `00` - the fixed-function update is disabled once the program can discard. With the stencil test disabled the store writes nothing, and a program that discards every fragment writes nothing either. It commits the result of the stencil op, and the "old" value that op works from is taken from `r0/r1`, so a program with `KEEP`-style ops has to fetch the surface first (`PSEQ: 0x00820008`, which loads render target 2 into `r0/r1`); without the fetch the old value is whatever `r0/r1` happen to hold. See the fragment shader ISA document, section 8.2.

## 3. Linker assembler

The linker assembler defines which vertex export registers will be copied to the TRAM, hence the (from)export register-(to)TRAM row locations, to what format the exported vertex register components will be converted during the copying to the TRAM and how the TRAM row component will be interpolated during the rasterization stage of graphics pipeline.

There is only one LINK instruction, which takes the following form:

	LINK fmt (mod), fmt (mod), fmt (mod), fmt (mod), tramN.swizzle, exportM (z)

- fmt - destination TRAM component format:
	- fp20 - 20bit float
	- fx10.l - 10bit fixed point float, the low halve of the TRAM component 
	- fx10.h - 10bit fixed point float, the high halve of the TRAM component 
	- NOP - the TRAM component is "skipped", i.e. unaffected by the LINK operation
- mod - interpolation modifiers, given in parens:
	- dis - interpolation disable
- tramN.swizzle - the TRAM row N, where N is 0..15 and destination components swizzle is "xyzw"
- exportM - the exported vertex register M, where M is 0..15
- z - optional modifier, used to pass .z component to the fragment program (select VEC4.x = VEC4.z) 

Example:

	LINK fp20, NOP, fp20 (dis), fx10.h, tram0.ywzx, export1

Here the content of the VEC4 vertex export register 1 copied to the TRAM row 0, so that:
- export1.x => converted to fp20 => copied to the tram0.y
- export1.y => skipped => the content of tram0.w is not altered
- export1.z => converted to fp20 => copied to the tram0.z and interpolation parameter "interpolation disable" is set for the the tram0.z
- export1.w => converted to fx10 => copied to the high halve of the tram0.x
