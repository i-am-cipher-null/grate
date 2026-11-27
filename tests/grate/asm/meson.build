# Fragment asm encoding tests.  These do not touch the GPU: they assemble
# sources and compare word streams, so they run on any host with libgrate
# built.

asm_includes = include_directories(
	'../../../include',
	'../../../src/libgrate'
)

tooling_test = executable(
	'tooling_test',
	'tooling_test.c',
	include_directories : asm_includes,
	dependencies : math,
	link_with : [libgrate, libhost1x]
)

test('asm-tooling', tooling_test, args : [meson.current_build_dir()])
