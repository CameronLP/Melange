import gi, os, sys, traceback
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk
OUT = os.environ["OUT_DIR"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
errors = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
results = []
def check(label, ok, detail=""):
    ok = bool(ok); results.append(ok); print(f"{'PASS' if ok else 'FAIL'} {label} {detail}", flush=True)
def popovers(w):
    n = 0; c = w.get_first_child()
    while c:
        if isinstance(c, Gtk.PopoverMenu): n += 1
        c = c.get_next_sibling()
    return n
def labels(m):
    out = []
    for i in range(m.get_n_items()):
        v = m.get_item_attribute_value(i, "label", None)
        if v: out.append(v.get_string())
        sec = m.get_item_link(i, "section")
        if sec: out += labels(sec)
    return out
def pump(n=10):
    for _ in range(n): GLib.MainContext.default().iteration(False)
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    box = win.content_box
    win.on_content_right_click(None, 1, 200, 200); pump()
    pop = next(c for c in [box.get_first_child()] + [] if False) if False else None
    c = box.get_first_child(); pops = []
    while c:
        if isinstance(c, Gtk.PopoverMenu): pops.append(c)
        c = c.get_next_sibling()
    check("right-click opens a menu even with controls showing", len(pops) == 1 and pops[0].get_visible())
    got = labels(pops[0].get_menu_model())
    check("menu items", got == ["Next Preset", "Previous Preset", "Favorite", "Pause", "Lock Preset", "Shuffle", "Hide Controls", "Transparency Mode", "Now Playing", "Fullscreen", "Browse Presets…", "Load Preset…", "Preferences…"], str(got))
    pops[0].popdown(); pump(20)
    for _ in range(5):
        win.on_content_right_click(None, 1, 100, 100); pump(); 
        c = box.get_first_child()
        while c:
            if isinstance(c, Gtk.PopoverMenu): c.popdown()
            c = c.get_next_sibling()
        pump(20)
    check("closed menus are detached (no pile-up)", popovers(box) == 0, f"{popovers(box)} attached after 6 right-clicks")
    before = win.current_preset_name
    win.activate_action("win.next-preset", None)
    check("menu actions work (Next Preset)", win.current_preset_name != before)
    win.lookup_action("immersive-mode").change_state(GLib.Variant("b", True))
    win.on_content_right_click(None, 1, 50, 50); pump()
    check("with controls hidden the menu still opens (Hide Controls checked)", popovers(box) == 1 and win.lookup_action("immersive-mode").get_state().get_boolean())
    check("no exceptions", not errors, errors[0][-400:] if errors else "")
    print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
