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

import fnmatch
import threading
import cgi
import urllib
import os
import gobject

try:
    import totem
except ImportError:
    class totem:
	"""Stub substitute for pydoc"""
	class Plugin:
	    pass

def match_check(filename, pattern):
    """Check the filename against a pattern.

    Patterns are semicolon-separated lists of glob patterns.
    """
    for p in pattern.split(';'):
	if fnmatch.fnmatch(filename, p):
	    return True
    return False

def my_base(path):
    """Substitute for os.path.basename.

    This function works identically, except that it returns '/' as the
    basename of '/', as a special case.
    """
    base = os.path.basename(path)
    if base == '':
	return '/'
    return base

class GObjectRPC:
    """RPC service for GObject main loop.

    This object is used by a thread to synchronously execute function
    calls in the GObject main loop. Return values and exceptions are
    propagated as though the function were executed in the current
    thread.

    Example usage:

        # In a background thread...
	rpc = GObjectRPC()

	# Equivalent to: r = func(a, b, c), except that func() executes
	# in the GObject main loop
	r = rpc(func, a, b, c)
    """
    def __init__(self):
	"""Constructor. One event object is created for synchronization."""
	self.func = None
	self.args = None
	self.kwargs = None
	self.retval = None
	self.exval = None
	self.event = threading.Event()

    def __call__(self, func, *args, **kwargs):
	"""Execute the given function in the GObject main loop.

	Any arguments passed are fed to the function. Execution is
	synchronous: this method doesn't return until after the function
	has finished executing.

	The return value of this method is the return value of the
	supplied function. If the function raises an exception, then it
	will be caught in the GObject main loop and re-raised as though
	it were raised from this method.

	Note that attempting an RPC call from within the main loop will
	result in a deadlock.
	"""
	self.func = func
	self.args = args
	self.kwargs = kwargs

	self.event.clear()
	gobject.idle_add(self._run)
	self.event.wait()

	if self.exval:
	    raise self.exval

	return self.retval

    def _run(self):
	"""Helper method, executed in the GObject main loop.

	Do not call this method directly.
	"""
	try:
	    self.retval = self.func(*self.args, **self.kwargs)
	    self.exval = None
	except Exception as e:
	    self.exval = e
	self.event.set()

class StaticResponse:
    """WSGI responder which delivers a static object."""
    def __init__(self, ctype, text, code = '200 OK', headers = []):
	"""Initialize the WSGI responder.

	At a minimum, you must supply content type and text/data (both
	strings). Optionally, you may specify extra headers and an
	altered response code.
	"""
	self.code = code
	self.ctype = ctype
	self.text = text
	self.headers = [
	    ('Content-Type', self.ctype),
	    ('Content-Length', str(len(self.text)))] + headers

    def __call__(self, environ, start_response):
	"""WSGI handler method."""
	start_response(self.code, self.headers)
	return [self.text]

not_found = StaticResponse('text/plain', 'Not found',
	code = '404 Not Found')
forbidden = StaticResponse('text/plain', 'Forbidden',
	code = '403 Forbidden')
bad_request = StaticResponse('text/plain', 'Bad request',
	code = '400 Bad Request')
dash_redirect = StaticResponse('text/plain', '',
	code = '302 Found', headers = [('Location', '/')])

STYLE_CSS = """body {
    font-family: sans-serif;
    font-size: 32px;
    background: #000000;
    color: #a0a0a0;
}

a {
    text-decoration: none;
    font-weight: bold;
    color: #00a000;
}

#top {
    position: absolute;
    top: 0px;
    left: 0px;
    width: 100%;
    height: 50px;
    background: #00a000;
    color: #000000;
    padding: 10px;
    font-size: 40px;
    font-weight: bold;
}

#main {
    position: absolute;
    top: 80px;
    left: 0px;
    width: 100%;
    padding: 10px;
}

.filelist {
    margin-top: 1em;
    font-size: 80%;
}

.volume {
    font-family: monospace;
}
"""

HTML_START = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN"
		      "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
