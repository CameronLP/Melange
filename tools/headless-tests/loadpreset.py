import gi, os
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk
OUT = os.environ["OUT_DIR"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
results = []
def check(label, ok, detail=""):
    ok = bool(ok); results.append(ok); print(f"{'PASS' if ok else 'FAIL'} {label} {detail}", flush=True)
def labels(m):
    out = []
    for i in range(m.get_n_items()):
        v = m.get_item_attribute_value(i, "label", None)
        if v: out.append(v.get_string().replace("_", ""))
        for link in ("section", "submenu"):
            sub = m.get_item_link(i, link)
            if sub: out += labels(sub)
    return out
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    check("main menu has Load Preset…", "Load Preset…" in labels(win.menu_button.get_menu_model()))
    win.on_content_right_click(None, 1, 100, 100)
    pops = []; c = win.content_box.get_first_child()
    while c:
        if isinstance(c, Gtk.PopoverMenu): pops.append(c)
        c = c.get_next_sibling()
    check("right-click menu has Load Preset…", pops and "Load Preset…" in labels(pops[-1].get_menu_model()))
    for p in pops: p.popdown()
    opened = []
    Gtk.FileDialog.open = lambda self, parent, cancellable, callback: opened.append([f.get_name() for f in self.get_filters()])
    win.activate_action("win.load-preset", None)
    check("choosing it opens the .milk file picker", opened == [["MilkDrop presets"]], str(opened))
    print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
