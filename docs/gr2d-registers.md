# Tegra 2 (AR20) — GR2D register reference

The 2D engine has two classes: **0x51** (pixel/fill operations, called *g2d* below)
and **0x52** (the surface/scaling blitter, *surfd*). Everything Grate pushes for
2D goes through one of those two classes, selected with `setclass`.

This document is a register-level reference for what is known. It supersedes the
one-line entry for the 2D engine in [[MMIO Registers]] and complements
[[Command Stream]], which covers the CDMA encoding and the 3D program upload.

## 0. Status vocabulary

No vendor register database for GR2D is available: the repository has no
`tgr_2d.xml.h`, and the 2D register names below are **Grate's names** taken from
`src/libhost1x/host1x-gr2d.c`, which is the only place in the tree that programs
this engine. Each register is marked:

| Mark | Meaning |
|:--|:--|
| **R** | read off a working command stream (Grate's, validated by rendering) |
| **I** | inferred: the value works, the field meaning is deduced from the code comment or from one experiment |
| **?** | address known (it is written), meaning unknown |

The per-register *address* is solid for every row marked R or I: the address comes
from the CDMA `mask`/`nonincr` operand, so an off-by-one in this table would break
the operation.

## 1. Where the 2D engine sits

| Item | Value |
|:--|:--|
| Host1x class, fills/clears | `0x51` |
| Host1x class, surface blit | `0x52` |
| Syncpoint increment register | `0x000` in both classes |
| Command buffer | `HOST1X_BO_FLAG_COMMAND_BUFFER`, one job per operation |

Both classes use the same submission shape: program the registers, write the
syncpoint-increment word to `0x000`, submit, then wait for the fence. There is no
program sequencer and no instruction stream - every 2D operation is a fixed
register set that starts when the trigger register is written.

## 2. Class 0x51 - fill / clear / blit

| Addr | Name | Status | Contents |
|--:|:--|:--:|:--|
| `0x000` | `SYNCPT_INCR` | R | `0x000001 << 8 \| syncpt_id`: increment syncpoint `syncpt_id` by 1 when the operation completes. Same encoding as the 3D engine. |
| `0x009` | `TRIGGER` | I | Operation start: `0x3A` in this class - fill and blit alike. Writing it starts the operation. |
| `0x00c` | `CMDSEL` | I | Secondary selector, `0` in this class (fill and blit both). Written immediately after the trigger. |
| `0x01e` | `CONTROLSECOND` | ? | Zero in every operation Grate issues. |
| `0x01f` | `CONTROLMAIN` | I | Main operation control word, see 2.1. |
| `0x020` | `ROPFADE` | I | Raster op / fade control; `0xCC` in every Grate operation. Presumably carries the 4-term ROP3 style function and the fade coefficient; neither is varied by Grate. |
| `0x02b` | `DSTBA` | R | Destination base address (relocated; byte address in the BO). |
| `0x02e` | `DSTST` | R | Destination stride in bytes. |
| `0x031` | `SRCBA` | R | Source base address (blit only). |
| `0x034` | `SRCST` | R | Source stride in bytes (blit only). |
| `0x035` | `COLOR` | R | Fill colour, already packed in the destination pixel format. |
| `0x038` | `DSTSIZE` | R | `height << 16 \| width` of the destination rectangle. |
| `0x039` | `SRCPS` | R | Source origin: `y << 16 \| x`. |
| `0x03a` | `DSTPS` | R | Destination origin: `y << 16 \| x`. |
| `0x046` | `TILEMODE` | I | Tile layout selector, see 2.2. |

### 2.1 `CONTROLMAIN` (0x01f)

| Bits | Field | Status | Meaning |
|--:|:--|:--:|:--|
| 28 | `SB_HFLIP` | I | Source horizontal flip (surface blit path only; set for the RGB->RGB path) |
| 27 | `SB_VFLIP` | I | Source vertical flip (same) |
| 20 | `SRC_DEPTH_SAME` | I | `1` = source colour depth equals destination depth; `0` = mono source |
| 17:16 | `DST_BPP_CODE` | I | Destination colour depth: `bytes_per_pixel >> 1`, i.e. `0` = 8 bpp, `1` = 16 bpp, `2` = 32 bpp |
| 14 | `YFLIP` | I | Destination vertical flip |
| 10 | `YDIR` | I | Backwards copy in y (used when source and destination overlap and the destination starts lower) |
| 9 | `XDIR` | I | Backwards copy in x (same, for the horizontal axis) |
| 6 | `SRCSLD` | I | Source solid: fill the destination from the constant `0x035` instead of reading a source surface |
| 2 | `TURBOFILL` | I | Use the fast fill path |

The blit path sets `SRC_DEPTH_SAME` and `DST_BPP_CODE`, the fill path sets
`SRCSLD | TURBOFILL`; the directional bits only appear for overlapping
same-surface copies, where the driver also reverses the start coordinates.

### 2.2 `TILEMODE` (0x046)

| Bits | Field | Status | Meaning |
|--:|:--|:--:|:--|
| 20 | `DST_TILED` | I | `1` = destination surface is `PIX_BUF_LAYOUT_TILED_16x16` |
| 0 | `SRC_TILED` | I | `1` = source surface is tiled |

Tiling is 16x16 with 16-bit elements: tile `(tx, ty)` starts at
`ty * 16 * stride + tx * 256 * bytes_per_pixel`, and inside it the offset for
`(x, y)` is `(y % 16) * 16 * bytes_per_pixel + (x % 16) * bytes_per_pixel`.
Grate computes exactly that in `sb_offset()`, and a fill into a tiled surface
with `0x20 << 20` writes the same bytes a tiled 3D render target does.

### 2.3 Fixed-point formats used by the 2D engine

None of the 2D coefficient or scale fields are IEEE floats; all are sign/magnitude
or two's-complement fixed point, and Grate packs them itself:

| Format | Encoding | Used for |
|:--|:--|:--|
| 6.12 | `(int)(x * 4096 + 0.5) & 0x3FFFF` | scaling increments |
| 2.7 | sign bit 8, magnitude in bits 7:0 (`x * 128`) | CSC coefficients (`cvr`, `cub`, `cur`, `cvb`) |
| 1.7 | sign bit 8, magnitude in bits 7:0 (`x * 128`) | CSC coefficients (`cug`, `cvg`) |
| 0.8 | `(int)(x * 256 + 0.5) & 0xFF` | initial source offsets |

## 3. Class 0x52 - surface blit (scaling and colour conversion)

| Addr | Name | Status | Contents |
|--:|:--|:--:|:--|
| `0x000` | `SYNCPT_INCR` | R | as class 0x51 |
| `0x009` | `TRIGGER` | I | `0x38` for this class (surface blit) |
| `0x00c` | `CMDSEL` | I | `1` |
| `0x011` | `VDDA` | I | Vertical scale increment, 6.12. Clamped to `[1/4096, 64 - 1/4096]` by the driver |
| `0x012` | `VDDAINI` | I | Initial source y offset, 0.8 |
| `0x013` | `HDDA` | I | Horizontal scale increment, 6.12 |
| `0x014` | `HDDAINILS` | I | Initial source x offset, 0.8 (`ls` = limited/shifted) |
| `0x015` | `CSCFIRST` | I | CSC coefficients: `cvr` at 23:12, `cub` at 11:0, both 2.7 |
| `0x016` | `CSCSECOND` | I | `cyx` at 31:24 (1.7), `cur` at 23:12 (2.7), `cug` at 11:0 (1.7) |
| `0x017` | `CSCTHIRD` | I | `cvb` at 23:16 (2.7), `cvg` at 15:0 (1.7) |
| `0x01c` | `SBFORMAT` | I | `dst_format << 8 \| src_format`; `14` = BGRA8888, `15` = RGBA8888 in Grate's numbering |
| `0x01d` | `CONTROLSB` | I | `hftype` at 23:20, `vfen` at 18, `vftype` at 19:16 (see 3.1) |
| `0x01e` | `CONTROLSECOND` | ? | zero |
| `0x01f` | `CONTROLMAIN` | I | as class 0x51, plus the flip bits 28/27 |
| `0x02b` | `DSTBA` | R | destination base, already offset by `sb_offset(dst, dx, dy)` |
| `0x02e` | `DSTST` | R | destination stride |
| `0x031` | `SRCBA` | R | source base, offset by `sb_offset(src, sx, sy)` |
| `0x033` | `SRCST` | R | source stride |
| `0x037` | `SRCSIZE` | R | `src_height << 16 \| src_width` |
| `0x038` | `DSTSIZE` | R | `dst_height << 16 \| dst_width` |
| `0x046` | `TILEMODE` | I | as class 0x51 |
| `0x048` | `SRCBA_SB_SURFBASE` | I | duplicated source base for the surface-blit datapath |
| `0x049` | `DSTBA_SB_SURFBASE` | I | duplicated destination base for the surface-blit datapath |

Two differences from class 0x51 are worth noting, both from comparing the two
push sequences:

* the surface blitter takes the *starting position* through the base addresses
  (`sb_offset()` folds `sx`/`sy` and `dx`/`dy` into them, with `yflip` adding
  `pitch * dst_height`) and through the 0.8 initial offsets, so it has no `SRCPS`
  or `DSTPS` register; class 0x51 writes those instead;
* the source stride is `0x033` here and `0x034` in class 0x51. The two are
  different register blocks, so this is not a conflict, but it is the kind of
  detail a second implementation has to copy rather than derive.

### 3.1 Scale-factor classes (`CONTROLSB`)

`hftype`/`vftype` select a filtering mode, and `hfen`/`vfen` gate the filtering
entirely. Grate picks them from the inverse scale factor alone:

| inverse scale | `hftype` | `vftype` |
|:--|--:|--:|
| not filtered (`hfen`/`vfen` = 0) | 7 | 0, with `vfen` forced to 0 |
| 1.0 | 7 | 0, with `vfen` cleared |
| < 1.0 (magnification) | 0 | 0 |
| 1.0 .. 1.3 | 1 | 1 |
| 1.3 .. 2.0 | 3 | 2 |
| >= 2.0 (minification) | 6 | 3 |

A value outside `[1/4096, 64 - 1/4096)` clamps the increment and clears the
corresponding enable, so the engine copies in that axis instead of scaling.

The vertical path also adjusts the heights it programs: without filtering it
subtracts 1 from source and destination height, with filtering 2, and clamps at
0 - the increment applies to the span *between* the first and last output rows.

The colour-conversion matrix is programmed to the identity (`cvr = cub = cyx = 1`,
everything else 0) for RGB->RGB, which is why a surface blit between two
`RGBA8888` surfaces is bit exact.

## 4. Trigger values

| Class | Trigger | Cmdsel | `CONTROLMAIN` | Operation |
|:--|:--|:--|:--|:--|
| 0x51 | `0x3A` | 0 | `SRCSLD \| TURBOFILL` | fill / clear from the constant colour |
| 0x51 | `0x3A` | 0 | `SRC_DEPTH_SAME \| DST_BPP_CODE` (+ flip/direction bits) | copy between two surfaces of equal bytes-per-pixel |
| 0x52 | `0x38` | 1 | (see 3.1) | scaled/colour-converted surface blit |

Within class 0x51 the trigger does **not** select the operation: fill and blit
share `0x3A`/`0` and are told apart by `CONTROLMAIN`. Across the two classes the
trigger value does differ, but the class has to be written with `setclass`
anyway, so the trigger alone never identifies an operation.

## 5. What is not known

* **The rest of both register blocks.** Everything above `0x049` is unmapped, and
  so is the range `0x001..0x008`, `0x00d..0x010`, `0x018..0x01b`, `0x021..0x02a`.
  Grate never touches them. Whether the 2D engine has a second output surface, a
  separate mask/alpha input, or a dither/AA control in that space is unknown.
* **`ROPFADE` (`0x020`).** `0xCC` is used for fill and blit alike. The ROP3 selector
  and any fade coefficient are not separated out; nothing in Grate varies them.
* **`CONTROLSECOND` (`0x01e`).** Written as 0 in both paths; on the 3D side the
  same "second" word carries the depth test and is not zero, so a 2D equivalent
  may exist and default off.
* **CRTC/scanline synchronisation.** Whether the engine can sync a 2D operation
  to a display scanline (the `0x52` block has no obvious register for it) is not
  established.
* **The `SBFORMAT` numbering.** `14`/`15` are the only two values Grate exercises,
  and it uses them for BGRA8888/RGBA8888. Whether the field shares the 3D
  `PIXEL_FORMAT_*` table (`0x0d` = RGBA8888 there, not `0x0f`) is untested.

## 6. Where the numbers come from

`src/libhost1x/host1x-gr2d.c` holds three operations - `host1x_gr2d_clear_rect()`
(fill), `host1x_gr2d_blit()` (class 0x51 blit) and
`host1x_gr2d_surface_blit()` (class 0x52) - and every address above appears there
as the operand of a CDMA `mask`/`nonincr` instruction. The 3D engine's register
blocks are in [[GR3D Registers]]; the general MMIO layout is in
[[MMIO Registers]].
