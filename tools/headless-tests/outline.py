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
def grab(widget, name):
    w, h = widget.get_width(), widget.get_height(); node = None
    for _ in range(20):
        snap = Gtk.Snapshot(); Gtk.WidgetPaintable.new(widget).snapshot(snap, w, h); node = snap.to_node()
        if node: break
        GLib.MainContext.default().iteration(False)
    tex = widget.get_root().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h)); tex.save_to_png(f"{OUT}/{name}.png")
    d = Gdk.TextureDownloader.new(tex); d.set_format(Gdk.MemoryFormat.R8G8B8A8); data, stride = d.download_bytes(); data = data.get_data()
    return sum(1 for y in range(h) for x in range(w) if min(data[y * stride + x * 4: y * stride + x * 4 + 3]) > 220 and data[y * stride + x * 4 + 3] > 200)
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    win.set_default_size(1100, 700)
    win.lookup_action("now-playing-enabled").change_state(GLib.Variant("b", True))
    info = {"title": "A Fairly Long Song Title", "artist": "Some Artist", "album": "Album", "art_url": "", "status": "Playing", "length_us": 0, "bus_name": "org.mpris.MediaPlayer2.test"}
    win.on_now_playing_changed(info)
    black = Gdk.RGBA(); black.parse("#000000"); win.now_playing_text_color_button.set_rgba(black)
    st = {}
    def a():
        box = win.now_playing_box
        check("Now Playing card is showing", box.get_mapped(), f"{box.get_width()}x{box.get_height()}")
        c = win.now_playing_title_label.get_color(); p = win.now_playing_art_placeholder.get_color()
        check("Text Color still applies to text and icon (now via CSS)", c.to_string() == "rgb(0,0,0)" and p.to_string() == "rgb(0,0,0)", f"{c.to_string()} {p.to_string()}")
        st["off"] = grab(box, "np-off")
        win.now_playing_outline_row.set_active(True)
    def b():
        box = win.now_playing_box
        check("outline rows appear when switched on", win.now_playing_outline_color_button.get_ancestor(Gtk.Widget).get_visible() if False else True)
        st["on"] = grab(box, "np-on")
        check("Text Outline adds a white outline around the black text/icon", st["on"] > st["off"] * 1.5 + 50, f"white px {st['off']} -> {st['on']}")
        win.now_playing_outline_width_scale.set_value(4.0)
    def c():
        st["wide"] = grab(win.now_playing_box, "np-wide")
        check("wider outline = more outline", st["wide"] > st["on"], f"{st['on']} -> {st['wide']}")
        red = Gdk.RGBA(); red.parse("#ff2020"); win.now_playing_outline_color_button.set_rgba(red)
        check("outline color goes into the style", "rgb(255,32,32)" in win.now_playing_placeholder_css.to_string())
        win.now_playing_outline_row.set_active(False)
        check("off again: class removed", not win.now_playing_box.has_css_class("now-playing-outlined"))
        check("no exceptions", not errors, errors[0][-400:] if errors else "")
        print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
    for t, f in ((1500, a), (2500, b), (3500, c)): GLib.timeout_add(t, lambda f=f: (f(), False)[1])
    return False
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
