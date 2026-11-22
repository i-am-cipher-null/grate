#!/usr/bin/env python3
"""Extract the uploaded ALU schedule words from a run's raw dumps.

Usage: dump_sched_words.py RUN_DIR [OUT_TSV]

RUN_DIR is a run directory written by asm_characterize.py (results.tsv plus
logs/). Every log that contains a raw fragment-program dump contributes one row
per 0x801 and 0x806 upload word, in upload order, so a per-batch mask can be
read back from the run that produced it:

    run  phase  name  index  batch  sched  complement

`batch` is the position of the word inside its stream and `index` is the raster
index within the log. Words are printed as the assembler uploaded them, so a
mask that never reached the engine shows up as an unchanged word rather than as
a missing effect.

The dump format is the one GRATE_DUMP_RAW prints: "Fragment program raw" ...
"Linker program raw", one `[index] = 0xword,` per word. An upload word is
`opcode 2` in bits 28..31, the register offset in bits 16..27 and the number of
following data words in bits 0..15, so the words are walked by count: a data
word can have a 2 in its top nibble (a schedule word with bit 29 set does), and
only the count makes the boundary unambiguous.
"""

import pathlib
import re
import sys
import csv

WORD_RE = re.compile(r"\[(\d+)\] = 0x([0-9A-Fa-f]+),")
UPLOAD = 2
REG_ALU_SCHED = 0x801
REG_ALU_COMPLEMENT = 0x806

FIELDS = ("run", "phase", "name", "index", "batch", "sched", "complement")


def dump_words(text):
    """Return (sched, complement) word lists from one log's fragment dump."""
    try:
        start = text.index("Fragment program raw")
        end = text.index("Linker program raw", start)
    except ValueError:
        return None
    words = [int(m.group(2), 16)
             for m in WORD_RE.finditer(text[start:end])]
    # The count in a header's low half walks the stream correctly for the
    # program arrays (0x801, 0x804, 0x806, 0x901) but not for the leading
    # vertex streams, so locate the two headers of interest directly and take
    # the words that follow them. A data word only reaches the same pattern if
    # its top nibble is 2, which happens for a schedule word with bit 29 set,
    # so the two headers are asserted to be unique.
    found = {REG_ALU_SCHED: [], REG_ALU_COMPLEMENT: []}
    for index, word in enumerate(words):
        if (word >> 28) != UPLOAD:
            continue
        reg = (word >> 16) & 0xFFF
        if reg not in found:
            continue
        if found[reg]:
            raise ValueError(f"more than one 0x{reg:03x} upload in the dump")
        found[reg] = words[index + 1:index + 1 + (word & 0xFFFF)]
    return found[REG_ALU_SCHED], found[REG_ALU_COMPLEMENT]


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    run = pathlib.Path(sys.argv[1])
    rows = []
    with (run / "results.tsv").open() as stream:
        entries = list(csv.DictReader(stream, delimiter="\t"))
    for entry in entries:
        stem = f"{entry['phase']}-{entry['name']}-{int(entry['iteration']):03d}"
        log = run / "logs" / f"{stem}.stderr"
        if not log.is_file():
            continue
        parsed = dump_words(log.read_text(errors="replace"))
        if parsed is None:
            continue
        sched, complement = parsed
        for index in range(max(len(sched), len(complement))):
            rows.append({
                "run": entry["iteration"],
                "phase": entry["phase"],
                "name": entry["name"],
                "index": index,
                "batch": index,
                "sched": "0x{:08X}".format(sched[index])
                         if index < len(sched) else "",
                "complement": "0x{:08X}".format(complement[index])
                              if index < len(complement) else "",
            })
    if not rows:
        raise SystemExit("no dumps found in the run directory")
    out = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else run / "schedule-words.tsv"
    with out.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    lengths = {r["name"]: 0 for r in rows}
    for row in rows:
        lengths[row["name"]] = max(lengths[row["name"]], int(row["batch"]) + 1)
    distinct = sorted(set(lengths.values()))
    print(f"{out}: {len(rows)} rows from {len(lengths)} variants; "
          f"{distinct} schedule/complement words per run")


if __name__ == "__main__":
    main()
