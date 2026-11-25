#!/usr/bin/env python3
"""Parse-check every variant of a note 57 manifest with the tree's parser.

Usage: check_all.py <manifest.json> [...]

Materialises each variant exactly as the harness would, writes it to a
temporary file and runs the tree's fragment assembler/parser on it through
tests/grate/asm/disasm_fs.c. Prints one line per failing variant.
"""

import json
import os
import subprocess
import sys
import tempfile

DISASM = "/tmp/disasm_fs"

failures = 0
checked = 0
for path in sys.argv[1:]:
    manifest = json.load(open(path))
    fs_path = manifest["program"]["fs"]
    template = open(fs_path).read()
    for v in manifest["variants"]:
        text = template
        for key, value in v["replace"]["fs"].items():
            text = text.replace(key, value)
        fd, tmp = tempfile.mkstemp(suffix=".asm")
        os.write(fd, text.encode())
        os.close(fd)
        ret = subprocess.run([DISASM, tmp], capture_output=True, text=True)
        os.unlink(tmp)
        checked += 1
        if ret.returncode != 0:
            failures += 1
            print("FAIL %s %s: %s" % (os.path.basename(path), v["name"],
                                      ret.stderr.strip()))
print("checked %d variants, %d failed" % (checked, failures))
sys.exit(1 if failures else 0)
