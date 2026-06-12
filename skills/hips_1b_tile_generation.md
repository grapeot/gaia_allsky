# Skill: HiPS 十亿像素瓦片生成

把 6 亿星深星表渲成可在 Aladin Lite 里 zoom 漫游的十亿像素 HiPS 金字塔。
zoom in 看锐利单星、zoom out 看乳光银河，像在 DSS 巡天图上漫游。

## 何时用

需要 8K 以上、直到十亿像素级别的超大渲染时。单张巨图既打不开也没法分享，
改用 HiPS 金字塔 + Aladin Lite 浏览。8K 以下用单图即可，别上 HiPS。

## ⚠️ zoom-out 糊的真因 = Allsky 64px 粗预览（最大的坑，先读这个）

**现象**：在 Aladin Lite 里 zoom out（大 FOV，实测 50-67°）时画面发糊、星点/乳光劣化；
zoom in（FOV<25°）就清晰。

**真因（调研 + 浏览器 Network 实测确认）**：大 FOV 时 Aladin **不取全分辨率 512×512
瓦片，而是用 HiPS 的 `Allsky` 预览文件**——hipsgen 默认把 Norder3 的每个 512px 瓦片
降到 **64×64** 拼成一张图（8× 损失）。所以糊 = 你在看 64px 粗预览。浏览器 Network 面板
一看便知：糊的时候请求的是 `Norder3/Allsky.jpg`，不是 `NorderN/DirX/NpixY.jpg`。

**这条坑害我走了一整轮弯路**：曾以为糊是"hipsgen 对 8-bit 瓦片做 median 池化压星抹
乳光"，于是实现了整套 float 域三通道 HiPS（`--fits` + `build_rgb_float_hips.py`）。实测
两条路的低 Norder raw tile 平均差仅 ~16.5/255、zoom-out 观感**没有有意义的差别**——
**池化方法不是问题，Allsky 分辨率才是**。下方"两个致命物理 bug"那套"必须 float sum
池化"的理论，在实际渲染里没被证实，别据此去走 float（慢 3×、白费）。

**解法（最省，改动最小）**：HiPS 拼好后、部署前，用全分辨率 Norder3 瓦片重建一个
256px/tile（4×）的 Allsky 覆盖默认 64px 版：

```bash
python src/rebuild_allsky_hires.py --hips outputs/hips1b_out_bsc5   # 默认 256px/tile
```

实测 Aladin v2 加载后 zoom-out 明显变清晰（用户确认"比之前好太多"）。hipsgen 自己的
`ALLSKY` action 只出 64px、没有分辨率参数，所以必须这样手动重建。落地页/客户端不用改。

### 升 v3 的探索记录（更彻底的修法，落地页集成待续）

**已实测验证：v3 大 FOV 直接取全分辨率瓦片、绕过 Allsky，根治 zoom-out 糊。**
裸 v3 测试页加载我们的 HiPS，FOV 90°（比 v2 糊掉的 51-67° 还大）银河依然清晰；
浏览器 Network 确认：v3 在 FOV 90° 只取 2 次 Allsky 后就全是 `NpixXX.jpg` 全分辨率
瓦片（v2 在 50-67° 死守 Allsky 不取瓦片）。所以 v3 比"重建高分 Allsky"补丁更彻底，
连 rebuild_allsky_hires 都不需要。

**已验证的 v3 代码**（裸测试页跑通的确切写法）：
```html
<!-- 单 script，无 jQuery、无单独 css -->
<script src="https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.js"></script>
<script>
  A.init.then(function () {                                  // v3 init 是异步 Promise
    var aladin = A.aladin('#aladin-lite-div', { showReticle:false, cooFrame:'equatorial', fov:90 });
    var hipsDir = location.origin + location.pathname.replace(/[^/]*$/, "");  // 目录 URL
    var hips = A.HiPS(hipsDir, { imgFormat:'jpg', maxOrder:6, cooFrame:'equatorial' });  // createImageSurvey 已废，用 A.HiPS
    aladin.setImageSurvey(hips);
    aladin.gotoRaDec(270, -22); aladin.setFov(90);           // gotoRaDec/setFov v3 保留
  });
</script>
```
properties 不用改（hipsgen 已自动写 `dataproduct_subtype=color`）。v3 **必须 HTTP 不能
file://**（空天球头号坑）。

**未解的卡点（落地页集成留作单独任务）**：把上面 wiring 搬进**样式化落地页**
（hips_landing_page.html，带大量 CSS/DOM）后报 `Survey not found`、空天球，而**裸测试页同样
代码却成功**。根因未定死——疑似样式化页的 DOM/CSS/时序，或 v3 对部分天区 HiPS
（moc_sky_fraction=0.2，Norder0 只 6/12 格、Npix0 等 404）的探测在不同上下文行为不同
（补黑瓦片没解）。排查方向：裸页 vs 样式化页逐项 diff（先剥掉 CSS/其它 DOM 只留 viewer
div，再逐步加回，定位哪个元素/样式破坏加载）。

