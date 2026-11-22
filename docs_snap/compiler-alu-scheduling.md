# Compiler ALU scheduling

This document is the compiler-facing reading of the ALU forwarding and
scheduling behaviour measured on AR20 in notes 28 to 37 of
`tests/grate/asm_characterize.experiments.md`. It states what a scheduler may
rely on, which of the rules in [[Writing a Compiler]] section 4 no longer hold,
and what a backend should change. It contains no new measurements: every claim
cites the notes section and the pinned manifest that produced it, and anything
not measured is marked as such.

**Status.** This is a research and design note, not an implemented backend
specification. The recommended compiler changes below are proposals; this
document does not describe changes already made to the compiler.

**Relationship to the ISA references.** `docs/fragment-shader-isa.md` is the
concise canonical ISA description. `docs_snap/fragment-shader-isa.md` is the
extended AR20 evidence and research reference. This document extracts the
ALU scheduling and liveness consequences for compiler design; it is not a
second ISA specification.

**Generation scope.** Everything here is AR20 (Tegra 2, `T20`) behaviour,
measured on the headless AC100 with the grate assembler and the manifest-driven
harness. Nothing on this page is claimed for T114 or later, whose schedule word
uses a different layout (`wide_alu_sched`, `count[10:8]` / `addr[22:16]`, and
the vendor compiler writes `0xe0` into bits 29..31). Where a rule is likely to
differ on T114 it is called out.

## 1. The three units a scheduler works with

| unit | what it is | size |
|:--|:--|:--|
| packet | one group of four ALU slots, one `sched` address | 4 instructions |
| batch | one entry in every per-batch stream (`0x801`, `0x804`, `0x806`, `0x901`, PSEQ, MFU, TEX) | 1..3 packets |
| group | `alu_buffer_size` consecutive batches, flushed together | 1..3 batches |

The `0x801` word carries `count[1:0]` = **packets** and `addr[7:2]` = the first
packet of that batch, so a batch reaches 12 instruction slots through repeated
`ALU:` blocks and a fourth block is refused by the assembler
([[Fragment Shader ISA]] "Batch structure", notes 32). The count is the 2-bit
field's limit, not the grammar's.

Two further limits come from the same measurement set and constrain a scheduler
more than the ALU width does:

* only a DW word in the **group's first batch** produces an observable store
  (notes 3), and a group produces **one** store: with two DWs in one group the
  first word's target is written and the second is dropped, whatever the two
  targets are (notes 29, 33);
* a group is driver state (`alu_buffer_size`), not shader state, so the group's
  extent is not something a compiler chooses at encode time.

## 2. What the hardware forwards, by distance

"Latch" below means the `alu0..alu3` result registers: the result of the ALU
slot with that number, whatever its destination field said. "Row" means
`r0/r1` and `r2/r3`, the registers a DW stores and a later packet reads.

| producer → consumer | visible on AR20 | notes |
|:--|:--|--:|
| same packet, batch has **one** packet | any latch, any reader slot, either order, own slot excluded | 28 |
| same packet, batch has more than one packet | **no** | 32, 36 |
| read in packet k of a multi-packet batch | packet **(k-1) mod M**'s slot-N result, all four latches, any reader slot; M = the group's packet count | 32, 36, 38 |
| two-packet batch, either direction | both packets see each other; a packet never sees its own results | 36 |
| three-packet batch | forward one hop, and packet 0 reads packet 2 (the wrap); two packets apart is 0 | 32, 36 |
| one packet later, batch boundary inside a group (one-packet batches) | all four latches, any slot - the M=2/M=3 case of the rotation | 28, 36 |
| across a group flush, group is **one** batch | the group's **last packet's** slot-0 result, to slot 0 only, one group deep | 31, 32, 36 |
| across a group flush, group is 2 or 3 batches | 0, from any batch of the previous group, in the shapes tested | 36 |
| row written → read in the same packet | no | 28 |
| row written → read one packet later | no | 32 |
| row written → read after a group boundary | yes | 28 |
| latch across an idle group | no - a forward, not storage | 31 |

