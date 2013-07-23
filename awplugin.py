#!/usr/bin/python
# Anuweb - Totem web interface
# Copyright (C) 2013 Daniel Beer
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

import gtk
import gconf
import threading
import gobject
import anuweb
from wsgiref import simple_server

try:
    import totem
except ImportError:
    class totem:
	"""Stub substitute for pydoc"""
	class Plugin:
	    pass

class NoDNSHandler(simple_server.WSGIRequestHandler):
    """Variant of the default WSGI request handler that avoids DNS.

    We don't need hostnames in the logs, and reverse DNS generally takes
    a long time to fail, blocking requests for an unbearable length of
    time.
    """
    def address_string(self):
	"""Override that avoids reverse DNS.

	This method is supposed to return the client's hostname.
	Instead, we just return the IP address as a string.
	"""
	return self.client_address[0]

class ServerThread(threading.Thread):
    """WSGI server thread.

    This object provides thread which runs the WSGI reference server. It
    also implements a synchronized shutdown.
    """
    def __init__(self, handler, addr):
	"""Initialize a server.

	You must supply a handler function object, and a (address, port)
	tuple. The server port will be bound, but the server thread
	won't start until you call the start() method.
	"""
	threading.Thread.__init__(self)
	self.server = simple_server.WSGIServer(addr, NoDNSHandler)
	self.server.set_app(handler)

    def run(self):
	"""Worker function.

	Do not call this method -- it's what runs in the created thread.
	"""
	self.server.serve_forever()
	self.server.server_close()

    def shutdown(self):
	"""Synchronous shutdown.

	Shut down a running server thread. The method doesn't return
	until after the thread is terminated. The server's resources are
	freed.
	"""
	self.server.shutdown()
	self.join()

GCONF_KEY = '/apps/totem/plugins/anuweb'

def read_config():
    """Load configuration dictionary from GConf.

    All known keys are loaded from GCONF_KEY. Defaults are substituted
    for missing values.
    """

    def default(v, d):
	if v is None:
	    return d
	return v

    g = gconf.client_get_default()
    return {
	'server_port':
	    default(g.get_int(GCONF_KEY + '/server_port'), 8099),
	'default_media_path':
	    default(g.get_string(GCONF_KEY + '/default_media_path'), '/'),
	'filter_pattern':
	    default(g.get_string(GCONF_KEY + '/filter_pattern'),
		    '*.m??;*.avi;*.og?'),
	'path_restrict':
	    default(g.get_string(GCONF_KEY + '/path_restrict'), '/')
    }

