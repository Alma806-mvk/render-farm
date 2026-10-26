# render-farm

Free Blender render farm on GitHub Actions. Public repo on purpose: public repos get free, unlimited
standard runners (4 vCPU / 16 GB each, up to 40 in parallel on GitHub Pro).

- `.github/workflows/blender-farm.yml`: splits a frame range across runners, renders with Blender's
  `bpy` pip package on CPU, stitches an MP4 with ffmpeg.
- `tools/cloud-render/render_blend_cpu.py`: the renderer (tested in a Claude Code cloud session).
- `scenes/demo.blend`: 24-frame test scene (rotating cube + sphere, 1920x1080).

Run it from the Actions tab: "Blender render farm" -> Run workflow. Download `render-mp4` when done.
Nothing private goes in this repo.