Three consequences are easy to miss:

1. **The intra-packet bypass exists only in a one-packet batch.** The same
   three-slot chain that returns `0x00100010` in a one-packet batch returns
   `0x00000000` in a three-packet batch of the same program (`p1-chain` versus
   `p0-chain`, notes 32). Nothing errors; the dependent links read zero. The
   rotation rule says the same thing: a packet sees packet (k-1) mod M, so in a
   multi-packet batch a slot's own packet is never a source.
2. **The forward is not one-directional.** Packet 0 of a multi-packet batch
   reads the batch's **last** packet, so "the previous packet" is a cycle, not
   an ordering (notes 36). Section 32's forward-only fold was measured with a
   probe that moved latch index and read slot together, so it could only see the
   forward half. For a scheduler the wrap matters because a two-packet batch
   makes a chain legal in both directions, and a three-packet one makes
   "packet 0 depends on packet 2" legal while "packet 2 depends on packet 0" is
   not.
3. **The group carry is one packet, not one group, of information.** It is the
   previous group's *last* packet's slot-0 result, so a batch that writes
   through slot 0 early and ends with NOPs carries nothing (`flush-3blk` `0`
   against `flush-3blk-last` `0x10`, notes 32), and a group whose slot 0 was
   idle carries nothing even if other slots wrote (notes 31). It is also only
   present when the group is a single batch: at `alu_buffer_size` 2 and 3 the
   same shape carries nothing (notes 36).

### Format rules

A latch read sees only the half its writer filled, and only in the format the
reader asked for (notes 28). What is measured, case by case:

* a `.l*` write is read by an `.l` reader in the same packet and one packet
  later, and is **not** seen by a bare (fp20) reader in either position;
* a `.lh` write is seen by a bare reader in the same packet, and **not** by an
  `.l` reader one packet later (`pkt-alu0-fp20` is `0`); the same-packet `.l`
  read of an `.lh` write was not measured;
* a `.*h`-only write is seen by neither reader, at any distance
  (`same-chain-hi`, `pkt-alu0-hi`, `flush-writer-hi` all read zero).

So a producer has to match the *consumer's* source format, not its own
preference: a value read by a later packet as fp20 must be written `.lh`, and a
value read as fx10 must be written `.l*`. That is the rule
[[Writing a Compiler]] 4.1-4.3 already states, and it survives unchanged - what
does not survive is the assumption that such a value can live in a latch.

## 3. Liveness: when a value must be in a row register

The measured rules give a simple decision procedure.

| value's next use | where it must live | why |
|:--|:--|:--|
| later slot of the **same packet**, batch is one packet | latch of the producing slot | intra-packet bypass (notes 28) |
| next packet, same batch, consumer is not packet 0 | latch of the producing slot | packet k reads k-1, all four latches (notes 32, 36) |
| consumer is packet 0 of a multi-packet batch | latch of the group's **last** packet (at M=4 packed two per batch: slot 3 only, notes 38) | the cycle wraps (notes 36, 38) |
| any packet after that, same batch | row register, read in a later group | no forward is that deep, and rows commit only at a group boundary |
| another packet of the **same** batch, non-adjacent | row register, read in a later group | rows are not visible inside a group (notes 28, 32) |
| later group | row register | the latch carries nothing across a flush once the group is more than one batch, and only slot 0 even then |

Consequences for register allocation:

* **`alu0..3` are not spill slots and not registers in the liveness sense.**
  Nothing is saved: an idle group loses the value (notes 31). A value cannot be
  kept "in `alu0`" across a group, and a scheduler must not free a row register
  because the value "is also in a latch".
* **Row registers are the only place a value can live across a group
  boundary**, which is also where a DW reads its payload. A row round trip is
  therefore the cost of any dependency that spans a group, and the cost of every
  export.
