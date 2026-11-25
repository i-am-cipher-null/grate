/*
 * Note 57 helper: print the assembled fragment program words in hex, so the
 * exact instruction word for a probe shape can be recorded and two shapes that
 * differ only in the sfu operand field can be compared word by word.
 */

#include <stdio.h>

#include "grate.h"
#include "grate-3d.h"

int main(int argc, char **argv)
{
    int i, w;

    for (i = 1; i < argc; i++) {
        struct grate_shader *fs;

        fs = grate_shader_parse_fragment_asm_from_file(argv[i]);
        if (!fs) {
            fprintf(stderr, "%s: fragment asm parse failed\n", argv[i]);
            return 1;
        }

        printf("== %s (%u words)\n", argv[i], fs->num_words);
        for (w = 0; w < (int)fs->num_words; w++)
            printf("%3d %08x\n", w, fs->words[w]);
    }

    return 0;
}
