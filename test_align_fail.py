"""v0.8.5 词色标注生成失败容错：单句异常/返回 None 不应令整次请求 500，
失败句须计入 errors 并返回具体原因。临时 sqlite，mock generate_alignment。"""
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

_passed = _failed = 0
def check(name, cond, extra=''):
    global _passed, _failed
    if cond:
        _passed += 1; print(f'  PASS  {name}')
    else:
        _failed += 1; print(f'  FAIL  {name}  {extra}')

with flask_app.app.app_context():
    db.drop_all(); db.create_all()
    a = User(username='admin', role='admin'); a.set_password('admin123'); db.session.add(a)
    db.session.commit()
    c = Course(title='T'); db.session.add(c); db.session.flush()
    CID = c.id
    for i, (en, zh) in enumerate([
        ('A rooster sings .', '一只公鸡啼叫。'),     # 正常
        ('A cat sits near .', '一只猫坐在附近。'),    # 模拟异常
        ('The dog runs .', '狗跑。'),                 # 模拟返回 None
    ], 1):
        db.session.add(Sentence(course_id=CID, sentence_order=i, english=en, chinese=zh, alignment={}))
    db.session.commit()

CTX = flask_app.app.app_context(); CTX.push()
client = flask_app.app.test_client()
def login(u):
    r = client.post('/api/v1/auth/login', json={'username': u, 'password': 'admin123'})
    return r.get_json().get('access_token') if r.status_code == 200 else None
H = {'Authorization': 'Bearer ' + login('admin')}

# mock：key 存在；第 2 句抛异常，第 3 句返回 None，其余正常
flask_app.resolve_api_key = lambda u: 'fake-key'
def fake_generate(english, chinese, user=None):
    if 'cat' in english:
        raise TimeoutError('AI 请求超时 (ReadTimeout)')
    if 'dog' in english:
        return None
    return {'units': [{'en': english, 'pos': 'OTHER', 'content': True, 'color': '#e74c3c', 'zh': chinese}]}
flask_app.generate_alignment = fake_generate

r = client.post(f'/api/v1/admin/course/{CID}/align', headers=H)
check('align 返回 200（不再 500）', r.status_code == 200, r.status_code)
d = r.get_json()
print('  resp:', d)
check('done=1（仅正常句写入）', d.get('done') == 1, d)
check('failed=2', d.get('failed') == 2, d)
check('errors 含 2 条', len(d.get('errors', [])) == 2, d)
check('异常句原因含 TimeoutError', any('TimeoutError' in e.get('error','') for e in d['errors']), d)
check('None 句原因含 无法解析', any('无法解析' in e.get('error','') for e in d['errors']), d)
check('errors 各带 order 与 english', all('order' in e and 'english' in e for e in d['errors']), d)

# align-all 同样容错
r2 = client.post('/api/v1/admin/align-all', headers=H)
d2 = r2.get_json()
print('  resp2:', d2)
check('align-all 返回 200', r2.status_code == 200, r2.status_code)
check('align-all failed=2', d2.get('failed') == 2, d2)
check('align-all errors 带 course 标题', any('course' in e for e in d2.get('errors', [])), d2)

print(f'\nRESULT: {_passed} passed, {_failed} failed')
sys.exit(1 if _failed else 0)
