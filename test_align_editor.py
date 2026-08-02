"""v0.8.3 词色标注人工校对编辑器：GET 句子列表 + PUT 保存 alignment。
临时 sqlite，不影响真实数据库。沿用 test_v08.py 的引擎重绑技巧。
"""
import os, sys, tempfile
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
from app import ALIGN_PALETTE

_passed = 0
_failed = 0
def check(name, cond, extra=''):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f'  PASS  {name}')
    else:
        _failed += 1
        print(f'  FAIL  {name}  {extra}')

with flask_app.app.app_context():
    db.drop_all(); db.create_all()
    a = User(username='admin', role='admin'); a.set_password('admin123'); db.session.add(a)
    s = User(username='stu', role='student'); s.set_password('admin123'); db.session.add(s)
    db.session.commit()
    c = Course(title='T'); db.session.add(c); db.session.flush()
    COURSE_ID = c.id
    db.session.add(Sentence(course_id=COURSE_ID, sentence_order=1,
        english='A rooster sings on the wall .', chinese='一只公鸡在墙上啼叫。', alignment={}))
    db.session.commit()
    SID = Sentence.query.filter_by(course_id=COURSE_ID).first().id

CTX = flask_app.app.app_context(); CTX.push()
client = flask_app.app.test_client()
def login(u):
    r = client.post('/api/v1/auth/login', json={'username': u, 'password': 'admin123'})
    return r.get_json().get('access_token') if r.status_code == 200 else None
def H(tok): return {'Authorization': 'Bearer ' + tok}
admin_tok = login('admin')
stu_tok = login('stu')

# ---------- 1) GET 句子列表（含 alignment 字段与标题） ----------
r = client.get(f'/api/v1/admin/course/{COURSE_ID}/sentences', headers=H(admin_tok))
check('GET sentences 200', r.status_code == 200, r.status_code)
d = r.get_json()
check('GET sentences 含 title', d.get('title') == 'T', d)
check('GET sentences 返回该句', len(d.get('sentences', [])) == 1 and d['sentences'][0]['id'] == SID, d)

# ---------- 2) PUT 人工校对：改写片段 + 上色规则 ----------
new_units = [
    {'en': 'a rooster', 'zh': '一只公鸡', 'content': True},
    {'en': 'sings', 'zh': '啼叫', 'content': True},
    {'en': 'on the wall', 'zh': '在墙上', 'content': True},
    {'en': '.', 'zh': '', 'content': False},
]
r = client.put(f'/api/v1/admin/sentence/{SID}/alignment', headers=H(admin_tok), json={'units': new_units})
check('PUT alignment 200', r.status_code == 200, r.status_code)
saved = r.get_json().get('alignment', {}).get('units', [])
check('content&zh 片段按顺序上色', saved[0]['color'] == ALIGN_PALETTE[0] and saved[1]['color'] == ALIGN_PALETTE[1] and saved[2]['color'] == ALIGN_PALETTE[2], saved)
check('虚词 content=False 黑色', saved[3]['color'] is None, saved)

# ---------- 3) 已写库（学生端序列化可见） ----------
with flask_app.app.app_context():
    s2 = Sentence.query.get(SID)
    check('写库：en 已改', s2.alignment['units'][0]['en'] == 'a rooster', s2.alignment)
    check('写库：虚词无颜色', s2.alignment['units'][3]['color'] is None, s2.alignment)

# ---------- 4) 空 units → 400 ----------
r = client.put(f'/api/v1/admin/sentence/{SID}/alignment', headers=H(admin_tok), json={'units': []})
check('空 units 400', r.status_code == 400, r.status_code)

# ---------- 5) 非管理员 403 ----------
r = client.put(f'/api/v1/admin/sentence/{SID}/alignment', headers=H(stu_tok), json={'units': [{'en': 'x', 'zh': 'y'}]})
check('非管理员 403', r.status_code == 403, r.status_code)

print(f'\nAlignment editor tests: {_passed} passed, {_failed} failed.')
sys.exit(1 if _failed else 0)
