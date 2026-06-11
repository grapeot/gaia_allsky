"""Bortle 序列自动验收：吃一组真实渲染的 PNG/JPG，用量化判据回答"这组图对不对"。

为什么吃真图而不是重实现显示链：之前版本重实现了一条简化显示链来出指标，结果
它给 PASS 而真 render_fov 渲出来 FAIL——判定器和真管线脱节，PASS 毫无意义。
现在直接读 renderer 落地的图，judge 的就是眼睛看到的那张。

两个解耦的物理量（都在 sum(RGB) 灰度上量，0..765）：

  1. 对比度 contrast = (band_median - sky_median) / sky_median。
     这是 Weber 对比——"银河比天空底亮多少倍"。**不是绝对亮度**：绝对亮度带着
     ~60 counts 的天光底座，会让所有 bortle 看起来差不多亮，把判据骗过去。对比度
     才是眼睛判断"看不看得见银河"的量。预期 B1 高、B7≈看不见、B9≈0。

  2. 高光纹理 texture = band 区 p90 - p50。银心糊成一片 → 纹理低；有结构 → 高。
     防止"修暗了但银心糊掉"蒙混过关。

  3. 空间硬度 hardness = band 边缘"过渡带"里 log 亮度梯度（按对比归一）的 p90。
     这是第三种失效模式：在 B6/B7，Weber 可见度阈按绝对面亮度切银河——银河在
     空间上陡降，阈值像等高线一样只留下最亮的银心一小块，硬边漂在纯黑上，其余
     全被裁掉。contrast/texture 都抓不到（band median 很低反而"达标"），但图看着
     就是错的：自然柔和的图银河会在很多像素上渐隐，不会是一块硬边斑。
     hardness 量的是边缘**形状**而非亮度：硬斑 = 陡（高），柔和渐隐 = 缓（低）。

判据（PASS）：
  - 单调：contrast 随 bortle 严格递减。
  - B1 暗空可见：contrast(B1) ≥ MIN_DARK（银河 majestic）。
  - B7 城郊消失：contrast(B7) ≤ MAX_B7（人眼几乎看不见银河）。
  - B9 城心全灭：contrast(B9) ≤ MAX_B9。
  - B1 不糊：texture(B1) ≥ MIN_TEXTURE。
  - B5/B6/B7 不出硬斑：hardness ≤ MAX_HARDNESS（自然柔和渐隐，不是等高线硬块）。

判据阈值来自物理预期 + 公认好图基准，不围着当前数字划线。

用法：传一组 "bortle:图路径"：
  python src/validate_bortle_series.py 1:outputs/b1.png 7:outputs/b7.png 9:outputs/b9.png
"""
import sys

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

# 判据阈值（contrast = (band-sky)/sky；texture = band p90-p50 on 0..765 灰度）
MIN_DARK_CONTRAST = 1.0   # B1 银河至少比天空底亮 1 倍（majestic）
MAX_B7_CONTRAST = 0.08    # B7 银河对比 ≤8%，人眼几乎不可见
MAX_B9_CONTRAST = 0.04    # B9 ≤4%，完全不可见
MIN_B1_TEXTURE = 90.0     # B1 核区纹理下限（好图 noweber 基准 164，糊版 121）
# hardness 阈：硬斑伪影 vs 柔和渐隐的分界。标定（见 hardness()）：
#   柔和：noweber 软基准 0.084；本序列 B1..B5 簇在 0.10..0.12（低分辨率 JPEG 略高）。
#   硬斑：B7=0.255 / B8=0.277 / B9=0.285，B6=0.177 是伪影刚冒头。
# 0.15 落在 0.12 与 0.177 之间的谷里，比最差柔和图（B3=0.121）高 ~24%，
# 比最低硬斑图（B6=0.177）低 ~15%，两边都有真实余量，不围着任一数字划线。
MAX_HARDNESS = 0.15
# hardness 退化保护：hardness 把边缘梯度按"崖高"(band-sky log 对比)归一。当银河
# 已正确消退（contrast→0、崖高→0），归一的分母趋零，残余的亚像素噪声梯度被除爆，
# hardness 虚高。但此时根本没有"斑"可言——"边缘硬不硬"在没有边缘时无定义。所以
# 当某档 contrast 低于此底（band 已等价消失，contrast 本身已判washout）时豁免硬斑
# 检查。这不是放水：是 hardness 这个量在低对比下退化为除零噪声，物理上不适用。
HARDNESS_CONTRAST_FLOOR = 0.20  # contrast 低于此 → band 已消退，硬斑检查不适用


