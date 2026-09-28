import { canUseXR } from "./xr.js?v=4";

const VERTEX = `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;
layout(location=0) in vec2 corner;
layout(location=1) in uint splatId;
uniform sampler2D positions, velocities, covarianceA, covarianceB, harmonics;
uniform mat4 view, projection;
uniform vec3 eye;
uniform vec2 viewport, focal;
uniform float time, nearPlane, opacityFloor;
uniform int coefficients, shDegree;
uniform bool useVelocity;
out vec2 gaussian;
flat out vec4 color;
vec4 load(sampler2D tex, int i) {
  int width = textureSize(tex,0).x;
  return texelFetch(tex,ivec2(i%width,i/width),0);
}
vec3 shColor(int index, vec3 d) {
  float x=d.x,y=d.y,z=d.z,xx=x*x,yy=y*y,zz=z*z;
  float b[16];
  b[0]=0.28209479177387814;
  b[1]=-0.4886025119029199*y; b[2]=0.4886025119029199*z; b[3]=-0.4886025119029199*x;
  b[4]=1.0925484305920792*x*y; b[5]=-1.0925484305920792*y*z;
  b[6]=0.31539156525252005*(2.0*zz-xx-yy); b[7]=-1.0925484305920792*x*z;
  b[8]=0.5462742152960396*(xx-yy);
  b[9]=-0.5900435899266435*y*(3.0*xx-yy); b[10]=2.890611442640554*x*y*z;
  b[11]=-0.4570457994644658*y*(4.0*zz-xx-yy);
  b[12]=0.3731763325901154*z*(2.0*zz-3.0*xx-3.0*yy);
  b[13]=-0.4570457994644658*x*(4.0*zz-xx-yy);
  b[14]=1.445305721320277*z*(xx-yy); b[15]=-0.5900435899266435*x*(xx-3.0*yy);
  vec3 rgb=vec3(0.5);
  int bandCount=(shDegree+1)*(shDegree+1);
  for(int i=0;i<16;i++) {
    if(i>=bandCount) break;
    rgb += b[i]*load(harmonics,index*coefficients+i).xyz;
  }
  return max(rgb,vec3(0.0));
}
void main() {
  int id=int(splatId);
  vec4 p=load(positions,id), v=load(velocities,id), a=load(covarianceA,id), b=load(covarianceB,id);
  float dt=time-p.w;
  vec3 world=p.xyz+(useVelocity ? v.xyz*dt : vec3(0.0));
  vec3 center=(view*vec4(world,1.0)).xyz;
  float depth=-center.z;
  float opacity=max(opacityFloor,b.z*exp(-0.5*(dt/v.w)*(dt/v.w)));
  gaussian=corner; color=vec4(0.0);
  if(depth<=nearPlane || opacity<1.0/255.0) { gl_Position=vec4(2.0,2.0,0.0,1.0); return; }
  mat3 C=mat3(a.x,a.y,a.z, a.y,a.w,b.x, a.z,b.x,b.y);
  vec3 right=vec3(view[0][0],view[1][0],view[2][0]);
  vec3 up=vec3(view[0][1],view[1][1],view[2][1]);
  vec3 back=vec3(view[0][2],view[1][2],view[2][2]);
  // Bound the covariance Jacobian outside the frustum, as in the CUDA rasterizer.
  // Without this, near-camera offscreen centers produce screen-filling ellipses.
  vec2 halfFov=viewport/(2.0*focal);
  vec2 slope=clamp(center.xy/depth,(projection[2].xy-1.3)*halfFov,(projection[2].xy+1.3)*halfFov);
  vec3 jx=(focal.x/depth)*(right+slope.x*back);
  vec3 jy=(focal.y/depth)*(up+slope.y*back);
  float aa=dot(jx,C*jx)+0.3, ab=dot(jx,C*jy), bb=dot(jy,C*jy)+0.3;
  float mid=0.5*(aa+bb), delta=length(vec2(0.5*(aa-bb),ab));
  float l1=max(mid+delta,0.1), l2=max(mid-delta,0.1);
  vec2 axis=abs(ab)>0.000001 ? normalize(vec2(ab,l1-aa)) : (aa>=bb ? vec2(1,0) : vec2(0,1));
  vec2 offset=corner.x*sqrt(l1)*axis+corner.y*sqrt(l2)*vec2(-axis.y,axis.x);
  vec4 clip=projection*vec4(center,1.0);
  vec2 ndc=clip.xy/clip.w+offset*2.0/viewport;
  gl_Position=vec4(ndc,clip.z/clip.w,1.0);
  vec3 direction=world-eye;
  direction /= max(length(direction),0.000001);
  color=vec4(shColor(id,direction),opacity);
}`;

