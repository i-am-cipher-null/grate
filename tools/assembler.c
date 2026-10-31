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
				struct vs_mat4 *m = &test->vs_mat4s[test->vs_mat4s_nb];
				char *p = strchr(optarg, '=');
				unsigned n = 0;

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

		ctx->textures[0] = tex;
	}

	if (getenv("GRATE_STENCIL")) {
		stencil = host1x_pixelbuffer_create(grate->host1x,
						    options.width, options.height,
						    options.width,
						    PIX_BUF_FMT_S8,
						    PIX_BUF_LAYOUT_LINEAR);
		if (!stencil) {
			fprintf(stderr, "stencil buffer alloc failed\n");
			return 1;
		}
		grate_3d_ctx_bind_stencil_buffer(ctx, stencil);
		if (getenv("GRATE_STENCIL_TEST")) {
			grate_3d_ctx_perform_stencil_test(ctx, true);
			grate_3d_ctx_set_stencil_func(ctx,
				GRATE_3D_CTX_STENCIL_TEST_FRONT,
				GRATE_3D_CTX_STENCIL_TEST_ALWAYS,
				(unsigned)(getenv("GRATE_STENCIL_REF") ? atoi(getenv("GRATE_STENCIL_REF")) : 0), 0xff);
			grate_3d_ctx_set_stencil_ops(ctx,
				GRATE_3D_CTX_STENCIL_TEST_FRONT,
				GRATE_3D_CTX_STENCIL_OP_KEEP,
				GRATE_3D_CTX_STENCIL_OP_KEEP,
				GRATE_3D_CTX_STENCIL_OP_REPLACE);
		}
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

		rt5 = host1x_pixelbuffer_create(grate->host1x,
						options.width, options.height,
						options.width * rt5_bpp,
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
		grate_flush(grate);

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

			fprintf(stdout, "rt4: %08X %08X %08X %08X nz=%u first=%u\n",
				r32[0], r32[1], r32[2], r32[3], nz, first);
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
