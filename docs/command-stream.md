# Tegra 2 (AR20) 3D engine - command stream

Host1x command stream for the 3D engine: CDMA instruction encodings and the
register pushes that load a fragment program. See [[Host1x]] for the general
command-stream model and [[Fragment Shader ISA]] for the words themselves.

See [NVIDIA's Host1x documentation](http://http.download.nvidia.com/tegra-public-appnotes/host1x.html)

## 1. CDMA instructions

|  Bits  | Meaning          |
|-------:|:-----------------|
|     31 | 0                |
| 28..30 | opcode           |
|  0..27 | opcode dependent |

## 2. Opcode encodings

|  Value | Mnemonic |
|-------:|:---------|
|      0 | setclass |
|      1 | incr     |
|      2 | nonincr  |
|      3 | mask     |
|      4 | imm      |
|      5 | restart  |
|      6 | gather   |


### setclass

|  Bits  | Meaning    |
|-------:|:-----------|
| 28..31 | opcode = 0 |
| 16..27 | offset     |
|  6..15 | class id   |
|   0..5 | mask       |

### incr/nonincr

|  Bits  | Meaning        |
|-------:|:---------------|
| 28..31 | opcode = 1 / 2 |
| 16..27 | offset         |
|  0..15 | count          |

### mask

|  Bits  | Meaning    |
|-------:|:-----------|
| 28..31 | opcode = 3 |
| 16..27 | offset     |
|  0..15 | mask       |

### imm

|  Bits  | Meaning    |
|-------:|:-----------|
| 28..31 | opcode = 4 |
| 16..27 | offset     |
|  0..15 | value      |

### restart

|  Bits  | Meaning    |
|-------:|:-----------|
| 28..31 | opcode = 5 |
|  0..27 | address    |

### gather

|  Bits  | Meaning    |
|-------:|:-----------|
| 28..31 | opcode = 6 |
| 16..27 | offset     |
|     15 | insert     |
|     14 | incr    |
|  0..13 | count   |

## 3. GR3D register pushes for fragment programs

Every fragment-shader stream is pushed through an index/data register pair in
`HOST1X_CLASS_GR3D`. `libcgc/shader.c` in Grate decodes a whole command stream
with exactly these seven pairs, one word per batch index, which is a useful
cross-check because the blob drives the same registers:

| Index register | Data register | Stream |
|---------------:|:--------------|:--|
| `0x540` | `0x541` | PSEQ program words |
| `0x600` | `0x601` | MFU schedule words |
| `0x603` | `0x604` | MFU program words (two dwords per MFU, `2*j` per batch) |
| `0x700` | `0x701` | TEX program words |
| `0x800` | `0x801` | ALU schedule words |
| `0x803` | `0x804` | ALU program words (four 64-bit slots per packet) |
| `0x900` | `0x901` | DW program words |

The dumper asserts that PSEQ, MFU, TEX, DW and ALU all received the *same*
number of entries - i.e. the streams are pushed in lockstep, one entry per exec
batch (MFU and ALU extras are counted by their schedule words), which is also
what the batched encoder in [[Compiler Internals]] assumes.

The ALU/MFU schedule word is read by the vendor dumper as

```
count = word & 3          /* words to execute this batch */
addr  = word >> 2         /* index of the first word */
```

consistent with the `count[1:0]` / `address[7:2]` layout documented in
[[Writing a Compiler]] section 4.1 (the "wide" ALU variant uses bits 10:8 and
22:16 instead, and is not used by the blob on AR20).

## 4. Fragment program upload sequence


**Known**, from `grate-asm.c` and `include/tgr_3d.xml.h`; this is the definitive
map and was not previously written down anywhere in `docs/`.

| Host1x push | Register | Contents |
|:--|:--|:--|
| `IMM 0x500 0` | `FP_PSEQ_UPLOAD_INST_BUFFER_FLUSH` | flush before upload (assembler emits this, Mesa does not) |
| `IMM 0x520 <0x20006000 \| n>` | `FP_PSEQ_ENGINE_INST` | start PSEQ, `n` = batch count |
| `IMM 0x540 0` | `FP_PSEQ_UPLOAD_INST_ID` | upload pointer reset |
| `NONINCR 0x541 n` | `FP_PSEQ_UPLOAD_INST` | PSEQ words, one per batch |
| `IMM 0x545 0` | `FP_PSEQ_QUAD_ID` | |
| `INCR 0x546 1` | `FP_PSEQ_DW_CFG` | `pseq_to_dw_exec_nb` at bit 6 |
| `IMM 0x600 0` | `FP_UPLOAD_MFU_SCHED_ID` | |
| `NONINCR 0x601 n` | `FP_UPLOAD_MFU_SCHED` | MFU schedule, one per batch |
| `IMM 0x603 0` | `FP_UPLOAD_MFU_INST_ID` | |
| `NONINCR 0x604 2*m` | `FP_UPLOAD_MFU_INST` | MFU words, **high dword first** |
| `IMM 0x700 0` | `FP_UPLOAD_TEX_INST_ID` | |
| `NONINCR 0x701 n` | `FP_UPLOAD_TEX_INST` | TEX words, one per batch |
| `IMM 0x800 0` | `FP_UPLOAD_ALU_SCHED_ID` | |
| `NONINCR 0x801 n` | `FP_UPLOAD_ALU_SCHED` | ALU schedule, one per batch |
| `IMM 0x803 0` | `FP_UPLOAD_ALU_INST_ID` | |
| `NONINCR 0x804 8*p` | `FP_UPLOAD_ALU_INST` | ALU packets, 4 insns x 2 dwords, **high first** |
| `NONINCR 0x806 n` | `FP_UPLOAD_ALU_INST_COMPLEMENT` | the `ALU_COMPLEMENT` word per batch (zero in practice) |
| `INCR 0x820 32` | `FP_CONST(0..31)` | fragment uniforms, 32 words |
| `IMM 0x900 0` / `NONINCR 0x901 n` | `FP_UPLOAD_DW_INST` | DW words, one per batch |
| `INCR 0x300 2*k` | linker program | 2 words per varying ([[Shader Linking]]) |
| `IMM 0xe20 <(alu_buffer_size-1) \| ...>` | `ALU_BUFFER_SIZE` | bits 0..1 = size-1, bits 2..9 = `(300-1)/(size*4)` |
| `IMM 0xe21 <rows \| 64/rows<<8>` | `TRAM_SETUP` | used TRAM rows |

