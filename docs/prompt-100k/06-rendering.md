# 第六卷　渲染管线、着色器、程序化纹理与性能

> 本项目坚持单文件、零依赖、`file://` 双击可运行。这意味着不能使用 Three.js、不能加载外部着色器文件、不能依赖 CDN，也不能使用 ES module。所有矩阵运算、着色器、纹理生成都内联手写。本卷给出扩展后的渲染架构，重点解决"真实银河系"带来的粒子密度、颜色与暗尘问题。

---

### 6.1 渲染架构总览

渲染分为五个子系统：

1. **几何与矩阵**：手写 4×4 列主序矩阵库，负责透视、视图、模型、法线矩阵。
2. **粒子系统**：银河粒子、暗尘、HII 区、恒星标记、本星系群代理、背景星空。全部使用 `gl.POINTS` 与点精灵。
3. **球体系统**：太阳、行星、月球、其他恒星。使用经纬球网格与传统顶点/片元着色器。
4. **纹理系统**：程序化生成银河盘纹理、行星表面、月球表面、恒星表面。生成过程分片异步，避免阻塞首屏。
5. **UI 层**：HTML/CSS 覆盖层，负责标签、面板、按钮、比例尺、帮助。WebGL 画布只负责三维内容。

绘制顺序在第二卷已定义。这里补充两个原则：

- 不透明球体先画，透明粒子后画。球体之间可以开启深度测试；粒子之间关闭深度写入，只做加色混合或反向混合，避免透明排序。
- 每个渲染通道结束时恢复混合状态与深度状态，防止上一个通道的 `blendFunc` 泄漏到下一个通道。这是 WebGL 最常见的"画面突然变亮/变暗"来源。

### 6.2 WebGL 上下文与能力检测

启动流程：

```
canvas = document.getElementById('scene');
gl = canvas.getContext('webgl2', opts) || canvas.getContext('webgl', opts);
if (!gl) { showFallback(); return; }
```

`opts` 建议：

```
{
  alpha: false,
  antialias: true,
  depth: true,
  stencil: false,
  premultipliedAlpha: false,
  preserveDrawingBuffer: false,
  powerPreference: 'high-performance'
}
```

注意：

- `preserveDrawingBuffer: false` 可以提升性能，但截图必须在同一帧绘制完成后同步调用 `canvas.toBlob`，不能等到下一帧。
- `antialias: true` 在部分集成显卡上开销较大。若检测到低画质或移动端，可关闭 MSAA，改用高像素比 + 后期抗锯齿。
- WebGL2 优先，但着色器必须兼容 GLSL ES 1.00，避免维护两套。若使用 WebGL2 专有特性（如 `textureLod`、`drawArraysInstanced`），必须提供 WebGL1 回退路径。
- 浮点纹理（`OES_texture_float`）不是必需；银河盘纹理使用 `UNSIGNED_BYTE`，避免兼容性问题。
- 实例化（`ANGLE_instanced_arrays`）可选，粒子数量不大时直接用一个 VBO 更简单。

能力检测结果存入 `caps` 对象，供画质系统使用：

```
caps = {
  webgl2: !!gl2,
  maxTexture: gl.getParameter(gl.MAX_TEXTURE_SIZE),
  maxVertexAttribs: gl.getParameter(gl.MAX_VERTEX_ATTRIBS),
  pointSizeRange: gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE),
  dpr: window.devicePixelRatio || 1
};
```

如果 `maxTexture < 2048`，银河盘纹理降到 1024；如果点尺寸上限小于 32，粒子最大尺寸相应调低，避免被驱动截断。

### 6.3 矩阵库

沿用现有的手写矩阵库，但补两个函数：

- `M4.invert(out, a)`：用于把屏幕坐标反投影到世界坐标，支持点击拾取。
- `M4.lookAt(out, eye, center, up)`：用于恒星系统与地月系的相机。
- `M4.fromRotationTranslationScale(out, quat, pos, scale)`：用于星系代理的朝向与大小。

矩阵必须是列主序，并且 `multiply` 不能使用共享的临时数组导致重入错误。现有实现用 `M4._t` 临时数组，在嵌套调用时会出错。修正方式：`multiply` 内部使用局部 `Float32Array(16)`，或提供一个显式的 `multiplyInto(out, a, b, tmp)`。性能敏感的热路径可以复用一个专用的临时数组，但必须保证同一时刻只有一个 `multiply` 在写它。

视图矩阵的构造顺序：`view = lookAt(eye, target, up)`，模型矩阵 `model = translate * rotate * scale`，MVP = `proj * view * model`。不要在图元之间复用 MVP 而不重新计算。

### 6.4 粒子着色器

银河粒子使用点精灵。顶点属性布局建议：

