# Fragment hardware experiments (manifest-driven)

**Status.** Measured on hardware. Every word below was produced by
`asm_characterize.py` from the manifest named with it, three times per variant,
with the controls passing before and after the sweep. The raw evidence is the
`characterization/<name>-<timestamp>/` directory the runner leaves behind
(`results.tsv`, `logs/`, `metadata.json`), which is git-ignored.

**Scope.** Three features that a backend needs and that had no measurement
behind them: the ALU addition-disable field, the TEX coordinate register
select, and the `alu_buffer_size` rows. The runner itself and the manifest
format are in [asm_characterize.md](asm_characterize.md); the environment
variables and the traps are in `docs/hardware-testing.md` on the
`reverse-engineering` branch.

## Running them

From the repository root, with a build tree that has `tools/assembler`:

    GRATE_ASSEMBLER=$HOME/build/grate/tools/assembler \
        meson devenv -C ~/build/grate \
        python3 $PWD/tests/grate/asm_characterize.py \
        $PWD/tests/grate/asm_characterize.alu_add_disable.json

`meson devenv` changes the working directory to the build tree, so both paths
have to be absolute - a relative manifest path fails with
`No such file or directory: '<build>/tests/grate/...'`. The runner derives the
repository root from the script's own path, so the manifest and the `program`
stages it names still resolve against the source tree.

Each manifest runs two controls first, refuses to start the sweep if either
fails, and repeats the controls afterwards. A run is only counted when the
process exits 0 and prints exactly one `result 0x........` line whose value
matches `expected`, which every variant here carries.

Results below are from commit `250b1f3` on a Toshiba AC100 / Dynabook AZ
(Tegra 20, `7.2.0-gd1efa725955b`, armv7l).

## 1. ALU addition disable: two multiplies per slot

`asm_characterize.alu_add_disable.json`, template
`asm/alu_add_disable.in`. The opcode is `MUL` - the parser sets ALU bit 59 from
the mnemonic, so the two spellings differ in one bit. Operand values are
`u0 = u1 = 0.5`, `u2 = 0.75`, and batch 1 prefills both fx10 halves of `r2` with
`0.5` (`0x7F`) so an unwritten half cannot be mistaken for a product. The DW
readback word is `r3.hi, r3.lo, r2.hi, r2.lo` from MSB, so one store shows both
halves of `r2`.

| variant | instruction | word |
|:--|:--|--:|
| `prefill-only` | `NOP` | `00007F7F` |
| `mad-three-operand-control` | `MAD r2.l*, u0, u1, u2` | `00007FFF` |
| `mul-low-mask` | `MUL r2.l*, u0, u1, u2` | `0000BF40` |
| `mul-high-mask` | `MUL r2.*h, u0, u1, u2` | `000040BF` |
| `mul-both-mask-fp20` | `MUL r2.lh, u0, u1, u2` | `0000E700` |
| `mul-rd-is-rb` | `MUL r2.l*, u0, u1, u2, rB` | `00006040` |
| `mul-rd-is-rc` | `MUL r2.l*, u0, u1, u2, rC` | `00008F40` |
| `mad-rd-is-rb-control` | `MAD r2.l*, u0, u1, u2, rB` | `00007F9F` |

Reading it:

* **the addend really is dropped.** `MAD` stores `0.25 + 0.75 = 1.0` (`FF` in
  the low byte); `MUL` with the same operands stores `0.25`, so bit 59 is not a
  formatting flag.
* **a single-half destination yields both products, and the mask bit chooses
  which half gets `rA*rB`.** `MUL r2.l*` writes `r2.lo = 0.25` and
  `r2.hi = 0.75`; `MUL r2.*h` writes `r2.hi = 0.25` and `r2.lo = 0.75`. That is
  the patent table in `docs/fragment-shader-isa.md` 5.13 measured exactly, and
  it is a
  trap for a backend: `.l*` does **not** mean "low half only" for `MUL`, so a
  value the allocator believes is live in the high half is destroyed.
* **the second product follows `rD`, not a fixed register.** `rB` gives
  `0.75 * 0.5 = 0.375` (`60`) and `rC` gives `0.75 * 0.75 = 0.5625` (`8F`),
  both landing in the half the mask did not name.
* **the fp20 (`.lh`) destination keeps one product.** It stores `0xE700`, which
  is `fp20` of `0.25` read back through the two fx10 halves - the second product
  is lost. So the 2-for-1 form exists only with a single-half mask.