* **A row write is not visible inside its own group.** A producer and its reader
  in the same batch cannot communicate through a row (`p0-row`, `0`; notes 32),
  and the same-packet case is older news (notes 28). If they are in the same
  batch and not adjacent packets, the value has to be duplicated in a latch or
  the schedule has to move one of them.

## 4. Scheduling constraints when packing packets into a batch

Packing is available (12 slots instead of 4) and is not free. The constraints a
scheduler has to satisfy, in the order they bind:

1. **Dependent ALU operations must be in packets that the rotation pairs.**
   In a multi-packet batch the latch path is packet (k-1) mod M -> packet k,
   one hop, any slot, all four latches. So a chain becomes one hop per packet:
   `chain-3pkt` runs three dependent operations as `0x00100010`, but only
   because each hop sits in its own packet (notes 32). In a two-packet batch the
   pair is mutual, so the one hop may point either way (`chain-2pkt` `0x10`,
   `back` `0x40`, notes 36); in a three-packet batch only the forward hops work
   and the wrap is the extra one.
2. **A packet's slots must be mutually independent.** Inside a packet of a
   multi-packet batch there is no bypass at all - not even backwards - and a
   self-read is zero in every shape (notes 32, 36).
3. **The batch's last packet owns the group carry.** If the batch's slot 0 is
   meant to feed the next group (the only latch carry that exists), the write
   has to be in the last packet and read by the next group's slot 0.
4. **The group's first batch owns the store.** ALU packets in the group's
   second and third batches can produce values but cannot store them in that
   group; their payload is stored by the next group's DW at the earliest. With
   `alu_buffer_size` 1 every batch is a group, with 2 only even batches can
   store, with 3 only every third.
5. **One DW per group, and it reads group-final rows.** A second DW in the group
   is dropped, on whatever pair of targets (notes 29, 33). The payload is read
   at the group boundary, so the row pair a DW names must not be overwritten
   later in the group by a different value the scheduler wants to keep -
   including by a later packet of the same batch.
6. **Register pressure moves from rows to latches as packets are packed.** A
   three-packet batch can hold three dependent hops without touching a row
   inside the batch, but it cannot use a slot-level temporary chain, so the
   allocator has to be told which hops are packet-aligned.

## 5. Compiler assumptions this invalidates

Against [[Writing a Compiler]] section 4 and the reference backend it describes:

| existing statement | status | evidence |
|:--|:--|:--|
| "build the intra-batch dependency graph over `aluN`, chain `slot k writes -> slot k+1 reads`" | **valid only for one-packet batches**; wrong (silently zero) in a packed batch | `p0-chain` 0 vs `p1-chain` `0x00100010`, notes 32 |
| "write the chain tail back to a row register with `.lh`" | stays valid, and is now known to be the *only* way a value survives a group | notes 28, 31 |
| "a value a later batch must read has to be written `.lh`" | true as a format rule, but a latch cannot carry it: the row is the transport | notes 31, 32 |
| "one ALU packet and one MFU word per NIR instruction, `address` = list index" | the address counts **packets**, not instructions or batches; the reference path is right only because it never packs | notes 32 |
| "one DW per batch" (MRT) | too weak: the observable limit is **one DW per group**, and the second word is dropped, not merged | notes 29, 33 |
| the blob sets schedule bits 29/30 from read/write masks (`alu_flush_batch`) | emitted by the vendor compiler, but neither bit changes forwarding, stores or scheduling on AR20, per batch | notes 30, 34 |

The last row is worth separating: `compiler-internals.md` shows the blob
computing bit 29 (a bank is read, or partially written) and bit 30 (a bank is
written) per ALU row from its read/write masks. That is a decompile-derived
explanation of *why the vendor encoder sets them*, and it is not confirmed
hardware behaviour. On AR20 the measurement is that setting them, on every batch
or on one batch at a time, changes nothing observable in five shapes. A backend
should therefore not rely on them to resolve a hazard on AR20, and should not
assume they are inert on T114.