const FRAGMENT = `#version 300 es
precision highp float;
in vec2 gaussian;
flat in vec4 color;
out vec4 fragColor;
void main() {
  float radius=dot(gaussian,gaussian);
  if(radius>9.0) discard;
  float alpha=min(0.99,color.a*exp(-0.5*radius));
  if(alpha<1.0/255.0) discard;
  fragColor=vec4(color.rgb*alpha,alpha);
}`;

export class SplatRenderer {
  constructor(canvas) {
    this.canvas = canvas;
    const gl = (this.gl = canvas.getContext("webgl2", {
      alpha: true,
      antialias: false,
      depth: false,
      stencil: false,
      xrCompatible: canUseXR(),
    }));
    if (!gl)
      throw new Error(
        "WebGL2 is unavailable. Enable browser hardware acceleration or try a desktop browser.",
      );
    this.textures = [];
    this.program = gl.createProgram();
    for (const [kind, source] of [
      [gl.VERTEX_SHADER, VERTEX],
      [gl.FRAGMENT_SHADER, FRAGMENT],
    ]) {
      const shader = gl.createShader(kind);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        const error = gl.getShaderInfoLog(shader);
        gl.deleteShader(shader);
        throw new Error(`Shader compilation failed: ${error}`);
      }
      gl.attachShader(this.program, shader);
      gl.deleteShader(shader);
    }
    gl.linkProgram(this.program);
    if (!gl.getProgramParameter(this.program, gl.LINK_STATUS))
      throw new Error(gl.getProgramInfoLog(this.program));
    this.uniforms = Object.fromEntries(
      [
        "positions",
        "velocities",
        "covarianceA",
        "covarianceB",
        "harmonics",
        "view",
        "projection",
        "eye",
        "viewport",
        "time",
        "focal",
        "nearPlane",
        "opacityFloor",
        "coefficients",
        "shDegree",
        "useVelocity",
      ].map((name) => [name, gl.getUniformLocation(this.program, name)]),
    );
    this.vao = gl.createVertexArray();
    gl.bindVertexArray(this.vao);
    this.corners = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.corners);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-3, -3, 3, -3, -3, 3, 3, 3]),
      gl.STATIC_DRAW,
    );
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    this.order = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.order);
    gl.enableVertexAttribArray(1);
    gl.vertexAttribIPointer(1, 1, gl.UNSIGNED_INT, 0, 0);
    gl.vertexAttribDivisor(1, 1);
    gl.disable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.clearColor(0.027, 0.039, 0.047, 1);
  }
  makeTexture(values) {
    const gl = this.gl,
      max = gl.getParameter(gl.MAX_TEXTURE_SIZE),
      texels = values.length / 4;
    const width = Math.min(
      max,
      Math.max(1, 2 ** Math.ceil(Math.log2(Math.sqrt(texels)))),
    );
    const height = Math.ceil(texels / width);
    if (height > max)
      throw new Error(
        "This model exceeds this GPU's texture capacity.",
      );
    const texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA32F, width, height);
    const full = Math.floor(texels / width),
      tail = texels % width;
    if (full)
      gl.texSubImage2D(
        gl.TEXTURE_2D,
        0,
        0,
        0,
        width,
        full,
        gl.RGBA,
        gl.FLOAT,
        values.subarray(0, full * width * 4),
      );
    if (tail)
      gl.texSubImage2D(
        gl.TEXTURE_2D,
        0,
        0,
        full,
        tail,
        1,
        gl.RGBA,
        gl.FLOAT,
        values.subarray(full * width * 4),
      );
    if (gl.getError() !== gl.NO_ERROR) {
      gl.deleteTexture(texture);
      throw new Error("GPU upload failed. Try a smaller model.");
    }
    return texture;
  }
  setModel(model) {
    const textures = [];
    try {
      for (const array of [
        model.positionTime,
        model.velocityDuration,
        model.covarianceA,
        model.covarianceB,
        model.sh,
      ])
        textures.push(this.makeTexture(array));
    } catch (error) {
      textures.forEach((t) => this.gl.deleteTexture(t));
      throw error;
    }
    this.textures.forEach((t) => this.gl.deleteTexture(t));
    this.textures = textures;
    this.coefficients = model.coefficients;
    this.useVelocity = model.useVelocity;
    this.opacityFloor = model.opacityFloor;
  }
  beginXRFrame(layer) {
    const gl = this.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, layer.framebuffer);
    gl.disable(gl.SCISSOR_TEST);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
  }
  draw(order, camera, time, degree, resolution = 1, viewport = null) {
    const gl = this.gl,
      { canvas } = this;
    if (!viewport) {
      const ratio = Math.min(2, window.devicePixelRatio || 1) * resolution;
      const width = Math.max(1, Math.round(canvas.clientWidth * ratio)),
        height = Math.max(1, Math.round(canvas.clientHeight * ratio));
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.clearColor(0.027, 0.039, 0.047, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
      viewport = { x: 0, y: 0, width, height };
    }
    const { x, y, width, height } = viewport;
    gl.viewport(x, y, width, height);
    gl.useProgram(this.program);
    gl.bindVertexArray(this.vao);
    const u = this.uniforms;
    [
      "positions",
      "velocities",
      "covarianceA",
      "covarianceB",
      "harmonics",
    ].forEach((name, i) => {
      gl.activeTexture(gl.TEXTURE0 + i);
      gl.bindTexture(gl.TEXTURE_2D, this.textures[i]);
      gl.uniform1i(u[name], i);
    });
    gl.uniformMatrix4fv(u.view, false, camera.view);
    const f = 1 / Math.tan(camera.fov / 2);
    const projection = camera.projection ?? new Float32Array([
      f * height / width, 0, 0, 0, 0, f, 0, 0,
      0, 0, -1, -1, 0, 0, -2 * camera.near, 0,
    ]);
    gl.uniformMatrix4fv(u.projection, false, projection);
    gl.uniform3fv(u.eye, camera.eye);
    gl.uniform2f(u.viewport, width, height);
    gl.uniform1f(u.time, time);
    gl.uniform2f(u.focal, width * projection[0] / 2, height * projection[5] / 2);
    gl.uniform1f(u.nearPlane, camera.near);
    gl.uniform1i(u.coefficients, this.coefficients);
    gl.uniform1i(u.shDegree, degree);
    gl.uniform1i(u.useVelocity, this.useVelocity ? 1 : 0);
    gl.uniform1f(u.opacityFloor, this.opacityFloor);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.order);
    if (this.lastOrder !== order) {
      gl.bufferData(gl.ARRAY_BUFFER, order, gl.DYNAMIC_DRAW);
      this.lastOrder = order;
    }
    gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, order.length);
  }
  destroy() {
    const gl = this.gl;
    this.lastOrder = null;
    // A current program remains alive after deleteProgram until it is unbound.
    gl.useProgram(null);
    gl.bindVertexArray(null);
    gl.bindBuffer(gl.ARRAY_BUFFER, null);
    this.textures.forEach((t) => gl.deleteTexture(t));
    gl.deleteBuffer(this.order);
    gl.deleteBuffer(this.corners);
    gl.deleteVertexArray(this.vao);
    gl.deleteProgram(this.program);
  }
}
