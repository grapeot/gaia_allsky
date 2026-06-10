"""L 型飞行视频: 北斗起步, 两段叙事(物理诚实)。

第一段 沿银道飞 ~400pc (全天 equirectangular 视角):
  近处星座(北斗)散架变形, 银河带基本不动。
  题眼: 星座是视角幻觉, 银河是大尺度结构。近处星视差真实可靠。

第二段 垂直飞出 + 镜头下俯 (鱼眼方位投影对准银南极/脚下):
  整个星空收缩成脚下一个发光的球。
  题眼: 连"整个星空"都只是出发点周围一个有限的小球(Gaia 可见光视差能及的边界),
        银河真身在球外够不着。模拟变不出数据里没有的——数据的边界也要诚实。

每帧曝光补偿(独立归一, 抵消飞行平方反比变暗)。blooming 亮星光晕。
8bit PNG 预览 + 16bit HDR TIFF。两段各渲, 末尾拼接。
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import render_3d as r3
import render_starmap as rs

OUTDIR = os.path.join(os.path.dirname(__file__), "..", "outputs", "l_video_frames")


def _ease(t):
    return 0.5 - 0.5 * np.cos(np.pi * np.clip(t, 0, 1))


def _expose(cv, gamma, pct):
    """每帧曝光补偿: 独立归一(相机自动曝光, 抵消飞行平方反比变暗) → [0,1] 浮点。"""
    return rs.normalize_brightness(cv, pct, "gamma", gamma)


def render_video(data_path, W=2048, H=1024, n1=300, n2=300,
                 leg1_pc=400.0, leg2_pc=2500.0, gamma=2.2, pct=99.7,
                 bloom_strength=0.5, save_hdr=True, outdir=None, fps=60):
    """渲 L 飞行视频。n1/n2: 两段帧数。第一段 equirect(WxH), 第二段 fisheye(HxH 方形居中补黑到 WxH)。"""
    from PIL import Image
    import tifffile
    outdir = outdir or OUTDIR
    os.makedirs(outdir, exist_ok=True)

    d = np.load(data_path)
    ra, dec, dist_pc, g = d["ra"], d["dec"], d["dist_pc"], d["g"]
    bv = np.nan_to_num(d["bp_rp"], nan=0.7)
    xyz = r3._radec_dist_to_xyz(ra, dec, dist_pc)
    d_plane = r3.flight_direction("galactic_plane")
    d_pole = r3.flight_direction("galactic_pole")
    down = -d_pole   # 脚下=银南极

    print(f"L video: leg1={leg1_pc}pc(银道,{n1}f) + leg2={leg2_pc}pc(垂直下俯,{n2}f) @{W}x{H}")
    idx = 0

    # ---- 第一段: 沿银道飞, 全天 equirectangular, 北斗散架/银河不动 ----
    for i in range(n1):
        s = _ease(i / max(n1 - 1, 1)) * leg1_pc
        obs = d_plane * s
        # 统一 PSF 成像模型: 饱和锚定固定参考星等, 整段视频稳定(替换旧加性 bloom)。
        cv = r3.render_3d_frame(xyz, g, bv, obs, W, H, gain=1.0,
                                sat_level=r3.sat_level_from_ref_mag(6.0))
        lin = _expose(cv, gamma, pct)
        Image.fromarray((lin * 255).astype("uint8")).save(f"{outdir}/frame_{idx:04d}.png")
        if save_hdr:
            tifffile.imwrite(f"{outdir}/frame_{idx:04d}.tif", (lin * 65535).astype("uint16"))
        if i % 60 == 0:
            print(f"  leg1 {i}/{n1} obs={s:.0f}pc")
        idx += 1
    base = d_plane * leg1_pc   # 第二段从第一段终点接续

    # ---- 第二段: 垂直飞出 + 下俯, 鱼眼方位投影, 星空收缩成脚下的球 ----
    S = H   # 鱼眼方形边长
    x0 = (W - S) // 2
    for i in range(n2):
        s = _ease(i / max(n2 - 1, 1)) * leg2_pc
        obs = base + d_pole * s
        disk = r3.render_fisheye_lookdir(xyz, g, bv, obs, down, S, fov_deg=170.0,
                                         gain=1.0, sat_level=r3.sat_level_from_ref_mag(6.0))
        lin = _expose(disk, gamma, pct)
        frame = np.zeros((H, W, 3), np.float32)
        frame[:, x0:x0 + S] = lin
        Image.fromarray((frame * 255).astype("uint8")).save(f"{outdir}/frame_{idx:04d}.png")
        if save_hdr:
            tifffile.imwrite(f"{outdir}/frame_{idx:04d}.tif", (frame * 65535).astype("uint16"))
        if i % 60 == 0:
            print(f"  leg2 {i}/{n2} obs_vert={s:.0f}pc")
        idx += 1

    print(f"done, {idx} frames in {outdir}")
    return outdir, idx


def assemble_mp4(frames_dir, out_path, fps=60):
    """用 ffmpeg 把 PNG 帧序列合成 mp4。需要 frame_%04d.png。"""
    import subprocess
    cmd = ["ffmpeg", "-y", "-framerate", str(fps),
           "-i", os.path.join(frames_dir, "frame_%04d.png"),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", out_path]
    subprocess.run(cmd, check=True)
    return out_path


if __name__ == "__main__":
    data = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "gaia_3d_deep.npz")
    od, n = render_video(data)
    assemble_mp4(od, os.path.join(os.path.dirname(__file__), "..", "outputs", "l_flight.mp4"))
