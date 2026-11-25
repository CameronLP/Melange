import gi, os, math, random, sys, traceback
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib
import cairo
OUT = os.environ["OUT_DIR"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
from melange import aux_window as A
from melange import mini_visualizer_base as B
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
def sig(): return [math.sin(i * 0.05) * 0.6 + random.uniform(-0.2, 0.2) for i in range(2048)]
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    try:
        fails, checked = [], 0
        for kind in A.AUX_WINDOW_TITLES:
            if kind in ("dvd", "pipes"): continue
            aw = A.AuxVisualizerWindow(win, kind)
            heat = hasattr(aw, f"{kind}_palette")
            for key, label in B.PALETTE_CHOICES:
                if heat: setattr(aw, f"{kind}_palette", key)
                else: aw.color_style = "gradient"; aw.line_gradient = key
                aw.reverse_colors = True
                for _ in range(2): aw.push_audio(sig(), sig())
                surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 160, 120); aw.on_draw(aw.drawing_area, cairo.Context(surf), 160, 120)
                slot = "palette" if heat else "mode"
                lut = B._PALETTE_LUTS.get(f"effective-{id(aw)}-{slot}")
                lo = getattr(aw, f"{kind}_color_lo", None) if heat else aw.line_color_lo
                hi = getattr(aw, f"{kind}_color_hi", None) if heat else aw.line_color_hi
                own = [B.palette_color(key, i / 255, lo, hi) for i in range(256)]
                checked += 1
                if not (lut and all(max(abs(a - b) for a, b in zip(lut[i], own[255 - i])) < 1e-6 for i in range(256))):
                    fails.append(f"{kind}/{label}")
            aw.close()
        print(f"checked {checked}; reversed exactly: {checked - len(fails)}; fails {fails[:5]}", flush=True)
    except Exception:
        traceback.print_exc()
    sys.stdout.flush(); os._exit(0)
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
