import gi, os
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
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    p = A.AuxVisualizerWindow(win, "peak"); p.show_labels = True
    check("dB by default: half amplitude reads ~90%", abs(p.db_frac(0.5) - 0.8997) < 0.01, f"{p.db_frac(0.5):.3f}")
    dd = next(w for w in p.iter_widgets(p.settings_rows["Scale"]) if isinstance(w, Gtk.DropDown)); dd.set_selected(1)
    check("Scale → Linear: half amplitude reads 50%", p.peak_scale == "linear" and abs(p.db_frac(0.5) - 0.5) < 1e-9, f"{p.db_frac(0.5):.3f}")
    d = [0.5] * 1024
    for name in ("db", "linear"):
        p.peak_scale = name; p.peak_left = p.peak_right = 0; p.peak_hold_left = p.peak_hold_right = 0
        p.push_audio(d, d)
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 140, 300); cr = cairo.Context(surf); p.on_draw(p.drawing_area, cr, 140, 300); surf.write_to_png(f"{OUT}/peak-{name}.png")
    p.close()
    print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
