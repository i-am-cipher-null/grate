/*
 * Copyright (c) 2016 Dmitry Osipenko
 *
 * Permission is hereby granted, free of charge, to any person obtaining a
 * copy of this software and associated documentation files (the "Software"),
 * to deal in the Software without restriction, including without limitation
 * the rights to use, copy, modify, merge, publish, distribute, sublicense,
 * and/or sell copies of the Software, and to permit persons to whom the
 * Software is furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
 * THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
 * FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
 * DEALINGS IN THE SOFTWARE.
 */

#include <byteswap.h>
#include <getopt.h>
#include <libgen.h>
#include <locale.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "etc1.h"
#include "grate.h"
#include "libgrate-private.h"
#include "grate-3d.h"
#include "tgr_3d.xml.h"

struct vs_uniform {
	char name[256];
	float values[4];
};

struct fs_uniform {
	char name[256];
	float value;
};

struct vs_mat4 {
	char name[256];
	float values[16];
};

/* GRATE_RT5_FMT decides the pixel size of the rt5 readback */
static unsigned rt5_bpp = 4;
static unsigned rt5_pitch;

struct vs_asm_test {
	char *vs_path;
	char *fs_path;
	char *linker_path;
	uint32_t expected_result;
	bool has_expected;
	bool test_only;
	bool print_result;
	bool texture;
	bool mipmap;

	struct vs_mat4 vs_mat4s[16];
	unsigned vs_mat4s_nb;

	struct vs_uniform vs_uniforms[256];
	unsigned vs_uniforms_nb;

	struct fs_uniform fs_uniforms[32 * 2];
	unsigned fs_uniforms_nb;
};

static const float vertices[] = {
	-1.0f,  1.0f, 0.0f, 1.0f,
	-1.0f, -1.0f, 0.0f, 1.0f,
	 1.0f,  1.0f, 0.0f, 1.0f,
	 1.0f, -1.0f, 0.0f, 1.0f,
};

static const float colors[] = {
	1.0f, 0.0f, 0.0f, 1.0f,
	0.0f, 1.0f, 0.0f, 1.0f,
	0.0f, 0.0f, 1.0f, 1.0f,
	1.0f, 0.0f, 0.0f, 1.0f,
};

static const unsigned short indices[] = {
	0, 1, 2, 1, 2, 3,
};

static const float texcoords[] = {
	0.0f, 0.0f,
	0.0f, 1.0f,
	1.0f, 0.0f,
	1.0f, 1.0f,
};
static int parse_command_line(struct vs_asm_test *test, int argc, char *argv[])
{
	int ret;
	int c;

	memset(test, 0, sizeof(*test));

	do {
		struct option long_options[] =
		{
			{"expected",	required_argument, NULL, 0},
			{"vs",		required_argument, NULL, 0},
			{"fs",		required_argument, NULL, 0},
			{"lnk",		required_argument, NULL, 0},
			{"testonly",	no_argument, NULL, 0},
			{"vs_uniform",	required_argument, NULL, 0},
			{"fs_uniform",	required_argument, NULL, 0},
			{"result",	no_argument, NULL, 0},
			{"texture",	no_argument, NULL, 0},
			{"vs_mat4",	required_argument, NULL, 0},
			{"mipmap",	no_argument, NULL, 0},
			{ /* Sentinel */ }
		};
		int option_index = 0;

		c = getopt_long(argc, argv, "h", long_options, &option_index);

		switch (c) {
		case 0:
			switch (option_index) {
			case 0:
				ret = sscanf(optarg, "0x%X", &test->expected_result);
				if (ret != 1) {
					fprintf(stderr, "failed to parse \"expected\" argument\n");
					return 0;
				}
				test->has_expected = true;
				break;
			case 1:
				test->vs_path = optarg;
				break;
			case 2:
				test->fs_path = optarg;
				break;
			case 3:
				test->linker_path = optarg;
				break;
			case 4:
				test->test_only = true;
				break;
			case 5:
				ret = sscanf(optarg, "[\"%[^\"]\"]=(%f,%f,%f,%f)",
					     test->vs_uniforms[test->vs_uniforms_nb].name,
					     &test->vs_uniforms[test->vs_uniforms_nb].values[0],
					     &test->vs_uniforms[test->vs_uniforms_nb].values[1],
					     &test->vs_uniforms[test->vs_uniforms_nb].values[2],
					     &test->vs_uniforms[test->vs_uniforms_nb].values[3]);
				if (ret != 5) {
					fprintf(stderr, "failed to parse argument %s %d\n",
						optarg, ret);
					return 0;
				}
				test->vs_uniforms_nb++;
				break;
			case 6:
				ret = sscanf(optarg, "[\"%[^\"]\"]=%f",
					     test->fs_uniforms[test->fs_uniforms_nb].name,
					     &test->fs_uniforms[test->fs_uniforms_nb].value);
				if (ret != 2) {
					fprintf(stderr, "failed to parse argument %s %d\n",
						optarg, ret);
					return 0;
				}
				test->fs_uniforms_nb++;
				break;
			case 7:
				test->print_result = true;
				break;
			case 8:
				test->texture = true;
				break;
			case 10:
				test->mipmap = true;
				break;
			case 9: {
				struct vs_mat4 *m;
				char *p;
				unsigned n = 0;

				if (test->vs_mat4s_nb >= sizeof(test->vs_mat4s) /
						       sizeof(test->vs_mat4s[0])) {
					fprintf(stderr, "too many vs_mat4 values (max %zu)\n",
						sizeof(test->vs_mat4s) / sizeof(test->vs_mat4s[0]));
					return 0;
				}

				m = &test->vs_mat4s[test->vs_mat4s_nb];
				p = strchr(optarg, '=');

				if (sscanf(optarg, "[\"%255[^\"]\"]", m->name) != 1 || !p) {
					fprintf(stderr, "failed to parse \"vs_mat4\" argument %s\n",
						optarg);
					return 0;
				}

				p++;
				while (*p && *p != '(')
					p++;
				if (*p == '(')
					p++;

				while (*p && *p != ')' && n < 16) {
					m->values[n++] = strtod(p, &p);
					while (*p == ',' || *p == ' ')
						p++;
				}

				if (n != 16) {
					fprintf(stderr, "vs_mat4 %s: expected 16 values, got %u\n",
						m->name, n);
					return 0;
				}
				test->vs_mat4s_nb++;
				break;
			}
			default:
				return 0;
			}
			break;
		case -1:
			break;
		default:
			fprintf(stderr, "Invalid arguments\n\n");
		case 'h':
			fprintf(stderr, "Valid arguments:\n");
			fprintf(stderr, "\t--vs path : vertex asm path\n");
			fprintf(stderr, "\t--fs path : fragment asm path\n");
			fprintf(stderr, "\t--lnk path : linker asm path\n");
			fprintf(stderr, "\t--expected 0x00000000 : perform the test\n");
			fprintf(stderr, "\t--testonly : don't show the rendered result\n");
			fprintf(stderr, "\t--result : print the result word always\n");
			fprintf(stderr, "\t--texture : create a test texture\n");
			fprintf(stderr, "\t--mipmap : generate a mip chain for the test texture\n");
			fprintf(stderr, "\t--vs_mat4 '[\"name\"]=(16 values)' : set a vertex mat4 uniform\n");
			fprintf(stderr, "\t-h : this help\n");
			return 0;
		}
	} while (c != -1);

	return 1;
}

