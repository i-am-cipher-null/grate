# Fragment hardware characterization runner

Run the characterization manifest from the repository root. Select the assembler explicitly or through GRATE_ASSEMBLER:

    GRATE_ASSEMBLER=build/tools/assembler \
        python3 tests/grate/asm_characterize.py \
        tests/grate/asm_characterize.example.json

The example compares raw TEX words with the existing explicit-LOD program. It is a hardware test, not a compile-only check: run it on the Tegra 2 system with the display/DRM environment used by the other grate tests.

Each manifest contains:

- program: default vertex, fragment and linker assembly paths.
- controls: known-result runs that execute before and after the sweep. Every control must specify expected; a failed initial control prevents variants from running.
- variants: experimental cases. Each can provide args, environment, expected, and alternate vs, fs or lnk files.
- replace: optional per-stage textual substitutions. Each placeholder must occur exactly once. This is useful for changing one raw instruction word while keeping the rest of the source unchanged.

The runner executes each variant three times by default. Use --repeat to override that count, --control-repeat to repeat controls, and --timeout to set the per-draw timeout. Environment entries set variables for that run; a value of null removes an inherited variable. Existing GRATE_* environment variables and display-session variables are included in metadata.json.

Each run saves stdout and stderr separately under logs/. The results.tsv file records the phase, case, iteration, exit status, result word, expected word, elapsed time, command and source-file SHA-256 hashes. metadata.json records the manifest, source revision, dirty-tree state, device model and kernel information. The default output directory is under characterization/, which is git-ignored.

A result is considered valid only when the process exits successfully and prints exactly one result 0x........ line. Expected values are checked both by the runner and by the assembler's --expected option. A timeout is recorded as a failed run; after a hardware timeout, check the device state before treating subsequent measurements as valid.
