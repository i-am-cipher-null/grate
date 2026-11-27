# 65. The blob lowers a cube coordinate to rows 0..2 in argument order, and the special word leaves the face index in row 2

Offline compiler-output study of NVIDIA's Tegra 20 fragment compiler (`libcgdrv.so`
from L4T R16.5, recovered in note 64).  It compiles 111 controlled `textureCube`
variants and decodes the resulting MFU/ALU/TEX words by hand.  No texture was
allocated or bound, no hardware was run, and the `0x750 + unit` texture word is
not touched.

### 65.1 Verdicts

- **Confirmed (uniform):** row *k* before the special word holds the *k*-th
  component of the `textureCube` argument.  `dir.zxy` is compiled to
  `r0<-c2, r1<-c0, r2<-c1`; the compiler does not canonicalize the order.  All
  48 permutation/sign variants and the scale controls follow
  `r_k <- ±c_{p[k]}` with no exceptions (48/48 seed maps checked).
- **Confirmed:** the special word itself is invariant.  Every one of the 48
  uniform matrix variants compiles to the same two words, `0x28689402
  0x00000000`: `sfu: rcp r10`, `mul0: r0 = r2 * r0`, `mul1: r1 = r2 * r1`, no
  fetch.  The same TEX word `0x00000400` appears in all 111 variants.
- **Confirmed:** the varying packet holds **two** MFU instructions, and the
  compiler's own dump prints the second one one 32-bit word too early.  Decoded
  at the correct boundary, the second word is byte-identical to the uniform cube
  word.  The first word (`0x104e51ba 0x00008102`) is the varying fetch /
  perspective word and is present in a 2D varying shader that has no cube word
  at all.
- **Confirmed:** the compiler's varying path emits **no code at all** for a pure
  permutation of the coordinate (all six permutations, and `.zyx`/`.wzy` of a
  vec4 varying, have the same code hash).  Sign changes do change the fetch
  slots and add ALU negations.  The executable words carry no other trace of
  the permutation, and the varying symbol metadata is byte-identical.  The full
  binary is not byte-identical: two permutations differ in the symbol-name
  offsets and in the first word of the shader stream (the stream header that
  follows the names); the executable GR3D words do not differ.
