DIR=$(dirname $0)

	$DIR/../../tools/assembler --vs $DIR/asm/vs_mov.txt       --fs $DIR/asm/fs_vs_tests.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0xFF01FF00 --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_constant.txt  --fs $DIR/asm/fs_vs_tests.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0x664D331A --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_branching.txt --fs $DIR/asm/fs_vs_tests.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0xFF01FF00 --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_function.txt  --fs $DIR/asm/fs_vs_tests.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0xFF01FF00 --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_stack.txt     --fs $DIR/asm/fs_vs_tests.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0xFF01FF00 --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_predicate.txt --fs $DIR/asm/fs_vs_tests.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0x667F007F --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_pred_le.txt --fs $DIR/asm/fs_vs_tests.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0x00FFFF00 --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_pred_ne.txt --fs $DIR/asm/fs_vs_tests.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0xFFFF00FF --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_pred_ge.txt --fs $DIR/asm/fs_vs_tests.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0xFF00FFFF --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_branch_notaken.txt --fs $DIR/asm/fs_vs_tests.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0x4C4C4C4C --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_txl.txt --fs $DIR/asm/fs_vs_tests.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0xFF000000 --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_constant_relative_addressing.txt  --fs $DIR/asm/fs_vs_tests.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0x664D331A --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_attribute_relative_addressing.txt --fs $DIR/asm/fs_vs_tests.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0xFF01FF00 --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_export_relative_addressing.txt    --fs $DIR/asm/fs_vs_tests.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0x6601FF1A --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_uniform.txt  --fs $DIR/asm/fs_vs_tests.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0x664D331A --vs_uniform '["uniform_100"]=(0.1,0.2,0.0,0.0)' --vs_uniform '["uniform_135"]=(0.0,0.0,0.3,0.4)' --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_mov.txt  --fs $DIR/asm/fs_uniform.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0x19334C66 --fs_uniform '["uniform_0_low"]=0.1' --fs_uniform '["uniform_2"]=0.2' --fs_uniform '["uniform_31_low"]=0.3' --fs_uniform '["uniform_31_high"]=0.4' --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_mov.txt  --fs $DIR/asm/fs_mfu_tests.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0x7F6A7FE0 --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/filter_quad_vs.txt --fs $DIR/asm/filter_quad_fs.txt --lnk $DIR/asm/filter_quad_linker.txt --expected 0x00000000 --texture --mipmap --vs_mat4 '["mvp"]=(1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1)' --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_mov.txt   --fs $DIR/asm/tex_cross_batch.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0xFFFFFFFF --texture --vs_mat4 '["mvp"]=(1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1)' --fs_uniform '["u0"]=0.7' --fs_uniform '["u1"]=0.1' --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/filter_quad_vs.txt --fs $DIR/asm/tex_explicit_lod.txt --lnk $DIR/asm/filter_quad_linker.txt --expected 0x00545454 --texture --mipmap --vs_mat4 '["mvp"]=(1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1)' --fs_uniform '["lod"]=2' --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_mov.txt --fs $DIR/asm/perf/fill_4b_12p.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0x40404040 --fs_uniform '["u0"]=0.25' --testonly \
&&	$DIR/../../tools/assembler --vs $DIR/asm/vs_mov.txt --fs $DIR/asm/perf/cost_mfu_12b.txt --lnk $DIR/asm/linker_vs_tests.txt --expected 0x40404040 --fs_uniform '["u0"]=0.25' --testonly \
&&	echo "All tests passed"
