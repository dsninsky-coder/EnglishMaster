"""v0.8.7 词色标注：英文多词短语（短语动词）同色组 gid 测试。
- _assign_alignment_colors：相同 gid 的片段共享同色（即使不相邻），独立内容片段各自取色；
- PUT /admin/sentence/<id>/alignment 能存回 gid 并在 GET 中保持同色分组。
临时 sqlite。"""
import sys, os, tempfile, json
sys.path.insert(0, 'backend')

TMP_DB = tempfile.mktemp(suffix='.db')
import app as flask_app
flask_app.app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{TMP_DB}'
from models import db
try:
    db.engines.pop(flask_app.app, None)
except Exception:
    pass
from models import User, Course, Sentence

_passed = _failed = 0
def check(name, cond, extra=''):
    global _passed, _failed
    if cond:
        _passed += 1; print(f'  PASS  {name}')
    else:
        _failed += 1; print(f'  FAIL  {name}  {extra}')

# 1) 纯函数：短语动词 throws it up
raw = [
    {'en': 'throws', 'zh': '抛', 'gid': 1},
    {'en': 'it', 'zh': '', 'gid': 0},
    {'en': 'up', 'zh': '起', 'gid': 1},
    {'en': 'in the air', 'zh': '空中', 'gid': 0},
]
out = flask_app._assign_alignment_colors(raw)
check('throws 上了色', out[0]['color'] is not None, out[0])
check('it 黑色（不上色）', out[1]['color'] is None, out[1])
check('up 上了色', out[2]['color'] is not None, out[2])
check('throws 与 up 同色（同 gid）', out[0]['color'] == out[2]['color'], (out[0]['color'], out[2]['color']))
check('in the air 与 throws 不同色', out[3]['color'] != out[0]['color'], (out[3]['color'], out[0]['color']))
check('gid 被原样保存', out[0]['gid'] == 1 and out[2]['gid'] == 1 and out[1]['gid'] == 0, out)

# 2) 连续独立实词各自取色（回归：无 gid 时不应串色）
raw2 = [
    {'en': 'The', 'zh': '', 'gid': 0},
    {'en': 'cat', 'zh': '猫', 'gid': 0},
    {'en': 'barn', 'zh': '谷仓', 'gid': 0},
]
out2 = flask_app._assign_alignment_colors(raw2)
check('无 gid 时各实词取不同色', out2[1]['color'] != out2[2]['color'], out2)

# 3) 端到端：PUT 存 gid，GET 回来仍同色
with flask_app.app.app_context():
    db.drop_all(); db.create_all()
    a = User(username='admin', role='admin'); a.set_password('admin123'); db.session.add(a)
    db.session.commit()
    AID = a.id
    c = Course(title='T'); db.session.add(c); db.session.flush()
    CID = c.id
    s = Sentence(course_id=CID, sentence_order=1,
                 english='He throws it up in the air .', chinese='他把它抛向空中。', alignment={})
    db.session.add(s); db.session.commit()
    SID = s.id

CTX = flask_app.app.app_context(); CTX.push()
client = flask_app.app.test_client()
def login(u):
    r = client.post('/api/v1/auth/login', json={'username': u, 'password': 'admin123'})
    return r.get_json().get('access_token') if r.status_code == 200 else None
H = {'Authorization': 'Bearer ' + login('admin')}

units_payload = [
    {'en': 'He', 'zh': '', 'content': False, 'gid': 0},
    {'en': 'throws', 'zh': '抛', 'content': True, 'gid': 1},
    {'en': 'it', 'zh': '', 'content': False, 'gid': 0},
    {'en': 'up', 'zh': '起', 'content': True, 'gid': 1},
    {'en': 'in the air', 'zh': '空中', 'content': True, 'gid': 0},
    {'en': '.', 'zh': '', 'content': False, 'gid': 0},
]
r = client.put(f'/api/v1/admin/sentence/{SID}/alignment', headers=H, json={'units': units_payload})
check('PUT 返回 200', r.status_code == 200, r.status_code)
saved = r.get_json()['alignment']['units']
c_throws = next(u for u in saved if u['en'] == 'throws')['color']
c_up = next(u for u in saved if u['en'] == 'up')['color']
c_air = next(u for u in saved if u['en'] == 'in the air')['color']
check('PUT 后 throws 与 up 颜色相同', c_throws == c_up, (c_throws, c_up))
check('PUT 后 in the air 颜色不同', c_air != c_throws, (c_air, c_throws))
check('PUT 后 gid 已持久化', next(u for u in saved if u['en'] == 'throws')['gid'] == 1, saved)

# 校验 GET /admin/course/<id>/sentences 返回的 alignment 也保持同色
r2 = client.get(f'/api/v1/admin/course/{CID}/sentences', headers=H)
sent = next(x for x in r2.get_json()['sentences'] if x['id'] == SID)
g_throws = next(u for u in sent['alignment']['units'] if u['en'] == 'throws')['color']
g_up = next(u for u in sent['alignment']['units'] if u['en'] == 'up')['color']
check('GET 句子列表保持 throws/up 同色', g_throws == g_up, (g_throws, g_up))

print(f'\nRESULT: {_passed} passed, {_failed} failed')
sys.exit(1 if _failed else 0)
