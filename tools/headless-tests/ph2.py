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
def info(entry, identity, length):
    return {"title": "T", "artist": "A", "album": "B", "art_url": "", "status": "Playing", "length_us": length,
            "bus_name": "org.mpris.MediaPlayer2.x", "identity": identity, "desktop_entry": entry}
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    win.lookup_action("now-playing-enabled").change_state(GLib.Variant("b", True))
    theme = Gtk.IconTheme.get_for_display(win.get_display())
    for n in ("web-browser-symbolic", "video-x-generic-symbolic", "network-wireless-symbolic", "audio-x-generic-symbolic"):
        check(f"theme has {n}", theme.has_icon(n))
    ph = win.now_playing_art_placeholder
    def shown(i):
        win.on_now_playing_changed(i)
        return ph.get_icon_name()
    check("default Music Note, whatever the player", shown(info("firefox", "Firefox", 0)) == "audio-x-generic-symbolic")
    win.now_playing_placeholder_style_row.set_selected(1)
    check("Player's Icon: Firefox's own (symbolic) icon", shown(info("firefox", "Firefox", 0)) == "firefox-symbolic", ph.get_icon_name())
    check("Flatpak VLC (icon not visible) → video icon", shown(info("org.videolan.VLC", "VLC media player", 5_000_000)) == "video-x-generic-symbolic", ph.get_icon_name())
    check("unknown browser → web browser icon", shown(info("", "Brave", 0)) == "web-browser-symbolic", ph.get_icon_name())
    check("Flatpak music player with track length → music note", shown(info("app.rufin.rufin", "Rufin", 180_000_000)) == "audio-x-generic-symbolic", ph.get_icon_name())
    check("no length (stream/radio) → wireless icon", shown(info("org.example.Radio", "Radio", 0)) == "network-wireless-symbolic", ph.get_icon_name())
    win.now_playing_placeholder_style_row.set_selected(0)
    check("back to Music Note updates the showing icon", ph.get_icon_name() == "audio-x-generic-symbolic", ph.get_icon_name())
    check("no exceptions", not errors, errors[0][-400:] if errors else "")
    print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
