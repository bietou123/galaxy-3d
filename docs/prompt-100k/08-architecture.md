# 第八卷　数据模型、代码架构与单文件工程化

> 单文件不等于一团乱麻。`index.html` 会增长到数千行，如果没有清晰的数据与函数分层，后续每加一个星系都会牵动十处代码。本卷定义数据表结构、状态机实现、渲染循环组织、命名约定与单文件内的模块边界，目标是"新增一个星系只需改数据表"。

---

### 8.1 单文件约束下的分层

文件内部按下列顺序组织，每段用醒目的注释分隔，禁止交叉引用导致的隐式耦合：

```
1. <head>：meta、标题、内联 CSS
2. <body>：canvas、标签容器、面板、侧栏、帮助、加载条
3. <script>：
   3.1 常量与配置（CFG、LAYER、PHASE、LAYER_CAM）
   3.2 数据表（GALAXIES、STARS、PLANETS、MOONS、ARMS、SCIENCE_NOTES）
   3.3 工具函数（math、random、noise、color、dom）
   3.4 矩阵库（M4）
   3.5 WebGL 初始化与着色器源码
   3.6 缓冲区构建（buildGalaxy、buildLocalGroup、buildStars、buildSolar、buildEarthMoon）
   3.7 纹理生成（genGalaxyTex、genEarthTex、genMoonTex、genPlanetTex）
   3.8 状态机与相机（layerState、cam、tween、transitionTo）
   3.9 拾取与交互（pick、pointer、wheel、keyboard、touch）
   3.10 UI（labels、panel、sidebar、legend、scalebar、help、demo）
   3.11 渲染（drawLocalGroup、drawGalaxy、drawSolar、drawEarthMoon、frame）
   3.12 深链接与启动（parseHash、boot）
```

约束：

- 后段可以调用前段的函数，前段不得调用后段的函数（除了通过回调注册）。这避免"启动时调用渲染"这类隐形依赖。
- 数据表放在工具函数之前，且不依赖任何运行时状态，保证可以单独复制到测试脚本中验证。
- 不引入任何全局可变的"当前场景对象"，用显式的 `switch (layer)` 分派，便于静态检查。

### 8.2 配置表 CFG

配置按层级分组，每层独立，不共享易变的默认值：

```
var CFG = {
  build: {
    seed: 20261108,
    particlesHigh: 600000,
    particlesMid: 390000,
    particlesLow: 210000
  },
  galaxy: {
    wuPerLightYear: 1 / 20,
    diskRadius: 2500,
    bulgeRadius: 195,
    barA: 685, barB: 163, barC: 114, barAngle: 25 * DEG,
    thinHz: 49, thickHz: 195, hR: 570,
    armCount: 4, armPitch: 13 * DEG, armR0: 685,
    sunR: 1335, sunTheta: 2.30, sunY: 22,
    innerR: 250, outerR: 7000, defaultR: 3600,
    diveMinR: 120
  },
  localGroup: {
    wuPerLightYear: 1 / 1000,
    defaultR: 4500, minR: 2000, maxR: 12000,
    fov: 50 * DEG,            // 本层窄视场，退出时恢复 camera.fov
    minGlowPx: 7,             // 显示放大的最小柔光半径（像素）
    minGlowPxPerRadius: 0.22, // 随盘半径略增，保留成员之间的次序
    defaultTarget: [1000, 80, 720],
    defaultTheta: 1.02, defaultPhi: 1.19
  },
  starSystem: {
    defaultR: 80, minR: 2, maxR: 500
  },
  solar: {
    auScale: 30, auPow: 0.55,
    rScale: 1.9, rPow: 0.38,
    sunRadius: 5.0, daysPerSec: 30,
    defaultR: 360, minR: 18, maxR: 950,
    focusR: 42
  },
  earthMoon: {
    earthRadius: 1.0, moonRadius: 0.273,
    distance: 60.3, ecc: 0.0549, incl: 5.14 * DEG,
    daysPerSec: 1,            // 保留字段；实际时间尺度由 EM_DAY_SECONDS = 4 统一（见 5.10）
    defaultR: 130, minR: 2, maxR: 400,
    near: 0.05,               // 按层收紧的近裁剪，全局仍是 camera.near = 0.5
    fov: 55 * DEG,
    minPhi: 0.08, maxPhi: 1.45,
    enterTheta: 1.15, enterPhi: 1.12,
    moonVisualScale: 1.0,
    enterR: 8,                // 从太阳系聚焦地球后缩到这个半径就进地月系
    ambient: 0.155, atmoScale: 1.02
  },
  camera: { fov: 55 * DEG, near: 0.5, far: 50000 },
  tween: { layer: 1700, focus: 1200 }
};
```

