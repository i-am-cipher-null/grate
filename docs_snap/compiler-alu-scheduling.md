# Compiler ALU scheduling

This document is the compiler-facing reading of the ALU forwarding and
scheduling behaviour measured on AR20 in notes 28 to 42 of
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
| group | `alu_buffer_size` consecutive batches, flushed together | 1..4 batches measured |

The `0x801` word carries `count[1:0]` = **packets** and `addr[7:2]` = the first
packet of that batch, so a batch reaches 12 instruction slots through repeated
`ALU:` blocks and a fourth block is refused by the assembler
([[Fragment Shader ISA]] "Batch structure", notes 32). The count is the 2-bit
field's limit, not the grammar's.

Two further constraints matter to a scheduler:

* the assembler grammar provides one DW instruction position per exec batch;
  additional ALU packets in that batch do not add DW positions. Multiple DW
  instructions can therefore occur in one group, one per batch, but two DWs
  in one batch or packet cannot be encoded with this assembler;
* hardware DW results are position- and target-class-dependent. Class-2
  colour/spill/MRT tests (notes 29, 33) showed the first-batch target receiving
  the group's final row payload in the distinguishable cases. Later tests
  (note 42) found zero-valued writes to subsequent targets in mixed
  depth/colour pairs, and zero readbacks where the target's clear was already
  zero. A universal first-wins/drop rule is not established. For predictable
  nonzero output, one DW per group, placed in the first batch, remains the
  conservative compiler policy;
* a group is driver state (alu_buffer_size), not shader state, so its extent
  is not something a compiler chooses at encode time.

## 2. What the hardware forwards, by distance

"Latch" below means the `alu0..alu3` result registers: the result of the ALU
slot with that number, whatever its destination field said. "Row" means
`r0/r1` and `r2/r3`, the registers a DW stores and a later packet reads.

| producer → consumer | visible on AR20 | notes |
|:--|:--|--:|
| same packet, batch has **one** packet | any latch, any reader slot, either order, own slot excluded | 28 |
| same packet, batch has more than one packet | **no** | 32, 36 |
| read in packet k of a multi-packet group in a complete tested rotation | packet (k-1) mod M's slot-N result, all four latches, any reader slot | 32, 36, 39 |
| M=4, size 2, split 3+1 or 1+3 | full cycle, including all packet-3-to-packet-0 latch wraps | 39 |
| M=4, size 2, split 2+2 | three forward edges for all latches; wrap packet 3 to packet 0 only through latch 3 written in slot 3 | 38 |
| two-packet batch, either direction | both packets see each other; a packet never sees its own results | 36 |
| three-packet batch | forward one hop, and packet 0 reads packet 2 (the wrap); two packets apart is 0 | 32, 36 |
| one packet later, batch boundary inside a group (one-packet batches) | all four latches, any slot - the M=2/M=3 case of the rotation | 28, 36 |
| across a group flush, group is **one** batch | the group's **last packet's** slot-0 result, to slot 0 only, one group deep | 31, 32, 36 |
| across a group flush, group is 2 or 3 batches | 0, from any batch of the previous group, in the shapes tested | 36 |
| across a group flush, group is 4 batches | 0 in tested cases: alu0 from each packet to next-group slot 0, plus selected alu0/alu1 reader-slot combinations | 40 |
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
2. **The forward is not one-directional in the complete rotations.** Packet 0
   can read the group's last packet (notes 36), but the M=4 wrap depends on
   packet distribution: size-2 3+1 and 1+3 groups have the full wrap for all
   latches; size-2 2+2 has only the slot-3 packet-3-to-packet-0 wrap. A
   scheduler must model the actual packing, not infer every wrap edge from M
   alone.
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
| consumer is packet 0 of a multi-packet group | latch of the group's last packet in the complete tested rotations; at M=4 size-2 2+2, only latch 3 is available on the wrap; size-2 3+1/1+3 retain all latches (notes 38, 39) | the cycle wraps, with a packing-specific M=4 exception |
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
3. **The group-flush carry is a narrow size-1 case.** The established positive
   path is the previous group's last packet slot-0 result read by slot 0 of the
   next group, and it was measured when the group is one batch. Sizes 2 and 3
   have no carry in the tested shapes. At size 4, alu0 writes from all four
   packet positions and selected alu1/reader-slot combinations returned zero;
   this is not a complete all-latch/all-slot test (note 40). Do not schedule
   against a size-4 carry.
4. **Place the payload-producing DW in the group's first batch.** A single
   first-batch DW is the measured arrangement that stores the expected
   group-final row payload. A later-batch-only DW returned zero on RT1 rather
   than leaving its nonzero clear, so do not describe that case as simply
   "no store" (note 42).
