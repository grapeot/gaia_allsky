# 直渲 HiPS 调试笔记（tmp，剪切问题接力用）

记录直渲 HEALPix 瓦片调试的中间过程 + 复用方法。核心是**用 hipsgen 做 gold standard 逐瓦片
对比**。本文件是临时接力笔记，问题彻底解决后可删，结论沉淀进 working.md。

## 当前未解状态（2026-06-14）

直渲剪切修复到"椭圆核 work、无缝、普通星圆"，但两层残留：
1. **饱和大星核** 球面轴比 1.143（单星椭圆核映射是圆 1.003，矛盾）——饱和盘形状没被小椭圆核控制。
2. **剪切是 cell 内连续场、非每瓦片常数**——下半/跨 face 瓦片单中心 J 修不全、形状错位。
   修向：逐点 J 场 / cdshealpix `hash_with_dxdy` 连续亚像素 forward map。

### 2026-06-14 更新：local shear v2 进展

当前实验分支：`fix-hips-local-shear`。

**代码变化**：
- `render_hips_direct_pipeline.py` 改用 `cdshealpix.lonlat_to_healpix(..., return_offsets=True)` 获取
  order-K cell 内连续 `dx/dy`，不再用 Norder(K+9) 离散 sub-cell 近似做 forward map。
- 亮星 bloom 支持 per-star shear：每颗亮星按自身位置算局部 HEALPix 雅可比 `J/sqrt(det)`，用
  `J^-1` 椭圆核补偿显示端剪切。
- `local_shear` 第一版只修亮星核，低 order 变化太小（N3 平均差 0.33/255），肉眼形变基本不会靠它变好。
- `local_shear2` 第二版对 N3-N5 的暗星/乳光层也做 block-local inverse-shear PSF（64px block，
  每块一个局部椭圆高斯核）。用户反馈：**扭曲好不少了**。这说明截图里的主要问题不只是亮星，而是
  zoom-out 低 order 的暗星/乳光纹理也需要按 HEALPix 局部剪切预补偿。

**当前可看产物**：
- `outputs/direct_local_shear2/hips/`：同区 `l=351.95 b=10 half=5`，N3-N5 为 local shear v2，N6-N8
  复用 local_shear 第一版（这些层 cell 内剪切梯度小）。已补 `index.html`、`properties`、
  `Norder3/Allsky.jpg`。预览：`http://localhost:8780/`。
- `outputs/direct_tile669_center_shear2/hips/`：以问题 tile 自身为中心的 ±5° 小样。tile 识别为
  **Norder3 Npix669**，中心 `l=352.494, b=8.875`，ICRS `ra=253.125207 dec=-30.000473`。
  已补 `index.html`、`properties`、`Norder3/Allsky.jpg`。预览：`http://localhost:8781/`。
- diff 图：`outputs/_scratch/direct_local_shear2_order{3,4,5}_old_new1_new2_diff.png`。四排依次是
  old/direct_v3、new1/只亮星 local shear、new2/暗星也 local shear、old-vs-new2 diff×4。

**验证结果**：
- 单星 probe（Norder3 npix669）：无 shear 球面轴比 1.225/1.059；旧中心 shear 残余 1.082/1.068；
  local shear 亮度加权轴比 **1.000/1.000**。说明局部核补偿方向和量是对的。
- `python -m pytest tests/ -q`：**93 passed**。
- 与 `direct_v3` 同区比较：`local_shear2` 在 N3-N5 平均绝对差约 2.7-2.9/255，明显大于只修亮星
  的第一版；N8 与 `south_gold` 的相关性基本不变，说明位置/朝向没有被改坏。
- tile 内容翻转检查：同 npix 比较 `direct_v3` vs `local_shear2`，原图相关性约 0.99，水平/垂直翻转
  相关性接近 0 或负数，说明 **Norder tile 文件本身没有左右反**。若 Aladin 初始大 FOV 看起来左右反，
  更可能是 `Allsky.jpg` 重建方向/布局的问题，而不是 Norder tile 的 dx/dy 映射。

**性能观察**：
- N3-N5 单独 102 tile：8 workers 19.0s，32 workers 19.4s，worker 数没帮助，瓶颈是每 tile 内部
  Python block 卷积/进程启动开销。
- 完整 N3-N8 tile669 小样 4762 tile：32 workers 39.2s（121 tile/s），并行效率正常很多。
  后续若确认 local_shear2 视觉正确，再优化 N3-N5 sheared PSF 的实现。

