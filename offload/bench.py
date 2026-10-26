"""Render benchmark: the same test on any machine (the PC, a Claude Code cloud session, a runner), to compare their strength.

  python bench.py [--out result.json] [--frames 60]

It mimics the editorial engine's per-frame work without any private files: draw a 1920x1080 frame with skia (gradient,
rounded cards, text, a blurred glow), copy it to numpy and blend it (like the engine's finish pass), then ffmpeg encodes.
  A. one render process         frames per second of a single process (the engine renders on one core)
  B. all cores                  one process per CPU thread at once: total frames per second (what pieces in parallel get)
  C. ffmpeg x264 encode         1080p30 test pattern, 300 frames, libx264 medium crf 18: encode fps
Needs: numpy, skia-python, ffmpeg (or `pip install imageio-ffmpeg`).
"""
import argparse, json, os, platform, shutil, subprocess, sys, time
from concurrent.futures import ProcessPoolExecutor

W, H = 1920, 1080


def draw_frames(n):
    import numpy as np, skia
    surf = skia.Surface(W, H); font = skia.Font(skia.Typeface('Arial'), 64); t0 = time.perf_counter()
    for f in range(n):
        c = surf.getCanvas(); c.clear(skia.Color(20, 14, 40))
        shader = skia.GradientShader.MakeLinear([(0, 0), (W, H)], [skia.Color(40, 20, 90), skia.Color(10, 10, 20)])
        c.drawRect(skia.Rect(0, 0, W, H), skia.Paint(Shader=shader))
        glow = skia.Paint(Color=skia.Color(150, 110, 255, 160), ImageFilter=skia.ImageFilters.Blur(30, 30), AntiAlias=True)
        c.drawCircle(W / 2 + 200 * np.sin(f / 10), H / 2, 220, glow)
        for k in range(60):
            x, y = (k * 137 + f * 7) % (W - 300), (k * 71 + f * 3) % (H - 200)
            c.drawRRect(skia.RRect.MakeRectXY(skia.Rect(x, y, x + 280, y + 170), 18, 18),
                        skia.Paint(Color=skia.Color(255, 255, 255, 40 + k % 120), AntiAlias=True))
        for k in range(8):
            c.drawString(f'the #1 thing to understand {f}-{k}', 80, 140 + k * 110, font, skia.Paint(Color=skia.ColorWHITE, AntiAlias=True))
        arr = surf.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType)
        rgb = arr[..., :3].astype(np.float32)
        out = np.clip(rgb * 0.96 + rgb.mean(axis=(0, 1)) * 0.04 + 1.5, 0, 255).astype(np.uint8)   # a finish-style pass
        _ = out.tobytes()
    return time.perf_counter() - t0


def ffmpeg_exe():
    if shutil.which('ffmpeg'): return 'ffmpeg'
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def cpu_name():
    try:
        if sys.platform == 'win32':
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0')
            return winreg.QueryValueEx(k, 'ProcessorNameString')[0].strip()
        for line in open('/proc/cpuinfo'):
            if line.startswith('model name'): return line.split(':', 1)[1].strip()
    except Exception: pass
    return platform.processor()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out'); ap.add_argument('--frames', type=int, default=60); a = ap.parse_args()
    n, threads = a.frames, os.cpu_count()
    res = {'machine': platform.node(), 'os': platform.platform(), 'cpu': cpu_name(), 'threads': threads,
           'python': platform.python_version(), 'date': time.strftime('%Y-%m-%d %H:%M')}
    draw_frames(3)                                                     # warm-up (imports, font cache)
    t = draw_frames(n); res['A_one_process_fps'] = round(n / t, 2); print(f"A one process: {res['A_one_process_fps']} frames/s", flush=True)
    t0 = time.perf_counter()
    with ProcessPoolExecutor(threads) as ex: list(ex.map(draw_frames, [n] * threads))
    res['B_all_cores_fps'] = round(n * threads / (time.perf_counter() - t0), 2)
    res['B_speedup_vs_one'] = round(res['B_all_cores_fps'] / res['A_one_process_fps'], 2)
    print(f"B all {threads} threads: {res['B_all_cores_fps']} frames/s ({res['B_speedup_vs_one']}x one process)", flush=True)
    t0 = time.perf_counter()
    subprocess.run([ffmpeg_exe(), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=30:duration=10',
                    '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-f', 'null', '-'], check=True)
    res['C_x264_encode_fps'] = round(300 / (time.perf_counter() - t0), 1); print(f"C ffmpeg x264 1080p: {res['C_x264_encode_fps']} fps", flush=True)
    print(json.dumps(res, indent=1))
    if a.out: open(a.out, 'w').write(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
