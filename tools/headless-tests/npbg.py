import gi, os, sys, traceback
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk, Gdk, Graphene
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
def corner(widget, name):
    w, h = widget.get_width(), widget.get_height(); node = None
    for _ in range(20):
        snap = Gtk.Snapshot(); Gtk.WidgetPaintable.new(widget).snapshot(snap, w, h); node = snap.to_node()
        if node: break
        GLib.MainContext.default().iteration(False)
    tex = widget.get_root().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h)); tex.save_to_png(f"{OUT}/{name}.png")
    d = Gdk.TextureDownloader.new(tex); d.set_format(Gdk.MemoryFormat.R8G8B8A8); data, stride = d.download_bytes(); data = data.get_data()
    o = (h - 4) * stride + (w // 2) * 4
    return tuple(data[o:o + 4])
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    win.lookup_action("now-playing-enabled").change_state(GLib.Variant("b", True))
    win.on_now_playing_changed({"title": "Song", "artist": "Artist", "album": "Album", "art_url": "", "status": "Playing", "length_us": 0, "bus_name": "org.mpris.MediaPlayer2.test"})
    def a():
        box = win.now_playing_box
        p = corner(box, "bg-default"); check("default: black at 50% (as before)", p[:3] == (0, 0, 0) and abs(p[3] - 128) <= 2, str(p))
        red = Gdk.RGBA(); red.parse("#ff0000"); win.now_playing_bg_color_button.set_rgba(red); win.now_playing_bg_opacity_scale.set_value(0.8)
    def b():
        p = corner(win.now_playing_box, "bg-red80"); check("Background Color red at 80%", p[:3] == (255, 0, 0) and abs(p[3] - 204) <= 2, str(p))
        win.now_playing_bg_opacity_scale.set_value(0.0)
    def c():
        p = corner(win.now_playing_box, "bg-0"); check("0% = no background", p[3] == 0, str(p))
        win.on_now_playing_changed  # noqa
        win.now_playing_show_background_changed(False)
        check("rows hidden with Show Background off", not any(r.get_visible() for r in win.now_playing_bg_rows))
        win.now_playing_show_background_changed(True)
        check("…shown again when on", all(r.get_visible() for r in win.now_playing_bg_rows))
        check("no exceptions", not errors, errors[0][-400:] if errors else "")
        print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
    for t, f in ((1500, a), (2500, b), (3500, c)): GLib.timeout_add(t, lambda f=f: (f(), False)[1])
    return False
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
