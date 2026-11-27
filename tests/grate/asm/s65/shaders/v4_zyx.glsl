precision mediump float;
varying vec4 vdir;
uniform samplerCube tex;
void main()
{
    gl_FragColor = textureCube(tex, vec3(vdir.zyx));
}
