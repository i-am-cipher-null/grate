# Tegra 2 (AR20) - geometry submission (IDX)

**Status.** Reference. Addresses, bit positions and values are read off the
command streams Grate and the blob submit. Fields that have not been isolated are
marked `unknown`.

**Scope.** The `IDX` block at `0x100`..`0x126`: attribute buffers and formats, the
read/write masks, the index buffer and the draw words. The same registers with
per-field status markers are in [[GR3D Registers]] §2; the draw job itself is in
[[Command Stream]].

| Offset | Description |
|---:|:--|
| `0x100`..`0x1ff` | attribute buffer base addresses and format descriptions |
| `0x120` | input/output buffer bit masks |
| `0x121` | index buffer base address |
| `0x122` | drawing configuration |
| `0x123` | number of drawing elements |

## 1. Attribute buffers (`0x100`..`0x1ff`)

Two consecutive words describe each of the 16 input attribute buffers: the first
word is the memory address, the second the format description.

**First word**

| Bits | Meaning |
|-----:|:--|
| 0..31 | memory base address |

**Second word**

| Bits | Meaning |
|-----:|:--|
| 0..3 | attribute format (component type) |
| 4..7 | attribute size (component count) |
| 8..31 | attribute stride, in bytes |

The measured word `0x0000104d` is a `vec4` float32 attribute: format `0xd`, size
4, stride 16. An earlier revision of this table gave the size field as 4..6 and
the stride as 8..19; neither narrower bound is established, and the size field has
to be at least 4 bits to hold the value 4.

## 2. Input/output masks (`0x120`)

The masks select which attribute buffers are loaded from DRAM into the VPE and
which output buffers the vertex shader may write - the engine skips the
corresponding fetches and stores.

| Bits | Meaning |
|-----:|:--|
| 0..15 | output buffer write-enable mask |
| 16..31 | input buffer read-enable mask |

## 3. Index buffer (`0x121`)

| Bits | Meaning |
|-----:|:--|
| 0..31 | memory base address of the index buffer |

## 4. Drawing configuration (`0x122`)

How the VPE interprets the input vertices: primitive type and whether an index
buffer is used.

| Bits | Meaning |
|-----:|:--|
| 24..26 | primitive type |
| 27 | flat-shade last |
| 28..29 | index buffer type |

## 5. Element count (`0x123`)

| Bits | Meaning |
|-----:|:--|
| 20..31 | number of elements, minus 1 |
