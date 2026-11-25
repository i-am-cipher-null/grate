#!/usr/bin/env python3
"""Print one variant's fragment source from a note 57 manifest.

Usage: check_asm.py <manifest.json> <variant name>

Applies the manifest's own `replace` map to the template's text, which is the
same substitution the harness performs, and writes the result to stdout so it
can be handed to tests/grate/asm/disasm_fs.c without running the GPU.
"""

import json
import sys

path, want = sys.argv[1], sys.argv[2]
manifest = json.load(open(path))
fs_path = manifest["program"]["fs"]
text = open(fs_path).read()
for v in manifest["variants"]:
    if v["name"] == want:
        for key, value in v["replace"]["fs"].items():
            text = text.replace(key, value)
        sys.stdout.write(text)
        break
else:
    sys.exit("no variant %r in %s" % (want, path))
