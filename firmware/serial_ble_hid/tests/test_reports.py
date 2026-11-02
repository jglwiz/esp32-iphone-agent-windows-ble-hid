"""Wire contract: independent motion collections, one button-only report."""
from collections import defaultdict
from pathlib import Path
import re
import unittest


def parse_report_map():
    source = (Path(__file__).resolve().parents[1] / 'hid_reports.h').read_text()
    source = re.sub(r'//[^\n]*', '', source)
    data = bytes(int(v, 16) for v in re.findall(r'0x([0-9a-fA-F]{2})', source))
    offset = 0
    state = {'page': 0, 'id': 0, 'size': 0, 'count': 0}
    usages, collections, inputs = [], [], []
    mouse_apps = 0
    while offset < len(data):
        prefix = data[offset]
        size = (0, 1, 2, 4)[prefix & 3]
        typ, tag = (prefix >> 2) & 3, prefix >> 4
        value = int.from_bytes(data[offset+1:offset+1+size], 'little')
        offset += size + 1
        if typ == 1:
            key = {0: 'page', 7: 'size', 8: 'id', 9: 'count'}.get(tag)
            if key:
                state[key] = value
        elif typ == 2 and tag == 0:
            usages.append(value)
        elif typ == 0:
            if tag == 10:
                mouse = value == 1 and state['page'] == 1 and usages == [2]
                mouse_apps += int(mouse)
                collections.append(mouse or (collections[-1] if collections else False))
            elif tag == 12:
                collections.pop()
            elif tag == 8:
                inputs.append({**state, 'constant': bool(value & 1),
                               'relative': bool(value & 4), 'usages': tuple(usages),
                               'mouse': bool(collections and collections[-1])})
            usages = []
    if collections:
        raise ValueError('Unclosed HID collection')
    return mouse_apps, inputs


class ReportMapTests(unittest.TestCase):
    def test_click_payload_has_only_one_shared_button_state(self):
        _, inputs = parse_report_map()
        buttons = [f for f in inputs if f['page'] == 9 and not f['constant']]
        self.assertEqual(len(buttons), 1)
        self.assertEqual((buttons[0]['id'], buttons[0]['size'], buttons[0]['count']), (5, 1, 3))
        self.assertTrue(buttons[0]['mouse'])
        button_report = [f for f in inputs if f['id'] == 5]
        self.assertTrue(all(f['constant'] or f['page'] == 9 for f in button_report))

    def test_wire_sizes_exclude_report_reference_id(self):
        _, inputs = parse_report_map()
        bits = defaultdict(int)
        for f in inputs:
            bits[f['id']] += f['size'] * f['count']
        self.assertEqual(dict(bits), {1: 64, 2: 32, 4: 40, 5: 8})

    def test_motion_axes_do_not_share_one_mouse_collection(self):
        apps, inputs = parse_report_map()
        self.assertEqual(apps, 2)
        relative = [f for f in inputs if f['id'] == 2 and not f['constant']]
        absolute = [f for f in inputs if f['id'] == 4 and not f['constant']]
        self.assertEqual([(f['usages'], f['relative']) for f in relative], [((0x30, 0x31, 0x38), True)])
        self.assertEqual([(f['usages'], f['relative']) for f in absolute], [((0x30, 0x31), False)])


if __name__ == '__main__':
    unittest.main()
