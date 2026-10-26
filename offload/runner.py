"""Offload runner: the one script every remote machine runs (GitHub runner, Kaggle kernel, Modal container, Camber job).

It is self-contained (standard library + `cryptography`), so each backend can ship it as a single file.

  python runner.py --manifest manifest.json      --machine 0 --root /work --out /out
  python runner.py --manifest-url URL (key in env OFFLOAD_KEY) --machine 0 --root /work --out /out --encrypt-result

What it does:
  1. downloads the job's layers (encrypted .ofl files from the store), checks them, decrypts and unpacks them into --root
  2. installs what the job needs (pip packages; ffmpeg if the machine has none)
  3. runs this machine's chunks, `per_machine` at a time, each with its own log
  4. packs outputs, logs and timings into --out/result.tar (or result.ofl, encrypted with the job's result key)

Public logs (GitHub) only ever show progress lines: command output goes into the per-chunk logs inside the result.
"""
import argparse, base64, concurrent.futures as cf, hashlib, io, json, os, shutil, struct, subprocess, sys, tarfile, time, urllib.request
from pathlib import Path

MAGIC = b'OFL1'
SEG = 32 << 20                                   # 32 MB plaintext per AES-GCM segment


# ---------- encryption (AES-256-GCM, segmented so big files never sit in memory twice) ----------
def _aes(key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key)


def encrypt_file(src, dst, key):
    a, nonce = _aes(key), os.urandom(8)
    with open(src, 'rb') as f, open(dst, 'wb') as g:
        g.write(MAGIC + nonce); i = 0
        while True:
            block = f.read(SEG)
            if not block and i: break
            ct = a.encrypt(nonce + struct.pack('>I', i), block, MAGIC)
            g.write(struct.pack('>I', len(ct)) + ct); i += 1
            if len(block) < SEG: break


def decrypt_file(src, dst, key):
    a = _aes(key)
    with open(src, 'rb') as f, open(dst, 'wb') as g:
        head = f.read(12)
        if head[:4] != MAGIC: raise ValueError(f'{src}: not an offload file')
        nonce, i = head[4:], 0
        while True:
            n = f.read(4)
            if not n: break
            g.write(a.decrypt(nonce + struct.pack('>I', i), f.read(struct.unpack('>I', n)[0]), MAGIC)); i += 1


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(8 << 20), b''): h.update(b)
    return h.hexdigest()


# ---------- setup ----------
def log(msg): print(time.strftime('%H:%M:%S'), msg, flush=True)


def download(url, path, tries=8):
    for k in range(tries):                       # GitHub's release CDN answered 504 for over 20 s on 26 Oct: back off up to ~4 min
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'offload-runner/1'})
            with urllib.request.urlopen(req, timeout=600) as r, open(path, 'wb') as f: shutil.copyfileobj(r, f, 8 << 20)
            return
        except Exception as e:
            if k == tries - 1: raise
            wait = min(60, 5 * 2 ** k); log(f'download failed ({e}); retry in {wait} s'); time.sleep(wait)


def fetch_layer(layer, root, tmp):
    enc, plain = tmp / f"{layer['id']}.ofl", tmp / f"{layer['id']}.tar"
    if layer.get('path'): enc = Path(layer['path'])                      # already on disk (Camber stash, local tests)
    else: download(layer['url'], enc)
    if layer.get('sha256') and sha256(enc) != layer['sha256']: raise ValueError(f"layer {layer['id']}: checksum mismatch")
    decrypt_file(enc, plain, base64.b64decode(layer['key']))
    with tarfile.open(plain) as t:
        try: t.extractall(root / layer.get('dest', ''), filter='fully_trusted')
        except TypeError: t.extractall(root / layer.get('dest', ''))      # Python older than 3.11.4
    for p in (enc, plain):
        if not layer.get('path') or p == plain: p.unlink(missing_ok=True)
    return layer['id']


def ensure_ffmpeg(bindir):
    if shutil.which('ffmpeg'): return 'system'
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'imageio-ffmpeg'], check=True)
    import imageio_ffmpeg
    bindir.mkdir(parents=True, exist_ok=True); exe = bindir / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
    if not exe.exists(): shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), exe); exe.chmod(0o755)
    os.environ['PATH'] = f'{bindir}{os.pathsep}' + os.environ['PATH']
    return 'imageio-ffmpeg'


