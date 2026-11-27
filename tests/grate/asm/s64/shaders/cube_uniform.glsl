precision mediump float;
uniform samplerCube tex;
uniform vec3 dir;
void main()
{
    gl_FragColor = textureCube(tex, dir);
}
