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

`clean-*` is the pinned manifest run from a clone that was checked out at the
commit which contains it, with an empty `git_status`, so the claim that the
pinned words reproduce from the committed inputs has a run of its own to point
at. Its `repository_root` is the temporary clone, not the working checkout.

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

* manifest: `asm_characterize.barycentric.json` (sha256 17fb3a6088f9)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 62b8b53dec8fa84c1cc7aa8e862946257b37bd7a with a dirty tree
* created: 2026-11-19T23:07:18+00:00; 133 rows, repeat 3, timeout 10.0 s
* summary: 133 passed, 0 failed of 133 - passed

## verify-barycentric

* manifest: `asm_characterize.barycentric.json` (sha256 cbd45ac88500)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 62b8b53dec8fa84c1cc7aa8e862946257b37bd7a with a dirty tree
* created: 2026-11-19T23:07:35+00:00; 133 rows, repeat 3, timeout 10.0 s
* summary: 133 passed, 0 failed of 133 - passed

## pin-persp

* manifest: `asm_characterize.persp.json` (sha256 ac5628fec50d)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: e37a0b39505915dabc7ebe9b180017172eb513f4 with a dirty tree
* created: 2026-11-19T23:33:02+00:00; 118 rows, repeat 3, timeout 10.0 s
* summary: 118 passed, 0 failed of 118 - passed

## verify-persp

* manifest: `asm_characterize.persp.json` (sha256 a7eec1ce8bed)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: e37a0b39505915dabc7ebe9b180017172eb513f4 with a dirty tree
* created: 2026-11-19T23:34:32+00:00; 118 rows, repeat 3, timeout 10.0 s
* summary: 118 passed, 0 failed of 118 - passed

## pin-mfu-matrix

* manifest: `asm_characterize.mfu_selector_matrix.json` (sha256 101838034d2f)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 4da918dd1c9559500d7c6c97f63d165684e69aef with a dirty tree
* created: 2026-11-20T09:13:35+00:00; 340 rows, repeat 3, timeout 10.0 s
* summary: 340 passed, 0 failed of 340 - passed

## verify-mfu-matrix

* manifest: `asm_characterize.mfu_selector_matrix.json` (sha256 9614da2f6724)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 4da918dd1c9559500d7c6c97f63d165684e69aef with a dirty tree
* created: 2026-11-20T09:14:28+00:00; 340 rows, repeat 3, timeout 10.0 s
* summary: 340 passed, 0 failed of 340 - passed

## clean-mfu-matrix

* manifest: `asm_characterize.mfu_selector_matrix.json` (sha256 9614da2f6724)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: f1c82db7be023940de95842826b87df3ef5bd3fd with a clean tree (clone in /tmp/clean)
* created: 2026-11-20T09:17:48+00:00; 340 rows, repeat 3, timeout 10.0 s
* summary: 340 passed, 0 failed of 340 - passed

## pin-mfu-chain

* manifest: `asm_characterize.mfu_chain.json` (sha256 dd766400f350)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 656556cc8397f3cac7f41885ad09eda320030b37 with a dirty tree
* created: 2026-11-20T12:07:09+00:00; 70 rows, repeat 3, timeout 10.0 s
* summary: 70 passed, 0 failed of 70 - passed

## verify-mfu-chain

* manifest: `asm_characterize.mfu_chain.json` (sha256 dd766400f350)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 656556cc8397f3cac7f41885ad09eda320030b37 with a dirty tree
* created: 2026-11-20T12:07:27+00:00; 70 rows, repeat 3, timeout 10.0 s
* summary: 70 passed, 0 failed of 70 - passed

## clean-mfu-chain

* manifest: `asm_characterize.mfu_chain.json` (sha256 dd766400f350)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 75eea754ca4bbcea83cae28c9a133a3c9a8e0b75 with a clean tree (clone in /tmp/clean2)
* created: 2026-11-20T12:11:06+00:00; 70 rows, repeat 3, timeout 10.0 s
* summary: 70 passed, 0 failed of 70 - passed

## pin-sfu-ops

* manifest: `asm_characterize.sfu_ops.json` (sha256 75d55be7e5bb)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 1592d9412c5294e5b1517fdb2d2db80936364725 with a dirty tree
* created: 2026-11-21T13:52:43+00:00; 367 rows, repeat 3, timeout 10.0 s
* summary: 367 passed, 0 failed of 367 - passed

## verify-sfu-ops

* manifest: `asm_characterize.sfu_ops.json` (sha256 82f33210143a)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 1592d9412c5294e5b1517fdb2d2db80936364725 with a dirty tree
* created: 2026-11-21T13:53:31+00:00; 367 rows, repeat 3, timeout 10.0 s
* summary: 367 passed, 0 failed of 367 - passed