5. **Use one DW per group as a conservative output constraint.** The assembler
   allows at most one DW per batch, not one per group. With multiple DWs in a
   group, class-2 pairs and mixed depth/colour pairs do not have one universal
   result: some competing targets retain their baseline, while mixed-class
   tests show the second target receiving zero. Keep the named row pair stable
   through the group boundary, and do not rely on later DWs to produce a
   useful nonzero output (notes 29, 33, 42).
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
| "one DW per batch" (MRT) | the assembler has one DW slot per batch; one DW per group is a conservative compiler policy, not a proven hardware arbitration limit. Multiple DWs can yield baseline-preserving or zero-valued targets depending on class and placement | notes 29, 33, 42 |
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
of current compiler behaviour. M=4 has now been tested in 1+1+1+1, 2+2, 3+1
and 1+3 packings; the remaining hardware gate is to understand why 2+2 loses
three wrap latches and to close the still-ambiguous DW cases. Do not treat the
measured packet/group relation as universal beyond the tested packings.

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
   consumer reads** (`.lh` for fp20/bare readers, `.l*` for fx10 readers). The
   only established carry is the one-batch, previous-last-packet slot-0 to
   next-group slot-0 path. The size-4 follow-up found no carry in the tested
   alu0/alu1 cases, but did not test every latch/reader-slot combination. Do
   not use group carry as a compiler optimisation outside a verifier's exact
   measured case.
4. **Schedule predictable exports with one DW per group.** Put it in the
   group's first batch and keep its named row pair stable through the group
   boundary. Treat multi-DW groups as unresolved: mixed depth/colour tests show
   a zero write to the second target, and the three-DW zero-cleared-target
   readbacks are partly ambiguous. Do not assume a second DW is always dropped
   or that multiple targets receive useful payloads.
5. **Do not model latches as registers.** The register allocator should treat
   `alu0..3` as an intra-packet forwarding path with a lifetime measured in
   packets, never as storage; the measured eviction rules (idle group, second
   packet boundary, own slot) are all silent failures.
6. **Re-pin the manifests after any scheduler change.** Include `pack`,
   `pack2`, `pack3`, `latch-rotate`, `alu-pack4` and its wrap/probe/size4 sets,
   `alu-pack13`, `alu-pack43`, `alu-carry4`, `alu-send-cross`, the DW three-word,
   size-3 and class sets, `send-pack`, `sched-at`, `sched-b2` and `dw-mrt`.

## 7. Open questions that limit the model

* The mechanism behind the rotation. "Packet k reads packet (k-1) mod M" is an
  input/output relation; a rotating register file, a reordered issue and a
  double-buffered write bus all fit it, and nothing measured separates them.
* What fixes M. It equals the group's packet count in the complete measured
  rotations. M=4 is now tested with one packet per batch (size 4) and with
  size-2 splits 3+1 and 1+3, all of which have the complete cycle; the size-2
  2+2 split has the forward edges but only the slot-3 wrap. Why the 2+2
  packing loses the other three wrap latches, and what other packet
  distributions do, remain open.
* Why the group flush carry exists at size 1 and not in the tested size-2,
  size-3 or size-4 combinations. The size-4 negative matrix covers alu0 from
  each packet and selected alu1/reader-slot cases, not all latch/slot pairs.
* Why recv/send is packet-local. The slot-0 sender in packet 0 to slot-1
  receiver in packet 1, its reverse, and an alternate packet-0 slot-3 sender
  were tested without cross-packet transfer; other slot mappings remain open.
