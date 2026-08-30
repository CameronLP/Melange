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

import ctypes
import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, GLib, GObject

from . import projectm

_libgl = ctypes.CDLL("libGL.so.1")
_libgl.glGetIntegerv.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_int32)]
_libgl.glDisable.argtypes = [ctypes.c_uint32]
_libgl.glViewport.argtypes = [ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
_libgl.glBindFramebuffer.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
_libgl.glBindVertexArray.argtypes = [ctypes.c_uint32]
_libgl.glDepthMask.argtypes = [ctypes.c_ubyte]
_libgl.glColorMask.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ubyte]

_GL_FRAMEBUFFER = 0x8D40
_GL_FRAMEBUFFER_BINDING = 0x8CA6
_GL_VERTEX_ARRAY_BINDING = 0x85B5
_GL_SCISSOR_TEST = 0x0C11
_GL_DEPTH_TEST = 0x0B71
_GL_CULL_FACE = 0x0B44
_GL_BLEND = 0x0BE2


class ProjectMView(Gtk.GLArea):
    """Renders MilkDrop-compatible visuals via libprojectM. Replaces
    the old WebKitWebView/Butterchurn stack - see the projectm-spike
    work log (referenced in the plan file) for why each piece here is
    the way it is; none of this was guessed."""

    __gtype_name__ = "ProjectMView"

    __gsignals__ = {
        # Fired once the ProjectM instance and Playlist are both ready
        # to use (i.e. after the first real "resize", since a real
        # size is needed before ever loading a preset). Callers should
        # not touch .projectm/.playlist before this fires.
        "ready": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):

        super().__init__()

        self.set_hexpand(True)
        self.set_vexpand(True)

        # This app is not a browser tab - it owns the whole GL context,
        # so there's no reason to accept whatever GTK defaults to here.
        # libprojectM is built without GLES support, and Gtk.GLArea
        # defaults to GLES on at least some Mesa/Wayland setups; if it
        # does, projectm_create() fails (returns NULL, with the actual
        # reason swallowed by a blanket catch(...) in projectM's own C
        # wrapper - it just looks like unexplained failure otherwise).
        self.set_use_es(False)
        self.set_required_version(3, 3)

        self.projectm = None
        self.playlist = None
        self._ready_emitted = False
        self._preset_loaded = False
        self._real_fbo = None

        self.connect("realize", self.on_realize)
        self.connect("unrealize", self.on_unrealize)
        self.connect("resize", self.on_resize)
        self.connect("render", self.on_render)

        self.add_tick_callback(self.on_tick)

    def on_realize(self, glarea):

        glarea.make_current()

        if glarea.get_error():
            print("ProjectMView: GL context error:", glarea.get_error())
            return

        try:
            self.projectm = projectm.ProjectM()

        except RuntimeError as e:
            print("ProjectMView:", e)
            return

        self.playlist = projectm.Playlist(self.projectm)

        # Deliberately not emitting "ready" or loading any preset here
        # - the widget has no real size yet at realize time in this
        # app's actual layout (header bar, toast overlay, toolbar view
        # all need a layout pass first), and loading a preset before a
        # real size is known bakes a wrong size into that preset's
        # internal render targets. on_resize below waits for the first
        # real size before emitting "ready", so window.py never gets a
        # chance to load a preset too early.

    def on_unrealize(self, glarea):

        if self.playlist:
            self.playlist.destroy()
            self.playlist = None

        if self.projectm:
            self.projectm.destroy()
            self.projectm = None

        self._ready_emitted = False
        self._preset_loaded = False
        self._real_fbo = None

    def on_resize(self, glarea, width, height):

        if not self.projectm or width <= 0 or height <= 0:
            return

        self.projectm.set_window_size(width, height)

        if not self._ready_emitted:
            self._ready_emitted = True

            # Cache the real FBO here, before "ready" gives window.py
            # a chance to load any preset. A preset's internal setup
            # (e.g. its first blur pass, or a hard-cut transition's
            # DrawInitialImage) does its own GL work outside of any
            # GTK-guaranteed render-signal setup, which can leave the
            # ambient FBO binding at 0 - querying lazily on first
            # render would then cache that corrupted value forever.
            fbo = ctypes.c_int32(0)
            _libgl.glGetIntegerv(_GL_FRAMEBUFFER_BINDING, ctypes.byref(fbo))
            self._real_fbo = fbo.value

            self.emit("ready")

    def on_render(self, glarea, gl_context):

        if not self.projectm:
            return True

        if self._real_fbo is None:
            fbo = ctypes.c_int32(0)
            _libgl.glGetIntegerv(_GL_FRAMEBUFFER_BINDING, ctypes.byref(fbo))
            self._real_fbo = fbo.value

        fbo = self._real_fbo

        # GTK's own compositor (GSK) does its own GL state setup/
        # teardown around this signal; resetting these explicitly
        # avoids depending on whatever it happened to leave behind.
        # VAO is saved/restored (not just reset) since GTK's own
        # rendering relies on its own VAO staying bound afterward.
        saved_vao = ctypes.c_int32(0)
        _libgl.glGetIntegerv(_GL_VERTEX_ARRAY_BINDING, ctypes.byref(saved_vao))

        _libgl.glDisable(_GL_SCISSOR_TEST)
        _libgl.glDisable(_GL_DEPTH_TEST)
        _libgl.glDisable(_GL_CULL_FACE)
        _libgl.glDisable(_GL_BLEND)
        _libgl.glDepthMask(1)
        _libgl.glColorMask(1, 1, 1, 1)
        _libgl.glViewport(0, 0, glarea.get_width(), glarea.get_height())

        self.projectm.render_frame(fbo)

        _libgl.glBindVertexArray(saved_vao.value)
        _libgl.glBindFramebuffer(_GL_FRAMEBUFFER, fbo)

        return True

    def on_tick(self, widget, frame_clock):

        widget.queue_render()
        return True

    def load_preset_file(self, path, smooth_transition=True):

        if self.projectm:
            self.projectm.load_preset_file(path, smooth_transition)
            self.unstick()

    def unstick(self):
        """A newly loaded preset renders as a solid, un-animated color
        confined/zoomed to the middle of the canvas until
        projectm_set_window_size is called several times with
        gradually changing values, paced across real frames (mirrors
        what happens organically during a live window drag-resize).
        Root cause not fully understood - see the projectm-spike work
        log - but this reliably un-sticks it without requiring any
        real GTK resize, and is harmless to call after every preset
        load. Must run after EVERY load (not just the first), since
        each preset gets fresh internal render targets."""

        base_w = self.get_width()
        base_h = self.get_height()

        if base_w <= 0 or base_h <= 0:
            return

        self._unstick_steps = list(range(0, 41)) + list(range(40, -1, -1))
        self._unstick_base = (base_w, base_h)
        GLib.timeout_add(20, self._unstick_step)

    def _unstick_step(self):

        if not self.projectm or not self._unstick_steps:
            return False

        step = self._unstick_steps.pop(0)
        base_w, base_h = self._unstick_base
        self.projectm.set_window_size(base_w + step, base_h + step)

        return True

    def pcm_add_int16(self, samples, channels=projectm.PROJECTM_STEREO):

        if self.projectm:
            self.projectm.pcm_add_int16(samples, channels)

    def set_sensitivity(self, value):

        if self.projectm:
            self.projectm.set_beat_sensitivity(value)

    def set_locked(self, locked):

        if self.projectm:
            self.projectm.set_preset_locked(locked)
