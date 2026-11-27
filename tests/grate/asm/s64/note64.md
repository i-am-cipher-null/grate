## 64. Cube maps: the compiler is recoverable, the shader is target-blind, and the cube state is not in the descriptor model

Cube sampling is required by GLES2, so AR20 almost certainly has it. What this
note establishes is narrower and firmer: **no path in this tree can bind a cube
texture**, and the pieces that *are* observable show that face selection is not
in the generated program. The vendor shader compiler, previously reported as
absent, was recovered from NVIDIA's Tegra20 L4T R16.5 package and now runs
offline on the AC100. It reproduces the note-53 capture exactly and shows that a
`textureCube` sample compiles to a plain TEX word with no target or face field
and no face-selection arithmetic.

Measured on the Toshiba AC100 (`7.2.0-gd1efa725955b`, Tegra 20 / AR20, Debian 13
armhf), branch `code/fragment-isa-experiments`, head `c6da85e`. Compiler runs are
offline (CPU only, no GPU submission); the hardware control is a `tools/assembler
--expected` run on the AC100. No stable documentation was modified.

### 64.1 Verdicts

- **The compiler is no longer the blocker.** `subprojects/cgdrv.wrap` names
  `Tegra20_Linux_R16.5_armhf.tbz2`; that file is still served, its SHA-256
  matches the wrap (`02b7a83b...c41587`), and `libcgdrv.so` extracted from it
  compiles fragment shaders on the AC100 even though `nvrm_init` fails (there is
  no `/dev/nvmap`). The fragment ABI is `CgDrv_Compile(cg, 1, 2, ...)`; type 2 is
  fragment, type 1 is vertex.
- **The generated cube program contains no face selection.** For
  `textureCube(tex, vdir)` the compiler emits two MFU words and zero ALU words:
  the major-axis reciprocal and two multiplies. There is no compare, select,
  abs, swizzle or branch. Face selection cannot be in the program.
- **The TEX instruction is target-blind.** The cube sample is `TEX 0x00000400`;
  a 2D sample is `0x00000430`; a cube+2D program has `0x00000400` (cube, s0) and
  `0x00000431` (2D, s1). The only differences are the sampler index and the
  coordinate/destination register selects. No bit says "cube".
- **The target reaches the driver as shader metadata, not as instruction
  state.** The compiler's uniform table reports `datatype: 0x0000042d
  (samplerCube)`. The driver therefore knows the target at link/draw time and
  must program it into texture state.
- **That state is outside the documented descriptor model.** `TEXTURE_DESC1` and
  `TEXTURE_DESC2` were swept bit by bit with R varying (note 51 and the ISA
  text); nothing enables face selection and R has no effect on a 2D binding. A
  current control reproduces this (64.5). The vendor driver, however, writes a
  **third word per texture unit** to `0x750 + unit`, which is precisely the
  register block the model treats as inert/unknown. That is now the prime
  candidate for cube/mip state and the concrete next target.
- **No cube sample was obtained, so the face table, per-face orientation and
  tie rule remain unresolved.** This is a tooling/driver boundary, not a
  measurement of AR20.

### 64.2 Capability audit

**GLES.** `/usr/lib/arm-linux-gnueabihf/libGLESv2.so.2.1.0` (87,516 bytes) is the
glvnd dispatch stub from `libgles2 1.7.0-1+b2`, not an implementation. Mesa
25.0.7 is installed and ships `tegra_dri.so`, but its 3D path requires nouveau:

```
$ sed -n '527,545p' src/gallium/drivers/tegra/tegra_screen.c
   const char * const drivers[] = {"nouveau"};
   screen->gpu_fd = loader_open_render_node_platform_device(drivers, ...);
   screen->gpu = nouveau_drm_screen_create(screen->gpu_fd);