/*
 * Block-compressed test textures.
 *
 * The sampler has no pitch field (docs/hardware-testing.md), so it walks rows
 * at width * bpp / texel_width, which for a 4x4-block format is one 8-byte
 * block per 4 texels: width / 4 blocks per block row. GRATE_TEX_FORMAT=dxt1
 * or =etc1 fills block row b with a single solid colour, cycling blue, green,
 * red, white like GRATE_TEX_ROWCOLOR, so block-row addressing is readable.
 */
static uint16_t rgb565(unsigned colour)
{
	unsigned r = colour & 0xff;
	unsigned g = (colour >> 8) & 0xff;
	unsigned b = (colour >> 16) & 0xff;

	return ((r * 31 / 255) << 11) | ((g * 63 / 255) << 5) | (b * 31 / 255);
}

/*
 * GRATE_TEX_MIN_FILTER / GRATE_TEX_MAG_FILTER select the sampler's filters by
 * name, so a probe can ask for a linear or blending state explicitly instead
 * of relying on the --mipmap defaults.
 */
static int texture_filter_from_name(const char *name,
				    enum grate_textute_filter *filter)
{
	if (!strcmp(name, "nearest"))
		*filter = GRATE_TEXTURE_NEAREST;
	else if (!strcmp(name, "linear"))
		*filter = GRATE_TEXTURE_LINEAR;
	else if (!strcmp(name, "nearest-mipmap-nearest"))
		*filter = GRATE_TEXTURE_NEAREST_MIPMAP_NEAREST;
	else if (!strcmp(name, "linear-mipmap-nearest"))
		*filter = GRATE_TEXTURE_LINEAR_MIPMAP_NEAREST;
	else if (!strcmp(name, "nearest-mipmap-linear"))
		*filter = GRATE_TEXTURE_NEAREST_MIPMAP_LINEAR;
	else if (!strcmp(name, "linear-mipmap-linear"))
		*filter = GRATE_TEXTURE_LINEAR_MIPMAP_LINEAR;
	else
		return -1;

	return 0;
}

/*
 * GRATE_TEX_WRAP / GRATE_TEX_WRAP_S / GRATE_TEX_WRAP_T name the sampler's wrap
 * state, so an out-of-range coordinate can be attributed to it.
 */
static int wrap_mode_from_name(const char *name,
			       enum grate_textute_wrap_mode *mode)
{
	if (!strcmp(name, "clamp"))
		*mode = GRATE_TEXTURE_CLAMP_TO_EDGE;
	else if (!strcmp(name, "repeat"))
		*mode = GRATE_TEXTURE_REPEAT;
	else if (!strcmp(name, "mirror"))
		*mode = GRATE_TEXTURE_MIRRORED_REPEAT;
	else
		return -1;

	return 0;
}

/*
 * GRATE_TEX_CHANNELS paints one channel per texel in the first row and a word
 * with four distinct bytes in the second row, so the component order on the
 * different destination paths can be named rather than inferred from a
 * uniform colour.
 */
static void setup_channel_texture(struct grate *grate,
				  struct grate_texture *tex,
				  int tex_w, int tex_h)
{
	static const unsigned channel[4] = {
		0xFF000000, 0x00FF0000, 0x0000FF00, 0x000000FF,
	};
	int x;

	for (x = 0; x < 4 && x < tex_w; x++)
		grate_texture_clear_rect(grate, tex, channel[x], x, 0, 1, 1);

	if (tex_h > 1)
		grate_texture_clear_rect(grate, tex, 0x11223344, 0, 1, 1, 1);
}