原则：

- 所有角度以弧度存储，常量定义时用 `X * DEG` 转换，避免"这里写的是度还是弧度"的混淆。
- 所有世界单位明确归属某一层，命名带层级前缀，例如 `galaxy.diskRadius`、`earthMoon.distance`。
- 不在渲染函数里内嵌魔数。凡是在两处以上出现的数值，必须进 CFG。
- 画质档位不修改 CFG，而是通过 `qualityScale` 系数在构建与绘制时使用。
- **视场角按层切换，不存在第二套标定**。`camera.fov` 是基准值（55°），本星系群层在进入时把 `cam.fov` 换成 `localGroup.fov`（50°），退出时换回。投影矩阵、屏幕投影、拾取热区、标签避让都读同一个 `cam.fov`，切换在转场遮罩下完成，用户看不到跳变。新增层级若需要别的视场，只加一个 `xxx.fov`，不要再引入"某个函数里写死的角度"。
- **近/远裁剪面是跨层共享的，目前取 `near 0.5`、`far 50,000`**。规划时写的是 `near 0.01`、`far 200,000`，实际落地时收紧：`near` 太小会让银河层（尺寸以千 wu 计）的深度精度明显变差，而 `far` 只需覆盖本星系群层的 12,000 wu 视距加成员间距。地球半径定为 1 wu 之后，0.5 的 `near` 在地月系层近距离观察时可能裁掉地球本身（最小视距 2 wu），P5/P6 实现时必须实测；若确实裁切，按层设置 `near`，而不是把全局 `near` 再压小。**实测结论（P6）：确实会裁。** 聚焦月球时相机距离按 `max(半径 × 6 + 1.2, 2.4)` 取 2.84 wu，尚有余量；但 `cam.minR` 会降到 `max(0.6, 半径 × 1.6) = 0.6`，此时相机距月面只有 0.33 wu，0.5 的近裁剪直接把月球正面切掉。因此落地为 `CFG.earthMoon.near = 0.05`：进入地月系时把 `cam.near` 换成这一层的值，退出时恢复 `CFG.camera.near = 0.5`，投影矩阵与探针都读同一个 `cam.near`；全局 `near` 始终是 0.5，没有为地月系牺牲其他层的深度精度。

### 8.3 星系数据表

```
var GALAXIES = [
  {
    id: 'milkyway',
    name: '银河系',
    en: 'Milky Way',
    alias: 'Galaxy',
    type: 'SBbc 棒旋星系',
    distLy: 0,
    diamLy: [87400, 100000],
    stars: '1000–4000 亿',
    mass: '1–1.5 万亿',
    member: '本星系群',
    pos: [0, 0, 0],
    radius: 50,
    tilt: 0,
    colorCore: '#ffd08a',
    colorArm: '#a8c8ff',
    particles: 6000,
    intro: '……',
    note: '……',
    navigable: 'galaxy'
  },
  {
    id: 'm31',
    name: '仙女座星系',
    en: 'Andromeda Galaxy',
    alias: 'M31 / NGC 224',
    type: 'SA(s)b 旋涡星系',
    distLy: 2540000,
    diamLy: [140000, 220000],
    stars: '约 1 万亿',
    mass: '1–1.5 万亿',
    member: '本星系群',
    pos: [1980, 180, 1570],
    radius: 110,
    tilt: 77 * DEG,
    colorCore: '#ffe0a8',
    colorArm: '#9fc4ff',
    particles: 8000,
    intro: '……',
    note: '……',
    navigable: null
  }
];
```

