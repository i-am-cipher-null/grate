# Compiler ALU scheduling

This document is the compiler-facing reading of the ALU forwarding and
scheduling behaviour measured on AR20 in notes 28 to 44 of
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
| group | `alu_buffer_size` consecutive batches, flushed together | 1..4 batches measured; tested groups contain up to 5 ALU packets |

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
| read in packet k of a multi-packet group, M=2 or M=3 | packet (k-1) mod M's slot-N result, all four latches, any reader slot, in the tested arrangements | 32, 36 |
| M=4, size 2, split 3+1 or 1+3 | full cycle, all four latches and all packet-3-to-packet-0 reader slots | 39 |
| M=4, size 3, split 2+1+1, 1+2+1 or 1+1+2 | full cycle, all four latches and all packet-3-to-packet-0 reader slots | 43 |
| M=4, size 4, split 1+1+1+1 | full cycle, all four latches and all wrap reader slots | 43 |
| M=4, size 2, split 2+2 | three forward edges for all latches; packet 3 slot 3 feeds packet 0's alu3 wrap, readable from any reader slot | 38, 43 |
| M=5, size 2 or 3, eight tested distributions | full five-packet cycle, all four latches and all packet-4-to-packet-0 reader slots | 44 |
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
   `0x00000000` in a three-packet batch of the same program (`p1-chain`
   versus `p0-chain`, notes 32). Nothing errors; the dependent links read
   zero. In a multi-packet group a packet sees its predecessor, not itself.
2. **The wrap is measured per packing, not implied by M alone.** M=4 has a
   restricted wrap only in the tested size-2 2+2 arrangement: the three
   forward edges work for all latches, but packet 3 slot 3 to packet 0 alu3 is
   the only wrap that survives. All other tested M=4 arrangements - size-2
   3+1 and 1+3, the three size-3 splits, and size-4 1+1+1+1 - have the
   complete four-latch cycle. All eight tested M=5 distributions at sizes 2
   and 3 also have the complete cycle (notes 38, 39, 43, 44). The reason for
   the M=4 2+2 exception is not known.
3. **The group-flush carry is separate from the within-group rotation.** The
   positive path is the previous group's last packet slot-0 result to the
   next group's slot 0, measured when the group is one batch. No carry appeared
   at sizes 2 or 3 in the tested shapes (note 36). At size 4, alu0 writes from
   each packet position and selected alu0/alu1 reader-slot combinations all
   returned zero, but this was not a complete all-latch/all-slot matrix
   (note 40). Do not infer that a group-boundary carry is available merely
   because the packet rotation wraps inside a group.

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
| consumer is packet 0 of a multi-packet group | latch of the group's last packet in a complete tested rotation; M=4 size-2 2+2 permits only the alu3 wrap from packet 3 slot 3; the other tested M=4 packings and all eight tested M=5 distributions have the full four-latch wrap (notes 38, 39, 43, 44) | the cycle wraps, but only where measured |
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

1. **Dependent ALU operations must be in packets that the measured rotation
   pairs.** M=2 and M=3 use the complete packet rotation in the tested shapes.
   M=4 is packing-dependent: size-2 splits 3+1 and 1+3, all three size-3
   splits, and size-4 1+1+1+1 have the full four-latch cycle. Size-2 split
   2+2 has the three forward edges for every latch but only the packet-3-slot-3
   to packet-0-alu3 wrap. M=5 has the full four-latch cycle in all eight tested
   size-2 and size-3 distributions. The five-packet wrap matrix varies reader
   slot independently, while its writer slot is coupled to the latch index.
   A scheduler must know the actual packet distribution before adding a wrap
   dependency. Treat any edge not established for that packing as requiring
   a row-register round trip.
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
of current compiler behaviour. M=4 has now been tested in size-2 2+2, 3+1 and 1+3, all three size-3
four-packet splits, and size-4 1+1+1+1. M=5 has been tested in eight size-2
and size-3 distributions. The remaining forwarding gate is to determine
whether the M=4 2+2 anomaly is specific to four packets or recurs in a
uniform 2+2+2 six-packet group. The DW zero-write cases also remain open. Do
not treat the measured packet/group relation as universal beyond the tested
packings.

1. **Make the packet an explicit unit in the backend's IR.** A batch is a list
   of one to three packets; the encoder sets `count = len(packets)` and
   `addr` = the running packet index. Today's reference path has neither concept,
   which is why it cannot pack even where packing is free.