```
location 0: position   vec3   (12 bytes)
location 1: color      vec3   (3 bytes, normalized UNSIGNED_BYTE)
location 2: size       float  (1 byte, normalized)
location 3: brightness float  (1 byte, normalized)
```

总步长 20 字节，按 4 字节对齐。顶点着色器：

```glsl
precision highp float;
attribute vec3 aPosition;
attribute vec3 aColor;
attribute float aSize;
attribute float aBrightness;

uniform mat4 uView;
uniform mat4 uProj;
uniform float uPixelRatio;
uniform float uAtten;
uniform float uExposure;
uniform float uTime;
uniform float uSpin;

varying vec3 vColor;
varying float vEnergy;

void main() {
  vec3 p = aPosition;
  // 银河自转：绕 Y 轴旋转
  float c = cos(uSpin);
  float s = sin(uSpin);
  p = vec3(p.x * c + p.z * s, p.y, -p.x * s + p.z * c);

  vec4 viewPos = uView * vec4(p, 1.0);
  gl_Position = uProj * viewPos;

  float dist = max(-viewPos.z, 1.0);
  float pointSize = aSize * uPixelRatio * (uAtten / dist);
  gl_PointSize = clamp(pointSize, 1.0, 64.0);

  // 能量补偿：远处小点保留可见亮度，但不无限增强
  vEnergy = aBrightness * uExposure * (1.0 + 0.6 * clamp(1.0 - dist / uAtten, 0.0, 1.0));
  vColor = aColor;
}
```

片元着色器：

```glsl
precision mediump float;
varying vec3 vColor;
varying float vEnergy;

void main() {
  vec2 uv = gl_PointCoord * 2.0 - 1.0;
  float r2 = dot(uv, uv);
  if (r2 > 1.0) discard;

  // 指数衰减，中心亮、边缘柔和
  float alpha = exp(-r2 * 3.2) * (1.0 - r2);
  vec3 color = vColor * vEnergy;

  gl_FragColor = vec4(color, alpha);
}
```

混合模式：星光使用加色混合 `gl.blendFunc(gl.SRC_ALPHA, gl.ONE)`，让重叠的星点自然增亮。暗尘使用反向混合 `gl.blendFunc(gl.ZERO, gl.ONE_MINUS_SRC_ALPHA)`，或用乘法混合 `gl.blendFunc(gl.DST_COLOR, gl.ZERO)`，取决于实现。使用反向混合时，片元颜色应为暗褐色，alpha 为遮挡强度。

注意事项：

- `gl_PointSize` 在许多驱动上有上限（常见 63 或 255），必须 clamp，否则粒子会突然消失或变成方块。
- 点精灵在远处小于 1 像素时会被丢弃。使用 `max(pointSize, 1.0)` 保证可见，但会导致远处过亮；用 `vEnergy` 随距离衰减来抵消。
- 不要在片元着色器里用 `gl_PointCoord` 做复杂噪声，性能开销大。暗尘的噪声应在顶点属性或纹理中预计算。
- 加色混合下，大量粒子重叠会过曝。核球区域必须限制单个粒子的亮度，或使用色调映射（简单的 `1 - exp(-x)`）。

### 6.5 球体着色器

行星与恒星共用一套球体着色器，通过 uniforms 区分：

```glsl
precision highp float;
attribute vec3 aPosition;
attribute vec2 aUV;
attribute vec3 aNormal;

uniform mat4 uMVP;
uniform mat4 uModel;
uniform mat3 uNormalMat;

varying vec2 vUV;
varying vec3 vNormal;
varying vec3 vWorldPos;

void main() {
  vUV = aUV;
  vNormal = normalize(uNormalMat * aNormal);
  vec4 world = uModel * vec4(aPosition, 1.0);
  vWorldPos = world.xyz;
  gl_Position = uMVP * vec4(aPosition, 1.0);
}
```

片元着色器（简化版）：

```glsl
precision mediump float;
varying vec2 vUV;
varying vec3 vNormal;
varying vec3 vWorldPos;

uniform sampler2D uTex;
uniform vec3 uLightDir;
uniform vec3 uAmbient;
uniform vec3 uSunColor;
uniform vec3 uAtmosphere;
uniform float uHasAtmosphere;

void main() {
  vec3 n = normalize(vNormal);
  float ndl = max(dot(n, uLightDir), 0.0);

  // 柔和的晨昏线，避免硬切
  float diffuse = smoothstep(-0.08, 0.35, ndl);
  vec3 base = texture2D(uTex, vUV).rgb;
  vec3 color = base * (uAmbient + uSunColor * diffuse);

  // 大气边缘辉光
  if (uHasAtmosphere > 0.5) {
    float rim = pow(1.0 - max(dot(n, normalize(-vWorldPos)), 0.0), 2.0);
    color += uAtmosphere * rim * (0.3 + 0.7 * diffuse);
  }

  gl_FragColor = vec4(color, 1.0);
}
```