字段约定：

- `id` 全小写、无空格，用于深链接与拾取，永远不改。
- `name` 是中文名，`en` 是英文或编号，`alias` 是可选的目录编号。
- `type` 使用标准分类字符串，不要在 UI 里二次翻译。
- `distLy`、`diamLy` 用数字，区间用二元数组，UI 统一格式化为"约 254 万光年"。
- `pos` 是世界坐标，单位由所属层决定。
- `radius` 是视觉半径，不是真实半径。
- `tilt` 是盘面相对视线的倾角，由模型矩阵应用。
- `navigable` 表示点击后能进入的层级；为 `null` 时只显示资料。
- `intro` 与 `note` 是完整中文段落，不在代码里拼接。

### 8.4 恒星与行星数据表

恒星：

```
var STARS = [
  {
    id: 'sol', name: '太阳', en: 'Sun', spectral: 'G2V',
    distLy: 0, mass: 1.0, radiusRsun: 1.0, tempK: 5772,
    planets: 8, level: 'A',
    pos: [1335, 22, 0],     // 银河层坐标（示意）
    color: '#ffe9b0',
    intro: '……'
  },
  {
    id: 'alphacen', name: '南门二（半人马座 α）', en: 'Alpha Centauri',
    spectral: 'G2V + K1V + M5.5Ve',
    distLy: 4.37, mass: 1.10, radiusRsun: 1.22, tempK: 5790,
    planets: 'A/B 无确认；比邻星 b、d',
    level: 'B',
    pos: [1400, 26, 120],
    color: '#fff0c8',
    intro: '……'
  }
];
```

`level` 决定渲染深度：A 完整、B 有确认行星轨道、C 只有候选虚线、D 只有恒星本体。

行星：

```
var BODIES = [
  {
    id: 'mercury', name: '水星', en: 'Mercury', kind: 'planet',
    radiusKm: 2439.7, au: 0.387, periodDays: 87.97,
    rotHours: 1407.6, moons: 0, tempC: [-173, 427],
    colorA: '#9a8f86', colorB: '#6e655d',
    texture: 'rocky',
    orbitGroup: 'inner',
    intro: '……', facts: ['……']
  }
];
```

统一用 `BODIES` 而不是分表，用 `kind` 区分 planet / moon / dwarf / asteroid。这样侧栏列表、拾取、渲染可以共用一套逻辑。地球与月球也在这张表里，`kind` 分别为 `planet` 与 `moon`，额外字段 `parent: 'earth'` 用于月球。

卫星单独字段：

```
{ id: 'moon', name: '月球', en: 'Moon', kind: 'moon', parent: 'earth',
  radiusKm: 1737.4, distKm: 384400, periodDays: 27.322,
  rotLocked: true, ecc: 0.0549, inclDeg: 5.14 }
```

### 8.5 层级参数表

每层的相机、比例、可见对象都集中在 `CFG` 的对应分组里。规划时另设过一张 `LAYER_CAM` 表，落地时合并进 `CFG`：两张表描述同一批数值，迟早会漂移，而所有入口函数本来就只读 `CFG`。

各层相机范围（进入时写入 `cam.minR/maxR/fov/near`，聚焦时只在局部收紧 `minR`，退出时恢复进入前保存的值）：

