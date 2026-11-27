precision mediump float;
varying vec3 vdir;
uniform samplerCube tex;
void main()
{
    gl_FragColor = textureCube(tex, vec3(vdir.y, vdir.x, vdir.z));
}
