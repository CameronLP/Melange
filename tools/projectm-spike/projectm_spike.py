"""projectM spike: native MilkDrop rendering in a Gtk.GLArea, no WebKit.

Exploration prototype for replacing Butterchurn/WebKitGTK with
libprojectM - see docs/projectm-exploration.md. Not used by the app.

Usage:
    python3 projectm_spike.py <libprojectM-4.so> <preset dir> [pipewire sink]

Environment:
    SPIKE_SECONDS=N          measure for N seconds (after 10s warm-up), print stats, quit
    SPIKE_PRESET_SECONDS=N   switch preset every N seconds (default 10)
    SPIKE_SNAP_DIR=dir       save PNG captures of the frames listed in...
    SPIKE_SNAP_FRAMES=a,b,c  ...these frame numbers

Needs a libprojectM 4 build. projectm_opengl_render_frame_fbo() is only
on projectM's development branch so far; with a 4.1.x release it falls
back to projectm_opengl_render_frame(), which needs the target-FBO
patch in this directory to draw into the GLArea's framebuffer.
"""

import ctypes
import locale
import os
import random
import sys
import threading
import time
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, GLib, Gst, GdkPixbuf

LIB_PATH, PRESET_DIR = sys.argv[1], sys.argv[2]
SINK = sys.argv[3] if len(sys.argv) > 3 else None

# --- libprojectM C API via ctypes -------------------------------------
pm = ctypes.CDLL(LIB_PATH)
pm.projectm_create.restype = ctypes.c_void_p
pm.projectm_destroy.argtypes = [ctypes.c_void_p]
pm.projectm_set_window_size.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
pm.projectm_load_preset_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_bool]
pm.projectm_pcm_add_int16.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_int]
pm.projectm_set_preset_locked.argtypes = [ctypes.c_void_p, ctypes.c_bool]
pm.projectm_set_soft_cut_duration.argtypes = [ctypes.c_void_p, ctypes.c_double]
pm.projectm_opengl_render_frame.argtypes = [ctypes.c_void_p]
HAS_FBO_API = hasattr(pm, "projectm_opengl_render_frame_fbo")
if HAS_FBO_API:
    pm.projectm_opengl_render_frame_fbo.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
PROJECTM_STEREO = 2

PresetFailed = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p)
pm.projectm_set_preset_switch_failed_event_callback.argtypes = [
    ctypes.c_void_p, PresetFailed, ctypes.c_void_p
]