| 层 | minR | maxR | defaultR | 视场 | near |
|---|---|---|---|---|---|
| 本星系群 `CFG.localGroup` | 2000 | 12000 | 4500 | 50° | 0.5 |
| 银河系 `CFG.galaxy` | 250 | 7000 | 3600 | 55° | 0.5 |
| 恒星系统 `CFG.starSystem` | 2 | 500 | 80（按系统跨度算 `camR`，夹在 2–500） | 55° | 0.5 |
| 太阳系 `CFG.solar` | 18 | 950 | 360 | 55° | 0.5 |
| 地月系 `CFG.earthMoon` | 2 | 400 | 130 | 55° | **0.05** |

`fov` 按层区分是有必要的：内部光带视角需要更宽的视场，本星系群层需要稍窄以减少畸变；地月系沿用 55°，但在「真实比例」下默认视距 130 wu 才能让整条 60.3 wu 的月轨留在画面内。`near` 只有地月系与众不同，原因见 8.2 的实测结论。

### 8.6 状态机实现

状态机用两个变量而非一个：

```
var layer = LAYER.GALAXY;      // 当前层级
var phase = PHASE.IDLE;        // idle | transition | focus
var focusId = null;            // 聚焦对象 id
var stack = [];                // 返回栈
var queuedTransition = null;   // 过渡期间排队的目标（最多一个）
```

`transitionTo(target, opts)` 的完整流程：

```
function transitionTo(target, opts) {
  opts = opts || {};
  if (phase === PHASE.TRANSITION) {
    queuedTransition = { target: target, opts: opts };
    return;
  }
  if (!isValidTransition(layer, target)) {
    console.warn('invalid transition', layer, '->', target);
    return;
  }
  stack.push(snapshot());
  if (stack.length > 8) stack.shift();

  phase = PHASE.TRANSITION;
  var from = layer;
  var anchor = opts.anchorId || null;

  tweenCam(computeEntryCam(target, opts), CFG.tween.layer, easeInOutCubic);

  startCrossfade(from, target, CFG.tween.layer, function () {
    layer = target;
    focusId = opts.focusId || null;
    phase = PHASE.IDLE;
    updateHash();
    rebuildLabels(target);
    if (queuedTransition) {
      var q = queuedTransition;
      queuedTransition = null;
      transitionTo(q.target, q.opts);
    }
  });
}
```

要点：

- `phase` 在过渡开始即设为 `TRANSITION`，所有输入处理函数首行检查并决定是忽略还是排队。
- 返回值用快照对象，避免保存对可变全局对象的引用。
- 过渡完成回调只设置一次 `layer`，不允许在动画中途提前赋值，否则拾取与标签会读到中间态。
- `queuedTransition` 最多一个，后到的同目标忽略，不同目标替换。
- 非法转移只警告，不弹窗。

聚焦（`focusOn(id)`）是 `layer` 内部的状态变化，不改变 `layer`，只改 `focusId` 与 `phase = FOCUS`。退出聚焦时 `phase` 回到 `IDLE`，相机回到聚焦前的位置（用一个小快照栈保存，命名为 `focusStack`，与 `stack` 分开）。

### 8.7 相机对象与补间

```
var cam = {
  theta: 0, phi: 0, radius: 0,
  target: [0, 0, 0],
  // 目标值（输入写入这里）
  tTheta: 0, tPhi: 0, tRadius: 0,
  tTarget: [0, 0, 0],
  // 补间
  tween: null   // { from, to, t0, dur, ease, done, onDone }
};
```

补间只保留一个活动对象：

```
function tweenCam(to, dur, ease, onDone) {
  cam.tween = {
    from: { theta: cam.tTheta, phi: cam.tPhi, radius: cam.tRadius, target: cam.tTarget.slice() },
    to: normalizeTo(to),
    t0: performance.now(),
    dur: dur,
    ease: ease || easeInOutCubic,
    onDone: onDone || null
  };
}
```

每帧：