**仍待查**：
- `Allsky.jpg` 方向：`rebuild_allsky_hires.py` 目前简单按 `row=npix//27, col=npix%27` 贴 tile，未验证
  HiPS Allsky 预览是否需要 tile 内翻转/旋转。用户观察到 `direct_local_shear2` 左右反，优先怀疑 Allsky
  预览层；Norder tile 级相关性不支持 tile 文件整体左右反。
- 饱和大星盘：local shear 修了核/暗星层，但极亮星 tone clip 后的饱和平台是否完全圆，还需继续量。

### 2026-06-14 更新：shear3/shear4

**shear3（`outputs/direct_ra17_dec-45_shear3/hips`，预览 `http://localhost:8783/`）**：把普通暗星的
sheared PSF 从 N3-N5 扩展到 N6-N8。用户确认：**zoom-in 星点椭圆问题解决**。代价是 N8 也要椭圆
卷积，RA17/-45 小样速度从 shear2 的 ~118 tile/s 降到 ~72 tile/s。

**shear4（`outputs/direct_ra17_dec-45_shear4/hips`，预览 `http://localhost:8784/`）**：修亮星 bloom
跨 tile 截断。根因是旧逻辑只画中心在本 tile 的亮星，中心在相邻 tile 的大光晕不会贡献到当前 tile，
Aladin 里就显示成沿 HEALPix 边界硬切的三角/扇形。新逻辑对亮星额外用 high-order nested face 坐标
落到当前 tile 的扩边画布，允许 `[-margin, TILE+margin)` 内的邻 tile 亮星贡献 wing；tile 外亮星的
局部 shear 取当前 tile 最近边界位置。

用户给的亮星截断坐标：
- `16:54:16.892 -42:24:38.42`：附近有 G=3.15 亮星中心在相邻 N8 tile。shear4 对当前 tile N8
  变化明显（mean abs 4.31/255，max diff 231）。
- `17:18:49.704 -44:07:35.19`：最近 G=5.74 亮星中心在本 tile 且靠近底边，缺失主要发生在下方邻 tile，
  当前 tile 自身变化小。邻域对比图已生成。

QA 图：
- `outputs/_scratch/cut1_n8_shear3_vs_shear4_neighbors.png`
- `outputs/_scratch/cut2_n8_shear3_vs_shear4_neighbors.png`
- `outputs/_scratch/seam_n8_shear3_vs_shear4_neighbors.png`

注意：shear4 是当前亮星截断修复候选，还需用户在 Aladin 肉眼确认。

**shear5（`outputs/direct_ra17_dec-45_shear5/hips`，预览 `http://localhost:8785/`）**：修普通星
PSF 的块状/梳状问题。shear3/4 虽然有椭圆核，但普通星坐标仍通过 high-order `healpy.pix2xyf`
变成整数子格，0.6px 小核被采样成奇怪的斜块。shear5 改为：本 tile 内星用 `cdshealpix` 连续
`dx/dy`，只对邻 tile 跨界贡献退回 high-order face 坐标；普通星 accumulation 用 bilinear splat
保留亚像素中心。新增测试 `test_bilinear_splat_preserves_subpixel_position`，全量 `pytest`：**94 passed**。

用户给的 PSF/接缝坐标在 shear5 相对 shear4 的高阶 tile 变化明显：
- `17:08:03.290 -50:19:03.89`：N8 mean abs 20.35/255，max 235。
- `17:35:41.095 -44:54:29.47`：N8 mean abs 23.34/255，max 236。
- `16:53:55.165 -41:48:34.84`：N8 mean abs 10.78/255，max 233。

下一步看用户肉眼验收：如果 shear5 仍有接缝，问题就不是普通星中心量化，而是跨 face / Allsky /
亮星超大光晕覆盖半径不足。

**shear6（`outputs/direct_ra17_dec-45_shear6/hips`，预览 `http://localhost:8786/`）**：修 zoom-out
接缝的另一层根因：低 order tile 选择半径太小。旧 main 用 `half*1.5` 选每层 jobs；对 N3/N4 这种
单 tile 很大的层，视野边缘附近的粗 tile 邻居会漏掉。Aladin zoom-out 取 N3/N4 或 Allsky 时就看到
黑缝，即使 N5-N8 高阶 tile 都存在。

修法：新增 `_tile_search_radius(half, korder)=max(half*1.5, half + tile_fov)`，其中
`tile_fov=58.6323/2^korder`。也就是低 order 加一整个 tile-width guard band，高 order 仍保持原半径。
新增测试 `test_low_order_tile_search_has_guard_band`，全量 `pytest`：**95 passed**。

