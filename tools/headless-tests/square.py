import gi, os
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk
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
    xy = A.AuxVisualizerWindow(win, "xy"); xy.present()
    vs = A.AuxVisualizerWindow(win, "vectorscope"); vs.present()
    sp = A.AuxVisualizerWindow(win, "spectrum"); sp.present()
    steps = []
    def at(t, f): steps.append((t, f))
    at(800, lambda: (xy.set_default_size(560, 380), vs.set_default_size(300, 520), sp.set_default_size(500, 200)))
    at(1000, lambda: check("window really resizes when asked (GNOME allows it)", xy.get_width() == 560 and xy.get_height() == 380, f"{xy.get_width()}x{xy.get_height()}"))
    def after():
        check("X-Y Scope snaps square, following the wider change", xy.get_width() == xy.get_height() == 560, f"{xy.get_width()}x{xy.get_height()}")
        check("Vector Scope snaps square, following the taller change", vs.get_width() == vs.get_height() == 520, f"{vs.get_width()}x{vs.get_height()}")
        check("other kinds aren't touched", sp.get_width() == 500 and sp.get_height() == 200, f"{sp.get_width()}x{sp.get_height()}")
        sw = xy.settings_rows["Maintain Aspect Ratio"].get_activatable_widget(); sw.set_active(False)
        xy.set_default_size(600, 300)
    at(1900, after)
    def off():
        check("switch off: free shape", xy.get_width() == 600 and xy.get_height() == 300, f"{xy.get_width()}x{xy.get_height()}")
        xy.settings_rows["Maintain Aspect Ratio"].get_activatable_widget().set_active(True)
    at(2700, off)
    def on_again():
        check("switch back on: squares up", xy.get_width() == xy.get_height(), f"{xy.get_width()}x{xy.get_height()}")
        xy.maximize()
    at(3500, on_again)
    def maxed():
        check("maximized isn't forced square", xy.is_maximized() and xy.get_width() != xy.get_height(), f"{xy.get_width()}x{xy.get_height()}")
        print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
    at(4600, maxed)
    for t, f in steps: GLib.timeout_add(t, lambda f=f: (f(), False)[1])
    return False
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
