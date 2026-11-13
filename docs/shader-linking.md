# Tegra 2 (AR20) - shader linker program

**Status.** Reference. Every field is reproduced by the words the engine
receives (section 3); where a field has not been isolated on its own, that is
stated.

**Scope.** The link program at `0x300`: how vertex-shader exports reach TRAM, and
the per-component interpolation and destination-format fields. The surrounding
setup-unit registers are in [[GR3D Registers]] §4; the fragment side that reads
these rows is in [[Fragment Shader ISA]] §6.2.

The linker copies the vertex shader's exports into TRAM, the memory the
rasteriser and the fragment shader read. It is a program of 64-bit words starting
at `0x300`: the first word names the export to copy, the second names the TRAM row
and carries the interpolation and format fields.

Before linking, the vertex exports are squeezed. If the vertex program writes
exports 2, 7 and 9 (that is, those exports are enabled in the output attribute
mask), they are remapped to 2 -> 0, 7 -> 1, 9 -> 2, and the linker sees them as
exports 0, 1 and 2.

## 1. First word

| Bits | Meaning |
|-----:|:--|
| 0 | VEC4 select, see 1.1 |
| 3..6 | vertex export index, in |
| 9..14 | TRAM row index, out |
| 16 | TRAM.x constant across width |
| 17 | TRAM.x constant across length |
| 18..19 | TRAM.x across point |
| 20 | TRAM.y constant across width |
| 21 | TRAM.y constant across length |
| 22..23 | TRAM.y across point |
| 24 | TRAM.z constant across width |
| 25 | TRAM.z constant across length |
| 26..27 | TRAM.z across point |
| 28 | TRAM.w constant across width |
| 29 | TRAM.w constant across length |
| 30..31 | TRAM.w across point |

### 1.1 VEC4 select

| Value | Meaning |
|------:|:--|
| 0 | VEC4.xyzw: the attribute is fetched from the VPE export into the TRAM row |
| 1 | VEC4.x = VEC4.z: the export's `x` becomes `gl_FragCoord.z`; `export.x` should be swizzled to TRAM.w (the TRAM swizzle itself is unchanged) |

## 2. Second word

| Bits | Meaning |
|-----:|:--|
| 0..1 | TRAM.x destination swizzle |
| 2..3 | TRAM.x destination type |
| 4..5 | TRAM.y destination swizzle |
| 6..7 | TRAM.y destination type |
| 8..9 | TRAM.z destination swizzle |
| 10..11 | TRAM.z destination type |
| 12..13 | TRAM.w destination swizzle |
| 14..15 | TRAM.w destination type |
| 16 | TRAM.x triangle interpolation disable |
| 17 | TRAM.y triangle interpolation disable |
| 18 | TRAM.z triangle interpolation disable |
| 19 | TRAM.w triangle interpolation disable |

### 2.1 Destination type

| Value | Meaning |
|------:|:--|
| 0 | none / skip |
| 1 | fx10, low half |
| 2 | fx10, high half |
| 3 | fp20 |

### 2.2 Destination swizzle

| Value | Meaning |
|------:|:--|
| 0 | TRAM.x |
| 1 | TRAM.y |
| 2 | TRAM.z |
| 3 | TRAM.w |

Disabling triangle interpolation takes the value from the third vertex when
drawing triangles. That suits triangle strips and fans, but not independent
triangles, where the "constant across length" flag is the one to use.

## 3. Verification from the posted words

Every field above is reproduced by the words the engine actually receives. Each
`LINK` line becomes one 64-bit instruction at `0x300`/`0x301` (the register has
stride 2: `LINKER_INSTRUCTION(i) = 0x300 + 2i`), and a command-stream dump shows
the two halves in push order, first word first:

| `LINK` line | `0x300` | `0x301` |
|:--|--:|--:|
| `fp20, fp20, fp20, fp20, tram0.yxzw, export1` | `0x00000008` | `0x0000FECD` |
| `fp20, fp20, fp20, fp20, tram0.xyzw, export3` | `0x00000018` | `0x0000FEDC` |
| `fp20, fp20, NOP, NOP, tram0.xyzw, export3` | `0x00000018` | `0x000032DC` |
| `fp20, fp20, fp20, fp20, tram0.yxzw, export1 (z)` | `0x00000009` | `0x0000FECD` |
| `fx10.l, fx10.h, fx10.l, fx10.h, tram1.xyzw, export2` | `0x00000210` | `0x0000B694` |
| `fp20 (cw), fp20, fp20, fp20, tram0.xyzw, export1` | `0x00010008` | `0x0000FEDC` |
| `fp20, fp20 (dis), fp20, fp20, tram0.xyzw, export1` | `0x00000008` | `0x0002FEDC` |

Reading two of them against the tables, low nibble first:

* first word of `tram1.xyzw, export2`: `0x210` = export index 2 in bits 3..6
  (`0x10`) plus TRAM row 1 in bits 9..14 (`0x200`), with no interpolation
  modifiers - so the row index is a 6-bit field, not the 4 bits the assembler
  grammar accepts;
* latter word of `tram0.yxzw` with `fp20` everywhere: `0xFECD` reads as bit pairs
  `01` (swizzle x = y), `11` (type x = fp20), `00` (swizzle y = x), `11` (type y),
  `10` (swizzle z = z), `11` (type z), `11` (swizzle w = w), `11` (type w) -
  exactly `.yxzw` in `fp20`;
* `(z)` sets bit 0 only (`0x9` = export 1 plus `vec4_select`), and `(cw)` on the
  first column sets bit 16 (`0x10008`);
* `(dis)` on the **second** column sets bit 17 (`0x0002FEDC`), i.e. the
  interpolation-disable bits are 16 + component index. A first attempt that put
  the modifier on column 2 and expected bit 16 was the error, not the hardware.

### 3.1 Assembler spelling

The modifier tokens are `cw`, `cl` and `dis` (`linker_asm.l`), and the fx10 types
are spelled `fx10.l` and `fx10.h`, not `fx10l`/`fx10h`. A line that fails to parse
produces no job at all, which can masquerade as a successful measurement - see
[[Hardware Testing]], "A verification trap".

## 4. TRAM row count (`0xe21`)

| Bits | Meaning |
|-----:|:--|
| 8..14 | number of used TRAM rows |
| 0..6 | 64 / number of used TRAM rows |

The fragment program can only name 16 rows (the MFU `var` field is 4 bits), so a
driver assigns 0..15 and sets this count to `max_row + 1`. The row count must
divide 64.
