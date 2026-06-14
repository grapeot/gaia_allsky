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

## ⚠️ 暗区"沿银河方向的斜杠子" = 瓦片 tone 用了 per-tile 自适应（第二大坑）

**现象**：HiPS 拼好后，银河带**两侧的暗空区**出现一组斜向、平行、扇形发散的浅色条纹
（投影后沿银河方向）；亮带本身看不出。规则的横竖网格不是它——那才是渐晕。

**真因**：`render_tan_wcs.render_tile` 早期直接调 `beg.tone_adapted`，而 `tone_adapted`
是**单张图自适应**链——sky-floor 取本张 tile 的 25 百分位、white-point 取 99.5 百分位。
含银河带多的 tile 这两个分位更高，标定不同，**同一片天在相邻两张里被映射到不同亮度**。
实测相邻 tile 重叠区（同一片天）背景差 **32%**，拼接即成接缝。

**关键诊断手法（别重蹈我误判三次的覆辙）**：渐晕、Gaia 扫描条带、bloom 视场外缺失我
都先后猜错。一锤定音的判据是**量相邻 tile 重叠区**（fov=6/step=5 有 1° 重叠，是同一片
天）：raw canvas（accumulate + 立体角归一化，bloom/tone 之前）块间比值 **1.00**（几何层
干净），artifact 全在 tone 链。padding 重渲（补 bloom 视场外贡献）只把 33%→30%，证明
不是 bloom。

**解法（已落地，源头消除，重渲一次即可）**：tile 路径不调 `tone_adapted`，改为直接调底层，
传**全局固定标定**——`adapt_sky_floor(sky_anchor=rh.additive_skyglow_level(bortle)*3)`（物理
天光底作 floor，块间同一基准）+ `finish_sky_adapted(..., TILE_STRETCH)` 固定 white-point
stretch（`TILE_STRETCH=1.0`，hero +6mag 下背景已满，per-tile stretch 本就 clamp 到 ~1）。
验证：重叠区差 **32% → 0.1%**。`adapt_sky_floor` 的 `sky_anchor` 参数本来就是为"块间一致 /
sweep 路径"设计的，tile 路径漏用了才退回 per-tile percentile。

**⚠️ sky_anchor 量纲坑（必须 ×3）**：`adapt_sky_floor` 内部拿 sky_anchor 跟 `canvas.sum(-1)`
（三通道和）比，而 `add_skyglow` 给 RGB 每通道各加 `additive_skyglow_level`，所以暗空背景
的 sum 是它的 **3 倍**。anchor 漏乘 3 会把黑场锚高 3×（scale 偏大）→ 整图背景被向上推、
暗空发灰发蒙（现象像"整体亮度被垫高"，不是过曝削顶——两种发白机制不同）。修正 ×3 后暗空
tile 背景 p5 从 64→22。这个 bug 是固定标定的连带：per-tile 自适应在时会自动补偿掩盖它，
固定标定后才显形。

**⚠️ 演进（最终解法见步骤 1 的标定流程）**：上面 `TILE_STRETCH=1.0 + sky_anchor=天光底×3`
是早期固定标定，能消接缝但对比平（复刻不了 hero 的暗压亮提）。**最终方案是「全天 tone 标定」**
（`calibrate_alltile_tone.py` 产出 calib JSON，render 加 `--calib`）：sky_anchor 用相同 fov/size
**实测**暗空 canvas sum（不是解析的天光底×3）、配 `star_contrast=4` + `stretch=1.0`，既消接缝
又复刻 hero 对比。**正式渲染走 --calib**，本节的 ×3/TILE_STRETCH 是无 calib 时的保守兜底。

改完必须**全量重渲 338 张**
再重拼金字塔。

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

### 1. 全天 tone 标定 + 渲带 WCS 的 TAN 瓦片（我做）

**先标定、再渲染。** 标定算出全天固定的 (sky_anchor, star_contrast, stretch) 冻结成 JSON，
渲染时所有 tile 用同一组 → 复刻 hero 单图的对比观感（暗压亮提）且块间一致（无接缝）。

```bash
source .venv/bin/activate
# [0] 全天 tone 标定（与全量渲染用相同 tile-fov/tile-size！sky_anchor 依赖归一化 norm）
python src/calibrate_alltile_tone.py --data data/raw/fov_g20_bsc5.npz \
  --tile-fov 6 --tile-size 2048 --value 6 --target-sky 0.020 \
  --star-contrast 4 --target-white 2.6 --out outputs/alltile_calib.json
# [1] 渲 tile（用 calib，floor/对比/白点全用冻结值）
python src/render_tan_wcs.py \
  --data data/raw/fov_g20_bsc5.npz --out outputs/hips1b_tiles_hero --tiles \
  --l-range=-41,79 --b-range=-31,43 \
  --tile-fov 6 --tile-step 5 --tile-size 2048 --workers 8 \
  --value 6 --calib outputs/alltile_calib.json
```

