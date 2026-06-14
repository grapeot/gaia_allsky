# 直渲 HiPS 调试笔记（tmp，剪切问题接力用）

记录直渲 HEALPix 瓦片调试的中间过程 + 复用方法。核心是**用 hipsgen 做 gold standard 逐瓦片
对比**。本文件是临时接力笔记，问题彻底解决后可删，结论沉淀进 working.md。

## 当前未解状态（2026-06-14）

直渲剪切修复到"椭圆核 work、无缝、普通星圆"，但两层残留：
1. **饱和大星核** 球面轴比 1.143（单星椭圆核映射是圆 1.003，矛盾）——饱和盘形状没被小椭圆核控制。
2. **剪切是 cell 内连续场、非每瓦片常数**——下半/跨 face 瓦片单中心 J 修不全、形状错位。
   修向：逐点 J 场 / cdshealpix `hash_with_dxdy` 连续亚像素 forward map。

产物目录（都在 outputs/，未进 git）：
- `direct_south/` 直渲圆核（变形基线，中心 l351.95 b10 ±5°）
- `direct_v3/` 直渲椭圆核（无缝、上半对下半错位）
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