FFMPEG_STATIC = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz'


def static_ffmpeg(bindir):
    """ffmpeg + ffprobe as one static download from GitHub (seconds), instead of `apt install ffmpeg` (minutes)."""
    if shutil.which('ffprobe') and shutil.which('ffmpeg'): return 'system'
    bindir.mkdir(parents=True, exist_ok=True); arc = bindir / 'ffmpeg.tar.xz'
    download(FFMPEG_STATIC, arc)
    with tarfile.open(arc) as t:
        for mbr in t.getmembers():
            if mbr.name.endswith(('/bin/ffmpeg', '/bin/ffprobe')):
                mbr.name = Path(mbr.name).name; t.extract(mbr, bindir)
    arc.unlink()
    os.environ['PATH'] = f'{bindir}{os.pathsep}' + os.environ['PATH']
    return 'static'


def apt(pkgs, libs, bins=()):
    """System packages (e.g. libegl1 for skia, ffmpeg for ffprobe), only when one of `libs` or `bins` is missing and apt is
    usable (root or sudo)."""
    import ctypes.util
    have = (not libs or all(ctypes.util.find_library(l) for l in libs)) and all(shutil.which(b) for b in bins)
    if not pkgs or ((libs or bins) and have) or not shutil.which('apt-get'): return 'skipped'
    pre = [] if os.geteuid() == 0 else (['sudo', '-n'] if shutil.which('sudo') else None)
    if pre is None: return 'no root'
    env = dict(os.environ, DEBIAN_FRONTEND='noninteractive')
    for attempt in (1, 2):                       # apt has no timeout of its own: one stalled mirror held a machine 11+ min (26 Oct)
        try:
            subprocess.run(pre + ['apt-get', 'update', '-qq'], env=env, stdout=subprocess.DEVNULL, check=True, timeout=180)
            subprocess.run(pre + ['apt-get', 'install', '-y', '-qq', '--no-install-recommends'] + pkgs, env=env,
                           stdout=subprocess.DEVNULL, check=True, timeout=300)
            return 'installed' if attempt == 1 else 'installed (retry)'
        except subprocess.TimeoutExpired:
            log(f'apt stalled (attempt {attempt})')
    raise RuntimeError('apt stalled twice')


def setup(m, root):
    t = time.time(); s = m.get('setup', {}); pip = s.get('pip', []); steps = {}
    def lap(name):
        nonlocal t; steps[name] = round(time.time() - t, 1); t = time.time(); log(f'setup {name[:-2]} done in {steps[name]} s')
    apt_state = apt(s.get('apt', []), s.get('apt_libs', []), s.get('apt_bins', [])); lap('apt_s')
    wheels = root / s['wheels'] if s.get('wheels') else None          # the job's packages shipped as a store layer
    if pip and not (wheels and wheels.exists() and subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '-q', '--disable-pip-version-check', '--no-index', '--find-links', str(wheels)] + pip,
            timeout=600).returncode == 0):
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--disable-pip-version-check'] + pip, check=True, timeout=900)
    lap('pip_s')
    how = static_ffmpeg(root / '.offload_bin') if s.get('static_ffmpeg') else ensure_ffmpeg(root / '.offload_bin'); lap('ffmpeg_s')
    if s.get('warmup'):                                              # once per machine, before pieces run in parallel (model downloads)
        ctx = {'python': sys.executable, 'root': str(root)}
        env = dict(os.environ, **{k: fmt(v, ctx) for k, v in m.get('env', {}).items()})
        try: subprocess.run([fmt(x, ctx) for x in s['warmup']], cwd=root / m.get('workdir', ''), env=env, stdout=subprocess.DEVNULL,
                            check=False, timeout=600)
        except subprocess.TimeoutExpired: log('warmup stalled: pieces will fetch what they need themselves')
    lap('warmup_s')
    return {'setup_s': round(sum(steps.values()), 1), **steps, 'ffmpeg': how, 'apt': apt_state, 'wheels': bool(wheels and wheels.exists())}


