import gi, os
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk, Adw, Gdk, Graphene
OUT = os.environ["OUT_DIR"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
from melange import aux_window as A
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
results = []
def check(label, ok, detail=""):
    ok = bool(ok); results.append(ok); print(f"{'PASS' if ok else 'FAIL'} {label} {detail}", flush=True)
def widgets(w):
    yield w; c = w.get_first_child()
    while c: yield from widgets(c); c = c.get_next_sibling()
def shot(aw, name):
    root = aw.settings_split; w, h = root.get_width(), root.get_height()
    for _ in range(10):
        snap = Gtk.Snapshot(); Gtk.WidgetPaintable.new(root).snapshot(snap, w, h); node = snap.to_node()
        if node: break
        GLib.MainContext.default().iteration(False)
    if node: aw.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h)).save_to_png(f"{OUT}/{name}.png")
    print("shot", name, bool(node), flush=True)
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    aw = A.AuxVisualizerWindow(win, "spectrum"); aw.set_default_size(420, 300); aw.present()
    st = {}
    def a():
        check("starts closed", not aw.settings_split.get_show_sidebar() and not aw.settings_button.get_active())
        check("toggle is in the title bar", aw.settings_button.get_ancestor(Adw.HeaderBar) is aw.header)
        check("uses sidebar icon", aw.settings_button.get_icon_name() == "sidebar-show-right-symbolic")
        check("no floating overlay gear", not any(isinstance(w, Gtk.MenuButton) or (isinstance(w, Gtk.ToggleButton) and w.has_css_class("nav-arrow-button")) for w in widgets(aw.toolbar_view)))
        shot(aw, "closed")
        aw.settings_button.set_active(True)
    def b():
        check("title-bar toggle opens it", aw.settings_split.get_show_sidebar())
        inner = [w for w in widgets(aw.settings_split.get_sidebar()) if isinstance(w, Gtk.ToggleButton) and w.get_icon_name() == "sidebar-show-right-symbolic"]
        check("sidebar header has a matching toggle, shown active", len(inner) == 1 and inner[0].get_active() and inner[0].get_icon_name() == "sidebar-show-right-symbolic")
        shot(aw, "open")
        inner[0].set_active(False)
        st["inner"] = inner[0]
    def c():
        check("sidebar toggle closes it, title-bar toggle synced", not aw.settings_split.get_show_sidebar() and not aw.settings_button.get_active())
        print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
    for t, f in ((800, a), (1600, b), (2400, c)): GLib.timeout_add(t, lambda f=f: (f(), False)[1])
    return False
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
