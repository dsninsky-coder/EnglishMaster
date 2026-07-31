"""英语大师 · Flask 后端主程序。

入口：python app.py
- 提供 /api/v1 下的全部 REST 接口
- 生产模式下可直接托管前端 build 产物（frontend/dist）
"""
import os
import csv
import io
import json
import datetime
import re
import random

from flask import Flask, request, jsonify, send_from_directory, render_template, session
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, get_jwt_identity
)
from sqlalchemy import func, distinct

import models
from models import db, User, AdminShareKey, Course, Sentence, CourseAssignment, \
    StudentSentenceProgress, WrongAnswer, CoinTransaction, ShopItem, PurchaseOrder, \
    Wish, WishSupport, SystemSetting, CourseWord, Appeal
from word_data import WordDataManager
import deepseek_client as ds

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'instance', 'english.db')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')           # 课程音频存放根目录
COURSE_UPLOAD_DIR = os.path.join(UPLOAD_DIR, 'courses')
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(COURSE_UPLOAD_DIR, exist_ok=True)

# 各步通过阈值
# 步骤阈值（v0.5：1=沉浸 2=英译中 3=听音写中文 4=跟读 5=中译英 6=续写 7=单词巩固）
STEP_THRESHOLDS = {1: 0.0, 2: 0.85, 3: 0.80, 4: 0.0, 5: 0.80, 6: 0.70, 7: 0.70}
# 评分步骤（发放通关/完美金币）：跟读(4)与沉浸(1)为非评分练习步骤；单词巩固(7)为评分步骤
SCORED_STEPS = {2, 3, 5, 6, 7}

# 系统配置默认值（可在 /admin/settings 后台修改）
DEFAULT_SETTINGS = {
    'checkin_coin': 1,            # 每日签到金币
    'checkin_require_task': True, # 签到前须先完成至少一个学习任务
    'streak_bonus_per_day': 0,    # 连续签到每日奖励（0=关闭）
    'streak_bonus_cap': 10,       # 连续签到奖励封顶天数
    'step_en_hint_words': 3,       # Step5(中译英) 提示：每次随机显示的单词数
    'step_en_hint_changes': 5,     # Step5(中译英) 提示：最多可更换次数（足够多次即可揭示全句）
}


def get_setting(key):
    return SystemSetting.get(key, DEFAULT_SETTINGS.get(key))


def set_setting(key, value):
    SystemSetting.set(key, value)


def get_ai_proxy():
    """全局 AI 代理配置（听说大师判分用）。管理员可在后台设置，兼容任意 OpenAI Chat 接口。"""
    cfg = get_setting('ai_proxy') or {}
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        'base_url': (cfg.get('base_url') or '').strip() or 'https://api.deepseek.com/v1',
        'model': (cfg.get('model') or '').strip() or 'deepseek-chat',
    }


def extract_json(text):
    """从可能含 markdown 栅栏/前言的文本中稳健提取 JSON（对象或数组）。

    处理：① 去除 ```json ... ``` / ``` ... ``` 代码栅栏；
    ② 去除开头的前言文字（定位首个 { 或 [）；
    ③ 解析失败时尝试截断尾部多余字符再解析。
    """
    if not text:
        return None, '内容为空'
    m = re.search(r'```(?:json)?\s*(.*?)```', text, re.S | re.I)
    candidate = m.group(1) if m else text
    candidate = candidate.strip()
    if not candidate:
        return None, '未找到任何内容'
    if candidate[0] not in '[{':
        i = next((k for k, c in enumerate(candidate) if c in '[{'), None)
        if i is None:
            return None, '内容中未包含 JSON（需有 { 或 [）'
        candidate = candidate[i:]
    try:
        return json.loads(candidate), None
    except Exception as e:
        for close in (']}', '}'):
            j = candidate.rfind(close)
            if j > 0:
                try:
                    return json.loads(candidate[:j + 1]), None
                except Exception:
                    pass
        return None, f'JSON 解析失败：{e}'
# 连签里程碑（额外奖励日）
STREAK_MILESTONES = {3, 7, 14, 30}

# 标准 Flask 布局：templates/ 放页面，static/ 放 css/js（自动以 /static/ 提供）
app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'english-master-dev-secret')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'english-master-jwt-secret-key-2026!!')
# 学习可能持续很久，默认 15 分钟过期会导致"学一半提交被踢回登录"。
# 延长到 30 天，配合前端 401 提示，避免"莫名其妙退出登录"。
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = datetime.timedelta(days=30)

CORS(app, resources={r"/api/*": {"origins": "*"}})
jwt = JWTManager(app)
db.init_app(app)


# ---------------- 工具函数 ----------------
def current_user():
    uid = get_jwt_identity()
    return User.query.get(int(uid)) if uid else None


def role_required(role):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            u = current_user()
            if not u:
                return jsonify(error='未登录'), 401
            if u.role != role:
                return jsonify(error='无权限'), 403
            return fn(*args, **kwargs)
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator


def admin_only(fn):
    return jwt_required()(role_required('admin')(fn))


def resolve_api_key(user):
    """API 优先级：分享 Key > 私有 Key > None。"""
    if user.shared_api_key_id:
        sk = AdminShareKey.query.get(user.shared_api_key_id)
        if sk and sk.is_active:
            return sk.api_key_value
    if user.private_api_key:
        return user.private_api_key
    return None


def add_coins(user_id, amount, reason, category=None, operator_id=None):
    u = User.query.get(user_id)
    if not u:
        return
    u.coin_balance = (u.coin_balance or 0) + amount
    db.session.add(CoinTransaction(user_id=user_id, amount=amount, reason=reason,
                                   category=category, operator_id=operator_id))
    db.session.add(u)


def get_progress(student_id, sentence_id, step):
    p = StudentSentenceProgress.query.filter_by(
        student_id=student_id, sentence_id=sentence_id, step=step).first()
    if not p:
        p = StudentSentenceProgress(
            student_id=student_id, sentence_id=sentence_id, step=step, proficiency=0)
        db.session.add(p)
    return p


def record_wrong(student_id, sentence_id, step, user_input, correct_answer, error_type):
    db.session.add(WrongAnswer(
        student_id=student_id, sentence_id=sentence_id, step=step,
        user_input=user_input, correct_answer=correct_answer, error_type=error_type))


# ---------------- 课程单词提取（v0.5 Step7 单词巩固） ----------------
# 英语虚词（功能词）集合：提取单词时去除这些，只保留名词/动词/形容词/副词等实词。
# 覆盖：冠词、代词、介词、连词、助动词、限定词、否定词、疑问词、常见副词小品词等。
EN_STOPWORDS = set("""
a an the and or but if then else when while because since although though however
for to of in on at by with from into onto upon about over under between among across
after before during within without through against along around near off up down out
inside outside beneath below behind beyond above under until till as be am is are
was were been being do does did done have has had having will would shall should can
could may might must ought need dare i me my we us our you your he him his she her
they them their it its this that these those who whom whose which what whatever
whoever each every either neither another any some such both all most other several
many much few little no not nor so than too very just only also even still yet again
once here there where why how
""".split())


def extract_course_words(course):
    """从课程所有句子的英文文本中提取实词（去虚词、去重、小写、按字母序）。

    仅作一次性提取存入 course_words 表；学生测试时直接读表，不实时提取。
    """
    seen = set()
    words = []
    sents = Sentence.query.filter_by(course_id=course.id).order_by(Sentence.sentence_order).all()
    for s in sents:
        toks = re.findall(r"[A-Za-z']+", s.english or '')
        for t in toks:
            tl = t.lower().strip("'")
            if len(tl) <= 1:
                continue
            if tl in EN_STOPWORDS:
                continue
            if tl in seen:
                continue
            seen.add(tl)
            words.append(tl)
    words.sort()
    return words


# ---------------- 鉴权 ----------------
@app.route('/api/v1/auth/register', methods=['POST'])
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or not password:
        return jsonify(error='用户名和密码必填'), 400
    if User.query.filter_by(username=username).first():
        return jsonify(error='用户名已存在'), 409
    u = User(username=username, role='student')
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    return jsonify(message='注册成功'), 201


