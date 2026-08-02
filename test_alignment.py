"""测试 Step1 词色对齐生成逻辑（v0.7.7）"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))
from unittest import mock

import app as app_module


FAKE_AI = '''{"units":[
  {"en":"The","pos":"DET","zh":""},
  {"en":"cat","pos":"NOUN","zh":"猫"},
  {"en":"sleeps","pos":"VERB","zh":"在睡觉"},
  {"en":".","pos":"PUNCT","zh":""}
]}'''


def test_generate_alignment():
    with mock.patch.object(app_module, 'resolve_api_key', return_value='fake-key'), \
         mock.patch.object(app_module, 'get_ai_proxy', return_value={'base_url': '', 'model': ''}), \
         mock.patch.object(app_module.ds, '_chat', return_value=FAKE_AI):
        res = app_module.generate_alignment('The cat sleeps.', '猫在睡觉。', user=object())
    assert res and 'units' in res, '应返回带 units 的 dict'
    units = res['units']
    # 顺序：The(cat sleeps .)
    assert units[0]['en'] == 'The' and units[0]['content'] is False and units[0]['color'] is None
    assert units[1]['en'] == 'cat' and units[1]['content'] is True and units[1]['color'] == '#e74c3c'
    assert units[2]['en'] == 'sleeps' and units[2]['content'] is True and units[2]['color'] == '#2980b9'
    assert units[3]['en'] == '.' and units[3]['content'] is False and units[3]['color'] is None
    print('PASS generate_alignment: 虚词黑色、实词按序上色', [u['color'] for u in units])


def test_alignment_or_empty_handles_exception():
    with mock.patch.object(app_module, 'generate_alignment', side_effect=RuntimeError('boom')):
        res = app_module.alignment_or_empty('x', 'y', None)
    assert res == {}, '异常应降级为空 dict'
    print('PASS alignment_or_empty: 异常降级为空 dict')


def test_generate_alignment_no_key():
    with mock.patch.object(app_module, 'resolve_api_key', return_value=None):
        res = app_module.generate_alignment('The cat sleeps.', '猫在睡觉。', user=object())
    assert res is None, '无 AI key 应返回 None'
    print('PASS generate_alignment: 无 key 返回 None')


def test_generate_alignment_phrase_level():
    FAKE = '''{"units":[
      {"en":"The farm","pos":"NOUN","zh":"农场"},
      {"en":"wakes up","pos":"VERB","zh":"醒来"},
      {"en":"early","pos":"ADV","zh":"在清晨"},
      {"en":".","pos":"PUNCT","zh":""}
    ]}'''
    with mock.patch.object(app_module, 'resolve_api_key', return_value='fake-key'), \
         mock.patch.object(app_module, 'get_ai_proxy', return_value={'base_url': '', 'model': ''}), \
         mock.patch.object(app_module.ds, '_chat', return_value=FAKE):
        res = app_module.generate_alignment('The farm wakes up early .', '农场早早醒来。', user=object())
    units = res['units']
    # 短语级：多词片段作为一个 unit，且按「有中文即上色」
    assert units[0]['en'] == 'The farm' and units[0]['content'] is True and units[0]['color'] == '#e74c3c', units[0]
    assert units[1]['en'] == 'wakes up' and units[1]['content'] is True and units[1]['color'] == '#2980b9', units[1]
    assert units[2]['en'] == 'early' and units[2]['content'] is True and units[2]['color'] == '#27ae60', units[2]
    assert units[3]['en'] == '.' and units[3]['content'] is False and units[3]['color'] is None, units[3]
    print('PASS generate_alignment(phrase): 短语级切分 + 按中文上色', [u['en'] for u in units])


if __name__ == '__main__':
    test_generate_alignment()
    test_alignment_or_empty_handles_exception()
    test_generate_alignment_no_key()
    test_generate_alignment_phrase_level()
    print('\nAll alignment tests passed.')
