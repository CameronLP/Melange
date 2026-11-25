import gi, os, sys, traceback
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk, Adw, Gdk
OUT = os.environ["OUT_DIR"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
from melange import aux_window as A
errors = []
def hook(t, v, tb):
    errors.append("".join(traceback.format_exception(t, v, tb))); 
sys.excepthook = hook
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
def walk(w):
    out = [w]; c = w.get_first_child()
    while c: out += walk(c); c = c.get_next_sibling()
    return out
def label_for(w):
    p = w.get_parent()
    while p is not None:
        for c in walk(p):
            if isinstance(c, Gtk.Label) and c.get_label(): return c.get_label()
        p = p.get_parent()
    return "?"
def pump(n=5):
    for _ in range(n): GLib.MainContext.default().iteration(False)
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    import array, random
    import time
    for kind in A.AUX_WINDOW_TITLES:
        t0=time.time(); print('START', kind, flush=True)
        aw = A.AuxVisualizerWindow(win, kind); aw.present(); pump(20)
        data = [random.uniform(-0.6, 0.6) for _ in range(2048)]
        def feed():
            try: aw.push_audio(data, data[::-1])
            except Exception: errors.append(f"[{kind}] push_audio\n" + traceback.format_exc())
            pump(3)
        feed()
        aw.settings_button.set_active(True); pump(20)
        shown = aw.settings_split.get_show_sidebar()
        if not shown: errors.append(f"[{kind}] sidebar did not open")
        box = aw.settings_split.get_sidebar()

        n = 0
        for w in walk(box):
            lab = label_for(w); before = len(errors); None
            try:
                if isinstance(w, Gtk.Switch):
                    w.set_active(not w.get_active()); feed(); w.set_active(not w.get_active()); n += 1
                elif isinstance(w, Gtk.SpinButton):
                    a = w.get_adjustment(); v = a.get_value()
                    for x in (a.get_lower(), a.get_upper(), v): w.set_value(x); feed()
                    n += 1
                elif isinstance(w, Gtk.Scale):
                    a = w.get_adjustment(); v = a.get_value()
                    for x in (a.get_lower(), a.get_upper(), v): a.set_value(x); feed()
                    n += 1
                elif isinstance(w, Gtk.DropDown):
                    s = w.get_selected()
                    for i in range(w.get_model().get_n_items()): w.set_selected(i); feed()
                    w.set_selected(s); n += 1
                elif isinstance(w, Gtk.ColorDialogButton):
                    from gi.repository import Gdk
                    c = Gdk.RGBA(); c.parse("#123456"); w.set_rgba(c); feed(); n += 1
                elif isinstance(w, Gtk.Button) and not isinstance(w, Gtk.MenuButton) and w.get_label():
                    w.emit("clicked"); feed(); n += 1
            except Exception:
                errors.append(f"[{kind}] {lab}\n" + traceback.format_exc())
            for e in errors[before:]:
                print(f"ERROR in {kind} / {lab}: {e.strip().splitlines()[-1]}", flush=True)
        ctl = Gtk.EventControllerKey()
        aw.on_key_pressed(None, Gdk.KEY_Escape, 0, 0); pump(10)
        if aw.settings_split.get_show_sidebar() or aw.settings_button.get_active(): errors.append(f"[{kind}] Esc did not close sidebar")
        aw.settings_button.set_active(True); pump(5)
        if not aw.settings_split.get_show_sidebar(): errors.append(f"[{kind}] reopen failed")
        aw.settings_split.set_show_sidebar(False); pump(5)
        if aw.settings_button.get_active(): errors.append(f"[{kind}] button not synced on close")
        # resize extremes
        for size in ((120, 80), (900, 600)):
            aw.set_default_size(*size); pump(10); feed()
        nat = "n/a (scrolls)"
        print(f"{kind}: {n} controls exercised, popover natural height {nat}px (window {aw.get_default_size()})", flush=True)
        aw.close(); pump(10)
    print("TOTAL ERRORS", len(errors)); 
    for e in errors[:6]: print("----\n" + e)
    os._exit(0)
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