要点：

- 夜半球不能纯黑，`uAmbient` 至少 0.02–0.05，且可以略带蓝色。
- 晨昏线用 `smoothstep`，宽度约 0.1–0.2，模拟大气散射。
- 地球的大气辉光使用菲涅尔项，颜色淡蓝；月球无大气，`uHasAtmosphere = 0`。
- 行星纹理坐标要处理经度接缝，避免一条明显的竖线。
- 土星环需要单独的透明环面网格与着色器，按视角改变透明度（环的颗粒散射），不能只贴一张圆环图。

### 6.6 程序化纹理生成

银河盘纹理（可选）：

- 尺寸 2048×2048，`RGB` 或 `RGBA`，`UNSIGNED_BYTE`。
- 生成对数螺旋 + fBm 噪声的亮度场，颜色按半径与旋臂位置混合。
- 生成后上传为纹理，开启 mipmap 与各向异性过滤（若支持）。
- 纹理只作为粒子盘的底层柔光，不承担主要细节，强度 0.3–0.5。
- 生成过程每 32 行 `setTimeout(step, 0)` 分片，显示进度条。

地球纹理：

- 尺寸 1024×512（高画质 2048×1024）。
- 使用 3D fBm 噪声生成大陆：噪声值高于阈值为陆地，低于阈值为海洋。
- 陆地颜色按纬度与高度变化：赤道附近绿色，沙漠区域黄褐，高纬度针叶林深绿，极地白色。
- 海洋颜色从深海蓝到浅海青。
- 云层单独一张纹理，alpha 通道由 fBm 生成，叠加时缓慢旋转。
- 城市灯光可以单独一张纹理，只在夜半球显示。

月球纹理：

- 尺寸 1024×512。
- 基础灰色，叠加月海（大块暗斑）与高地（亮斑）。
- 陨石坑：随机放置 200–400 个圆，边缘亮、内部暗，大小按幂律分布。
- 第谷环形山的辐射纹可以用几十条淡淡的亮线表示。
- 月球正面与背面应有差异，但不必精确测绘；如果做背面，使用不同的随机种子生成。

行星纹理：

- 水星：灰色，密集陨石坑。
- 金星：黄白色云层，柔和条纹。
- 火星：红褐色，极冠，暗色沙丘。
- 木星：水平条纹 + 大红斑。
- 土星：淡金色条纹，较柔和。
- 天王星：淡青色，几乎无条纹。
- 海王星：深蓝色，少量条纹与大暗斑。

恒星纹理：

- 使用 3D 噪声生成米粒组织，颜色由光谱型决定。
- 红超巨星增加大面积对流斑；白矮星用极小的亮球，不强求纹理。

纹理生成的性能：所有纹理在启动时异步生成，先显示低分辨率版本，再替换为高分辨率。不要在每一帧生成纹理，也不要在主线程做超过 50ms 的同步计算。

### 6.7 暗尘与混合

暗尘带是银河真实感的关键。实现方式：

- 单独一层粒子，属性与星光类似，但颜色为暗褐，alpha 为遮挡强度。
- 混合模式：`gl.blendFunc(gl.ZERO, gl.ONE_MINUS_SRC_ALPHA)`，即 `dst = dst * (1 - srcAlpha)`。这会让背景按 alpha 变暗。
- 绘制顺序：在所有星光之后、UI 之前。暗尘必须能遮挡核球与旋臂，但不能遮挡太阳标记（标记最后画）。
- 暗尘粒子尺寸较大（普通星点的 2–4 倍），alpha 低（0.1–0.4），形成柔和遮挡。
- 暗尘的分布沿旋臂内侧，并在核球前方形成大裂缝。大裂缝可以在内部视角中特别加强。

一个常见错误：把暗尘画成黑色粒子并使用普通 alpha 混合。这样暗尘会变成"黑烟"，而不是"遮挡"。必须使用反向混合或乘法混合。

HII 区使用加色混合，颜色粉紫，尺寸较大，亮度高。它们应该画在暗尘之前还是之后？物理上 HII 区在暗尘附近，有些被遮挡，有些在前景。简化处理：先画 HII，再画暗尘，让暗尘能压暗部分 HII；同时让少量前景 HII 画在暗尘之后，形成层次。默认实现可以全部先画 HII 再画暗尘，视觉上已经足够。

### 6.8 背景星空与远景星系

背景星空是一层独立的点精灵，不随银河旋转，不参与拾取。它使用极低的亮度与极小的尺寸，颜色从冷白到淡黄。为了营造深度，可以分两层：近景背景星（稍亮、稍大）与远景背景星（极暗、极小）。