static int setup_rgba_texture(struct grate *grate, struct grate_texture *tex,
			      int tex_w, int tex_h, bool mipmap)
{
	grate_texture_clear(grate, tex, 0xFFFFFFFF);
	grate_texture_clear_rect(grate, tex, 0x00000000, 0, 0,
				 tex_w / 2, tex_h);

	if (getenv("GRATE_TEX_ROWCOLOR")) {
		static const unsigned row_color[4] = {
			0x000000FF, 0x0000FF00, 0x00FF0000, 0xFFFFFFFF,
		};
		int y;

		for (y = 0; y < tex_h; y++)
			grate_texture_clear_rect(grate, tex,
						 row_color[y & 3],
						 0, y, tex_w, 1);
	}

	if (getenv("GRATE_TEX_ROWCHECK")) {
		int y;

		grate_texture_clear(grate, tex, 0xFFFFFFFF);
		for (y = 1; y < tex_h; y += 2)
			grate_texture_clear_rect(grate, tex, 0,
						 0, y, tex_w, 1);
	}

	if (getenv("GRATE_TEX_CHANNELS"))
		setup_channel_texture(grate, tex, tex_w, tex_h);

	/*
	 * GRATE_TEX_PIXEL=x,y,0xRRGGBBAA clears level 0 to zero and paints one
	 * texel. Run before the mip chain is generated, it gives each level a
	 * distinct value: a single white texel of a 16x16 surface averages down to
	 * 0x40 at level 1, 0x10 at level 2, 0x04 at level 3 and 0x01 at level 4.
	 */
	/*
	 * GRATE_TEX_CHECKER alternates texels between black and white. Every 2x2
	 * block of the surface averages to 0x7F, so level 0 is a checker and every
	 * generated level above it is flat: a fetch that selects a level by a
	 * coordinate derivative changes from a pattern to one word.
	 */
	if (getenv("GRATE_TEX_CHECKER")) {
		int x;

		grate_texture_clear(grate, tex, 0x00000000);
		for (x = 0; x < tex_w; x += 2)
			grate_texture_clear_rect(grate, tex, 0xFFFFFFFF,
						 x, 0, 1, tex_h);
	}

	/*
	 * GRATE_TEX_STEPS=1 gives the mip chain a readout whose shape names the
	 * level. The surface is a one-texel checker split into four vertical bands
	 * of decreasing intensity (0xFF, 0xBF, 0x7F, 0x3F over black), so level 1
	 * is four flat bands at 0x80, 0x60, 0x40, 0x20, level 2 two bands at 0x70 and
	 * 0x30, and level 3 and above the single flat 0x50. Level 0 is the checker
	 * itself. Four states - checker, four bands, two bands, one word - can be
	 * read from the map without assuming any word in advance, and the band
	 * count does not depend on the sampling phase.
	 */
	if (getenv("GRATE_TEX_STEPS")) {
		static const unsigned band[4] = {
			0xFFFFFFFF, 0xFFBFBFBF, 0xFF7F7F7F, 0xFF3F3F3F,
		};
		int x, y;

		grate_texture_clear(grate, tex, 0x00000000);
		for (y = 0; y < tex_h; y++)
			for (x = y & 1; x < tex_w; x += 2)
				grate_texture_clear_rect(grate, tex,
						 band[x * 4 / tex_w], x, y, 1, 1);
	}

	/*
	 * GRATE_TEX_COLUMNS=n paints white columns every n texels. Every level of
	 * the generated chain then has its own value: with n = 4, level 0 is a
	 * 1-in-4 column pattern, level 1 is 1-in-2 at 0x80, and levels 2..4 are
	 * the flat words 0x40, 0x20 and 0x10. A fetch whose level changes is
	 * therefore distinguishable from the whole band, not just from level 0.
	 */
	if (getenv("GRATE_TEX_COLUMNS")) {
		int spacing = atoi(getenv("GRATE_TEX_COLUMNS"));
		int x;

		if (spacing < 2) {
			fprintf(stderr, "GRATE_TEX_COLUMNS needs a spacing >= 2\n");
			return -1;
		}
		grate_texture_clear(grate, tex, 0x00000000);
		for (x = 0; x < tex_w; x += spacing)
			grate_texture_clear_rect(grate, tex, 0xFFFFFFFF,
						 x, 0, 1, tex_h);
	}

	if (getenv("GRATE_TEX_PIXEL")) {
		unsigned px, py, color;

		if (sscanf(getenv("GRATE_TEX_PIXEL"), "%u,%u,0x%x",
			   &px, &py, &color) != 3 ||
		    px >= (unsigned)tex_w || py >= (unsigned)tex_h) {
			fprintf(stderr, "GRATE_TEX_PIXEL needs x,y,0xRRGGBBAA "
				"inside the texture\n");
			return -1;
		}
		grate_texture_clear(grate, tex, 0x00000000);
		grate_texture_clear_rect(grate, tex, color, px, py, 1, 1);
	}

	/*
	 * GRATE_TEX_GRID=1 fills a 4x4 grid of blocks with 16 distinct words, the
	 * red byte naming the block column and the green byte the block row. A
	 * coordinate outside [0,1] that wraps in s moves the red byte only and one
	 * that wraps in t moves the green byte only, so the two axes' wrap states
	 * are readable from a single fetched word and can be mixed independently.
	 * It is applied last, so it wins over the surfaces above.
	 */
	if (getenv("GRATE_TEX_GRID")) {
		int bw = tex_w / 4 ? tex_w / 4 : 1;
		int bh = tex_h / 4 ? tex_h / 4 : 1;
		int bx, by;

		grate_texture_clear(grate, tex, 0xFF000000);
		for (by = 0; by < 4; by++)
			for (bx = 0; bx < 4; bx++)
				grate_texture_clear_rect(grate, tex,
						 0xFF000000 |
						 ((unsigned)((bx + 1) * 0x30) << 16) |
						 ((unsigned)((by + 1) * 0x30) << 8),
						 bx * bw, by * bh, bw, bh);
	}

	if (mipmap) {
		if (grate_texture_generate_mipmap(grate, tex))
			return -1;
		grate_texture_set_min_filter(tex,
				GRATE_TEXTURE_NEAREST_MIPMAP_NEAREST);
		grate_texture_set_mag_filter(tex, GRATE_TEXTURE_NEAREST);
		grate_texture_set_max_lod(tex, 11);
	}

	return 0;
}

static int upload_block_texture(struct grate *grate,
				struct grate_texture *tex,
				unsigned width, unsigned height,
				enum pixel_format format)
{
	static const unsigned row_color[4] = {
		0x000000FF, 0x0000FF00, 0x00FF0000, 0xFFFFFFFF,
	};
	unsigned bw = (width + 3) / 4, bh = (height + 3) / 4;
	/* lay the blocks out at the pixbuf's pitch so load_data can memcpy */
	unsigned pitch = tex->pixbuf->pitch, b, x, y;
	void *data;
	int err;

	if (pitch < bw * 8)
		return -1;

	data = calloc(bh, pitch);
	if (!data)
		return -1;

	if (format != PIX_BUF_FMT_DXT1 && format != PIX_BUF_FMT_ETC1) {
		free(data);
		return -1;
	}

	/* GRATE_TEX_ZERO leaves the surface all zero, to see what the sampler
	 * makes of a block of zero bytes. */
	if (!getenv("GRATE_TEX_ZERO")) {
		if (format == PIX_BUF_FMT_DXT1) {
			for (b = 0; b < bh; b++) {
				uint32_t *blk =
					(uint32_t *)((char *)data + b * pitch);
				uint16_t c = rgb565(row_color[b & 3]);

				/* colour0 == colour1 == c, all indices 0: solid */
				for (x = 0; x < bw; x++) {
					blk[x * 2 + 0] = c | ((uint32_t)c << 16);
					blk[x * 2 + 1] = 0;
				}
			}
		} else {
			unsigned char *src = malloc(width * height * 3);
			unsigned char *enc;

			if (!src) {
				free(data);
				return -1;
			}

			enc = calloc(bh, bw * 8);
			if (!enc) {
				free(src);
				free(data);
				return -1;
			}

			for (y = 0; y < height; y++) {
				for (x = 0; x < width; x++) {
					unsigned colour = row_color[(y / 4) & 3];
					unsigned char *px =
						src + (y * width + x) * 3;

					px[0] = colour;
					px[1] = colour >> 8;
					px[2] = colour >> 16;
				}
			}

			/* encodes contiguously, so copy block row by row */
			err = etc1_encode_image(src, width, height, 3,
						width * 3, enc);
			free(src);
			if (err) {
				free(enc);
				free(data);
				return -1;
			}

			/* GR3D wants the blocks byte-swapped (grate-texture.c) */
			for (b = 0; b < bh; b++) {
				for (x = 0; x < bw; x++) {
					uint64_t *bp = (uint64_t *)
						(enc + b * bw * 8) + x;
					uint64_t a = bswap_32(*bp >> 32);
					uint64_t c = bswap_32(*bp);
					uint64_t w = (c << 32) | a;

					memcpy((char *)data + b * pitch +
					       x * 8, &w, 8);
				}
			}

			free(enc);
		}
	}