* **`rD` is not a free operand.** The field is six bits (a register select plus
  modifiers), so `MAD r2.l*, u0, u1, u2, u3` is a parse error ("Invalid src
  register"); `rD` can only be `1.0`, `rB` or `rC`. With the usual omitted `rD`
  the second product is therefore `rC * 1.0`, i.e. `MUL r2.l*, a, b, c` copies
  `c` into the other half for free.

A backend that emits `MUL` must therefore either use a destination half it does
not need, or pair two products deliberately. The saving is one ALU slot per two
multiplies, out of the 256-slot budget.

## 2. TEX coordinate and destination register select

`asm_characterize.tex_register_select.json`, template
`asm/tex_src_select.in`, run with `--texture`. TEX bit 5 (destination) was
already measured; bit 4 (coordinates) was only written up. The harness's default
texture is 4x4 `RGBA8888` with columns 0..1 black and 2..3 white, so `S` alone
decides the colour, and the four row registers hold distinct fp20 values
`r0 = 0.05`, `r1 = 0.25`, `r2 = 0.75`, `r3 = 0.95`. Coordinates are written with
`.lh` (fp20); an fx10 coordinate half does not work.

| variant | TEX | reads `S` from | word |
|:--|:--|:--|--:|
| `src0-dst0` | `tex r0, r1, tex0, r0, r1, r2` | `r0 = 0.05` | `00000000` |
| `src0-dst1` | `tex r2, r3, tex0, r0, r1, r2` | `r0 = 0.05` | `00000000` |
| `src1-dst0` | `tex r0, r1, tex0, r2, r3, r0` | `r2 = 0.75` | `FFFFFFFF` |
| `src1-dst1` | `tex r2, r3, tex0, r2, r3, r0` | `r2 = 0.75` | `FFFFFFFF` |

* bit 4 selects the second coordinate group: `S,T,R = r2,r3,r0` instead of
  `r0,r1,r2`, and the sampled column moves from 0 (black) to 3 (white) while
  everything else in the program is unchanged;
* bit 5 is independent, so all four combinations are legal;
* `src1-dst1` samples *and* overwrites `r2`/`r3` in one instruction and still
  returns the white texel, so the read happens before the write.

The attribution is not only the colour: dumping the raw program shows the two
`src0`/`src1` programs differ in exactly one word, `[10] = 0x00000400` versus
`0x00000410`. Bit 4 is the only bit that moved.

What this buys a backend: the sample result can live in `r0`/`r1` while the
coordinates stay in `r2`/`r3`, which removes the copy into the pixel packet that
`filter_quad_fs` and `tex_cross_batch` otherwise need before every sample.

## 3. `alu_buffer_size` rows

`asm_characterize.alu_buffer_rows.json`, template `asm/alu_buf_three.in`. Three
batches: `r2.lo = 0.25`, then `r3.hi = 0.5`, then a store in the last batch.
`alu_buffer_size` comes from the template's directive (which the driver writes
to `0xe20`); the `0x520` start word is poked per variant.

| variant | buffer size | `0x520` | word |
|:--|--:|:--|--:|
| `size1-baseline` | 1 | `0x20006003` | `7F000040` |
| `size2-two-rows` | 2 | `0x20006006` | `7F000040` |
| `size3-documented-zero` | 3 | `0x20006009` | `00000000` |
| `size4-documented-zero` | 4 | `0x2000600c` | `00000000` |

* **the poke has to be the whole start word.** `0x520` is
  `0x20006000 | count`, and poking the bare count (`GRATE_POKE=0x520=6`)
  replaces the start bits and hangs every size including 2; the first sweep in
  this session did exactly that and reported twelve timeouts. Poking the full
  word runs all four sizes without a hang. Anyone repeating the `0x520`
  measurements in `docs/hardware-testing.md` should check that their poke kept
  the `0x20006000` bits.
* **size 2 returns the same word as size 1 for this shape**, reproducibly,
  three runs out of three. It is not the value the per-batch row rule predicts:
  `r2` (batch 0) and `r3` (batch 1) both survive into the store of batch 2,
  whereas batch 2 runs on row 0 at size 2 and should see only `r2`. So the store
  of the last batch saw writes from two different batches, and "batch `i`
  executes on row `i mod N`, each row holding its own `r0..r3`" does not explain
  this shape.
* **sizes 3 and 4 still return zero**, which reproduces the register
  reference's note but not its explanation: they do not hang here and they do
  not return the baseline word.

So `alu_buffer_size = 2` is usable for a program whose store is in the last
batch, but the row assignment is not yet modelled well enough to schedule
against, and sizes 3 and 4 remain unusable. That is where the next experiment
belongs: sweep which batch writes each of `r0..r3` and read the stored word for
each buffer size, instead of assuming the rows.

## What the runner cost

Three things that produced wrong results first:

* **an unencodable instruction looks like a hardware result.** The first
  `MUL` sweep used four uniforms and every variant exited 1 with
  `fs: line N: Invalid src register`; the runner records that as `exit-error`,
  not as a result, which is the only reason it was not mistaken for a colour.
  Check the status of the first repeat before reading any word.
* **`expected` has to be pinned from a measured run, not from a guess.** Two
  words were written the wrong way round in this session (`0x007F0040` for
  `0x7F000040`) because the DW byte order was applied from the wrong end. Run
  once with `expected` omitted, read `results.tsv`, then pin: the second run is
  the proof, the first is only a measurement.
* **a uniform-driven probe needs the immediate-operand control in the same
  run.** `fs_alu_immediate_control.txt` is that control (it must read
  `0x000000FF`) and it is a control in all three manifests, next to the shipped
  varying case (`0xFF01FF00`), because a board that has lost the constant path
  returns a plausible `f(0)` instead of an error.
