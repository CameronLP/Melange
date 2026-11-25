import gi, os, random, math
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk
import cairo
OUT = os.environ["OUT_DIR"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
from melange import aux_window as A
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
results = []
def check(label, ok, detail=""):
    ok = bool(ok); results.append(ok); print(f"{'PASS' if ok else 'FAIL'} {label} {detail}", flush=True)
def extent(aw, amp, frames=40, name=None):
    aw.vector_surface = None
    for _ in range(frames):
        l = [max(-1, min(1, random.gauss(0, amp))) for _ in range(1024)]
        r = [max(-1, min(1, random.gauss(0, amp))) for _ in range(1024)]
        aw.push_audio(l, r)
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 300, 300); cr = cairo.Context(surf)
        aw.on_draw(aw.drawing_area, cr, 300, 300)
    surf.flush()
    if name: surf.write_to_png(f"{OUT}/{name}.png")
    d = surf.get_data(); st = surf.get_stride(); far = 0; lit_r = []
    for y in range(0, 300, 2):
        for x in range(0, 300, 2):
            o = y * st + x * 4
            if d[o + 1] > 120 and d[o] < 120:   # green trace pixel
                rr = max(abs(x - 150), abs(y - 150)) / 138
                lit_r.append(rr)
    lit_r.sort()
    return lit_r[int(len(lit_r) * 0.99)] if lit_r else 0
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    aw = A.AuxVisualizerWindow(win, "vectorscope"); aw.present()
    aw.show_labels = False; aw.background_mode = "black"
    loud = extent(aw, 0.55, name="loud-auto")
    check("loud wide stereo, Auto Gain: cloud stays off the edges", loud < 0.95, f"99th-percentile reach {loud:.2f} of edge")
    quiet = extent(aw, 0.03, frames=120, name="quiet-auto")
    check("quiet signal, Auto Gain: still fills a good part", quiet > 0.4, f"reach {quiet:.2f}")
    aw.vector_auto_gain = False; aw.vector_envelope = 0
    loud_fixed = extent(aw, 0.55, name="loud-fixed")
    check("Auto Gain off: full-scale fits inside (no longer sqrt2 too big)", loud_fixed <= 1.0, f"reach {loud_fixed:.2f}")
    print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
