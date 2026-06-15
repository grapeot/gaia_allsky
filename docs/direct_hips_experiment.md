# Direct HiPS Experiment Information Pack

本文档封存 2026-06-14 的 direct HiPS 实验。它面向未来接手的 AI agent：目标不是给读者讲故事，而是把问题背景、设计假设、实验路径、代码位置、失败边界和复现实验集中放在一个地方。当前项目的稳定生产路径仍是 **TAN/WCS 渲染 + hipsgen 生成 HiPS**；direct HiPS 是一次性能优化实验，已经合入若干有价值的不变量保护，但整体暂时不作为继续调参的主线。

## 1. 原始动机

十亿像素级 HiPS 渲染的瓶颈不在我们自己的星表 rasterization，而在 hipsgen 的重投影。即使在 M3 Ultra 上，目标约 1.5 arcsec/pixel 的银河区域生成 HiPS 也会让 hipsgen 花数小时；全天会更慢。Profiling 和对照实验显示，hipsgen 的核心成本来自它对每个输出 tile 像素做 backward gather：取输出 HiPS 像素中心，反投影回源 TAN 图，再 bilinear sample。

这个设计对一般天文图像合理。DSS、FITS cutout、已有 survey image 都是 raster image，只有 backward resample 才能保证目标 grid 每个像素有值。但本项目的输入不是观测相机图像，而是点源星表。我们理论上不需要先在 TAN 平面烤成 raster，再让 hipsgen 反查；可以把每颗星直接 forward 投到 HiPS tile 的 HEALPix 像素，跳过 TAN 中间产物和 hipsgen 重投影。

预期收益是数量级级别的加速：点源 forward splat 天然可并行，每个 tile 只需要从星表筛选候选星、计算目标像素、累积 PSF 和 tone mapping。早期单 tile 测试给出过 35 tile/s vs hipsgen 7 tile/s，完整 pipeline 在理想情况下可能从数小时降到分钟级。

## 2. 稳定生产路径

当前稳定路径仍然是：

1. 用 `render_tan_wcs.py` 把星表渲成带 WCS 的 TAN tile。
2. 用 hipsgen 读取 PNG + `.hhh` WCS header，生成标准 HiPS 金字塔。
3. 用 Aladin Lite 浏览 HiPS。

这条路径慢，但几何语义清楚：hipsgen 负责 HEALPix/HiPS 的 pixel orientation、tile boundary、Allsky、properties 和 MOC。它的 backward gather 会把 TAN 平面里正常圆形的 PSF 采样到 HEALPix 参数平面中；文件里的星可能是椭圆，但 Aladin 把 HiPS tile 显示回球面时会重新变圆。

`docs/rfc.md` 只描述这条生产路径。direct HiPS 相关实验和失败边界统一放在本文档，避免把实验假设混进项目主架构。

## 3. Direct HiPS 的核心设计

direct renderer 的目标是把点源直接写到最终 HiPS tile：

1. 对每个 Norder 收集覆盖目标视野的 HEALPix cell。
2. 对每个 cell 作为一个 512x512 tile 渲染。
3. 星表从 Galactic `(l,b)` 转 ICRS `(ra,dec)`。
4. 使用 `cdshealpix.lonlat_to_healpix(..., return_offsets=True)` 得到 order-K owner cell 和连续 `dx/dy`。
5. 将 `dx/dy` 映射到 tile 像素，注意 HiPS 写盘方向是 `row <- dx`、`col <- dy`。
6. 对普通星做 subpixel bilinear splat + PSF；对亮星做局部 window bloom。
7. 加 skyglow、tone mapping、PixInsight 风格调色，写 `NorderK/DirD/NpixN.jpg`。

相关代码：

1. `src/render_hips_direct_pipeline.py`：direct HiPS 主实验实现。
2. `src/render_tan_wcs.py`：亮星 `_bright_star_wings`，后来扩展为支持 per-star shear。
3. `src/rebuild_allsky_hires.py`：用 Norder3 tile 手工重建高分辨率 `Allsky.jpg`。
4. `tests/test_render.py`：direct HiPS 的几何和边界不变量测试。

## 4. 关键几何事实

