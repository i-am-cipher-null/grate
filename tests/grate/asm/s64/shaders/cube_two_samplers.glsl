precision mediump float;
varying vec3 vdir;
uniform samplerCube tex;
uniform samplerCube tex2;
void main()
{
    gl_FragColor = textureCube(tex, vdir) + textureCube(tex2, vdir);
}
