"""Turn a saved unknown transport reward card into a usable template.

`IslandTransport.record_unknown_reward()` dumps unrecognised reward cards into
`assets/island/transport/new_templates/reward_<view>_s<scale>_<time>.png`.
Those crops are drawn at `TransportItemMatcher.CARD_SCALE[view]`, so they have to
be scaled back before they can serve as an unnumbered template.

Usage:
    # see what is waiting
    toolkit/python.exe dev_tools/transport_add_template.py --list

    # add one, the name must be the English item name in DIC_ISLAND_ITEM
    toolkit/python.exe dev_tools/transport_add_template.py \
        --image assets/island/transport/new_templates/reward_list_reward_s0.665_xxx.png \
        --name Pelt

Chinese names work too, they are looked up in DIC_ISLAND_ITEM.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, '.')

import cv2
import numpy as np

from module.island.data import DIC_ISLAND_ITEM
from module.island.transport import TransportItemMatcher

FOLDER = './assets/island/transport'
NEW_FOLDER = './assets/island/transport/new_templates'
CARD_WHITE = TransportItemMatcher.CARD_WHITE
PATTERN = re.compile(r'^reward_(?P<view>[a-z_]+)_s(?P<scale>[0-9.]+)_')


def card_icon(image):
    """Trim the white card background, same rule as TransportItemMatcher.card_icon."""
    diff = np.abs(image.astype(int) - np.array(CARD_WHITE)).max(axis=2) > 25
    ys, xs = np.where(diff)
    if not len(ys):
        return image
    return image[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def resolve_name(name):
    """Accept an English name, a Chinese name, or an item id."""
    for item_id, data in DIC_ISLAND_ITEM.items():
        en = data['name']['en'].replace(' ', '_')
        if name == en or name == data['name']['cn'] or name == str(item_id):
            return en
    return None


def list_waiting():
    if not os.path.isdir(NEW_FOLDER):
        print('no new_templates folder yet, nothing to do')
        return []
    files = [f for f in sorted(os.listdir(NEW_FOLDER))
             if f.startswith('reward_') and f.endswith('.png')]
    if not files:
        print('new_templates is empty')
        return []
    print('waiting in new_templates:')
    for f in files:
        path = os.path.join(NEW_FOLDER, f)
        image = cv2.imread(path)
        size = '{}x{}'.format(image.shape[1], image.shape[0]) if image is not None else 'unreadable'
        print('  {:<56} {}'.format(f, size))
    print()
    print('pick one and re-run with --image <path> --name <English item name>')
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--list', action='store_true', help='show saved unknown cards')
    parser.add_argument('--image', help='crop saved by record_unknown_reward()')
    parser.add_argument('--name', help='English item name, e.g. Pelt')
    parser.add_argument('--dry-run', action='store_true', help='check only, do not write')
    args = parser.parse_args()

    if args.list or not args.image:
        list_waiting()
        return 0

    if not os.path.isfile(args.image):
        print('missing file:', args.image)
        return 2

    match = PATTERN.match(os.path.basename(args.image))
    if not match:
        print('file name does not look like a saved reward card:', os.path.basename(args.image))
        return 2
    view = match.group('view')
    scale = float(match.group('scale'))
    if view not in TransportItemMatcher.CARD_SCALE:
        print('unknown view:', view)
        return 2
    if abs(scale - TransportItemMatcher.CARD_SCALE[view]) > 1e-6:
        print('warning: scale in file name ({}) differs from CARD_SCALE ({})'.format(
            scale, TransportItemMatcher.CARD_SCALE[view]))

    if not args.name:
        print('--name is required, use the English item name from DIC_ISLAND_ITEM')
        return 2
    name = resolve_name(args.name)
    if name is None:
        print('no item matches', repr(args.name), '- check the spelling against DIC_ISLAND_ITEM')
        return 2

    target = os.path.join(FOLDER, name + '.png')
    if os.path.isfile(target):
        print('template already exists:', target)
        return 0

    crop = cv2.imread(args.image)
    icon = card_icon(crop)
    base = cv2.resize(icon, None, fx=1 / scale, fy=1 / scale, interpolation=cv2.INTER_CUBIC)
    print('saved crop  : {}x{} ({}, scale {})'.format(crop.shape[1], crop.shape[0], view, scale))
    print('trimmed     : {}x{}'.format(icon.shape[1], icon.shape[0]))
    print('base size   : {}x{}'.format(base.shape[1], base.shape[0]))

    # Verify the way TransportItemMatcher will use it: scale down by CARD_SCALE[view]
    # and search the original crop.
    probe = cv2.resize(base, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    if probe.shape[0] > crop.shape[0] or probe.shape[1] > crop.shape[1]:
        print('probe larger than the card, the crop probably includes a counter badge')
        return 3
    result = cv2.matchTemplate(crop, probe, cv2.TM_CCOEFF_NORMED)
    score = float(cv2.minMaxLoc(result)[1])
    print('self-match  : {:.3f} (matcher threshold {})'.format(
        score, TransportItemMatcher.SIMILARITY))
    if score < TransportItemMatcher.SIMILARITY:
        print('FAILED: this crop is not consistent enough to become a template')
        return 3

    if args.dry_run:
        print('dry run, nothing written. would create', target)
        return 0

    cv2.imwrite(target, base)
    print('written     :', target)
    print()
    print('restart Alas (or just the island task) so the template cache is rebuilt.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
