# Exploring a switch from Butterchurn/WebKitGTK to libprojectM

Status: **feasibility spike done (2026-11-24); no app code changed yet.**
Prototype: `tools/projectm-spike/` (standalone, not used by Melange).

## Summary

Native libprojectM rendering into a `Gtk.GLArea` works from Python via
`ctypes`. It plays real `.milk` presets with live PipeWire audio at
~59 fps on this machine. Compared with Melange's current main window,
it uses about **a third of the CPU and a quarter of the memory**. Two
integration problems had to be solved first: the render target and a
locale bug. Both have small, known workarounds.

## What the spike does

`tools/projectm-spike/projectm_spike.py`:

- opens a GTK4 window with a `Gtk.GLArea` (OpenGL 3.3 core),
- loads libprojectM 4 with `ctypes` (projectM has no Python binding),
- captures audio with the same GStreamer/PipeWire pipeline as Melange
  (`start_system_audio`) and feeds it with `projectm_pcm_add_int16`,
- loads `.milk` presets from a directory and switches between them with
  smooth transitions,
- prints fps, CPU, RSS, per-frame render time and preset failures, and
  can save PNG captures of chosen frames.

`tools/projectm-spike/pm_offscreen.c` is a minimal C program that
renders projectM into an offscreen EGL framebuffer with no GTK. It was
used to tell projectM problems apart from GTK integration problems.

## Results

Headless (`mutter --headless`), Intel Iris Xe (Mesa 25.3, GL 4.6 core),
pink noise into a null sink, 30 s measured after a 10 s warm-up:

| | Melange today (main window only) | projectM spike |
|---|---|---|
| Processes | Python + WebKit page + WebKit network | 1 (Python) |
| CPU (one core = 100%) | ~13% Python + ~43% WebKit page ≈ **56%** | **17–19%** |
| Memory (RSS) | 193 + 451 + 59 ≈ **700 MB** | **~180 MB** |
| Frame rate | display rate | 58.8 fps |
| projectM render call | n/a | ~1.5 ms/frame |

RSS double-counts shared libraries across WebKit's processes, so the
real memory gap is somewhat smaller than 700 vs 180, but still large.
GPU load was not measured. The spike has none of Melange's UI, so a
real port would sit somewhat above the spike's numbers.

Preset loading: 17 loads across 4 runs from Fedora's classic collection
(`/usr/share/projectM/presets`, 4,188 `.milk` files): 0 failures, all
visibly rendering. That's a small sample, not a compatibility rate.

## Problems found, and their workarounds

### 1. Fedora's projectM is too old for a C binding

Fedora 43 ships projectM **3.1.12**, which only has a C++ API. The
stable C API that `ctypes` needs is projectM **4.x**. So Melange would
build libprojectM 4 itself as a flatpak module: CMake, about a minute
to build, LGPL-2.1. Its `projectm-eval` dependency is a git submodule;
release tarballs include it, development snapshots don't.

### 2. projectM 4.1.x always draws to framebuffer 0

`ProjectM::RenderFrame()` ends with `glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 0)`
(its own comment says "ToDo: Allow external apps to provide a custom
target framebuffer"). A `Gtk.GLArea` renders into its own framebuffer,
so the output would never appear. Options:

- projectM's development branch adds
  `projectm_opengl_render_frame_fbo(instance, fbo)`, which does exactly
  this, but isn't released yet. The spike was tested against master at
  `9e26f5b5bf89b015f197a16a61bcb21f4946d985`.
- For 4.1.8 (latest release), a 2-line patch
  (`tools/projectm-spike/projectm-4.1.8-render-to-bound-fbo.patch`)
  makes it draw into whichever framebuffer is bound. Tested; same
  results as master.

The spike uses `render_frame_fbo` when the library has it and falls
back to the patched `render_frame` otherwise.

### 3. Locale: every preset renders black under a comma-decimal locale

This was the hard one. Inside GTK, every preset, including projectM's
built-in idle preset, rendered pure black, with no GL errors and no
preset-load failures. The same library rendered correctly in the
offscreen C test.

The cause is that GTK switches the process to the user's locale
(`nb_NO.UTF-8` here), where decimals use a comma. projectM parses
`.milk` values with locale-dependent C functions, so `0.5` mis-parses
and presets compute nothing visible. The fix is to keep
`LC_NUMERIC` as `"C"`:

```python
locale.setlocale(locale.LC_NUMERIC, "C")
```

This affects any user with a comma-decimal locale (most of Europe), so
it must be in the port from day one. It looks unreported upstream
(nothing found searching projectM's issues for "locale"), so it's worth
filing.

Ruled out on the way: GTK's GL state (blend, scissor, samplers, pixel
store all default), the GTK framebuffer itself (rendering into our own
framebuffer and blitting was black too), and audio input.

### 4. Some presets output alpha = 0

MilkDrop never used alpha and some presets leave it at 0, which GTK
would composite as transparent. The spike forces alpha to 1 after each
frame (`glColorMask(0,0,0,1)` + clear). Verified in captures.

### 5. Don't load a preset before the area has a size

At `realize` the GLArea is still 0×0. The spike creates projectM in
`realize` but sets the window size and loads the first preset on the
first render with a real size.

## What a real port would involve

From the existing TODO entry, plus what the spike showed:

- **Presets:** Butterchurn's JSON packs (core + baron) can't be used.
  Melange would ship or download a `.milk` collection instead, e.g.
  projectM's own preset packs (check licences before bundling).
  Favorites and playlists are keyed on Butterchurn preset names and
  won't carry over automatically.
- **Moves from JS to Python:** preset list/queue/history/shuffle,
  cycling timer, favorites, playlists, loading user `.milk` files.
  projectM also has its own optional playlist library
  (`libprojectM-4-playlist`).
- **Beat/drop detection** (JS today, experimental): port to Python on
  the same PCM, or use projectM's built-in beat sensitivity and hard
  cuts.
- **Settings mapping:** sensitivity → scale PCM or
  `projectm_set_beat_sensitivity`; transition time →
  `projectm_set_soft_cut_duration`; frame-rate cap → render-loop
  throttling; render scale → render to a smaller framebuffer and scale
  up, or `projectm_set_mesh_size` for per-vertex detail.
- **Removed entirely:** WebKitGTK, the local HTTP server, the
  base64/`evaluate_javascript` audio bridge, Vite build step, shader
  repair hacks for converted presets.
- **Should keep working with small changes:** mirror windows
  (`Gtk.WidgetPaintable` of the GLArea instead of the WebView), Now
  Playing overlay and header bar (already GTK), aux windows (separate,
  unaffected).
- **Packaging:** add a libprojectM module to
  `com.cameronlp.Melange.json` (pinned master commit, or 4.1.8 + the
  patch until 4.2 ships), and a preset collection.

## Open questions

1. Replace Butterchurn outright, or ship projectM as a selectable
   backend for a while?
2. Which `.milk` collection to ship, and how to handle existing
   favorites/playlists.
3. On-screen check on the real desktop (everything here was headless),
   including HiDPI scaling and fullscreen.
4. GPU load compared with WebKit/WebGL.
5. Preset failure rate over a large sample (hundreds of presets), and
   how slow-loading presets affect frame times during transitions.
