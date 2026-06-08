"""Camera motion paths shared by Gaia video renderers."""
import numpy as np

import render_3d as r3


def ease(t):
    return 0.5 - 0.5 * np.cos(np.pi * np.clip(t, 0, 1))


def normalize(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    if n == 0:
        raise ValueError("zero-length vector")
    return v / n


def slerp(a, b, t):
    """Spherical linear interpolation between two unit directions."""
    a = normalize(a)
    b = normalize(b)
    dot = np.clip(np.dot(a, b), -1.0, 1.0)
    if dot > 0.9995:
        return normalize(a + (b - a) * t)
    theta = np.arccos(dot)
    sin_theta = np.sin(theta)
    return (
        np.sin((1 - t) * theta) / sin_theta * a
        + np.sin(t * theta) / sin_theta * b
    )


def l_motion(frames, leg1_pc=400.0, leg2_pc=2500.0, split=0.5,
             leg1_dir=None, leg2_dir=None, leg2_target=None):
    """Shared L-shaped path: first leg forward, then toward a second-leg target or direction."""
    if frames <= 0:
        raise ValueError("frames must be positive")
    d1 = normalize(leg1_dir if leg1_dir is not None else r3.flight_direction("galactic_plane"))
    d2 = normalize(leg2_dir if leg2_dir is not None else r3.flight_direction("galactic_pole"))
    target2 = np.asarray(leg2_target, dtype=float) if leg2_target is not None else None
    split_index = max(1, min(frames - 1, int(round(frames * split)))) if frames > 1 else 1
    positions = np.zeros((frames, 3), dtype=float)
    phase = np.zeros(frames, dtype=float)
    for i in range(frames):
        if i < split_index:
            t = i / max(split_index - 1, 1)
            positions[i] = d1 * (ease(t) * leg1_pc)
        else:
            t = (i - split_index) / max(frames - split_index - 1, 1)
            base = d1 * leg1_pc
            if target2 is not None:
                positions[i] = base + (target2 - base) * ease(t)
            else:
                positions[i] = base + d2 * (ease(t) * leg2_pc)
            phase[i] = ease(t)
    return positions, phase


def look_path(frames, start_dir, end_dir, phase):
    """Smooth look direction path using the second-leg phase as the interpolation parameter."""
    return np.array([slerp(start_dir, end_dir, t) for t in phase])
