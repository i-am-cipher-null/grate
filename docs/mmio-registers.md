A very approximate MMIO-register overview:

| First | Last  | Codename  | Use                    | Reference |
|------:|:------|:---------:|:-----------------------|:----------|
| 0x000 | 0x015 | HOST1X    | synchronization        | [[GR3D Registers]] §1 |
| 0x100 | 0x126 | IDX       | geometry submission    | [[GR3D Registers]] §2 |
| 0x200 | 0x20b | VPE       | vertex shader          | [[GR3D Registers]] §3 |
| 0x300 | 0x364 | SU        | (Setup Unit) program vertex to fragment shader<br>linker engine (VPE clip output to MFU) | [[GR3D Registers]] §4 |
| 0x400 | 0x41a | QRast     | (Quad Rasterizer) depth/stencil/VCAA,<br>output goes to PSEQ or MFU | [[GR3D Registers]] §5 |
| 0x500 | 0x547 | PSEQ      | (Program Sequencer) control pixel pipeline flow  | [[GR3D Registers]] §6 |
| 0x600 | 0x60e | MFU       | attribute interpolate and special functions unit | [[GR3D Registers]] §7 |
| 0x700 | 0x79f | TEX       | texture mapping                                  | [[GR3D Registers]] §8 |
| 0x800 | 0x807 | ALU       | fragment shader arithmetic-logic unit            | [[GR3D Registers]] §9 |
| 0x900 | 0x90b | DW        | (Data Write) shader export unit, drive PSEQ<br>output to FDC using specified pixel color format<br>or back to PSEQ | [[GR3D Registers]] §10 |
| 0xa00 | 0xa0c | FDC       | (Fragment Data Cache) configure and control (flush to DRAM)<br>unified memory cache used by QRast, PSEQ, DW | [[GR3D Registers]] §11 |
| 0xb00 | 0xb14 | GSHIM     | ???                    | [[GR3D Registers]] §12 |
| 0xc00 | 0xc53 | PIPEALIAS | ???                    | [[GR3D Registers]] §13 |
| 0xe00 | 0xe87 | GLOBAL    | store framebuffer base addresses used for<br>intermediate (depth buffer, stencil...) and final<br>render results, various engines configs used to<br>orchestrate their coordination and miscellaneous<br>stuff | [[GR3D Registers]] §14 |

Two 3D-side register blocks are only partly inside that span: the `0xe30`..`0xe3f`
render-target limit registers (pushed by ardrv, still unnamed in `tgr_3d.xml.h`)
and the Tegra 30/114-only registers (`0x90x`, `0xe4x`) that Grate resets but does
not otherwise use. Both are listed in [[GR3D Registers]] §14.2 and §15.

The 2D engine is a separate class space, not part of this map; see
[[GR2D Registers]].

Start at [[Index]] for the document map and the reading order by task.
