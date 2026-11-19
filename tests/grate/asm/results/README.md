# Raw characterization results

One directory per hardware run: `results.tsv` (every repeat of every step, with
the exact command and source hashes) and `metadata.json` (manifest, assembler,
kernel, device model, git commit, and a copy of the manifest that was run).
`*/logs/` and `*/generated/` are ignored: the TSV's `status` column is what a
log would show, and the generated `.asm` files can be rebuilt from the manifest.

`pin-*` is the run whose words the checked-in manifest was pinned from;
`verify-*` is the checked-in manifest run end to end afterwards. Neither run is
made against the commit that contains the manifest - the pinning run predates
the pin, and the verification run predates the commit - so the manifest hash in
`metadata.json`, not the commit id, is what identifies the program that ran.

The matrix manifests are also run per size so that a wedge in one size cannot
invalidate another; generate such a manifest with
`make_alu_buffer_matrix.py --sizes N --out /tmp/m_nN.json`.

## count-sweep

* manifest: `asm_characterize.alu_buffer_sweep.json` (sha256 64f6cc8effd9)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 10295478c79bf7ba54187b22b662468c30299130 with a dirty tree
* created: 2026-11-19T18:52:04+00:00; 34 rows, repeat 1, timeout 8.0 s
* summary: 34 passed, 0 failed of 34 - passed

## matrix-n1

* manifest: `m_n1.json` (sha256 77cdcf3ccf96)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 071394eba3b09ffb62bcf54d8debd80ae21074e1 with a dirty tree
* created: 2026-11-19T19:15:04+00:00; 148 rows, repeat 3, timeout 10.0 s
* summary: 148 passed, 0 failed of 148 - passed

## matrix-n2

* manifest: `m_n2.json` (sha256 95cdf7caac6f)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 071394eba3b09ffb62bcf54d8debd80ae21074e1 with a dirty tree
* created: 2026-11-19T19:18:33+00:00; 148 rows, repeat 3, timeout 10.0 s
* summary: 148 passed, 0 failed of 148 - passed

## matrix-n3

* manifest: `m_n3.json` (sha256 f56e3f6a6078)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 071394eba3b09ffb62bcf54d8debd80ae21074e1 with a dirty tree
* created: 2026-11-19T19:19:01+00:00; 226 rows, repeat 3, timeout 10.0 s
* summary: 226 passed, 0 failed of 226 - passed

## matrix-n4

* manifest: `m_n4.json` (sha256 1bf9e341d632)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 071394eba3b09ffb62bcf54d8debd80ae21074e1 with a dirty tree
* created: 2026-11-19T19:19:34+00:00; 124 rows, repeat 3, timeout 10.0 s
* summary: 124 passed, 0 failed of 124 - passed

## blocks

* manifest: `blocks.json` (sha256 1455ec66d8b6)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 071394eba3b09ffb62bcf54d8debd80ae21074e1 with a dirty tree
* created: 2026-11-19T19:19:59+00:00; 40 rows, repeat 3, timeout 10.0 s
* summary: 40 passed, 0 failed of 40 - passed

## ladder-n3

* manifest: `lad_n3.json` (sha256 5c18315c5b50)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 071394eba3b09ffb62bcf54d8debd80ae21074e1 with a dirty tree
* created: 2026-11-19T19:20:21+00:00; 34 rows, repeat 3, timeout 10.0 s
* summary: 34 passed, 0 failed of 34 - passed

## ladder-n4

* manifest: `lad_n4.json` (sha256 dc034c3882ea)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 071394eba3b09ffb62bcf54d8debd80ae21074e1 with a dirty tree
* created: 2026-11-19T19:20:28+00:00; 28 rows, repeat 3, timeout 10.0 s
* summary: 28 passed, 0 failed of 28 - passed

## matrix-verify

* manifest: `asm_characterize.alu_buffer_matrix.json` (sha256 7429938558d7)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 071394eba3b09ffb62bcf54d8debd80ae21074e1 with a dirty tree
* created: 2026-11-19T19:21:17+00:00; 634 rows, repeat 3, timeout 10.0 s
* summary: 634 passed, 0 failed of 634 - passed

## pin-matrix

* manifest: `m_all.json` (sha256 4b8ea809ec01)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 3c36ef062f67a67627b323ea5bfb630762838d77 with a dirty tree
* created: 2026-11-19T20:08:35+00:00; 634 rows, repeat 3, timeout 10.0 s
* summary: 634 passed, 0 failed of 634 - passed

## pin-ladder

* manifest: `ladder_all.json` (sha256 f10c059e6228)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 3c36ef062f67a67627b323ea5bfb630762838d77 with a dirty tree
* created: 2026-11-19T20:10:12+00:00; 106 rows, repeat 3, timeout 10.0 s
* summary: 106 passed, 0 failed of 106 - passed

## pin-xgroup