# ---------- run ----------
def fmt(arg, ctx):
    return arg.format(**ctx) if isinstance(arg, str) else str(arg)


def run_chunk(m, c, root, out):
    ctx = dict(c, root=str(root), out=str(out / f"chunk_{c['i']:04d}{m.get('out_ext', '')}"), python=sys.executable,
               n=len(m['chunks']), outdir=str(out))
    cmd = [fmt(a, ctx) for a in m['command']]
    env = dict(os.environ, OFFLOAD_ROOT=str(root), **{k: fmt(v, ctx) for k, v in m.get('env', {}).items()})
    t = time.time()
    with open(out / f"chunk_{c['i']:04d}.log", 'w', encoding='utf-8') as lf:
        try:                                     # a stuck piece fails after piece_timeout_s instead of holding the machine for hours
            code = subprocess.run(cmd, cwd=root / m.get('workdir', ''), env=env, stdout=lf, stderr=subprocess.STDOUT,
                                  timeout=m.get('piece_timeout_s', 1800)).returncode
        except subprocess.TimeoutExpired:
            lf.write(f"\n[offload] piece {c['i']} stopped after {m.get('piece_timeout_s', 1800)} s (stuck)\n"); code = 124
    return {'i': c['i'], 'exit': code, 'seconds': round(time.time() - t, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifest'); ap.add_argument('--manifest-url')
    ap.add_argument('--machine', type=int, required=True)
    ap.add_argument('--root', default='work'); ap.add_argument('--out', default='out')
    ap.add_argument('--encrypt-result', action='store_true')
    a = ap.parse_args()
    t0 = time.time(); root, out = Path(a.root).resolve(), Path(a.out).resolve()
    root.mkdir(parents=True, exist_ok=True); out.mkdir(parents=True, exist_ok=True); tmp = root / '.offload_tmp'; tmp.mkdir(exist_ok=True)

    if a.manifest_url:
        download(a.manifest_url, tmp / 'manifest.ofl'); decrypt_file(tmp / 'manifest.ofl', tmp / 'manifest.json', base64.b64decode(os.environ['OFFLOAD_KEY']))
        m = json.loads((tmp / 'manifest.json').read_text('utf-8')); (tmp / 'manifest.json').unlink()
    else:
        m = json.loads(Path(a.manifest).read_text('utf-8'))
    mine = [m['chunks'][i] for i in m['assign'][a.machine]]
    log(f"job {m['job']} machine {a.machine}: {len(mine)} chunks, {os.cpu_count()} cpus")

    stats = {'machine': a.machine, 'cpus': os.cpu_count()}
    t = time.time()
    with cf.ThreadPoolExecutor(4) as ex: list(ex.map(lambda L: fetch_layer(L, root, tmp), m['layers']))
    stats['fetch_s'] = round(time.time() - t, 1); log(f"layers ready in {stats['fetch_s']} s")
    stats.update(setup(m, root)); log(f"setup done in {stats['setup_s']} s")

    t = time.time(); res = []
    with cf.ThreadPoolExecutor(max(1, m.get('per_machine', 1))) as ex:
        for r in ex.map(lambda c: run_chunk(m, c, root, out), mine):
            res.append(r); log(f"chunk {r['i']}: {'ok' if r['exit'] == 0 else 'FAILED exit %d' % r['exit']} in {r['seconds']} s")
    stats.update(render_s=round(time.time() - t, 1), total_s=round(time.time() - t0, 1), chunks=res)
    (out / f'timing_m{a.machine}.json').write_text(json.dumps(stats, indent=1))

    tar = out.parent / f'result_m{a.machine}.tar'
    with tarfile.open(tar, 'w') as tf:
        for p in sorted(out.iterdir()): tf.add(p, arcname=p.name)
    if a.encrypt_result:
        encrypt_file(tar, out / 'result.ofl', base64.b64decode(m['result_key'])); tar.unlink()
    else:
        shutil.move(tar, out / 'result.tar')
    failed = [r['i'] for r in res if r['exit']]
    log(f"done in {stats['total_s']} s" + (f", failed chunks {failed}" if failed else ''))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
