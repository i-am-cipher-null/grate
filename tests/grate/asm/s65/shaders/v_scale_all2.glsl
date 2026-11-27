precision mediump float;
varying vec3 vdir;
uniform samplerCube tex;
void main()
{
    gl_FragColor = textureCube(tex, vdir * 2.0);
}
