"""开发期冒烟测试：用 Flask test_client 跑通主链路。
用法（在 backend 目录，依赖已装、数据库已 init 后）：
    python _smoke.py
跑完可删除。
"""
import json
from app import app

client = app.test_client()


def show(name, r):
    try:
        print(f'[{name}] {r.status_code}', r.get_json())
    except Exception:
        print(f'[{name}] {r.status_code}', r.data[:200])


# 学生注册
show('register', client.post('/api/v1/auth/register',
      json={'username': 'smoke_stu', 'password': 'pw123'}))
# 学生登录
r = client.post('/api/v1/auth/login', json={'username': 'smoke_stu', 'password': 'pw123'})
tok = r.get_json()['access_token']
H = {'Authorization': f'Bearer {tok}'}
show('login-stu', r)

show('me', client.get('/api/v1/me', headers=H))
show('checkin', client.post('/api/v1/checkin', headers=H))
show('checkin2', client.post('/api/v1/checkin', headers=H))  # 应提示已签到

# 管理员登录
r = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'admin123'})
atok = r.get_json()['access_token']
AH = {'Authorization': f'Bearer {atok}'}
show('login-admin', r)

sr = client.get('/api/v1/admin/students', headers=AH)
stu_id = sr.get_json()['students'][0]['id']
show('students', sr)

show('assign', client.post('/api/v1/admin/assign-course', headers=AH,
     json={'course_id': 1, 'student_ids': [stu_id]}))
show('courses-stu', client.get('/api/v1/courses', headers=H))
show('sentences', client.get('/api/v1/courses/1/sentences', headers=H))

# Step2 提交（无 key，走本地回退判分）
show('submit2', client.post('/api/v1/step/submit', headers=H,
     json={'sentence_id': 1, 'step': 2, 'user_input': '汤姆是一只猫'}))
show('finish2', client.post('/api/v1/step/finish', headers=H,
     json={'course_id': 1, 'step': 2, 'accuracy': 1.0}))

# 商店 / 许愿 / 报表
show('shop', client.get('/api/v1/shop/items', headers=H))
show('report', client.get(f'/api/v1/reports/student/{stu_id}', headers=H))
show('dbview', client.get('/api/v1/admin/db-view?table=users', headers=AH))

print('\nSMOKE DONE')