```

Tegra20 has no nouveau support, and EGL cannot get as far as a screen anyway:

| attempt | result |
|:--|:--|
| default (x11) | `EGL user error 0x3003 ... xcb_connect failed` |
| `EGL_PLATFORM=surfaceless` | `using driver tegra for 3`, then `failed to create dri2 screen`; `/dev/dri/card0: Permission denied` (0660 root:video, user is only in `render`) |
| `EGL_PLATFORM=drm` / `gbm` | `failed to open /dev/dri/card0: Permission denied`, `DRI2: failed to create gbm device` |
| `EGL_PLATFORM=device` | `EGL_BAD_PARAMETER` in `eglGetPlatformDisplay` |

There is no X or Wayland server and no `eglinfo`/`es2_info`. The gles2 test
binaries therefore report `pbuffer_create() failed`. `GL_OES_texture_cube_map`
is a GLES1 extension; in GLES2 cube maps are core, so the capability that
matters is a working GLES2 context. None can be created here, so no
`GL_VERSION` or `GL_EXTENSIONS` string could be queried on this machine.

The *vendor* GLES stack is also unusable here, and for the reason flagged during
this work: it targets NVIDIA's kernel interface, not the host1x DRM driver in
this kernel. `libGLESv2.so.2` from L4T needs `libnvrm.so`, `libnvrm_graphics.so`,
`libnvos.so`, `libnvddk_2d.so` and `libardrv_dynamic.so`, and there is no
`/dev/nvmap` or `/dev/nvhost-*` — only `/dev/dri/card0` and `renderD128`. It was
not run.

**Kernel.** `/sys/class/drm/card0/device/uevent` is
`OF_COMPATIBLE_0=nvidia,tegra20-host1x`, driver `tegra 1.0.0`; dmesg:
`[drm] Initialized tegra 1.0.0 for drm on minor 0`. This is a command-submission
DRM driver; `drm/tegra_drm.h` has no texture, sampler or cube field (grep count
0). `libwrap` opens `/dev/dri/render128` (and `card0`/`by-path`), which is how the
assembler reaches the engine.

**libgrate.** `grate_create_texture(grate, width, height, format, layout)` and
`struct grate_texture` carry width, height and pitch only. `grate-3d.c`'s
`grate_3d_set_texture_desc` builds DESC1 from format/wrap/filter and DESC2 from
width/height/log2/LOD. There is no face count, no face array, no target. The
`GRATE_TEX_FACES` six-face *stack* exists only in the upstream `gsrc` assembler
(not in this experiment tree); it lays six faces in one 2D surface, which the
sampler reads as an atlas, not as a cube.

**Compiler.** Earlier notes reported `CgDrv_Create: BLOB compiler is
unavailable`; that is still true of the *installed* tree, because
`src/libcgc/dummy.c` is used when `dependency('cgdrv')` is not found. The wrap
in `subprojects/cgdrv.wrap` shows how to supply the real one:

```
source_url = http://developer.download.nvidia.com/mobile/tegra/l4t/r16.5.0/
             ventana_release_armhf/Tegra20_Linux_R16.5_armhf.tbz2
source_hash = 02b7a83b29cec59cf275b98f0de0fc87a24ffa25593af6af729cd2e881c41587
```

The download is 24,432,568 bytes and the hash matches. `nv_tegra/nvidia_drivers.tbz2`
contains `usr/lib/libcgdrv.so` (4,276,308 bytes, ARM EABI5, stripped), which
depends on `libnvrm.so` and `libnvos.so` from the same package. `CgDrv_Create`
prints `****nvrm_init failed****` (no `/dev/nvmap`) but returns a usable context,
and compilation succeeds. This makes the compiler a CPU-side tool: no vendor
kernel module, GPU submission or GLES driver is needed.

### 64.3 The compiled cube program

With `-Dcgdrv=enabled`, `tools/cgc` links the blob. The driver-side ABI used by
`libcgc/shader.c` is `CgDrv_Compile(cg, 1, type, source, size, 0, 0)`, with type
2 = fragment.

`textureCube(tex, vdir)` (`shaders/cube_varying.glsl`), raw words:

```
MFU-SCHED[000]: 00000001   MFU-SCHED[001]: 00000005
MFU[000]: 104e51ba         MFU[001]: 00008102
MFU[002]: 28689402         MFU[003]: 00000000
TEX[000]: 00000000         TEX[001]: 00000400
ALU-SCHED[000]: 00000000   ALU-SCHED[001]: 00000000
DW[000]: 00000000          DW[001]: 00020005
```

and its disassembly:

```
MFU :001  104e51ba 00008102 | sfu: rcp r4
                             mul0: bar sfu bar0
                             mul1: bar sfu bar1
                             ipl: var1 v0, var1 v0, var1 v0, nop
MFU :002  00008102 28689402 | mul0: bar r2 r0
                             mul1: #0 r0 r1
                             ipl: var1 v0, nop, var1 v20, var1_sat v8
