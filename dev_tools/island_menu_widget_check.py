"""Offline render check for the restaurant menu picker.

The restaurant menus used to be a textarea holding ``{dish: amount}`` YAML and
the user had to type the amount by hand. They are now an ``island_menu``
widget: pick a dish from a dropdown limited to what that restaurant serves, and
the amount is written from the shelf capacity.

Checks:
- the five menus are declared as ``island_menu`` in args.json
- each menu renders and exposes exactly one pin (the dish dropdown, no amount)
- the dropdown offers exactly the dishes of that restaurant, and nothing else
- an existing menu is rendered as rows, one per dish
- the dish amount follows grade + waitress capacity, and falls back safely
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
from module.island.data import DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE
from module.island.item_editor import is_menu_item, menu_item_options, restaurant_id_of_menu
from module.webui.lang import t

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
    # put_select() is wrapped in a custom_widget whose payload holds the pin
    data = spec.get('data')
    if isinstance(data, dict) and isinstance(data.get('contents'), list):
        for child in data['contents']:
            walk(child, found)


def render(menu_key, value, restaurant_id=None, lang='cn'):
    name = f'IslandBusiness_IslandRestaurant_{menu_key}'
    root = w.put_output({
        'name': name,
        'title': menu_key,
        'widget_type': 'island_menu',
        'help': t(f'IslandRestaurant.{menu_key}.help'),
        'value': value,
        'options': [],
        'options_label': [],
        'invalid_feedback': '',
        'lang': lang,
    })
    specs = []
    walk(root, specs)
    return specs


def pins_of(specs):
    return [s['input'] for s in specs if s.get('type') == 'pin']


MENUS = {
    'KoiMenu': 601,
    'BearMenu': 602,
    'EateryMenu': 603,
    'GrillMenu': 604,
    'CafeMenu': 901,
}

print()
print('== args.json ==')
args = json.load(open('module/config/argument/args.json', encoding='utf-8'))
group = args['IslandBusiness']['IslandRestaurant']
for menu_key in MENUS:
    check(f'{menu_key} type', group[menu_key]['type'] == 'island_menu', group[menu_key]['type'])

print()
print('== render ==')
for menu_key, restaurant_id in MENUS.items():
    specs = render(menu_key, '{}')
    pins = pins_of(specs)
    check(f'{menu_key} renders', bool(specs))
    check(f'{menu_key} has exactly one pin', len(pins) == 1, str(len(pins)))
    if not pins:
        continue
    pin = pins[0]
    options = [o['value'] for o in pin['options']]
    labels = [o['label'] for o in pin['options']]
    expected = [str(i) for i in sorted(DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE[restaurant_id])]
    check(
        f'{menu_key} offers {len(expected)} dishes',
        options[0] == '' and options[1:] == expected,
        f'{len(options) - 1} options',
    )
    check(f'{menu_key} placeholder label', labels[0] == t('Gui.Text.IslandMenuPick'), labels[0])
    # Only this restaurant's dishes, another restaurant's dish must be absent
    foreign = [
        str(i) for i in DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE[601]
        if i not in DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE[restaurant_id]
    ]
    check(
        f'{menu_key} excludes other restaurants',
        not (set(foreign) & set(options)),
        f'e.g. {foreign[:2]}',
    )
    check(f'{menu_key} no amount pin', len(pins) == 1)

print()
print('== existing menu ==')
for menu_key, restaurant_id in MENUS.items():
    dishes = sorted(DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE[restaurant_id])[:3]
    text = '\n'.join(f'{i}: 7.0' for i in dishes)
    specs = render(menu_key, text)
    texts = [
        str(s.get('content'))
        for s in specs
        if s.get('type') == 'html'
    ]
    joined = ' '.join(texts)
    found = sum(1 for i in dishes if f'({i})' in joined)
    check(f'{menu_key} shows {len(dishes)} configured rows', found == len(dishes), str(found))
    # The stored amounts must survive a render round trip unchanged
    check(
        f'{menu_key} keeps stored amounts',
        all(f'{i}' in joined for i in dishes),
        str(dishes),
    )

print()
print('== invalid dish rejected ==')
for menu_key, restaurant_id in MENUS.items():
    own = next(iter(DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE[restaurant_id]))
    check(f'{menu_key} accepts own dish', is_menu_item(restaurant_id, own))
foreign_id = next(iter(DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE[601]))
check('Bear rejects Koi dish', not is_menu_item(602, foreign_id), foreign_id)
check('unknown restaurant rejects everything', not is_menu_item(None, foreign_id))
check('menu options of unknown restaurant empty', menu_item_options(None) == [])

print()
print('== dish amount ==')


class _FakeConfig:
    def __init__(self, data):
        self.data = data

    def read_file(self, name):
        return self.data


class _FakeGui:
    def __init__(self, data):
        self.alas_name = 'alas'
        self.alas_config = _FakeConfig(data)


def fake_config(grade, waitress_slots=None):
    """Config dict with the given grade for every restaurant.

    Args:
        waitress_slots: {restaurant_id: (slot1, slot2)}, defaults to no waitress.
    """
    from module.island_handler.restaurant_config import RESTAURANT_CONFIG

    waitress_slots = waitress_slots or {}
    section = {}
    for rid, config in RESTAURANT_CONFIG.items():
        section[config['grade_key']] = grade
        slots = waitress_slots.get(rid, ('none', 'none'))
        for key, value in zip(config['waitress_keys'], slots):
            section[key] = value
    return {'IslandBusiness': {'IslandRestaurant': section}}


original_local = w.local
try:
    w.local = types.SimpleNamespace(gui=_FakeGui(fake_config('diamond', {601: ('Chao_Ho', 'any')})))
    check('diamond + Chao_Ho = 7', w.island_menu_dish_amount(601) == 7, w.island_menu_dish_amount(601))

    w.local = types.SimpleNamespace(gui=_FakeGui(fake_config('diamond')))
    check('diamond, no waitress = 6', w.island_menu_dish_amount(601) == 6, w.island_menu_dish_amount(601))

    w.local = types.SimpleNamespace(gui=_FakeGui(fake_config('bronze')))
    check('bronze = 5', w.island_menu_dish_amount(601) == 5, w.island_menu_dish_amount(601))

    # An invalid waitress value must not raise out of the widget
    w.local = types.SimpleNamespace(gui=_FakeGui(fake_config('diamond', {601: ('Not_A_Waitress', 'none')})))
    check('invalid waitress falls back to 6', w.island_menu_dish_amount(601) == 6, w.island_menu_dish_amount(601))

    w.local = types.SimpleNamespace(gui=_FakeGui({}))
    check('empty config falls back to 6', w.island_menu_dish_amount(601) == 6, w.island_menu_dish_amount(601))

    w.local = types.SimpleNamespace(gui=None)
    check('broken session falls back to 6', w.island_menu_dish_amount(601) == 6, w.island_menu_dish_amount(601))

    check('unknown restaurant falls back to 6', w.island_menu_dish_amount(None) == 6)
finally:
    w.local = original_local

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


class _Pin(dict):
    def __getitem__(self, key):
        return dict.get(self, key)


import contextlib

pc.output_register_callback = fake_register
w.pin = _Pin()
w.toast = lambda *args, **kwargs: None
w.use_scope = lambda *args, **kwargs: contextlib.nullcontext()

for menu_key, restaurant_id in MENUS.items():
    CALLBACKS.clear()
    gui = _FakeGui(fake_config('diamond', {601: ('Chao_Ho', 'any')}))
    gui.modified_config_queue = _Queue()
    w.local = types.SimpleNamespace(gui=gui)

    name = f'IslandBusiness_IslandRestaurant_{menu_key}'
    w.put_output({
        'name': name,
        'title': menu_key,
        'widget_type': 'island_menu',
        'help': None,
        'value': '{}',
        'options': [],
        'options_label': [],
        'invalid_feedback': '',
        'lang': 'cn',
    })
    dish = sorted(DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE[restaurant_id])[0]
    w.pin[f'{name}_Dish'] = str(dish)
    check(f'{menu_key} add button registered', '0' in CALLBACKS, str(len(CALLBACKS)))
    if '0' not in CALLBACKS:
        continue
    CALLBACKS['0'](None)
    written = gui.modified_config_queue.items[-1]['value'] if gui.modified_config_queue.items else ''
    # Only Koi has a capacity bonus waitress in the fake config
    expected_amount = 7 if restaurant_id == 601 else 6
    check(
        f'{menu_key} add writes capacity {expected_amount}',
        f'({dish}): {expected_amount}' in written,
        written.replace('\n', ' | '),
    )
    check(
        f'{menu_key} add saves to the right path',
        gui.modified_config_queue.items[-1]['name'] == f'IslandBusiness.IslandRestaurant.{menu_key}',
        gui.modified_config_queue.items[-1]['name'],
    )
    # Adding the same dish twice must not duplicate or rewrite the config
    before = len(gui.modified_config_queue.items)
    CALLBACKS['0'](None)
    check(f'{menu_key} duplicate ignored', len(gui.modified_config_queue.items) == before, str(before))

    # A dish of another restaurant is rejected
    own_dishes = DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE[restaurant_id]
    foreign = next(
        i
        for rid, menu in DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE.items()
        if rid != restaurant_id
        for i in menu
        if i not in own_dishes
    )
    w.pin[f'{name}_Dish'] = str(foreign)
    before = len(gui.modified_config_queue.items)
    CALLBACKS['0'](None)
    check(f'{menu_key} foreign dish rejected', len(gui.modified_config_queue.items) == before)

print()
print('== restaurant lookup ==')
for menu_key, restaurant_id in MENUS.items():
    check(f'{menu_key} -> {restaurant_id}', restaurant_id_of_menu(menu_key) == restaurant_id)
check('unknown menu key', restaurant_id_of_menu('NopeMenu') is None)

print()
print('{} failure(s)'.format(len(failures)))
sys.exit(1 if failures else 0)