HEALPix tile 的二维参数平面是等面积但不保角。实测 tile 像素网格在球面上的两轴夹角约 77.7°，不是 90°。这导致一个反直觉现象：

1. 如果文件里的 PSF 是像素平面圆，Aladin 显示回球面后会变成椭圆。
2. hipsgen 文件里的点源常常是椭圆，因为 backward sampling 把源图圆 PSF 烤进了 HEALPix 参数平面。
3. 这些文件内椭圆在 Aladin 里反而可能显示成圆。

因此不能简单用文件里的圆/椭圆判断对错。判断时必须区分三层：

1. 文件像素平面的形状。
2. 经过 HEALPix tile-to-sky 映射后的球面形状。
3. Aladin 端实际显示出来的形状。

早期误判包括：把 Aladin 显示端的形变当成 Aladin bug，把整数 splat 和内存量化当成根因，把 hipsgen 文件椭圆当成错误。最后定位到 HEALPix 参数平面 shear 后，才有了 local shear 补偿方案。

## 5. 已经尝试过的修复路径

### 5.1 基础 direct renderer

最早版本用 `healpy.ang2pix(nside=2^(K+9))` 找到 order-(K+9) 子像素，再用 `healpy.pix2xyf` 映射到 tile 内坐标。它解决了 z-order 和 row/col 转置问题，和 hipsgen 同 npix 文件整体相关性较高，但 zoom-out/zoom-in 都有明显几何问题。

关键教训：`healpy` 给的是离散 child pixel，不给连续 owner cell offset。对 0.6px PSF 来说，整数子像素会带来块状/梳状采样伪影。

### 5.2 局部 shear 椭圆核

加入 `_local_shear_matrices(npix,korder,dx,dy)`，用 HEALPix 参数平面到局部切平面的雅可比 `J`，去掉面积缩放后得到归一化 shear。理想的像素空间核是 `J^-1` 椭圆高斯，这样映射回球面接近圆。

验证结果：单星 probe 在球面上可以做到轴比约 1.0，说明核方向和量是对的。但真实 tile 里还有饱和盘、cell 内 shear 梯度、tile 边界和采样语义问题。

### 5.3 低 order 暗星层 local shear

用户观察到低阶 zoom-out 形变不只是亮星，暗星和乳光纹理也被 HEALPix shear 拉斜。于是 N3-N5 的普通星/乳光层改为 block-local inverse-shear PSF。用户反馈形变有改善。

实现方式：把 tile 分成 64px block，每块用 block center 的 local shear 生成一个椭圆核，block 内普通星先 bilinear splat 到小画布，再卷积。

风险：block-local 是近似。相邻 tile 边缘使用的 block center 不一样，仍可能留下 seam 或 PSF 形状差异。

### 5.4 N6-N8 普通星也做 shear

用户确认 zoom-in 星点椭圆仍明显，于是普通星 sheared PSF 扩展到 N6-N8。这样 N8 也要椭圆卷积，速度从约 118 tile/s 降到约 72 tile/s，但用户认为 zoom-in 星点椭圆问题改善。

### 5.5 亮星 bloom 跨 tile

旧逻辑只画中心落在本 tile 的亮星。中心在相邻 tile 的亮星，其光晕落进当前 tile 时会被整块裁掉，Aladin 中表现为沿 HEALPix 边界的扇形截断。修复后，亮星可以通过 same-face continuous/tile-offset 坐标贡献到当前 tile 的扩边画布。

这一类问题是明确 bug，保留修复有意义。

### 5.6 连续 `dx/dy` 和 bilinear splat

普通星从 `healpy` 离散 child pixel 改为 `cdshealpix.lonlat_to_healpix(..., return_offsets=True)` 的连续 `dx/dy`，并用 `_bincount_bilinear` 做 subpixel splat。这样消除了整数格点导致的一部分块状/梳状 PSF。

这一类不变量也值得保留，因为它是 forward renderer 正确性的前提。

### 5.7 低 order tile guard band

half=5 测试区在 N3/N4 这种粗 tile 上会漏掉视野边缘邻居。Aladin zoom-out 取低阶 tile 时会看到黑缝。修复为 `_tile_search_radius(half,korder)=max(half*1.5, half + tile_fov)`，低阶增加一个 tile-width guard band。

