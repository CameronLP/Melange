import gi, os
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gdk
OUT = os.environ["OUT_DIR"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
results = []
def check(label, ok, detail=""):
    ok = bool(ok); results.append(ok); print(f"{'PASS' if ok else 'FAIL'} {label} {detail}", flush=True)
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    split = win.sidebar_split
    check("F9 is the toggle-sidebar shortcut", "F9" in app.get_accels_for_action("win.toggle-sidebar"))
    win.activate_action("win.toggle-sidebar", None); check("F9 opens the sidebar", split.get_show_sidebar())
    win.activate_action("win.toggle-sidebar", None); check("F9 again closes it", not split.get_show_sidebar())
    def a():
        win.fullscreen(); split.set_show_sidebar(True)
    def b():
        win.on_key_pressed(None, Gdk.KEY_Escape, 0, 0)
        check("Esc closes the sidebar first, stays fullscreen", not split.get_show_sidebar() and win.is_fullscreen())
        win.on_key_pressed(None, Gdk.KEY_Escape, 0, 0)
    def c():
        check("Esc again leaves fullscreen", not win.is_fullscreen())
        print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
    for t, f in ((500, a), (1500, b), (2500, c)): GLib.timeout_add(t, lambda f=f: (f(), False)[1])
    return False
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
