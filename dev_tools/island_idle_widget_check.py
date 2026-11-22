"""Offline render check for the idle accumulating picker.

The idle accumulating list used to be a textarea holding ``{item: rate}`` YAML,
where the rate is not a stock amount but the expected daily accumulation speed
the task uses to weigh idle recipes. It is now an ``island_idle_items`` widget:
pick an item from a dropdown of everything a production recipe can output, and
the rate is written from what one idle dispatch produces.

Checks:
- the argument is declared as ``island_idle_items`` in args.json
- the widget renders with exactly one pin (the item dropdown, no amount input)
- the dropdown offers exactly the producible items
- an existing list is rendered as rows, one per item, showing its rate
- the add callback writes the dispatch amount to the right config path
- duplicate items, non-producible items and an empty selection are rejected
"""
import json
import sys
import types

sys.path.insert(0, '.')

import pywebio.io_ctrl as pc
import pywebio.output as po
import pywebio.pin as pp

FAKE_SCOPE = '_probe_scope'
po.get_scope = lambda: FAKE_SCOPE
pp.get_scope = lambda: FAKE_SCOPE

pc.output_register_callback = lambda callback: '0'
pc.Output.show = lambda self, *args, **kwargs: None

import module.webui.lang as L

L.reload()

import module.webui.widgets as w
from module.island.data import DIC_ISLAND_ITEM, DIC_ISLAND_RECIPE
from module.island.item_editor import (
    idle_accumulating_amount,
    idle_accumulating_options,
    is_producible_item,
    producible_items,
)
from module.webui.lang import t

NAME = 'IslandProduction_IslandProduction_IdleAccumulatingItems'
failures = []


def check(label, condition, detail=''):
    detail = str(detail) if detail != '' else ''
    print('[{:^4}] {}{}'.format('OK' if condition else 'FAIL', label, ' | ' + detail if detail else ''))
    if not condition:
        failures.append(label)


def walk(node, found):
    spec = node.spec if isinstance(node, pc.Output) else node
    if not isinstance(spec, dict):
        return
    found.append(spec)
    children = spec.get('contents')
    if isinstance(children, list):
        for child in children:
            walk(child, found)
    content = spec.get('content')
    if isinstance(content, list):
        for child in content:
            walk(child, found)
    data = spec.get('data')
    if isinstance(data, dict) and isinstance(data.get('contents'), list):
        for child in data['contents']:
            walk(child, found)


def render(value, lang='cn'):
    root = w.put_output({
        'name': NAME,
        'title': 'IdleAccumulatingItems',
        'widget_type': 'island_idle_items',
        'help': t('IslandProduction.IdleAccumulatingItems.help'),
        'value': value,
        'options': [],
        'options_label': [],
        'invalid_feedback': '',
        'lang': lang,
    })
    specs = []
    walk(root, specs)
    return specs


print()
print('== args.json ==')
args = json.load(open('module/config/argument/args.json', encoding='utf-8'))
arg = args['IslandProduction']['IslandProduction']['IdleAccumulatingItems']
check('widget type', arg['type'] == 'island_idle_items', arg['type'])

print()
print('== producible items ==')
producible = producible_items()
recipes = {
    item_id
    for recipe in DIC_ISLAND_RECIPE.values()
    for item_id, amount in (recipe.get('commission_product') or {}).items()
    if amount > 0
}
check('producible items match recipe outputs', set(producible) == recipes, len(producible))
check('producible items sorted', producible == sorted(producible))
check('no duplicate producers', all(
    sum(1 for r in DIC_ISLAND_RECIPE.values() if item_id in (r.get('commission_product') or {})) == 1
    for item_id in producible
))
# Seeds are planted, not produced by a recipe, and must not be offered
check('seed item rejected', not is_producible_item(1005), 1005)
check('unknown item rejected', not is_producible_item(999999), 999999)
check('empty item rejected', not is_producible_item(''), '')

print()
print('== render ==')
specs = render('{}')
pins = [s['input'] for s in specs if s.get('type') == 'pin']
check('renders', bool(specs))
check('has exactly one pin', len(pins) == 1, str(len(pins)))
if pins:
    pin = pins[0]
    options = [o['value'] for o in pin['options']]
    check('dropdown placeholder', options[0] == '')
    check(
        'dropdown offers every producible item',
        options[1:] == [str(i) for i in producible],
        f'{len(options) - 1} options',
    )
    check('placeholder label', pin['options'][0]['label'] == t('Gui.Text.IslandIdlePick'))
    labels = [o['label'] for o in pin['options'][1:]]
    check('labels carry the item id', all('(' in label for label in labels), labels[0])
    check('no amount pin', len(pins) == 1)

