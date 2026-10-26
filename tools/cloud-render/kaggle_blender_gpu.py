# Blender GPU rendering on Kaggle (free: ~30 GPU-hours/week, no credit card).
#
# Paste this whole file into ONE Kaggle notebook cell.
# Notebook settings (right sidebar): Accelerator = "GPU T4 x2", Internet = On.
# Kaggle requires phone verification before GPU/Internet can be enabled, and users aged
# 13-16 need Kaggle's parent/guardian consent form (kaggle.com/guardian-consent-minor-use).
#
# What it does: downloads the official Blender Linux build, enables Cycles GPU rendering
# (OptiX first, CUDA fallback, both T4s), renders the frame range, zips the frames into
# /kaggle/working/frames.zip (download it from the notebook's Output tab).
#
# STATUS: NOT tested by Claude. The research session (2026-10-26) had no GPU and could not
# reach download.blender.org. It follows the standard wget-and-render pattern, so expect small
# fixes on first run (most likely the exact tarball name). Check the listing at
# https://download.blender.org/release/Blender5.2/ and set BLENDER_VER to a file that exists.

import glob
import os
import shutil
import subprocess

BLENDER_VER = "5.2.3"                               # must match a file in download.blender.org/release/Blender5.2/
# BLEND can be a Kaggle dataset path (/kaggle/input/YOUR-DATASET/scene.blend) or an http(s) URL.
# Default: the public demo scene from the render-farm repo (24 frames, 1920x1080), downloaded automatically.
BLEND = "https://raw.githubusercontent.com/Alma806-mvk/render-farm/main/scenes/demo.blend"
OUT = "/kaggle/working/frames"
START, END = 1, 24
SAMPLES = 64                                        # Cycles samples; 0 keeps the file's setting

if BLEND.startswith("http"):
    local = "/kaggle/working/" + BLEND.rsplit("/", 1)[-1]
    subprocess.run(["wget", "-q", "-O", local, BLEND], check=True)
    BLEND = local

major_minor = ".".join(BLENDER_VER.split(".")[:2])
folder = f"blender-{BLENDER_VER}-linux-x64"
tarball = f"{folder}.tar.xz"
if not os.path.isdir(folder):
    url = f"https://download.blender.org/release/Blender{major_minor}/{tarball}"
    print("downloading", url)
    subprocess.run(["wget", "-q", url], check=True)
    subprocess.run(["tar", "xf", tarball], check=True)
blender = f"./{folder}/blender"

GPU_SETUP = r'''
import bpy, sys
prefs = bpy.context.preferences.addons["cycles"].preferences
chosen = "NONE"
for backend in ("OPTIX", "CUDA"):
    try:
        prefs.compute_device_type = backend
        prefs.get_devices()
        if any(d.type == backend for d in prefs.devices):
            chosen = backend
            break
    except Exception as exc:
        print("backend", backend, "not available:", exc)
for d in prefs.devices:
    d.use = d.type in (chosen, "CPU")
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "GPU" if chosen != "NONE" else "CPU"
samples = int(sys.argv[sys.argv.index("--") + 1])
if samples:
    scene.cycles.samples = samples
print("Cycles backend:", chosen, "| devices:", [(d.name, d.type, d.use) for d in prefs.devices])
'''
with open("gpu_setup.py", "w") as fh:
    fh.write(GPU_SETUP)

os.makedirs(OUT, exist_ok=True)
cmd = [
    blender, "-b", BLEND, "--python", "gpu_setup.py",
    "-o", f"{OUT}/frame_####", "-F", "PNG",
    "-s", str(START), "-e", str(END), "-a",
    "--", str(SAMPLES),
]
print(" ".join(cmd))
subprocess.run(cmd, check=True)

shutil.make_archive("/kaggle/working/frames", "zip", OUT)
print("done:", len(glob.glob(f"{OUT}/*.png")), "frames -> /kaggle/working/frames.zip")
