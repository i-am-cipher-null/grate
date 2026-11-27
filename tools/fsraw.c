/*
 * fsraw: print an assembled fragment program both ways.
 *
 * Usage: fsraw <fragment.asm> [more.asm ...]
 *
 * The raw word dump comes first and is authoritative: the complete host1x
 * command stream with word offsets, then each unit's raw words with its exec
 * batch position.  The symbolic disassembly follows, and is only a reading of
 * those words - the two are printed separately so a probe can record the raw
 * form even when the decoder prints nothing for a field.
 */

#include <stdio.h>
#include <stdlib.h>

#include "grate.h"
#include "grate-3d.h"

int main(int argc, char **argv)
{
	int i;

	if (argc < 2) {
		fprintf(stderr, "usage: %s <fragment.asm> [more.asm ...]\n",
			argv[0]);
		return 1;
	}

	for (i = 1; i < argc; i++) {
		struct grate_shader *fs;
		char *raw;

		fs = grate_shader_parse_fragment_asm_from_file(argv[i]);
		if (!fs) {
			fprintf(stderr, "%s: fragment asm parse failed\n",
				argv[i]);
			return 1;
		}

		raw = grate_shader_words_dump(fs);
		if (!raw)
			return 1;

		printf("== %s\n%s", argv[i], raw);
		printf("-- disassembly (hypothesis)\n%s\n",
		       grate_shader_disasm_fs(fs));

		free(raw);
	}

	return 0;
}
