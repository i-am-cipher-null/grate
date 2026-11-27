/*
 * Encoding round-trip and field-coverage test for the fragment assembler.
 *
 * This test does not use the GPU and does not go through the disassembler: it
 * assembles sources and compares the assembled word streams.  A symbolic form
 * that produces a different word stream from its raw equivalent is a tooling
 * bug, not a hardware result.
 *
 * Checks:
 *   1. a structured MFU instruction and the raw hex halves of its word assemble
 *      to identical streams;
 *   2. every `src` and `dst` selector the fields can hold encodes distinctly,
 *      and a selector the field cannot hold is rejected instead of being
 *      silently truncated;
 *   3. the 6-bit SFU register field covers 0..63 distinctly and rejects 64;
 *   4. `pseq_to_dw_exec_nb` above its 7-bit field and `alu_buffer_size` outside
 *      1..4 are rejected;
 *   5. the raw word dump contains every word of the stream, so a probe can
 *      record raw evidence rather than a disassembly.
 *
 * Usage: tooling_test [work-dir]   (default /tmp)
 */

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "grate.h"
#include "grate-3d.h"

static const char *work_dir = "/tmp";
static int failures;
static int checks;

static void check(int cond, const char *fmt, ...)
{
	va_list ap;

	checks++;
	if (cond)
		return;

	failures++;
	fprintf(stderr, "FAIL: ");
	va_start(ap, fmt);
	vfprintf(stderr, fmt, ap);
	va_end(ap);
	fprintf(stderr, "\n");
}

struct encoded {
	uint32_t *words;
	unsigned nb;
	int ok;
};

/*
 * One exec batch: PSEQ, MFU, TEX, ALU and DW are all required, and an ALU
 * destination needs an explicit sub-register suffix.
 */
static void write_prog(const char *path, const char *directives,
		       const char *mfu, const char *alu)
{
	FILE *fp = fopen(path, "w");

	if (!fp)
		return;

	fprintf(fp, "%s\n.asm\nEXEC\n", directives ? directives : "");
	fprintf(fp, "\tPSEQ:\tNOP\n");
	fprintf(fp, "\tMFU:\t%s\n", mfu ? mfu : "NOP");
	fprintf(fp, "\tTEX:\tNOP\n");
	if (alu)
		fprintf(fp, "\tALU:\n\t\tALU0:\t%s\n", alu);
	fprintf(fp, "\tALU_COMPLEMENT:\t0xFFFFFFFC\n");
	fprintf(fp, "\tDW:\tNOP\n;\n");
	fclose(fp);
}

static struct encoded encode(const char *name, const char *mfu,
			     const char *alu)
{
	struct encoded e = { 0 };
	char path[512];
	struct grate_shader *fs;

	snprintf(path, sizeof(path), "%s/tooling-%s.in", work_dir, name);
	write_prog(path, NULL, mfu, alu);

	fs = grate_shader_parse_fragment_asm_from_file(path);
	if (!fs) {
		remove(path);
		return e;
	}

	e.ok = 1;
	e.nb = fs->num_words;
	e.words = malloc(e.nb * sizeof(*e.words));
	if (!e.words)
		e.ok = 0;
	else
		memcpy(e.words, fs->words, e.nb * sizeof(*e.words));

	remove(path);
	return e;
}

/* Assemble a whole file; 1 on success. */
static int assembles(const char *name, const char *directives,
		     const char *mfu)
{
	char path[512];
	struct grate_shader *fs;

	snprintf(path, sizeof(path), "%s/tooling-%s.in", work_dir, name);
	write_prog(path, directives, mfu, NULL);
	fs = grate_shader_parse_fragment_asm_from_file(path);
	remove(path);

	return fs != NULL;
}

static int same_stream(const struct encoded *a, const struct encoded *b)
{
	unsigned i;

	if (!a->ok || !b->ok || a->nb != b->nb)
		return 0;

	for (i = 0; i < a->nb; i++)
		if (a->words[i] != b->words[i])
			return 0;

	return 1;
}

static void free_encoded(struct encoded *e)
{
	free(e->words);
	memset(e, 0, sizeof(*e));
}

/* Word index of the first 0x604 NONINCR data word, or 0. */
static unsigned mfu_data_word(const struct encoded *e)
{
	unsigned w;

	for (w = 0; e->ok && w + 2 < e->nb; w++)
		if (e->words[w] == 0x26040002)
			return w + 1;

	return 0;
}

