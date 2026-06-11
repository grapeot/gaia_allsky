# Skill: HiPS 十亿像素瓦片生成

把 6 亿星深星表渲成可在 Aladin Lite 里 zoom 漫游的十亿像素 HiPS 金字塔。
zoom in 看锐利单星、zoom out 看乳光银河，像在 DSS 巡天图上漫游。

## 何时用

需要 8K 以上、直到十亿像素级别的超大渲染时。单张巨图既打不开也没法分享，
改用 HiPS 金字塔 + Aladin Lite 浏览。8K 以下用单图即可，别上 HiPS。

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
cp outputs/_hips1b_out/index.html outputs/hips1b_out_bsc5/index.html
```

样式化落地页（`outputs/_hips1b_out/index.html`，暗色/金色风格）是**自包含、可移植**的：
- Aladin Lite v2 从 CDN 加载，无本地 css/js 依赖。
- HiPS 目录用 `hipsDir = location.href` 相对定位，复制进哪个 HiPS 根目录就指向同目录金字塔。
- **自带初始视角**：`aladin.gotoRaDec(270, -22); aladin.setFov(90)` + `fov: 90`（落银心、
  90° 起手视野）。默认页没有这个，复制过去才有正确的开场构图。
- survey id `GaiaMW1B` 与 properties 对得上，不用改。

复制后即整目录可部署，落地页和金字塔在同一目录、相对引用自洽。

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