**为什么要标定（接缝 vs 对比的矛盾，踩了一整轮才理清）：**
- hero 单图（render_fov）好看，是因为 tone_adapted 在**整幅图上算一次** floor/white-point。
  瓦片若让每张各自 percentile 估，含银带多的 tile 标定不同 → 沿银河方向接缝。
- 解法：全天用一组固定 (sky_anchor, star_contrast, stretch)。这三个值就是 calib JSON。
- **真根因细节**：hero 用 render_fov（**无**立体角归一化），tile 用 render_tan_wcs（**有**，
  ×REF_OMEGA/cdelt²≈×50）。两边喂进 tone 的 canvas 量级差 ~50 倍，所以 **hero 的 stretch
  数值不能直接搬到 tile**（搬了银带爆 50 倍）。必须用 tile 自己的归一化 canvas 实测 anchor。

**标定产出的三个值（hero 同款观感）：**
- `sky_anchor`：用相同 fov/size 在高纬暗空点实测的 canvas sum p25（**依赖 fov/size！**
  fov6/512→3.8, fov20/650→4.1, fov6/2048→2.7。calibrate 和 render 的 fov/size 不一致会
  报错拒跑）。它把黑场锚到 target_sky，复刻 hero 暗空（PNG 暗 p5≈26）。
- `star_contrast=4`：暗压亮提的主旋钮。sc=6 银心略过曝、sc=1 太平，**sc=4** 银心亮 p99≈214
  不爆、暗空 p5≈26——最贴 hero。
- `stretch=1.0`：归一化后亮部已足，白点拉伸退到下限即可。

**其它要点：**
- `--value 6`（敏感度 +6mag，暗星增益 ~250×）和 `--target-sky 0.020` 是 hero 同款，不要漏。
  无标定时（不传 --calib）退回保守路径：物理天光底×3 作 floor + TILE_STRETCH=1.0，无接缝
  但对比平、不复刻 hero。**正式渲染务必走标定路径。**
- 网格 25×15=375 格，约 338 张非空。每格 2048² PNG + `.hhh`(WCS header)，多进程并行，
  内存随 worker 和 tile-size 恒定（与瓦片总数无关）。`fov=6/step=5` ≈ 十亿像素等效。

**坑（务必读）：**
- **sky_anchor 依赖 fov/size**：统计量对 size 「不敏感」只对**分布形状**成立；anchor 是
  canvas **绝对量级**，含立体角归一化 norm=REF_OMEGA/cdelt²，随 fov/size 变。所以标定必须
  用与全量渲染相同的 tile-fov/tile-size（render 会校验，不一致直接报错）。
- **后台启动必须在 wrapper 脚本里 `source .venv/bin/activate`**。`nohup python ...`
  不继承当前 shell 的 venv，会 `python: No such file or directory` 静默失败。
- **手性**：像素映射 `+xi`（东向）配 WCS `CDELT1<0`，只处理一次。两处都加负号 → 左右镜像。
- PixInsight 这步（下一步）留作色温/去绿精修，**不是救对比**——对比由标定的 star_contrast 定。

### 2. 调色（色温/去绿精修）——默认 Python 复现，免 PixInsight

**默认走 Python**（`src/pi_curves_scnr.py`）：render_tan_wcs 渲完直接在 worker 里用 numpy 复现
`batch_process_frames.xpsm`（CurvesTransformation B/K/b* + SCNR），**无需 PixInsight、随渲染
多进程并行、省一次读写**。render_pipeline.sh / render_tan_wcs 默认就做（`--color-xpsm` 指定
xpsm，`--color-xpsm none` 跳过）。所以**这步通常不用单独跑**——渲出来的 tile 已调好色。
与真 PI 逐像素 eval：mean≈3.6/255、p99≈11，视觉等价（见 pi_curves_scnr 注释 + test_pi_curves_scnr）。
没装 PixInsight 的机器（如渲染用的 Linux）走这条，整条 pipeline 纯 Python。

**可选：真 PixInsight 批处理**（要逐像素和 GUI 一致、或调更复杂的 xpsm 时）：
```bash
python tools/pixinsight_batch.py --xpsm skills/batch_process_frames.xpsm \
  --in outputs/hips1b_tiles_hero --in-place --workers 8 --slot-base 200 --resume
```
**色温/去绿精修，不救对比**（对比由上一步标定的 star_contrast 定）。详见
[bestpractice_pixinsight_batch.md](bestpractice_pixinsight_batch.md)。注意 shm 坑（下）。