## 6. Recommended changes

None of these changes are implemented. They are proposals, not a description
of current compiler behaviour. The four-packet rotation case in section 7 is
the next hardware-validation gate; until it is tested, do not treat the
general packet/group scheduling model as fully established.

1. **Make the packet an explicit unit in the backend's IR.** A batch is a list
   of one to three packets; the encoder sets `count = len(packets)` and
   `addr` = the running packet index. Today's reference path has neither concept,
   which is why it cannot pack even where packing is free.
2. **Encode ALU dependencies as packet edges, not slot edges.** Within a packet,
   allow only independent operations. Where a batch is known to be one packet,
   permit the slot chain as a fast path - but decide the packet count before
   fixing the chain, or the encoder will emit a chain the hardware cannot serve.
   Validate with the `pack` manifest: `p0-chain` must stay `0` and `p1-chain`
   `0x00100010` after any change.
3. **Materialise values into rows at every group boundary, in the format the
   consumer reads** (`.lh` for fp20/bare readers, `.l*` for fx10 readers). Treat
   the flush carry as an optional optimisation that a verifier must allow
   explicitly: it exists, but it is one packet deep, slot-0 only, comes from the
   *last* packet, and was only measured when the group is a single batch - four
   conditions that a scheduler will break by accident. It is not a substitute
   for a row on any multi-batch group.
4. **Schedule exports one group apart and keep their payload rows stable.** One
   DW per group, always in the group's first batch, with the row pair it names
   untouched from the write to the group boundary. For MRT that means one group
   per target, so an N-target shader costs at least N groups, and the 64-batch
   budget is what it is spent from.
5. **Do not model latches as registers.** The register allocator should treat
   `alu0..3` as an intra-packet forwarding path with a lifetime measured in
   packets, never as storage; the measured eviction rules (idle group, second
   packet boundary, own slot) are all silent failures.
6. **Re-pin the manifests after any scheduler change.** The `pack`, `pack2`,
   `pack3`, `latch-rotate`, `send-pack`, `sched-at`, `sched-b2` and `dw-mrt`
   sets each pin exact words for the shapes an encoder can get wrong, and
   re-running them is cheaper than re-deriving the model.

## 7. Open questions that limit the model

* The mechanism behind the rotation. "Packet k reads packet (k-1) mod M" is an
  input/output relation; a rotating register file, a reordered issue and a
  double-buffered write bus all fit it, and nothing measured separates them.
* What fixes M. It equals the group's packet count in the measured shapes
  (`alu_buffer_size` 1/2/3 with one-packet batches, 1 with a two- and a
  three-packet batch, and 4 with one packet per batch). With the group's four
  packets packed two per batch at size 2 (notes 38) the forward edges still
  follow the rule, but only the slot-3 wrap survives; why the packing costs the
  other latches' wrap, and what a 3 + 1 packing does, are open.
* Why the group flush preserves the last packet's slot-0 result when the group
  is a single batch and nothing when it is two or three (notes 36). Sections 31
  and 32 measured only the first case, so their claims hold but their scope is
  narrower than they read.
* Why `send`/`recv` do not cross a packet boundary although the latch path does
  (notes 37), and the untested pairing: receive in slot 1 of packet 1 from a
  sender in slot 0 of packet 0 through the latch rotation.
* Whether MFU and TEX per-batch counts use the same packet unit as ALU.
* Whether the bits 29/30 hazard flags do anything on T114, and what bits 8..28
  mean on AR20.

## 8. Established invariants, labelled

Everything above in prose, as the list a backend can be checked against. Labels:
**Measured** = repeatable hardware observation, **Inferred** = consistent with
measurement but not isolated, **Open** = not established. All are AR20.