* The DW zero-write mechanism. Mixed class-1/class-2 pairs show the second
  target receiving zero, while some class-2 results and the three-word
  RT4/RT5 results cannot distinguish untouched zero from a zero write. Does
  the first DW consume/clear the row payload, or does later DW execution
  independently read a zero source? The assembler also prevents two DWs in
  one batch, so same-packet arbitration is an encoding limitation.
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
| 3 | In complete tested rotations, a read in packet k is served by packet (k-1) mod M. M=4 has full wrap at size 4 one-packet-per-batch and size 2 split 3+1/1+3; size 2 split 2+2 has only the slot-3 wrap. | **Measured**, with the listed packing qualifications | notes 32, 36, 38, 39 |
| 4 | A two-packet batch forwards in both directions, so "packet 0 sees packet 1" is legal. Nothing in a multi-packet batch forwards two packets. | **Measured** | notes 36 |
| 5 | A packet never sees its own results in a multi-packet batch, so its slots are independent and its chain must be one hop per packet. | **Measured** | notes 32, 36 |
| 6 | `.l*` (fx10) and `.lh` (fp20) writes fill different latch views; a bare read sees only an `.lh` write, an `.l` read only a `.l*` one, a `.*h`-only write fills neither, at every measured distance and in both directions. | **Measured** | notes 28, 36 |
| 7 | A `.lh` write is readable bare in the same packet but not by an `.l` reader one packet later; a same-packet `.l` read of an `.lh` write was not measured. | **Measured** / **Open** | notes 28 |
| 8 | The previous group's last packet's slot-0 result is readable by the next group's slot 0, one group deep, not a saved register. | **Measured** when the group is one batch | notes 31, 32, 36 |
| 9 | That group carry is absent at sizes 2/3 in tested shapes, and absent at size 4 for the tested alu0/alu1 writer/reader combinations (not a full latch matrix). | **Measured** (negative, with size-4 scope limited) | notes 36, 40 |
| 10 | Row registers are the only storage that survives a group: a row write is invisible in its own packet and its own group, visible to the next group. | **Measured** | notes 28, 32 |
| 11 | One DW per group, placed in the first batch, is the conservative way to obtain a predictable nonzero group-final payload. Multiple DW results depend on class and placement: mixed depth/colour pairs write zero to the second target; some zero-cleared targets are ambiguous. | **Measured observations; universal arbitration rule open** | notes 29, 33, 42 |
| 12 | `recv`/`send` transfer between slots inside one packet. Packet-0 slot-0 send to packet-1 slot-1 recv, the reverse, and an alternate slot-3 sender showed no cross-packet transfer; other mappings remain open. | **Measured** / **Open** | notes 13, 16, 37, 41 |
| 13 | A `send` on a producer does not disturb the latch path, and the group carry holds the plain slot-0 result. | **Measured** | notes 37 |
| 14 | Schedule bits 29 and 30 change nothing observable, on any of the three batches of the template, in five shapes. | **Measured** | notes 30, 34, 35 |
| 15 | M=4 has a full cycle at size 4 one-packet-per-batch and size 2 split 3+1/1+3; size 2 split 2+2 keeps the forward edges but only latch 3 wraps. The reason is unknown. | **Measured** for these packings | notes 38, 39 |
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
   measured only for single-batch groups; no carry appeared in the tested
   size-2/3 cases or the selected size-4 cases, and no general multi-batch
   carry may be assumed.
5. **"`send`/`recv` is a cross-packet accumulator."** Within a packet yes;
   across a packet boundary no measurement shows a transfer, so a scheduler must
   not use it to carry a dependency between packets.
6. **"Bits 29/30 encode a hazard the hardware uses."** On AR20 they do not, on
   any batch; on T114 nothing is claimed.

### Recommended scheduling, dependency and liveness rules

* Build the dependency graph over packets, not slots. Within a packet, require
  independence, and decide the packet count before choosing a chain shape.
* When a batch holds two or more packets, model only measured packet edges.
  M=2/M=3 use the complete rotation in the tested shapes. At M=4, allow the
  full wrap for one-packet-per-batch, 3+1 and 1+3 packings; for 2+2 allow the
  three forward edges for every latch and the packet-3-to-packet-0 wrap only
  for alu3 written in slot 3. Mark unmeasured/absent edges as requiring a row.
* Treat latch lifetimes in packets, and give a value only one latch hop before
  it must be in a row. Materialise to `.lh` for fp20 readers and `.l*` for fx10
  readers, at every group boundary.
* Keep one DW per group in the first batch as a conservative compiler policy,
  with the named row pair stable through the group boundary. The experiments
  do not establish a universal drop/first-wins mechanism for later DWs; mixed
  depth/colour tests show a zero write to the second target (note 42).
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
| M=4 rotation: 2+2, 1+1+1+1, 3+1 and 1+3 packings | 38, 39 | `_alu_pack4*`, `asm_characterize.alu_pack13.json`, `asm_characterize.alu_pack43.json` | `pin-`/`verify-`/`clean-alu-pack4*`, `-alu-pack13`, `-alu-pack43` |
| `send`/`recv` are packet-local; cross-packet slot-1 receiver probe | 37, 41 | `asm_characterize.alu_send_pack.json`, `asm_characterize.alu_send_cross.json` | `pin-alu-send-pack`, `pin-alu-send-cross`, `clean-alu-send-cross` |

The run directories live under `tests/grate/asm/results/` and each holds the
results rows, the run metadata (device, kernel, tree, source hashes) and, for
the schedule-bit sets, the uploaded `0x801`/`0x806` words per batch.
