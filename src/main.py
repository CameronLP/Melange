# main.py
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

import sys
import gi

from gettext import gettext as _

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk, Gdk, Gio
from pathlib import Path

from .window import MelangeWindow

css = Gtk.CssProvider()

css.load_from_path(
    str(
        Path(__file__).parent / "style.css"
    )
)

Gtk.StyleContext.add_provider_for_display(
    Gdk.Display.get_default(),
    css,
    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
)


class MelangeApplication(Adw.Application):

    def __init__(self):

        super().__init__(
            application_id="com.cameronlp.Melange"
        )

        quit_action = Gio.SimpleAction.new("quit", None)

        quit_action.connect(
            "activate",
            lambda action, param: self.quit()
        )

        self.add_action(quit_action)

        shortcuts_action = Gio.SimpleAction.new("shortcuts", None)

        shortcuts_action.connect(
            "activate",
            self.show_shortcuts
        )

        self.add_action(shortcuts_action)

        about_action = Gio.SimpleAction.new("about", None)

        about_action.connect(
            "activate",
            self.show_about
        )

        self.add_action(about_action)

        self.set_accels_for_action("app.quit", ["<Primary>q"])
        self.set_accels_for_action("app.shortcuts", ["<Primary>question"])
        self.set_accels_for_action("win.next-preset", ["Right", "space"])
        self.set_accels_for_action("win.previous-preset", ["Left"])
        self.set_accels_for_action("win.toggle-fullscreen", ["F11"])
        self.set_accels_for_action("win.lock-preset", ["l"])
        self.set_accels_for_action("win.shuffle-preset", ["s"])


    def do_activate(self):

        window = MelangeWindow(application=self)
        window.present()


    def show_shortcuts(self, action, param):

        builder = Gtk.Builder.new_from_resource(
            "/com/cameronlp/Melange/shortcuts-dialog.ui"
        )

        dialog = builder.get_object("shortcuts_dialog")

        dialog.present(self.get_active_window())


    def show_about(self, action, param):

        about = Adw.AboutDialog(
            application_name="Melange",
            application_icon="com.cameronlp.Melange",
            developer_name="Cameron Penne",
            version="0.1.0",
            license_type=Gtk.License.GPL_3_0,
            comments=_(
                "A MilkDrop-style music visualizer for the desktop"
            ),
            website="https://github.com/CameronLP/Melange",
            issue_url="https://github.com/CameronLP/Melange/issues",
        )

        about.add_credit_section(
            _("Powered By"),
            [
                "Jordan Berg (Butterchurn) https://github.com/jberg/butterchurn",
                "Jordan Berg (MilkDrop preset conversion) https://github.com/jberg/milkdrop-preset-converter",
                "baron (Butterchurn preset pack) https://github.com/uvmain/butterchurn-presets-baron",
            ]
        )

        about.present(self.get_active_window())


def main(version):

    app = MelangeApplication()
    app.run()


if __name__ == "__main__":
    main()
