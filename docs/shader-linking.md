In order to pass data between the vertex and fragment shader, a sort of link-program seems to be copying from the output of the vertex shader into the TRAM (the RAM used as input to rasterization and shading).

This program is located at offset 0x300 and upwards, and consists of 64-bit words. The first word contains the location in the vertex shader export to copy from, the latter word contains the location in TRAM, interpolation parameters are mixed in.

Prior to linking, the vertex export is being squeezed. For instance, if vertex program writes to the exports 2, 7 and 9 (i.e. these exports are enabled in the output attribute mask), they will be remapped as 2 -> 0, 7 -> 1, 9 -> 2; the linker will see them as 0, 1 and 2 exports.

#### First word

| Bits   | Meaning                          |
|-------:|:---------------------------------|
|      0 | Vertex export VEC4 select        |
|   3..6 | (in) Vertex export index         |
|  9..14 | (out) TRAM row index             |
|     16 | TRAM.x is constant across width ?|
|     17 | TRAM.x is constant across length?|
| 18..19 | TRAM.x across point ?            |
|     20 | TRAM.y is constant across width ?|
|     21 | TRAM.y is constant across length?|
| 22..23 | TRAM.y across point ?            |
|     24 | TRAM.z is constant across width ?|
|     25 | TRAM.z is constant across length?|
| 26..27 | TRAM.z across point ?            |
|     28 | TRAM.w is constant across width ?|
|     29 | TRAM.w is constant across length?|
| 30..31 | TRAM.w across point ?            |

#### Vertex export VEC4 select:

VEC4.xyzw:

- Vertex attribute data will be fetched from VPE export to TRAM row.

VEC4.x = VEC4.z:

- Vertex position.z coordinate will be set to the export.x component and propagated to the fragment shader (gl_FragCoord.z), export.x should be swizzled to TRAM.w (actual TRAM seems untouched).

| Value  | Meaning                  |
|-------:|:-------------------------|
|   0    | VEC4.xyzw                |
|   1    | VEC4.z (VEC4.x = VEC4.z) |

#### Latter word

| Bits   | Meaning                                   |
|-------:|:------------------------------------------|
|   0..1 | TRAM.x destination swizzle                |
|   2..3 | TRAM.x destination type                   |
|   4..5 | TRAM.y destination swizzle                |
|   6..7 | TRAM.y destination type                   |
|   8..9 | TRAM.z destination swizzle                |
| 10..11 | TRAM.z destination type                   |
| 12..13 | TRAM.w destination swizzle                |
| 14..15 | TRAM.w destination type                   |
|     16 | TRAM.x triangle interpolation disable     |
|     17 | TRAM.y triangle interpolation disable     |
|     18 | TRAM.z triangle interpolation disable     |
|     19 | TRAM.w triangle interpolation disable     |

#### TRAM destination type:

| Value  | Meaning         |
|-------:|:----------------|
|   0    | none/skip       |
|   1    | fx10 low halve  |
|   2    | fx10 high halve |
|   3    | fp20            |

#### TRAM destination swizzle:

| Value  | Meaning |
|-------:|:--------|
|   0    | TRAM.x  |
|   1    | TRAM.y  |
|   2    | TRAM.z  |
|   3    | TRAM.w  |

Disabling triangle interpolation picks the value from the third vertex in if drawing triangles. This is what we want for triangle strips and triangle fans, but not normal triangles. Which means we'll probably need to use the "constant across length"-flag in that case.

#### Verified from the posted words

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
  interpolation-disable bits are 16 + component index - a first attempt that put
  the modifier on column 2 and expected bit 16 was the error, not the hardware.

One correction to the assembler documentation: the modifier tokens are `cw`,
`cl` and `dis` (`linker_asm.l`), and the fx10 types are spelled `fx10.l` and
`fx10.h`, not `fx10l`/`fx10h`. A line that fails to parse produces no job at all,
which can masquerade as a successful measurement - see [[Hardware Testing]],
"A verification trap".

#### Offset 0xe21:

Defines the number of used TRAM rows.

|   Bits | Meaning                       |
|-------:|:------------------------------|
|  8..14 | number of used TRAM rows      |
|   0..6 | 64 / number of used TRAM rows |