**⚠️ shm 段耗尽坑（batch「卡死一半 worker」的真根因）**：每个 PixInsight 实例占 1 个 SysV
共享内存段，macOS 系统全局上限 `kern.sysv.shmmni=32`。崩溃/被 kill 的实例**泄漏僵尸段不释
放**，反复跑后累积占满 32 → 新实例 `QSharedMemory::create: out of resources` 启动即崩、无
done 标记 = 表现为「卡死」（误判过 slot/WebEngine，都不是）。`pixinsight_batch.py` 已内置：
跑前清无附着的 PI shm 段 + 余量不足自动降并发 + 收尾清理。若仍触顶，手动清
`ipcs -m | awk '$1=="m"&&$5=="<user>"{print $2}' | xargs -n1 ipcrm -m`（确认 PI 全退后），
或 `sudo sysctl -w kern.sysv.shmmni=128` 抬上限。

### 3. hipsgen 拼金字塔（用户做）

**默认本机跑**（下面命令）。**大规模时（几万~几十万 tile、十几小时）可外包给更强的 Linux
机器**——见 3.4，sync 整个 repo 过去那边渲+拼。规模判断：万级 tile 本机
（数十分钟~数小时）够；全天 1.5arcsec/px（~22 万 tile）建议外包。

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
- **`fading=true` 必加**：羽化重叠过渡，消**亮处**的混合硬边；默认 mean 混合会在亮处留
  可见接缝。**不要用 `border=` 裁边**（裁过头露黑缝，更糟）。注意 fading 只处理混合边，
  **救不了暗区的 per-tile tone 接缝**（那是渲染层的标定不一致，见上"第二大坑"，必须在
  render_tile 用全局固定 sky_anchor + stretch 解决）。
- 产出 Norder0-6 七层金字塔、约 1.3 万瓦片、~1.2-1.4G、~16 分钟。
- **注意 `hips_hierarchy=median`**：Aladin 金字塔降采样用 median，对高分辨率多图会
  重蹈乳光丢失（见下"sum vs mean"）。若要严格物理正确，自己 sum 池化建层，别让
  hipsgen 做平均；当前成品接受 hipsgen 默认。
- **⚠️ 高分辨率源必加 `hips_order=N`（否则 hipsgen 过采样、时间数倍）**：hipsgen 不限 order
  时按源像素密度**自动选深一层**。1.5 arcsec/px 源会被选到 Norder9（0.8 arcsec/px），对源
  2× 过采样插值（无新信息、放大反而糊）、瓦片 ×4、**12h vs 3.5h**。显式 `hips_order=8`
  （Norder8≈1.6 arcsec/px 匹配源）画质不损、省 75%。判据：Norder N 的 arcsec/px =
  `sqrt(4π/(12·4^N))·(180·3600/π)/512`，选最接近源 arcsec/px 的 N（1.5→8, 6→6, 10→5/6）。
  配 `maxthread=32` 吃满核。低分辨率源（1B 的 10 arcsec）不加也行（自适应到 Norder6）。

### 3.4 在更强的机器上渲染：sync 整个 repo（不打包）

大规模时（全天 1.5arcsec/px ~22 万 tile，渲染+hipsgen 十几小时）外包给更强的机器。**不打
专用包**——代码本就在 repo，直接 rsync 整个工作区过去（排除大产物），那边重建 venv 跑现成脚本。
比传渲好的 tiles 轻得多：全天 tiles ~513G vs 分桶 npz 16G + 代码。

```bash
# 本机：sync repo（排大目录，只带代码 + 要用的分桶 npz，~17G）
rsync -av --progress \
  --exclude='outputs/' --exclude='.venv/' --exclude='__pycache__/' \
  --exclude='data/raw/flatiron_gaia_source_fov_gz/' \
  --include='data/raw/' --include='data/raw/<分桶 npz>' --exclude='data/raw/*.npz' \
  ./ user@host:/path/repo/

# 目标机：重建 venv + 跑（渲染链纯 numpy/scipy/PIL/astropy，无 macOS 依赖）
python3 -m venv .venv && source .venv/bin/activate
pip install numpy scipy Pillow astropy astropy-healpix
sudo apt install openjdk-11-jdk            # hipsgen 必须 JDK 11
# 标定→渲→PI(可选,装了PI才有)→hipsgen→allsky，按 W/XMX/MAXTHREAD 调机器：
bash tools/build_hips_pipeline.sh <tag> <tile-fov> <tile-step> <tile-size> <workers>
# 或手动分步（calibrate_alltile_tone → render_tan_wcs --calib → hipsgen hips_order=8 → rebuild_allsky）
```

要点：分桶 npz（build_healpix_bucketed.py 产出）跨机直接用；hipsgen 须 JDK 11 + AladinBeta.jar
（在 outputs/tmp_reference_hips/，sync 时按需带上或目标机另放）；hips_order=8 别漏（防过采样）；
长任务 zellij/tmux 持久 pane 或 nohup。这就是「换台机器渲染 = sync repo」的通用形态，不需专用包。

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
