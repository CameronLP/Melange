# Headless GUI tests

Scripts written while building the mini visualizers, Now Playing and
other features. Each one drives the installed Flatpak build inside a
headless GNOME compositor, so it never touches your desktop, and with an
isolated config, so it never touches your settings. They print
`PASS`/`FAIL` lines and a `SUMMARY`.

They aren't a polished suite yet: step 2 of the mini visualizer refactor
(see TODO.md) is meant to turn the useful ones into one.

## Running one

```sh
flatpak-builder --user --install --force-clean build-dir com.cameronlp.Melange.json

OUT=$(mktemp -d); mkdir -p "$OUT/config"
mutter --headless --no-x11 --virtual-monitor 1920x1080 --wayland-display melange-headless &
env -u DISPLAY WAYLAND_DISPLAY=melange-headless \
    flatpak run --env=OUT_DIR="$OUT" --env=GDK_BACKEND=wayland \
    --env=PYTHONPATH=/app/share/melange --env=PYTHONUNBUFFERED=1 \
    --filesystem="$OUT" --command=python3 com.cameronlp.Melange - \
    < tools/headless-tests/auxfuzz.py
```

Then stop that mutter, and any Melange instances the test started
(`flatpak ps`, `flatpak kill <instance>`). Don't kill your own Melange.

Some scripts expect audio. For silent audio, make a null sink and feed it
noise, then point Melange's audio source at it:

```sh
pactl load-module module-null-sink sink_name=melange_leaktest
gst-launch-1.0 audiotestsrc wave=pink-noise ! audioconvert ! pulsesink device=melange_leaktest &
```

## What they cover

| Script | Checks |
| --- | --- |
| `auxfuzz.py` | Every control of every mini visualizer kind, at extreme values and sizes |
| `persist.py` | Mini visualizer settings and size are saved and restored |
| `colors.py` | The Colors group: rows per kind and style, old-setting conversions |
| `bg.py` | Background: Color, Main Visualizer, Transparent |
| `rev2.py` | Reverse works for every kind and gradient |
| `pan.py` | 3D pan and Center View; Main Visualizer Off page layout |
| `sw.py` | Narrow mini visualizers open settings in their own window |
| `toggle.py` | Settings sidebar toggle in the mini visualizer title bar |
| `osc.py`, `peak.py`, `vstest.py`, `square.py`, `fit.py` | Oscilloscope, Peak, Vector Scope, square scopes, 3D framing |
| `outline.py`, `npbg.py`, `ph2.py` | Now Playing outline, background, placeholder icon |
| `rc.py`, `off.py`, `turnon.py`, `qw.py`, `sidebar.py` | Main window: right-click menu, Main Visualizer off/Turn On, quick wins, sidebar shortcuts |

Known test-only failures: `persist.py` reports 3 `view_hint_shown`
mismatches (the one-time 3D hint correctly re-shows), and `bg.py` and
`qw.py` each have one check with a stale expectation.