```
if (cam.tween) {
  var tw = cam.tween;
  var k = Math.min(1, (now - tw.t0) / tw.dur);
  var e = tw.ease(k);
  cam.tTheta  = lerp(tw.from.theta,  tw.to.theta,  e);
  cam.tPhi    = lerp(tw.from.phi,    tw.to.phi,    e);
  cam.tRadius = lerp(tw.from.radius, tw.to.radius, e);
  cam.tTarget = lerp3(tw.from.target, tw.to.target, e);
  if (k >= 1) {
    cam.tween = null;
    if (tw.onDone) tw.onDone();
  }
}
```

补间期间禁止相机输入写入 `tTheta/tPhi/tRadius/tTarget`；用户按 `Esc` 时清空 `cam.tween` 并调用其 `onDone` 的"取消"变体（或直接不调用，由状态机自行复位）。

### 8.8 缓冲区与绘制调用

每个渲染层是一个对象：

```
var GL_LAYER = function (name) {
  return {
    name: name,
    vbo: null, count: 0,
    mode: 'points',           // points | spheres | lines | billboards
    blend: 'add',             // add | multiply | alpha | none
    depthWrite: false,
    visible: function (layer) { return true; }
  };
};
```

绘制流程：

```
function drawPass(pass, camera, quality) {
  if (!pass.visible(layer)) return;
  gl.bindBuffer(gl.ARRAY_BUFFER, pass.vbo);
  setupAttribs(pass);
  useProgram(pass.program);
  setUniforms(pass, camera);
  setBlend(pass.blend);
  gl.depthMask(pass.depthWrite);
  gl.drawArrays(gl.POINTS, 0, Math.floor(pass.count * quality.pointRatio));
  restoreBlend();
}
```

每个 pass 结束必须恢复：

- `gl.disable(gl.BLEND)` 或在下一个 pass 前重新设置。
- `gl.depthMask(true)` 恢复深度写入。
- `gl.disableVertexAttribArray` 之后才切换到下一个缓冲区的属性布局。属性布局不同而忘记禁用，会导致下一个 pass 读到错误的数据，表现为"粒子变成一片乱点"。

### 8.9 着色器管理

着色器源码用模板字符串存为常量，编译函数集中：

```
function compile(type, src) {
  var sh = gl.createShader(type);
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    console.error(gl.getShaderInfoLog(sh), src);
    return null;
  }
  return sh;
}

function program(vsSrc, fsSrc) {
  var vs = compile(gl.VERTEX_SHADER, vsSrc);
  var fs = compile(gl.FRAGMENT_SHADER, fsSrc);
  var p = gl.createProgram();
  gl.attachShader(p, vs); gl.attachShader(p, fs);
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
    console.error(gl.getProgramInfoLog(p));
    return null;
  }
  return { id: p, u: cacheUniforms(p), a: cacheAttribs(p) };
}
```

uniform 位置提前缓存到对象里，不要每次绘制都 `getUniformLocation`。属性位置同样缓存。所有uniform名字集中在 `cacheUniforms` 的白名单里，避免拼写错误导致的静默失效。

### 8.10 数学与噪声工具

需要的最小集合：

- `clamp(v, a, b)`、`lerp(a, b, t)`、`smoothstep(a, b, x)`、`easeInOutCubic(t)`。
- `rand()`、`randRange(a, b)`、`gaussian()`（Box–Muller，缓存第二个值）。
- `mulberry32(seed)`：可复现的伪随机数生成器，替代 `Math.random()`，保证每次加载的银河一致。
- `hash3(x, y, z)`：确定性哈希，用于颜色微扰。
- `noise3(x, y, z)`：简化的值噪声，8 个角点哈希 + 三线性插值。
- `fbm3(x, y, z, octaves)`：`noise3` 叠加，用于纹理与旋臂亮度。
- `hexToRgb(hex)`：返回 [0..1] 的三元组，构建缓冲区时使用。

所有噪声函数必须无外部依赖、无状态、可在构建循环中调用数十万次而不产生 GC 压力。避免在噪声内部创建对象或数组。