* manifest: `xg.json` (sha256 6dac55611e1f)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 3c36ef062f67a67627b323ea5bfb630762838d77 with a dirty tree
* created: 2026-11-19T20:10:26+00:00; 88 rows, repeat 3, timeout 10.0 s
* summary: 88 passed, 0 failed of 88 - passed

## pin-arb

* manifest: `arb.json` (sha256 6e84df3f6a00)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 3c36ef062f67a67627b323ea5bfb630762838d77 with a dirty tree
* created: 2026-11-19T20:11:53+00:00; 70 rows, repeat 3, timeout 10.0 s
* summary: 70 passed, 0 failed of 70 - passed

## verify-matrix

* manifest: `asm_characterize.alu_buffer_matrix.json` (sha256 107e918fa1dc)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 3c36ef062f67a67627b323ea5bfb630762838d77 with a dirty tree
* created: 2026-11-19T20:12:26+00:00; 634 rows, repeat 3, timeout 10.0 s
* summary: 634 passed, 0 failed of 634 - passed

## verify-ladder

* manifest: `asm_characterize.alu_buffer_ladder.json` (sha256 d6ebfe554423)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 3c36ef062f67a67627b323ea5bfb630762838d77 with a dirty tree
* created: 2026-11-19T20:13:41+00:00; 106 rows, repeat 3, timeout 10.0 s
* summary: 106 passed, 0 failed of 106 - passed

## verify-xgroup

* manifest: `asm_characterize.alu_buffer_xgroup.json` (sha256 abf223c8526b)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 3c36ef062f67a67627b323ea5bfb630762838d77 with a dirty tree
* created: 2026-11-19T20:13:55+00:00; 88 rows, repeat 3, timeout 10.0 s
* summary: 88 passed, 0 failed of 88 - passed

## verify-arb

* manifest: `asm_characterize.alu_buffer_arb.json` (sha256 ded52c878037)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 3c36ef062f67a67627b323ea5bfb630762838d77 with a dirty tree
* created: 2026-11-19T20:14:08+00:00; 70 rows, repeat 3, timeout 10.0 s
* summary: 70 passed, 0 failed of 70 - passed

## pin-arbpseq

* manifest: `asm_characterize.alu_buffer_arb_pseq.json` (sha256 a9b3832de8a9)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 687cc90b5fbd18542b47477b55aafa97b532feed with a dirty tree
* created: 2026-11-19T20:43:14+00:00; 226 rows, repeat 3, timeout 10.0 s
* summary: 226 passed, 0 failed of 226 - passed

## verify-arbpseq

* manifest: `asm_characterize.alu_buffer_arb_pseq.json` (sha256 6254f2c06cb4)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 687cc90b5fbd18542b47477b55aafa97b532feed with a dirty tree
* created: 2026-11-19T20:43:56+00:00; 226 rows, repeat 3, timeout 10.0 s
* summary: 226 passed, 0 failed of 226 - passed

## pin-dwpos

* manifest: `dwpos.json` (sha256 491942c8573d)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 45fdd777581611b415e19406020ce1c12f477ee3 with a dirty tree
* created: 2026-11-19T20:57:41+00:00; 16 rows, repeat 3, timeout 10.0 s
* summary: 16 passed, 0 failed of 16 - passed

## verify-dwpos

* manifest: `asm_characterize.alu_buffer_arb_dwpos.json` (sha256 1f376dba2082)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 45fdd777581611b415e19406020ce1c12f477ee3 with a dirty tree
* created: 2026-11-19T20:59:12+00:00; 16 rows, repeat 3, timeout 10.0 s
* summary: 16 passed, 0 failed of 16 - passed

## pin-dwpos2

* manifest: `dwpos3.json` (sha256 af7d111bfcb1)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: f0994d5a2ecb8c8ba18d62aa7d85a69317ead886 with a dirty tree
* created: 2026-11-19T21:11:59+00:00; 19 rows, repeat 3, timeout 10.0 s
* summary: 19 passed, 0 failed of 19 - passed

## verify-dwpos2

* manifest: `asm_characterize.alu_buffer_arb_dwpos.json` (sha256 b4d25b83f718)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: f0994d5a2ecb8c8ba18d62aa7d85a69317ead886 with a dirty tree
* created: 2026-11-19T21:12:12+00:00; 19 rows, repeat 3, timeout 10.0 s
* summary: 19 passed, 0 failed of 19 - passed

## pin-mfusrc

* manifest: `asm_characterize.mfu_selector_src.json` (sha256 78d7d7f15d8f)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: f0994d5a2ecb8c8ba18d62aa7d85a69317ead886 with a dirty tree
* created: 2026-11-19T21:20:47+00:00; 76 rows, repeat 3, timeout 10.0 s
* summary: 76 passed, 0 failed of 76 - passed

## verify-mfusrc

* manifest: `asm_characterize.mfu_selector_src.json` (sha256 927ca8ca1133)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: f0994d5a2ecb8c8ba18d62aa7d85a69317ead886 with a dirty tree
* created: 2026-11-19T21:21:21+00:00; 76 rows, repeat 3, timeout 10.0 s
* summary: 76 passed, 0 failed of 76 - passed