| # | invariant | label | evidence |
|--:|:--|:--|:--|
| 1 | In a one-packet batch, a latch read in slot S sees the packet's slot-N result from any slot, in either issue order, whichever slot wrote it. | **Measured** | notes 28 |
| 2 | The reading slot's own result is 0, in a one-packet batch and in every multi-packet arrangement. | **Measured** | notes 28, 32, 36 |
| 3 | In a multi-packet batch a latch read in slot S of packet k is served by packet (k-1) mod M, M = the group's packet count, all four latches, any slot. | **Measured** for M=1 to 4 when each batch holds one packet; at M=4 packed two packets per batch the forward edges hold and only the slot-3 wrap does | notes 32, 36, 38 |
| 4 | A two-packet batch forwards in both directions, so "packet 0 sees packet 1" is legal. Nothing in a multi-packet batch forwards two packets. | **Measured** | notes 36 |
| 5 | A packet never sees its own results in a multi-packet batch, so its slots are independent and its chain must be one hop per packet. | **Measured** | notes 32, 36 |
| 6 | `.l*` (fx10) and `.lh` (fp20) writes fill different latch views; a bare read sees only an `.lh` write, an `.l` read only a `.l*` one, a `.*h`-only write fills neither, at every measured distance and in both directions. | **Measured** | notes 28, 36 |
| 7 | A `.lh` write is readable bare in the same packet but not by an `.l` reader one packet later; a same-packet `.l` read of an `.lh` write was not measured. | **Measured** / **Open** | notes 28 |
| 8 | The previous group's last packet's slot-0 result is readable by the next group's slot 0, one group deep, not a saved register. | **Measured** when the group is one batch | notes 31, 32, 36 |
| 9 | That group carry is absent when the group is two or three batches, in the shapes tested. | **Measured** (negative) | notes 36 |
| 10 | Row registers are the only storage that survives a group: a row write is invisible in its own packet and its own group, visible to the next group. | **Measured** | notes 28, 32 |
| 11 | A group produces one observable DW; a second DW in the same group is dropped and writes nothing, whatever its target. | **Measured** | notes 29, 33 |
| 12 | `recv`/`send` transfer between slots **inside one packet** and work in any tested packet of a batch. Tested cross-packet pairings do not transfer in either direction; the slot-0 sender in packet 0 to slot-1 receiver in packet 1 through rotation remains untested. | **Measured** / **Open** | notes 13, 16, 37 |
| 13 | A `send` on a producer does not disturb the latch path, and the group carry holds the plain slot-0 result. | **Measured** | notes 37 |
| 14 | Schedule bits 29 and 30 change nothing observable, on any of the three batches of the template, in five shapes. | **Measured** | notes 30, 34, 35 |
| 15 | M equals the group's packet count for M=1 to 4; a four-packet group packed two per batch keeps the forward edges but loses the wrap for latches 0 to 2. | **Measured** for M=1 to 4 | notes 38 |
| 16 | The rotation applies to MFU/TEX streams or to the bits 8..28 of the schedule word. | **Open** | - |
| 17 | Anything about T114: the schedule word layout differs and its count field is `count[10:8]`. | **Open** | notes 30 |

### Assumptions that are unsafe under this model

1. **"`aluN` is a register."** Every failure mode is silent: a flush, a second
   packet boundary, the reader's own slot and a wrong format all read zero, with
   no error bit. An allocator must not let a value's only copy live in a latch.
2. **"A latch dependency is a slot edge inside the batch."** In a packed batch
   a slot-level chain reads zeros; the only edges are packet edges, and the read
   slot is irrelevant while the *packet index* is not.
3. **"Dependencies only point forward."** Packet 0 of a two- or three-packet
   batch reads the batch's last packet. A scheduler that orders packets by
   dependency as an acyclic list will silently produce a wrong packet 0.
4. **"The flush carry is available anywhere a slot-0 write is."** It is
   measured only for single-batch groups; relying on it at
   `alu_buffer_size` 2/3 is unsupported.
5. **"`send`/`recv` is a cross-packet accumulator."** Within a packet yes;
   across a packet boundary no measurement shows a transfer, so a scheduler must
   not use it to carry a dependency between packets.
