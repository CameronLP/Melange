import gi, os, sys, traceback
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk, Graphene
OUT = os.environ["OUT_DIR"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
from melange import aux_window as A
errors = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
results = []
def check(label, ok, detail=""):
    ok = bool(ok); results.append(ok); print(f"{'PASS' if ok else 'FAIL'} {label} {detail}", flush=True)
def run(win):
    v = win.projectm_view
    if v is None or not v.size_known: return True
    win.lookup_action("audio-source").change_state(GLib.Variant("s", "melange_leaktest"))
    aw = A.AuxVisualizerWindow(win, "vu"); win.aux_windows["vu"] = aw; aw.present()
    st = {"renders": 0, "aux": 0}
    orig_r = v.on_render
    def counting_render(*a): st["renders"] += 1; return orig_r(*a)
    v.disconnect_by_func(orig_r) if False else None
    orig_push = aw.push_audio
    def counting_push(l, r): st["aux"] += 1; orig_push(l, r)
    aw.push_audio = counting_push
    orig_q = v.queue_render
    def q(): st["renders"] += 1; orig_q()
    v.queue_render = q
    def a():
        win.presets.set_cycle_interval(1)
        win.lookup_action("main-visualizer").change_state(GLib.Variant("b", False))
        st["r0"], st["a0"], st["preset"] = st["renders"], st["aux"], win.current_preset_name
    def b():
        check("off: visualizer hidden, status page shown", not v.get_visible() and win.main_off_page.get_visible())
        check("off: projectM paused (no audio into it)", v.paused)
        check("off: no render requests", st["renders"] - st["r0"] <= 1, f"{st['renders'] - st['r0']} in 3 s")
        check("off: mini visualizers still get audio", st["aux"] - st["a0"] > 20, f"{st['aux'] - st['a0']} chunks in 3 s")
        check("off: no auto-cycling", win.current_preset_name == st["preset"])
        check("off: floating buttons hidden", not win.prev_arrow_button.get_visible() and not win.favorite_button.get_visible())
        win.lookup_action("pause").change_state(GLib.Variant("b", True))
        check("Pause while off: no badge over the off page", not win.paused_badge.get_visible())
        root = win.toolbar_view; w, h = root.get_width(), root.get_height()
        snap = Gtk.Snapshot(); Gtk.WidgetPaintable.new(root).snapshot(snap, w, h); node = snap.to_node()
        if node: win.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h)).save_to_png(f"{OUT}/off.png")
        btn = win.main_off_page.get_child(); btn.emit("clicked")
    def c():
        check("Turn On: visualizer back, page gone", v.get_visible() and not win.main_off_page.get_visible() and win.lookup_action("main-visualizer").get_state().get_boolean())
        check("…still paused (Pause was on), badge shows", v.paused and win.paused_badge.get_visible())
        win.lookup_action("pause").change_state(GLib.Variant("b", False))
        check("unpause: running again", not v.paused and win.prev_arrow_button.get_visible())
        win.presets.set_cycle_interval(0)
        check("no exceptions", not errors, errors[0][-400:] if errors else "")
        print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
    for t, f in ((1000, a), (4000, b), (5000, c)): GLib.timeout_add(t, lambda f=f: (f(), False)[1])
    return False
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