这项修复明确解决了覆盖问题。half12 验证中，新坐标 `17:25:21.134 -43:27:49.31` 在 N3-N8 自 tile 和 8 邻域均齐全。

### 5.8 N5 seam 追加保护

用户后来指出三个 N5 seam：

1. `17:48:09.057 -45:03:21.48`
2. `17:25:14.033 -43:28:41.8`
3. `17:37:31.102 -41:53:04.36`

三个点都在同 face 的 N5 tile 右边缘 2-7px 内，邻 tile 齐全。我们尝试了两个理论修复：

1. 普通星中心在 tile 外时，仍允许 PSF 翼卷回当前 tile。
2. off-tile 贡献从 Norder(K+9) 整数子像素改为 `cdshealpix` 连续 `dx/dy` 加整数 tile offset，保证同一颗星在相邻 tile 的坐标只差 512px 的整数 tile offset。

这两个不变量是正确的，测试已加入；但它们没有改善三条 seam 的文件级边缘跳变。因此这三条 seam 的主因不是缺 tile、不是普通星翼被裁，也不是 off-tile 坐标量化混用。

## 6. hipsgen 官方实现的相关事实

官方源码在 Aladin source jar 中。关键文件：

1. `cds/allsky/ThreadBuilderTile.java`
2. `cds/tools/hpxwcs/Tile2HPX.java`
3. `cds/allsky/Context.java`

核心语义：

1. 对每个输出 tile pixel `(x,y)`，hipsgen 先用 `context.xy2hpx[y * width + x]` 得到 tile 内 child HEALPix offset。
2. 全局 child index 是 `npix_file * tileSide^2 + offset`。
3. `Healpix.getNestedFast(order + tileOrder).center(index, radec)` 取 child cell center。
4. 这个球面点反投影回源图 WCS。
5. 从源图做 bilinear sample。

边界处理：

1. source image/cell 边界附近允许略微越界，避免 bilinear footprint 被过早判 invalid。
2. cell 边界上会复制最后一个 pixel，避免读取邻 cell 时重复或缺值。
3. 若 bilinear 四点中部分 blank，则用第一个 valid pixel 填补 blank；全 blank 才返回 NaN。
4. 多个 source image 覆盖同一输出像素时按权重合成，并对 cell overlap 边界做 divisor，避免重复计权。

对 direct renderer 的启示：hipsgen 的稳定锚点不是 tile-local raster 操作，而是全局 HEALPix child pixel center。forward splat 如果要达到同等 seam 稳定性，最好也把目标写入全局 HEALPix child grid，或者实现一个等价的 backward/gather 验证路径。

## 7. 当前结论

direct HiPS 的大方向仍然合理：输入是点源星表，绕过已有 raster 图像的 backward resample 在理论上有性能优势。但当前实现还没有达到生产质量，主要卡在两个问题上。

第一，PSF 采样语义不稳。文件里的椭圆一部分是 expected，因为 Aladin 显示端会把 HEALPix shear 映回球面；但用户截图里的块状、斜向马赛克感不是理想 PSF。它更像 forward splat + undersampled elliptical kernel 的采样伪影。

第二，N5 seam 仍存在。覆盖缺失、跨 face、off-tile wing 和坐标混用都已排除。剩余嫌疑是 forward 局部卷积和 hipsgen backward gather 的语义差异，或者 block-local shear 近似在 tile 边界不连续。

因此这个 debug 阶段到这里收束。继续局部调参数收益不高。未来如果重启，应先建立一个更小、更确定的验证基线：用人工星表、单个 N5 邻 tile pair、全局 HEALPix child grid，对比 forward splat、backward gather 和 hipsgen 的输出，而不是继续在真实银河密集场上肉眼调。

## 8. 未来重启的建议路线

### 8.1 最小复现实验

建立一个脚本，例如 `tools/probe_direct_hips_sampling.py`，不要读完整 Gaia 星表，只构造几颗人工星：

1. 一颗普通暗星放在 N5 tile 右边界外 0.2px。
2. 一颗普通暗星放在 N5 tile 右边界内 0.2px。
3. 一颗中亮星放在同样位置。
4. 一颗亮星放在跨 tile 需要 bloom 的位置。