## pin-mfu-dst

* manifest: `m_dst.json` (sha256 e036fa3cd4cc)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 550426e24e48260a3e9e644ed7f2fc634d365c52 with a dirty tree
* created: 2026-11-19T21:38:24+00:00; 58 rows, repeat 3, timeout 10.0 s
* summary: 58 passed, 0 failed of 58 - passed

## pin-mfu-field

* manifest: `m_field.json` (sha256 e5031b071ccb)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 550426e24e48260a3e9e644ed7f2fc634d365c52 with a dirty tree
* created: 2026-11-19T21:38:32+00:00; 28 rows, repeat 3, timeout 10.0 s
* summary: 28 passed, 0 failed of 28 - passed

## pin-mfu-sfu

* manifest: `m_sfu.json` (sha256 45e18ab4a23b)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 550426e24e48260a3e9e644ed7f2fc634d365c52 with a dirty tree
* created: 2026-11-19T21:40:33+00:00; 28 rows, repeat 3, timeout 10.0 s
* summary: 28 passed, 0 failed of 28 - passed

## verify2-mfu-dst

* manifest: `asm_characterize.mfu_selector_dst.json` (sha256 e08c1582d9e4)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 550426e24e48260a3e9e644ed7f2fc634d365c52 with a dirty tree
* created: 2026-11-19T21:41:07+00:00; 58 rows, repeat 3, timeout 10.0 s
* summary: 58 passed, 0 failed of 58 - passed

## verify2-mfu-field

* manifest: `asm_characterize.mfu_selector_field.json` (sha256 4fd09a37e561)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 550426e24e48260a3e9e644ed7f2fc634d365c52 with a dirty tree
* created: 2026-11-19T21:41:16+00:00; 28 rows, repeat 3, timeout 10.0 s
* summary: 28 passed, 0 failed of 28 - passed

## verify2-mfu-sfu

* manifest: `asm_characterize.mfu_selector_sfu.json` (sha256 f53789fadf41)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 550426e24e48260a3e9e644ed7f2fc634d365c52 with a dirty tree
* created: 2026-11-19T21:41:21+00:00; 28 rows, repeat 3, timeout 10.0 s
* summary: 28 passed, 0 failed of 28 - passed

## verify2-mfu-src

* manifest: `asm_characterize.mfu_selector_src.json` (sha256 c0bd6a5eae2f)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 550426e24e48260a3e9e644ed7f2fc634d365c52 with a dirty tree
* created: 2026-11-19T21:41:36+00:00; 76 rows, repeat 3, timeout 10.0 s
* summary: 76 passed, 0 failed of 76 - passed

## pin-sfu-operand

* manifest: `asm_characterize.sfu_operand.json` (sha256 5d6d3ba37a62)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 6896330c1f19b22d16a287de3c356f13d7298c11 with a dirty tree
* created: 2026-11-19T22:05:43+00:00; 22 rows, repeat 3, timeout 10.0 s
* summary: 22 passed, 0 failed of 22 - passed

## pin-sfu-input-w

* manifest: `asm_characterize.sfu_input_w.json` (sha256 e9a5d1a55467)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 6896330c1f19b22d16a287de3c356f13d7298c11 with a dirty tree
* created: 2026-11-19T22:08:26+00:00; 40 rows, repeat 3, timeout 10.0 s
* summary: 40 passed, 0 failed of 40 - passed

## verify-sfu-operand

* manifest: `asm_characterize.sfu_operand.json` (sha256 c61cdfc0979d)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 6896330c1f19b22d16a287de3c356f13d7298c11 with a dirty tree
* created: 2026-11-19T22:09:25+00:00; 22 rows, repeat 3, timeout 10.0 s
* summary: 22 passed, 0 failed of 22 - passed

## verify-sfu-input-w

* manifest: `asm_characterize.sfu_input_w.json` (sha256 eda9e278ed84)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 6896330c1f19b22d16a287de3c356f13d7298c11 with a dirty tree
* created: 2026-11-19T22:09:39+00:00; 40 rows, repeat 3, timeout 10.0 s
* summary: 40 passed, 0 failed of 40 - passed

## pin-barycentric

* manifest: `asm_characterize.barycentric.json` (sha256 defbea408899)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: be5547d2805f2209b1a26bf369a0afe0a60a78d8 with a dirty tree
* created: 2026-11-19T22:41:47+00:00; 88 rows, repeat 3, timeout 10.0 s
* summary: 88 passed, 0 failed of 88 - passed

## verify-barycentric

* manifest: `asm_characterize.barycentric.json` (sha256 9351d0f78643)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: be5547d2805f2209b1a26bf369a0afe0a60a78d8 with a dirty tree
* created: 2026-11-19T22:43:46+00:00; 88 rows, repeat 3, timeout 10.0 s
* summary: 88 passed, 0 failed of 88 - passed