- **Derived (from the measured semantics of notes 56.7 and 57.3 applied to the
  compiler's word):** after the word, row 2 holds the face descriptor
  `2d + (sign<0)` — exactly the GLES cube face index 0..5 — and rows 0/1 hold
  the face-local coordinates.  For all six faces row 0 equals the GLES `sc`;
  row 1 equals `tc` for +Z and `-tc` for the other five.
- **Unresolved:** how the texture unit consumes row 2 (no TEX field changes, so
  it must be row-driven if it is consumed at all); why row 1 carries `-tc` on
  five faces; and how the link stage maps GLSL components to the varying fetch
  slots (the fragment binary does not contain that mapping).

### 65.2 What was compiled

`make_s65.py` writes each source from a fixed template, invokes
`~/l4t/build/tools/cgc -F -v`, stores the unmodified stdout/stderr, and writes
`manifest.tsv` with the source hash, the full-binary hash, a hash of the
executable GR3D words, the seed rows, the decoded MFU words and the TEX word.

| group | variants |
|:--|--:|
| uniform 6 permutations × 8 sign masks | 48 |
| varying 6 permutations × 8 sign masks | 48 |
| uniform scale: `2x`, `2y`, `2z`, `*2.0`, `*4.0`, `*0.5`, `normalize` | 7 |
| varying scale: `2x`, `2y`, `2z`, `*2.0`, `normalize` | 5 |
| vec4 varying: `.xyz`, `.zyx`, `.wzy` | 3 |
| **total** | **111** |

All 111 compiled with 0 errors.  The files are `shaders/*.glsl`,
`words/*.cgc.txt` (about 2.7 MB of raw dumps) and `manifest.tsv`.

The two baselines reproduce s64 exactly (the uniform one is also note 53's
capture):

| variant | GR3D code SHA-256 | s64 source |
|:--|:--|:--|
| `u_xyz_+++` | `c74a6c6a62e468053bf76a04262184af5981325610bcf173c9caeb1ad686f989` | `cube_uniform` |
| `v_xyz_+++` | `4346e761d76e3243879d266c7cdf85d4d7fa608ff7ecd1c2680541ff0e51fa19` | `cube_varying` |

`u_xyz_+++` differs from the s64 source only in spelling the coordinate as
`vec3(dir.x, dir.y, dir.z)` instead of `dir`; the executable words are
identical.

### 65.3 Instruction boundaries: the varying packet is two instructions, not three

`words/v_xyz_+++.cgc.txt` lists four MFU words:

```
GR3D: MFU[000]: 104e51ba   GR3D: MFU[001]: 00008102
GR3D: MFU[002]: 28689402   GR3D: MFU[003]: 00000000
```

and `libcgc/shader.c` prints them as two lines, the second of which is
`00008102 28689402 | 00000000 20001400`, "`sfu: nop`" with `ipl: ... v20 ...
v8`.  That second line is not a real instruction: the schedule word is
`MFU-SCHED[001] = 5 = (1 << 2) | 1`, an offset of one **64-bit** instruction
slot (words 2..3), but the dump indexes `mfu + (sched >> 2) + j * 2`, i.e.
`mfu + 1` rather than `mfu + 2`.  Every field of the printed second line is
therefore assembled from the high half of instruction 1 and the low half of
instruction 2.

Decoded at the real boundary:

| word | instruction | fields |
|:--|:--|:--|
| `104e51ba 00008102` | interpolation | `sfu: rcp r4`, `mul0: bar = sfu * bar0`, `mul1: bar = sfu * bar1`, `ipl: var1 v0, var1 v0, var1 v0, nop` |
| `28689402 00000000` | cube coordinate | `sfu: rcp r10`, `mul0: r0 = r2 * r0`, `mul1: r1 = r2 * r1`, `ipl: nop ×4` |

The first word is not cube preparation.  `s64/words/twod_varying.cgc.txt` has
`MFU[000..001] = 104e51ba 00408000` and no
`28689402` at all: a 2D varying sample carries the same `rcp r4` /
`bar = sfu * barN` word (with a two-component fetch).  Its fetch and barycentric
multiplies are the interpolation path; the cube lowering is the second word,
identical to the uniform one.

This also reconciles note 55.8: the varying shader's "extra" MFU instruction is
the interpolation word, and the cube word is the one that was already known.
The `v20`/`v8` operands the old dump printed are bits of `0x28689402`
reinterpreted, not operands.

A second, smaller print bug matters for reading note 53's line: the dump prints
a multiplier destination of 4 as `#4` and a destination of 5 as `r1`
(`dst == 1` is `bar`, `dst > 4` is `r(dst-4)`, everything else is `#dst`).
Destination code 4 is row 0, so `mul0: #4 r2 r0` means `r0 = r2 * r0`, not an
immediate `#4`.  The field decode in 65.5 uses the encoding, not the printed
label.

### 65.4 The uniform path: component to row

The uniform is read through `c0..c2`; `dir.x` is `c0`, `dir.y` is `c1`,
`dir.z` is `c2` (confirmed by `dir.xxx`, which seeds all three rows from `c0`).
The compiler emits one `mad r_k.hl, <operand>, #1, #0` per component and keeps
the argument's order:

| argument | seed rows |
|:--|:--|
| `dir.xyz` | `r0<-c0; r1<-c1; r2<-c2` |
| `dir.xzy` | `r0<-c0; r1<-c2; r2<-c1` |
| `dir.yxz` | `r0<-c1; r1<-c0; r2<-c2` |
| `dir.yzx` | `r0<-c1; r1<-c2; r2<-c0` |
| `dir.zxy` | `r0<-c2; r1<-c0; r2<-c1` |
| `dir.zyx` | `r0<-c2; r1<-c1; r2<-c0` |

Signs are folded into the same `mad`: `-dir.x` becomes `mad r0.hl, -c0, #1,
#0`.  All eight masks follow `r_k <- ±c_k`, and every permutation/sign
combination follows `r_k <- ±c_{p[k]}`.  Axis scaling adds the ALU scale field
(`c0 * #2`); a global scale uses that field for 2.0 and an embedded constant
(`c0, ec0`) for 4.0 and 0.5.  None of these changes the MFU or TEX words.

### 65.5 What the special word does to those rows

`0x28689402` decodes as `opcode=1` (rcp), `reg=10`, `mul0 = r0 <- r2 * r0`,
`mul1 = r1 <- r2 * r1`; the second half is zero (no fetch).  Notes 54–57
measured what this selector does, and note 57.1 corrected the multiplier view:

- The selector computes `M = max(|r0|, |r1|, |r2|)` and `1/M`; the sign is
  dropped.
- It rewrites rows 0..2.  With `d = argmax(|r_i|)` (ties to the highest index),
  `s = sign(r_d)`:
  1. `row2 <- 2d + (s < 0)`;
  2. `d` is 0 or 1: `row_d <- -s * (row2 before the word)`, the other of rows
     0/1 unchanged;
  3. `d` is 2: `row0 <- s * (row0 before the word)`, `row1` unchanged.
- Inside the same word, multiplier source `r2` carries the injected `1/M` (the
  SFU result wins the collision), and the row sources read the row file **after**
  those writes (57.1).

So the compiler's two multiplies produce, with `(rx, ry, rz) = (row0, row1,
row2)` before the word and `M = |r_d|`:

| d | rows after the special word | after `mul0`/`mul1` | descriptor |
|:--|:--|:--|:--|
| 0 | `r0 = -s*rz, r1 = ry` | `r0 = -s*rz/M, r1 = ry/M` | 0 or 1 |
| 1 | `r0 = rx, r1 = -s*rz` | `r0 = rx/M, r1 = -s*rz/M` | 2 or 3 |
| 2 | `r0 = s*rx, r1 = ry` | `r0 = s*rx/M, r1 = ry/M` | 4 or 5 |

This reproduces the three measured words of note 55.5 that the older prose could
not explain:

| seeds `r0,r1,r2` | d,s | computed | stored | note 55.5 word |
|:--|:--|:--|:--|:--|
| 4, 0.25, 0.5 | 0,+ | `r0=-0.125`, `r1=0.0625` | `00`, `D7` | `0x0000D700` |
| 0.25, 0.5, 4 | 2,+ | `r0=0.0625`, `r1=0.125` | `D7`, `DF` | `0xDF00D700` |
| 4, 0.5, 0.25 | 0,+ | `r0=-0.0625`, `r1=0.125` | `00`, `DF` | `0xDF000000` |

Note 55.5's "the lane whose coordinate is the maximum comes out zero" was the
store format hiding the negative operand (`-s*rz/M`), not a cleared row; 56.7,
56.8 and 57.1 are the corrected account.

### 65.6 The transformed coordinate and the face descriptor

Writing the six faces out, with `(rx,ry,rz)` normalised by `M`, gives:

| d,s | row2 | GLES face | GLES `sc` | GLES `tc` | this row0 | this row1 |
|:--|--:|:--|:--|:--|:--|:--|
| 0,+ | 0 | +X | `-rz/M` | `-ry/M` | `-rz/M` | `+ry/M` |
| 0,- | 1 | -X | `+rz/M` | `-ry/M` | `+rz/M` | `+ry/M` |
| 1,+ | 2 | +Y | `+rx/M` | `+rz/M` | `+rx/M` | `-rz/M` |
| 1,- | 3 | -Y | `+rx/M` | `-rz/M` | `+rx/M` | `+rz/M` |
| 2,+ | 4 | +Z | `+rx/M` | `+ry/M` | `+rx/M` | `+ry/M` |
| 2,- | 5 | -Z | `-rx/M` | `-ry/M` | `-rx/M` | `+ry/M` |

Row 2 is the GLES face index in the spec's order, for every sign and every
permutation, because `2d + (s<0)` is exactly `+X,-X,+Y,-Y,+Z,-Z`.  Row 0 is the
spec's `sc` in all six rows.  Row 1 is `tc` only for +Z and `-tc` for the other
five.  No measurement here covers the texture unit, so the `t` sign and the way
TEX consumes row 2 are not explained; they are listed as unresolved rather than
inferred.

Permutations act on the *argument*, not on the hardware mapping: `dir.zxy`
seeds `(rz, rx, ry)` into rows 0,1,2, so `d` is the index of the largest of
those three and the descriptor names the face of the permuted vector.  Signs
cannot change `d` (absolute values) and flip `s` only when the negated component
is dominant, so the descriptor changes by 0 or 1.  Axis scaling can change `d`
and the descriptor at run time; global scaling changes `M` only, so the
normalised coordinates and the descriptor are unchanged.

With all three components zero, the tie rule selects `d = 2` and `s >= 0`, so
the descriptor is 4 (+Z) — the degenerate case note 57 reported.

The TEX word is `0x00000400` in every variant, so the face is not encoded in
TEX.  That is consistent with note 64's finding that the TEX word is
target-blind, and with the face living in the coordinate row 2 instead; whether
TEX reads row 2 is not measured.

### 65.7 The varying path

The baseline `v_xyz_+++` is the interpolation word followed by the uniform cube
word (65.3).  Nothing else is added for the cube operation itself.

**Permutations.**  All six permutations of `vdir` have the same executable
words for each sign mask (one code hash per mask), and `v4_xyz`, `v4_zyx` and
`v4_wzy` of a `vec4` varying all carry the same `4346e761…` code hash as the
vec3 baseline.  The fragment binary is also identical apart
from symbol-name offsets in the string table; the varying symbol metadata
(`datatype 0x403`, `location 7`, `unknown01 0xc94`) does not change.  A pure
permutation therefore adds no instruction and leaves no explicit mark in the
fragment program.  Whether the link stage restores it by repacking the varying
cannot be decided from a fragment-only dump; this is left unresolved, not
assumed either way.

**Signs.**  A negated component is either fetched through TRAM component 3 and
negated by an ALU `mad` into its row (z in `++-`, y in `+-+`, x in `-++`), or
fetched normally and negated in place (`+--`, `---`).  The eight masks compile
as:

| mask | first fetch slots | ALU | second fetch |
|:--|:--|:--|:--|
| `+++` | 0,1,2 | none | none |
| `++-` | 0,1,3 | `r2 = -r3` | none |
| `+-+` | 0,2,3 | `r1 = -r3` | none |
| `+--` | 0,1,3 | `r2 = -r3; r1 = -r1` | none |
| `-++` | 1,2,3 | `r0 = -r3` | none |
| `-+-` | 1,3 | `r0 = -r1; r2 = -r3` | `v1` slot 1 -> `r1` |
| `--+` | 2,3 | `r0 = -r2; r1 = -r3` | `v1` slot 2 -> `r2` |
| `---` | 0,1,3 | `r2 = -r3; r1 = -r1; r0 = -r0` | none |

The slot assignment differs per mask (for `++-`, z is fetched into slot 3; for
`-+-`, x is in slot 1 of `v0`, y in slot 1 of `v1`, z in slot 3 of `v0`).  That
is a compiler choice of TRAM packing, and it is not recorded in the fragment
binary's varying symbol; it can only be reconciled at link time.  Mapping the
observed slots to GLSL components is therefore out of reach for a fragment-only
dump.  What *is* certain is that after the fetches and the ALU, rows 0..2 hold
the signed argument in order, and the cube word that follows is the same
`0x28689402 0x00000000`.

**Scaling.**  `vdir * 2.0` and the axis scales add ALU scale fields (and, in the
`*2.0` case, fetch z through slot 3 so the three scaled rows can be written);
the cube word is unchanged.  `normalize(vdir)` adds the dot-product /
`rsq`-`rcp` sequence, then still emits the cube word: the explicit
normalisation does not replace the special operation's own `1/M`.

### 65.8 Additional compiler-generated arithmetic

- Uniform: nothing beyond the three seed MADs.  Signs are operand negate bits,
  scaling is the ALU scale field or an embedded constant; the MFU and TEX words
  never change.
- Varying: the interpolation word (`rcp r4`, two barycentric multiplies, the
  TRAM fetch), plus ALU negations and extra fetch slots for signed coordinates.
  The `-+-` and `--+` masks need a second fetch word because one component is
  taken from another TRAM row.
- `normalize` control: dot-product MADs, `rcp`/`rsq` MFU words and the three
  scaling MADs, followed by the unchanged cube word.  The explicit
  normalisation and the special operation compute the same `1/M`.

### 65.9 Measured, derived, unresolved

| statement | status |
|:--|:--|
| Uniform row *k* is argument component *k* (`c0,c1,c2` order) | compiler output, 48/48 |
| The uniform cube word is always `28689402 00000000` | compiler output, 48/48 |
| The varying cube word equals the uniform word | compiler output (corrected boundary) |
| The preceding varying MFU word is interpolation | compiler output (also present in a 2D varying shader) |
| Pure permutations add no varying code | compiler output, all six |
| The special selector computes `1/max(abs)` and rewrites rows per 56.7/57.3 | measured, notes 54–57 |
| Multiplier slot `r2` is the injected `1/M`; row sources read post-write | measured, note 57.1 |
| Row 2 after the word is the GLES face index | derived from the above rules |
| Row 0 equals GLES `sc`; row 1 equals `±tc` | derived arithmetic on those rules |
| TEX consumes row 2 as the face | unresolved (TEX word never changes) |
| Link-time TRAM packing restores varying permutations | unresolved (not in the fragment dump) |
| Why row 1 is `-tc` on five faces | unresolved |

### 65.10 Reproducing

```
cd ~/src/grate-exp
python3 tests/grate/asm/s65/make_s65.py
```

The compiler and libraries are those built by `s64/compile_cube.sh`; the script
defaults to `~/l4t/build/tools/cgc` and
`subprojects/Linux_for_Tegra`.  Each `words/<variant>.cgc.txt` is the complete
`cgc -F -v` output, including the echoed source and the full shader binary, and
`manifest.tsv` carries the source hash, the binary hash, the executable-word
hash, the seed rows, the decoded MFU words and the TEX word for that exact
source.  Nothing here writes outside `tests/grate/asm/s65/`.