	err = host1x_pixelbuffer_load_data(grate->host1x, tex->pixbuf, data,
					   pitch, bh * pitch, format,
					   PIX_BUF_LAYOUT_LINEAR);
	free(data);

	return err;
}

static void dump_raw(struct grate_shader *vs,
		     struct grate_shader *fs,
		     struct grate_shader *linker,
		     struct grate_program *program)
{
	unsigned i;

	fprintf(stderr, "\nVertex constants raw:\n");

	for (i = 0; i < 256 * 4; i++)
		fprintf(stderr, "\t[%d] = 0x%08X,\n",
			i, program->vs_constants[i]);

	fprintf(stderr, "\nFragment constants raw:\n");

	for (i = 0; i < 32; i++)
		fprintf(stderr, "\t[%d] = 0x%08X,\n",
			i, program->fs_constants[i]);

	fprintf(stderr, "\nVertex program raw:\n");

	for (i = 0; i < vs->num_words; i++)
		fprintf(stderr, "\t[%d] = 0x%08X,\n", i, vs->words[i]);

	fprintf(stderr, "\nFragment program raw:\n");

	for (i = 0; i < fs->num_words; i++)
		fprintf(stderr, "\t[%d] = 0x%08X,\n", i, fs->words[i]);

	fprintf(stderr, "\nLinker program raw:\n");

	for (i = 0; i < linker->num_words; i++)
		fprintf(stderr, "\t[%d] = 0x%08X,\n", i, linker->words[i]);
}

static void dump_asm(struct grate_shader *vs,
		     struct grate_shader *fs,
		     struct grate_shader *linker)
{
	fprintf(stderr, "\nVertex disassembly:\n%s\n",
		grate_shader_disasm_vs(vs) ?: "");

	fprintf(stderr, "\nFragment disassembly:\n%s\n",
		grate_shader_disasm_fs(fs) ?: "");

	fprintf(stderr, "\nLinker disassembly:\n%s\n",
		grate_shader_disasm_linker(linker) ?: "");
}

static enum grate_3d_ctx_stencil_operation stencil_zpass_op(void)
{
	const char *op = getenv("GRATE_STENCIL_OP");

	if (!op || !strcmp(op, "replace"))
		return GRATE_3D_CTX_STENCIL_OP_REPLACE;
	if (!strcmp(op, "keep"))
		return GRATE_3D_CTX_STENCIL_OP_KEEP;
	if (!strcmp(op, "zero"))
		return GRATE_3D_CTX_STENCIL_OP_ZERO;
	if (!strcmp(op, "invert"))
		return GRATE_3D_CTX_STENCIL_OP_INVERT;
	if (!strcmp(op, "incr"))
		return GRATE_3D_CTX_STENCIL_OP_INCR;
	if (!strcmp(op, "decr"))
		return GRATE_3D_CTX_STENCIL_OP_DECR;
	if (!strcmp(op, "incr_wrap"))
		return GRATE_3D_CTX_STENCIL_OP_INCR_WRAP;
	if (!strcmp(op, "decr_wrap"))
		return GRATE_3D_CTX_STENCIL_OP_DECR_WRAP;

	fprintf(stderr, "unknown GRATE_STENCIL_OP \"%s\"\n", op);
	return GRATE_3D_CTX_STENCIL_OP_REPLACE;
}

/*
 * Draw the quad once per fragment program named in GRATE_EXTRA_FS, keeping the
 * bound render targets and the vertex setup. With flush_each the job is flushed
 * and waited for after every program; without it the draws stay in one
 * submission and the caller flushes at the end.
 */
static int run_extra_programs(struct grate *grate, struct grate_3d_ctx *ctx,
			      struct vs_asm_test *test, struct host1x_bo *bo,
			      bool flush_each)
{
	char *list = strdup(getenv("GRATE_EXTRA_FS"));
	char *save = NULL;
	char *path;

	if (!list)
		return 1;

	for (path = strtok_r(list, ":", &save); path;
	     path = strtok_r(NULL, ":", &save)) {
		struct grate_shader *vs2, *fs2, *lnk2;
		struct grate_program *program2;
		unsigned j;

		vs2 = grate_shader_parse_vertex_asm_from_file(test->vs_path);
		fs2 = grate_shader_parse_fragment_asm_from_file(path);
		lnk2 = grate_shader_parse_linker_asm_from_file(test->linker_path);
		if (!vs2 || !fs2 || !lnk2) {
			fprintf(stderr, "%s assembler parse failed\n", path);
			free(list);
			return 1;
		}

		program2 = grate_program_new(grate, vs2, fs2, lnk2);
		if (!program2) {
			fprintf(stderr, "extra program alloc failed\n");
			free(list);
			return 1;
		}
		grate_program_link(program2);
		grate_3d_ctx_bind_program(ctx, program2);

		for (j = 0; j < test->vs_uniforms_nb; j++) {
			int loc2 = grate_get_vertex_uniform_location(
					program2, test->vs_uniforms[j].name);

			if (loc2 >= 0)
				grate_3d_ctx_set_vertex_uniform(ctx, loc2, 4,
						test->vs_uniforms[j].values);
		}

		for (j = 0; j < test->fs_uniforms_nb; j++) {
			int loc2 = grate_get_fragment_uniform_location(
					program2, test->fs_uniforms[j].name);

			if (loc2 >= 0)
				grate_3d_ctx_set_fragment_uniform(ctx, loc2, 1,
						&test->fs_uniforms[j].value);
		}

		grate_3d_draw_elements(ctx, TGR3D_PRIMITIVE_TYPE_TRIANGLES,
				       bo, TGR3D_INDEX_MODE_UINT16,
				       ARRAY_SIZE(indices));
		if (flush_each)
			grate_flush(grate);
		fprintf(stdout, "extra-draw=%s\n", path);
	}

	free(list);
	return 0;
}

