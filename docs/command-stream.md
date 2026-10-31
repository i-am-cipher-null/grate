See [NVIDIA's Host1x documentation](http://http.download.nvidia.com/tegra-public-appnotes/host1x.html)

## CDMA instructions

|  Bits  | Meaning          |
|-------:|:-----------------|
|     31 | 0                |
| 28..30 | opcode           |
|  0..27 | opcode dependent |

## Opcodes

|  Value | Mnemonic |
|-------:|:---------|
|      0 | setclass |
|      1 | incr     |
|      2 | nonincr  |
|      3 | mask     |
|      4 | imm      |
|      5 | restart  |
|      6 | gather   |

## Opcode encodings

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

## GR3D push registers

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