TEX :002          00000400 | tex s0
DW  :002          00020005 | store rt1, r0, r1
```

There is no ALU word at all in the program. `0x00000400` is bit 10 (enable),
sampler 0, coordinates from R0..R3 and destination R0/R1: the same encoding the
2D sample uses. The `0x28689402` word is byte-for-byte the note-53 capture; the
uniform-coordinate variant (`shaders/cube_uniform.glsl`) reproduces the note-53
pair `0x28689402 0x00000000` exactly.

Comparisons (all in `words/`):

| program | TEX words | note |
|:--|:--|:--|
| `textureCube` varying | `0x400` | MFU only, ALU empty |
| `textureCube` uniform | `0x400` | repoduces note 53 |
| `textureCube(vdir*2.0)` | `0x400` | scaling appears as ALU `mad rN.hl, rN*#2, #1, #0` |
| `textureCube(normalize(vdir))` | `0x400` | normalization uses `rsq`/`sqrt`, still no selection |
| two cube samplers | `0x400`, `0x401` | only sampler index changes |
| `texture2D` varying | `0x430` | sampler 0, coords R2/R3, dst R2/R3 |
| cube + 2D | cube `0x400`, 2D `0x431` | cube is s0, 2D is s1 |

The uniform table reports the target:
`2: samplerCube tex, datatype: 0x0000042d (samplerCube)`. So the only channel
that carries "this sampler is a cube" is the program's sampler metadata, not the
instruction stream. That is exactly the input a driver needs to program
fixed-function texture state.

### 64.4 What the vendor driver does with it (static, not run)

`libardrv_dynamic.so` (46,096 bytes) exports the AR20 command builders. Two are
directly relevant:

```
NvArdSetTexExt(ctx, unit, flag, ?, ptr)      @ 0x52d4
NvArdSetTexControl(ctx, a, b, c)             @ 0x53e8
```

`NvArdSetTexExt` writes three words for one unit:

- `INCR 0x720 + 2*unit, 2` with DESC1 and DESC2 from `ptr[0]`/`ptr[1]`
- `INCR 0x750 + unit, 1` with `ptr[2]`

So **each texture unit has a third descriptor word at `0x750 + unit`**, the block
`docs_snap/gr3d-registers.md` lists as "? / inert for a power-of-two texture".
`NvArdSetTexControl` writes `0x740` with an 8-bit packed value
(`(c | a<<4 | b<<2) & 0xff`), which is the `0x035` grate writes before each draw.

In `libGLESv2.so.2` the binding path loads that triple from `unit+0x30`
(DESC2), `unit+0x34` (DESC1) and `unit+0x38` (the `0x750` word), and after the
first unit it checks `[unit+0x84] == 3`; if so it walks `[unit+0x9c]` entries of
0x14 bytes and calls `NvArdSetTexExt` again for each. The field names and the
entry meaning are not established here, so whether that loop is mip levels,
sampler slots or cube faces is **unresolved**; it is reported as an observation,
not as a cube result. It does show that the driver models per-unit state that
the local descriptor model does not.

What is established: the AR20 TEX word and the two documented descriptor words
carry no target; the third word at `0x750 + unit` is real, is written per
texture unit, and sits outside everything swept so far. That is the smallest
credible place for the missing cube state.

### 64.5 Hardware control: R still has no effect on a 2D binding

Run with the experiment assembler on the AC100 (see `hw_r_control.sh`). The
store-only program proves the uniform really moves R per fragment; the sampler
program is `cube_coord_fs.txt` here (the shipped cube-coordinate shader, copied
from `gsrc/tests/grate/asm/`): S/T from the varying, R from
`posx*rslope+rbias`, `txb` in the next batch).

| program | `rslope=0, rbias=0` | `rslope=1/256, rbias=-32` |
|:--|:--|:--|
| store-only (R,S/T through the ALU) | `00000000:65532` | 81-pixel bands `F600F600`, `F600F500`, `F600F400`, `F600F300`, ... |
| sample | `FFFFFFFF:32768 00000000:32764` | **byte-identical** to the `R=0` frame |

The store control varies; the sample does not. This reproduces the note-51
result on the current build: through a 2D descriptor, R is ignored. It is a
statement about the binding, not about AR20.

### 64.6 Required questions

