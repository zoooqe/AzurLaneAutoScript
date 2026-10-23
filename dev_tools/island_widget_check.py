"""Offline smoke test for the island item picker widget.

pywebio normally needs a live browser session, so we patch the few session
dependent hooks. This exercises the widget tree plus every button callback
(add / edit / delete / clear) and checks the YAML written back to config.
"""
import contextlib
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
from module.island.item_editor import item_label, items_from_text


class FakePin(dict):
    def __getitem__(self, key):
        return dict.get(self, key)


class FakeQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)

    @property
    def last(self):
        return self.items[-1] if self.items else None


class FakeGui:
    def __init__(self):
        self.modified_config_queue = FakeQueue()


class FakeLocal:
    def __init__(self):
        self.gui = FakeGui()


pin = FakePin()
local = FakeLocal()
w.pin = pin
w.local = local
w.use_scope = lambda *args, **kwargs: contextlib.nullcontext()

NAME = 'IslandProduction_IslandProduction_DailyBufferItems'
ITEM_PIN = NAME + '_Item'
AMOUNT_PIN = NAME + '_Amount'

initial = {2000: 300, 3044: 10.5}
initial_text = '\n'.join('{}: {}'.format(item_label(k), v) for k, v in initial.items())
kwargs = {
    'name': NAME,
    'title': 'DailyBufferItems',
    'help': None,
    'value': initial_text,
    'options': [],
    'options_label': [],
    'invalid_feedback': 'x',
    'lang': 'cn',
}


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


root = w.put_arg_island_items(dict(kwargs))
specs = []
walk(root, specs)

print('parsed initial:', items_from_text(initial_text))
print('callback count:', len(CALLBACKS), '(expect 2 rows * 2 + add + clear = 6)')

add = CALLBACKS['4']
clear = CALLBACKS[str(len(CALLBACKS) - 1)]
queue = local.gui.modified_config_queue

pin[ITEM_PIN] = '2001'
pin[AMOUNT_PIN] = '500'
add(None)
print('add 2001=500 ->', queue.last['value'].replace('\n', ' | '))

pin[ITEM_PIN] = '9999'
pin[AMOUNT_PIN] = '1'
add(None)
print('invalid item rejected, queue size still', len(queue.items))

pin[ITEM_PIN] = '2001'
pin[AMOUNT_PIN] = '-3'
add(None)
print('negative amount rejected, queue size still', len(queue.items))

pin[ITEM_PIN] = '2000'
pin[AMOUNT_PIN] = '0'
add(None)
print('zero amount rejected, queue size still', len(queue.items))

delete = CALLBACKS['1']
pin[ITEM_PIN] = ''
pin[AMOUNT_PIN] = ''
delete(None)
print('delete row ->', queue.last['value'].replace('\n', ' | '))

clear(None)
print('clear ->', repr(queue.last['value']), '| key:', queue.last['name'])

print('widget built with', len(specs), 'spec nodes')
