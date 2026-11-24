# projectm_view.py
#
# Copyright 2026 Cameron
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Experimental native MilkDrop renderer: libprojectM in a Gtk.GLArea.

An alternative to the Butterchurn/WebKitGTK visualizer, selected with
the "projectM Renderer" switch in Preferences > Experimental. See
docs/projectm-exploration.md for the background and the integration
problems worked around below.
"""

import ctypes
import locale
import os
from pathlib import Path

from gi.repository import Gtk, GLib

# Bundled starter presets (data/projectm-presets, installed next to this
# module's own directory) - overridable for running from a source tree.
PRESET_DIR = Path(os.environ.get(
    "MELANGE_PROJECTM_PRESETS",
    Path(__file__).resolve().parent.parent / "projectm-presets"
))

LIBRARY_NAME = os.environ.get("MELANGE_PROJECTM_LIB", "libprojectM-4.so.4")

PROJECTM_STEREO = 2
GL_DRAW_FRAMEBUFFER_BINDING = 0x8CA6
GL_FRAMEBUFFER = 0x8D40
GL_COLOR_BUFFER_BIT = 0x4000

# At most this much captured audio waits for the next frame (0.25s of
# 44.1kHz stereo S16) - same bound as window.py's MAX_PENDING_AUDIO_BYTES.
MAX_PENDING_AUDIO_BYTES = 44100

SMOOTH_TRANSITION_SECONDS = 2.0

_PresetFailedCallback = ctypes.CFUNCTYPE(
    None, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p
)

_lib = None
_gl = None
_load_error = None


def _load_libraries():

    global _lib, _gl, _load_error

    if _lib is not None or _load_error is not None:
        return _lib is not None

    try:
        lib = ctypes.CDLL(LIBRARY_NAME)
        gl = ctypes.CDLL("libGL.so.1")
    except OSError as error:
        _load_error = str(error)
        return False

    lib.projectm_create.restype = ctypes.c_void_p
    lib.projectm_destroy.argtypes = [ctypes.c_void_p]
    lib.projectm_set_window_size.argtypes = [
        ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t
    ]
    lib.projectm_load_preset_file.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_bool
    ]
    lib.projectm_pcm_add_int16.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_int
    ]
    lib.projectm_set_preset_locked.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    lib.projectm_set_soft_cut_duration.argtypes = [ctypes.c_void_p, ctypes.c_double]
    lib.projectm_set_preset_switch_failed_event_callback.argtypes = [
        ctypes.c_void_p, _PresetFailedCallback, ctypes.c_void_p
    ]
    lib.projectm_opengl_render_frame.argtypes = [ctypes.c_void_p]

    if hasattr(lib, "projectm_opengl_render_frame_fbo"):
        lib.projectm_opengl_render_frame_fbo.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32
        ]

    gl.glGetIntegerv.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_int)]
    gl.glBindFramebuffer.argtypes = [ctypes.c_uint, ctypes.c_uint]
    gl.glColorMask.argtypes = [ctypes.c_ubyte] * 4
    gl.glClearColor.argtypes = [ctypes.c_float] * 4
    gl.glClear.argtypes = [ctypes.c_uint]

    _lib, _gl = lib, gl

    return True


def availability():
    """(available, reason) - whether libprojectM and presets can be used."""

    if not _load_libraries():
        return False, f"libprojectM couldn't be loaded ({_load_error})"

    if not find_presets():
        return False, f"No .milk presets found in {PRESET_DIR}"

    return True, ""


def find_presets():

    if not PRESET_DIR.is_dir():
        return []

    return sorted(PRESET_DIR.rglob("*.milk"))


def preset_catalog():
    """{display name: path} for the bundled presets. Names are file
    stems; a stem used in more than one category gets the category
    appended so every name stays unique."""

    paths = find_presets()
    stems = {}

    for path in paths:
        stems[path.stem] = stems.get(path.stem, 0) + 1

    catalog = {}

    for path in paths:
        name = path.stem

        if stems[name] > 1:
            name = f"{name} ({path.parent.name})"

        catalog[name] = path

    return catalog


class ProjectMView(Gtk.GLArea):
    """Renders presets it's told to load - navigation, history, the
    queue and cycling live in preset_manager.PresetManager."""

    def __init__(self, on_preset_failed=None):

        super().__init__()

        # on_preset_failed(path, reason) - called on the main loop.
        self.on_preset_failed_callback = on_preset_failed

        self.handle = None
        self.size_known = False
        self.pending_audio = bytearray()
        self.requested_preset = None  # (path, smooth), loaded on next render
        self.transition_seconds = SMOOTH_TRANSITION_SECONDS

        self.set_required_version(3, 3)
        self.set_hexpand(True)
        self.set_vexpand(True)

        self.connect("realize", self.on_realize)
        self.connect("unrealize", self.on_unrealize)
        self.connect("render", self.on_render)
        self.connect("resize", self.on_resize)

        self.add_tick_callback(self.on_tick)

    # --- lifecycle ------------------------------------------------------

    def on_realize(self, area):

        # projectM parses .milk numbers with the C library's locale-
        # dependent functions, and GTK switches the process to the
        # user's locale. Under a comma-decimal locale (e.g. nb_NO)
        # every preset value mis-parses and everything renders black,
        # so numeric formatting has to stay "C" while projectM is used.
        locale.setlocale(locale.LC_NUMERIC, "C")

        self.make_current()

        if self.get_error() is not None:
            print("projectM: GL context error:", self.get_error().message)
            return

        self.handle = _lib.projectm_create()

        if not self.handle:
            print("projectM: projectm_create() failed")
            return

        self._failed_callback = _PresetFailedCallback(self.on_preset_failed)
        _lib.projectm_set_preset_switch_failed_event_callback(
            self.handle, self._failed_callback, None
        )
        _lib.projectm_set_soft_cut_duration(self.handle, self.transition_seconds)

        # Melange decides when to switch presets (cycle interval,
        # next/previous), not projectM's own timer/hard cuts.
        _lib.projectm_set_preset_locked(self.handle, True)

        self.size_known = False

    def on_unrealize(self, area):

        self.make_current()

        if self.handle:
            _lib.projectm_destroy(self.handle)
            self.handle = None

    def on_resize(self, area, width, height):

        if self.handle and self.size_known:
            _lib.projectm_set_window_size(self.handle, width, height)

    def on_tick(self, widget, frame_clock):

        self.queue_render()

        return GLib.SOURCE_CONTINUE

    # --- rendering ------------------------------------------------------

    def on_render(self, area, context):

        if not self.handle:
            return True

        scale = self.get_scale_factor()
        width, height = self.get_width() * scale, self.get_height() * scale

        if width <= 0 or height <= 0:
            return True

        # The area is still 0x0 at realize, so presets are only loaded
        # once there's a real size.
        if not self.size_known:
            self.size_known = True
            _lib.projectm_set_window_size(self.handle, width, height)

        if self.requested_preset is not None:
            path, smooth = self.requested_preset
            self.requested_preset = None
            _lib.projectm_load_preset_file(self.handle, str(path).encode(), smooth)

        if self.pending_audio:
            data = bytes(self.pending_audio)
            self.pending_audio.clear()
            _lib.projectm_pcm_add_int16(
                self.handle, data, len(data) // 4, PROJECTM_STEREO
            )

        framebuffer = ctypes.c_int(0)
        _gl.glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, ctypes.byref(framebuffer))

        # projectM 4.1.x always draws to framebuffer 0 unless patched
        # (flatpak/projectm-4.1.8-render-to-bound-fbo.patch makes it use
        # whatever is bound - the GLArea's); newer versions take the
        # target explicitly.
        if hasattr(_lib, "projectm_opengl_render_frame_fbo"):
            _lib.projectm_opengl_render_frame_fbo(self.handle, framebuffer.value)
        else:
            _lib.projectm_opengl_render_frame(self.handle)

        # MilkDrop never used alpha and some presets leave it at 0,
        # which GTK would composite as transparent - force opaque.
        _gl.glBindFramebuffer(GL_FRAMEBUFFER, framebuffer.value)
        _gl.glColorMask(0, 0, 0, 1)
        _gl.glClearColor(0.0, 0.0, 0.0, 1.0)
        _gl.glClear(GL_COLOR_BUFFER_BIT)
        _gl.glColorMask(1, 1, 1, 1)

        return True

    # --- audio ----------------------------------------------------------

    def push_audio(self, data):

        # Interleaved S16LE stereo, called on the main thread
        # (window.py flush_pending_audio); consumed on the next frame.
        self.pending_audio.extend(data)

        excess = len(self.pending_audio) - MAX_PENDING_AUDIO_BYTES

        if excess > 0:
            del self.pending_audio[:excess + (-excess % 4)]

    # --- presets --------------------------------------------------------

    def load_preset(self, path, smooth=True):
        """Show this .milk file (loaded on the next frame, when the GL
        context is current)."""

        self.requested_preset = (Path(path), smooth)
        self.queue_render()

    def set_transition_seconds(self, seconds):

        self.transition_seconds = max(0.0, float(seconds))

        if self.handle:
            _lib.projectm_set_soft_cut_duration(self.handle, self.transition_seconds)

    def on_preset_failed(self, filename, message, user_data):

        name = filename.decode(errors="replace") if filename else "?"
        reason = message.decode(errors="replace") if message else ""
        print(f"projectM: preset failed: {name}: {reason}")

        # Called from inside projectM's render call; report it once
        # that has returned.
        if self.on_preset_failed_callback:
            GLib.idle_add(
                lambda: (self.on_preset_failed_callback(name, reason), False)[1]
            )
