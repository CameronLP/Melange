import gi, os, math
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
def lit(aw):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 300, 160); cr = cairo.Context(surf); aw.on_draw(aw.drawing_area, cr, 300, 160); surf.flush()
    d = surf.get_data(); return sum(1 for i in range(0, len(d), 4) if d[i + 1] > 150 and d[i] < 150)
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    o = A.AuxVisualizerWindow(win, "oscilloscope"); o.show_labels = False; o.background_mode = "black"
    s = [math.sin(i * 0.05) * 0.7 for i in range(2048)]; o.push_audio(s, s)
    thin = lit(o); o.settings_rows["Line Width"].get_activatable_widget() if False else None
    scale = next(w for w in o.iter_widgets(o.settings_rows["Line Width"]) if isinstance(w, Gtk.Scale)); scale.set_value(4.0)
    thick = lit(o)
    check("Oscilloscope Line Width thickens the trace", o.scope_line_width == 4.0 and thick > thin * 2, f"{thin} -> {thick} lit px")
    p = A.AuxVisualizerWindow(win, "pipes"); p.present()
    def a():
        p.lookup_action("compact-mode").change_state(GLib.Variant("b", True))
        p.mouse_move(None, 100, 100)
        check("moving the mouse reveals the grip", p.move_handle.has_css_class("revealed"))
    def b():
        check("grip fades after ~3 s still", not p.move_handle.has_css_class("revealed"))
        p.mouse_move(None, 120, 110)
        check("…and comes back on movement", p.move_handle.has_css_class("revealed"))
        o.close(); p.close()
        print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
    GLib.timeout_add(800, lambda: (a(), False)[1]); GLib.timeout_add(4600, lambda: (b(), False)[1])
    return False
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