@app.route('/api/v1/auth/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    u = User.query.filter_by(username=username).first()
    if not u or not u.check_password(password):
        return jsonify(error='用户名或密码错误'), 401
    u.last_active = models.utcnow()
    db.session.commit()
    token = create_access_token(identity=str(u.id))
    # 双写 session：单词大师使用服务端 session 认证，与 JWT(SPA) 共享同一登录态
    session['user'] = username
    session.modified = True
    return jsonify(
        access_token=token,
        user={
            'id': u.id, 'username': u.username, 'role': u.role,
            'coin_balance': u.coin_balance, 'daily_streak': u.daily_streak,
            'has_private_key': bool(u.private_api_key),
            'has_shared_key': bool(u.shared_api_key_id),
        })


@app.route('/api/v1/me', methods=['GET'])
@jwt_required()
def me():
    u = current_user()
    if not u:
        return jsonify(error='未登录'), 401
    return jsonify(user={
        'id': u.id, 'username': u.username, 'role': u.role,
        'coin_balance': u.coin_balance, 'daily_streak': u.daily_streak,
        'last_checkin_date': str(u.last_checkin_date) if u.last_checkin_date else None,
        'total_perfect_steps': u.total_perfect_steps,
        'has_private_key': bool(u.private_api_key),
        'has_shared_key': bool(u.shared_api_key_id),
        'allow_skip': bool(u.allow_skip),
    })


@app.route('/api/v1/user/apikey', methods=['POST'])
@jwt_required()
def set_api_key():
    u = current_user()
    data = request.get_json(silent=True) or {}
    key = (data.get('api_key') or '').strip()
    if not key:
        return jsonify(error='API Key 不能为空'), 400
    u.private_api_key = key
    db.session.commit()
    return jsonify(message='已保存私有 API Key')


@app.route('/api/v1/auth/change-password', methods=['POST'])
@jwt_required()
def change_password():
    u = current_user()
    data = request.get_json(silent=True) or {}
    old_pw = data.get('old_password') or ''
    new_pw = data.get('new_password') or ''
    if not new_pw:
        return jsonify(error='新密码不能为空'), 400
    if not u.check_password(old_pw):
        return jsonify(error='原密码错误'), 400
    u.set_password(new_pw)
    db.session.commit()
    return jsonify(message='密码已修改，请重新登录')


# ---------------- 签到 ----------------
@app.route('/api/v1/checkin', methods=['POST'])
@jwt_required()
def checkin():
    u = current_user()
    today = datetime.date.today()
    if u.last_checkin_date == today:
        return jsonify(message='今日已签到', already=True,
                       coins_gained=0, streak=u.daily_streak,
                       balance=u.coin_balance)
    # 签到需先完成任务（管理员可配置）
    if get_setting('checkin_require_task') and (u.last_task_date is None or u.last_task_date != today):
        return jsonify(error='请先完成至少一个学习任务（任一 Step 提交）后再签到',
                       require_task=True), 400
    yesterday = today - datetime.timedelta(days=1)
    if u.last_checkin_date == yesterday:
        u.daily_streak = (u.daily_streak or 0) + 1
    else:
        u.daily_streak = 1
    u.last_checkin_date = today
    gain = int(get_setting('checkin_coin') or 1)
    # 连续签到奖励（管理员后台配置）
    per_day = int(get_setting('streak_bonus_per_day') or 0)
    cap = int(get_setting('streak_bonus_cap') or 10)
    bonus = 0
    if per_day > 0 and u.daily_streak >= 2:
        bonus = per_day * min(u.daily_streak, cap)
    add_coins(u.id, gain, '每日签到', category='checkin')
    if bonus:
        add_coins(u.id, bonus, f'连续签到第{u.daily_streak}天奖励', category='checkin')
    db.session.commit()
    return jsonify(message='签到成功', already=False,
                   coins_gained=gain + bonus, bonus=bonus,
                   streak=u.daily_streak, balance=u.coin_balance)


@app.route('/api/v1/checkin/info', methods=['GET'])
@jwt_required()
def checkin_info():
    u = current_user()
    today = datetime.date.today()
    return jsonify(
        coin=int(get_setting('checkin_coin') or 1),
        require_task=bool(get_setting('checkin_require_task')),
        streak_bonus_per_day=int(get_setting('streak_bonus_per_day') or 0),
        already=(u.last_checkin_date == today),
        streak=u.daily_streak or 0,
        did_task_today=(u.last_task_date == today),
    )


# ---------------- 课程 ----------------
@app.route('/api/v1/courses', methods=['GET'])
@jwt_required()
def my_courses():
    u = current_user()
    if u.role == 'admin':
        courses = Course.query.order_by(Course.created_at.desc()).all()
        return jsonify(courses=[{'id': c.id, 'title': c.title,
                                 'is_published': c.is_published,
                                 'sentence_count': Sentence.query.filter_by(course_id=c.id).count()}
                                for c in courses])
    assignments = CourseAssignment.query.filter_by(student_id=u.id).all()
    # 收集课程并按 order_index 排序，用于解锁式学习判定
    pairs = []
    for a in assignments:
        c = Course.query.get(a.course_id)
        if not c:
            continue
        pairs.append((a, c))
    pairs.sort(key=lambda ac: (ac[1].order_index or 0, ac[1].id))
    # 第一个未完成课程的下标（解锁式学习中作为"当前进行中"课程）
    first_incomplete = None
    for i, (a, c) in enumerate(pairs):
        if not a.is_completed:
            first_incomplete = i
            break
    out = []
    for i, (a, c) in enumerate(pairs):
        if a.is_completed:
            status = 'review'            # 回顾（已学过）
        else:
            mode = a.unlock_mode or 'free'
            if mode == 'free':
                status = 'start'         # 开始（自由学习，已解锁）
            else:
                status = 'start' if i == first_incomplete else 'locked'  # 未解锁（待解锁）
        if a.appeal_locked:
            status = 'start'   # 附议重锁课程仍可进入重学被锁步骤
        out.append({
            'assignment_id': a.id,
            'course_id': c.id,
            'title': c.title,
            'current_step': a.current_step,
                'step_unlocks': {
                '1': a.step_1_unlocked, '2': a.step_2_unlocked,
                '3': a.step_3_unlocked, '4': a.step_4_unlocked,
                '5': a.step_5_unlocked, '6': a.step_6_unlocked,
                '7': a.step_7_unlocked},
            'completed_steps': a.completed_steps,
            'is_completed': a.is_completed,
            'unlock_mode': a.unlock_mode or 'free',
            'status': status,
            'is_unlocked': status != 'locked',
            'appeal_locked': bool(a.appeal_locked),
            'appeal_lock_step': a.current_step if a.appeal_locked else None,
        })
    return jsonify(courses=out, allow_skip=bool(u.allow_skip))


@app.route('/api/v1/courses/<int:course_id>/sentences', methods=['GET'])
@jwt_required()
def course_sentences(course_id):
    u = current_user()
    course = Course.query.get_or_404(course_id)
    # 人工附议重锁状态（学生端进入课程时用于拦截）
    asm = CourseAssignment.query.filter_by(student_id=u.id, course_id=course_id).first() if u.role == 'student' else None
    appeal_locked = bool(asm and asm.appeal_locked)
    appeal_lock_step = asm.current_step if appeal_locked else None
    # 数据加载：优先返回未掌握(proficiency<80)的句子
    sents = Sentence.query.filter_by(course_id=course_id) \
        .order_by(Sentence.sentence_order).all()
    if u.role == 'student':
        progressed = []
        for s in sents:
            worst = db.session.query(func.min(StudentSentenceProgress.proficiency)) \
                .filter_by(student_id=u.id, sentence_id=s.id).scalar()
            mastered = (worst is not None and worst >= 80)
            progressed.append({'sentence': s, 'mastered': mastered})
        unmastered = [p for p in progressed if not p['mastered']]
        show = unmastered if unmastered else progressed
        all_mastered = len(unmastered) == 0 and len(progressed) > 0
        data = [serialize_sentence(p['sentence']) for p in show]
        return jsonify(course={'id': course.id, 'title': course.title,
                               'full_text': course.full_text},
                       sentences=data,
                       all_mastered=all_mastered,
                       total=len(sents),
                       appeal_locked=appeal_locked, appeal_lock_step=appeal_lock_step,
                       en_hint={'words': get_setting('step_en_hint_words') or 3,
                                'changes': get_setting('step_en_hint_changes') or 5})
    data = [serialize_sentence(s) for s in sents]
    return jsonify(course={'id': course.id, 'title': course.title,
                           'full_text': course.full_text},
                   sentences=data, total=len(sents),
                   en_hint={'words': get_setting('step_en_hint_words') or 3,
                            'changes': get_setting('step_en_hint_changes') or 5})


def serialize_sentence(s):
    return {
        'id': s.id, 'sentence_order': s.sentence_order,
        'english': s.english, 'chinese': s.chinese,
        'audio_url': s.audio_url,
        'target_words': s.target_words or [],
        'svo': s.svo or [],
        'chinese_keywords': s.chinese_keywords or [],
    }


@app.route('/api/v1/courses/<int:course_id>/words', methods=['GET'])
@jwt_required()
def course_words(course_id):
    """返回该课程单词库的乱序单词列表（学生做题时直接读取，不实时提取）。"""
    words = CourseWord.query.filter_by(course_id=course_id).all()
    wlist = [w.word for w in words]
    random.shuffle(wlist)
    return jsonify(words=wlist, total=len(wlist))


@app.route('/api/v1/step/word-judge', methods=['POST'])
@jwt_required()
def word_judge():
    """Step7 单词巩固：用 AI 判断学生中文翻译是否正确。

    返回 {correct: bool, reason: str}：
    - 正确：correct=true，reason 为空；
    - 错误：correct=false，reason 为 AI 用一句话简要说明的错误原因（前端在解析界面展示）。
    未配置 AI 时 correct=null 并给出提示（前端不计入正确率、引导配置）。
    """
    u = current_user()
    data = request.get_json(silent=True) or {}
    word = (data.get('word') or '').strip()
    answer = (data.get('answer') or '').strip()
    if not word or not answer:
        return jsonify(correct=False, reason='请填写单词与你的翻译')
    key = resolve_api_key(u)
    proxy = get_ai_proxy()
    if not key:
        return jsonify(correct=None,
                       reason='未配置 AI 模型（请在管理员设置中填写 API Key 与模型），暂时无法自动判分')
    messages = [
        {"role": "system", "content": (
            "你是严谨的英语老师。给定一个英文单词和学生的中文翻译，判断翻译是否正确。"
            "如果正确，reason 填空字符串；如果错误，用一句简短中文（不超过30字）点明主要问题，语气温和。"
            "只输出 JSON：{\"correct\": true/false, \"reason\": \"...\"}。"
        )},
        {"role": "user", "content": json.dumps(
            {"word": word, "student_translation": answer}, ensure_ascii=False)},
    ]
    content = ds._chat(key, messages, base_url=proxy['base_url'], model=proxy['model'])
    if not content:
        return jsonify(correct=None, reason='AI 服务暂时不可用，请稍后再试')
    try:
        obj, err = extract_json(content)
        if obj is None:
            return jsonify(correct=None, reason='AI 返回无法解析')
        correct = bool(obj.get('correct', False))
        reason = (obj.get('reason') or '').strip()
        # 判错：自动加入该生词表（与单词大师打通，每 10 词一个 list）
        added_error = False
        if correct is False:
            try:
                added_error = WordDataManager().add_error_word(u.username, word)
            except Exception:
                added_error = False
        return jsonify(correct=correct, reason=reason, added_error=added_error)
    except Exception:
        return jsonify(correct=None, reason='AI 返回解析失败')


# ---------------- 闯关提交 ----------------
@app.route('/api/v1/step/submit', methods=['POST'])
@jwt_required()
def step_submit():
    u = current_user()
    u.last_task_date = datetime.date.today()   # 标记今日已完成学习任务
    data = request.get_json(silent=True) or {}
    sentence_id = data.get('sentence_id')
    step = int(data.get('step', 0))
    user_input = (data.get('user_input') or '').strip()
    if step not in (1, 2, 3, 4, 5, 6):
        return jsonify(error='非法 step'), 400
    s = Sentence.query.get_or_404(sentence_id)
    key = resolve_api_key(u)
    proxy = get_ai_proxy()
    result = {'correct': False, 'similarity': None,
              'error_type': None, 'standard_answer': None, 'proficiency': 0}

    if step == 1:
        # 沉浸输入无评分
        p = get_progress(u.id, sentence_id, 2)
        p.last_reviewed = models.utcnow()
        db.session.commit()
        result['correct'] = True
        result['standard_answer'] = s.english
        return jsonify(**result)

    if step == 4:
        # 跟读（Step4）为纯听读练习，不评分、无需提交
        return jsonify(error='跟读步骤无需提交'), 400

    # 主动跳过：不评分/不调用 AI，直接判为未通过、记入错题（纳入下一轮复习），并返回标准答案
    if bool(data.get('skipped')):
        if step == 6:
            nxt = Sentence.query.filter_by(course_id=s.course_id) \
                .filter(Sentence.sentence_order == s.sentence_order + 1).first()
            if not nxt:
                return jsonify(error='该句无下一句'), 400
            std = nxt.english
        elif step in (2, 3):
            std = s.chinese
        else:
            std = s.english
        record_wrong(u.id, s.id, step, user_input or '(跳过)', std, '主动跳过')
        p = get_progress(u.id, s.id, step)
        p.proficiency = max(0, (p.proficiency or 0) - 5)
        p.last_reviewed = models.utcnow()
        db.session.commit()
        result.update(correct=False, standard_answer=std,
                      error_type='主动跳过', skipped=True, proficiency=p.proficiency)
        return jsonify(**result)

    if step == 2:
        # 英译中：本地字符相似度优先；不达标再交给 AI 按完整度+准确度打分
        local_sim = ds.local_similarity(user_input, s.chinese)
        if local_sim >= 0.70:
            correct = True
            sim = local_sim
            method = 'local'
        else:
            ai = ds.ai_score_chinese(key, user_input, s.chinese,
                                     base_url=proxy['base_url'], model=proxy['model'])
            sim = ai if ai is not None else local_sim
            correct = sim >= 0.75
            method = 'ai' if ai is not None else 'local'
        result.update(correct=correct, similarity=round(sim, 3),
                      standard_answer=s.chinese, method=method)
        _apply_step_result(u, s, step, correct, user_input, s.chinese, key, result,
                          base_url=proxy['base_url'], model=proxy['model'])

    elif step == 3:
        sim = ds.score_similarity(key, user_input, s.chinese,
                                 base_url=proxy['base_url'], model=proxy['model'])
        correct = sim >= 0.80
        result.update(correct=correct, similarity=round(sim, 3),
                      standard_answer=s.chinese)
        _apply_step_result(u, s, step, correct, user_input, s.chinese, key, result,
                          base_url=proxy['base_url'], model=proxy['model'])

    elif step == 5:
        # 中译英：本地英文单词对比优先；不通过再交给 DeepSeek 按完整度+准确度打分
        passed, matched, total = ds.local_english_match(user_input, s.english, s.target_words)
        ai = None
        if passed:
            correct = True
            method = 'local'
        else:
            ai = ds.ai_score_english(key, user_input, s.english, task='en',
                                    base_url=proxy['base_url'], model=proxy['model'])
            if ai is None:
                correct = False
                method = 'local'
            else:
                correct = ai >= 0.80
                method = 'ai'
        result.update(correct=correct, standard_answer=s.english, method=method,
                      local_match=f'{matched}/{total}',
                      similarity=(round(ai, 3) if (method == 'ai' and ai is not None) else None))
        _apply_step_result(u, s, step, correct, user_input, s.english, key, result,
                          base_url=proxy['base_url'], model=proxy['model'])

    elif step == 6:
        # 延展叙述：本地"含核心词+长度≤20"优先；否则交给 DeepSeek 评连贯+完整准确
        nxt = Sentence.query.filter_by(course_id=s.course_id) \
            .filter(Sentence.sentence_order == s.sentence_order + 1).first()
        if not nxt:
            return jsonify(error='该句无下一句'), 400
        words = re.findall(r"[A-Za-z']+", user_input or '')
        local_pass = (len(words) <= 20) and \
            ds.local_english_match(user_input, nxt.english, nxt.target_words)[0]
        if local_pass:
            correct = True
            method = 'local'
            sim = 0.0
        else:
            ai = ds.ai_score_english(key, user_input, nxt.english, task='cont',
                                    base_url=proxy['base_url'], model=proxy['model'])
            if ai is None:
                correct = False
                method = 'local'
                sim = 0.0
            else:
                correct = ai >= 0.70
                method = 'ai'
                sim = ai
        result.update(correct=correct, similarity=round(sim, 3),
                      standard_answer=nxt.english, method=method)
        _apply_step_result(u, s, step, correct, user_input, nxt.english, key, result,
                          base_url=proxy['base_url'], model=proxy['model'])

    return jsonify(**result)


def _apply_step_result(u, s, step, correct, user_input, correct_answer, key, result,
                       base_url=None, model=None):
    p = get_progress(u.id, s.id, step)
    if correct:
        p.proficiency = min(100, (p.proficiency or 0) + 10)
    else:
        p.proficiency = max(0, (p.proficiency or 0) - 5)
    p.last_reviewed = models.utcnow()
    p.is_mastered = p.proficiency >= 80
    if not correct:
        err = ds.analyze_error(key, user_input, correct_answer, step,
                              base_url=base_url, model=model)
        record_wrong(u.id, s.id, step, user_input, correct_answer, err)
        result['error_type'] = err
    db.session.commit()
    result['proficiency'] = p.proficiency


@app.route('/api/v1/step/finish', methods=['POST'])
@jwt_required()
def step_finish():
    u = current_user()
    data = request.get_json(silent=True) or {}
    course_id = data.get('course_id')
    step = int(data.get('step', 0))
    accuracy = float(data.get('accuracy', 0.0))
    perfect = bool(data.get('perfect', accuracy >= 1.0))
    a = CourseAssignment.query.filter_by(student_id=u.id, course_id=course_id).first()
    if not a:
        return jsonify(error='未分配该课程'), 404
    thr = STEP_THRESHOLDS.get(step, 1.0)
    passed = accuracy >= thr
    # 强制解锁：学生开启 allow_skip 后，一轮结束仍有未通过句也可强制解锁下一步（不发金币奖励）
    forced = bool(data.get('force')) and bool(u.allow_skip) and not passed
    if not passed and not forced:
        return jsonify(passed=False, message='正确率未达标，继续练习吧',
                       threshold=thr)
    unlocked_next = False
    awards = []
    if step not in (a.completed_steps or []):
        a.completed_steps = (a.completed_steps or []) + [step]
        # 该步是否存在待审附议：存在则先暂扣本步奖励，待管理员裁决后补发
        has_pending_appeal = Appeal.query.filter_by(
            student_id=u.id, course_id=course_id, step=step, status='pending').first() is not None
        # 仅评分步骤（SCORED_STEPS）发放通关/完美奖励；跟读(4)与沉浸(1)为非评分练习；强制解锁不发奖励
        if step in SCORED_STEPS and not forced:
            if has_pending_appeal:
                supp = list(a.appeal_suppressed or [])
                if step not in supp:
                    supp.append(step)
                a.appeal_suppressed = supp
                perf_map = dict(a.appeal_suppressed_perfect or {})
                perf_map[str(step)] = bool(perfect)
                a.appeal_suppressed_perfect = perf_map
                awards.append('附议待审·奖励暂扣')
            else:
                add_coins(u.id, 1, f'Step{step}通关奖励', category='study')
                awards.append('Step通关 +1')
                # 首次完美：必须一次性全对（无重做）才发放奖励
                if perfect and step not in (a.perfect_steps or []):
                    a.perfect_steps = (a.perfect_steps or []) + [step]
                    u.total_perfect_steps = (u.total_perfect_steps or 0) + 1
                    add_coins(u.id, 3, f'Step{step}完美通关奖励', category='study')
                    awards.append('完美通关 +3')
        # 若该步已无待审附议，解除课程重锁（重学通过）
        if a.appeal_locked and not Appeal.query.filter_by(
                student_id=u.id, course_id=course_id, step=step, status='pending').first():
            a.appeal_locked = False
        # 解锁下一步
        if step < 7:
            setattr(a, f'step_{step+1}_unlocked', True)
            a.current_step = max(a.current_step or 1, step + 1)
            unlocked_next = True
        # 全部通关：标记完成（不再发"课程全通"额外奖励，每个步骤已奖励过）
        if set(a.completed_steps or []) >= {2, 3, 5, 6, 7}:
            a.is_completed = True
    db.session.commit()
    return jsonify(passed=True, forced=forced, unlocked_next=unlocked_next,
                   awards=awards, balance=u.coin_balance,
                   completed_steps=a.completed_steps)


# ---------------- 人工附议（学生申请 / 管理员裁决） ----------------
APPEAL_COST = 2   # 每次人工附议消耗金币


@app.route('/api/v1/step/appeal', methods=['POST'])
@jwt_required()
def step_appeal():
    """学生对系统判错的题目申请人工附议（花费 2 金币）。

    题目暂记为"默认通过"以便继续；奖励在 step_finish 时若该步存在待审附议则暂扣，
    待管理员裁决后再补发（通过）或永久扣留（驳回）。
    """
    u = current_user()
    data = request.get_json(silent=True) or {}
    sentence_id = data.get('sentence_id')
    step = int(data.get('step', 0))
    user_input = (data.get('user_input') or '').strip()
    standard_answer = (data.get('standard_answer') or '').strip()
    if step not in (2, 3, 5, 6, 7):
        return jsonify(error='该步骤不支持人工附议'), 400
    s = Sentence.query.get(sentence_id) if sentence_id else None
    course_id = s.course_id if s else data.get('course_id')
    if not course_id:
        return jsonify(error='缺少课程信息'), 400
    # 防重复：同一题目同一学生同一待审附议不再扣费
    dup = Appeal.query.filter_by(student_id=u.id, course_id=course_id, step=step, status='pending')
    dup = dup.filter_by(sentence_id=sentence_id) if sentence_id else dup.filter(Appeal.sentence_id.is_(None))
    if dup.first():
        return jsonify(error='该题目已申请附议，等待审核中', already=True)
    if (u.coin_balance or 0) < APPEAL_COST:
        return jsonify(error=f'金币不足，无法申请人工附议（需 {APPEAL_COST} 金币）'), 400
    add_coins(u.id, -APPEAL_COST, f'申请人工附议（Step{step}）', category='appeal')
    db.session.add(Appeal(student_id=u.id, course_id=course_id, step=step,
                          sentence_id=sentence_id, student_answer=user_input,
                          standard_answer=standard_answer, status='pending'))
    db.session.commit()
    return jsonify(ok=True, cost=APPEAL_COST, balance=u.coin_balance,
                   message=f'已申请人工附议，扣除 {APPEAL_COST} 金币，等待管理员审核')


@app.route('/api/v1/admin/appeals', methods=['GET'])
@admin_only
def admin_appeals():
    status = request.args.get('status', 'pending')
    q = Appeal.query
    if status and status != 'all':
        q = q.filter_by(status=status)
    rows = q.order_by(Appeal.created_at.desc()).all()
    out = []
    for a in rows:
        stu = User.query.get(a.student_id)
        course = Course.query.get(a.course_id)
        s = Sentence.query.get(a.sentence_id) if a.sentence_id else None
        out.append({
            'id': a.id, 'student': stu.username if stu else '?',
            'student_id': a.student_id, 'course': course.title if course else '?',
            'course_id': a.course_id, 'step': a.step,
            'sentence_en': s.english if s else (a.standard_answer or ''),
            'sentence_cn': s.chinese if s else '',
            'student_answer': a.student_answer, 'standard_answer': a.standard_answer,
            'status': a.status, 'admin_note': a.admin_note,
            'created_at': str(a.created_at),
        })
    return jsonify(appeals=out, total=len(out))


@app.route('/api/v1/admin/appeals/pending-count', methods=['GET'])
@admin_only
def admin_appeals_pending_count():
    return jsonify(count=Appeal.query.filter_by(status='pending').count())


@app.route('/api/v1/admin/appeal/<int:appeal_id>/resolve', methods=['POST'])
@admin_only
def admin_appeal_resolve(appeal_id):
    u = current_user()
    a = Appeal.query.get_or_404(appeal_id)
    if a.status != 'pending':
        return jsonify(error='该附议已处理'), 400
    data = request.get_json(silent=True) or {}
    decision = data.get('decision')
    note = (data.get('note') or '').strip()
    if decision not in ('approved', 'rejected'):
        return jsonify(error='decision 必须是 approved / rejected'), 400
    a.status = decision
    a.admin_id = u.id
    a.admin_note = note
    a.resolved_at = models.utcnow()
    stu = User.query.get(a.student_id)

    refund_amt = 0
    bonus_amt = 0
    if decision == 'approved':
        # 学生没错：返还 2 金币 + 补发该步被暂扣奖励 + 标记该句掌握
        add_coins(a.student_id, APPEAL_COST, '人工附议通过·返还金币', category='appeal', operator_id=u.id)
        refund_amt = APPEAL_COST
        if a.sentence_id:
            p = get_progress(a.student_id, a.sentence_id, a.step)
            p.proficiency = 100
            p.is_mastered = True
            p.last_reviewed = models.utcnow()
        asm = CourseAssignment.query.filter_by(student_id=a.student_id, course_id=a.course_id).first()
        if asm:
            supp = list(asm.appeal_suppressed or [])
            perf_map = dict(asm.appeal_suppressed_perfect or {})
            if a.step in supp:
                add_coins(a.student_id, 1, f'Step{a.step}通关奖励（附议通过补发）', category='study', operator_id=u.id)
                bonus_amt += 1
                if perf_map.get(str(a.step)) and a.step not in (asm.perfect_steps or []):
                    asm.perfect_steps = (asm.perfect_steps or []) + [a.step]
                    stu.total_perfect_steps = (stu.total_perfect_steps or 0) + 1
                    add_coins(a.student_id, 3, f'Step{a.step}完美通关奖励（附议通过补发）', category='study', operator_id=u.id)
                    bonus_amt += 3
                supp = [x for x in supp if x != a.step]
                asm.appeal_suppressed = supp
                perf_map.pop(str(a.step), None)
                asm.appeal_suppressed_perfect = perf_map
            # 此步已无待审附议：解除课程重锁（若因本步驳回而上锁）
            if asm.appeal_locked and not Appeal.query.filter_by(
                    student_id=a.student_id, course_id=a.course_id, step=a.step, status='pending').first():
                asm.appeal_locked = False
    else:
        # 学生错：2 金币已扣（申请时），课程重新上锁——仅该错误步需重学
        asm = CourseAssignment.query.filter_by(student_id=a.student_id, course_id=a.course_id).first()
        if asm:
            cs = list(asm.completed_steps or [])
            if a.step in cs:
                cs = [x for x in cs if x != a.step]
                asm.completed_steps = cs
            ps = list(asm.perfect_steps or [])
            if a.step in ps:
                ps = [x for x in ps if x != a.step]
                asm.perfect_steps = ps
            asm.is_completed = False
            asm.current_step = a.step
            asm.appeal_locked = True
            for k in range(a.step + 1, 8):
                setattr(asm, f'step_{k}_unlocked', False)
            supp = list(asm.appeal_suppressed or [])
            if a.step in supp:
                supp = [x for x in supp if x != a.step]
                asm.appeal_suppressed = supp
            perf_map = dict(asm.appeal_suppressed_perfect or {})
            perf_map.pop(str(a.step), None)
            asm.appeal_suppressed_perfect = perf_map
    db.session.commit()
    return jsonify(ok=True, decision=decision, refund=refund_amt, bonus=bonus_amt,
                   balance=stu.coin_balance)


@app.route('/api/v1/admin/extract-all-course-words', methods=['POST'])
@admin_only
def admin_extract_all_words():
    """一键提取所有课程的单词（保留管理员手动添加的词，仅替换自动提取部分）。"""
    courses = Course.query.order_by(Course.id).all()
    total_words = 0
    detail = []
    for c in courses:
        CourseWord.query.filter_by(course_id=c.id, is_custom=False).delete()
        words = extract_course_words(c)
        for w in words:
            db.session.add(CourseWord(course_id=c.id, word=w, is_custom=False))
        total_words += len(words)
        detail.append({'id': c.id, 'title': c.title, 'words': len(words)})
    db.session.commit()
    return jsonify(ok=True, courses=len(courses), total_words=total_words, detail=detail)


# ---------------- 全局闪电复习 (SRS) ----------------
@app.route('/api/v1/review/flashcards', methods=['GET'])
@jwt_required()
def review_flashcards():
    u = current_user()
    wrong = WrongAnswer.query.filter_by(student_id=u.id) \
        .order_by(func.random()).limit(5).all()
    out = []
    for w in wrong:
        s = Sentence.query.get(w.sentence_id)
        if not s:
            continue
        mode = 'zh2en' if w.step in (5, 6) else 'en2zh'
        out.append({
            'wrong_id': w.id, 'sentence_id': w.sentence_id, 'step': w.step,
            'mode': mode, 'english': s.english, 'chinese': s.chinese,
            'audio_url': s.audio_url, 'svo': s.svo or [],
        })
    return jsonify(cards=out)


@app.route('/api/v1/review/submit', methods=['POST'])
@jwt_required()
def review_submit():
    u = current_user()
    u.last_task_date = datetime.date.today()   # 标记今日已完成学习任务
    data = request.get_json(silent=True) or {}
    wrong_id = data.get('wrong_id')
    step = int(data.get('step', 0))
    user_input = (data.get('user_input') or '').strip()
    w = WrongAnswer.query.get_or_404(wrong_id)
    s = Sentence.query.get(w.sentence_id)
    key = resolve_api_key(u)
    proxy = get_ai_proxy()
    if step in (5, 6):
        correct = ds.check_svo(user_input, s.svo) if step == 5 else True
        if step == 6:
            correct = True  # 复习模式宽松判通过
    else:
        sim = ds.score_similarity(key, user_input, s.chinese,
                                 base_url=proxy['base_url'], model=proxy['model'])
        correct = sim >= (0.80 if step == 3 else 0.75)
    p = get_progress(u.id, s.id, step)
    if correct:
        p.proficiency = min(100, (p.proficiency or 0) + 5)
        p.is_mastered = p.proficiency >= 80
        add_coins(u.id, 5, '错题复习通过', category='study')
        db.session.delete(w)
    else:
        p.proficiency = 0
        p.is_mastered = False
    p.last_reviewed = models.utcnow()
    db.session.commit()
    return jsonify(correct=correct, proficiency=p.proficiency, balance=u.coin_balance)


# ---------------- 商店 ----------------
@app.route('/api/v1/shop/items', methods=['GET'])
@jwt_required()
def shop_items():
    items = ShopItem.query.filter_by(is_on_shelf=True).all()
    return jsonify(items=[{
        'id': i.id, 'name': i.name, 'description': i.description,
        'price_coins': i.price_coins, 'stock': i.stock} for i in items])


@app.route('/api/v1/shop/buy', methods=['POST'])
@jwt_required()
def shop_buy():
    u = current_user()
    data = request.get_json(silent=True) or {}
    item_id = data.get('item_id')
    item = ShopItem.query.get_or_404(item_id)
    if not item.is_on_shelf:
        return jsonify(error='商品已下架'), 400
    if item.stock == 0:
        return jsonify(error='库存不足'), 400
    if (u.coin_balance or 0) < item.price_coins:
        return jsonify(error='金币不足'), 400
    add_coins(u.id, -item.price_coins, f'购买商品:{item.name}', category='shop')
    if item.stock > 0:
        item.stock -= 1
    db.session.add(PurchaseOrder(student_id=u.id, item_id=item.id, status='pending'))
    db.session.commit()
    return jsonify(message='购买成功，等待发货', balance=u.coin_balance)


# ---------------- 许愿池 ----------------
@app.route('/api/v1/wish/create', methods=['POST'])
@jwt_required()
def wish_create():
    u = current_user()
    data = request.get_json(silent=True) or {}
    content = (data.get('content') or '').strip()
    coins = int(data.get('coins', 0))
    if not content:
        return jsonify(error='愿望内容必填'), 400
    if coins < 10:
        return jsonify(error='至少投入 10 金币'), 400
    if (u.coin_balance or 0) < coins:
        return jsonify(error='金币不足'), 400
    add_coins(u.id, -coins, '许愿投入', category='wish')
    w = Wish(student_id=u.id, content=content, total_coins_invested=coins, status='pending')
    db.session.add(w)
    db.session.commit()
    return jsonify(message='愿望已发布，等待审核', wish_id=w.id)


@app.route('/api/v1/wish/support', methods=['POST'])
@jwt_required()
def wish_support():
    u = current_user()
    data = request.get_json(silent=True) or {}
    wish_id = data.get('wish_id')
    coins = int(data.get('coins', 0))
    w = Wish.query.get_or_404(wish_id)
    if w.status != 'pending':
        return jsonify(error='该愿望不在审核中'), 400
    if coins <= 0:
        return jsonify(error='助力金币需大于0'), 400
    if (u.coin_balance or 0) < coins:
        return jsonify(error='金币不足'), 400
    add_coins(u.id, -coins, f'助力愿望#{wish_id}', category='support')
    w.total_coins_invested = (w.total_coins_invested or 0) + coins
    db.session.add(WishSupport(wish_id=wish_id, supporter_id=u.id, coins=coins))
    db.session.commit()
    return jsonify(message='助力成功', total=w.total_coins_invested, balance=u.coin_balance)


@app.route('/api/v1/wishes/public', methods=['GET'])
@jwt_required()
def wishes_public():
    # 许愿池：展示审核中(pending)与已批准(approved)的愿望，任何人可见并可助力
    wishes = Wish.query.filter(Wish.status.in_(['pending', 'approved'])) \
        .order_by(Wish.created_at.desc()).all()
    out = []
    for w in wishes:
        stu = User.query.get(w.student_id)
        out.append({
            'id': w.id, 'student': stu.username if stu else '?',
            'content': w.content, 'status': w.status,
            'total_coins_invested': w.total_coins_invested,
            'supporters': WishSupport.query.filter_by(wish_id=w.id).count(),
        })
    return jsonify(wishes=out)


@app.route('/api/v1/wish/<int:wish_id>', methods=['GET'])
@jwt_required()
def wish_detail(wish_id):
    w = Wish.query.get_or_404(wish_id)
    stu = User.query.get(w.student_id)
    supports = WishSupport.query.filter_by(wish_id=wish_id).all()
    return jsonify(wish={
        'id': w.id, 'student': stu.username if stu else '?',
        'content': w.content, 'total': w.total_coins_invested,
        'status': w.status, 'admin_reply': w.admin_reply,
        'supports': [{'supporter_id': s.supporter_id, 'coins': s.coins} for s in supports],
    })


@app.route('/api/v1/wishes', methods=['GET'])
@jwt_required()
def list_wishes():
    u = current_user()
    if u.role == 'admin':
        wishes = Wish.query.order_by(Wish.created_at.desc()).all()
    else:
        wishes = Wish.query.filter_by(student_id=u.id).all()
    out = []
    for w in wishes:
        supports = WishSupport.query.filter_by(wish_id=w.id).all()
        out.append({
            'id': w.id, 'content': w.content,
            'total_coins_invested': w.total_coins_invested,
            'status': w.status, 'admin_reply': w.admin_reply,
            'supporters': len(supports),
            'supports': [{'supporter_id': sp.supporter_id, 'coins': sp.coins}
                         for sp in supports],
            'created_at': str(w.created_at),
        })
    return jsonify(wishes=out)


# ---------------- 报表 ----------------
@app.route('/api/v1/reports/student/<int:student_id>', methods=['GET'])
@jwt_required()
def student_report(student_id):
    u = current_user()
    if u.role != 'admin' and u.id != student_id:
        return jsonify(error='无权限'), 403
    assignments = CourseAssignment.query.filter_by(student_id=student_id).all()
    assigned = len(assignments)
    completed = sum(1 for a in assignments if a.is_completed)

    # 学习天数
    study_days = db.session.query(
        distinct(func.date(CoinTransaction.created_at))) \
        .filter_by(user_id=student_id).count()

    # 各 Step 正确率（按课程）
    step_accuracy = []
    for a in assignments:
        c = Course.query.get(a.course_id)
        if not c:
            continue
        sents = Sentence.query.filter_by(course_id=c.id).all()
        per_step = {}
        for st in (2, 3, 4, 5, 6):
            vals = []
            for s in sents:
                prof = db.session.query(StudentSentenceProgress.proficiency) \
                    .filter_by(student_id=student_id, sentence_id=s.id, step=st).scalar()
                if prof is not None:
                    vals.append(prof / 100.0)
            per_step[str(st)] = round(sum(vals) / len(vals), 3) if vals else 0.0
        step_accuracy.append({'course': c.title, **per_step})

    # 错题高频 Top10（按句聚合）
    wrong_rows = db.session.query(
        WrongAnswer.sentence_id, func.count(WrongAnswer.id)) \
        .filter_by(student_id=student_id).group_by(WrongAnswer.sentence_id) \
        .order_by(func.count(WrongAnswer.id).desc()).limit(10).all()
    wrong_top = []
    for sid, cnt in wrong_rows:
        s = Sentence.query.get(sid)
        if s:
            wrong_top.append({'sentence_id': sid, 'english': s.english,
                              'chinese': s.chinese, 'count': cnt,
                              'words': s.target_words or []})

    # 活跃日历（按日期聚合提交次数）
    cal = {}
    for tbl in (WrongAnswer, StudentSentenceProgress):
        rows = db.session.query(func.date(tbl.created_at if hasattr(tbl, 'created_at')
                                          else tbl.last_reviewed)) \
            .filter_by(student_id=student_id).all()
        for (d,) in rows:
            if d:
                cal[d] = cal.get(d, 0) + 1

    user = User.query.get(student_id)
    return jsonify(report={
        'overview': {
            'assigned_count': assigned, 'completed_count': completed,
            'total_study_days': study_days,
            'total_coins': user.coin_balance if user else 0,
            'perfect_steps': user.total_perfect_steps if user else 0,
        },
        'step_accuracy': step_accuracy,
        'wrong_top10': wrong_top,
        'calendar': cal,
    })


# ================= 管理员接口 =================

@app.route('/api/v1/admin/share-key', methods=['POST'])
@admin_only
def create_share_key():
    u = current_user()
    data = request.get_json(silent=True) or {}
    val = (data.get('api_key_value') or '').strip()
    if not val:
        return jsonify(error='Key 不能为空'), 400
    sk = AdminShareKey(admin_id=u.id, api_key_value=val, is_active=True)
    db.session.add(sk)
    db.session.commit()
    return jsonify(message='已创建分享 Key', id=sk.id)


@app.route('/api/v1/admin/share-keys', methods=['GET'])
@admin_only
def list_share_keys():
    keys = AdminShareKey.query.all()
    return jsonify(keys=[{'id': k.id, 'admin_id': k.admin_id,
                          'is_active': k.is_active,
                          'masked': (k.api_key_value[:6] + '...' + k.api_key_value[-4:])
                          if len(k.api_key_value) > 10 else '****'} for k in keys])


@app.route('/api/v1/admin/set-share', methods=['POST'])
@admin_only
def set_share():
    data = request.get_json(silent=True) or {}
    student_id = data.get('student_id')
    share_key_id = data.get('share_key_id')  # null 表示取消
    stu = User.query.get_or_404(student_id)
    if share_key_id:
        sk = AdminShareKey.query.get_or_404(share_key_id)
        stu.shared_api_key_id = sk.id
    else:
        stu.shared_api_key_id = None
    db.session.commit()
    return jsonify(message='已更新分享设置',
                   has_shared_key=bool(stu.shared_api_key_id))


@app.route('/api/v1/admin/ai-proxy', methods=['GET'])
@admin_only
def get_ai_proxy_config():
    p = get_ai_proxy()
    return jsonify(base_url=p['base_url'], model=p['model'])


@app.route('/api/v1/admin/ai-proxy', methods=['POST'])
@admin_only
def save_ai_proxy_config():
    data = request.get_json(silent=True) or {}
    base_url = (data.get('base_url') or '').strip()
    model = (data.get('model') or '').strip()
    if not base_url:
        return jsonify(error='Base URL 不能为空'), 400
    if not model:
        return jsonify(error='模型名不能为空'), 400
    # 兼容用户可能把 chat/completions 后缀一起填进来
    if base_url.rstrip('/').endswith('/chat/completions'):
        base_url = base_url.rstrip('/')[: -len('/chat/completions')]
    set_setting('ai_proxy', {'base_url': base_url, 'model': model})
    return jsonify(message='AI 代理已保存', base_url=base_url, model=model)


@app.route('/api/v1/admin/students', methods=['GET'])
@admin_only
def admin_students():
    students = User.query.filter_by(role='student').order_by(User.id).all()
    out = []
    for s in students:
        out.append({
            'id': s.id, 'username': s.username, 'coin_balance': s.coin_balance,
            'daily_streak': s.daily_streak,
            'last_active': str(s.last_active), 'has_shared_key': bool(s.shared_api_key_id),
            'allow_skip': bool(s.allow_skip),
        })
    return jsonify(students=out)


@app.route('/api/v1/admin/set-allow-skip', methods=['POST'])
@admin_only
def admin_set_allow_skip():
    data = request.get_json(silent=True) or {}
    student_id = data.get('student_id')
    allow = bool(data.get('allow_skip'))
    stu = User.query.get_or_404(student_id)
    stu.allow_skip = allow
    db.session.commit()
    return jsonify(message='已' + ('开启' if allow else '关闭') + '允许跳过', allow_skip=allow)


@app.route('/api/v1/admin/reset-password', methods=['POST'])
@admin_only
def admin_reset_pw():
    data = request.get_json(silent=True) or {}
    student_id = data.get('student_id')
    new_pw = data.get('new_password') or ''
    if not new_pw:
        return jsonify(error='新密码必填'), 400
    stu = User.query.get_or_404(student_id)
    stu.set_password(new_pw)
    db.session.commit()
    return jsonify(message='密码已重置')


@app.route('/api/v1/admin/adjust-coins', methods=['POST'])
@admin_only
def admin_adjust():
    u = current_user()
    data = request.get_json(silent=True) or {}
    student_id = data.get('student_id')
    amount = int(data.get('amount', 0))
    reason = (data.get('reason') or '管理员调整').strip()
    if amount == 0:
        return jsonify(error='金额不能为0'), 400
    stu = User.query.get_or_404(student_id)
    cat = 'reward' if amount > 0 else 'penalty'
    add_coins(student_id, amount, f'管理员调整:{reason}', category=cat, operator_id=u.id)
    db.session.commit()
    return jsonify(message='已调整', balance=stu.coin_balance)


@app.route('/api/v1/admin/settings', methods=['GET'])
@admin_only
def admin_get_settings():
    return jsonify(settings={k: get_setting(k) for k in DEFAULT_SETTINGS})


@app.route('/api/v1/admin/settings', methods=['POST'])
@admin_only
def admin_save_settings():
    data = request.get_json(silent=True) or {}
    for k in DEFAULT_SETTINGS:
        if k in data:
            set_setting(k, data[k])
    return jsonify(message='设置已保存', settings={k: get_setting(k) for k in DEFAULT_SETTINGS})


@app.route('/api/v1/admin/delete-student', methods=['POST'])
@admin_only
def admin_delete_student():
    """删除学员（需二次输入管理员密码，防误删）。"""
    u = current_user()
    data = request.get_json(silent=True) or {}
    student_id = data.get('student_id')
    admin_pwd = data.get('admin_password') or ''
    if not u.check_password(admin_pwd):
        return jsonify(error='管理员密码错误，删除已取消'), 403
    stu = User.query.get_or_404(student_id)
    if stu.role == 'admin':
        return jsonify(error='不能删除管理员'), 400
    # 级联清理该学员相关数据
    CourseAssignment.query.filter_by(student_id=stu.id).delete()
    StudentSentenceProgress.query.filter_by(student_id=stu.id).delete()
    WrongAnswer.query.filter_by(student_id=stu.id).delete()
    CoinTransaction.query.filter_by(user_id=stu.id).delete()
    PurchaseOrder.query.filter_by(student_id=stu.id).delete()
    WishSupport.query.filter_by(supporter_id=stu.id).delete()
    Wish.query.filter_by(student_id=stu.id).delete()
    db.session.delete(stu)
    db.session.commit()
    return jsonify(message=f'学员 {stu.username} 已删除')


@app.route('/api/v1/admin/upload-course', methods=['POST'])
@admin_only
def upload_course():
    u = current_user()
    # 获取原始文本：multipart 文件 / 表单 raw_text / JSON body（结构化对象）
    raw = None
    obj = None
    if request.content_type and 'multipart/form-data' in request.content_type:
        f = request.files.get('file')
        if f:
            raw = f.read().decode('utf-8-sig')   # 兼容 BOM
        else:
            raw = (request.form.get('raw_text') or '').strip()
    else:
        data = request.get_json(silent=True) or {}
        if 'raw_text' in data:
            raw = data['raw_text']
        elif 'sentences' in data or 'title' in data:
            obj = data                         # 已是单篇课程结构
        else:
            raw = ''

    if raw is not None:
        obj, err = extract_json(raw)
        if err:
            return jsonify(
                error='课程 JSON 解析失败：' + err,
                hint='支持：① 单篇 {"title":...,"sentences":[...]}；② 含多篇的 JSON 数组；'
                     '③ LLM 输出的 ```json ... ``` 栅栏文本。字段：'
                     'article_id, title, full_text, sentences[{sentence_id,english,chinese,target_words}]。'), 400

    if isinstance(obj, dict):
        items = [obj]
    elif isinstance(obj, list):
        items = obj
    else:
        return jsonify(error='课程数据格式不正确：应为对象或数组'), 400

    created, skipped, created_ids = [], [], []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            skipped.append({'index': idx + 1, 'reason': '不是对象'})
            continue
        title = (item.get('title') or '').strip()
        sentences = item.get('sentences') or []
        if not title or not isinstance(sentences, list) or not sentences:
            skipped.append({'index': idx + 1, 'reason': '缺少 title 或 sentences'})
            continue
        bad = [si + 1 for si, s in enumerate(sentences)
               if not isinstance(s, dict) or not s.get('english') or not s.get('chinese')]
        if bad:
            skipped.append({'index': idx + 1, 'reason': f'第 {bad[0]} 句缺少 english 或 chinese'})
            continue
        course = Course(title=title, full_text=item.get('full_text'),
                        external_article_id=int(item['article_id']) if item.get('article_id') else None,
                        created_by_admin_id=u.id, is_published=False)
        db.session.add(course)
        db.session.flush()
        folder = os.path.join(COURSE_UPLOAD_DIR, str(course.id))
        os.makedirs(folder, exist_ok=True)
        for sidx, s in enumerate(sentences):
            db.session.add(Sentence(
                course_id=course.id,
                sentence_order=int(s.get('sentence_id') or s.get('sentence_order') or sidx + 1),
                english=s['english'], chinese=s['chinese'],
                audio_url=s.get('audio_url') or '',
                target_words=s.get('target_words') or [],
                svo=s.get('svo') or [],
                chinese_keywords=s.get('chinese_keywords') or []))
        created.append(title)
        created_ids.append(course.id)
    db.session.commit()
    if not created:
        return jsonify(error='没有任何课程被创建', skipped=skipped), 400
    return jsonify(message=f'已创建 {len(created)} 门课程',
                   created_count=len(created), created_ids=created_ids,
                   titles=created, skipped=skipped,
                   first_course_id=created_ids[0] if created_ids else None,
                   audio_folder=(f'/uploads/courses/{created_ids[0]}/' if created_ids else ''))


@app.route('/api/v1/admin/upload-audio', methods=['POST'])
@admin_only
def upload_audio():
    """批量上传音频：文件名数字 = sentence_id（如 1.mp3 → 第1句）。"""
    course_id = int(request.form.get('course_id'))
    course = Course.query.get_or_404(course_id)
    files = request.files.getlist('audio')
    if not files:
        return jsonify(error='未收到音频文件'), 400
    folder = os.path.join(COURSE_UPLOAD_DIR, str(course_id))
    os.makedirs(folder, exist_ok=True)
    results = []
    for f in files:
        if not f or not f.filename:
            continue
        fn = f.filename
        base = os.path.splitext(fn)[0]
        m = re.match(r'(\d+)', base)
        if not m:
            results.append({'file': fn, 'status': 'skipped',
                            'reason': '文件名不含数字，无法对应句子'})
            continue
        num = int(m.group(1))
        s = Sentence.query.filter_by(course_id=course_id, sentence_order=num).first()
        if not s:
            results.append({'file': fn, 'status': 'skipped',
                            'reason': f'无 sentence_id={num} 的句子'})
            continue
        safe = re.sub(r'[^A-Za-z0-9_.\-]', '_', fn)
        f.save(os.path.join(folder, safe))
        url = f'/uploads/courses/{course_id}/{safe}'
        s.audio_url = url
        results.append({'file': fn, 'sentence_id': num, 'status': 'ok', 'url': url})
    db.session.commit()
    ok_count = sum(1 for r in results if r['status'] == 'ok')
    return jsonify(message=f'音频处理完成，成功 {ok_count}/{len(files)}',
                   results=results)


@app.route('/api/v1/admin/scan-audio', methods=['POST'])
@admin_only
def scan_audio():
    """扫描课程音频文件夹，自动与数据库同步（供远程批量上传后一键入库）。

    规则（文件名数字 = 句子序号，如 1.mp3 → 第1句）：
      - 磁盘有文件、DB 未指向 → 自动写入 audio_url
      - DB 指向某文件、但该文件在磁盘已不存在 → 清理引用（前端将显示缺音频）
      - 磁盘文件序号在库中无对应句子 → 记录为孤儿文件，跳过
    支持单课程扫描（body 传 course_id）或全量扫描（不传 course_id）。
    """
    data = request.get_json(silent=True) or {}
    cid = data.get('course_id')
    if cid:
        course = Course.query.get_or_404(int(cid))
        courses = [course]
    else:
        courses = Course.query.order_by(Course.id).all()

    AUDIO_EXT = ('.mp3', '.wav', '.ogg', '.m4a')
    report = []
    total_updated = 0
    total_cleared = 0
    total_orphan = 0
    for course in courses:
        folder = os.path.join(COURSE_UPLOAD_DIR, str(course.id))
        disk_files = {}        # sentence_order -> filename
        orphans = []
        if os.path.isdir(folder):
            for fn in sorted(os.listdir(folder)):
                low = fn.lower()
                if not low.endswith(AUDIO_EXT):
                    continue
                m = re.match(r'(\d+)', os.path.splitext(fn)[0])
                if not m:
                    orphans.append(fn)
                    continue
                disk_files[int(m.group(1))] = fn
        disk_filenames = set(disk_files.values())

        sents = Sentence.query.filter_by(course_id=course.id).order_by(Sentence.sentence_order).all()
        sent_orders = {s.sentence_order for s in sents}
        updated, cleared = [], []
        for s in sents:
            disk_file = disk_files.get(s.sentence_order)
            if disk_file:
                target = f'/uploads/courses/{course.id}/{disk_file}'
                if s.audio_url != target:
                    s.audio_url = target
                    updated.append(s.sentence_order)
            else:
                if s.audio_url:
                    old_fn = s.audio_url.rsplit('/', 1)[-1]
                    if old_fn and old_fn not in disk_filenames:
                        s.audio_url = ''
                        cleared.append(s.sentence_order)
        db.session.commit()

        # 孤儿：磁盘有序号但库无对应句子
        orphan_orders = sorted(set(disk_files) - sent_orders)
        orphan_files = [disk_files[o] for o in orphan_orders] + orphans
        total_orphan += len(orphan_files)
        report.append({
            'course_id': course.id,
            'title': course.title,
            'disk_files': len(disk_files),
            'updated': updated,
            'cleared': cleared,
            'updated_count': len(updated),
            'cleared_count': len(cleared),
            'orphan_files': orphan_files,
            'orphan_count': len(orphan_files),
        })
        total_updated += len(updated)
        total_cleared += len(cleared)

    return jsonify(message=f'扫描完成：同步 {total_updated} 句音频，清理 {total_cleared} 条失效引用，{total_orphan} 个未匹配文件',
                   total_updated=total_updated, total_cleared=total_cleared, total_orphan=total_orphan,
                   report=report)


def _parse_csv(raw):
    reader = csv.DictReader(io.StringIO(raw))
    sentences = []
    full = []
    for row in reader:
        eng = row.get('english', '').strip()
        if eng:
            full.append(eng)
        sentences.append({
            'sentence_order': int(row.get('sentence_order', 0) or 0),
            'english': eng,
            'chinese': row.get('chinese', '').strip(),
            'audio_url': row.get('audio_url', '').strip(),
            'target_words': _json_or_list(row.get('target_words')),
            'svo': _json_or_list(row.get('svo')),
            'chinese_keywords': _json_or_list(row.get('chinese_keywords')),
        })
    return sentences, ' '.join(full)


def _json_or_list(v):
    if not v:
        return []
    v = v.strip()
    if v.startswith('['):
        try:
            return json.loads(v)
        except Exception:
            return []
    return [x.strip() for x in v.split(',') if x.strip()]


@app.route('/api/v1/admin/publish-course', methods=['POST'])
@admin_only
def publish_course():
    data = request.get_json(silent=True) or {}
    cid = data.get('course_id')
    c = Course.query.get_or_404(cid)
    c.is_published = True
    db.session.commit()
    return jsonify(message='课程已发布')


@app.route('/api/v1/admin/assign-course', methods=['POST'])
@admin_only
def assign_course():
    data = request.get_json(silent=True) or {}
    cid = data.get('course_id')
    student_ids = data.get('student_ids', [])
    # 解锁模式：free=自由学习（解锁即可学）/ locked=解锁式学习（完成一门才解锁下一门）
    unlock_mode = data.get('unlock_mode', 'free')
    if unlock_mode not in ('free', 'locked'):
        unlock_mode = 'free'
    c = Course.query.get_or_404(cid)
    if not c.is_published:
        return jsonify(error='请先发布课程'), 400
    for sid in student_ids:
        a = CourseAssignment.query.filter_by(student_id=sid, course_id=cid).first()
        if a:
            # 覆盖重置进度
            a.current_step = 1
            a.step_1_unlocked = True
            a.step_2_unlocked = a.step_3_unlocked = a.step_4_unlocked = a.step_5_unlocked = a.step_6_unlocked = False
            a.is_completed = False
            a.completed_steps = []
            a.perfect_steps = []
            a.completion_awarded = False
            a.unlock_mode = unlock_mode
            a.assigned_at = models.utcnow()
        else:
            a = CourseAssignment(student_id=sid, course_id=cid, unlock_mode=unlock_mode)
        db.session.add(a)
    db.session.commit()
    return jsonify(message=f'已分配 {len(student_ids)} 名学生（{unlock_mode == "locked" and "解锁式学习" or "自由学习"}）')


def _assign_one(sid, cid, unlock_mode):
    """给单个学生分配单门课程（已分配则覆盖重置进度）。返回是否成功。"""
    a = CourseAssignment.query.filter_by(student_id=sid, course_id=cid).first()
    if a:
        a.current_step = 1
        a.step_1_unlocked = True
        a.step_2_unlocked = a.step_3_unlocked = a.step_4_unlocked = a.step_5_unlocked = False
        a.is_completed = False
        a.completed_steps = []
        a.perfect_steps = []
        a.completion_awarded = False
        a.unlock_mode = unlock_mode
        a.assigned_at = models.utcnow()
    else:
        a = CourseAssignment(student_id=sid, course_id=cid, unlock_mode=unlock_mode)
    db.session.add(a)


@app.route('/api/v1/admin/assign-courses-batch', methods=['POST'])
@admin_only
def assign_courses_batch():
    """批量推送：一次性把多门课程分配给多名学生。"""
    data = request.get_json(silent=True) or {}
    course_ids = data.get('course_ids', [])
    student_ids = data.get('student_ids', [])
    unlock_mode = data.get('unlock_mode', 'free')
    if unlock_mode not in ('free', 'locked'):
        unlock_mode = 'free'
    if not course_ids:
        return jsonify(error='请选择至少一门课程'), 400
    if not student_ids:
        return jsonify(error='请选择至少一名学生'), 400
    # 仅推送已发布课程；按 order_index 排序，保证解锁式学习顺序稳定
    courses = Course.query.filter(Course.id.in_(course_ids)).all()
    published = [c for c in courses if c.is_published]
    skipped = [c.id for c in courses if not c.is_published]
    if not published:
        return jsonify(error='所选课程均未发布，请先发布'), 400
    published.sort(key=lambda c: (c.order_index or 0, c.id))
    pairs = 0
    for sid in student_ids:
        for c in published:
            _assign_one(sid, c.id, unlock_mode)
            pairs += 1
    db.session.commit()
    msg = f'已向 {len(student_ids)} 名学生推送 {len(published)} 门课程（共 {pairs} 条分配，{unlock_mode == "locked" and "解锁式学习" or "自由学习"}）'
    if skipped:
        msg += f'；跳过未发布课程 {len(skipped)} 门'
    return jsonify(message=msg, assigned_pairs=pairs, skipped_unpublished=skipped)


@app.route('/api/v1/admin/sentences/<int:course_id>', methods=['GET'])
@admin_only
def admin_sentences(course_id):
    sents = Sentence.query.filter_by(course_id=course_id) \
        .order_by(Sentence.sentence_order).all()
    return jsonify(sentences=[serialize_sentence(s) for s in sents])


@app.route('/api/v1/admin/db-view', methods=['GET'])
@admin_only
def db_view():
    table = request.args.get('table', '')
    allowed = {
        'users': User, 'admin_share_keys': AdminShareKey, 'courses': Course,
        'sentences': Sentence, 'course_assignments': CourseAssignment,
        'student_sentence_progress': StudentSentenceProgress,
        'wrong_answers': WrongAnswer, 'coin_transactions': CoinTransaction,
        'shop_items': ShopItem, 'purchase_orders': PurchaseOrder,
        'wishes': Wish, 'wish_supports': WishSupport,
    }
    model = allowed.get(table)
    if not model:
        return jsonify(error='未知表', allowed=list(allowed.keys())), 400
    rows = model.query.limit(100).all()
    cols = [c.name for c in model.__table__.columns]
    data = [{c.name: _serial(getattr(v, c.name)) for c in model.__table__.columns}
            for v in rows]
    return jsonify(columns=cols, rows=data)


def _serial(v):
    if isinstance(v, (datetime.date, datetime.datetime)):
        return str(v)
    if isinstance(v, (dict, list)):
        return v
    return v


# 商店管理
@app.route('/api/v1/admin/shop-item', methods=['POST'])
@admin_only
def upsert_shop_item():
    u = current_user()
    data = request.get_json(silent=True) or {}
    iid = data.get('id')
    if iid:
        item = ShopItem.query.get_or_404(iid)
    else:
        item = ShopItem(admin_id=u.id)
    item.name = data.get('name', item.name)
    item.description = data.get('description', item.description)
    item.price_coins = int(data.get('price_coins', item.price_coins))
    item.stock = int(data.get('stock', item.stock if item.stock is not None else -1))
    item.is_on_shelf = bool(data.get('is_on_shelf', item.is_on_shelf))
    db.session.add(item)
    db.session.commit()
    return jsonify(message='商品已保存', id=item.id)


@app.route('/api/v1/admin/shop-items', methods=['GET'])
@admin_only
def admin_shop_items():
    items = ShopItem.query.order_by(ShopItem.id).all()
    return jsonify(items=[{
        'id': i.id, 'name': i.name, 'description': i.description,
        'price_coins': i.price_coins, 'stock': i.stock, 'is_on_shelf': i.is_on_shelf,
    } for i in items])


@app.route('/api/v1/admin/orders', methods=['GET'])
@admin_only
def admin_orders():
    orders = PurchaseOrder.query.order_by(PurchaseOrder.created_at.desc()).all()
    out = []
    for o in orders:
        stu = User.query.get(o.student_id)
        item = ShopItem.query.get(o.item_id)
        out.append({
            'id': o.id, 'student': stu.username if stu else '?',
            'student_id': o.student_id,
            'item': item.name if item else '?',
            'price': item.price_coins if item else 0,
            'status': o.status, 'admin_note': o.admin_note, 'reject_reason': o.reject_reason,
            'created_at': str(o.created_at),
            'shipped_at': str(o.shipped_at) if o.shipped_at else None,
            'completed_at': str(o.completed_at) if o.completed_at else None,
        })
    return jsonify(orders=out)


@app.route('/api/v1/admin/ship-order', methods=['POST'])
@admin_only
def ship_order():
    data = request.get_json(silent=True) or {}
    oid = data.get('order_id')
    note = data.get('admin_note', '')
    o = PurchaseOrder.query.get_or_404(oid)
    o.status = 'shipped'
    o.admin_note = note
    db.session.commit()
    return jsonify(message='已发货')


# 许愿池管理
@app.route('/api/v1/admin/wish/process', methods=['POST'])
@admin_only
def wish_process():
    data = request.get_json(silent=True) or {}
    wid = data.get('wish_id')
    action = data.get('action')  # approve / reject / complete
    reply = (data.get('reply') or '').strip()
    w = Wish.query.get_or_404(wid)
    if action == 'approve':
        if w.status not in ('pending', 'rejected'):
            return jsonify(error='该愿望当前状态不可批准'), 400
        w.status = 'approved'
        w.resolved_at = models.utcnow()
    elif action == 'reject':
        if w.status == 'completed':
            return jsonify(error='已完成的愿望不能驳回'), 400
        if w.status == 'rejected':
            return jsonify(error='该愿望已驳回'), 400
        # 退还创建人原始投入 + 所有助力金币
        total = w.total_coins_invested or 0
        if total > 0:
            add_coins(w.student_id, total, f'许愿被驳回退回:{w.content[:20]}',
                      category='refund', operator_id=current_user().id)
        supports = WishSupport.query.filter_by(wish_id=w.id).all()
        for sp in supports:
            add_coins(sp.supporter_id, sp.coins, f'助力被驳回退回:#{w.id}',
                      category='refund', operator_id=current_user().id)
        w.status = 'rejected'
        w.resolved_at = models.utcnow()
    elif action == 'complete':
        if w.status != 'approved':
            return jsonify(error='只有已批准的愿望才能归档完成'), 400
        w.status = 'completed'
        w.completed_at = models.utcnow()
    else:
        return jsonify(error='action 必须为 approve/reject/complete'), 400
    w.admin_reply = reply
    db.session.commit()
    return jsonify(message='已处理', status=w.status)


# ---------------- 金币流水 ----------------
@app.route('/api/v1/coin/transactions', methods=['GET'])
@jwt_required()
def coin_transactions():
    u = current_user()
    rows = CoinTransaction.query.filter_by(user_id=u.id) \
        .order_by(CoinTransaction.created_at.desc()).all()
    out = [{
        'id': r.id, 'amount': r.amount, 'reason': r.reason,
        'category': r.category, 'created_at': str(r.created_at),
    } for r in rows]
    return jsonify(transactions=out, balance=u.coin_balance)


@app.route('/api/v1/admin/coin-transactions', methods=['GET'])
@admin_only
def admin_coin_transactions():
    rows = CoinTransaction.query.order_by(CoinTransaction.created_at.desc()).limit(500).all()
    out = []
    for r in rows:
        u = User.query.get(r.user_id)
        op = User.query.get(r.operator_id) if r.operator_id else None
        out.append({
            'id': r.id, 'user': u.username if u else '?', 'user_id': r.user_id,
            'amount': r.amount, 'reason': r.reason, 'category': r.category,
            'operator': op.username if op else '', 'created_at': str(r.created_at),
        })
    return jsonify(transactions=out)


# ---------------- 课程管理列表 ----------------
@app.route('/api/v1/admin/courses', methods=['GET'])
@admin_only
def admin_courses():
    courses = Course.query.order_by(Course.id).all()
    out = []
    for c in courses:
        sents = Sentence.query.filter_by(course_id=c.id).all()
        total = len(sents)
        audio = sum(1 for s in sents if s.audio_url)
        missing = [s.sentence_order for s in sents if not s.audio_url]
        has_error = any((not s.english) or (not s.chinese) for s in sents)
        out.append({
            'id': c.id, 'title': c.title, 'is_published': c.is_published,
            'sentence_count': total, 'audio_count': audio,
            'missing_audio': missing, 'has_error': has_error,
            'created_at': str(c.created_at),
        })
    return jsonify(courses=out)


@app.route('/api/v1/admin/course/<int:course_id>/errors', methods=['GET'])
@admin_only
def admin_course_errors(course_id):
    c = Course.query.get_or_404(course_id)
    sents = Sentence.query.filter_by(course_id=course_id).order_by(Sentence.sentence_order).all()
    missing_audio = [s.sentence_order for s in sents if not s.audio_url]
    missing_fields = [s.sentence_order for s in sents if (not s.english) or (not s.chinese)]
    return jsonify(course_id=course_id, title=c.title, total=len(sents),
                   missing_audio=missing_audio, missing_fields=missing_fields,
                   has_error=(len(missing_fields) > 0))


# ---------------- 课程单词库管理（v0.5 Step7 单词巩固） ----------------
@app.route('/api/v1/admin/course/<int:course_id>/extract-words', methods=['POST'])
@admin_only
def admin_extract_words(course_id):
    """一键提取课程实词并存入 course_words（保留管理员手动添加的词）。"""
    c = Course.query.get_or_404(course_id)
    words = extract_course_words(c)
    # 保留手动添加的词，避免被覆盖
    custom = {w.word for w in CourseWord.query.filter_by(course_id=course_id, is_custom=True)}
    CourseWord.query.filter_by(course_id=course_id, is_custom=False).delete()
    for w in words:
        if w in custom:
            continue
        db.session.add(CourseWord(course_id=course_id, word=w, is_custom=False))
    db.session.commit()
    out = CourseWord.query.filter_by(course_id=course_id).order_by(CourseWord.word).all()
    return jsonify(message='已提取并保存单词', count=len(out),
                   words=[{'id': w.id, 'word': w.word, 'is_custom': w.is_custom} for w in out])


@app.route('/api/v1/admin/course/<int:course_id>/words', methods=['GET'])
@admin_only
def admin_list_words(course_id):
    Course.query.get_or_404(course_id)
    out = CourseWord.query.filter_by(course_id=course_id).order_by(CourseWord.word).all()
    return jsonify(words=[{'id': w.id, 'word': w.word, 'is_custom': w.is_custom} for w in out])


@app.route('/api/v1/admin/course/<int:course_id>/word', methods=['POST'])
@admin_only
def admin_add_word(course_id):
    Course.query.get_or_404(course_id)
    word = ((request.get_json(silent=True) or {}).get('word') or '').strip().lower()
    if not word:
        return jsonify(error='单词不能为空'), 400
    if CourseWord.query.filter_by(course_id=course_id, word=word).first():
        return jsonify(error='单词已存在'), 400
    w = CourseWord(course_id=course_id, word=word, is_custom=True)
    db.session.add(w)
    db.session.commit()
    return jsonify(message='已添加单词', id=w.id, word=w.word, is_custom=True)


@app.route('/api/v1/admin/course/<int:course_id>/word/<int:word_id>', methods=['DELETE'])
@admin_only
def admin_delete_word(course_id, word_id):
    w = CourseWord.query.get_or_404(word_id)
    if w.course_id != course_id:
        return jsonify(error='单词与课程不匹配'), 400
    db.session.delete(w)
    db.session.commit()
    return jsonify(message='已删除单词')


@app.route('/api/v1/admin/unpublish-course', methods=['POST'])
@admin_only
def unpublish_course():
    data = request.get_json(silent=True) or {}
    c = Course.query.get_or_404(data.get('course_id'))
    c.is_published = False
    db.session.commit()
    return jsonify(message='课程已撤销发布')


@app.route('/api/v1/admin/course/<int:course_id>', methods=['DELETE'])
@admin_only
def delete_course(course_id):
    c = Course.query.get_or_404(course_id)
    Sentence.query.filter_by(course_id=course_id).delete()
    CourseAssignment.query.filter_by(course_id=course_id).delete()
    db.session.delete(c)
    db.session.commit()
    return jsonify(message='课程已删除')


@app.route('/api/v1/admin/update-course', methods=['POST'])
@admin_only
def update_course():
    data = request.get_json(silent=True) or {}
    c = Course.query.get_or_404(data.get('course_id'))
    if data.get('title') is not None:
        c.title = data['title']
    if 'full_text' in data and data['full_text'] is not None:
        c.full_text = data['full_text']
    if 'sentences' in data and isinstance(data['sentences'], list):
        Sentence.query.filter_by(course_id=c.id).delete()
        for sidx, s in enumerate(data['sentences']):
            if not isinstance(s, dict):
                continue
            db.session.add(Sentence(
                course_id=c.id,
                sentence_order=int(s.get('sentence_order') or s.get('sentence_id') or sidx + 1),
                english=s.get('english', ''), chinese=s.get('chinese', ''),
                audio_url=s.get('audio_url') or '',
                target_words=s.get('target_words') or [],
                svo=s.get('svo') or [],
                chinese_keywords=s.get('chinese_keywords') or []))
    db.session.commit()
    return jsonify(message='课程已更新')


# ---------------- 订单生命周期 + 商品上下架 ----------------
@app.route('/api/v1/shop/orders', methods=['GET'])
@jwt_required()
def my_orders():
    u = current_user()
    orders = PurchaseOrder.query.filter_by(student_id=u.id) \
        .order_by(PurchaseOrder.created_at.desc()).all()
    out = []
    for o in orders:
        item = ShopItem.query.get(o.item_id)
        out.append({
            'id': o.id, 'item_name': item.name if item else '?',
            'price': item.price_coins if item else 0,
            'status': o.status, 'admin_note': o.admin_note, 'reject_reason': o.reject_reason,
            'created_at': str(o.created_at),
            'shipped_at': str(o.shipped_at) if o.shipped_at else None,
            'completed_at': str(o.completed_at) if o.completed_at else None,
        })
    return jsonify(orders=out)


@app.route('/api/v1/admin/archive-order', methods=['POST'])
@admin_only
def archive_order():
    u = current_user()
    data = request.get_json(silent=True) or {}
    o = PurchaseOrder.query.get_or_404(data.get('order_id'))
    if o.status not in ('shipped', 'pending'):
        return jsonify(error='只有已发货或待发货订单可归档'), 400
    o.status = 'completed'
    o.completed_at = models.utcnow()
    db.session.commit()
    return jsonify(message='已存档，交易完成')


@app.route('/api/v1/admin/reject-order', methods=['POST'])
@admin_only
def reject_order():
    u = current_user()
    data = request.get_json(silent=True) or {}
    o = PurchaseOrder.query.get_or_404(data.get('order_id'))
    if o.status == 'completed':
        return jsonify(error='已完成订单不能驳回'), 400
    if o.status == 'rejected':
        return jsonify(error='该订单已驳回'), 400
    item = ShopItem.query.get(o.item_id)
    price = item.price_coins if item else 0
    add_coins(o.student_id, price, f'订单驳回退回:{item.name if item else "?"}',
              category='refund', operator_id=u.id)
    if item and item.stock is not None and item.stock >= 0:
        item.stock += 1
    o.status = 'rejected'
    o.reject_reason = (data.get('reason') or '').strip()
    db.session.commit()
    stu = User.query.get(o.student_id)
    return jsonify(message='已驳回，金币已退回', balance=stu.coin_balance if stu else None)


@app.route('/api/v1/admin/toggle-shelf', methods=['POST'])
@admin_only
def toggle_shelf():
    data = request.get_json(silent=True) or {}
    item = ShopItem.query.get_or_404(data.get('item_id'))
    item.is_on_shelf = not item.is_on_shelf
    db.session.commit()
    return jsonify(message='已' + ('下架' if not item.is_on_shelf else '上架'),
                   is_on_shelf=item.is_on_shelf)


# ---------------- 前端页面（标准 Flask 模板） ----------------
@app.route('/')
def index():
    return render_template('index.html')


# 课程音频静态托管
@app.route('/uploads/<path:path>')
def serve_uploads(path):
    return send_from_directory(UPLOAD_DIR, path)


# ---------------- 单词大师（独立 Blueprint，挂到主 app） ----------------
from wordmaster import wordmaster_bp
app.register_blueprint(wordmaster_bp)


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        try:
            from init_db import migrate
            migrate()
        except Exception as e:
            print('migrate skipped:', e)
    app.run(host='0.0.0.0', port=5000, debug=True)
