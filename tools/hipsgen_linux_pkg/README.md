# HiPS 金字塔生成包（Linux，仅 hipsgen 步）

这个包把**已渲染好的 TAN 瓦片**拼成 HiPS 金字塔（可在 Aladin Lite 里无限 zoom 的全天/视场图）。
瓦片的渲染和调色在别处（一台 mac）做完，这里只负责**计算密集但单机可跑**的 hipsgen 拼接。
设计成：你（AI）拿到这个包 + 一个瓦片目录，就能独立跑完出成品 HiPS，不需要原渲染管线。

## 这个包是什么 / 不是什么

- **是**：hipsgen（Aladin/HipsGen，Java）+ 高分 Allsky 重建（Python）+ 落地页。输入瓦片、输出 HiPS。
- **不是**：不含瓦片渲染（Gaia 星表 → TAN 投影 PNG）、不含 PixInsight 调色。那些已在上游做完。

## 目录

```
run_hipsgen.sh          # 主入口（一条命令跑完 hipsgen + allsky + 落地页）
bin/AladinBeta.jar      # Aladin/HipsGen v11（拼金字塔的 Java 工具）
src/rebuild_allsky_hires.py   # 把 64px 的 Allsky 预览重建成 256px（修 zoom-out 糊）
src/hips_landing_page.html    # 样式化落地页（覆盖 hipsgen 生成的简陋 index.html）
README.md               # 本文件
```

> 注：`bin/AladinBeta.jar`（6.4M）不进 git。mac 端用 `pack.sh` 打包时自动从
> `outputs/tmp_reference_hips/AladinBeta.jar` 拷入；手动打包则自行放一个 Aladin/HipsGen v11 jar 到 `bin/`。

瓦片目录**不在包内**（太大，单独 rsync）。它是一堆 `tile_*.png` + 同名 `tile_*.hhh`：
- `.png` = TAN 投影的彩色瓦片（1536×1536，约 1.5 arcsec/px）
- `.hhh` = 每张瓦片的 WCS header（FITS 文本），hipsgen 靠它定位瓦片在天球的位置（**不靠文件名**）

## 依赖

1. **OpenJDK 11**（必须是 11，新版 JDK 移除了此 jar 依赖的 JApplet → 不兼容）
   ```bash
   # Debian/Ubuntu
   sudo apt install openjdk-11-jdk
   # 或 conda: conda install -c conda-forge openjdk=11
   which java && java -version   # 确认是 11.x；不是的话传 JAVA=/path/to/jdk11/bin/java
   ```
2. **Python 3 + Pillow**（rebuild_allsky 用）
   ```bash
   pip install Pillow
   ```

## 怎么跑

```bash
# 1) 把瓦片目录 rsync 过来（在 mac 上执行）：
#    rsync -av /path/on/mac/hips_xxx_tiles/  user@linux:/data/tiles/

# 2) 在 Linux 上跑：
TILES=/data/tiles OUT=/data/hips_out bash run_hipsgen.sh

# 可调（按机器）：
JAVA=/opt/jdk-11/bin/java XMX=200g MAXTHREAD=64 TILES=/data/tiles OUT=/data/hips_out bash run_hipsgen.sh
```

跑完 `OUT/` 就是完整 HiPS（Norder0-8 金字塔 + index.html）。预览：
```bash
cd /data/hips_out && python3 -m http.server 8080
# 浏览器开 http://<linux-ip>:8080/
```
最终部署：把 `OUT/` rsync 到 web 服务器即可。

## 关键参数与坑（务必读）

### 为什么 `hips_order=8`（默认，别去掉）

瓦片源是 1.5 arcsec/px。hipsgen 若**不限 order**，会按源像素密度**自动选 Norder9**
（0.8 arcsec/px）——但那比源还细，是对源做 2× 过采样插值，**没有新信息、放大看反而糊**，
而且多拼一整层让瓦片数 ×4、时间数倍（实测 12h vs 3.5h）。

`hips_order=8`（Norder8 ≈ 1.6 arcsec/px）正好匹配源真分辨率：画质不损、省 75% 时间瓦片。
**源分辨率变了要相应调**：每像素角尺寸 a (arcsec)，对应 order ≈ log2(可看的最细) ——
经验：1.5 arcsec/px → order 8；6 arcsec/px → order 6；10 arcsec/px → order 5/6。
判据：Norder N 的 arcsec/px = sqrt(4π/(12·4^N))·(180·3600/π)/512，选最接近源 arcsec/px 的 N。

### 其它

- `fading=true` 必加：羽化瓦片重叠过渡，消亮处混合硬边。
- `color=jpeg`：输出 JPEG 瓦片（彩色）。
- `creator_did` / `obs_title` 必加：缺 ID 这版 HipsGen 会 `*ERROR: Missing ID` 退出。
- `target=271.672 -25.873`：FOV 中心（银心赤道坐标），落地页初始视角用。全天数据可改或留默认。
- **JDK 必须 11**：`Unsupported class version` / JApplet 报错 = JDK 版本不对。
- hipsgen 耗时 ∝ 瓦片数 × 深度。几万源瓦片 + order8 ≈ 数小时；用 `MAXTHREAD` 吃满核。
- 长任务用 `nohup bash run_hipsgen.sh ... & disown` 脱离会话，SSH 断也不被 kill。

## 验收

- `OUT/` 下有 `Norder0`..`Norder8` 七~九层目录、`Norder3/Allsky.jpg`、`properties`、`index.html`。
- `properties` 里 `hips_order = 8`、`dataproduct_subtype = color`。
- 浏览器加载 index.html，能看到图、能 zoom；zoom 到最深（Norder8）星点清晰不糊。
- 如果 zoom-out（大 FOV）发糊：确认 rebuild_allsky 跑过了（Norder3/Allsky.jpg 应是高分版、几 MB 不是几百 KB）。
