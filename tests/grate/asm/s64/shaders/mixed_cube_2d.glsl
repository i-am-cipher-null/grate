precision mediump float;
varying vec3 vdir;
varying vec2 vst;
uniform samplerCube cubetex;
uniform sampler2D twodtex;
void main()
{
    gl_FragColor = textureCube(cubetex, vdir) + texture2D(twodtex, vst);
}