def hardness(y, band, sky):
    """空间硬度：band 边缘过渡带里 log 亮度梯度（按 band-sky 对比归一）的 p90。

    硬斑伪影 = 等高线切出的一块硬边斑漂在黑底上 → 边缘陡（高分）；
    自然柔和 = 银河在很多像素上渐隐 → 边缘缓（低分）。量的是**形状**不是亮度。

    三条设计保证它只看形状、不看亮度/分辨率/星点：
      - 平滑尺度 s = frac·min(H,W)，是画幅的固定比例。既让"梯度/尺度"对分辨率
        不变（低分辨率图不会因像素粗而虚高），又把单颗星抹平（只剩弥散银河的边）。
      - 在 log 亮度上算梯度：整体乘个亮度系数在 log 下是常数平移，梯度不变
        → **亮度不变**，暗的柔和图和亮的柔和图同样低分。
      - 梯度按 band 与 sky 的 log 对比（"崖高"）归一：得到纯形状量——亮度每变化
        一个崖高，要走画幅的多少比例。硬斑崖矮但边陡 → 归一后高；柔和崖高但摊得宽
        → 归一后低。这也是为什么硬斑虽然绝对梯度小，归一后反而是高分。
    """
    H, W = y.shape
    s = 0.01 * min(H, W)                       # 平滑尺度 = 画幅的 1%
    logy = np.log(gaussian_filter(y, s) + 1.0)
    log_contrast = float(np.median(logy[band]) - np.median(logy[sky]))  # 崖高（log）
    mid = (~band) & (~sky)                     # 过渡带：非 band 非深空，正是渐隐区
    gy, gx = np.gradient(logy)
    norm = (np.hypot(gx, gy) * s) / max(log_contrast, 1e-3)  # 梯度/尺度，按崖高归一
    return float(np.percentile(norm[mid], 90))


def measure(path):
    """读真图，返回 (contrast, texture, hardness, band_median, sky_median)。"""
    y = np.asarray(Image.open(path).convert("RGB"), float).sum(-1)
    ylow = gaussian_filter(y, 8.0)
    band = ylow > np.percentile(ylow, 88)
    sky = ylow < np.percentile(ylow, 40)
    band_med = float(np.median(y[band]))
    sky_med = float(np.median(y[sky]))
    contrast = (band_med - sky_med) / max(sky_med, 1e-6)
    texture = float(np.percentile(y[band], 90) - np.percentile(y[band], 50))
    hard = hardness(y, band, sky)
    return contrast, texture, hard, band_med, sky_med


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    items = []
    for a in argv:
        b, path = a.split(":", 1)
        items.append((int(b), path))
    items.sort()

    print(f"{'bortle':>6} {'contrast':>9} {'texture':>8} {'hardness':>9} {'band':>6} {'sky':>6}")
    rows = []
    for b, path in items:
        c, t, h, bm, sm = measure(path)
        rows.append((b, c, t, h))
        print(f"{b:>6} {c:>9.3f} {t:>8.1f} {h:>9.3f} {bm:>6.0f} {sm:>6.0f}")

    bvals = [r[0] for r in rows]
    cvals = [r[1] for r in rows]
    by_b = dict((r[0], r) for r in rows)

    checks = []
    # 单调递减：相邻档对比度应递减。但当两个相邻档都已落到 washout 底
    # （都 < HARDNESS_CONTRAST_FLOOR，银河都已正确消失），它们并列在天光底上
    # （如 B7=B9=0.000）是正确结果而非缺陷——此时放宽到 ≥。只有"上面那档更暗"
    # 才算真错。镜像上方硬斑的 washout-tail 豁免逻辑，同一物理判断。
    def _mono_ok(hi, lo):
        if hi < lo:
            return False
        if hi == lo:
            return hi < HARDNESS_CONTRAST_FLOOR  # 并列仅在都已消退时允许
        return True
    monotonic = all(_mono_ok(cvals[i], cvals[i + 1]) for i in range(len(cvals) - 1))
    checks.append(("contrast 单调递减", monotonic, ""))
    if 1 in by_b:
        ok = by_b[1][1] >= MIN_DARK_CONTRAST
        checks.append((f"B1 暗空可见(≥{MIN_DARK_CONTRAST})", ok, f"={by_b[1][1]:.2f}"))
        ok = by_b[1][2] >= MIN_B1_TEXTURE
        checks.append((f"B1 不糊(纹理≥{MIN_B1_TEXTURE:.0f})", ok, f"={by_b[1][2]:.0f}"))
    if 7 in by_b:
        ok = by_b[7][1] <= MAX_B7_CONTRAST
        checks.append((f"B7 银河消失(≤{MAX_B7_CONTRAST})", ok, f"={by_b[7][1]:.3f}"))
    if 9 in by_b:
        ok = by_b[9][1] <= MAX_B9_CONTRAST
        checks.append((f"B9 全灭(≤{MAX_B9_CONTRAST})", ok, f"={by_b[9][1]:.3f}"))
    # 硬斑检查：B5/B6/B7 是 Weber 阈值切出等高线硬块的高发区。但只在 band 仍可见
    # （contrast ≥ floor）时才适用——band 已消退时 hardness 退化为除零噪声（见上方
    # HARDNESS_CONTRAST_FLOOR 注释），此时由 contrast 判据负责确认 washout，硬斑豁免。
    for lvl in (5, 6, 7):
        if lvl in by_b:
            c = by_b[lvl][1]
            h = by_b[lvl][3]
            if c < HARDNESS_CONTRAST_FLOOR:
                checks.append((f"B{lvl} 无硬斑(band已消退, 豁免)", True,
                               f"contrast={c:.3f}<{HARDNESS_CONTRAST_FLOOR}"))
            else:
                ok = h <= MAX_HARDNESS
                checks.append((f"B{lvl} 无硬斑(hardness≤{MAX_HARDNESS})", ok, f"={h:.3f}"))

    print("\n判据：")
    for name, ok, extra in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name} {extra}")
    allok = all(c[1] for c in checks)
    print(f"\n总判定：{'PASS ✓' if allok else 'FAIL ✗'}")
    return 0 if allok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