| Question | Outcome |
|:--|:--|
| Can this hardware/software path create and sample a cube map? | **Not demonstrated. Exact blockers:** no working GLES on this kernel (glvnd stub; Mesa tegra 3D needs nouveau; EGL cannot create a screen; vendor GLES needs nvmap/nvhost); libgrate has no cube target or face allocation; the compiler works but emits shader words only. |
| Which face corresponds to each principal direction? | **Unresolved** — no cube sample could be bound, so no face identity could be measured. |
| Local S/T orientation on each face? | **Unresolved** — same blocker. |
| How are major-axis ties resolved? | **Unresolved** — same blocker; no boundary directions could be sampled. |
| Shader or fixed-function face selection? | **Selection is not in the generated program.** The cube program has zero ALU words (no compare/select/abs/branch) and only major-axis normalization; its TEX word has no target/face field, and identical TEX encodings are emitted for 2D, cube and mixed samplers. The target reaches the driver only as `samplerCube` metadata. Selection therefore happens after the program, in texture state (fixed-function), but the enabling state has not been located. |
| Role of the captured normalization sequence? | **Measured: normalization only.** `sfu: rcp r4/r10` gives `1/max(|x|,|y|,|z|)` (notes 54/55) and the two multipliers scale rows by it; the word is reproduced exactly by the recovered compiler. It prepares a coordinate; it does not choose a face. |
| What remains unavailable/unobservable? | See 64.7. |

### 64.7 What remains unavailable

- **A cube binding in libgrate.** No function takes a target or six faces, and
  the descriptor builder has no field for either. Adding one means deciding the
  `0x750 + unit` encoding by guesswork, which cannot distinguish "AR20 ignores
  it" from "the guess was wrong".
- **The vendor GLES path.** Its kernel interface (`/dev/nvmap`, `/dev/nvhost-*`)
  is absent from this machine, and its 3D driver was not run. Static analysis of
  `libGLESv2.so.2` is possible but slow: the regions carrying the GL enum tables
  are packed/compressed (`0x8513` appears only as noise), and the descriptor
  packing happens in unnamed internal functions.
- **The `0x750 + unit` contract.** The vendor driver writes it; nothing in this
  tree reads it for sampling; its meaning, width and face layout are unmeasured.
- **The face order and orientation.** These are only observable through a
  working cube sample, which none of the available interfaces provides.

The smallest next step that could change this is not another descriptor-bit
sweep. It is either (a) extracting the `0x750 + unit` encoding from the vendor
draw path (by static analysis of `libGLESv2.so.2`/`libardrv_dynamic.so`, or by
capturing its pushbuffer on a vendor kernel), or (b) an AR20 register description
for the third texture word. With that one word known, the six-face test in the
brief becomes a normal `GRATE_POKE` experiment.

### 64.8 Reproduce

`compile_cube.sh` downloads the L4T package (hash-checked), extracts
`libcgdrv.so`/`libnvrm.so`/`libnvos.so` into `subprojects/Linux_for_Tegra`,
configures a separate build with `-Dcgdrv=enabled`, and compiles every shader in
`shaders/` with `-v`. The blob is not committed; only the sources and the
resulting word dumps in `words/` are.

`hw_r_control.sh` runs the two hardware controls above and prints the frame
signatures. It needs the AC100 and `tools/assembler`.

Commands used (device, `~/build/grate-cgc`):

```
./tools/cgc -F -v shaders/cube_varying.glsl > words/cube_varying.cgc.txt 2>&1
./tools/cgc -F -v shaders/twod_varying.glsl > words/twod_varying.cgc.txt 2>&1
```

`words/*.cgc.txt` are the complete dumps (symbols, raw words, disassembly,
stream hexdump); the tables above are extracted from them.

### 64.9 Runs and method notes

Device: Toshiba AC100, Tegra 20 / AR20, `armv7l`, kernel
`7.2.0-gd1efa725955b`, Debian 13 (trixie), Mesa 25.0.7, user in `render` only.
Repository `i-am-cipher-null/grate`, branch `code/fragment-isa-experiments`,
head `c6da85e` before this note. Compiler package:
`Tegra20_Linux_R16.5_armhf.tbz2`, 24,432,568 bytes, SHA-256
`02b7a83b29cec59cf275b98f0de0fc87a24ffa25593af6af729cd2e881c41587`.

Hardware control: two assembler invocations per program with the standard
256x256 target, `--texture`, `--expected 0xDEADBEEF --testonly`; the failure path
prints the per-pixel words that the signatures above summarise. Both sample runs
produced the same 16,446-line dump byte for byte (`cmp` identical); the store
runs differed in the first band word.

The vendor binaries were read only: `ldd`, `nm -D`, `objdump -d`, `strings`.
Nothing from the vendor package was installed or run against the GPU.