2. **Encode ALU dependencies as packet edges, not slot edges.** Within a
   packet, allow only independent operations. Choose the packet count and
   distribution before scheduling dependencies: at M=4 the 2+2 split does
   not have the same wrap edges as 3+1 and 1+3. Validate the exact packing
   against the corresponding manifest set, and require a row for any edge
   not measured in that packing.
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
6. **Re-pin the manifests after any scheduler change.** Include `pack`, `pack2`, `pack3`, `latch-rotate`, `alu-pack4` and its
   wrap/probe/size4 sets, `alu-pack13`, `alu-pack43`, the `alu-dist211`,
   `alu-dist121`, `alu-dist112` and `alu-blocks4` sets, all eight `alu-m5-*`
   distributions, `alu-carry4`, `alu-send-cross`, the DW three-word, size-3
   and class sets, `send-pack`, `sched-at`, `sched-b2` and `dw-mrt`.

## 7. Open questions that limit the model

* The mechanism behind the rotation. "Packet k reads packet (k-1) mod M" is an
  input/output relation; a rotating register file, a reordered issue and a
  double-buffered write bus all fit it, and nothing measured separates them.
* What fixes M and the M=4 exception. M=4 is now measured in size-2 2+2,
  3+1 and 1+3, all three size-3 four-packet splits, and size-4 1+1+1+1;
  every arrangement except size-2 2+2 has the complete four-latch cycle.
  Size-2 2+2 keeps the three forward edges but only the slot-3 packet-3 to
  packet-0 alu3 wrap. M=5 is measured in all eight size-2 and size-3
  distributions listed in note 44; all have the complete four-latch cycle.
  The five-packet results rule out simple explanations based on two
  multi-packet batches, no singleton batch, both wrap endpoints being in
  multi-packet batches, and the tested batch-order mirrors. The mechanism
  remains open. The next discriminator is M=6 size-3 2+2+2, compared with
  3+3 and 1+2+3. The size-4 M=5 distribution 1+1+1+2 is also untested.
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
| 3 | Complete tested rotations use packet (k-1) mod M. At M=4, size-2 3+1/1+3, all three size-3 splits and size-4 1+1+1+1 have the full four-latch cycle; size-2 2+2 has only the slot-3 wrap. All eight tested M=5 size-2/size-3 distributions have the full four-latch cycle. | **Measured**, with the listed packing qualifications | notes 32, 36, 38, 39, 43, 44 |
| 4 | A two-packet batch forwards in both directions, so "packet 0 sees packet 1" is legal. Nothing in a multi-packet batch forwards two packets. | **Measured** | notes 36 |
| 5 | A packet never sees its own results in a multi-packet batch, so its slots are independent and its chain must be one hop per packet. | **Measured** | notes 32, 36 |
| 6 | `.l*` (fx10) and `.lh` (fp20) writes fill different latch views; a bare read sees only an `.lh` write, an `.l` read only a `.l*` one, a `.*h`-only write fills neither, at every measured distance and in both directions. | **Measured** | notes 28, 36 |
| 7 | A `.lh` write is readable bare in the same packet but not by an `.l` reader one packet later; a same-packet `.l` read of an `.lh` write was not measured. | **Measured** / **Open** | notes 28 |
| 8 | The previous group's last packet's slot-0 result is readable by the next group's slot 0, one group deep, not a saved register. | **Measured** when the group is one batch | notes 31, 32, 36 |
| 9 | That group carry is absent at sizes 2/3 in tested shapes, and absent at size 4 for the tested alu0/alu1 writer/reader combinations (not a full latch matrix). | **Measured** (negative, with size-4 scope limited) | notes 36, 40 |
| 10 | Row registers are the only storage that survives a group: a row write is invisible in its own packet and its own group, visible to the next group. | **Measured** | notes 28, 32 |
| 11 | One DW per group, placed in the first batch, is the conservative way to obtain a predictable nonzero group-final payload. Multiple DW results depend on class and placement: mixed depth/colour pairs write zero to the second target; some zero-cleared targets are ambiguous. | **Measured observations; universal arbitration rule open** | notes 29, 33, 42 |
| 12 | `recv`/`send` transfer between slots inside one packet. Packet-0 slot-0 send to packet-1 slot-1 recv, the reverse, and an alternate slot-3 sender showed no cross-packet transfer; other mappings remain open. | **Measured** / **Open** | notes 13, 16, 37, 41 |
| 13 | A `send` on a producer does not disturb ordinary latch forwarding; the plain slot-0 group carry was preserved in the tested one-batch group case. | **Measured** | notes 37 |
| 14 | Schedule bits 29 and 30 change nothing observable, on any of the three batches of the template, in five shapes. | **Measured** | notes 30, 34, 35 |
| 15 | M=4 size-2 3+1/1+3, all three size-3 four-packet splits and size-4 1+1+1+1 have the full four-latch cycle; size-2 2+2 keeps the forward edges but only latch 3 wraps. All eight tested M=5 distributions at sizes 2 and 3 have the full four-latch cycle. The M=4 2+2 mechanism is unknown. | **Measured** for these packings and latch coverage | notes 38, 39, 43, 44 |
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
  full four-latch cycle for size-2 3+1/1+3, size-3 2+1+1/1+2+1/1+1+2, and
  size-4 1+1+1+1; for size-2 2+2 allow the three forward edges for every
  latch and the packet-3-to-packet-0 wrap only for alu3 written in slot 3.
  The eight tested M=5 size-2/size-3 distributions have the full cycle, with
  the measured writer-slot/latch pairing noted in note 44. Mark unmeasured
  edges as requiring a row.
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
implementation on its own: the packet-count and rotation rules are measured up
to five packets in the listed packings, but six-packet groups, other
unmeasured distributions and the T114 layout are not.