**现状**：落地页保持 v2 + 高分 Allsky（能用）。v3 根治结论已证明，落地页 v3 集成待续。

## 端到端流程与交接（谁做哪步）

有一个手动的 PixInsight 调色步骤夹在中间，agent 和用户分工明确：

1. **agent 渲 tiles** → `outputs/hips1b_tiles_bsc5/`（338 张 2048² PNG + .hhh）。
2. **用户在 PixInsight 批处理调色**（用 `skills/batch_process_frames.xpsm`），覆盖回 tiles。
   agent 在这一步停下等用户。
3. **用户说"处理好了"** → agent 跑 hipsgen 拼最终 HiPS 金字塔 + 改 `index.html` 指向新版。
4. **用户 rsync 到服务器**（yage，Cloudflare 后）。对 agent 透明。

下面分步给命令。

## 三步流程

### 1. 渲带 WCS 的 TAN 瓦片（我做）

```bash
source .venv/bin/activate
python src/render_tan_wcs.py \
  --data data/raw/fov_g20_bsc5.npz --out outputs/hips1b_tiles_bsc5 --tiles \
  --l-range=-41,79 --b-range=-31,43 \
  --tile-fov 6 --tile-step 5 --tile-size 2048 --workers 8
```

- 网格 25×15=375 格，约 338 张非空（落在广州 FOV 内的）。
- 每格一张 2048² PNG + 同名 `.hhh`（FITS WCS header），多进程并行，
  worker 数与 tile-size 不变则内存恒定（与瓦片总数无关），凑更高分辨率只需更多格。
- `fov=6/step=5` ≈ 十亿像素等效。放大就调小 tile-fov/step。

**坑（务必读）：**
- **后台启动必须在 wrapper 脚本里 `source .venv/bin/activate`**。`nohup python ...`
  不继承当前 shell 的 venv，会 `python: No such file or directory` 静默失败。
- **立体角归一化**是关键：星光是 flux 语义，每像素亮度随像素角面积变化，不同投影/
  分辨率下同一颗星每像素亮度不同（这是 TAN 图看着比广州地平图暗的真因）。
  `render_tan_wcs.py` 在累积后每像素除以像素立体角转面亮度，一套 tone 通用。
- **手性**：像素映射 `+xi`（东向）配 WCS `CDELT1<0` 表达"经度向左增"，只处理一次。
  两处都加负号 → Aladin 里左右镜像。
- **tone 亮度**：默认 tone 比"调亮过的成品"暗约一档（银心 tile 中位 ~82 vs 成品 ~110）。
  star_contrast 对 TAN 路径几乎无效，target_white 影响弱。最终亮度由用户在
  PixInsight 里调（见下）或后期 hipsgen 前处理。

### 2. PixInsight 批处理调色（用户做）

见 [zoom15_video_workflow.md](zoom15_video_workflow.md) 的 PixInsight 一节——
同一个 `batch_process_frames.xpsm` process icon（CurvesTransformation×2 + SCNR）
也可批量过 tile，统一提亮/调色/去绿。

### 3. hipsgen 拼金字塔（用户做）

需 `openjdk@11`（新版 JDK 不兼容旧 jar，Java 26 的 JApplet 已移除）。

```bash
/opt/homebrew/opt/openjdk@11/bin/java -Xmx80g \
  -jar outputs/tmp_reference_hips/AladinBeta.jar -hipsgen \
  in=outputs/hips1b_tiles_bsc5 out=outputs/hips1b_out_bsc5 color=jpeg \
  creator_did=DuckBro obs_title=GaiaMW1B \
  "target=271.672 -25.873" fading=true
```

- **`creator_did=DuckBro obs_title=GaiaMW1B` 必加**：这版 HipsGen 缺 ID 会直接报
  `*ERROR: Missing ID` 退出（README/working.md 早期命令漏了这两个，踩过）。沿用旧
  survey id `GaiaMW1B` 让 Aladin Lite wiring 不用改。
- `target` 放 FOV 中心（银道 5,-2.5 → 赤道 271.672,-25.873）。
- **`fading=true` 必加**：消重叠接缝。瓦片重叠带恰好是各自 gnomonic 边缘畸变最大处，
  默认 mean 混合会在亮处留可见接缝；fading 羽化过渡消除它。**不要用 `border=` 裁边**
  （裁过头露黑缝，更糟）。
- 产出 Norder0-6 七层金字塔、约 1.3 万瓦片、~1.2-1.4G、~16 分钟。
- **注意 `hips_hierarchy=median`**：Aladin 金字塔降采样用 median，对高分辨率多图会
  重蹈乳光丢失（见下"sum vs mean"）。若要严格物理正确，自己 sum 池化建层，别让
  hipsgen 做平均；当前成品接受 hipsgen 默认。

### 3.5 用样式化落地页覆盖默认 index.html（我做）

hipsgen 在输出根目录会生成一个**简陋的默认 `index.html`**（只有裸 Aladin viewer、
无初始视角、无样式）。必须用项目的样式化落地页覆盖它：

