"""Offline render check for the bedtime time picker.

PyWebIO needs a live browser, so the session hooks are patched the same way as
in island_widget_check.py. The whole IslandProduction group is rendered from
args.json, so a widget type that the GUI cannot build is caught here rather
than when the page opens.

Checks:
- BedtimeTime is a "time" widget and renders one option per 30 minutes
- the stored value is the selected option, including an off-grid one
- an invalid `step` falls back to 30 instead of producing no options
- the option labels are the times themselves (no i18n key is needed)
- the page group still renders every argument
"""
import contextlib
import json
import re
import sys

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
from module.webui.lang import t
from module.webui.utils import parse_pin_value

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
    for key in ('contents', 'content'):
        children = spec.get(key)
        if isinstance(children, list):
            for child in children:
                walk(child, found)


def build(name, title, value, widget_type='time', **extra):
    kwargs = {
        'name': name,
        'title': title,
        'widget_type': widget_type,
        'help': t(f'IslandProduction.{title}.help'),
        'value': value,
        'options': [],
        'options_label': [],
        'invalid_feedback': t('Gui.Text.InvalidFeedBack', value),
    }
    kwargs.update(extra)
    root = w.put_output(kwargs)
    specs = []
    walk(root, specs)
    pins = [s['input'] for s in specs if s.get('type') == 'pin']
    return pins[0] if pins else None


print()
print('== history ==')
args = json.load(open('module/config/argument/args.json', encoding='utf-8'))
group = args['IslandProduction']['IslandProduction']
check('BedtimeTime type', group['BedtimeTime']['type'] == 'time', group['BedtimeTime']['type'])
check('BedtimeTime valuetype', group['BedtimeTime']['valuetype'] == 'str')
check('bedtime step', group['BedtimeTime'].get('step') == 30, group['BedtimeTime'].get('step'))

print()
print('== each argument still renders ==')
for arg, arg_dict in group.items():
    kwargs = dict(arg_dict)
    kwargs['widget_type'] = kwargs.pop('type')
    kwargs['name'] = f'IslandProduction_IslandProduction_{arg}'
    kwargs['title'] = arg
    kwargs['help'] = None
    kwargs['options'] = kwargs.pop('option', [])
    kwargs['options_label'] = []
    kwargs['invalid_feedback'] = 'x'
    if kwargs['widget_type'] == 'island_items':
        kwargs['lang'] = 'cn'
    try:
        root = w.put_output(kwargs)
        specs = []
        walk(root, specs)
        check(f'render {arg} ({kwargs["widget_type"]})', bool(specs))
    except Exception as e:
        check(f'render {arg} ({kwargs["widget_type"]})', False, repr(e))

print()
print('== options ==')
pin = build('IslandProduction_IslandProduction_BedtimeTime', 'BedtimeTime', '00:00')
options = [o['value'] for o in pin['options']]
labels = [o['label'] for o in pin['options']]
selected = [o['value'] for o in pin['options'] if o.get('selected')]
check('48 options per day', len(options) == 48, str(len(options)))
check('first is 00:00', options[0] == '00:00', options[0])
check('last is 23:30', options[-1] == '23:30', options[-1])
check('labels are the times', labels == options)
check('00:00 selected', selected == ['00:00'], str(selected))

pin = build('IslandProduction_IslandProduction_BedtimeTime', 'BedtimeTime', '23:00')
selected = [o['value'] for o in pin['options'] if o.get('selected')]
check('23:00 selectable and selected', selected == ['23:00'], str(selected))

print()
print('== off-grid and odd values ==')
pin = build('IslandProduction_IslandProduction_BedtimeTime', 'BedtimeTime', '07:15')
options = [o['value'] for o in pin['options']]
selected = [o['value'] for o in pin['options'] if o.get('selected')]
check('off-grid value kept', '07:15' in options and selected == ['07:15'], str(selected))
check('off-grid added once', len(options) == 49, str(len(options)))

pin = build('IslandProduction_IslandProduction_BedtimeTime', 'BedtimeTime', 23)
selected = [o['value'] for o in pin['options'] if o.get('selected')]
check('bare hour becomes 23:00', selected == ['23:00'], str(selected))

pin = build('IslandProduction_IslandProduction_BedtimeTime', 'BedtimeTime', '00:00', step=15)
check('step 15 gives 96 options', len(pin['options']) == 96, str(len(pin['options'])))
pin = build('IslandProduction_IslandProduction_BedtimeTime', 'BedtimeTime', '00:00', step=7)
check('invalid step falls back to 30', len(pin['options']) == 48, str(len(pin['options'])))

print()
print('== saving ==')
validate = group['BedtimeTime']['validate']
check('parse_pin_value keeps the string', parse_pin_value('07:30', 'str') == '07:30')
for value, expect in (('00:00', True), ('23:30', True), ('7:30', False), ('24:00', False), ('', False)):
    check(f'validate {value!r}', bool(re.fullmatch(validate, str(value))) == expect)

hours_validate = group['BedtimeHours']['validate']
for value, expect in (('8', True), ('7.5', True), ('0', False), ('25', False)):
    check(f'hours validate {value!r}', bool(re.fullmatch(hours_validate, str(value))) == expect)

print()
print('{} failure(s)'.format(len(failures)))
sys.exit(1 if failures else 0)