# --- the few raw GL calls needed around projectM -----------------------
gl = ctypes.CDLL("libGL.so.1")
gl.glGetIntegerv.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_int)]
gl.glBindFramebuffer.argtypes = [ctypes.c_uint, ctypes.c_uint]
gl.glColorMask.argtypes = [ctypes.c_ubyte] * 4
gl.glClearColor.argtypes = [ctypes.c_float] * 4
gl.glClear.argtypes = [ctypes.c_uint]
gl.glReadPixels.argtypes = [ctypes.c_int] * 4 + [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
GL_DRAW_FRAMEBUFFER_BINDING = 0x8CA6
GL_READ_FRAMEBUFFER = 0x8CA8
GL_COLOR_BUFFER_BIT = 0x4000
GL_RGBA = 0x1908
GL_UNSIGNED_BYTE = 0x1401

SNAP_DIR = os.environ.get("SPIKE_SNAP_DIR")
SNAP_FRAMES = {int(f) for f in os.environ.get("SPIKE_SNAP_FRAMES", "").split(",") if f}

Gst.init(None)

presets = sorted(str(p) for p in Path(PRESET_DIR).rglob("*.milk"))
random.seed(1)
random.shuffle(presets)
print(f"SPIKE presets found: {len(presets)}", flush=True)

stats = {"frames": 0, "loaded": 0, "failed": 0, "chunks": 0, "frame_ms": 0.0}


class Spike:

    def __init__(self, app):
        self.handle = None
        self.size_known = False
        self.pending = bytearray()
        self.lock = threading.Lock()
        self.preset_index = 0
        self.last_switch = 0.0
        self.preset_seconds = float(os.environ.get("SPIKE_PRESET_SECONDS", "10"))

        self.win = Gtk.ApplicationWindow(application=app, title="projectM spike")
        self.win.set_default_size(1280, 720)
        self.area = Gtk.GLArea()
        self.area.set_required_version(3, 3)
        self.area.connect("realize", self.on_realize)
        self.area.connect("unrealize", self.on_unrealize)
        self.area.connect("render", self.on_render)
        self.area.connect("resize", self.on_resize)
        self.win.set_child(self.area)
        self.area.add_tick_callback(lambda *a: (self.area.queue_render(), True)[1])
        self.win.present()
        self.start_audio()

    def on_realize(self, area):
        # projectM parses .milk numbers with the C library's locale-
        # dependent functions. GTK switches the process to the user's
        # locale, so on e.g. nb_NO ("0,5") every preset value mis-parses
        # and everything - even projectM's built-in idle preset - renders
        # black. Numeric formatting must stay "C" while projectM is used.
        locale.setlocale(locale.LC_NUMERIC, "C")

        area.make_current()
        if area.get_error():
            print("SPIKE GL error:", area.get_error(), flush=True)
            return
        self.handle = pm.projectm_create()
        self._failed_cb = PresetFailed(self.on_preset_failed)
        pm.projectm_set_preset_switch_failed_event_callback(self.handle, self._failed_cb, None)
        pm.projectm_set_soft_cut_duration(self.handle, 2.0)
        pm.projectm_set_preset_locked(self.handle, True)  # switching is driven from here

    def on_unrealize(self, area):
        area.make_current()
        if self.handle:
            pm.projectm_destroy(self.handle)
            self.handle = None

    def on_resize(self, area, width, height):
        if self.handle and self.size_known:
            pm.projectm_set_window_size(self.handle, width, height)

    def on_render(self, area, context):
        if not self.handle:
            return True

        scale = area.get_scale_factor()
        width, height = area.get_width() * scale, area.get_height() * scale
        if width <= 0 or height <= 0:
            return True

        # The area is still 0x0 at realize, so the first preset is only
        # loaded once a real size is known.
        if not self.size_known:
            self.size_known = True
            pm.projectm_set_window_size(self.handle, width, height)
            self.next_preset(smooth=False)

        with self.lock:
            data = bytes(self.pending)
            self.pending.clear()
        if data:
            pm.projectm_pcm_add_int16(self.handle, data, len(data) // 4, PROJECTM_STEREO)

        if time.monotonic() - self.last_switch > self.preset_seconds:
            self.next_preset(smooth=True)

        fbo = ctypes.c_int(0)
        gl.glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, ctypes.byref(fbo))

        start = time.perf_counter()
        if HAS_FBO_API:
            pm.projectm_opengl_render_frame_fbo(self.handle, fbo.value)
        else:
            pm.projectm_opengl_render_frame(self.handle)
        stats["frame_ms"] += (time.perf_counter() - start) * 1000

        # MilkDrop never cared about alpha and some presets leave it at
        # 0, which GTK would composite as transparent - force opaque.
        gl.glBindFramebuffer(0x8D40, fbo.value)
        gl.glColorMask(0, 0, 0, 1)
        gl.glClearColor(0, 0, 0, 1)
        gl.glClear(GL_COLOR_BUFFER_BIT)
        gl.glColorMask(1, 1, 1, 1)

        stats["frames"] += 1
        if SNAP_DIR and stats["frames"] in SNAP_FRAMES:
            self.snapshot(fbo.value, width, height, f"{SNAP_DIR}/frame{stats['frames']:05d}.png")
        return True

    def next_preset(self, smooth):
        path = presets[self.preset_index % len(presets)]
        self.preset_index += 1
        self.last_switch = time.monotonic()
        pm.projectm_load_preset_file(self.handle, path.encode(), smooth)
        stats["loaded"] += 1
        print("SPIKE preset:", Path(path).name, flush=True)

    def on_preset_failed(self, filename, message, user_data):
        stats["failed"] += 1
        print("SPIKE preset FAILED:", filename and filename.decode(errors="replace"),
              "|", message and message.decode(errors="replace"), flush=True)

    def snapshot(self, fbo, width, height, path):
        gl.glBindFramebuffer(GL_READ_FRAMEBUFFER, fbo)
        buf = ctypes.create_string_buffer(width * height * 4)
        gl.glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, buf)
        stride = width * 4
        rows = [buf.raw[y * stride:(y + 1) * stride] for y in range(height)]
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(b"".join(reversed(rows))),  # GL origin is bottom-left
            GdkPixbuf.Colorspace.RGB, True, 8, width, height, stride
        )
        pixbuf.savev(path, "png", [], [])
        print("SPIKE snapshot", path, flush=True)

    # Same capture as Melange's start_system_audio (window.py).
    def start_audio(self):
        target = f'target-object="{SINK}"' if SINK else ""
        self.pipeline = Gst.parse_launch(
            f'pipewiresrc {target} stream-properties="props,stream.capture.sink=true" ! '
            "audio/x-raw,format=S16LE,rate=44100,channels=2 ! audioconvert ! audioresample ! "
            "appsink name=sink emit-signals=true sync=false"
        )
        self.pipeline.get_by_name("sink").connect("new-sample", self.on_sample)
        self.pipeline.set_state(Gst.State.PLAYING)

    def on_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample:
            buf = sample.get_buffer()
            ok, info = buf.map(Gst.MapFlags.READ)
            if ok:
                with self.lock:
                    self.pending.extend(info.data)
                    del self.pending[:-44100]  # keep at most ~0.25s
                stats["chunks"] += 1
                buf.unmap(info)
        return Gst.FlowReturn.OK


def rss_mb():
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS"):
            return int(line.split()[1]) // 1024


def main():
    app = Gtk.Application(application_id="com.cameronlp.ProjectMSpike")
    holder = {}

    def activate(a):
        holder["spike"] = Spike(a)
        seconds = int(os.environ.get("SPIKE_SECONDS", "0"))
        if not seconds:
            return
        mark = {}

        def start_measuring():
            t = os.times()
            mark.update(cpu=t.user + t.system, wall=time.monotonic(),
                        frames=stats["frames"], frame_ms=stats["frame_ms"])
            return False

        def finish():
            t = os.times()
            cpu = t.user + t.system - mark["cpu"]
            wall = time.monotonic() - mark["wall"]
            frames = stats["frames"] - mark["frames"]
            avg = (stats["frame_ms"] - mark["frame_ms"]) / max(1, frames)
            print(f"SPIKE RESULT fps={frames / wall:.1f} cpu={100 * cpu / wall:.0f}% "
                  f"rss={rss_mb()}MB render_call_avg={avg:.2f}ms presets_loaded={stats['loaded']} "
                  f"failed={stats['failed']} audio_chunks={stats['chunks']}", flush=True)
            os._exit(0)

        GLib.timeout_add_seconds(10, start_measuring)
        GLib.timeout_add_seconds(10 + seconds, finish)

    app.connect("activate", activate)
    app.run([])


main()
