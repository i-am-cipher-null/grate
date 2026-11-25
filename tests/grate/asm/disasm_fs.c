/*
 * Note 56 cross-check helper: parse a fragment asm file and print Grate's own
 * disassembly of the assembled words, so the encoding used for the selector
 * screen is confirmed by an independent decoder rather than by the cgc blob (or
 * by reading the struct fields alone).
 */

#include <stdio.h>

#include "grate.h"

int main(int argc, char **argv)
{
    int i;

    for (i = 1; i < argc; i++) {
        struct grate_shader *fs;

        fs = grate_shader_parse_fragment_asm_from_file(argv[i]);
        if (!fs) {
            fprintf(stderr, "%s: fragment asm parse failed\n", argv[i]);
            return 1;
        }

        printf("== %s\n%s\n", argv[i], grate_shader_disasm_fs(fs));
    }

    return 0;
}