本星系群层的远景星系：3,000–5,000 个小椭圆或小螺旋，亮度极低，尺寸 2–8 像素。它们可以用一个简单的 billboard 着色器绘制，颜色偏暗红与暗蓝。不要在远景星系上使用高分辨率纹理，那会浪费性能。

### 6.9 辉光与色调映射

核球与恒星需要辉光，但完整 bloom 后处理成本较高。提供两级方案：

- **低成本**：在粒子/球体之后，叠加一个屏幕空间的径向渐变 billboard，尺寸为核球的 2–4 倍，颜色暖黄，透明度低。这只对核球与恒星有效，不处理所有亮部。
- **高成本（可选）**：降采样亮部 → 高斯模糊 → 叠加。仅在 high 画质开启，分辨率减半，模糊半径 4–8 像素。

色调映射：使用简单的 Reinhard 或指数映射，避免核球过曝成纯白：

```
color = color / (1.0 + color);
```

或：

```
color = 1.0 - exp(-color * exposure);
```

在片元着色器最后应用，或在后处理中统一应用。注意：加色混合的粒子在混合前不能做色调映射，否则失去叠加效果；应在混合后的全屏后处理中做。若没有后处理，则限制单个粒子的亮度上限。

### 6.10 抗锯齿与像素比

- 桌面端默认 `dpr = min(devicePixelRatio, 2)`。
- 移动端默认 `dpr = min(devicePixelRatio, 2)`，低画质降到 1.25 或 1。
- 高 DPI 屏幕上，粒子的 `gl_PointSize` 必须乘以 `dpr`，否则粒子会显得过小。
- 关闭 MSAA 时，可以开启一个轻量的 FXAA（可选），但不要为此引入外部依赖；手写 20 行 FXAA 即可。
- 行星边缘的锯齿比粒子更明显，可以给球体使用 MSAA 或更高的细分。

### 6.11 性能自适应

首屏后采样 120 帧，计算平均帧时间：

- 平均 > 1/20 秒（50ms）：降到 low。
- 平均 > 1/32 秒（31ms）：降到 mid。
- 只降不升，避免来回抖动；用户可以在设置中手动改回。
- URL 参数 `?quality=high|mid|low` 强制画质，优先级高于自动检测。

画质档位：

| 项目 | high | mid | low |
|---|---|---|---|
| 银河粒子比例 | 100% | 65% | 35% |
| 暗尘 | 开 | 开（50%） | 关 |
| HII 区 | 开 | 开 | 关 |
| 星团 | 开 | 开 | 关 |
| 银晕 | 全量 | 50% | 20% |
| 辉光 | 开 | 简化 | 关 |
| 像素比上限 | 2 | 1.5 | 1 |
| 球体细分 | 128×64 | 64×32 | 32×16 |
| 纹理尺寸 | 2048 | 1024 | 512 |

低画质不是"能跑就行"，而是在关闭细节的同时保留结构可读性：核球、棒、四条旋臂、太阳标记必须仍然可见。

### 6.12 无头验证与截图

自动化验证使用 Playwright 或 Chrome headless：

```
chrome --headless=new --disable-gpu --enable-unsafe-swiftshader \
       --virtual-time-budget=8000 --screenshot=out.png file:///.../index.html
```

注意事项：

- 无头环境下 `requestAnimationFrame` 会被节流，虚拟时间预算内可能只推进几帧。需要注入一个探针，手动调用渲染函数。
- CSS transition 不随虚拟时间推进，演示模式的透明度截图可能显示 UI 未隐藏。这不是产品 bug，测试时应注入 `* { transition: none !important; }`。
- WebGL 在 SwiftShader 下可用，但性能低；不要用无头的 FPS 作为性能依据。
- 截图必须在同一帧绘制完成后同步调用 `canvas.toBlob`，否则可能得到空白图像。
- 每个视角至少截三张：俯视、侧视、内部光带。分别检查结构、颜色、暗尘。

### 6.13 本卷验收标准

1. 粒子着色器在 WebGL1 与 WebGL2 下均能编译，无 GLSL 语法错误。
2. 暗尘使用反向/乘法混合，视觉上是"遮挡"而不是"黑烟"。
3. 行星夜半球保留微弱环境光，能看清轮廓。
4. 地球纹理的大陆、海洋、极冠可辨识；月球纹理有陨石坑与月海。
5. 核球不过曝成纯白，边缘有柔和辉光。
6. 高 DPI 屏幕上粒子不模糊、不变得过小。
7. 低画质模式仍能看清中央棒、四条旋臂与太阳标记。
8. 上下文丢失后能恢复，不白屏。
9. 首屏纹理生成不阻塞超过 100ms，有进度提示。
10. 1080p 下 high ≥ 30 FPS，mid ≥ 30 FPS，low ≥ 24 FPS。

本卷结束。第七卷将定义交互系统：相机操作、拾取、双击、键盘、触屏、演示模式、截图与帮助面板。
