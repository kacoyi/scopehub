"""Exercise the production drawing bundle in an ephemeral Ubuntu WebKitGTK view."""
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('WebKit2', '4.1')
from gi.repository import GLib, Gtk, WebKit2  # noqa: E402

root = Path(__file__).resolve().parents[2]
csp = json.loads((root / 'apps/desktop/src-tauri/tauri.conf.json').read_text())['app']['security']['csp']


class StaticBundle(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Content-Security-Policy', csp)
        super().end_headers()

    def log_message(self, *args):
        pass


# Test automation operates the ordinary host controls. The program field goes
# through the same worker as an edit made in a native window.
exercise = """
const wait = async (predicate) => {
  for (let i = 0; i < 400; i++) {
    const value = predicate();
    if (value) return value;
    await new Promise(r => setTimeout(r, 20));
  }
  throw Error('Native UI did not reach the expected state: ' + document.body.innerText.slice(-600));
};
const button = name => [...document.querySelectorAll('dialog[open] button')].find(b => b.textContent === name);
const sample = await wait(() => button('Try sample program'));
const start = performance.now();
sample.click();
const circle = await wait(() => document.querySelector('figure circle'));
if (circle.getAttribute('cx') !== '64') throw Error('Unexpected initial projection');
const firstRenderMs = performance.now() - start;
button('Accept view').click();
await wait(() => document.querySelector('[role=status]')?.textContent === 'Accepted view');
button('Expand viewport').click();
await wait(() => document.querySelector('figure circle')?.getAttribute('cx') === '202');
const editor = document.querySelector('dialog[open] textarea');
const setCode = text => {
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(editor, text);
  editor.dispatchEvent(new Event('input', {bubbles:true}));
};
setCode('// stock free\\nif ([typeof fetch, typeof document, typeof __TAURI_INTERNALS__, typeof process, typeof indexedDB, typeof confirm].every(x => x === "undefined")) rect(22,30,rows[0].volume,10,0);');
await wait(() => !button('Preview draft').disabled);
button('Preview draft').click();
await wait(() => document.querySelector('figure rect')?.getAttribute('width') === '20');
button('Restore accepted view').click();
await wait(() => document.querySelector('figure circle')?.getAttribute('cx') === '202');
setCode('// future free\\nwhile (true) {}');
const stallStart = performance.now();
button('Preview draft').click();
await wait(() => document.querySelector('[role=status]')?.textContent === 'Evaluating program…');
const slider = document.querySelector('dialog[open] input[type=range]');
Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(slider, '7');
slider.dispatchEvent(new Event('input', {bubbles:true}));
await wait(() => document.querySelector('figure g[stroke-width="7"]'));
const hostEditMs = performance.now() - stallStart;
await wait(() => document.querySelector('dialog[open] [role=alert]')?.textContent.includes('time limit'));
const stopMs = performance.now() - stallStart;
button('Restore accepted view').click();
await wait(() => editor.value.startsWith('// future overlay') && !document.querySelector('[role=alert]'));
return JSON.stringify({firstRenderMs,hostEditMs,stopMs,restoredX:document.querySelector('figure circle').getAttribute('cx')});
"""


def main():
    outcome = {}
    def finish(view, result):
        try:
            outcome.update(json.loads(view.call_async_javascript_function_finish(result).to_string()))
        except GLib.Error as error:
            outcome['error'] = str(error)
        Gtk.main_quit()

    def loaded(view, event):
        if event == WebKit2.LoadEvent.FINISHED:
            view.call_async_javascript_function(exercise, -1, None, None, None, None, finish)

    with ThreadingHTTPServer(('127.0.0.1', 0), partial(StaticBundle, directory=root / 'apps/desktop/ui/dist')) as server:
        Thread(target=server.serve_forever, daemon=True).start()
        context = WebKit2.WebContext.new_ephemeral()
        context.set_preferred_languages(['en-US'])
        view = WebKit2.WebView.new_with_context(context)
        window = Gtk.Window()
        window.set_default_size(1280, 800)
        window.add(view)
        window.show_all()
        view.connect('load-changed', loaded)
        view.load_uri(f'http://127.0.0.1:{server.server_port}/?renderer=drawing')
        GLib.timeout_add_seconds(30, Gtk.main_quit)
        Gtk.main()
        server.shutdown()
    print(json.dumps({'surface': 'Ephemeral GTK3 WebKit view, production bundle and configured CSP, no Tauri bridge',
                      'webkitgtk': '.'.join(map(str, [WebKit2.get_major_version(), WebKit2.get_minor_version(),
                                                      WebKit2.get_micro_version()])), **outcome}))
    return 0 if outcome.get('restoredX') == '202' and 'error' not in outcome else 1


if __name__ == '__main__':
    raise SystemExit(main())
