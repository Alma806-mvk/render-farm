#!/usr/bin/env python3
"""Render frames of a .blend file on CPU using Blender's pip package (bpy).

No Blender download needed: `pip install bpy` gives you the full engine as a Python
module. Tested 2026-10-26 inside a Claude Code cloud session (bpy 5.2.3 LTS, 4 vCPU,
16 GB RAM) and written so the same file runs unchanged on a GitHub Actions runner.

Usage:
  python render_blend_cpu.py scene.blend out_dir [--start 1] [--end 24]
                             [--samples 64] [--scale 100] [--engine CYCLES]
                             [--format PNG]

Output files: out_dir/frame_0001.png, frame_0002.png, ...
Stitch with:  ffmpeg -framerate 24 -i out_dir/frame_%04d.png -c:v libx264 -pix_fmt yuv420p out.mp4

Notes:
  * CYCLES on CPU always works headless. EEVEE needs Mesa EGL libraries
    (see cloud-env-setup.sh) and is slow in software GL; prefer CYCLES on CPU-only machines.
  * --samples / --scale override the file's settings so you can do fast previews.
"""
import argparse
import os
import sys
import time


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("blend", help="path to the .blend file")
    p.add_argument("out_dir", help="directory for rendered frames")
    p.add_argument("--start", type=int, help="first frame (default: file's frame_start)")
    p.add_argument("--end", type=int, help="last frame (default: file's frame_end)")
    p.add_argument("--samples", type=int, help="Cycles samples override")
    p.add_argument("--scale", type=int, help="resolution percentage override, e.g. 50")
    p.add_argument("--engine", default="CYCLES", choices=["CYCLES", "BLENDER_EEVEE", "BLENDER_WORKBENCH"])
    p.add_argument("--format", default="PNG", choices=["PNG", "JPEG", "OPEN_EXR"])
    p.add_argument("--threads", type=int, default=0, help="render threads (0 = auto)")
    a = p.parse_args()

    t_import = time.time()
    import bpy  # noqa: E402  (slow import, keep it after arg parsing)

    print(f"bpy {bpy.app.version_string} loaded in {time.time() - t_import:.1f}s", flush=True)

    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    scene = bpy.context.scene
    scene.render.engine = a.engine
    if a.engine == "CYCLES":
        scene.cycles.device = "CPU"
        if a.samples:
            scene.cycles.samples = a.samples
    if a.scale:
        scene.render.resolution_percentage = a.scale
    if a.threads:
        scene.render.threads_mode = "FIXED"
        scene.render.threads = a.threads
    scene.render.image_settings.file_format = a.format
    scene.render.use_file_extension = True

    scene.frame_start = a.start if a.start is not None else scene.frame_start
    scene.frame_end = a.end if a.end is not None else scene.frame_end
    os.makedirs(a.out_dir, exist_ok=True)
    scene.render.filepath = os.path.join(os.path.abspath(a.out_dir), "frame_")

    n = scene.frame_end - scene.frame_start + 1
    res = (scene.render.resolution_x * scene.render.resolution_percentage // 100,
           scene.render.resolution_y * scene.render.resolution_percentage // 100)
    print(f"rendering frames {scene.frame_start}-{scene.frame_end} ({n} frames) "
          f"with {a.engine} at {res[0]}x{res[1]}", flush=True)

    t0 = time.time()
    bpy.ops.render.render(animation=True, write_still=True)
    dt = time.time() - t0
    print(f"done: {n} frames in {dt:.1f}s ({dt / n:.1f}s/frame) -> {a.out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