验证点：
- `17:35:44.273 -44:56:12.50`：shear5 时 N3 缺 4 个邻居、N4 缺 2 个邻居；shear6 后 N3/N4/N5
  周围 8 邻居全存在。
- `17:25:23.191 -43:27:34.45`：shear6 后 N4/N5 周围 8 邻居全存在，N3 仅剩一个远角邻居缺失；
  直接上下/左右邻居已补齐。
- Norder3 Allsky 覆盖从 9 tile 增加到 16 tile。

当前推荐验收版本：`http://localhost:8786/`。

**half12 大区验证（`outputs/direct_ra17_dec-45_half12_shear6/hips`，预览 `http://localhost:8787/`）**：
同中心 RA17/Dec-45 重渲 half=12、N3-N8，26259 tile，378.5s（69.4 tile/s）。已补 `properties`、
`index.html`、`Norder3/Allsky.jpg`。用户新增检查点 `17:25:21.134 -43:27:49.31` 的 ICRS 坐标为
RA=261.338058、Dec=-43.463697，距中心 4.793°，不是 half=5 外边界。在 shear6 half=5 小区里：N4-N8
自 tile 和 8 邻域全存在，N3 自 tile 存在、8 邻域缺远角 `738`；在 half12 输出里：N3-N8 自 tile 和 8
邻域全存在。结论：该点的覆盖/低阶邻居问题在 half12 里已排除；若 Aladin 里仍有 PSF 异常，应转向
hipsgen 官方 backward sampling / tile orientation / border handling 的实现细节，而不是继续只查缺 tile。

**下一步**：clone/read hipsgen 官方实现，重点看 `ThreadBuilderTile` 一类 tile 反向采样代码：每个输出像素如何
取 HEALPix center、如何 bilinear、边界如何处理、Allsky 如何布局。当前 direct renderer 是 forward splat +
局部剪切补偿，和 hipsgen 的 backward resample 语义仍不同，这可能是剩余 PSF 观感差异的根因。

**Norder5 seam/PSF 追加检查**：用户给的三条 seam 坐标全部在 N5 tile 右边缘 2-7px 内、同 face、邻 tile
齐全。`shear6`、普通星 off-tile wing 修复版、连续 face 坐标修复版三者对这三条 seam 的简单文件级指标
都没有改善，说明主因不像缺 tile、普通星翼被裁或 off-tile 坐标量化。`block=16` 临时实验触发
`cdshealpix` Rust 线程池 `Resource temporarily unavailable`，已撤回，不作为候选。当前判断：截图里放大后的
块状/斜向 PSF 更像 forward splat + undersampled elliptical kernel 的采样伪影；文件内椭圆本身部分是 expected
（Aladin 显示端 shear 会再映回球面），但块状马赛克感不是理想状态。需要按 hipsgen 的 backward gather/bilinear
语义重设验证基线。

产物目录（都在 outputs/，未进 git）：
- `direct_south/` 直渲圆核（变形基线，中心 l351.95 b10 ±5°）
- `direct_v3/` 直渲椭圆核（无缝、上半对下半错位）
- `direct_local_shear2/` 直渲 local shear v2（同区，可看当前最佳修复）
- `direct_tile669_center_shear2/` 以问题 Norder3 Npix669 tile 中心重渲 ±5°
- `south_gold/` hipsgen 同区金标（**只渲了 N8**，orders="8"）
- `ant2048/` hipsgen 完整（心宿二 l351.95 b15.06 ±3°，N3-8 全）

## Gold standard 对比方法（核心复用）

**原则**：hipsgen 是权威重投影，但它**不是绝对正确**——对点源它双线性 rasterize 会把圆星拉
椭圆（轴比~1.15）。所以 gold 用于**几何/位置/朝向**对齐，不是画质金标。判画质看直渲自身。

### 关键陷阱（踩过的坑）
1. **必须同区同 npix**：早期拿 ant2048（中心 b15）比 direct_south（中心 b10），共有 npix 但
   covered 天区不重合 → "0 共有"或比到边缘空瓦片。**gold 必须用和直渲完全相同的 lc/bc/half 渲。**
2. **frame 要一致**：直渲强制 ICRS(equatorial)，hipsgen properties 也要 equatorial。
3. **gold 渲哪个 order**：比 N8 最干净（两边都满）；比低 order 要 hipsgen 也渲低 order。
4. **互相关 0.74-0.94 算高**（点源密集场，亚像素差 + 直渲无重投影模糊 vs hipsgen 双线性）。
   低于 0.5 才怀疑朝向/几何错。