## clean-sfu-ops

* manifest: `asm_characterize.sfu_ops.json` (sha256 82f33210143a)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 7eab753623c0eb8bfdd979a10e953a82939c6866 with a clean tree (clone in /tmp/clean-sfu)
* created: 2026-11-21T13:58:13+00:00; 367 rows, repeat 3, timeout 10.0 s
* summary: 367 passed, 0 failed of 367 - passed

## pin-sfu-pair

* manifest: `asm_characterize.sfu_pair.json` (sha256 040a8ac6b32e)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 9ce6bbf15a82ea68c45ee4a292fc9aa59e430503 with a dirty tree
* created: 2026-11-21T14:03:38+00:00; 154 rows, repeat 3, timeout 10.0 s
* summary: 154 passed, 0 failed of 154 - passed

## verify-sfu-pair

* manifest: `asm_characterize.sfu_pair.json` (sha256 6721298cb630)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 9ce6bbf15a82ea68c45ee4a292fc9aa59e430503 with a dirty tree
* created: 2026-11-21T14:04:05+00:00; 154 rows, repeat 3, timeout 10.0 s
* summary: 154 passed, 0 failed of 154 - passed

## clean-sfu-pair

* manifest: `asm_characterize.sfu_pair.json` (sha256 6721298cb630)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 40be0c8d88a30e2bd64db16994e3487382cd433d with a clean tree (clone in /tmp/clean-pair)
* created: 2026-11-21T14:06:10+00:00; 154 rows, repeat 3, timeout 10.0 s
* summary: 154 passed, 0 failed of 154 - passed

## pin-alu-fwd

* manifest: `asm_characterize.alu_fwd.json` (sha256 c27830fc843f)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 423bb090947e88712a3d769179c8a4a5fcc30934 with a dirty tree
* created: 2026-11-21T14:27:37+00:00; 169 rows, repeat 3, timeout 10.0 s
* summary: 169 passed, 0 failed of 169 - passed

## verify-alu-fwd

* manifest: `asm_characterize.alu_fwd.json` (sha256 d133fa98801e)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 423bb090947e88712a3d769179c8a4a5fcc30934 with a dirty tree
* created: 2026-11-21T14:28:10+00:00; 169 rows, repeat 3, timeout 10.0 s
* summary: 169 passed, 0 failed of 169 - passed

## pin-alu-result

* manifest: `asm_characterize.alu_result_regs.json` (sha256 44b84ef3e4e6)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 423bb090947e88712a3d769179c8a4a5fcc30934 with a dirty tree
* created: 2026-11-21T14:25:53+00:00; 43 rows, repeat 3, timeout 10.0 s
* summary: 43 passed, 0 failed of 43 - passed

## verify-alu-result

* manifest: `asm_characterize.alu_result_regs.json` (sha256 eb0c20007a35)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 423bb090947e88712a3d769179c8a4a5fcc30934 with a dirty tree
* created: 2026-11-21T14:26:57+00:00; 43 rows, repeat 3, timeout 10.0 s
* summary: 43 passed, 0 failed of 43 - passed

## clean-alu-fwd

* manifest: `asm_characterize.alu_fwd.json` (sha256 d133fa98801e)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: a66118f53b5f85e84e3ba8b5c2b1ac1d7853166f with a clean tree (clone in /tmp/clean-alu)
* created: 2026-11-21T14:33:14+00:00; 169 rows, repeat 3, timeout 10.0 s
* summary: 169 passed, 0 failed of 169 - passed

## clean-alu-result

* manifest: `asm_characterize.alu_result_regs.json` (sha256 eb0c20007a35)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: a66118f53b5f85e84e3ba8b5c2b1ac1d7853166f with a clean tree (clone in /tmp/clean-alu)
* created: 2026-11-21T14:33:42+00:00; 43 rows, repeat 3, timeout 10.0 s
* summary: 43 passed, 0 failed of 43 - passed

## pin-tram

* manifest: `asm_characterize.tram.json` (sha256 6db31af89570)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 04a96f4a42fd41a68ef933a25cb6430434422728 with a dirty tree
* created: 2026-11-21T14:55:44+00:00; 124 rows, repeat 3, timeout 10.0 s
* summary: 124 passed, 0 failed of 124 - passed

## verify-tram

* manifest: `asm_characterize.tram.json` (sha256 afd7d3f85936)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 04a96f4a42fd41a68ef933a25cb6430434422728 with a dirty tree
* created: 2026-11-21T14:56:15+00:00; 124 rows, repeat 3, timeout 10.0 s
* summary: 124 passed, 0 failed of 124 - passed

## clean-tram

