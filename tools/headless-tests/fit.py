import gi, os, math, random
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib
import cairo
OUT = os.environ["OUT_DIR"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
from melange import aux_window as A
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
def sig(): return [math.sin(i * 0.05) * 0.8 + random.uniform(-0.2, 0.2) for i in range(2048)]
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    ok = True
    for kind in ("terrain", "waterfall"):
        aw = A.AuxVisualizerWindow(win, kind); aw.background_mode = "transparent"; setattr(aw, f"{kind}_show_axes", True)
        for _ in range(10): aw.push_audio(sig(), sig()); aw.last_terrain_frame_time = aw.last_waterfall_frame_time = 0
        for el in (5, 30, 55, 85):
            setattr(aw, f"{kind}_elevation", math.radians(el)); aw.terrain_dirty = aw.waterfall_dirty = True
            w, h = 360, 220
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w * 3, h * 3); cr = cairo.Context(surf); cr.translate(w, h)
            aw.on_draw(aw.drawing_area, cr, w, h); surf.flush(); d = surf.get_data(); st = surf.get_stride()
            top = sum(1 for y in range(0, h) for x in range(w, 2 * w, 3) if d[y * st + x * 4 + 3] > 40)
            print(f"{kind} elevation {el}: pixels above the window = {top}", flush=True); ok &= top == 0
        aw.close()
    print("default terrain elevation:", round(math.degrees(A.AuxVisualizerWindow(win, "terrain").terrain_elevation)), flush=True)
    print("RESULT", "PASS" if ok else "FAIL", flush=True); os._exit(0)
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
