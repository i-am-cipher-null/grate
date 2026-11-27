/*
 * Raw-first word dump for an assembled fragment shader.
 *
 * The assembler, the linker and the disassembler are research tools, so the
 * instruction words are the evidence and the symbolic form is a hypothesis.
 * This dump prints the complete command stream, then, for every exec batch, the
 * raw words of each unit together with the word offsets they occupy in the
 * stream, so a probe can record what the hardware was given without relying on
 * any decoder's reading of a field.
 *
 * Nothing here interprets a mnemonic.  Unknown and reserved bits are printed as
 * numbers, never as zero and never as "reserved": the MFU padding nibble and the
 * ALU sub-register and fixed-point bits are all shown by value.
 *
 * The symbolic disassembly stays in grate_shader_disasm_fs(); this function is
 * only the raw side, so the two can be compared line by line.
 */

#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "grate-3d.h"
#include "fragment_asm.h"

#define MAX_UNITS 64

struct fs_units {
	uint32_t pseq[MAX_UNITS][1];
	int pseq_word[MAX_UNITS];
	unsigned pseq_nb;

	uint32_t mfu[MAX_UNITS][2];
	int mfu_word[MAX_UNITS];
	unsigned mfu_nb;

	uint32_t mfu_sched[MAX_UNITS];
	int mfu_sched_word[MAX_UNITS];
	unsigned mfu_sched_nb;

	uint32_t tex[MAX_UNITS];
	int tex_word[MAX_UNITS];
	unsigned tex_nb;

	uint32_t alu[MAX_UNITS][8];
	int alu_word[MAX_UNITS];
	unsigned alu_nb;

	uint32_t alu_sched[MAX_UNITS];
	int alu_sched_word[MAX_UNITS];
	unsigned alu_sched_nb;

	uint32_t complement[MAX_UNITS];
	int complement_word[MAX_UNITS];
	unsigned complement_nb;

	uint32_t dw[MAX_UNITS];
	int dw_word[MAX_UNITS];
	unsigned dw_nb;

	unsigned config_nb;
	uint32_t config_addr[MAX_UNITS];
	uint32_t config_word[MAX_UNITS];
	int config_off[MAX_UNITS];
};

struct out {
	char *buf;
	size_t len;
	size_t cap;
};

static void put(struct out *o, const char *fmt, ...)
{
	va_list ap;
	int need;

	if (o->len + 256 > o->cap) {
		size_t cap = o->cap ? o->cap * 2 : 4096;
		char *buf;

		while (cap < o->len + 256)
			cap *= 2;
		buf = realloc(o->buf, cap);
		if (!buf)
			return;
		o->buf = buf;
		o->cap = cap;
	}

	va_start(ap, fmt);
	need = vsnprintf(o->buf + o->len, o->cap - o->len, fmt, ap);
	va_end(ap);

	if (need > 0)
		o->len += need;
}

/*
 * Walk the host1x command stream.  The emitter writes each unit as a NONINCR
 * packet at a fixed register offset, but a hand-written or captured stream may
 * order them differently, so the addresses are read rather than assumed.
 */
static void collect(struct grate_shader *shader, struct fs_units *u)
{
	unsigned i;

	memset(u, 0, sizeof(*u));

	for (i = 0; i < shader->num_words; i++) {
		uint32_t cmd = shader->words[i];
		unsigned opcode = cmd >> 28;
		unsigned offset = (cmd >> 16) & 0xfff;
		unsigned count = cmd & 0xffff;
		unsigned k;

		if (opcode == 2 /* NONINCR */ && count) {
			for (k = 0; k < count; k++) {
				uint32_t w = shader->words[i + 1 + k];

				switch (offset) {
				case 0x541:
					if (u->pseq_nb < MAX_UNITS) {
						u->pseq_word[u->pseq_nb] = i + 1 + k;
						u->pseq[u->pseq_nb++][0] = w;
					}
					break;
				case 0x604:
					/* pairs: high half first, then low half */
					if ((k & 1) == 0 && u->mfu_nb < MAX_UNITS) {
						u->mfu_word[u->mfu_nb] = i + 1 + k;
						u->mfu[u->mfu_nb][1] = w;
					} else if (u->mfu_nb < MAX_UNITS) {
						u->mfu[u->mfu_nb][0] = w;
						u->mfu_nb++;
					}
					break;
				case 0x601:
					if (u->mfu_sched_nb < MAX_UNITS) {
						u->mfu_sched_word[u->mfu_sched_nb] = i + 1 + k;
						u->mfu_sched[u->mfu_sched_nb++] = w;
					}
					break;
				case 0x701:
					if (u->tex_nb < MAX_UNITS) {
						u->tex_word[u->tex_nb] = i + 1 + k;
						u->tex[u->tex_nb++] = w;
					}
					break;
				case 0x801:
					if (u->alu_sched_nb < MAX_UNITS) {
						u->alu_sched_word[u->alu_sched_nb] = i + 1 + k;
						u->alu_sched[u->alu_sched_nb++] = w;
					}
					break;
				case 0x804:
					/* eight words per packet; the emitter pairs them
					 * as 1,0 3,2 5,4 7,6 so the raw order is kept */
					if ((k % 8) == 0 && u->alu_nb < MAX_UNITS) {
						u->alu_word[u->alu_nb] = i + 1 + k;
						u->alu_nb++;
					}
					if (u->alu_nb)
						u->alu[u->alu_nb - 1][k % 8] = w;
					break;
				case 0x806:
					if (u->complement_nb < MAX_UNITS) {
						u->complement_word[u->complement_nb] = i + 1 + k;
						u->complement[u->complement_nb++] = w;
					}
					break;
				case 0x901:
					if (u->dw_nb < MAX_UNITS) {
						u->dw_word[u->dw_nb] = i + 1 + k;
						u->dw[u->dw_nb++] = w;
					}
					break;
				default:
					if (u->config_nb < MAX_UNITS) {
						u->config_off[u->config_nb] = offset;
						u->config_word[u->config_nb] = w;
						u->config_addr[u->config_nb++] = i + 1 + k;
					}
					break;
				}
			}
		}

		i += count;
	}
}