<head>
<meta http-equiv="content-type" content="text/html; charset=utf8" />
<style type="text/css">
%s
</style>
<title>Anuweb</title>
<body>
<div id="top">Anuweb</div>
<div id="main">
""" % STYLE_CSS

HTML_END = "</div></body></html>"

about_page = StaticResponse('text/html',
    HTML_START +
"""Anuweb (Totem web interface)<br />
Copyright &copy; Daniel Beer &lt;dlbeer@gmail.com&gt;<br />
<a href="http://www.dlbeer.co.nz/">www.dlbeer.co.nz</a><br />
<br />
[<a href="/">Dashboard</a>]
""" + HTML_END)

VOLUME_STEPS = 16

class AnuApp:
    """WSGI application for Totem interface.

    Once instantatated, this object behaves as a WSGI-compatible
    function object.
    """
    def __init__(self, totem_obj, config):
	"""Initialize the WSGI application

	You must supply a reference to the Totem object, and a
	dictionary containing configuration values.
	"""
	self.config = config
	self.rpc = GObjectRPC()
	self.totem_obj = totem_obj
	self.last_path = self.config['default_media_path']
	self.handlers = {
	    '/': self.root,
	    '/about': about_page,
	    '/action_fs': self.action_fs,
	    '/action_play': self.action_play,
	    '/action_pause': self.action_pause,
	    '/action_volume': self.action_volume,
	    '/action_open': self.action_open,
	    '/action_seek': self.action_seek,
	    '/action_ss_reset': self.action_ss_reset,
	    '/browse': self.browse
	}

    def __call__(self, environ, start_response):
	"""Handle a WSGI request.

	It's expected that this function will be called from outside the
	UI thread -- an RPC service is used to invoke methods on the
	Totem object where necessary.
	"""
	return self.handlers.get(environ['PATH_INFO'],
	    not_found)(environ, start_response)

    def is_allowed(self, path):
	"""Is this a browser-accessible path?"""
	r = self.config['path_restrict']

	if path[0:len(r)] != r:
	    return False
	if len(r) == len(path):
	    return True
	if len(r) > 0 and r[len(r) - 1] != '/' and path[len(r)] != '/':
	    return False

	for c in os.path.split(path):
	    if c == '..' or c == '.':
		return False

	return True

    def root(self, environ, start_response):
	"""Path: / (dashboard page)"""
	out = []
	out.append(HTML_START)

	mrl = self.rpc(self.totem_obj.get_current_mrl)

	out.append('Currently playing: ')
	if mrl is None:
	    out.append('nothing')
	else:
	    out.append(cgi.escape(os.path.basename(mrl)))
	    if self.rpc(self.totem_obj.is_paused):
		out.append(' (paused)')
	out.append('<br />')

	out.append('Player: ')
	out.append('[<a href="/action_fs">Fullscreen</a>] ')
	out.append('[<a href="/action_play">Play</a>] ')
	out.append('[<a href="/action_pause">Pause</a>] ')
	out.append('<br />')

	volume = int(round(self.rpc(self.totem_obj.get_volume) * VOLUME_STEPS))
	out.append('Volume: <span class="volume">')
	for i in xrange(0, VOLUME_STEPS + 1):
	    out.append(' <a href="/action_volume?level=%d">' % i)
	    if i <= volume:
		out.append('#')
	    else:
		out.append('-')
	    out.append('</a>')
	out.append('</span><br />')

	out.append('Seek: ')
	out.append('[<a href="/action_seek?rel=-60">&lt;&lt;</a>] ')
	out.append('[<a href="/action_seek?rel=-10">&lt;</a>] ')
	out.append('[<a href="/action_seek?rel=10">&gt;</a>] ')
	out.append('[<a href="/action_seek?rel=60">&gt;&gt;</a>] ')
	out.append('<br />')

	out.append('Misc: ')
	out.append('[<a href="/action_ss_reset">Screensaver off</a>] ')
	out.append('[<a href="/browse">Browse</a>] ')
	out.append('[<a href="/about">About</a>] ')
	out.append('<br />')

	out.append(HTML_END)

	start_response('200 OK',
		[('Content-Type', 'text/html'),
		 ('Content-Length', str(sum(map(len, out)))),
		 ('Cache-Control', 'no-cache')])
	return out

    def browse(self, environ, start_response):
	"""Path: /browse?path=<path> (file browser page)"""
	try:
	    d = cgi.parse_qs(environ['QUERY_STRING'])
	    path = d['path'][0]
	except:
	    path = self.last_path

	if not self.is_allowed(path):
	    return forbidden(environ, start_response)

	try:
	    content = os.listdir(path)
	except:
	    return not_found(environ, start_response)

	self.last_path = path

	p = path
	parentage = []
	while True:
	    parent = os.path.dirname(p)
	    if parent == p:
		break
	    parentage.append(parent)
	    p = parent
	parentage.reverse()

	out = []
	out.append(HTML_START)
	out.append('[<a href="/">Dashboard</a>] ')
	out.append('[<a href="/browse?path=%s">Media home</a>] ' %
		urllib.quote(self.config['default_media_path']))
	out.append('<br />')

	out.append('Path: ')
	for p in parentage:
	    if self.is_allowed(p):
		out.append('<a href="/browse?path=%s">%s</a> :: ' %
		    (urllib.quote(p), cgi.escape(my_base(p))))
	out.append(cgi.escape(my_base(path)))

	out.append('<br />')
	out.append('<div class="filelist">')

	content.sort(key = str.lower)
	for f in content:
	    if f[0] != '.':
		full_path = os.path.join(path, f)
		if os.path.isdir(full_path):
		    out.append('[DIR] <a href="/browse?path=%s">%s</a><br />' %
			    (urllib.quote(full_path), cgi.escape(f)))
		elif os.path.isfile(full_path) and \
		     match_check(f, self.config['filter_pattern']):
		    out.append('<a href="/action_open?path=%s">%s</a><br />' %
			    (urllib.quote(full_path), cgi.escape(f)))

	out.append('</div>')
	out.append(HTML_END)

	start_response('200 OK',
		[('Content-Type', 'text/html'),
		 ('Content-Length', str(sum(map(len, out)))),
		 ('Cache-Control', 'no-cache')])
	return out

    def action_seek(self, environ, start_response):
	"""Path: /action_seek?rel=<n> (seek forward/back)"""
	try:
	    d = cgi.parse_qs(environ['QUERY_STRING'])
	    rel = float(d['rel'][0])
	except:
	    return not_found(environ, start_response)

	self.rpc(self.totem_obj.action_seek_relative, rel * 1000.0)
	return dash_redirect(environ, start_response)

    def action_open(self, environ, start_response):
	"""Path: /action_open?path=<f> (play the given file)"""
	try:
	    d = cgi.parse_qs(environ['QUERY_STRING'])
	    path = d['path'][0]
	except:
	    return not_found(environ, start_response)

	if not self.is_allowed(path):
	    return forbidden(environ, start_response)

	mrl = 'file://' + path
	self.rpc(self.totem_obj.action_remote,
		 totem.REMOTE_COMMAND_REPLACE, mrl)
	self.rpc(self.totem_obj.action_remote,
		 totem.REMOTE_COMMAND_PLAY, mrl)
	return dash_redirect(environ, start_response)

    def action_volume(self, environ, start_response):
	"""Path: /action_volume?level=<n> (change volume)"""
	try:
	    d = cgi.parse_qs(environ['QUERY_STRING'])
	    level = int(d['level'][0])
	except:
	    return bad_request(environ, start_response)

	if level < 0:
	    level = 0
	if level > VOLUME_STEPS:
	    level = VOLUME_STEPS

	self.rpc(self.totem_obj.action_volume, float(level) / VOLUME_STEPS)
	return dash_redirect(environ, start_response)

    def action_play(self, environ, start_response):
	"""Path: /action_play (resume playback)"""
	self.rpc(self.totem_obj.action_play)
	return dash_redirect(environ, start_response)

    def action_pause(self, environ, start_response):
	"""Path: /action_pause (pause playback)"""
	self.rpc(self.totem_obj.action_pause)
	return dash_redirect(environ, start_response)

    def action_fs(self, environ, start_response):
	"""Path: /action_fs (toggle full-screen)"""
	self.rpc(self.totem_obj.action_fullscreen_toggle)
	return dash_redirect(environ, start_response)

    def action_ss_reset(self, environ, start_response):
	"""Path: /action_ss_reset (reset screensaver)"""
	os.system("xset dpms force on")
	os.system("xdg-screensaver reset")
	return dash_redirect(environ, start_response)
