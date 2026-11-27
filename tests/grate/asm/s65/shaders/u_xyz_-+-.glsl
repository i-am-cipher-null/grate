precision mediump float;
uniform samplerCube tex;
uniform vec3 dir;
void main()
{
    gl_FragColor = textureCube(tex, vec3(-dir.x, dir.y, -dir.z));
}
