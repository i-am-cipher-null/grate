# Raw characterization results

One directory per hardware run: `results.tsv` (every repeat of every
step, with the exact command and source hashes) and `metadata.json`
(manifest, assembler, kernel, device model, git commit). The per-draw
stdout/stderr logs stay in `characterization/`, which is ignored - the
TSV's `status` column is what a log would show.

The matrix manifests are run per size so that a wedge in one size cannot
invalidate another; `matrix-verify` is the checked-in 210-variant
manifest run end to end.

The per-size manifests are not checked in - generate one with
`make_alu_buffer_matrix.py --sizes N --out /tmp/m_nN.json`; the hash in each
section identifies the exact file that was run.

## count-sweep

* manifest: `asm_characterize.alu_buffer_sweep.json` (sha256 64f6cc8effd9)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 10295478c79bf7ba54187b22b662468c30299130
* created: 2026-11-19T18:52:04+00:00; 34 rows, repeat 1, timeout 8.0 s
* summary: 34 passed, 0 failed of 34

## matrix-n1

* manifest: `m_n1.json` (sha256 77cdcf3ccf96)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 071394eba3b09ffb62bcf54d8debd80ae21074e1
* created: 2026-11-19T19:15:04+00:00; 148 rows, repeat 3, timeout 10.0 s
* summary: 148 passed, 0 failed of 148

## matrix-n2

* manifest: `m_n2.json` (sha256 95cdf7caac6f)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 071394eba3b09ffb62bcf54d8debd80ae21074e1
* created: 2026-11-19T19:18:33+00:00; 148 rows, repeat 3, timeout 10.0 s
* summary: 148 passed, 0 failed of 148

## matrix-n3

* manifest: `m_n3.json` (sha256 f56e3f6a6078)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 071394eba3b09ffb62bcf54d8debd80ae21074e1
* created: 2026-11-19T19:19:01+00:00; 226 rows, repeat 3, timeout 10.0 s
* summary: 226 passed, 0 failed of 226

## matrix-n4

* manifest: `m_n4.json` (sha256 1bf9e341d632)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 071394eba3b09ffb62bcf54d8debd80ae21074e1
* created: 2026-11-19T19:19:34+00:00; 124 rows, repeat 3, timeout 10.0 s
* summary: 124 passed, 0 failed of 124

## matrix-verify

* manifest: `asm_characterize.alu_buffer_matrix.json` (sha256 7429938558d7)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 071394eba3b09ffb62bcf54d8debd80ae21074e1
* created: 2026-11-19T19:21:17+00:00; 634 rows, repeat 3, timeout 10.0 s
* summary: 634 passed, 0 failed of 634

## blocks

* manifest: `blocks.json` (sha256 1455ec66d8b6)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 071394eba3b09ffb62bcf54d8debd80ae21074e1
* created: 2026-11-19T19:19:59+00:00; 40 rows, repeat 3, timeout 10.0 s
* summary: 40 passed, 0 failed of 40

## ladder-n3

* manifest: `lad_n3.json` (sha256 5c18315c5b50)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 071394eba3b09ffb62bcf54d8debd80ae21074e1
* created: 2026-11-19T19:20:21+00:00; 34 rows, repeat 3, timeout 10.0 s
* summary: 34 passed, 0 failed of 34

## ladder-n4

* manifest: `lad_n4.json` (sha256 dc034c3882ea)
* device: Toshiba AC100 / Dynabook AZ; Linux kusma-ac100 7.2.0-gd1efa725955b #1 SMP PREEMPT Sun Aug 30 13:43:06 UTC 2026 armv7l
* commit: 071394eba3b09ffb62bcf54d8debd80ae21074e1
* created: 2026-11-19T19:20:28+00:00; 28 rows, repeat 3, timeout 10.0 s
* summary: 28 passed, 0 failed of 28