```bash
cp skills/hips_landing_page.html outputs/hips1b_out_bsc5/index.html
```

> reference 存在 **`skills/hips_landing_page.html`**（进 git）。不要从 `outputs/` 里那份
> 拷——`outputs/` 被 gitignore，且 regenerate HiPS 会覆盖丢失。skills 这份是权威存档。
>
> **落地页文案的唯一来源**：要改 HiPS 落地页的文字（标题/介绍/已知缺陷等），只改
> `skills/hips_landing_page.html` 这一份。两条 HiPS workflow 都从它注入：本 skill 的
> 老 JPEG 路（手动 `cp`）、`src/build_rgb_float_hips.py` 的 FITS 路（自动 `shutil.copy`），
> 改这一份两边自动同步。别在 `outputs/.../index.html` 上改（会被覆盖、且 gitignore）。
> 注意保持与主页 `docs/index.html` 叙事一致（如亮星已由 BSC5 补全，别再写"亮星缺席"）。

样式化落地页（暗色/金色风格）是**自包含、可移植**的：
- Aladin Lite v2 从 CDN 加载，无本地 css/js 依赖。
- HiPS 目录用 `hipsDir = location.href` 相对定位，复制进哪个 HiPS 根目录就指向同目录金字塔。
- **自带初始视角**：`aladin.gotoRaDec(270, -22); aladin.setFov(90)` + `fov: 90`（落银心、
  90° 起手视野）。默认页没有这个，复制过去才有正确的开场构图。
- survey id `GaiaMW1B` 与 properties 对得上，不用改。

复制后即整目录可部署，落地页和金字塔在同一目录、相对引用自洽。

### 3.6 重建高分辨率 Allsky（我做，**别忘**）

部署前必跑，否则 zoom-out 糊（见顶部"zoom-out 糊的真因"）：

```bash
python src/rebuild_allsky_hires.py --hips outputs/hips1b_out_bsc5
```

把默认 64px/tile 的 Allsky 重建成 256px/tile（4×）。备份原版到 `Allsky.jpg.orig64`。
RGB-float 路（`build_rgb_float_hips.py`）已把这步内置。

> **时序坑**：这步必须在**所有 hipsgen action 之后**跑。hipsgen 的 `ALLSKY`（以及
> `JPEG`/重建）action 会重新生成 64px Allsky、覆盖掉你的高分版。若部署前又跑过任何
> hipsgen，记得再跑一遍 rebuild_allsky_hires。

### 4. 部署（用户做）

HiPS 体量（>1G）超出 GitHub Pages。整个 HiPS 输出目录（含金字塔 + 覆盖后的 index.html）
rsync 到自有服务器（如经 Cloudflare），页面里 Aladin Lite 跨域加载时由 Cloudflare 加
`Access-Control-Allow-Origin` 头。低分辨率用 4K 单图 JPG 嵌主页，高分辨率指向 HiPS 落地页。

## 超大图的两个致命物理 bug（背景知识，别再踩）

渲超大图（12K→十亿像素）曾踩这两个坑，导致图比原生 1080 暗一个量级、tone 救不回：

1. **降采样语义错（mean vs sum）**：点源星光是 flux 语义（∝像素立体角 Ω），skyglow 是
   radiance 语义（常数底）。block-mean 守恒面亮度、把 flux 星光按 1/N 稀释 → 星光/天光
   比掉 N 倍。**正确是 block-sum 池化**（保光通量），sum 池化数学上等价于该分辨率直接
   从星表 binning，每层都是原生渲染。
2. **Weber 阈值误杀乳光**：`apply_extended_visibility_threshold` 在能量摊薄后把大片低
   对比乳光清零，发生在 tone 之前不可逆。**超大图渲瓦片时应关 Weber（ext-threshold 0）**，
   Weber 只在最终展示层按 bortle 施加。

**正确架构**：① 渲最高分辨率线性 float 画布（小 PSF 保锐星，不加 skyglow/Weber/tone）；
② sum 池化逐层建金字塔；③ skyglow + Weber + tone 在每层降采样后独立做，floor 用已知
常数对齐。绝不让 hipsgen 对 8-bit tone 过的瓦片做 mean（gamma 域 + clip 后能量不可逆，
zoom out 乳光出不来）。

**方法论铁律**：渲完图必 plot color histogram 和原生 1080 参照对比 p99/中位，对不上
别往下走。别用缩略图肉眼判断（会被骗）。

## 相关文件

- `src/render_tan_wcs.py` — TAN 投影 + 立体角归一化 + WCS 输出 + 瓦片模式
- `outputs/hips1b_tiles_bsc5/` — 当前 BSC5 cache 渲的瓦片
- `outputs/hips1b_tiles/` — 旧 1B 瓦片（用户调亮过的成品，亮度参照）
- `skills/AladinBeta.jar`（若挪入）或 `outputs/tmp_reference_hips/AladinBeta.jar`