static void dump_mfu(struct out *o, const uint32_t *w)
{
	mfu_instr m;

	m.part0 = w[0];
	m.part1 = w[1];

	put(o, "    fields: var0 sat=%u op=%u src=%u | var1 sat=%u op=%u src=%u\n",
	    m.var0_saturate, m.var0_opcode, m.var0_source,
	    m.var1_saturate, m.var1_opcode, m.var1_source);
	put(o, "            var2 sat=%u op=%u src=%u | var3 sat=%u op=%u src=%u\n",
	    m.var2_saturate, m.var2_opcode, m.var2_source,
	    m.var3_saturate, m.var3_opcode, m.var3_source);
	put(o, "            pad=0x%X | mul0 dst=%u src0=%u src1=%u"
	       " | mul1 dst=%u src0=%u src1=%u\n",
	    m.__pad, m.mul0_dst, m.mul0_src0, m.mul0_src1,
	    m.mul1_dst, m.mul1_src0, m.mul1_src1);
	put(o, "            opcode=%u (0x%X) reg=%u\n",
	    m.opcode, m.opcode, m.reg);
}

/*
 * The ALU packet is eight 32-bit words.  The emitter stores them in a union of
 * fields, but a raw word pair may hold a combination the field union does not
 * name, so the field decode is printed next to the words rather than instead of
 * them.  imm2's layout differs from imm0/imm1 and part6/part7 may be swapped by
 * an "alu3 immediate" form, so no immediate is decoded here: the eight words are
 * the statement.
 */
static void dump_alu(struct out *o, const uint32_t *w)
{
	alu_instr p;
	unsigned k;

	/* part0..part7 overlay the four 64-bit instructions a[0..3] */
	p.part0 = w[0]; p.part1 = w[1];
	p.part2 = w[2]; p.part3 = w[3];
	p.part4 = w[4]; p.part5 = w[5];
	p.part6 = w[6]; p.part7 = w[7];

	for (k = 0; k < 4; k++) {
		const union fragment_alu_instruction *a = &p.a[k];

		put(o, "    a[%u]: regs rD=%u rC=%u rB=%u rA=%u"
		       " | fixed10=%u%u%u%u | sub_hi=%u%u%u%u"
		       " | minus1=%u%u%u%u\n",
		    k, a->rD_reg_select, a->rC_reg_select,
		    a->rB_reg_select, a->rA_reg_select,
		    a->rD_fixed10, a->rC_fixed10, a->rB_fixed10, a->rA_fixed10,
		    a->rD_sub_reg_select_high, a->rC_sub_reg_select_high,
		    a->rB_sub_reg_select_high, a->rA_sub_reg_select_high,
		    a->rD_minus_one, a->rC_minus_one,
		    a->rB_minus_one, a->rA_minus_one);
		put(o, "          abs=%u%u%u%u | neg=%u%u%u | x2=%u%u%u"
		       " | dst=%u wl=%u wh=%u\n",
		    a->rD_absolute_value, a->rC_absolute_value,
		    a->rB_absolute_value, a->rA_absolute_value,
		    a->rC_negate, a->rB_negate, a->rA_negate,
		    a->rC_scale_by_two, a->rB_scale_by_two,
		    a->rA_scale_by_two,
		    a->dst_reg, a->write_low_sub_reg, a->write_high_sub_reg);
		put(o, "          opcode=%u add_dis=%u accum=%u%u cc=%u sat=%u"
		       " scale=%u\n",
		    a->opcode, a->addition_disable,
		    a->accumulate_result_this, a->accumulate_result_other,
		    a->condition_code, a->saturate_result, a->scale_result);
	}
}

