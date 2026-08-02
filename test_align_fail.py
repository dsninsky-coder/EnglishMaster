"""v0.8.6 词色标注：后台串行队列 + 真实失败原因透传。
- 端点只入队立即返回（queued=True），不再同步 500；
- worker(_process_align_job) 逐句容错，异常/None 句计入 errors 并记录具体原因；
- deepseek_client._chat(raise_on_error=True) 会把超时/限流/401 等真实异常向上抛。
临时 sqlite + mock。"""
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
import deepseek_client

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
    AID = a.id
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

# 1) 端点只入队，立即返回 queued=True（不再同步执行/500）
r = client.post(f'/api/v1/admin/course/{CID}/align', headers=H)
check('align 端点返回 200', r.status_code == 200, r.status_code)
d = r.get_json()
check('align 返回 queued=True', d.get('queued') is True, d)
check('align 返回 position', isinstance(d.get('position'), int), d)
check('队列里有 1 个任务', flask_app._align_queue.qsize() == 1, flask_app._align_queue.qsize())

# 2) 直接跑 worker 任务（模拟后台串行执行）
flask_app.align_status['last_result'] = None
flask_app.align_status['running'] = False
flask_app._process_align_job({'type': 'course', 'course_id': CID, 'user_id': AID})
res = flask_app.align_status['last_result']
print('  worker result:', res)
check('worker ok=True', res.get('ok') is True, res)
check('done=1（仅正常句写入）', res.get('done') == 1, res)
check('failed=2', res.get('failed') == 2, res)
check('errors 含 2 条', len(res.get('errors', [])) == 2, res)
check('异常句原因含 TimeoutError', any('TimeoutError' in e.get('error','') for e in res['errors']), res)
check('None 句原因含 无法解析', any('无法解析' in e.get('error','') for e in res['errors']), res)
check('errors 各带 order 与 english', all('order' in e and 'english' in e for e in res['errors']), res)

# 3) align-all 同样容错（再入队 + 跑 worker）
flask_app._align_queue = __import__('queue').Queue()
r2 = client.post('/api/v1/admin/align-all', headers=H)
check('align-all 端点返回 queued=True', r2.get_json().get('queued') is True, r2.get_json())
flask_app.align_status['last_result'] = None
flask_app.align_status['running'] = False
flask_app._process_align_job({'type': 'all', 'user_id': AID})
res2 = flask_app.align_status['last_result']
print('  worker-all result:', res2)
check('align-all failed=2', res2.get('failed') == 2, res2)
check('align-all errors 带 course 标题', any('course' in e for e in res2.get('errors', [])), res2)

# 4) _chat(raise_on_error=True) 透传真实异常（mock requests.post 抛超时）
class FakePost:
    def __init__(self, *a, **k): raise __import__('requests').exceptions.Timeout('mock timeout')
orig_post = deepseek_client.requests.post
deepseek_client.requests.post = FakePost
raised = None
try:
    deepseek_client._chat('k', [{'role':'user','content':'x'}], raise_on_error=True)
except Exception as e:
    raised = e
deepseek_client.requests.post = orig_post
check('_chat(raise_on_error=True) 抛出 Timeout', isinstance(raised, type(__import__('requests').exceptions.Timeout())), raised)
# 默认仍吞异常返回 None
deepseek_client.requests.post = FakePost
none_val = deepseek_client._chat('k', [{'role':'user','content':'x'}])
deepseek_client.requests.post = orig_post
check('_chat 默认吞异常返回 None', none_val is None, none_val)

print(f'\nRESULT: {_passed} passed, {_failed} failed')
sys.exit(1 if _failed else 0)