### 生成 gold（hipsgen 同区）
```bash
JAVA=/opt/homebrew/opt/openjdk@11/bin/java bash tools/render_per_order_pipeline.sh GOLDTAG \
  data/raw/gaia_allsky_g20_bsc5_hpx6.npz <lc> <bc> <half> "8" \
  --tile-size 2048 --workers 30 --hipsgen-par 1 --hipsgen-th 30
# 注意：后台跑老被 pkill 误伤/环境问题，前台跑稳。
```

### 5×N 对比图（逐瓦片直渲 vs hipsgen）
```python
import numpy as np,glob,re,healpy as hp
from PIL import Image
dN={int(re.search(r'Npix(\d+)',f).group(1)):f for f in glob.glob('outputs/DIRECT/hips/Norder8/**/*.jpg',recursive=True)}
hN={int(re.search(r'Npix(\d+)',f).group(1)):f for f in glob.glob('outputs/GOLD/hips/Norder8/**/*.jpg',recursive=True)}
common=set(dN)&set(hN)                       # 同区才有大量共有
sel=sorted(common,key=lambda n:-np.asarray(Image.open(dN[n]).convert('L')).mean())[:5]
W=256; c=Image.new('RGB',(W*5+40,W*2+30),(30,30,30))
for i,n in enumerate(sel):
    c.paste(Image.open(dN[n]).convert('RGB').resize((W,W),Image.NEAREST),(i*(W+10),0))      # 上排直渲
    c.paste(Image.open(hN[n]).convert('RGB').resize((W,W),Image.NEAREST),(i*(W+10),W+30))   # 下排hipsgen
    d=np.asarray(Image.open(dN[n]).convert('L'),float);h=np.asarray(Image.open(hN[n]).convert('L'),float)
    print(f'npix{n} 相关 {np.corrcoef(d.ravel(),h.ravel())[0,1]:.2f}')
c.save('outputs/_scratch/5x2_compare.png')
```

## 剪切量化方法（核心诊断）

### 测瓦片像素网格在球面的剪切（轴夹角）
```python
import numpy as np, healpy as hp
K=8; ns=2**(K+9); npix=685094
x8,y8,f8=hp.pix2xyf(2**K,npix,nest=True)
def vec(gx,gy):
    p=hp.xyf2pix(ns,int(gx),int(gy),int(f8),nest=True); return np.array(hp.pix2vec(ns,p,nest=True))
o=vec(x8*512+256,y8*512+256)
ex=vec(x8*512+266,y8*512+256)-o; ey=vec(x8*512+256,y8*512+266)-o
cosang=np.dot(ex,ey)/(np.linalg.norm(ex)*np.linalg.norm(ey))
print('x/y 轴球面夹角', np.degrees(np.arccos(cosang)),'°（90=正交,实测~77.7=剪切）')
# 长度比 max/min ≈ 1.0（等面积保步长），但夹角≠90 → 剪切非缩放
```

### 测某瓦片饱和大星的【球面】轴比（验证修复）
```python
# J = 像素(row,col)→切平面雅可比（列序 row,col），见 render_hips_direct_pipeline 同款
def vec(gx,gy): ...   # 同上
o=vec(x8*512+256,y8*512+256)
er=vec(x8*512+266,y8*512+256)-o; ec=vec(x8*512+256,y8*512+266)-o
uu=er/np.linalg.norm(er); vv=ec-np.dot(ec,uu)*uu; vv=vv/np.linalg.norm(vv)
J=np.array([[np.dot(er,uu),np.dot(ec,uu)],[np.dot(er,vv),np.dot(ec,vv)]])/10
a=np.asarray(Image.open(tile).convert('L'),float); th=a>200; ys,xs=np.where(th)  # 饱和区
pts=np.vstack([ys-ys.mean(),xs-xs.mean()])     # (row,col)
sph=J@pts; ev=np.linalg.eigvalsh(np.cov(sph))
print('大星球面轴比', np.sqrt(ev[1]/ev[0]),'(1=圆)')
# 圆核基线 1.233 → affine整图 1.064(有缝) → 椭圆核 1.143(无缝,饱和盘残留)
```