class ConfigDialog:
    """Plugin configuration dialog."""
    def __init__(self, save_cb = None):
	"""Construct the configuration dialog.

	You can optionally supply a function object to be invoked when
	the configuration is changed in GConf.
	"""
	PADDING = 10

	self.save_cb = save_cb
	self.dialog = gtk.Dialog("Anuweb", None,
		gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
		(gtk.STOCK_CANCEL, gtk.RESPONSE_REJECT,
		 gtk.STOCK_OK, gtk.RESPONSE_ACCEPT))
	tab = gtk.Table(4, 2)

	label = gtk.Label("Server port:")
	label.set_alignment(0.0, 0.5)
	tab.attach(label, 0, 1, 0, 1, xoptions = gtk.FILL,
		xpadding = PADDING, ypadding = PADDING)

	self.server_port = gtk.SpinButton()
	self.server_port.set_range(1, 65535)
	self.server_port.set_increments(1, 1024)
	self.server_port.set_digits(0)
	tab.attach(self.server_port, 1, 2, 0, 1,
		xpadding = PADDING, ypadding = PADDING)

	label = gtk.Label("Default media path:")
	label.set_alignment(0.0, 0.5)
	tab.attach(label, 0, 1, 1, 2, xoptions = gtk.FILL,
		xpadding = PADDING, ypadding = PADDING)

	self.media_path = gtk.FileChooserButton("Default media path")
	self.media_path.set_action(gtk.FILE_CHOOSER_ACTION_SELECT_FOLDER)
	self.media_path.set_size_request(250, -1)
	tab.attach(self.media_path, 1, 2, 1, 2,
		xpadding = PADDING, ypadding = PADDING)

	label = gtk.Label("Browser root:")
	label.set_alignment(0.0, 0.5)
	tab.attach(label, 0, 1, 2, 3, xoptions = gtk.FILL,
		xpadding = PADDING, ypadding = PADDING)

	self.path_restrict = gtk.FileChooserButton("Browser root")
	self.path_restrict.set_action(gtk.FILE_CHOOSER_ACTION_SELECT_FOLDER)
	self.path_restrict.set_size_request(250, -1)
	tab.attach(self.path_restrict, 1, 2, 2, 3,
		xpadding = PADDING, ypadding = PADDING)

	label = gtk.Label("File filter pattern (glob):")
	label.set_alignment(0.0, 0.5)
	tab.attach(label, 0, 1, 3, 4, xoptions = gtk.FILL,
		xpadding = PADDING, ypadding = PADDING)

	self.filter_pattern = gtk.Entry()
	tab.attach(self.filter_pattern, 1, 2, 3, 4,
		xpadding = PADDING, ypadding = PADDING)

	tab.show_all()
	self.dialog.vbox.pack_start(tab)

	self.dialog.connect('response', self.dialog_response)
	self.dialog.connect('show', self.dialog_show)

    def get_dialog(self):
	"""Obtain the actual GTK+ widget for the dialog."""
	return self.dialog

    def dialog_show(self, dialog):
	"""Hook to be run when the dialog is shown.

	This hook loads the configuration (or defaults) from GConf and
	populates the dialog box's widgets.
	"""
	cfg = read_config()
	self.server_port.set_value(cfg['server_port'])
	self.media_path.set_filename(cfg['default_media_path'])
	self.path_restrict.set_filename(cfg['path_restrict'])
	self.filter_pattern.set_text(cfg['filter_pattern'])

    def dialog_response(self, dialog, response_id):
	"""Hook to be run when the dialog box is closed.

	This hook checks the user's response, and if OK was clicked,
	calls self.save_settings().
	"""
	if response_id == gtk.RESPONSE_ACCEPT:
	    self.save_settings()

	self.dialog.hide()

    def save_settings(self):
	"""Write settings to GConf.

	If a save_cb hook was supplied, it will be invoked after saving
	the settings.
	"""
	g = gconf.client_get_default()
	g.set_int(GCONF_KEY + '/server_port',
		self.server_port.get_value_as_int())
	g.set_string(GCONF_KEY + '/default_media_path',
		self.media_path.get_filename())
	g.set_string(GCONF_KEY + '/path_restrict',
		self.path_restrict.get_filename())
	g.set_string(GCONF_KEY + '/filter_pattern',
		self.filter_pattern.get_text())

	if self.save_cb is not None:
	    self.save_cb()

class AnuwebPlugin(totem.Plugin):
    """Totem plugin: Anusha's Totem web interface."""
    def __init__(self):
	"""Plugin constructor."""
	totem.Plugin.__init__(self)
	self.server = None
	self.totem_obj = None

    def is_configurable(self):
	"""Does this plugin have a config dialog?"""
	return True

    def create_configure_dialog(self):
	"""Obtain a configuration dialog.

	Construct and return a GTK+ widget.
	"""
	return ConfigDialog(self.save_cb).get_dialog()

    def activate(self, totem_obj):
	"""Activate the plugin (start the server)."""
	self.stop_server()
	self.totem_obj = totem_obj
	self.start_server()

    def deactivate(self, totem_obj):
	"""Deactivate the plugin (stop the server)."""
	self.stop_server()
	self.totem_obj = None

    def stop_server(self):
	"""Stop the web server thread and destroy the server."""
	if self.server is not None:
	    self.server.shutdown()
	    self.server = None

    def start_server(self):
	"""Construct and start a web server thread.

	If this fails, a GTK+ dialog box will appear.
	"""
	try:
	    cfg = read_config()
	    app = anuweb.AnuApp(self.totem_obj, cfg)
	    self.server = ServerThread(app, ('0.0.0.0', cfg['server_port']))
	    self.server.start()
	except Exception as e:
	    m = gtk.MessageDialog(None, gtk.DIALOG_DESTROY_WITH_PARENT,
		gtk.MESSAGE_ERROR, gtk.BUTTONS_CLOSE, 'anuweb: ' + str(e))
	    m.run()
	    m.destroy()

    def save_cb(self):
	"""Settings change callback.

	If the server is currently running, stop and restart it.
	"""
	if self.server is not None:
	    self.stop_server()
	    self.start_server()