* manifest: `asm_characterize.tram.json` (sha256 afd7d3f85936)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 7cc75c67d763cbb1b824b15fa10fe4e1d3df3884 with a clean tree (clone in /tmp/clean-tram)
* created: 2026-11-21T14:59:13+00:00; 124 rows, repeat 3, timeout 10.0 s
* summary: 124 passed, 0 failed of 124 - passed

## pin-tex

* manifest: `asm_characterize.tex.json` (sha256 a7651df17488)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 29dea23ef05d9b24b237bb69baf6f52d262dc4df with a dirty tree
* created: 2026-11-21T15:33:38+00:00; 184 rows, repeat 3, timeout 15.0 s
* summary: 184 passed, 0 failed of 184 - passed

## verify-tex

* manifest: `asm_characterize.tex.json` (sha256 f5f4bf270ec7)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 29dea23ef05d9b24b237bb69baf6f52d262dc4df with a dirty tree
* created: 2026-11-21T15:34:29+00:00; 184 rows, repeat 3, timeout 15.0 s
* summary: 184 passed, 0 failed of 184 - passed

## clean-tex

* manifest: `asm_characterize.tex.json` (sha256 f5f4bf270ec7)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: c6a678b87ab7ca4edf3e841e2b8f15763343f70b with a clean tree (clone in /tmp/clean-tex)
* created: 2026-11-21T15:36:16+00:00; 184 rows, repeat 3, timeout 15.0 s
* summary: 184 passed, 0 failed of 184 - passed

## pin-pseq

* manifest: `asm_characterize.pseq.json` (sha256 b729908c2914)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: efb6078e33cb60eeb4a96234a6b9497daad5e44b with a dirty tree
* created: 2026-11-21T15:41:32+00:00; 85 rows, repeat 3, timeout 15.0 s
* summary: 85 passed, 0 failed of 85 - passed

## verify-pseq

* manifest: `asm_characterize.pseq.json` (sha256 2291ba0db017)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: efb6078e33cb60eeb4a96234a6b9497daad5e44b with a dirty tree
* created: 2026-11-21T15:41:45+00:00; 85 rows, repeat 3, timeout 15.0 s
* summary: 85 passed, 0 failed of 85 - passed

## clean-pseq

* manifest: `asm_characterize.pseq.json` (sha256 2291ba0db017)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 6b84375a5202f97ed681387f066ef4b0c809b93a with a clean tree (clone in /tmp/clean-pseq)
* created: 2026-11-21T15:42:57+00:00; 85 rows, repeat 3, timeout 15.0 s
* summary: 85 passed, 0 failed of 85 - passed

## pin-alu-control

* manifest: `asm_characterize.alu_control.json` (sha256 32226d2a8542)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 342e03f7689cd1a35cd7403675c1e69749364db8 with a dirty tree
* created: 2026-11-21T15:48:02+00:00; 94 rows, repeat 3, timeout 15.0 s
* summary: 94 passed, 0 failed of 94 - passed

## verify-alu-control

* manifest: `asm_characterize.alu_control.json` (sha256 45cc01a536b2)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 342e03f7689cd1a35cd7403675c1e69749364db8 with a dirty tree
* created: 2026-11-21T15:48:16+00:00; 94 rows, repeat 3, timeout 15.0 s
* summary: 94 passed, 0 failed of 94 - passed

## clean-alu-control

* manifest: `asm_characterize.alu_control.json` (sha256 45cc01a536b2)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 7bc67636fa124dd01cf49e0a049b8929856c6ba6 with a clean tree (clone in /tmp/clean-ac)
* created: 2026-11-21T15:50:20+00:00; 94 rows, repeat 3, timeout 15.0 s
* summary: 94 passed, 0 failed of 94 - passed

## pin-pseq-addr

* manifest: `asm_characterize.pseq_addr.json` (sha256 098102b69aaf)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 2566b5560e2cc9a0010f46d29b9aa3b7d496c0be with a dirty tree
* created: 2026-11-21T19:51:06+00:00; 58 rows, repeat 3, timeout 20.0 s
* summary: 58 passed, 0 failed of 58 - passed

## verify-pseq-addr

* manifest: `asm_characterize.pseq_addr.json` (sha256 a6eebb68f66d)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 2566b5560e2cc9a0010f46d29b9aa3b7d496c0be with a dirty tree
* created: 2026-11-21T19:51:30+00:00; 58 rows, repeat 3, timeout 20.0 s
* summary: 58 passed, 0 failed of 58 - passed

## clean-pseq-addr

* manifest: `asm_characterize.pseq_addr.json` (sha256 a6eebb68f66d)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 571431c60bd99e6bd4e8874fc39591f8ccea5b91 with a clean tree (clone in /tmp/clean-pseq-addr)
* created: 2026-11-21T19:55:49+00:00; 58 rows, repeat 3, timeout 20.0 s
* summary: 58 passed, 0 failed of 58 - passed

