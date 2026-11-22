#!/usr/bin/env python3
"""Append README entries for recorded run directories.

Usage: append_runs.py DIR [DIR ...], from the repository root. Each DIR is a
directory under tests/grate/asm/results holding results.tsv and metadata.json
copied from a run. The kind (pin, verify, clean) comes from the name prefix and
appears in the entry, so the index says what the run was for.
"""

import json
import pathlib
import sys

R = pathlib.Path("tests/grate/asm/results")
KINDS = {
    "pin": "unpinned manifest",
    "verify": "pinned manifest",
    "clean": "pinned manifest, clean clone",
}


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    text = (R / "README.md").read_text()
    blocks = []
    for name in sys.argv[1:]:
        directory = R / name
        if f"## {name}" in text:
            raise SystemExit(f"entry for {name} is already present")
        kind = KINDS[name.split("-")[0]]
        meta = json.loads((directory / "metadata.json").read_text())
        rows = sum(1 for _ in (directory / "results.tsv").open()) - 1
        summary = meta["summary"]
        lines = [f"## {name}", ""]
        lines.append("* manifest: `{}` (sha256 {}, {})".format(
            pathlib.Path(meta["manifest"]).name,
            meta["manifest_sha256"][:12], kind))
        lines.append(f"* device: {meta['device_model']}; {meta['uname']}")
        if meta["git_status"]:
            lines.append(f"* tree: {meta['git_commit']} with the set's files "
                         "untracked (see metadata git_status)")
        else:
            lines.append(f"* tree: {meta['git_commit']} with a clean tree")
        lines.append("* created: {}; {} rows, repeat {}, timeout {:.1f} s".format(
            meta["created_utc"], rows, meta["repeat"], meta["timeout_seconds"]))
        lines.append("* summary: {} passed, {} failed of {} - {}".format(
            summary["passed"], summary["failed"], rows,
            "passed" if not summary["failed"] else "failed"))
        if (directory / "schedule-words.tsv").is_file():
            lines.append("* schedule words: `schedule-words.tsv`, the 0x801 and "
                         "0x806 words per batch from each run's raw dump")
        blocks.append("\n".join(lines) + "\n")
    (R / "README.md").write_text(text.rstrip("\n") + "\n\n" + "\n".join(blocks))
    print("appended", ", ".join(sys.argv[1:]))


if __name__ == "__main__":
    main()