int main(int argc, char **argv)
{
	struct encoded structured = { 0 }, raw = { 0 }, prev = { 0 }, cur = { 0 };
	unsigned i;

	if (argc > 1)
		work_dir = argv[1];

	/* 1. structured MFU versus the raw hex halves of the same word. */
	structured = encode("mfu-structured",
			    "sfu: rsq r5 mul0: r1, sfu, #1 mul1: r2, src3, bar0",
			    NULL);
	check(structured.ok, "structured MFU did not assemble");

	{
		unsigned at = mfu_data_word(&structured);
		char mfu[128];

		check(at != 0, "no 0x604 MFU packet in the structured stream");
		/*
		 * The raw form takes the two halves in the order the emitter
		 * writes them: part1 (high) first, then part0.
		 */
		snprintf(mfu, sizeof(mfu), "0x%08X, 0x%08X",
			 at ? structured.words[at] : 0,
			 at ? structured.words[at + 1] : 0);
		raw = encode("mfu-raw", mfu, NULL);
		check(same_stream(&structured, &raw),
		      "structured and raw MFU do not assemble to the same stream");
		free_encoded(&raw);
	}
	free_encoded(&structured);

	/* 2. every src and dst selector the fields hold encodes distinctly. */
	prev.ok = 0;
	for (i = 0; i < 16; i++) {
		char mfu[64];

		snprintf(mfu, sizeof(mfu), "mul0: r0, src%u, src%u", i, i);
		cur = encode("src", mfu, NULL);
		check(cur.ok, "mul0 src%u did not assemble", i);
		if (prev.ok && cur.ok)
			check(!same_stream(&prev, &cur),
			      "mul0 src%u aliases src%u", i, i - 1);
		free_encoded(&prev);
		prev = cur;
	}
	free_encoded(&prev);

	prev.ok = 0;
	for (i = 4; i < 8; i++) {
		char mfu[64];

		snprintf(mfu, sizeof(mfu), "mul0: dst%u, r0, r0", i);
		cur = encode("dst", mfu, NULL);
		check(cur.ok, "mul0 dst%u did not assemble", i);
		if (prev.ok && cur.ok)
			check(!same_stream(&prev, &cur),
			      "mul0 dst%u aliases dst%u", i, i - 1);
		free_encoded(&prev);
		prev = cur;
	}
	free_encoded(&prev);

	cur = encode("src16", "mul0: r0, src16, r0", NULL);
	check(!cur.ok, "mul0 src16 was accepted; the 4-bit field aliases src0");
	free_encoded(&cur);

	cur = encode("dst8", "mul0: dst8, r0, r0", NULL);
	check(!cur.ok, "mul0 dst8 was accepted; the 3-bit field aliases dst0");
	free_encoded(&cur);

	/* 3. the 6-bit SFU register field. */
	prev.ok = 0;
	for (i = 0; i < 64; i++) {
		char mfu[64];

		snprintf(mfu, sizeof(mfu), "sfu: lg2 r%u", i);
		cur = encode("sfu-reg", mfu, NULL);
		check(cur.ok, "sfu: lg2 r%u did not assemble", i);
		if (prev.ok && cur.ok)
			check(!same_stream(&prev, &cur),
			      "sfu register r%u aliases r%u", i, i - 1);
		free_encoded(&prev);
		prev = cur;
	}
	free_encoded(&prev);

	cur = encode("sfu-reg64", "sfu: lg2 r64", NULL);
	check(!cur.ok, "sfu: lg2 r64 was accepted; the 6-bit field aliases r0");
	free_encoded(&cur);

	/* 4. program-wide counters. */
	check(!assembles("exec-nb", "pseq_to_dw_exec_nb = 200", "NOP"),
	      "pseq_to_dw_exec_nb = 200 was accepted; the field is 7 bits");
	check(!assembles("buf-size", "alu_buffer_size = 5", "NOP"),
	      "alu_buffer_size = 5 was accepted");
	check(!assembles("buf-size0", "alu_buffer_size = 0", "NOP"),
	      "alu_buffer_size = 0 was accepted; it becomes 4 after the -1");

	/* 5. the raw dump contains every word of the stream. */
	{
		char path[512];
		struct grate_shader *fs;
		char *dump;

		snprintf(path, sizeof(path), "%s/tooling-dump.in", work_dir);
		write_prog(path, NULL, "sfu: rsq r5 mul0: r1, sfu, #1",
			   "MAD r0.*, r1, r2, r3");
		fs = grate_shader_parse_fragment_asm_from_file(path);
		check(fs != NULL, "dump program did not assemble");
		if (fs) {
			dump = grate_shader_words_dump(fs);
			check(dump != NULL,
			      "grate_shader_words_dump returned NULL");
			if (dump) {
				for (i = 0; i < fs->num_words; i++) {
					char hex[16];

					snprintf(hex, sizeof(hex), "0x%08X",
						 fs->words[i]);
					check(strstr(dump, hex) != NULL,
					      "dump is missing word %u (%s)",
					      i, hex);
				}
				check(strstr(dump, "units ") != NULL,
				      "dump has no unit summary");
				check(strstr(dump, "exec 0:") != NULL,
				      "dump has no exec batch");
				free(dump);
			}

			/*
			 * Each "raw" line of the disassembly must be a run of words that
			 * occurs in the stream in that order, so the reading and the
			 * evidence cannot drift apart.
			 */
			{
				const char *dis = grate_shader_disasm_fs(fs);
				const char *p = dis ? strstr(dis, "; raw 0x") : NULL;
				int lines = 0;

				while (p) {
					uint32_t want[8];
					unsigned n = 0, w;
					int found = 0;

					const char *q = p + 5; /* "; raw" */

					for (;;) {
						unsigned v;
						int adv = 0;

						if (n == 8)
							break;
						if (sscanf(q, " 0x%8X%n", &v, &adv) != 1)
							break;
						want[n++] = v;
						q += adv;
					}

					for (w = 0; n && w + n <= fs->num_words; w++) {
						unsigned k;

						for (k = 0; k < n; k++)
							if (fs->words[w + k] != want[k])
								break;
						if (k == n) {
							found = 1;
							break;
						}
					}

					check(found, "raw line %d is not a run of stream words",
					      lines);
					lines++;
					p = strstr(q, "; raw 0x");
				}

				check(lines > 0, "disassembly has no raw word lines");
			}
		}
		remove(path);
	}

	printf("%d checks, %d failures\n", checks, failures);
	return failures ? 1 : 0;
}