print()
print('== existing list ==')
sample = {
    2000: 1603.679,
    3047: 1.6,
    3039: 10,
}
text = '\n'.join(
    '{} ({}): {}'.format(
        DIC_ISLAND_ITEM[i]['name']['cn'], i, v
    )
    for i, v in sample.items()
)
specs = render(text)
html = ' '.join(
    str(s.get('content')) for s in specs if s.get('type') == 'html'
)
check('shows every configured row', all(f'({i})' in html for i in sample), sample.keys())
check('shows the stored rates', all(str(v).rstrip('0').rstrip('.') in html for v in sample.values()))

print()
print('== dispatch amount ==')
for item_id in (2000, 2001, 3011, 3047):
    recipes_of_item = [
        r for r in DIC_ISLAND_RECIPE.values()
        if (r.get('commission_product') or {}).get(item_id, 0) > 0
    ]
    recipe = recipes_of_item[0]
    batches = int(6 * 36000 // recipe['workload'])
    expected = batches * recipe['commission_product'][item_id]
    check(
        f'{DIC_ISLAND_ITEM[item_id]["name"]["cn"]} rate = {expected}',
        idle_accumulating_amount(item_id) == expected,
        idle_accumulating_amount(item_id),
    )
check('non-producible item has no rate', idle_accumulating_amount(1005) is None)
check('unknown item has no rate', idle_accumulating_amount(999999) is None)
check('rate follows dispatch hours', idle_accumulating_amount(2000, dispatch_hours=12) == 2916,
      idle_accumulating_amount(2000, dispatch_hours=12))

print()
print('== options helper ==')
pairs = idle_accumulating_options('cn')
check('options helper covers producible items', [v for v, _ in pairs] == [str(i) for i in producible])
check('options helper labels', pairs[0][1].endswith('({})'.format(pairs[0][0])), pairs[0][1])

print()
print('== add callback ==')

CALLBACKS = {}


def fake_register(callback):
    callback_id = str(len(CALLBACKS))
    CALLBACKS[callback_id] = callback
    return callback_id


class _Queue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class _FakeGui:
    def __init__(self):
        self.alas_name = 'alas'
        self.modified_config_queue = _Queue()


class _Pin(dict):
    def __getitem__(self, key):
        return dict.get(self, key)


import contextlib

pc.output_register_callback = fake_register
w.pin = _Pin()
w.toast = lambda *args, **kwargs: None
w.use_scope = lambda *args, **kwargs: contextlib.nullcontext()

gui = _FakeGui()
w.local = types.SimpleNamespace(gui=gui)
w.put_output({
    'name': NAME,
    'title': 'IdleAccumulatingItems',
    'widget_type': 'island_idle_items',
    'help': None,
    'value': '{}',
    'options': [],
    'options_label': [],
    'invalid_feedback': '',
    'lang': 'cn',
})
check('add button registered', '0' in CALLBACKS, str(len(CALLBACKS)))
if '0' in CALLBACKS:
    item_id = 3039
    w.pin[f'{NAME}_IdleItem'] = str(item_id)
    CALLBACKS['0'](None)
    written = gui.modified_config_queue.items[-1]['value'] if gui.modified_config_queue.items else ''
    rate = idle_accumulating_amount(item_id)
    rate_text = str(int(rate)) if float(rate).is_integer() else str(rate)
    check(
        f'add writes rate {rate_text}',
        f'({item_id}): {rate_text}' in written,
        written.replace('\n', ' | '),
    )
    check(
        'add saves to the right path',
        gui.modified_config_queue.items[-1]['name'] == 'IslandProduction.IslandProduction.IdleAccumulatingItems',
        gui.modified_config_queue.items[-1]['name'],
    )
    before = len(gui.modified_config_queue.items)
    CALLBACKS['0'](None)
    check('duplicate ignored', len(gui.modified_config_queue.items) == before, str(before))

    w.pin[f'{NAME}_IdleItem'] = '1005'
    before = len(gui.modified_config_queue.items)
    CALLBACKS['0'](None)
    check('non-producible item rejected', len(gui.modified_config_queue.items) == before)

    w.pin[f'{NAME}_IdleItem'] = ''
    before = len(gui.modified_config_queue.items)
    CALLBACKS['0'](None)
    check('empty selection rejected', len(gui.modified_config_queue.items) == before)

print()
print('{} failure(s)'.format(len(failures)))
sys.exit(1 if failures else 0)
