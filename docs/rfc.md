# RFC：实现设计

## 总览

渲染管线分为四层，层层分离，每层只做一件事：

1. **星表数据**：从 Gaia DR3 拿到恒星的基本物理量：位置、亮度、颜色。这是唯一涉及外部科学数据的层。
2. **物理投影**：把物理量变成屏幕上的位置和线性亮度。不涉及显示美学。
3. **显示映射（tone mapping）**：把线性亮度压到 SDR 屏幕能显示的范围。这是工程显示层，参数全部显式暴露，不会混进物理模型。
4. **输出包装**：把渲染结果写成 PNG/TIFF/MP4，为 GitHub Pages 生成压缩预览。

这样分层的好处是：修改显示风格不会影响星表查询，换一种投影也不需要改动 tone mapping。

星表数据来自 Gaia DR3。基础全天图使用 `src/fetch_gaia_allsky.py` 获取银道坐标、G 星等和 BP-RP 颜色；3D 飞行使用 `src/fetch_gaia_3d.py` 获取 RA/Dec、视差和 G 星等。

物理投影把恒星放到屏幕上。全天图支持 equirectangular（用于 VR 视频）和 Mollweide（用于静态全天地图）；地面视角先把银道坐标转为赤道坐标，再按观测纬度和地方恒星时转为地平坐标；飞行视频先把视差转为三维笛卡尔坐标，再从移动观测者位置重投影。

显示映射把线性亮度压到 SDR。正式图显式记录 reference、white percentile、PSF 和 gamma。这些是显示参数，不是物理常数。

输出包装负责生成 PNG/TIFF/MP4 和 GitHub Pages 所需的压缩资产。

## 星等与颜色

星等转亮度使用 Pogson 公式：

```text
L = 10 ^ (-0.4 * (m - m_ref))
```

星色使用 BP-RP 或 B-V 的简化映射。它不是精确色彩科学，但足以让蓝白星和橙红星在大尺度图里产生可见差异。

## Bortle 和 NELM

Bortle 暗空等级通过天空面亮度转换成线性 skyglow。skyglow 是加性背景：

```text
observed = stars + skyglow
```

NELM 使用经验表锚定：Bortle 1 约 7.8，Bortle 6 约 5.3。正式视觉图中，处在有效极限星等的星被设为当前 skyglow 的固定点源对比。`+2mag` 表示有效极限星等在当前 Bortle baseline 上提高 2 等。

## 正式视觉图的 tone mapping

正式视觉图使用 `render_bortle_eye_grid.py`。

处理步骤：

1. 每个 panel 单独估计低百分位 sky floor。
2. 把 sky floor 映射到固定深灰。
3. sky floor 以上的信号乘 `star_contrast`。
4. 用 shared reference panel 计算 signal stretch。
5. 所有 panel 使用同一个 stretch，再做 gamma 输出。

这样做有两个目的。第一，背景适应符合人眼/相机直觉。第二，高光污染 panel 不会因为独立 normalization 被强行拉亮。

视觉层使用双层 PSF：

- `point_psf_px` 保留亮星点源。
- `psf_px * diffuse_strength` 形成低频银河结构，避免网页缩略图里银河消失。

## Bortle scale reference

Bortle 1-9 图是同一脚本的另一种参数配置，使用 `--reference-bortle 1 --reference-value 2`。这不是改变物理星等，而是用 Bortle 1/+2mag 那个更可读的显示 reference 来校准整张 scale grid。Bortle 7-9 仍然按同一 stretch 变暗。

## 飞行视频

两条视频共享同一条空间轨迹：先朝北斗方向短距离前进，再飞向银心方向上方的目标点。VR 版输出全天 equirectangular；forward 版输出透视相机，并绘制北斗连线帮助观察星座形状变化。

所有视频先渲染 PNG 帧，再用 ffmpeg 合成。这样中间结果可检查，也方便后续用 H.264/H.265 重新编码。

## 数据边界

Gaia 可见光视差是局部数据球，不是银河系全盘。因此飞出数据球后看到的是数据边界，而不是银河系真实俯视图。代码和文档必须保留这个边界，不能把局部数据渲染包装成银河全貌。

## 公开发布策略

Git 仓库只放代码、文档、测试和小型网页资产。大 Gaia 缓存、完整渲染帧、完整视频输出不进入 Git。GitHub Pages 使用压缩 JPEG/WebP/MP4 预览和可选下载链接。