char *grate_shader_words_dump(struct grate_shader *shader)
{
	static struct fs_units u;
	struct out o = { 0 };
	unsigned i, b, nb;

	if (!shader || !shader->words)
		return NULL;

	collect(shader, &u);

	put(&o, "words %u\n", shader->num_words);
	for (i = 0; i < shader->num_words; i++) {
		uint32_t cmd = shader->words[i];
		unsigned opcode = cmd >> 28;
		unsigned offset = (cmd >> 16) & 0xfff;
		unsigned count = cmd & 0xffff;
		unsigned k;

		put(&o, "  [%3u] 0x%08X", i, cmd);

		if (opcode == 2)
			put(&o, "  NONINCR 0x%03X count %u\n", offset, count);
		else if (opcode == 1)
			put(&o, "  INCR 0x%03X count %u\n", offset, count);
		else if (opcode == 0)
			put(&o, "  SETCL\n");
		else
			put(&o, "  data\n");

		/* the payload of a packet is not a command: label it as data */
		if (opcode == 1 || opcode == 2) {
			for (k = 0; k < count && i + 1 + k < shader->num_words; k++)
				put(&o, "  [%3u] 0x%08X  data\n", i + 1 + k,
				    shader->words[i + 1 + k]);
			i += count;
		}
	}

	nb = u.pseq_nb;
	if (u.mfu_sched_nb > nb) nb = u.mfu_sched_nb;
	if (u.alu_sched_nb > nb) nb = u.alu_sched_nb;
	if (u.tex_nb > nb) nb = u.tex_nb;
	if (u.dw_nb > nb) nb = u.dw_nb;
	if (u.complement_nb > nb) nb = u.complement_nb;

	put(&o, "units pseq=%u mfu=%u mfu_sched=%u tex=%u alu=%u alu_sched=%u"
	       " complement=%u dw=%u config=%u\n",
	    u.pseq_nb, u.mfu_nb, u.mfu_sched_nb, u.tex_nb, u.alu_nb,
	    u.alu_sched_nb, u.complement_nb, u.dw_nb, u.config_nb);

	for (b = 0; b < nb; b++) {
		put(&o, "exec %u:\n", b);

		if (b < u.pseq_nb)
			put(&o, "  pseq       [%d] 0x%08X\n",
			    u.pseq_word[b], u.pseq[b][0]);

		if (b < u.mfu_sched_nb)
			put(&o, "  mfu_sched  [%d] 0x%08X\n",
			    u.mfu_sched_word[b], u.mfu_sched[b]);

		if (b < u.tex_nb)
			put(&o, "  tex        [%d] 0x%08X\n",
			    u.tex_word[b], u.tex[b]);

		if (b < u.alu_sched_nb)
			put(&o, "  alu_sched  [%d] 0x%08X\n",
			    u.alu_sched_word[b], u.alu_sched[b]);

		if (b < u.complement_nb)
			put(&o, "  complement [%d] 0x%08X\n",
			    u.complement_word[b], u.complement[b]);

		if (b < u.dw_nb)
			put(&o, "  dw         [%d] 0x%08X\n",
			    u.dw_word[b], u.dw[b]);
	}

	for (i = 0; i < u.mfu_nb; i++) {
		put(&o, "mfu %u @ [%d,%d] 0x%08X 0x%08X\n", i,
		    u.mfu_word[i], u.mfu_word[i] + 1, u.mfu[i][0], u.mfu[i][1]);
		dump_mfu(&o, u.mfu[i]);
	}

	for (i = 0; i < u.alu_nb; i++) {
		unsigned k;

		put(&o, "alu %u @ [%d..%d]", i, u.alu_word[i], u.alu_word[i] + 7);
		for (k = 0; k < 8; k++)
			put(&o, "%s 0x%08X", k ? "," : "", u.alu[i][k]);
		put(&o, "\n");
		dump_alu(&o, u.alu[i]);
	}

	for (i = 0; i < u.config_nb; i++)
		put(&o, "config [%d] 0x%03X = 0x%08X\n",
		    u.config_addr[i], u.config_off[i], u.config_word[i]);

	return o.buf;
}