## 9. Evidence map

| claim | notes | manifest | recorded runs |
|:--|--:|:--|:--|
| intra-packet bypass, own-slot exclusion, format rules | 28 | `asm_characterize.alu_latch.json` | `pin-alu-latch`, `verify-alu-latch`, `clean-alu-latch` |
| group carry is a forward, one group deep, slot 0 | 31 | `asm_characterize.alu_latch_flush.json` | `pin-alu-latch-flush`, `verify-alu-latch-flush`, `clean-alu-latch-flush` |
| count = packets, 12 slots, packed forwarding rules | 32 | `asm_characterize.alu_pack.json` | `pin-alu-pack`, `verify-alu-pack`, `clean-alu-pack` |
| DW output under multi-word groups: class-2 pairs and later class-1/class-2 / three-word probes | 29, 33, 42 | `asm_characterize.dw_two_target.json`, `asm_characterize.dw_mrt.json`, `asm_characterize.dw_three.json`, `asm_characterize.dw_size3.json`, `asm_characterize.dw_class.json`, `asm_characterize.dw_class1p.json` | `pin-dw-two-target`, `pin-dw-mrt`, `pin-dw-three`, `pin-dw-size3`, `pin-dw-class`, `pin-dw-class1p` |
| bits 29/30 inert globally and per batch | 30, 34 | `asm_characterize.alu_sched.json`, `asm_characterize.alu_sched_at.json` | `pin-alu-sched-at`, `verify-alu-sched-at`, `clean-alu-sched-at` |
| bits 29/30 inert on the readback batch | 35 | `asm_characterize.alu_sched_b2.json` | `pin-alu-sched-b2`, `verify-alu-sched-b2`, `clean-alu-sched-b2` |
| two-packet batch forwarding, both directions, carry, formats | 36 | `asm_characterize.alu_pack2.json` | `pin-alu-pack2`, `verify-alu-pack2`, `clean-alu-pack2` |
| three-packet rotation and the wrap | 36 | `asm_characterize.alu_pack3.json` | `pin-alu-pack3`, `verify-alu-pack3`, `clean-alu-pack3` |
| rotation is group-scoped, sizes 1/2/3 | 36 | `asm_characterize.latch_rotate.json` | `pin-latch-rotate`, `verify-latch-rotate`, `clean-latch-rotate` |
| M=4 rotation: 2+2, 1+1+1+1, 3+1 and 1+3 packings | 38, 39 | `asm_characterize.alu_pack4*.json`, `asm_characterize.alu_pack13.json`, `asm_characterize.alu_pack43.json` | `pin-`/`verify-`/`clean-alu-pack4*`, `-alu-pack13`, `-alu-pack43` |
| M=4 size-3 distributions and complete size-4 four-latch reference | 43 | `asm_characterize.alu_dist211.json`, `asm_characterize.alu_dist121.json`, `asm_characterize.alu_dist112.json`, `asm_characterize.alu_blocks4.json` | `pin-`/`verify-`/`clean-alu-dist211`, `-dist121`, `-dist112`, `-blocks4` |
| M=5 forwarding and wrap matrices across eight distributions | 44 | `asm_characterize.alu_m5_*.json` | `pin-`/`verify-`/`clean-alu-m5-*` |
| `send`/`recv` are packet-local; cross-packet slot-1 receiver probe | 37, 41 | `asm_characterize.alu_send_pack.json`, `asm_characterize.alu_send_cross.json` | `pin-alu-send-pack`, `pin-alu-send-cross`, `clean-alu-send-cross` |
| size-4 group-flush carry probes | 40 | `asm_characterize.alu_carry4.json` | `pin-alu-carry4`, `verify-alu-carry4`, `clean-alu-carry4` |
| three-DW, later-position and depth/colour class probes | 42 | `asm_characterize.dw_three.json`, `asm_characterize.dw_size3.json`, `asm_characterize.dw_class.json`, `asm_characterize.dw_class1p.json` | `pin-dw-three`, `pin-dw-size3`, `pin-dw-class`, `pin-dw-class1p` |

The run directories live under `tests/grate/asm/results/` and each holds the
results rows, the run metadata (device, kernel, tree, source hashes) and, for
the schedule-bit sets, the uploaded `0x801`/`0x806` words per batch.