## pin-tex-sampling

* manifest: `asm_characterize.tex_sampling.json` (sha256 199a8ebea802)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 8812fe0b17b863fb53058d144ebd6db86d1d871a with a dirty tree
* created: 2026-11-21T20:32:54+00:00; 232 rows, repeat 3, timeout 20.0 s
* summary: 232 passed, 0 failed of 232 - passed

## verify-tex-sampling

* manifest: `asm_characterize.tex_sampling.json` (sha256 fcbe778dc3b1)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 8812fe0b17b863fb53058d144ebd6db86d1d871a with a dirty tree
* created: 2026-11-21T20:34:07+00:00; 232 rows, repeat 3, timeout 20.0 s
* summary: 232 passed, 0 failed of 232 - passed

## clean-tex-sampling

* manifest: `asm_characterize.tex_sampling.json` (sha256 fcbe778dc3b1)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 3ffcd6b3595c7cd09077c4e5ab9382ee30a29d03 with a clean tree (clone in /tmp/clean-tex-sampling)
* created: 2026-11-21T20:40:34+00:00; 232 rows, repeat 3, timeout 20.0 s
* summary: 232 passed, 0 failed of 232 - passed

## pin-alu-send

* manifest: `asm_characterize.alu_send.json` (sha256 becbc6389b99)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 1d3ecd68ca5e28768eae685ad76d8e17c559fd13 with a dirty tree
* created: 2026-11-21T20:49:44+00:00; 109 rows, repeat 3, timeout 15.0 s
* summary: 109 passed, 0 failed of 109 - passed

## verify-alu-send

* manifest: `asm_characterize.alu_send.json` (sha256 8996a2094b52)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 1d3ecd68ca5e28768eae685ad76d8e17c559fd13 with a dirty tree
* created: 2026-11-21T20:51:01+00:00; 109 rows, repeat 3, timeout 15.0 s
* summary: 109 passed, 0 failed of 109 - passed

## clean-alu-send

* manifest: `asm_characterize.alu_send.json` (sha256 8996a2094b52)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 9e561d77f63c6314ebe25dba9166b786d7237b58 with a clean tree (clone in /tmp/clean-alu-send)
* created: 2026-11-21T20:55:20+00:00; 109 rows, repeat 3, timeout 15.0 s
* summary: 109 passed, 0 failed of 109 - passed

## pin-pseq-split

* manifest: `asm_characterize.pseq_split.json` (sha256 456d65f91cba)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 5c582d36b2d3b6d542d08621e1885ae1970ef263 with a dirty tree
* created: 2026-11-21T21:13:15+00:00; 40 rows, repeat 3, timeout 20.0 s
* summary: 40 passed, 0 failed of 40 - passed

## verify-pseq-split

* manifest: `asm_characterize.pseq_split.json` (sha256 bb4b6b872647)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 5c582d36b2d3b6d542d08621e1885ae1970ef263 with a dirty tree
* created: 2026-11-21T21:14:17+00:00; 40 rows, repeat 3, timeout 20.0 s
* summary: 40 passed, 0 failed of 40 - passed

## clean-pseq-split

* manifest: `asm_characterize.pseq_split.json` (sha256 bb4b6b872647)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 6fc90076bf3207efbe79831ac559638072f1144d with a clean tree (clone in /tmp/clean-pseq-split)
* created: 2026-11-21T21:16:33+00:00; 40 rows, repeat 3, timeout 20.0 s
* summary: 40 passed, 0 failed of 40 - passed

## pin-alu-marks

* manifest: `asm_characterize.alu_marks.json` (sha256 0b688591add0)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 7092803c4782e9dd0a09aa69b3a62f626246ba89 with a dirty tree
* created: 2026-11-21T21:18:54+00:00; 232 rows, repeat 3, timeout 15.0 s
* summary: 232 passed, 0 failed of 232 - passed

## verify-alu-marks

* manifest: `asm_characterize.alu_marks.json` (sha256 f38df6a66d8e)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 7092803c4782e9dd0a09aa69b3a62f626246ba89 with a dirty tree
* created: 2026-11-21T21:21:00+00:00; 232 rows, repeat 3, timeout 15.0 s
* summary: 232 passed, 0 failed of 232 - passed

## clean-alu-marks

* manifest: `asm_characterize.alu_marks.json` (sha256 f38df6a66d8e)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: e7db8d1c8143a09ec3eeed0b3d559fca5332bc1a with a clean tree (clone in /tmp/clean-alu-marks)
* created: 2026-11-21T21:23:37+00:00; 232 rows, repeat 3, timeout 15.0 s
* summary: 232 passed, 0 failed of 232 - passed