## 关键事实速查
- HEALPix 剪切是固有：等面积参数化保面积/步长但不保角（Primer §3 Fig.3，斜45°菱形）。
- hipsgen 反向 pull-sample（`ThreadBuilderTile.java` hn.center + bilinear），不主动逆剪切。
- healpy 只有整数 pix2xyf；**cdshealpix `lonlat_to_healpix(...,return_offsets=True)` 给连续
  (hash,dx,dy)**，dx/dy∈[0,1) 是 cell 内分数坐标——forward 连续 map 的唯一来源。
- 转置：healpy (x,y) 与 HiPS 写盘 (col,row) 差转置（col←y, row←x）。
- 剪切随位置变（worst near interruption lines / base-cell diagonals）——单中心 J 不够。

## 怎么做测试（验证流程，从快到慢）

调这个问题的验证分四层，**从快到慢、从隔离到端到端**，每改一处按需跑：

### 1. 隔离单星 probe（最快，秒级，定位核/坐标对不对）
人造一颗星放瓦片已知位置，直渲它的核，量 FWHM x/y 或球面轴比。**隔离掉密集场/邻星污染**，
是判"核本身圆不圆/椭圆核方向对不对"的最干净手段。例：
```python
# 一颗星放瓦片中心，_bright_star_wings(shear=A) 画核，量映射球面是否圆
w=tw._bright_star_wings(512,np.array([256]),np.array([256]),np.array([1e3]),
                        np.array([[1.,1.,1.]]),np.array([3.0]),cdelt,margin=0,shear=A)
yy=w.sum(-1); th=yy>yy.max()*0.3; ys,xs=np.where(th)
sph=J@np.vstack([ys-ys.mean(),xs-xs.mean()]); ev=np.linalg.eigvalsh(np.cov(sph))
print('核映射球面轴比', np.sqrt(ev[1]/ev[0]))   # =1.003 → 核对
```
**教训**：单星 probe 说圆、但整瓦片饱和大星椭圆 → 问题不在核，在饱和盘/cell 内剪切梯度。
单星和整瓦片结论不一致本身就是诊断信息。

### 2. pytest 不变量（每次改 render_tan_wcs 必跑，守不回归）
```bash
source .venv/bin/activate
python -m pytest tests/test_render.py -k bright_wing -q    # 6 个 bloom 不变量
python -m pytest tests/ -q                                 # 全套 91（改核心代码后）
```
bloom 不变量：圆对称无方块、边缘渗入、拼接连续、σ随星等、性能不退化、局部窗口=全画布卷积。
**加 shear 参数时务必确认 shear=None 仍全过**（不破坏 TAN 路径）。新几何特性也该加不变量测试
（如"shear=A 的核映射球面轴比≈1""无 shear 时核圆对称"），目前还没加——是 TODO。

### 3. 整瓦片量化（中速，量真实数据的统计）
直渲一小块（±1° 几秒），量瓦片里星的椭圆度/球面轴比、边缘黑缝率。**避开密集场污染**：
取孤立星（连通域 4-60px）、看中位轴比（个别 1.3 是邻星/翼边缘偶发，中位才是真信号）。
黑缝检查：随机 30 瓦片，边缘行/列 <5 的占比 >0.3 算有缝。

### 4. 端到端 Aladin（最慢，最终验收，肉眼）
直渲 ±5°/±10° 全 order → 补 properties+Allsky+index → `python3 -m http.server` → Aladin 看。
**唯一能看跨瓦片拼接/朝向/zoom 连续性的方式**。但 Aladin 显示端有自己的重投影——
文件里椭圆可能显示圆、反之亦然（剪切那次就靠这个翻转定位根因）。所以**文件级量化 + Aladin
肉眼要结合**，单看一个会被骗。properties 关键字段：hips_frame=equatorial、hips_order_min
（minOrder=order 副作用会写错→zoom-out 空白）、hips_initial_ra/dec/fov。

### 测试顺序建议
改核函数 → 1 隔离单星（秒）→ 2 pytest（秒）→ 3 小块量化（几秒）→ 4 端到端 Aladin（确认才上）。
别跳过 1-2 直接渲大块看 Aladin——慢且分不清是核错还是别的。

## 调试方法论教训（这轮反复踩）
- **别拿 hipsgen 当画质金标**——它重投影把点源拉椭圆，直渲处理对几何后更圆。
- **跨机/跨配置比较必须逐字相同 tile/参数**（用错基线推出假的"慢20×"/"椭圆"多次）。
- **看图前先确认看的是同一个东西**（同区同 npix），否则各说各话。
- 量化用对指标：椭圆度看球面轴比（经 J 映射）不是像素轴比；CPU/内存 bound 看占用率。
- 后台 job 老被 pkill 误伤——验证类前台跑稳。