对同一个邻 tile pair 输出：

1. direct forward splat 当前实现。
2. global HEALPix child-grid accumulator。
3. backward gather reference：对每个输出 pixel center 计算它到人工星中心的局部球面距离，再评价 PSF。

比较指标：

1. 左右 tile seam 两侧 8px strip 的亮度剖面。
2. 单星在球面切平面中的轴比。
3. 文件像素平面的 expected ellipse 参数。
4. 同一颗星从左右 tile 渲染出来的中心一致性。

这个实验的成功标准不是好看，而是能把 seam/PSF 问题从真实星场里剥离出来。

### 8.2 推荐技术路线

优先考虑 global HEALPix child-grid anchoring：

1. 对目标 order K，把渲染实际锚到 order K+9 的 child grid。
2. 星的 PSF footprint 覆盖哪些 child pixel，就写哪些 global child index。
3. 最后按 `npix = child // 512^2`、`offset = child % 512^2` 分发到 tile。
4. tile 边界自然连续，不需要 per-tile halo 补丁。

如果继续使用 tile-local forward splat，至少要做到：

1. 所有 in-tile/off-tile 坐标来自同一套 continuous owner-cell + tile-offset 语义。
2. 普通星、亮星、bloom 都支持 subpixel center。
3. block-local shear 要么变成逐星/逐输出窗口 shear，要么在边界处使用与邻 tile 一致的采样点。
4. 不要再用真实密集星场作为第一验证对象。

### 8.3 性能注意事项

`cdshealpix.healpix_to_lonlat` 的 Python binding 在大量小调用和多进程下可能创建 Rust thread pool，`block=16` 临时实验曾触发 `Resource temporarily unavailable`。如果未来要做更细 block 或逐星 shear，应考虑：

1. 批量调用，减少 Python loop。
2. 缓存 block center 的 shear。
3. 限制 worker 数或禁用内部多线程。
4. 用 pure healpy / astropy-healpix 做局部差分时要先验证连续性和方向。

## 9. 复现命令与产物索引

当前重要输出都在 `outputs/`，不进 Git。常见命令如下：

```bash
source .venv/bin/activate

# RA=17h Dec=-45 的 half12 direct HiPS 实验区
python src/render_hips_direct_pipeline.py \
  data/raw/gaia_allsky_g20_bsc5_hpx6.npz \
  341.643613 -1.624362 12 "3 4 5 6 7 8" \
  outputs/direct_ra17_dec-45_half12_shear6/hips 32

# 补 Allsky
python src/rebuild_allsky_hires.py \
  --hips outputs/direct_ra17_dec-45_half12_shear6/hips \
  --order 3 --per 256

# 本地预览
python3 -m http.server 8787
```

历史预览端口：

1. `8783`：shear3，普通星 sheared PSF 扩展到 N6-N8。
2. `8784`：shear4，亮星 bloom 跨 tile 截断修复。
3. `8785`：shear5，普通星 continuous dx/dy + bilinear splat。
4. `8786`：shear6，低 order guard band。
5. `8787`：half12 shear6，大覆盖验证区。

用于回归的问题点：

1. `17:25:21.134 -43:27:49.31`：half=5 内部点，half12 中 N3-N8 邻域齐全。
2. `17:48:09.057 -45:03:21.48`：N5 右边缘 seam。
3. `17:25:14.033 -43:28:41.8`：N5 右边缘 seam。
4. `17:37:31.102 -41:53:04.36`：N5 右边缘 seam。

## 10. 已保留的代码价值

虽然 direct HiPS 暂停，但这次工作留下了有用代码和测试：

1. `cdshealpix` continuous dx/dy 的使用方式。
2. HEALPix local shear 矩阵和文件内椭圆/显示端圆的诊断逻辑。
3. per-star bright-wing shear 支持。
4. bilinear subpixel splat。
5. low-order tile search guard band。
6. off-tile PSF wing 不应被 tile-local binning 裁掉的测试。
7. adjacent same-face tile 坐标必须只差整数 tile offset 的测试。

这些不变量未来继续做 direct renderer 时仍然有用。当前不要把它们理解为 seam/PSF 已解决，只能理解为排除了一批错误路径。
