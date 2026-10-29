## Running the assembler on hardware

`tests/grate/asm_tests.sh` drives `tools/assembler`, which assembles a test
program and actually runs it on the GPU, then compares the first framebuffer
pixel against `--expected`. This makes the assembler usable as an experiment
platform: change an instruction, observe what the hardware does.

### Non-root access

The assembler opens a DRM node in `src/libhost1x/host1x-drm.c`. It only tried
`/dev/dri/card0` (`root:video 0660`), so it needed root or membership in `video`.
For headless work the render node `/dev/dri/renderD128` (`root:render 0660`) is
sufficient: buffers and submits use GEM ioctls
(`DRM_IOCTL_TEGRA_GEM_CREATE`, `DRM_IOCTL_TEGRA_GEM_MMAP`), which render nodes
allow. `DRM_IOCTL_MODE_CREATE_DUMB` appears only in the L4T/nvhost back end, not
in the DRM one.

Two changes are needed to run without root:

1. Fall back to `/dev/dri/renderD128` when `card0` cannot be opened. Without
   this, Grate falls back to the dummy interface and reports
   "Kernel driver interface undetected".
2. Skip `framebuffer_init` when no display was created. `drm_framebuffer_init()`
   calls `drmModeAddFB2WithModifiers()`/`drmModeAddFB2()`, which are mode-setting
   ioctls and fail with `EACCES` (`-13`) on a render node. A DRM framebuffer is
   only needed for scanout, so registration can be skipped when
   `host1x->display` is NULL - which is what the X11 back end already does by
   setting `framebuffer_init = NULL`.

Without (1) and (2) the run dies with
`host1x_framebuffer_create: framebuffer_init failed: -13`.

### UAPI detection is not reliable

`host1x_drm_open()` sets `drm->new_uapi` from `ver->version_major == 1`. That is
the generic DRM version, and the tegra driver reports `1.0.0` (see the `tegra
1.0.0` line in dmesg and `drm 1.1.0` in `/sys/class/drm/version`), so the test is
true for every tegra DRM release and carries no information about whether the
channel UAPI is implemented. It happens to select the new path on this kernel,
but it would also select it on a kernel that only has the downstream UAPI.

### The new UAPI on an upstream 7.2.0 kernel

`7.2.0-gd1efa725955b` implements the new channel UAPI in full, at the documented
numbers: `CHANNEL_OPEN` 0x10, `CHANNEL_CLOSE` 0x11, `CHANNEL_MAP` 0x12,
`CHANNEL_UNMAP` 0x13, `CHANNEL_SUBMIT` 0x14. `OPEN` returns the expected
`version` (0x20 for both gr2d and gr3d) and `capabilities`.

What breaks submission on this kernel is that Grate's bundled
`src/libhost1x/tegra_drm.h` is an *older revision of that same UAPI*. The ioctl
numbers only encode struct sizes, and map/unmap kept theirs, so nothing fails at
the ioctl-number level - the kernel simply reads the fields at the wrong offsets:

| struct | old (Grate) | current (kernel / Mesa) |
|:--|:--|:--|
| `channel_open` | 5 fields | 6: `context`, `version`, `capabilities`, `padding` |
| `channel_map` | `channel_ctx, flags, handle, mapping_id` | `context, handle, flags, mapping` |
| `channel_unmap` | `channel_ctx, mapping_id` | `context, mapping` |
| `channel_submit` | `syncpt_incr{id, num_incrs, fence_value}` | `syncpt{id, increments, value}` |

The fix is to replace Grate's copy with the current UAPI - Mesa's
`include/drm-uapi/tegra_drm.h` is byte-identical to the installed
`/usr/include/drm/tegra_drm.h` on this kernel - and rename the users. Only
`src/libhost1x/host1x-drm.c` references the old names.

A second, independent bug produced the `EINVAL` that looked like an ABI
mismatch: `drm_bo_map()` never set `args.flags`, so it passed zero, and the
kernel rejects a mapping that requests neither read nor write access. It needs

```c
args.flags = DRM_TEGRA_CHANNEL_MAP_READ_WRITE;
```

A third bug hid all of this: `drm_channel_submit2()` computed an error from the
submit ioctl and then returned 0 unconditionally, so submit failures were
swallowed. It now returns the real error.

With those changes the whole assemble-and-run suite exercises the GPU:

```
$ cd ~/build/grate && sh tests/grate/asm_tests.sh
All tests passed
```

Every run still logs `****nvrm_init failed****` and
`ERROR: host1x_drm_display_init: drm_display_create() failed: -19`. Both are
harmless: the blob compiler is unavailable, and there is no X server, so display
init fails and the assembler continues headless. `tools/assembler` has no
`--nodisplay` option of its own; `--testonly` is enough.

`asm_tests.sh` resolves the assembler as `$DIR/../../tools/assembler` relative
to its own directory, so it must be run from - or copied into - the build tree
where `tools/assembler` exists.

The build tree keeps its **own copy** (`~/build/grate/tests/grate/asm_tests.sh`),
so an edit under `src/grate/tests/` changes nothing until it is copied over. That
is worth remembering because the stale copy cheerfully reports `All tests passed`
for a file that no longer parses, or for a case that was never added: copy first,
then `sh -n` the result. To prove a new case actually executes, break its
`--expected` on purpose - the suite must start failing with `exit=1` - then
restore it. A suite that passes both ways is not running the case.

## Raw instruction-word experiments

`tools/assembler` takes `--result`, which always prints the result word:

```
result 0xFF00FF00
```

Without it you only see the word when `--expected` mismatches, and that path also
dumps the whole framebuffer.

The `DW` unit accepts a raw 32-bit word, so single-bit experiments are a text
edit rather than binary patching. The lexer token is `DW:` **including the
colon** - the colon is part of the mnemonic, so the line is:

```
	DW:	0x00028005
```

Writing `DW 0x...` fails with `fs: line N: syntax error`, because a bare `DW`
is not a token. Results are deterministic: the same shader run three times in a
row returns the same word.