int main(int argc, char *argv[])
{
	struct vs_asm_test test;
	struct grate_program *program;
	struct grate_framebuffer *fb;
	struct grate_shader *vs, *fs, *linker;
	struct grate_options options;
	struct grate *grate;
	struct grate_3d_ctx *ctx;
	struct host1x_pixelbuffer *pixbuf;
	struct host1x_pixelbuffer *depth = NULL;
	struct host1x_pixelbuffer *stencil = NULL;
	struct host1x_pixelbuffer *rt3 = NULL;
	struct host1x_pixelbuffer *rt4 = NULL;
	struct host1x_pixelbuffer *rt5 = NULL;
	struct host1x_bo *bo;
	uint32_t *fb_data;
	uint32_t result;
	int ret = 0;
	int location;
	int i;

	/* float decimal point is locale-dependent */
	setlocale(LC_ALL, "C");

	if (!parse_command_line(&test, argc, argv))
		return 1;

	if (!grate_parse_command_line(&options, argc, argv))
		return 1;

	grate = grate_init(&options);
	if (!grate)
		return 1;

	options.width = 256;
	options.height = 256;

	/*
	 * GRATE_SIZE=64 makes every surface, including the render targets that
	 * hold the fetch source, a genuine 64x64 allocation instead of a 64x64
	 * window of a 256x256 one, so the source pitch and the target size can
	 * be tested independently.
	 */
	if (getenv("GRATE_SIZE")) {
		int size = atoi(getenv("GRATE_SIZE"));

		if (size < 16 || size > 256) {
			fprintf(stderr, "GRATE_SIZE must be 16..256\n");
			return 1;
		}
		options.width = size;
		options.height = size;
	}

	fb = grate_framebuffer_create(grate, options.width, options.height,
				      PIX_BUF_FMT_RGBA8888,
				      PIX_BUF_LAYOUT_TILED_16x16,
				      GRATE_SINGLE_BUFFERED);
	if (!fb)
		return 1;

	grate_clear_color(grate, 0.3f, 0.3f, 0.3f, 0.3f);
	grate_bind_framebuffer(grate, fb);
	grate_clear(grate);

	/* Prepare shaders */

	vs = grate_shader_parse_vertex_asm_from_file(test.vs_path);
	if (!vs) {
		fprintf(stderr, "%s assembler parse failed\n",
			test.vs_path);
		return 1;
	}

	fs = grate_shader_parse_fragment_asm_from_file(test.fs_path);
	if (!fs) {
		fprintf(stderr, "%s assembler parse failed\n",
			test.fs_path);
		return 1;
	}

	linker = grate_shader_parse_linker_asm_from_file(test.linker_path);
	if (!linker) {
		fprintf(stderr, "%s assembler parse failed\n",
			test.linker_path);
		return 1;
	}

	program = grate_program_new(grate, vs, fs, linker);
	grate_program_link(program);

	/* Setup context */

	ctx = grate_3d_alloc_ctx(grate);

	if (getenv("GRATE_DEPTH")) {
		depth = host1x_pixelbuffer_create(grate->host1x,
						  options.width, options.height,
						  options.width * 2,
						  PIX_BUF_FMT_D16_LINEAR,
						  PIX_BUF_LAYOUT_LINEAR);
		if (!depth) {
			fprintf(stderr, "depth buffer alloc failed\n");
			return 1;
		}
		grate_3d_ctx_bind_depth_buffer(ctx, depth);
		grate_3d_ctx_perform_depth_test(ctx, true);
		grate_3d_ctx_perform_depth_write(ctx, true);
		grate_3d_ctx_set_depth_func(ctx, GRATE_3D_CTX_DEPTH_FUNC_ALWAYS);
	}
	if (test.texture) {
		struct grate_texture *tex;
		const char *tex_env = getenv("GRATE_TEX_SIZE");
		const char *fmt_env = getenv("GRATE_TEX_FORMAT");
		enum pixel_format fmt = PIX_BUF_FMT_RGBA8888;
		int tex_w = 4, tex_h = 4;

		if (tex_env)
			sscanf(tex_env, "%dx%d", &tex_w, &tex_h);

		if (fmt_env) {
			if (!strcmp(fmt_env, "dxt1"))
				fmt = PIX_BUF_FMT_DXT1;
			else if (!strcmp(fmt_env, "etc1"))
				fmt = PIX_BUF_FMT_ETC1;
			else {
				fprintf(stderr, "unknown GRATE_TEX_FORMAT \"%s\"\n",
					fmt_env);
				return 1;
			}

			if ((tex_w & 3) || (tex_h & 3)) {
				fprintf(stderr, "GRATE_TEX_FORMAT needs multiples of 4\n");
				return 1;
			}
		}

		tex = grate_create_texture(grate, tex_w, tex_h, fmt,
					   PIX_BUF_LAYOUT_LINEAR);
		if (!tex)
			return 1;
		if (getenv("GRATE_TEX_PITCH")) {
			unsigned tex_pitch = atoi(getenv("GRATE_TEX_PITCH"));

			host1x_pixelbuffer_free(tex->pixbuf);
			tex->pixbuf = host1x_pixelbuffer_create(grate->host1x,
							tex_w, tex_h, tex_pitch,
							fmt,
							PIX_BUF_LAYOUT_LINEAR);
			if (!tex->pixbuf)
				return 1;
		}

		if (fmt == PIX_BUF_FMT_RGBA8888) {
			if (setup_rgba_texture(grate, tex, tex_w, tex_h, test.mipmap))
				return 1;
		} else {
			/* gr2d cannot fill a block-compressed surface */
			if (test.mipmap) {
				fprintf(stderr, "GRATE_TEX_FORMAT has no mipmap path\n");
				return 1;
			}

			if (upload_block_texture(grate, tex, tex_w, tex_h, fmt))
				return 1;
		}

		{
			enum grate_textute_filter filter;
			const char *min = getenv("GRATE_TEX_MIN_FILTER");
			const char *mag = getenv("GRATE_TEX_MAG_FILTER");

			if (min) {
				if (texture_filter_from_name(min, &filter)) {
					fprintf(stderr,
						"unknown GRATE_TEX_MIN_FILTER \"%s\"\n",
						min);
					return 1;
				}
				grate_texture_set_min_filter(tex, filter);
			}

			if (mag) {
				if (texture_filter_from_name(mag, &filter)) {
					fprintf(stderr,
						"unknown GRATE_TEX_MAG_FILTER \"%s\"\n",
						mag);
					return 1;
				}
				grate_texture_set_mag_filter(tex, filter);
			}

			/*
			 * GRATE_TEX_WRAP=clamp|repeat|mirror sets the s and t wrap state
			 * explicitly, so an out-of-range coordinate can be attributed to the
			 * sampler's wrap mode rather than to the filter's footprint. The
			 * allocation leaves both flags clear, which is the repeat mode.
			 */
			{
				const char *wrap = getenv("GRATE_TEX_WRAP");
				enum grate_textute_wrap_mode mode;

				if (wrap) {
					if (wrap_mode_from_name(wrap, &mode)) {
						fprintf(stderr,
							"unknown GRATE_TEX_WRAP \"%s\"\n",
							wrap);
						return 1;
					}
					grate_texture_set_wrap_s(tex, mode);
					grate_texture_set_wrap_t(tex, mode);
				}
				/*
				 * GRATE_TEX_WRAP_S / GRATE_TEX_WRAP_T override one axis, so a
				 * coordinate outside [0,1] in both axes can be told apart per
				 * axis and the two states can be mixed (GRATE_TEX_WRAP still
				 * sets both as the base).
				 */
				wrap = getenv("GRATE_TEX_WRAP_S");
				if (wrap) {
					enum grate_textute_wrap_mode mode;

					if (wrap_mode_from_name(wrap, &mode)) {
						fprintf(stderr,
							"unknown GRATE_TEX_WRAP_S \"%s\"\n",
							wrap);
						return 1;
					}
					grate_texture_set_wrap_s(tex, mode);
				}
				wrap = getenv("GRATE_TEX_WRAP_T");
				if (wrap) {
					enum grate_textute_wrap_mode mode;

					if (wrap_mode_from_name(wrap, &mode)) {
						fprintf(stderr,
							"unknown GRATE_TEX_WRAP_T \"%s\"\n",
							wrap);
						return 1;
					}
					grate_texture_set_wrap_t(tex, mode);
				}
			}
		}

		ctx->textures[0] = tex;
	}

	if (getenv("GRATE_STENCIL")) {
		stencil = host1x_pixelbuffer_create(grate->host1x,
						    options.width, options.height,
						    options.width,
						    PIX_BUF_FMT_S8,
						    getenv("GRATE_STENCIL_TILED") ? PIX_BUF_LAYOUT_TILED_16x16
						    : PIX_BUF_LAYOUT_LINEAR);
		if (!stencil) {
			fprintf(stderr, "stencil buffer alloc failed\n");
			return 1;
		}
		grate_3d_ctx_bind_stencil_buffer(ctx, stencil);
		if (getenv("GRATE_STENCIL_TEST")) {
			grate_3d_ctx_perform_stencil_test(ctx, true);
			grate_3d_ctx_set_stencil_func(ctx,
				GRATE_3D_CTX_STENCIL_TEST_BOTH,
				GRATE_3D_CTX_STENCIL_TEST_ALWAYS,
				(unsigned)(getenv("GRATE_STENCIL_REF") ? atoi(getenv("GRATE_STENCIL_REF")) : 0), 0xff);
			grate_3d_ctx_set_stencil_ops(ctx,
				GRATE_3D_CTX_STENCIL_TEST_BOTH,
				GRATE_3D_CTX_STENCIL_OP_KEEP,
				GRATE_3D_CTX_STENCIL_OP_KEEP,
				stencil_zpass_op());
		}
	}

	if (getenv("GRATE_COVERAGE")) {
		/*
		 * GRATE_PITCH_PAD also applies to rt3, so a padded *tiled* source can
		 * be a fetch target: the pad only changes the allocation, the logical
		 * width stays options.width.
		 */
		unsigned rt3_pitch = options.width * 4;

		if (getenv("GRATE_PITCH_PAD"))
			rt3_pitch = (options.width +
				     (unsigned)atoi(getenv("GRATE_PITCH_PAD"))) * 4;

		rt3 = host1x_pixelbuffer_create(grate->host1x,
					       options.width, options.height,
					       rt3_pitch,
					       PIX_BUF_FMT_RGBA8888,
					       getenv("GRATE_COVERAGE_TILED") ?
					       PIX_BUF_LAYOUT_TILED_16x16 :
					       PIX_BUF_LAYOUT_LINEAR);
		if (!rt3) {
			fprintf(stderr, "coverage alloc failed\n");
			return 1;
		}
		grate_3d_ctx_bind_render_target(ctx, 3, rt3);
		grate_3d_ctx_enable_render_target(ctx, 3);
	}

	if (getenv("GRATE_RT4")) {
		rt4 = host1x_pixelbuffer_create(grate->host1x,
						options.width, options.height,
						options.width * 4,
						PIX_BUF_FMT_RGBA8888,
						PIX_BUF_LAYOUT_LINEAR);
		if (!rt4) {
			fprintf(stderr, "rt4 alloc failed\n");
			return 1;
		}
		grate_3d_ctx_bind_render_target(ctx, 4, rt4);
		grate_3d_ctx_enable_render_target(ctx, 4);
	}

	if (getenv("GRATE_TEX2")) {
		struct grate_texture *t2 = grate_create_texture(grate, 4, 4,
				PIX_BUF_FMT_RGBA8888, PIX_BUF_LAYOUT_LINEAR);
		if (!t2)
			return 1;
		grate_texture_clear(grate, t2, 0x000000FF);
		ctx->textures[1] = t2;
	}

	if (getenv("GRATE_RT5")) {
		enum pixel_format fmt = PIX_BUF_FMT_RGBA8888;
		const char *fmt_env = getenv("GRATE_RT5_FMT");

		if (fmt_env && !strcmp(fmt_env, "rgb565")) {
			fmt = PIX_BUF_FMT_RGB565;
			rt5_bpp = 2;
		} else if (fmt_env && !strcmp(fmt_env, "a8")) {
			fmt = PIX_BUF_FMT_A8;
			rt5_bpp = 1;
		} else if (fmt_env && !strcmp(fmt_env, "l8")) {
			fmt = PIX_BUF_FMT_L8;
			rt5_bpp = 1;
		} else if (fmt_env && strcmp(fmt_env, "rgba8888")) {
			fprintf(stderr, "unknown GRATE_RT5_FMT \"%s\"\n", fmt_env);
			return 1;
		}

		/*
		 * GRATE_PITCH_PAD=16 allocates the source 16 texels wider than the
		 * target, so a fetch that assumes an unpadded pitch walks sideways
		 * through the surface.
		 */
		if (getenv("GRATE_PITCH_PAD"))
			rt5_pitch = (options.width +
				     (unsigned)atoi(getenv("GRATE_PITCH_PAD"))) * rt5_bpp;
		else
			rt5_pitch = options.width * rt5_bpp;

		rt5 = host1x_pixelbuffer_create(grate->host1x,
						options.width, options.height,
						rt5_pitch,
						fmt,
						PIX_BUF_LAYOUT_LINEAR);
		if (!rt5) {
			fprintf(stderr, "rt5 alloc failed\n");
			return 1;
		}
		grate_3d_ctx_bind_render_target(ctx, 5, rt5);
		grate_3d_ctx_enable_render_target(ctx, 5);
	}

	grate_3d_ctx_bind_program(ctx, program);
	grate_3d_ctx_set_depth_range(ctx, 0.0f, 1.0f);
	grate_3d_ctx_set_dither(ctx, 0x779);
	grate_3d_ctx_set_point_params(ctx, 0x1401);
	grate_3d_ctx_set_point_size(ctx, 1.0f);
	grate_3d_ctx_set_line_params(ctx, 0x2);
	grate_3d_ctx_set_line_width(ctx, 1.0f);
	grate_3d_ctx_set_viewport_bias(ctx, 0.0f, 0.0f, 0.5f);
	grate_3d_ctx_set_viewport_scale(ctx, options.width, options.height, 0.5f);
	grate_3d_ctx_use_guardband(ctx, true);
	grate_3d_ctx_set_front_direction_is_cw(ctx, false);
	grate_3d_ctx_set_cull_face(ctx, GRATE_3D_CTX_CULL_FACE_NONE);
	grate_3d_ctx_set_scissor(ctx, 0, options.width, 0, options.height);
	grate_3d_ctx_set_point_coord_range(ctx, 0.0f, 1.0f, 0.0f, 1.0f);
	grate_3d_ctx_set_polygon_offset(ctx, 0.0f, 0.0f);
	grate_3d_ctx_set_provoking_vtx_last(ctx, true);

	/* Setup vertices attribute */

	location = grate_get_attribute_location(program, "position");
	if (location >= 0) {
		bo = grate_create_attrib_bo_from_data(grate, vertices);
		grate_3d_ctx_vertex_attrib_float_pointer(ctx, location, 4, bo);
		grate_3d_ctx_enable_vertex_attrib_array(ctx, location);
	}

	/* Setup colors attribute */

	location = grate_get_attribute_location(program, "color");
	if (location >= 0) {
		bo = grate_create_attrib_bo_from_data(grate, colors);
		grate_3d_ctx_vertex_attrib_float_pointer(ctx, location, 4, bo);
		grate_3d_ctx_enable_vertex_attrib_array(ctx, location);
	}

	/* Setup texcoords attribute */

	location = grate_get_attribute_location(program, "texcoord");
	if (location >= 0) {
		bo = grate_create_attrib_bo_from_data(grate, texcoords);
		grate_3d_ctx_vertex_attrib_float_pointer(ctx, location, 2, bo);
		grate_3d_ctx_enable_vertex_attrib_array(ctx, location);
	}

	/* Setup render target */

	pixbuf = grate_get_draw_pixbuf(fb);
	grate_3d_ctx_bind_render_target(ctx, 1, pixbuf);
	grate_3d_ctx_enable_render_target(ctx, 1);

	/* Create indices BO */

	bo = grate_create_attrib_bo_from_data(grate, indices);

	if (!test.test_only || getenv("GRATE_DUMP_RAW")) {
		dump_raw(vs, fs, linker, program);
		dump_asm(vs, fs, linker);
	}

	/* Setup uniforms */

	for (i = 0; i < test.vs_uniforms_nb; i++) {
		int loc = grate_get_vertex_uniform_location(
					program, test.vs_uniforms[i].name);

		grate_3d_ctx_set_vertex_uniform(ctx, loc, 4,
						test.vs_uniforms[i].values);
	}

	for (i = 0; i < test.vs_mat4s_nb; i++) {
		int loc = grate_get_vertex_uniform_location(
					program, test.vs_mat4s[i].name);

		if (loc < 0) {
			fprintf(stderr, "vs_mat4 \"%s\" not found\n",
				test.vs_mat4s[i].name);
			continue;
		}

		grate_3d_ctx_set_vertex_uniform(ctx, loc, 16,
						test.vs_mat4s[i].values);
	}

	for (i = 0; i < test.fs_uniforms_nb; i++) {
		int loc = grate_get_fragment_uniform_location(
					program, test.fs_uniforms[i].name);

		grate_3d_ctx_set_fragment_uniform(ctx, loc, 1,
						  &test.fs_uniforms[i].value);
	}

	{
		const char *draw_env = getenv("GRATE_DRAW_COUNT");
		unsigned draws = draw_env ? atoi(draw_env) : 1;
		struct timespec t0, t1;

		if (!draws)
			draws = 1;

		clock_gettime(CLOCK_MONOTONIC, &t0);
		for (i = 0; i < (int)draws; i++)
			grate_3d_draw_elements(ctx, TGR3D_PRIMITIVE_TYPE_TRIANGLES,
					       bo, TGR3D_INDEX_MODE_UINT16,
					       ARRAY_SIZE(indices));

		/*
		 * GRATE_EXTRA_FS=a.in:b.in draws the same quad again with each fragment
		 * program in turn. The render targets, the vertex setup and the vertex
		 * uniforms are kept, so a program that only fetches can be run in a later
		 * draw and still see what the first draw stored, which separates the
		 * fetch from the store the same fragment would otherwise do in the same
		 * draw.
		 *
		 * By default the first draw is flushed before the extra programs run.
		 * GRATE_EXTRA_BEFORE_FLUSH=1 issues the extra draws before that flush
		 * instead, so the two draws share one submission and no explicit flush
		 * separates them - the question is then whether a fetch still sees the
		 * earlier draw's writes.
		 */
		if (getenv("GRATE_EXTRA_FS") && getenv("GRATE_EXTRA_BEFORE_FLUSH")) {
			if (run_extra_programs(grate, ctx, &test, bo, false))
				return 1;
			grate_flush(grate);
		} else {
			grate_flush(grate);

			if (getenv("GRATE_EXTRA_FS")) {
				if (run_extra_programs(grate, ctx, &test, bo, true))
					return 1;
			}
		}

		fb_data = grate_framebuffer_data(fb, true);
		clock_gettime(CLOCK_MONOTONIC, &t1);

		if (!fb_data)
			return 1;

		if (draws > 1)
			fprintf(stdout, "draws=%u elapsed_ms=%.3f\n", draws,
				(t1.tv_sec - t0.tv_sec) * 1000.0 +
				(t1.tv_nsec - t0.tv_nsec) / 1000000.0);
	}

	result = fb_data[0];

	if (depth) {
		void *dmap = NULL;

		if (host1x_bo_mmap(depth->bo, &dmap) == 0 && dmap) {
			uint16_t *d16 = dmap;
			unsigned w = options.width, h = options.height;
			unsigned i;

			fprintf(stdout, "depth16 row0:");
			for (i = 0; i < 4; i++)
				fprintf(stdout, " %04X", d16[i]);
			fprintf(stdout, " mid: %04X %04X", d16[(h / 2) * w + w / 2],
				d16[(h / 2) * w + w / 2 + 1]);
			fprintf(stdout, " last: %04X %04X\n",
				d16[h * w - 2], d16[h * w - 1]);
		} else {
			fprintf(stderr, "depth mmap failed\n");
		}
	}

	if (stencil) {
		void *smap = NULL;

		if (host1x_bo_mmap(stencil->bo, &smap) == 0 && smap) {
			uint8_t *s8 = smap;
			unsigned w = options.width, h = options.height;

			fprintf(stdout, "stencil8 row0: %02X %02X %02X %02X mid: %02X %02X last: %02X %02X\n",
				s8[0], s8[1], s8[2], s8[3],
				s8[(h / 2) * w + w / 2], s8[(h / 2) * w + w / 2 + 1],
				s8[h * w - 2], s8[h * w - 1]);
		} else {
			fprintf(stderr, "stencil mmap failed\n");
		}
	}

	if (rt3) {
		void *rmap = NULL;

		if (host1x_bo_mmap(rt3->bo, &rmap) == 0 && rmap) {
			uint32_t *r32 = rmap;
			unsigned k, nz = 0, first = 0;
			unsigned words = options.width * options.height;

			for (k = 0; k < words; k++) {
				if (r32[k]) {
					if (!nz)
						first = k;
					nz++;
				}
			}

			fprintf(stdout, "cov3: %08X %08X %08X %08X nz=%u first=%u wf=%08X wf1=%08X\n",
				r32[0], r32[1], r32[2], r32[3], nz, first,
				r32[first], r32[first + 1]);
		}
	}

	if (rt4) {
		void *rmap = NULL;

		if (host1x_bo_mmap(rt4->bo, &rmap) == 0 && rmap) {
			uint32_t *r32 = rmap;
			unsigned k, nz = 0, first = 0;
			unsigned words = options.width * options.height;

			for (k = 0; k < words; k++) {
				if (r32[k]) {
					if (!nz)
						first = k;
					nz++;
				}
			}

			fprintf(stdout, "rt4: %08X %08X %08X %08X nz=%u first=%u wf=%08X wf1=%08X\n",
				r32[0], r32[1], r32[2], r32[3], nz, first,
				r32[first], r32[first + 1]);
		}
	}

	if (rt5) {
		void *rmap = NULL;

		if (host1x_bo_mmap(rt5->bo, &rmap) == 0 && rmap) {
			unsigned k;

			if (rt5_bpp == 4) {
				uint32_t *r32 = rmap;
				fprintf(stdout, "rt5: %08X %08X %08X %08X",
					r32[0], r32[1], r32[2], r32[3]);
			} else if (rt5_bpp == 2) {
				uint16_t *r16 = rmap;
				fprintf(stdout, "rt5(16):");
				for (k = 0; k < 8; k++)
					fprintf(stdout, " %04X", r16[k]);
			} else {
				uint8_t *r8 = rmap;
				fprintf(stdout, "rt5(8):");
				for (k = 0; k < 16; k++)
					fprintf(stdout, " %02X", r8[k]);
			}
			fprintf(stdout, "\n");
		}
	}

	/*
	 * GRATE_REGION=x,y,w,h reports a rectangle of the linear rt4 surface: one
	 * row of words, then a count of nonzero words and a digest of the whole
	 * rectangle. The digest becomes the result word, so a program whose output
	 * depends on the fragment position can be pinned as a whole instead of
	 * through fb_data[0] alone. The rectangle is reported in target memory
	 * order, which for a linear surface is row-major from the target origin.
	 */
	if (getenv("GRATE_REGION")) {
		const char *region_env = getenv("GRATE_REGION");
		unsigned x0, y0, rw, rh, yy, xx, nz = 0;
		uint32_t hash = 2166136261u;
		void *rmap = NULL;
		uint32_t *r32;

		if (!rt4 || sscanf(region_env, "%u,%u,%u,%u",
				   &x0, &y0, &rw, &rh) != 4 ||
		    !rw || !rh || x0 + rw > options.width ||
		    y0 + rh > options.height) {
			fprintf(stderr, "GRATE_REGION needs rt4 and x,y,w,h inside the target\n");
			return 1;
		}

		if (host1x_bo_mmap(rt4->bo, &rmap) != 0 || !rmap) {
			fprintf(stderr, "region mmap failed\n");
			return 1;
		}

		r32 = rmap;
		for (yy = 0; yy < rh; yy++) {
			fprintf(stdout, "region4 %u:", yy);
			for (xx = 0; xx < rw; xx++) {
				uint32_t w = r32[(y0 + yy) * options.width +
						 x0 + xx];

				fprintf(stdout, " %08X", w);
				hash = (hash ^ w) * 16777619u;
				nz += !!w;
			}
			fprintf(stdout, "\n");
		}

		fprintf(stdout, "region4 %ux%u+%u+%u nz=%u digest=0x%08X\n",
			rw, rh, x0, y0, nz, hash);
		result = hash;
	}

	if (test.print_result)
		fprintf(stdout, "result 0x%08X\n", result);

	if (test.has_expected && test.expected_result != result) {
		for (i = 0; i < options.width * options.height; i += 4)
			fprintf(stderr, "%d: 0x%08X 0x%08X 0x%08X 0x%08X\n", i,
				fb_data[i], fb_data[i + 1],
				fb_data[i + 2], fb_data[i + 3]);

		dump_asm(vs, fs, linker);

		fprintf(stderr, "\ntest %s; %s; %s; failed: expected 0x%08X, got 0x%08X\n",
			test.vs_path,
			test.fs_path,
			test.linker_path,
			test.expected_result, result);

		ret = 1;
	}

	if (!test.test_only) {
		grate_swap_buffers(grate);
		grate_wait_for_key(grate);
	}

	grate_exit(grate);
	return ret;
}
