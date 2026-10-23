"""Render the island item picker against the real config/alas.json.

Unlike island_widget_check.py (which uses fake data), this one loads the items
that are actually stored in config/alas.json, so the numbers here should match
what the browser shows.

Checks:
- how many rows each field renders
- that every field parses back into the same mapping
- that no <p> tag is emitted (that was the source of the huge row spacing)
"""
import contextlib
import json
import sys

sys.path.insert(0, '.')

import pywebio.io_ctrl as pc
import pywebio.output as po
import pywebio.pin as pp

FAKE_SCOPE = '_probe_scope'
po.get_scope = lambda: FAKE_SCOPE
pp.get_scope = lambda: FAKE_SCOPE

CALLBACKS = {}


def fake_register(callback):
    callback_id = str(len(CALLBACKS))
    CALLBACKS[callback_id] = callback
    return callback_id


pc.output_register_callback = fake_register
pc.Output.show = lambda self, *args, **kwargs: None

import module.webui.lang as L

L.reload()

import module.webui.widgets as w
from module.island.item_editor import items_from_text, items_to_text


class FakePin(dict):
    def __getitem__(self, key):
        return dict.get(self, key)


class FakeQueue:
    def put(self, item):
        pass


class FakeLocal:
    def __init__(self):
        gui = type('Gui', (), {})()
        gui.modified_config_queue = FakeQueue()
        self.gui = gui


w.pin = FakePin()
w.local = FakeLocal()
w.use_scope = lambda *args, **kwargs: contextlib.nullcontext()

with open('config/alas.json', encoding='utf-8') as f:
    alas = json.load(f)

section = alas['IslandProduction']['IslandProduction']
FIELDS = ('DailyBufferItems', 'HardFloorItems', 'IdleAccumulatingItems')

failures = []


def walk(node, found):
    spec = node.spec if isinstance(node, pc.Output) else node
    if not isinstance(spec, dict):
        return
    found.append(spec)
    for key in ('contents', 'content'):
        children = spec.get(key)
        if isinstance(children, list):
            for child in children:
                walk(child, found)


for field in FIELDS:
    text = section.get(field) or ''
    items = items_from_text(text)
    CALLBACKS.clear()

    root = w.put_arg_island_items({
        'name': 'IslandProduction_IslandProduction_{}'.format(field),
        'title': field,
        'help': None,
        'value': text,
        'options': [],
        'options_label': [],
        'invalid_feedback': 'x',
        'lang': 'cn',
    })
    specs = []
    walk(root, specs)

    # every row registers 2 callbacks (edit, delete); the add row registers 1,
    # plus 1 for "clear all"
    expect_callbacks = len(items) * 2 + 2
    html_specs = [s for s in specs if s.get('type') == 'html']
    html_text = ''.join(str(s.get('content', '')) for s in html_specs)
    has_p = '<p' in html_text.lower()
    roundtrip = items_to_text(items_from_text(items_to_text(items))) == items_to_text(items)

    ok = (len(CALLBACKS) == expect_callbacks) and roundtrip and not has_p
    print('[{:^4}] {:<22} items={:<3} callbacks={} (expect {}) <p>tag={} roundtrip={}'.format(
        'OK' if ok else 'FAIL', field, len(items), len(CALLBACKS), expect_callbacks, has_p, roundtrip))
    if items:
        sample = list(items.items())[:3]
        print('       sample:', ', '.join('id={} amt={}'.format(k, v) for k, v in sample))
    if not ok:
        failures.append(field)

print()
print('{} fields checked, {} failed'.format(len(FIELDS), len(failures)))
sys.exit(1 if failures else 0)