### 8.11 构建函数与内存

粒子构建的输出是 TypedArray，不要先推到普通数组再转换（几百万次 push 会造成严重的内存抖动）。

预先计算粒子数 `n`，分配：

```
var pos = new Float32Array(n * 3);
var col = new Uint8Array(n * 3);
var misc = new Uint8Array(n * 2);   // size, brightness
```

写入用索引 `i3`、`im2`，最后一次性 `bufferData`。若某层采样被拒绝（如椭球采样），用 `while` 重试，但设置重试上限，避免死循环。

构建函数签名统一：

```
function buildArms(ctx, count) {
  var total = count;
  // 返回 { pos, col, misc, count }
}
```

`ctx` 携带 `rng`、`qualityScale`、`armParams`，避免函数读取全局状态，便于以后把数据表提取成独立测试。

### 8.12 命名与注释约定

- 全局只读常量全大写：`LAYER`、`PHASE`、`CFG`。
- 全局可变状态用简短小写：`cam`、`layer`、`phase`、`stack`。
- 函数名用动词开头：`build`、`draw`、`pick`、`focus`、`transition`、`update`、`resize`。
- 布尔量用 `is/has/can` 前缀：`isMoon`, `hasRings`, `canNavigate`。
- 每个主要函数上方写一行注释说明职责，不写"这个函数是干什么的"式废话，而是写"为什么这样"。
- 注释用中文，代码标识符保持英文。不在注释里留 TODO 之外的作者标记。

### 8.13 单文件的行数与可维护性

预估行数：

| 段 | 行数（估） |
|---|---|
| CSS | 900 |
| HTML | 250 |
| CFG 与数据表 | 900 |
| 工具与矩阵 | 400 |
| 着色器 | 350 |
| 构建函数 | 1200 |
| 纹理生成 | 900 |
| 状态机与相机 | 400 |
| 交互 | 600 |
| UI | 700 |
| 渲染 | 900 |
| 启动 | 150 |
| 合计 | 约 7,650 |

虽然大，但仍是单个文件。为了让编辑器与浏览器都能承受：

- 不在文件中嵌入 base64 图片或字体。所有视觉元素程序化生成。
- 不在文件中内嵌大段中文以外的大文本；介绍文案每条控制在 300 字以内。
- 保持缩进 2 空格，不使用制表符。
- 每个逻辑段之间留一行空行与一条 `// ---- 名称 ----` 分隔注释，便于折叠。

### 8.14 调试开关

在 URL 上加 `?debug=1` 时：

- 显示 FPS、粒子数、绘制调用数、相机半径与角度。
- 在控制台暴露 `window.__galaxy = { cam, layer, transitionTo, testPick, seed }`，供无头测试脚本驱动。
- 显示拾取命中点（红色小十字）。
- 允许 `?seed=N` 覆盖随机种子，便于复现。

`?debug=1` 不影响正常用户，不得在未经参数时暴露调试 UI。

### 8.15 本卷验收标准

1. 所有层级切换都经过 `transitionTo`，代码中不存在对 `layer` 的分散直接赋值（除初始化与过渡完成处）。
2. 新增一个星系只需在 `GALAXIES` 添加一条记录，不需要改渲染、标签与面板代码。
3. 新增一颗行星只需在 `BODIES` 添加一条记录。
4. 所有着色器 uniform 位置被缓存，绘制热路径中没有 `getUniformLocation`。
5. 构建缓冲区时不使用普通数组累积，峰值内存不超过 120MB。
6. 每个 pass 前后混合与深度状态一致，切换层级后画面亮度不出现跳变。
7. `?debug=1` 能显示 FPS 与相机参数，`?seed=N` 能复现同一银河。
8. 文件内的逻辑段顺序与 8.1 节一致，前段不调用后段。

本卷结束。第九卷将定义实施路线图：分阶段交付、每阶段可验证、风险与回滚、性能预算与里程碑。