6. **"Bits 29/30 encode a hazard the hardware uses."** On AR20 they do not, on
   any batch; on T114 nothing is claimed.

### Recommended scheduling, dependency and liveness rules

* Build the dependency graph over packets, not slots. Within a packet, require
  independence, and decide the packet count before choosing a chain shape.
* When a batch holds two or more packets, allow a dependency edge only between
  packets that the rotation pairs - forward for every packet index k>=1, plus
  the 0-from-last edge - and mark every other cross-packet edge as needing a
  row.
* Treat latch lifetimes in packets, and give a value only one latch hop before
  it must be in a row. Materialise to `.lh` for fp20 readers and `.l*` for fx10
  readers, at every group boundary.
* Keep the "one DW per group, in the group's first batch, rows stable from write
  to boundary" rule as a hard constraint (notes 29, 33).
* Do not rely on the flush carry except where a verifier can prove the group is
  one batch and the writer is its last packet and the reader is slot 0.

None of this is implemented in the compiler, and this note does not justify an
implementation on its own: the packet-count and rotation rules are measured up to
four packets in the packings tested, but groups larger than four, other packings
and the T114 layout are not.

## 9. Evidence map

| claim | notes | manifest | recorded runs |
|:--|--:|:--|:--|
| intra-packet bypass, own-slot exclusion, format rules | 28 | `asm_characterize.alu_latch.json` | `pin-alu-latch`, `verify-alu-latch`, `clean-alu-latch` |
| group carry is a forward, one group deep, slot 0 | 31 | `asm_characterize.alu_latch_flush.json` | `pin-alu-latch-flush`, `verify-alu-latch-flush`, `clean-alu-latch-flush` |
| count = packets, 12 slots, packed forwarding rules | 32 | `asm_characterize.alu_pack.json` | `pin-alu-pack`, `verify-alu-pack`, `clean-alu-pack` |
| one DW per group, first word wins, dropped word writes nothing | 29, 33 | `asm_characterize.dw_two_target.json`, `asm_characterize.dw_mrt.json` | `pin-dw-two-target`, `pin-dw-mrt`, `verify-dw-mrt`, `clean-dw-mrt` |
| bits 29/30 inert globally and per batch | 30, 34 | `asm_characterize.alu_sched.json`, `asm_characterize.alu_sched_at.json` | `pin-alu-sched-at`, `verify-alu-sched-at`, `clean-alu-sched-at` |
| bits 29/30 inert on the readback batch | 35 | `asm_characterize.alu_sched_b2.json` | `pin-alu-sched-b2`, `verify-alu-sched-b2`, `clean-alu-sched-b2` |
| two-packet batch forwarding, both directions, carry, formats | 36 | `asm_characterize.alu_pack2.json` | `pin-alu-pack2`, `verify-alu-pack2`, `clean-alu-pack2` |
| three-packet rotation and the wrap | 36 | `asm_characterize.alu_pack3.json` | `pin-alu-pack3`, `verify-alu-pack3`, `clean-alu-pack3` |
| rotation is group-scoped, sizes 1/2/3 | 36 | `asm_characterize.latch_rotate.json` | `pin-latch-rotate`, `verify-latch-rotate`, `clean-latch-rotate` |
| M=4 rotation, one and two packets per batch | 38 | `asm_characterize.alu_pack4.json`, `_wrap`, `_probe`, `_probe2`, `_size4` | `pin-`/`verify-alu-pack4`, `-wrap`, `-probe`, `-probe2`, `-size4` |
| `send`/`recv` are packet-local | 37 | `asm_characterize.alu_send_pack.json` | `pin-alu-send-pack`, `verify-alu-send-pack`, `clean-alu-send-pack` |

The run directories live under `tests/grate/asm/results/` and each holds the
results rows, the run metadata (device, kernel, tree, source hashes) and, for
the schedule-bit sets, the uploaded `0x801`/`0x806` words per batch.
