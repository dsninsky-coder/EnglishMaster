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
import threading
import queue

from flask import Flask, request, jsonify, send_from_directory, render_template, session
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, get_jwt_identity
)
from sqlalchemy import func, distinct

import models
from models import db, User, AdminShareKey, Course, Sentence, CourseAssignment, \
    StudentSentenceProgress, WrongAnswer, CoinTransaction, ShopItem, PurchaseOrder, \
    Wish, WishSupport, SystemSetting, CourseWord, Appeal, \
    CourseScheme, CourseSchemeItem, CourseSchemeStudent, SchemeAssignment, SchemeStepProgress
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

# ================= 系统版本与升级内容（登录页展示 / 数据兼容性参考） =================
VERSION = 'v1.4.1'
# 每个版本是否影响老数据：全部为非破坏性（仅新增列 / 受控数据重映射），无清库操作。
# 详见 README「数据兼容性」一节；迁移前 init_db.py 会自动备份数据库。
CHANGELOG = [
    {'version': 'v1.4.0', 'date': '2026-08-06', 'title': '听力大师 4 步学习系统完整实现',
     'items': [
         '新增 Step 1 单词熟悉（全文单词 + 有道发音 + IPA 音标 + 中文释义，零金币学习步骤）',
         '新增 Step 2 句子理解（英/音/汉全展示，逐句浏览理解，零金币学习步骤）',
         '新增 Step 3 辅助听写（给中文释义 + 音频 → 写英文，答错不显答案、改到对为止，一次性答对 2 金币/二次 1 金币）',
         '新增 Step 4 纯听写 + 翻译（拆为 A 听写形 + B 翻译义两个独立题目，分别提交分别计币，每题最多 2 金币）',
         '跳过机制：本题答不出可跳至队尾，必须全部答完才能结束本步，无放弃出口；实在答不完可用人工复议强制结束',
         '回退机制：错误次数达阈值（课程方案中设置）自动触发回退选项，回退至上一步重新巩固',
         '冷却保护：回退后进入上一步，记录时间；再次进入曾被回退的步骤须等待冷却时长（防刷答案）',
         '人工复议：保留原规则（2 金币/次），集成到新 4 步系统中',
         '课程方案顺序解锁：前一课程完成才自动解锁下一课程，管理员可任意配置每课步骤',
         '学生端新增 🎯 听力大师 入口（独立于 🎧 听说大师），共享课程/句子/单词原材料',
         '旧版 7 步听说大师完全可用不受影响，两模块并行',
         '共新增 10 个学生端 API 端点 + 10 个管理员方案管理端点',
     ]},
    {'version': 'v0.8.7', 'date': '2026-08-02', 'title': '词色标注：英文多词短语同色组（gid）',
     'items': [
         '新增「同色组 gid」：一个短语拆成的多个英文片段填相同 gid 即共享同色，支持非相邻短语动词，如 throws … up（中间 it 黑色）',
         'AI 生成提示词补充 gid 规则：短语动词（throws it up / turn on the light）拆段并标同一 gid，动词与小品词同色、宾语黑色',
         '管理员校对编辑器：每个片段新增「同色组」数字输入框；相同数字→同色，下方学生端预览实时显示同色效果',
         '后端 _assign_alignment_colors 与前端 alignColorsFor 均 gid 感知；PUT /admin/sentence/<id>/alignment 支持保存 gid',
         '示例：He throws it up in the air → throws(抛,gid1) it(黑) up(起,gid1) in the air(空中)，throws 与 up 同红、it 黑',
         '无数据库结构变更（units 新增可选 gid 字段，旧标注无 gid 仍按原逻辑各自取色，向后兼容）',
     ]},
    {'version': 'v0.8.6', 'date': '2026-08-02', 'title': '词色标注：后台任务队列串行 + 真实失败原因透传',
     'items': [
         '修复：连续点击多个课程「生成词色标注」会多线程并行写同一 SQLite 文件，触发 database is locked → 500 → 前端只显示裸「生成失败」且无原因',
         '改为后台单 worker 串行队列：接口只入队立即返回，worker 依次处理；彻底消除并发写库冲突，也不再并行轰炸 AI 接口导致限流',
         '前端轮询 GET /admin/align-status：实时显示「正在生成《课程》… 进度 done/total」，完成后展示结果；失败原因常驻、点了才消失',
         'deepseek_client._chat 新增 raise_on_error：超时/429 限流/401/断网等真实异常现在会透传并记录到 errors（不再是笼统的「AI 未返回有效标注」）',
         '任务进行中禁用「生成词色标注」按钮，避免重复入队',
         '无数据库结构变更',
     ]},
    {'version': 'v0.8.5', 'date': '2026-08-02', 'title': '词色标注生成失败原因可见 + 提示常驻',
     'items': [
         '修复：单句 AI 生成异常（超时/网络/JSON 解析失败）会令整次请求 500，前端只弹「生成失败」且无原因、2.6 秒即消失',
         '后端逐句容错：生成异常或 AI 未返回有效结果不再中断整次请求，失败句记入 errors 并返回（含句序/英文/具体错误）',
         '管理员界面：生成失败时弹出常驻提示（sticky toast），列出每句失败原因，**需点击 ✕ 才消失**；成功仍自动消失',
         'toast 支持 sticky 模式与点击关闭；错误提示可换行展示多句失败',
         '无数据库结构变更',
     ]},
    {'version': 'v0.8.4', 'date': '2026-08-02', 'title': '词色标注：一个英文词对应中文多处（/ 、 分隔）',
     'items': [
         '中文对应支持多段：在「中文对应」里用 / 或 、 分隔多个分散的字/词，例如 near → 在/旁',
         '学生端 Step1 渲染：仅把中文句里对应的「在」「旁」分别上同色，「…」等中间字保持黑色',
         '管理员校对编辑器新增「学生端预览」：实时显示当前编辑的中文上色效果，输入即更新',
         '校对编辑器提示更新：说明 / 、 分隔用法；AI 生成提示词同步支持该写法',
         '无数据库结构变更（zh 仍是普通字符串，向后兼容旧标注）',
     ]},
    {'version': 'v0.8.3', 'date': '2026-08-02', 'title': '词色标注人工校对编辑器',
     'items': [
         '新增管理员「校对标注」入口（课程列表每课「校对标注」按钮，或 #/admin/align/<课程ID>）',
         '逐句编辑英文片段、中文对应、是否上色（黑/彩切换）、增删片段；保存即写库，学生端刷新即生效，无需重推',
         '后端：GET /admin/course/<id>/sentences（取句子+alignment）、PUT /admin/sentence/<id>/alignment（按句覆盖保存）',
         '保存时上色规则与生成一致：content 且 zh 非空按出现顺序循环取色，虚词黑色',
         '无数据库结构变更',
     ]},
    {'version': 'v0.8.2', 'date': '2026-08-02', 'title': '词色标注优化：短语级切分 + 状态可视化',
     'items': [
         '切分粒度改为「自然意群/短语」：如 The farm / wakes up / on the wall / a rooster，不再逐词孤立切分',
         '上色规则由「按词性」改为「该片段有中文翻译即上色」，英文片段与中文同色一一对应，更利于建立中英意群链接',
         '管理员课程列表新增「词色标注」列：显示 🎨 已标注 X/Y 或「未标注」',
         '错误检查新增词色标注检查：列出未生成标注的句子序号，并可一键为本科目生成',
         '新增「🎨 一键标注全部课程」按钮：相当于逐课点一遍，全量回填存量课程',
         '无数据库结构变更',
     ]},
    {'version': 'v0.8.1', 'date': '2026-08-02', 'title': '修复词色标注「未配置 AI 模型」报错',
     'items': [
         '根因：系统级 ai_proxy 设置此前只有 base_url/model，没有 API Key 字段；而词色标注生成去取「管理员个人 Key」，导致两头皆空必报错',
         '新增系统级 API Key：管理员「AI 模型设置」增加 API Key 输入框，存于 ai_proxy 设置，全系统兜底',
         'resolve_api_key 优先级改为：分享 Key > 私有 Key > 系统级全局 Key > None，管理员生成标注/学员未配 Key 时自动用系统 Key',
         '错误提示文案更正为「请在管理员『AI 模型设置』中填写 API Key」',
         '无数据库结构变更，仅配置项扩展',
     ]},
    {'version': 'v0.8.0', 'date': '2026-08-02', 'title': '稳定性与体验修复（7 项）',
     'items': [
         '会话过期：JWT 保持 30 天有效，并避免 401 重复跳转登录页死循环',
         '离开确认：做题/任务界面点击别的任务、退出等会离开当前页面的操作，弹出二次确认弹窗，确认后再跳转',
         '移动端输入框：聚焦时自动滚入可视区域，底部预留空间，避免输入法遮挡输入框',
         '单词巩固（Step7）：新增「不会」按钮，点击直接显示答案并自动加入生词表',
         '学生消息通知：登录进首页后弹窗提示金币奖励/扣除、商品发货/退款、许愿审批、人工复核等未读消息',
         'Step5（中译英）提示修复：单词数 X + 最大次数 Y 现正确按「最多 Y 次、每次提示 X 个随机单词」生效，不再在最后一次强制写出整句',
         '课程列表总进度步数此前已修正为 7（Step1~7）',
     ]},
    {'version': 'v0.7.7', 'date': '2026-08-02', 'title': 'Step1 词色对齐标注',
     'items': [
         'Step1 沉浸输入新增词色对齐：英文词与对应中文片段同色标注，虚词（冠词/代词/介词/连词/助词等）黑色不标注，实词（名/动/形/副）上色',
         '对齐在课程导入时一次性由 AI 生成并存入 Sentence.alignment（JSON），Step1 打开只读缓存、零实时开销；无 AI Key 时自动降级为普通显示',
         '管理员课程管理「单词库」新增「🎨 生成词色标注」按钮；另提供全量回填端点处理存量课程',
     ]},
    {'version': 'v0.7.6', 'date': '2026-07-31', 'title': '修复课程列表总进度步数显示',
     'items': [
         '课程列表「当前进度 Step X/5」硬编码为 5，改为按总步数 7 显示（Step1~7）',
     ]},
    {'version': 'v0.7.5', 'date': '2026-07-31', 'title': '修复 Step7 音译中判分漏洞',
     'items': [
         'Step7 单词巩固的音译中/英译中模式，增加前置校验：答案与英文单词相同、或不包含中文字符时直接判错，避免 AI 把英文单词误判为正确',
     ]},
    {'version': 'v0.7.4', 'date': '2026-07-31', 'title': '修复 v0.2→最新 升级迁移崩溃（续）',
     'items': [
         '修复 init_db.py 迁移顺序：step_7_unlocked / 人工复议 appeal_* 列原在 CourseAssignment ORM 查询之后才补齐，导致 ORM SELECT 报 no such column: step_7_unlocked；现改为在 ORM 查询前补齐本表全部列',
     ]},
    {'version': 'v0.7.3', 'date': '2026-07-31', 'title': '修复 v0.2→最新 升级迁移崩溃',
     'items': [
         '修复 init_db.py 步骤重编号迁移：原 "UPDATE step=step+1 WHERE step>=4" 在 (student_id,sentence_id,step) 唯一约束下会撞键崩溃；改为从大到小逐档 +1，避免中间态冲突',
         '消除升级时 3 处 SQLAlchemy LegacyAPIWarning（Query.get → Session.get）',
     ]},
    {'version': 'v0.7.2', 'date': '2026-07-31', 'title': '术语修正：人工复议',
     'items': [
         '将全部界面文案「附议」统一更正为「复议」（人工复议）；内部代码标识符保持 appeal 不变，无需迁移数据库',
     ]},
    {'version': 'v0.7', 'date': '2026-07-31', 'title': '人工复议 + 体验修复',
     'items': [
         '新增人工复议：答错自认正确可花 2 金币申请仲裁，管理员人工判对错',
         '管理员新增「⚖️ 人工复议」独立栏目，待办带角标',
         '课程管理新增「一键提取所有课程单词」',
         'Step2 补音效；Step3 加入「跳过看答案」',
     ]},
    {'version': 'v0.6', 'date': '2026-07', 'title': 'Step7 分批取词 + 生词表',
     'items': [
         'Step7 单词巩固改为分批取词、音汉/英汉交替',
         '新增生词表，打通单词大师',
         '发音改用有道 API（禁用浏览器朗读）',
     ]},
    {'version': 'v0.5', 'date': '2026-07', 'title': 'Step7 单词巩固',
     'items': [
         '新增 Step7 单词巩固、Step4 跟读细化',
         '单词提取（管理员一键提取实词）、Web Audio 音效',
     ]},
    {'version': 'v0.4', 'date': '2026-07', 'title': '步骤重编号 5→6（受控数据迁移）',
     'items': [
         '新增「跟读」Step4，形成六步闯关（旧 Step4/5 进度整体 +1 重映射，保留学习含义）',
         '中译英随机单词提示、移动端登录修复、Step1 体验优化',
     ]},
    {'version': 'v0.3', 'date': '2026-07', 'title': 'AI 模型可配置 + 管理 PC 化',
     'items': [
         'AI 模型可后台配置（兼容任意 OpenAI Chat 接口）',
         '管理后台 PC 化布局、系统工具独立入口',
         '课程音频扫描、金币/商店/许愿跨模块共享',
     ]},
    {'version': 'v0.2', 'date': '2026-07', 'title': '融合单词大师',
     'items': [
         '单词大师以 Blueprint 并入，统一入口「英语大师」',
     ]},
    {'version': 'v0.1', 'date': '2026-06', 'title': '初始发布',
     'items': [
         '五步法闯关式英语学习平台',
     ]},
]


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
        'api_key': (cfg.get('api_key') or '').strip(),
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
    """API 优先级：分享 Key > 私有 Key > 系统级全局 Key > None。

    系统级 Key 由管理员在「AI 模型设置」中填写（存于 ai_proxy 设置），
    作为全系统兜底：管理员自己做词色标注等系统操作、或学生未配个人 Key 时都能用。
    """
    if user and user.shared_api_key_id:
        sk = AdminShareKey.query.get(user.shared_api_key_id)
        if sk and sk.is_active:
            return sk.api_key_value
    if user and user.private_api_key:
        return user.private_api_key
    sys_key = get_ai_proxy().get('api_key')
    if sys_key:
        return sys_key
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


def extract_all_course_words(course):
    """v2.0: 提取课程全文全部单词（含虚词，按文中首次出现顺序，去重，小写）。"""
    seen = set()
    words = []
    sents = Sentence.query.filter_by(course_id=course.id).order_by(Sentence.sentence_order).all()
    for s in sents:
        toks = re.findall(r"[A-Za-z']+", s.english or '')
        for t in toks:
            tl = t.lower().strip("'")
            if len(tl) < 1:
                continue
            if tl in seen:
                continue
            seen.add(tl)
            words.append(tl)
    return words


def _generate_phonetic(word):
    """为单词生成 IPA 音标（eng-to-ipa 离线优先，AI 兜底）。

    返回 (phonetic, source) 元组：成功返回 ('/həˈloʊ/', 'eng-to-ipa')，失败返回 (None, None)。
    """
    try:
        from eng_to_ipa import convert
        result = convert(word, keep_punct=False, retrieve_all=False)
        if result and result != word:
            return (result, 'eng-to-ipa')
    except Exception:
        pass
    # AI 兜底：通过系统级 API Key 调用
    proxy = get_ai_proxy()
    key = proxy.get('api_key')
    if key:
        try:
            prompt = (
                f'Provide ONLY the IPA phonetic transcription for the English word "{word}" '
                f'in American English. Output ONLY the IPA symbols between slashes, nothing else. '
                f'Example: /həˈloʊ/'
            )
            r = ds.call(key, prompt, base_url=proxy['base_url'], model=proxy['model'], temperature=0.1)
            if r:
                match = re.search(r'/([^/]+)/', r.strip())
                if match:
                    return (f'/{match.group(1)}/', 'ai')
        except Exception:
            pass
    return (None, None)


def _generate_meaning(word, context):
    """为单词生成中文释义（AI，传入文章上下文确保语境一致）。

    context: 课程 full_text 或全部句子拼接的英文文本。
    返回 (meaning, source) 元组。
    """
    ctx = (context or '')[:2000]
    proxy = get_ai_proxy()
    key = proxy.get('api_key')
    if key:
        try:
            prompt = (
                f'根据以下英文文章片段，为单词 "{word}" 生成在该语境下的中文释义。\n'
                f'只输出中文释义（2-8个字），不要括号、不要拼音、不要例子。\n\n'
                f'文章片段：\n{ctx}'
            )
            r = ds.call(key, prompt, base_url=proxy['base_url'], model=proxy['model'], temperature=0.3)
            if r:
                meaning = r.strip().strip('"\'。，, ')
                if meaning and len(meaning) <= 20:
                    return (meaning, 'ai')
        except Exception:
            pass
    return (None, None)


# ---------------- Step1 词色对齐（一次性 AI 生成，存库） ----------------
# 调色板：按句子内「有中文对应的片段」顺序循环取色，英文片段与对应中文片段同色；无中文对应的功能词黑色。
ALIGN_PALETTE = ['#e74c3c', '#2980b9', '#27ae60', '#e67e22',
                 '#8e44ad', '#16a085', '#d35400', '#2c3e50']
# 注：切分粒度由 AI 提示词控制（按自然意群/短语，而非逐词）；上色规则为「片段有中文翻译即上色」。
ALIGN_CONTENT_POS = {'NOUN', 'PROPN', 'VERB', 'ADJ', 'ADV'}  # 保留供 pos 参考，上色不再依赖它


def _assign_alignment_colors(raw_units):
    """给对齐片段分配颜色。

    raw_units: 含 en/zh/content(可选)/gid(可选) 的片段列表。
    - 有中文对应(zh 非空)的片段上色；纯功能词(zh 空)黑色。
    - 同一 gid(>0) 的多个片段共享同一颜色，用于把"一个短语拆成的多段"归为一组
      （如短语动词 throws ... up：throws 与 up 同色，中间的 it 为黑色独立单元），
      即使这些片段在原句中并不相邻也能正确同色。
    - gid 为空/0 的片段各自独立取色。
    返回带 color / gid / content 的最终片段列表。
    """
    group_colors = {}
    out, idx = [], 0
    for u in raw_units:
        zh = (u.get('zh') or '').strip()
        has_zh = bool(zh)
        content = u.get('content', has_zh)
        if content is None:
            content = has_zh
        content = bool(content and has_zh)
        gid = u.get('gid')
        try:
            gid = int(gid)
        except (TypeError, ValueError):
            gid = 0
        color = None
        if content:
            key = gid if gid else ('solo', idx)
            if key not in group_colors:
                group_colors[key] = ALIGN_PALETTE[idx % len(ALIGN_PALETTE)]
                idx += 1
            color = group_colors[key]
        out.append({'en': (u.get('en') or '').strip(), 'pos': str(u.get('pos') or 'OTHER').upper(),
                    'content': content, 'color': color, 'zh': zh, 'gid': gid})
    return out


def generate_alignment(english, chinese, user=None):
    """一次性生成 Step1 词色对齐（调用已配置 AI）。

    返回 {'units': [{en, pos, content, color, zh, gid}, ...]}；无 AI Key 或生成失败时返回 None。
    颜色在后端按"实词出现顺序 / 同色组 gid"循环调色板分配，保证英文词与中文片段同色。
    """
    key = resolve_api_key(user) if user else None
    if not key:
        return None
    proxy = get_ai_proxy()
    system = (
        "你是英语标注助手。给定一句英文和它的中文翻译，请按「自然意群 / 短语」把英文切分成若干片段"
        "（不要逐词孤立切分），把功能词与其修饰的内容词合并到同一片段，例如：\n"
        "- \"The farm\"、\"a rooster\" 作为一个片段（冠词不单独拆出）；\n"
        "- \"wakes up\"、\"sings on\" 这类短语动词不拆开；\n"
        "- \"on the wall\"、\"in the morning\" 这类介词短语作为一个片段。\n"
        "每个片段给出：\n"
        "1) en：该英文片段原文（保留大小写；片段内词之间用原空格，标点可附着在词后）；\n"
        "2) zh：该片段在中文翻译里对应的中文片段（只写对应那部分，不要整句；"
        "若此英文片段在中文里完全没有对应词，如冠词 the/a、系动词 is/am/are、纯并列连词，则 zh 填空字符串）；\n"
        "若一个英文片段在中文里对应多个分散的字/词（如 near 对应「在…旁」），用 / 或 、 把这几处连起来，"
        "例如 near 的 zh 写 \"在/旁\"，渲染时这几处会分别上同一颜色；\n"
        "3) pos：该片段中心词的词性，取其一 NOUN/PROPN/VERB/ADJ/ADV/DET/PRON/ADP/CONJ/AUX/PART/NUM/INTJ/PUNCT/OTHER。\n"
        "4) gid：短语组编号（整数，可选）。当一个短语被拆成多个片段、需要它们显示**同色**时使用，"
        "尤其短语动词（动词+小品词），例如 \"throws it up\" 应拆为 throws(gid=1, zh=抛)、it(zh空,黑色)、"
        "up(gid=1, zh=起)——throws 与 up 同色（都属于 throw up 这个短语），中间的 it 是独立黑色单元；"
        "又如 \"turn on the light\"：turn(gid=1)、on(gid=1)、the light(无gid)。不构成短语的片段不要填 gid。\n"
        "要求：英文片段按原顺序拼接后必须等于原英文句（含空格与标点）；不要输出多余解释，"
        "只输出 JSON：{\"units\":[{\"en\":\"...\",\"zh\":\"...\",\"pos\":\"...\",\"gid\":0}]}（gid 可省略，默认为 0）。"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps({"english": english, "chinese": chinese}, ensure_ascii=False)},
    ]
    content = ds._chat(key, messages, base_url=proxy['base_url'], model=proxy['model'], raise_on_error=True)
    if not content:
        return None
    obj, err = extract_json(content)
    if not obj or not isinstance(obj.get('units'), list):
        return None
    raw = []
    for u in obj['units']:
        en = (u.get('en') or '').strip()
        if not en:
            continue
        raw.append({'en': en, 'pos': (u.get('pos') or 'OTHER').upper(),
                    'zh': (u.get('zh') or '').strip(), 'gid': u.get('gid', 0)})
    if not raw:
        return None
    return {'units': _assign_alignment_colors(raw)}


def alignment_or_empty(english, chinese, user):
    """生成对齐；任何异常都降级为空 dict（不影响导入流程）。"""
    try:
        return generate_alignment(english, chinese, user) or {}
    except Exception:
        return {}


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
            status = 'start'   # 复议重锁课程仍可进入重学被锁步骤
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
    # 人工复议重锁状态（学生端进入课程时用于拦截）
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
        'alignment': s.alignment or {},
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
    mode = data.get('mode') or 'en2zh'
    if not word or not answer:
        return jsonify(correct=False, reason='请填写单词与你的翻译')
    # 音译中 / 英译中 都要求写出中文意思，先做硬性校验，避免 AI 把英文单词误判为正确
    if mode in ('audio2zh', 'en2zh'):
        ans_norm = re.sub(r'[\s\u3000]+', '', answer).lower()
        word_norm = re.sub(r'[\s\u3000]+', '', word).lower()
        if ans_norm == word_norm:
            return jsonify(correct=False, reason='请写出中文意思，不要直接写英文单词')
        if not re.search(r'[\u4e00-\u9fff]', answer):
            return jsonify(correct=False, reason='请用中文写出该单词的意思')
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


@app.route('/api/v1/step/word-unknown', methods=['POST'])
@jwt_required()
def word_unknown():
    """Step7 单词巩固：学生点「不会」时，直接返回该词标准中文释义并加入生词表。"""
    u = current_user()
    data = request.get_json(silent=True) or {}
    word = (data.get('word') or '').strip()
    if not word:
        return jsonify(error='缺少单词'), 400
    key = resolve_api_key(u)
    proxy = get_ai_proxy()
    meaning = ''
    if key:
        messages = [
            {"role": "system", "content": "你是严谨的词典。给出英文单词的标准中文释义，只输出 JSON：{\"meaning\": \"中文释义\"}，不要任何多余解释。"},
            {"role": "user", "content": json.dumps({"word": word}, ensure_ascii=False)},
        ]
        content = ds._chat(key, messages, base_url=proxy['base_url'], model=proxy['model'])
        if content:
            obj, _ = extract_json(content)
            if obj:
                meaning = (obj.get('meaning') or '').strip()
    added = False
    try:
        added = WordDataManager().add_error_word(u.username, word)
    except Exception:
        added = False
    return jsonify(meaning=meaning, added=added)


# ---------------- 学生端消息通知（登录进首页后弹窗）----------------
def _notif_after(t, since):
    """t 为事件时间；since 为已读时间点。since 为空表示首次（全部展示）。"""
    if t is None:
        return False
    return since is None or t > since


@app.route('/api/v1/notifications', methods=['GET'])
@jwt_required()
def list_notifications():
    u = current_user()
    if u.role != 'student':
        return jsonify(notifications=[])
    since = u.last_notified_at
    items = []
    # 1) 金币奖励 / 扣除（管理员操作）
    for t in CoinTransaction.query.filter_by(user_id=u.id) \
            .filter(CoinTransaction.category.in_(['reward', 'penalty'])).all():
        if not _notif_after(t.created_at, since):
            continue
        sign = '+' if (t.amount or 0) >= 0 else '-'
        items.append({'type': 'coin', 'icon': '🪙',
                      'title': f'金币{sign}{abs(t.amount or 0)}',
                      'text': t.reason or '', 'time': t.created_at.isoformat() if t.created_at else ''})
    # 2) 商品发货 / 交易完成 / 退款
    for o in PurchaseOrder.query.filter_by(student_id=u.id) \
            .filter(PurchaseOrder.status.in_(['shipped', 'completed', 'rejected'])).all():
        t = o.shipped_at or o.completed_at or o.created_at
        if not _notif_after(t, since):
            continue
        m = {'shipped': '商品已发货', 'completed': '订单交易完成', 'rejected': '订单已退款'}[o.status]
        items.append({'type': 'order', 'icon': '📦', 'title': m,
                      'text': o.admin_note or '', 'time': (t.isoformat() if t else '')})
    # 3) 许愿审批 / 实现
    for w in Wish.query.filter_by(student_id=u.id) \
            .filter(Wish.status.in_(['approved', 'rejected', 'completed'])).all():
        t = w.resolved_at or w.completed_at or w.created_at
        if not _notif_after(t, since):
            continue
        m = {'approved': '许愿已通过', 'rejected': '许愿未通过', 'completed': '许愿已实现'}[w.status]
        items.append({'type': 'wish', 'icon': '🌟', 'title': m,
                      'text': w.admin_reply or w.content or '', 'time': (t.isoformat() if t else '')})
    # 4) 人工复核结果
    for a in Appeal.query.filter_by(student_id=u.id) \
            .filter(Appeal.status.in_(['approved', 'rejected'])).all():
        t = a.resolved_at or a.created_at
        if not _notif_after(t, since):
            continue
        m = {'approved': '人工复核通过', 'rejected': '人工复核被驳回'}[a.status]
        items.append({'type': 'appeal', 'icon': '⚖️', 'title': m,
                      'text': a.admin_note or '', 'time': (t.isoformat() if t else '')})
    items.sort(key=lambda x: x['time'], reverse=True)
    return jsonify(notifications=items)


@app.route('/api/v1/notifications/read', methods=['POST'])
@jwt_required()
def mark_notifications_read():
    u = current_user()
    u.last_notified_at = models.utcnow()
    db.session.commit()
    return jsonify(ok=True)


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
        # 该步是否存在待审复议：存在则先暂扣本步奖励，待管理员裁决后补发
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
                awards.append('复议待审·奖励暂扣')
            else:
                add_coins(u.id, 1, f'Step{step}通关奖励', category='study')
                awards.append('Step通关 +1')
                # 首次完美：必须一次性全对（无重做）才发放奖励
                if perfect and step not in (a.perfect_steps or []):
                    a.perfect_steps = (a.perfect_steps or []) + [step]
                    u.total_perfect_steps = (u.total_perfect_steps or 0) + 1
                    add_coins(u.id, 3, f'Step{step}完美通关奖励', category='study')
                    awards.append('完美通关 +3')
        # 若该步已无待审复议，解除课程重锁（重学通过）
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


# ---------------- 人工复议（学生申请 / 管理员裁决） ----------------
APPEAL_COST = 2   # 每次人工复议消耗金币


@app.route('/api/v1/step/appeal', methods=['POST'])
@jwt_required()
def step_appeal():
    """学生对系统判错的题目申请人工复议（花费 2 金币）。

    题目暂记为"默认通过"以便继续；奖励在 step_finish 时若该步存在待审复议则暂扣，
    待管理员裁决后再补发（通过）或永久扣留（驳回）。
    """
    u = current_user()
    data = request.get_json(silent=True) or {}
    sentence_id = data.get('sentence_id')
    step = int(data.get('step', 0))
    user_input = (data.get('user_input') or '').strip()
    standard_answer = (data.get('standard_answer') or '').strip()
    if step not in (2, 3, 5, 6, 7):
        return jsonify(error='该步骤不支持人工复议'), 400
    s = Sentence.query.get(sentence_id) if sentence_id else None
    course_id = s.course_id if s else data.get('course_id')
    if not course_id:
        return jsonify(error='缺少课程信息'), 400
    # 防重复：同一题目同一学生同一待审复议不再扣费
    dup = Appeal.query.filter_by(student_id=u.id, course_id=course_id, step=step, status='pending')
    dup = dup.filter_by(sentence_id=sentence_id) if sentence_id else dup.filter(Appeal.sentence_id.is_(None))
    if dup.first():
        return jsonify(error='该题目已申请复议，等待审核中', already=True)
    if (u.coin_balance or 0) < APPEAL_COST:
        return jsonify(error=f'金币不足，无法申请人工复议（需 {APPEAL_COST} 金币）'), 400
    add_coins(u.id, -APPEAL_COST, f'申请人工复议（Step{step}）', category='appeal')
    db.session.add(Appeal(student_id=u.id, course_id=course_id, step=step,
                          sentence_id=sentence_id, student_answer=user_input,
                          standard_answer=standard_answer, status='pending'))
    db.session.commit()
    return jsonify(ok=True, cost=APPEAL_COST, balance=u.coin_balance,
                   message=f'已申请人工复议，扣除 {APPEAL_COST} 金币，等待管理员审核')


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
        return jsonify(error='该复议已处理'), 400
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
        add_coins(a.student_id, APPEAL_COST, '人工复议通过·返还金币', category='appeal', operator_id=u.id)
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
                add_coins(a.student_id, 1, f'Step{a.step}通关奖励（复议通过补发）', category='study', operator_id=u.id)
                bonus_amt += 1
                if perf_map.get(str(a.step)) and a.step not in (asm.perfect_steps or []):
                    asm.perfect_steps = (asm.perfect_steps or []) + [a.step]
                    stu.total_perfect_steps = (stu.total_perfect_steps or 0) + 1
                    add_coins(a.student_id, 3, f'Step{a.step}完美通关奖励（复议通过补发）', category='study', operator_id=u.id)
                    bonus_amt += 3
                supp = [x for x in supp if x != a.step]
                asm.appeal_suppressed = supp
                perf_map.pop(str(a.step), None)
                asm.appeal_suppressed_perfect = perf_map
            # 此步已无待审复议：解除课程重锁（若因本步驳回而上锁）
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
    return jsonify(base_url=p['base_url'], model=p['model'], api_key_set=bool(p['api_key']))


@app.route('/api/v1/admin/ai-proxy', methods=['POST'])
@admin_only
def save_ai_proxy_config():
    data = request.get_json(silent=True) or {}
    base_url = (data.get('base_url') or '').strip()
    model = (data.get('model') or '').strip()
    api_key = (data.get('api_key') or '').strip()
    if not base_url:
        return jsonify(error='Base URL 不能为空'), 400
    if not model:
        return jsonify(error='模型名不能为空'), 400
    # 兼容用户可能把 chat/completions 后缀一起填进来
    if base_url.rstrip('/').endswith('/chat/completions'):
        base_url = base_url.rstrip('/')[: -len('/chat/completions')]
    cfg = {'base_url': base_url, 'model': model}
    if api_key:
        cfg['api_key'] = api_key
    set_setting('ai_proxy', cfg)
    return jsonify(message='AI 代理已保存', base_url=base_url, model=model, api_key_set=bool(api_key))


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
                chinese_keywords=s.get('chinese_keywords') or [],
                alignment=alignment_or_empty(s['english'], s['chinese'], u)))
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
        aligned = sum(1 for s in sents
                      if isinstance(s.alignment, dict) and s.alignment.get('units'))
        out.append({
            'id': c.id, 'title': c.title, 'is_published': c.is_published,
            'sentence_count': total, 'audio_count': audio,
            'missing_audio': missing, 'has_error': has_error,
            'aligned_count': aligned,
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
    missing_alignment = [s.sentence_order for s in sents
                         if not (isinstance(s.alignment, dict) and s.alignment.get('units'))]
    return jsonify(course_id=course_id, title=c.title, total=len(sents),
                   missing_audio=missing_audio, missing_fields=missing_fields,
                   missing_alignment=missing_alignment,
                   has_error=(len(missing_fields) > 0),
                   has_alignment_issue=(len(missing_alignment) > 0))


# ---------------- 课程单词库管理（v0.5 Step7 单词巩固 / v2.0 全文单词+音标+释义） ----------------
@app.route('/api/v1/admin/course/<int:course_id>/extract-words', methods=['POST'])
@admin_only
def admin_extract_words(course_id):
    """一键提取课程实词并存入 course_words（保留管理员手动添加的词）。
    旧版 v1.0 兼容：仅提取实词，按字母排序，供 Step7 使用。
    """
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


@app.route('/api/v1/admin/course/<int:course_id>/extract-all-words', methods=['POST'])
@admin_only
def admin_extract_all_words_v2(course_id):
    """v2.0: 提取全文全部单词（含虚词），按文中出现顺序，去重。
    保留 is_custom=True 的词，仅替换自动提取部分。
    """
    c = Course.query.get_or_404(course_id)
    words = extract_all_course_words(c)
    custom = {w.word for w in CourseWord.query.filter_by(course_id=course_id, is_custom=True)}
    CourseWord.query.filter_by(course_id=course_id, is_custom=False).delete()
    for w in words:
        if w in custom:
            continue
        db.session.add(CourseWord(course_id=course_id, word=w, is_custom=False))
    db.session.commit()
    out = CourseWord.query.filter_by(course_id=course_id).all()
    # 按文中出现顺序排序
    order = {w: i for i, w in enumerate(words)}
    out.sort(key=lambda x: order.get(x.word, 9999))
    return jsonify(message='已提取全文单词（含虚词）', count=len(out),
                   words=[{'id': w.id, 'word': w.word, 'is_custom': w.is_custom,
                           'meaning': w.meaning or '', 'phonetic': w.phonetic or ''} for w in out])


@app.route('/api/v1/admin/course/<int:course_id>/generate-phonetics', methods=['POST'])
@admin_only
def admin_generate_phonetics(course_id):
    """v2.0: 为课程所有单词批量生成音标（已有音标的跳过）。"""
    c = Course.query.get_or_404(course_id)
    words = CourseWord.query.filter_by(course_id=course_id).all()
    generated = 0
    details = []
    for w in words:
        if w.phonetic:
            continue
        ph, src = _generate_phonetic(w.word)
        if ph:
            w.phonetic = ph
            generated += 1
            details.append({'word': w.word, 'phonetic': ph, 'source': src})
    db.session.commit()
    return jsonify(message=f'已生成 {generated}/{len(words)} 个音标', generated=generated, details=details)


@app.route('/api/v1/admin/course/<int:course_id>/generate-meanings', methods=['POST'])
@admin_only
def admin_generate_meanings(course_id):
    """v2.0: 为课程所有单词批量生成中文释义（已有释义的跳过）。
    AI 传入文章全文上下文确保释义准确。
    """
    c = Course.query.get_or_404(course_id)
    # 构建上下文：full_text 或拼接所有句子
    if c.full_text:
        context = c.full_text
    else:
        sents = Sentence.query.filter_by(course_id=course_id).order_by(Sentence.sentence_order).all()
        context = ' '.join(s.english for s in sents if s.english)
    words = CourseWord.query.filter_by(course_id=course_id).all()
    generated = 0
    details = []
    for w in words:
        if w.meaning:
            continue
        meaning, src = _generate_meaning(w.word, context)
        if meaning:
            w.meaning = meaning
            generated += 1
            details.append({'word': w.word, 'meaning': meaning})
    db.session.commit()
    return jsonify(message=f'已生成 {generated}/{len(words)} 个释义', generated=generated, details=details)


# ---------------- Step1 词色对齐：管理员手动生成 / 全量回填 ----------------
# ---------------- 词色标注：后台任务队列（串行，避免并发写库冲突） ----------------
# 连续点击多个课程的「生成词色标注」时，若并行写同一个 SQLite 文件会触发
# "database is locked" 导致 500、前端只拿到裸「生成失败」。改用单 worker 串行处理：
# 接口只把任务入队并立即返回，worker 线程依次执行，前端轮询 /admin/align-status 看进度与失败原因。
_align_queue = queue.Queue()
_align_worker = None
_align_worker_lock = threading.Lock()
align_status = {
    'running': False,        # 是否有任务在跑
    'course': None,          # 当前任务课程名（'全部课程' 表示全量）
    'done': 0,               # 已完成句数
    'total': 0,              # 总句数
    'enqueued': 0,           # 队列中等待的任务数
    'last_result': None,     # 最近一次完成的任务结果 {ok, message, done, failed, errors} 或 {ok:False, error}
}


def _align_worker_thread():
    with app.app_context():
        while True:
            job = _align_queue.get()
            try:
                _process_align_job(job)
            except Exception as e:
                align_status['running'] = False
                align_status['last_result'] = {
                    'ok': False,
                    'error': f'{type(e).__name__}: {e}'
                }
            finally:
                _align_queue.task_done()


def ensure_align_worker():
    """懒启动单例 worker 线程（守护线程，随进程退出）。"""
    global _align_worker
    with _align_worker_lock:
        if _align_worker is None or not _align_worker.is_alive():
            _align_worker = threading.Thread(target=_align_worker_thread, daemon=True)
            _align_worker.start()


def _process_align_job(job):
    """在 worker 线程中串行执行单个标注任务（课程 or 全量）。真实异常会被记录进 errors。"""
    u = User.query.get(job['user_id'])
    if not u:
        align_status['running'] = False
        align_status['last_result'] = {'ok': False, 'error': '用户不存在'}
        return
    if not resolve_api_key(u):
        align_status['running'] = False
        align_status['last_result'] = {
            'ok': False,
            'error': '未配置 AI 模型，无法生成词色标注（请在管理员「AI 模型设置」中填写 API Key）'
        }
        return

    is_all = job['type'] == 'all'
    if is_all:
        courses = Course.query.all()
        align_status['course'] = '全部课程'
    else:
        c = Course.query.get(job['course_id'])
        if not c:
            align_status['running'] = False
            align_status['last_result'] = {'ok': False, 'error': '课程不存在'}
            return
        courses = [c]
        align_status['course'] = c.title

    # 先统计总句数用于进度展示
    total = 0
    for cc in courses:
        for s in Sentence.query.filter_by(course_id=cc.id).order_by(Sentence.sentence_order).all():
            if s.english and s.chinese:
                total += 1
    align_status['total'] = total
    align_status['done'] = 0
    align_status['running'] = True

    done, errors = 0, []
    for cc in courses:
        for s in Sentence.query.filter_by(course_id=cc.id).order_by(Sentence.sentence_order).all():
            if not s.english or not s.chinese:
                continue
            try:
                aligned = generate_alignment(s.english, s.chinese, u)
            except Exception as e:
                # 透传 _chat 抛出的真实异常（Timeout/429/401/ConnectionError 等）
                errors.append({'course': cc.title, 'order': s.sentence_order,
                               'english': s.english, 'error': f'{type(e).__name__}: {e}'})
                continue
            if not aligned:
                errors.append({'course': cc.title, 'order': s.sentence_order,
                               'english': s.english, 'error': 'AI 未返回有效标注（结果无法解析为 JSON）'})
                continue
            s.alignment = aligned
            done += 1
            align_status['done'] = done
    try:
        db.session.commit()
    except Exception as e:
        align_status['running'] = False
        align_status['last_result'] = {
            'ok': False,
            'error': f'数据库写入失败（{type(e).__name__}）：{e}'
        }
        return
    msg = (f'已为《{align_status["course"]}》生成 {done} 句词色标注'
           if not is_all else f'已全量生成 {done} 句词色标注') + (f'，{len(errors)} 句失败' if errors else '')
    align_status['running'] = False
    align_status['last_result'] = {
        'ok': True, 'message': msg, 'done': done, 'failed': len(errors), 'errors': errors
    }


@app.route('/api/v1/admin/course/<int:course_id>/align', methods=['POST'])
@admin_only
def admin_align_course(course_id):
    """为某课程所有句子一次性生成词色对齐（覆盖已有 alignment）。

    接口只负责把任务入队并立即返回，真正的生成由后台单 worker 串行执行，
    避免连续点击多个课程时并发写 SQLite 触发 "database is locked" 而 500。
    前端通过 GET /admin/align-status 轮询进度与失败原因。
    """
    u = current_user()
    if not resolve_api_key(u):
        return jsonify(error='未配置 AI 模型，无法生成词色标注（请在管理员「AI 模型设置」中填写 API Key）'), 400
    if not Course.query.get(course_id):
        return jsonify(error='课程不存在'), 404
    ensure_align_worker()
    _align_queue.put({'type': 'course', 'course_id': course_id, 'user_id': u.id})
    pos = _align_queue.qsize()
    align_status['enqueued'] = pos
    return jsonify(queued=True, position=pos,
                   message=f'已加入生成队列（排第 {pos} 位，后台将依次处理）')


@app.route('/api/v1/admin/align-all', methods=['POST'])
@admin_only
def admin_align_all():
    """全量回填：为所有课程的句子生成词色对齐（处理存量课程）。任务入队，串行执行。"""
    u = current_user()
    if not resolve_api_key(u):
        return jsonify(error='未配置 AI 模型，无法生成词色标注（请在管理员「AI 模型设置」中填写 API Key）'), 400
    ensure_align_worker()
    _align_queue.put({'type': 'all', 'user_id': u.id})
    pos = _align_queue.qsize()
    align_status['enqueued'] = pos
    return jsonify(queued=True, position=pos,
                   message=f'已加入全量生成队列（排第 {pos} 位，后台将依次处理）')


@app.route('/api/v1/admin/align-status', methods=['GET'])
@admin_only
def admin_align_status():
    """返回当前词色标注任务队列状态：是否在跑、进度、最近一次结果（含失败原因）。"""
    return jsonify(**align_status)


@app.route('/api/v1/admin/course/<int:course_id>/sentences', methods=['GET'])
@admin_only
def admin_course_sentences(course_id):
    """返回课程下所有句子（含 alignment），供词色标注校对编辑器使用。"""
    c = Course.query.get_or_404(course_id)
    sents = Sentence.query.filter_by(course_id=course_id).order_by(Sentence.sentence_order).all()
    return jsonify(title=c.title, sentences=[serialize_sentence(s) for s in sents])


@app.route('/api/v1/admin/sentence/<int:sentence_id>/alignment', methods=['PUT'])
@admin_only
def admin_update_sentence_alignment(sentence_id):
    """人工校对：覆盖单句的词色对齐 units（英文片段/中文对应/是否上色/同色组 gid）。"""
    s = Sentence.query.get_or_404(sentence_id)
    data = request.get_json(silent=True) or {}
    units = data.get('units')
    if not isinstance(units, list):
        return jsonify(error='units 必须是数组'), 400
    raw = []
    for it in units:
        if not isinstance(it, dict):
            continue
        en = str(it.get('en') or '').strip()
        if not en:
            continue
        raw.append({'en': en, 'pos': str(it.get('pos') or '').upper(),
                    'zh': str(it.get('zh') or '').strip(),
                    'content': it.get('content'), 'gid': it.get('gid', 0)})
    if not raw:
        return jsonify(error='对齐不能为空（至少保留一个英文片段）'), 400
    s.alignment = {'units': _assign_alignment_colors(raw)}
    db.session.commit()
    return jsonify(message='已保存', alignment=s.alignment)


@app.route('/api/v1/admin/course/<int:course_id>/words', methods=['GET'])
@admin_only
def admin_list_words(course_id):
    Course.query.get_or_404(course_id)
    out = CourseWord.query.filter_by(course_id=course_id).order_by(CourseWord.word).all()
    return jsonify(words=[{'id': w.id, 'word': w.word, 'is_custom': w.is_custom,
                           'meaning': w.meaning or '', 'phonetic': w.phonetic or ''} for w in out])


@app.route('/api/v1/admin/course/<int:course_id>/word', methods=['POST'])
@admin_only
def admin_add_word(course_id):
    Course.query.get_or_404(course_id)
    data = request.get_json(silent=True) or {}
    word = data.get('word', '').strip().lower()
    if not word:
        return jsonify(error='单词不能为空'), 400
    if CourseWord.query.filter_by(course_id=course_id, word=word).first():
        return jsonify(error='单词已存在'), 400
    w = CourseWord(course_id=course_id, word=word, is_custom=True,
                   meaning=data.get('meaning', '').strip() or None,
                   phonetic=data.get('phonetic', '').strip() or None)
    db.session.add(w)
    db.session.commit()
    return jsonify(message='已添加单词', id=w.id, word=w.word, is_custom=True,
                   meaning=w.meaning or '', phonetic=w.phonetic or '')


@app.route('/api/v1/admin/course/<int:course_id>/word/<int:word_id>', methods=['PUT'])
@admin_only
def admin_update_word(course_id, word_id):
    """v2.0: 修改单词的释义和音标（管理员编辑）。"""
    w = CourseWord.query.get_or_404(word_id)
    if w.course_id != course_id:
        return jsonify(error='单词与课程不匹配'), 400
    data = request.get_json(silent=True) or {}
    if 'meaning' in data:
        w.meaning = data['meaning'].strip() or None
    if 'phonetic' in data:
        w.phonetic = data['phonetic'].strip() or None
    db.session.commit()
    return jsonify(message='已更新', id=w.id, word=w.word,
                   meaning=w.meaning or '', phonetic=w.phonetic or '')


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
    u = current_user()
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
                chinese_keywords=s.get('chinese_keywords') or [],
                alignment=alignment_or_empty(s.get('english', ''), s.get('chinese', ''), u)))
    db.session.commit()
    return jsonify(message='课程已更新')


# ============================================================
# 听力大师（v2.0）课程方案管理 API
# ============================================================

@app.route('/api/v1/admin/schemes', methods=['GET'])
@admin_only
def admin_list_schemes():
    """列出所有课程方案（含学生数和课程数统计）。"""
    schemes = CourseScheme.query.order_by(CourseScheme.created_at.desc()).all()
    out = []
    for s in schemes:
        item_count = CourseSchemeItem.query.filter_by(scheme_id=s.id).count()
        student_count = CourseSchemeStudent.query.filter_by(scheme_id=s.id).count()
        out.append({
            'id': s.id, 'name': s.name, 'description': s.description or '',
            'max_errors_before_fallback': s.max_errors_before_fallback,
            'cooldown_minutes': s.cooldown_minutes,
            'is_active': s.is_active,
            'item_count': item_count, 'student_count': student_count,
            'created_at': str(s.created_at),
        })
    return jsonify(schemes=out)


@app.route('/api/v1/admin/schemes', methods=['POST'])
@admin_only
def admin_create_scheme():
    """新建课程方案。"""
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify(error='方案名称不能为空'), 400
    s = CourseScheme(
        name=name,
        description=(data.get('description') or '').strip() or None,
        max_errors_before_fallback=data.get('max_errors_before_fallback', 10),
        cooldown_minutes=data.get('cooldown_minutes', 5),
        is_active=data.get('is_active', False),
    )
    db.session.add(s)
    db.session.commit()
    return jsonify(message='方案已创建', id=s.id, name=s.name)


@app.route('/api/v1/admin/scheme/<int:scheme_id>', methods=['PUT'])
@admin_only
def admin_update_scheme(scheme_id):
    """编辑课程方案。"""
    s = CourseScheme.query.get_or_404(scheme_id)
    data = request.get_json(silent=True) or {}
    if 'name' in data:
        s.name = data['name'].strip()
    if 'description' in data:
        s.description = data['description'].strip() or None
    if 'max_errors_before_fallback' in data:
        s.max_errors_before_fallback = int(data['max_errors_before_fallback'])
    if 'cooldown_minutes' in data:
        s.cooldown_minutes = int(data['cooldown_minutes'])
    if 'is_active' in data:
        s.is_active = bool(data['is_active'])
    db.session.commit()
    return jsonify(message='方案已更新', id=s.id)


@app.route('/api/v1/admin/scheme/<int:scheme_id>', methods=['DELETE'])
@admin_only
def admin_delete_scheme(scheme_id):
    """删除课程方案（同时删除关联的 items/students/assignments）。"""
    s = CourseScheme.query.get_or_404(scheme_id)
    CourseSchemeItem.query.filter_by(scheme_id=scheme_id).delete()
    CourseSchemeStudent.query.filter_by(scheme_id=scheme_id).delete()
    SchemeAssignment.query.filter_by(scheme_id=scheme_id).delete()
    db.session.delete(s)
    db.session.commit()
    return jsonify(message='方案已删除')


# ---- 方案项目（每个课程启用哪些步骤） ----

@app.route('/api/v1/admin/scheme/<int:scheme_id>/items', methods=['GET'])
@admin_only
def admin_get_scheme_items(scheme_id):
    """获取方案下所有课程步骤配置（按 order_index 排序）。"""
    CourseScheme.query.get_or_404(scheme_id)
    items = (CourseSchemeItem.query
             .filter_by(scheme_id=scheme_id)
             .order_by(CourseSchemeItem.order_index).all())
    out = []
    for it in items:
        c = Course.query.get(it.course_id)
        out.append({
            'id': it.id,
            'course_id': it.course_id,
            'course_title': c.title if c else '(已删除)',
            'order_index': it.order_index,
            'steps': it.steps or [],
        })
    return jsonify(items=out)


@app.route('/api/v1/admin/scheme/<int:scheme_id>/items', methods=['POST'])
@admin_only
def admin_save_scheme_items(scheme_id):
    """全量替换方案内的课程步骤配置。
    请求体: { items: [{course_id, order_index, steps:[1,2,3]}, ...] }
    """
    CourseScheme.query.get_or_404(scheme_id)
    data = request.get_json(silent=True) or {}
    raw = data.get('items', [])
    # 删除旧配置
    CourseSchemeItem.query.filter_by(scheme_id=scheme_id).delete()
    for it in raw:
        course_id = int(it.get('course_id', 0))
        if not course_id:
            continue
        steps = it.get('steps', [])
        if isinstance(steps, list):
            steps = [int(s) for s in steps if int(s) in (1, 2, 3, 4)]
        else:
            steps = []
        db.session.add(CourseSchemeItem(
            scheme_id=scheme_id,
            course_id=course_id,
            order_index=int(it.get('order_index', 0)) or course_id,
            steps=steps,
        ))
    db.session.commit()
    return jsonify(message='步骤配置已保存')


# ---- 方案学生 ----

@app.route('/api/v1/admin/scheme/<int:scheme_id>/students', methods=['GET'])
@admin_only
def admin_get_scheme_students(scheme_id):
    """获取方案下分配的学生列表。"""
    CourseScheme.query.get_or_404(scheme_id)
    rows = (CourseSchemeStudent.query
            .filter_by(scheme_id=scheme_id).all())
    out = []
    for r in rows:
        u = User.query.get(r.student_id)
        out.append({
            'id': r.id,
            'student_id': r.student_id,
            'username': u.username if u else '?',
        })
    return jsonify(students=out)


@app.route('/api/v1/admin/scheme/<int:scheme_id>/students', methods=['POST'])
@admin_only
def admin_save_scheme_students(scheme_id):
    """全量替换方案分配的学生（传入学生ID列表）。
    请求体: { student_ids: [1,2,3] }
    """
    CourseScheme.query.get_or_404(scheme_id)
    data = request.get_json(silent=True) or {}
    ids = data.get('student_ids', [])
    CourseSchemeStudent.query.filter_by(scheme_id=scheme_id).delete()
    for sid in ids:
        db.session.add(CourseSchemeStudent(
            scheme_id=scheme_id,
            student_id=int(sid),
        ))
    db.session.commit()
    return jsonify(message='学生分配已保存', count=len(ids))


# ---- 推送方案到学生 ----

@app.route('/api/v1/admin/scheme/<int:scheme_id>/push', methods=['POST'])
@admin_only
def admin_push_scheme(scheme_id):
    """将方案推送给已分配的学生（创建 SchemeAssignment 记录）。
    已推送过的学生/课程组合会被跳过（幂等）。
    请求体: { student_ids: [1,2] }  可选，默认推送方案下所有学生。
    """
    s = CourseScheme.query.get_or_404(scheme_id)
    data = request.get_json(silent=True) or {}
    # 方案下的课程配置
    items = CourseSchemeItem.query.filter_by(scheme_id=scheme_id).order_by(
        CourseSchemeItem.order_index).all()
    if not items:
        return jsonify(error='方案中没有配置任何课程，请先设置步骤'), 400

    # 确定学生范围
    student_ids = data.get('student_ids')
    if student_ids:
        students = CourseSchemeStudent.query.filter(
            CourseSchemeStudent.scheme_id == scheme_id,
            CourseSchemeStudent.student_id.in_([int(sid) for sid in student_ids]),
        ).all()
    else:
        students = CourseSchemeStudent.query.filter_by(scheme_id=scheme_id).all()

    if not students:
        return jsonify(error='没有分配学生，请先选学生'), 400

    pushed = 0
    skipped = 0
    for st in students:
        for it in items:
            # 检查是否已推送
            existing = SchemeAssignment.query.filter_by(
                scheme_id=scheme_id,
                student_id=st.student_id,
                course_id=it.course_id,
            ).first()
            if existing:
                skipped += 1
                continue
            # 创建进度记录
            step_unlocks = {}
            for step_num in (1, 2, 3, 4):
                step_unlocks[str(step_num)] = step_num in (it.steps or [])
            # 第一个启用的步骤作为 current_step
            current_step = (it.steps or [1])[0] if it.steps else 1
            db.session.add(SchemeAssignment(
                scheme_id=scheme_id,
                student_id=st.student_id,
                course_id=it.course_id,
                current_step=current_step,
                step_unlocks=step_unlocks,
                completed_steps=[],
                perfect_steps=[],
                step_error_counts={},
                step_entered_at={},
                step_fallen_back={},
            ))
            pushed += 1
    db.session.commit()
    return jsonify(message=f'已推送 {pushed} 门课程（跳过 {skipped} 门已推送的）',
                   pushed=pushed, skipped=skipped)


# ---- 查看学生方案进度 ----

@app.route('/api/v1/admin/scheme/<int:scheme_id>/assignments', methods=['GET'])
@admin_only
def admin_scheme_assignments(scheme_id):
    """按学生分组查看方案进度。返回每个学生每门课的完成状态。"""
    CourseScheme.query.get_or_404(scheme_id)
    # 获取方案下所有课程（按 order_index 排序）
    items = (CourseSchemeItem.query
             .filter_by(scheme_id=scheme_id)
             .order_by(CourseSchemeItem.order_index).all())
    course_map = {}
    for it in items:
        c = Course.query.get(it.course_id)
        course_map[it.course_id] = {
            'course_id': it.course_id,
            'title': c.title if c else '(已删除)',
            'order_index': it.order_index,
            'steps': it.steps or [],
        }

    # 获取方案下所有学生
    students = CourseSchemeStudent.query.filter_by(scheme_id=scheme_id).all()
    student_map = {}
    for st in students:
        u = User.query.get(st.student_id)
        student_map[st.student_id] = {
            'student_id': st.student_id,
            'username': u.username if u else '?',
            'courses': {},
        }

    # 获取所有进度记录
    assignments = SchemeAssignment.query.filter_by(scheme_id=scheme_id).all()
    for a in assignments:
        if a.student_id in student_map:
            student_map[a.student_id]['courses'][a.course_id] = {
                'course_id': a.course_id,
                'current_step': a.current_step,
                'step_unlocks': a.step_unlocks or {},
                'completed_steps': a.completed_steps or [],
                'perfect_steps': a.perfect_steps or [],
                'is_completed': a.is_completed,
                'step_error_counts': a.step_error_counts or {},
            }

    return jsonify(
        scheme_name=CourseScheme.query.get(scheme_id).name,
        courses=sorted(course_map.values(), key=lambda x: x['order_index']),
        students=sorted(student_map.values(), key=lambda x: x['username']),
    )


# ============================================================
# 听力大师（v2.0）学生端学习 API
# ============================================================

def _get_scheme_assignment(student_id, course_id):
    """获取学生某课程的最新有效方案分配（取最近推送的活跃方案）。"""
    return (SchemeAssignment.query
            .filter_by(student_id=student_id, course_id=course_id)
            .join(CourseScheme, CourseScheme.id == SchemeAssignment.scheme_id)
            .filter(CourseScheme.is_active == True)
            .order_by(SchemeAssignment.assigned_at.desc())
            .first())


def _get_scheme(student_id):
    """获取学生所属的活跃课程方案。"""
    return (CourseScheme.query
            .join(CourseSchemeStudent, CourseSchemeStudent.scheme_id == CourseScheme.id)
            .filter(CourseSchemeStudent.student_id == student_id, CourseScheme.is_active == True)
            .first())


def _check_cooldown(assignment, target_step):
    """检查回退冷却：从回退发生到再次进入目标步的间隔是否 < cooldown_minutes。"""
    scheme = CourseScheme.query.get(assignment.scheme_id)
    if not scheme or not scheme.cooldown_minutes:
        return None  # 无冷却配置
    fb = assignment.step_fallen_back or {}
    # 只有当目标步曾被回退过，才检查冷却
    if not fb.get(str(target_step)):
        return None
    entered = assignment.step_entered_at or {}
    # 回退时记录的 entered_at[prev_step] 就是回退发生时间（离开目标步的时间）
    # 从所有 entered_at 中找到最晚的非目标步时间（即最近一次回退时间）
    last_fallback_time = None
    for k, v in entered.items():
        if k == str(target_step):
            continue
        try:
            t = datetime.datetime.fromisoformat(v)
            if last_fallback_time is None or t > last_fallback_time:
                last_fallback_time = t
        except Exception:
            pass
    if not last_fallback_time:
        return None
    delta = (datetime.datetime.utcnow() - last_fallback_time).total_seconds() / 60.0
    if delta < scheme.cooldown_minutes:
        w = int(scheme.cooldown_minutes - delta + 1)
        return f'冷却中，还需等待约 {w} 分钟才能进入（防止刷答案）'
    return None


@app.route('/api/v1/scheme/my', methods=['GET'])
@jwt_required()
def scheme_my_courses():
    """学生查看听力大师中分配的课程列表（按方案 order_index 排序）。"""
    u = current_user()
    scheme = _get_scheme(u.id)
    if not scheme:
        return jsonify(has_scheme=False, courses=[], message='尚未分配听力大师课程方案')
    # 获取方案下的课程（按 order_index）
    items = (CourseSchemeItem.query
             .filter_by(scheme_id=scheme.id)
             .order_by(CourseSchemeItem.order_index).all())
    courses_out = []
    for it in items:
        c = Course.query.get(it.course_id)
        if not c:
            continue
        asm = SchemeAssignment.query.filter_by(
            scheme_id=scheme.id, student_id=u.id, course_id=it.course_id).first()
        # 解锁规则：按顺序，前一个必须完成才能解锁下一个
        unlocked = True
        prev_item_idx = None
        for idx, pi in enumerate(items):
            if pi.course_id == it.course_id:
                prev_item_idx = idx
                break
        if prev_item_idx is not None and prev_item_idx > 0:
            prev_item = items[prev_item_idx - 1]
            prev_asm = SchemeAssignment.query.filter_by(
                scheme_id=scheme.id, student_id=u.id, course_id=prev_item.course_id).first()
            if prev_asm and not prev_asm.is_completed:
                unlocked = False

        courses_out.append({
            'course_id': c.id,
            'title': c.title,
            'order_index': it.order_index,
            'steps': it.steps or [],
            'unlocked': unlocked,
            'current_step': asm.current_step if asm else (it.steps[0] if it.steps else 1),
            'completed_steps': asm.completed_steps if asm else [],
            'is_completed': asm.is_completed if asm else False,
        })
    return jsonify(has_scheme=True, scheme_name=scheme.name, courses=courses_out)


@app.route('/api/v1/scheme/learn/<int:course_id>/state', methods=['GET'])
@jwt_required()
def scheme_learn_state(course_id):
    """获取学生某课程的学习状态（用于听力大师学习页面）。"""
    u = current_user()
    asm = _get_scheme_assignment(u.id, course_id)
    if not asm:
        return jsonify(error='未分配该课程'), 404
    scheme = CourseScheme.query.get(asm.scheme_id)
    item = CourseSchemeItem.query.filter_by(
        scheme_id=asm.scheme_id, course_id=course_id).first()
    enabled_steps = item.steps if item else [1, 2, 3, 4]
    # 每个启用步骤的句子数
    sentences = Sentence.query.filter_by(course_id=course_id).order_by(
        Sentence.sentence_order).all()
    sent_count = len(sentences)
    # 回退冷却检查
    cooldown_msg = _check_cooldown(asm, asm.current_step)
    return jsonify(
        scheme_id=asm.scheme_id,
        course_id=course_id,
        current_step=asm.current_step,
        step_unlocks=asm.step_unlocks or {},
        completed_steps=asm.completed_steps or [],
        perfect_steps=asm.perfect_steps or [],
        is_completed=asm.is_completed,
        enabled_steps=enabled_steps,
        sent_count=sent_count,
        error_counts=asm.step_error_counts or {},
        max_errors=scheme.max_errors_before_fallback,
        cooldown_minutes=scheme.cooldown_minutes,
        cooldown_msg=cooldown_msg,
        appeal_locked=asm.appeal_locked,
    )


@app.route('/api/v1/scheme/learn/<int:course_id>/words', methods=['GET'])
@jwt_required()
def scheme_learn_words(course_id):
    """听力大师 Step1：获取课程全文单词（含音标/释义/出现顺序）。"""
    u = current_user()
    asm = _get_scheme_assignment(u.id, course_id)
    if not asm:
        return jsonify(error='未分配该课程'), 404
    words = (CourseWord.query
             .filter_by(course_id=course_id)
             .order_by(CourseWord.created_at).all())
    if not words:
        # 若尚未提取，自动提取全文单词
        c = Course.query.get(course_id)
        if c:
            wlist = extract_all_course_words(c)
            for w in wlist:
                db.session.add(CourseWord(course_id=course_id, word=w))
            db.session.commit()
            words = (CourseWord.query
                     .filter_by(course_id=course_id)
                     .order_by(CourseWord.created_at).all())
    return jsonify(words=[{
        'id': w.id, 'word': w.word,
        'meaning': w.meaning or '', 'phonetic': w.phonetic or '',
    } for w in words])


@app.route('/api/v1/scheme/learn/<int:course_id>/sentences', methods=['GET'])
@jwt_required()
def scheme_learn_sentences(course_id):
    """听力大师学习页：获取课程全部句子。"""
    u = current_user()
    asm = _get_scheme_assignment(u.id, course_id)
    if not asm:
        return jsonify(error='未分配该课程'), 404
    sentences = Sentence.query.filter_by(course_id=course_id).order_by(
        Sentence.sentence_order).all()
    c = Course.query.get(course_id)
    return jsonify(
        course={'id': c.id, 'title': c.title},
        sentences=[{
            'id': s.id,
            'sentence_order': s.sentence_order,
            'english': s.english,
            'chinese': s.chinese,
            'audio_url': s.audio_url or '',
        } for s in sentences],
        total_steps=len(Sentence.query.filter_by(course_id=course_id).all()),
    )


def _grade_step3(user_input, sentence, key, proxy):
    """Step3 判分：给音+义，学生写英文。
    本地英文词匹配优先，不通过用 AI 兜底。
    """
    passed, matched, total = ds.local_english_match(user_input, sentence.english)
    if passed:
        return True, {'method': 'local', 'matched': f'{matched}/{total}'}
    if key:
        ai = ds.ai_score_english(key, user_input, sentence.english, task='en',
                                 base_url=proxy['base_url'], model=proxy['model'])
        if ai is not None and ai >= 0.75:
            return True, {'method': 'ai', 'similarity': round(ai, 3)}
    return False, {'method': 'local', 'matched': f'{matched}/{total}'}


def _grade_step4a(user_input, sentence, key, proxy):
    """Step4-A 判分：纯听写（给音写形），同 Step3 但无义提示，阈值更宽。"""
    passed, matched, total = ds.local_english_match(user_input, sentence.english)
    if passed:
        return True, {'method': 'local', 'matched': f'{matched}/{total}'}
    if key:
        ai = ds.ai_score_english(key, user_input, sentence.english, task='en',
                                 base_url=proxy['base_url'], model=proxy['model'])
        if ai is not None and ai >= 0.70:
            return True, {'method': 'ai', 'similarity': round(ai, 3)}
    return False, {'method': 'local', 'matched': f'{matched}/{total}'}


def _grade_step4b(user_input, sentence, key, proxy):
    """Step4-B 判分：翻译成中文。本地字符相似度优先，不通过用 AI。"""
    local_sim = ds.local_similarity(user_input, sentence.chinese)
    if local_sim >= 0.70:
        return True, {'method': 'local', 'similarity': round(local_sim, 3)}
    if key:
        ai = ds.ai_score_chinese(key, user_input, sentence.chinese,
                                 base_url=proxy['base_url'], model=proxy['model'])
        sim = ai if ai is not None else local_sim
        if sim >= 0.75:
            return True, {'method': 'ai', 'similarity': round(sim, 3)}
    return False, {'method': 'local', 'similarity': round(local_sim, 3)}


@app.route('/api/v1/scheme/step/submit', methods=['POST'])
@jwt_required()
def scheme_step_submit():
    """听力大师答题提交（Step 3/4）。"""
    u = current_user()
    u.last_task_date = datetime.date.today()
    data = request.get_json(silent=True) or {}
    course_id = int(data.get('course_id', 0))
    sentence_id = int(data.get('sentence_id', 0))
    question_type = (data.get('question_type') or '').strip()  # step3 / step4_dictation / step4_translation
    user_input = (data.get('user_input') or '').strip()
    if not user_input:
        return jsonify(error='请输入回答'), 400

    asm = _get_scheme_assignment(u.id, course_id)
    if not asm:
        return jsonify(error='未分配该课程'), 404

    s = Sentence.query.get_or_404(sentence_id)
    scheme = CourseScheme.query.get(asm.scheme_id)
    key = resolve_api_key(u)
    proxy = get_ai_proxy()

    # 判分
    if question_type == 'step3':
        correct, detail = _grade_step3(user_input, s, key, proxy)
    elif question_type == 'step4_dictation':
        correct, detail = _grade_step4a(user_input, s, key, proxy)
    elif question_type == 'step4_translation':
        correct, detail = _grade_step4b(user_input, s, key, proxy)
    else:
        return jsonify(error='无效的 question_type'), 400

    # 更新/创建进度记录
    prog = SchemeStepProgress.query.filter_by(
        scheme_id=asm.scheme_id, student_id=u.id,
        course_id=course_id, sentence_id=sentence_id,
        question_type=question_type,
    ).first()
    if not prog:
        prog = SchemeStepProgress(
            scheme_id=asm.scheme_id, student_id=u.id,
            course_id=course_id, sentence_id=sentence_id,
            question_type=question_type,
        )
        db.session.add(prog)

    prog.attempt_count = (prog.attempt_count or 0) + 1
    prog.last_attempt_at = models.utcnow()

    # 金币计算：
    coins_earned = 0
    if correct and not prog.ever_correct:
        # 首次答对
        prog.ever_correct = True
        prog.first_correct_attempt = prog.attempt_count
        if prog.attempt_count == 1:
            coins_earned = 2  # 一次答对 2 金币
        elif prog.attempt_count == 2:
            coins_earned = 1  # 两次答对 1 金币
        # 3次及以上 0 金币
        if coins_earned > 0:
            step_label = {'step3': 'Step3', 'step4_dictation': 'Step4-听写',
                          'step4_translation': 'Step4-翻译'}.get(question_type, question_type)
            add_coins(u.id, coins_earned,
                      f'听力大师·{step_label}·第{prog.attempt_count}次答对',
                      category='study')
            prog.coins_awarded = coins_earned
    elif correct and prog.ever_correct:
        # 重复答对无金币
        pass

    # 记录错误
    if not correct:
        record_wrong(u.id, s.id,
                     1 if question_type == 'step3' else 4,
                     user_input,
                     s.english if question_type != 'step4_translation' else s.chinese,
                     '作答错误')
        # 更新步骤错误计数
        ec = dict(asm.step_error_counts or {})
        step_key = question_type.split('_')[0]  # 'step3' or 'step4'
        ec[step_key] = (ec.get(step_key, 0) or 0) + 1
        asm.step_error_counts = ec

    db.session.commit()

    return jsonify(
        correct=correct,
        coins_earned=coins_earned,
        balance=u.coin_balance,
        attempt_count=prog.attempt_count,
        ever_correct=prog.ever_correct,
        first_correct_attempt=prog.first_correct_attempt,
        detail=detail,
        # 答错不显答案
        standard_answer=(s.english if (correct and question_type != 'step4_translation') else
                        (s.chinese if (correct and question_type == 'step4_translation') else None)),
    )


@app.route('/api/v1/scheme/step/skip', methods=['POST'])
@jwt_required()
def scheme_step_skip():
    """听力大师跳过题目（放到队列末尾，必须全部答完才能结束）。"""
    u = current_user()
    data = request.get_json(silent=True) or {}
    course_id = int(data.get('course_id', 0))
    sentence_id = int(data.get('sentence_id', 0))
    question_type = (data.get('question_type') or '').strip()

    asm = _get_scheme_assignment(u.id, course_id)
    if not asm:
        return jsonify(error='未分配该课程'), 404

    # 标记本题为跳过
    prog = SchemeStepProgress.query.filter_by(
        scheme_id=asm.scheme_id, student_id=u.id,
        course_id=course_id, sentence_id=sentence_id,
        question_type=question_type,
    ).first()
    if prog:
        prog.skipped = True
        db.session.commit()

    return jsonify(skipped=True, message='题目已跳过，稍后继续')


@app.route('/api/v1/scheme/step/finish', methods=['POST'])
@jwt_required()
def scheme_step_finish():
    """听力大师步骤完成（解锁下一步）。"""
    u = current_user()
    data = request.get_json(silent=True) or {}
    course_id = int(data.get('course_id', 0))
    step = int(data.get('step', 0))

    asm = _get_scheme_assignment(u.id, course_id)
    if not asm:
        return jsonify(error='未分配该课程'), 404

    item = CourseSchemeItem.query.filter_by(
        scheme_id=asm.scheme_id, course_id=course_id).first()
    enabled = item.steps if item else [1, 2, 3, 4]

    # 标记本步完成
    completed = list(asm.completed_steps or [])
    if step not in completed:
        completed.append(step)
    asm.completed_steps = completed

    # 找下一个启用的步骤
    next_step = None
    for s in enabled:
        if s > step:
            next_step = s
            break

    if next_step:
        unlocks = dict(asm.step_unlocks or {})
        unlocks[str(next_step)] = True
        asm.step_unlocks = unlocks
        asm.current_step = next_step
        # 记录进入时间
        entered = dict(asm.step_entered_at or {})
        entered[str(next_step)] = datetime.datetime.utcnow().isoformat()
        asm.step_entered_at = entered
    else:
        # 所有步骤完成
        asm.is_completed = True

    # 清除本步错误计数
    ec = dict(asm.step_error_counts or {})
    ec.pop(str(step), None)
    asm.step_error_counts = ec

    db.session.commit()

    # 检查下一门课程是否解锁
    items = (CourseSchemeItem.query
             .filter_by(scheme_id=asm.scheme_id)
             .order_by(CourseSchemeItem.order_index).all())
    next_course = None
    for i, it in enumerate(items):
        if it.course_id == course_id and i + 1 < len(items):
            next_course = items[i + 1].course_id
            break

    return jsonify(
        completed=True,
        next_step=next_step,
        next_course=next_course,
        is_course_completed=asm.is_completed,
        completed_steps=completed,
    )


@app.route('/api/v1/scheme/step/fallback', methods=['POST'])
@jwt_required()
def scheme_step_fallback():
    """听力大师回退到上一步（错误次数超阈值时触发）。"""
    u = current_user()
    data = request.get_json(silent=True) or {}
    course_id = int(data.get('course_id', 0))
    from_step = int(data.get('from_step', 0))

    asm = _get_scheme_assignment(u.id, course_id)
    if not asm:
        return jsonify(error='未分配该课程'), 404

    scheme = CourseScheme.query.get(asm.scheme_id)
    item = CourseSchemeItem.query.filter_by(
        scheme_id=asm.scheme_id, course_id=course_id).first()
    enabled = item.steps if item else [1, 2, 3, 4]

    # 检查错误阈值
    ec = dict(asm.step_error_counts or {})
    error_count = ec.get(str(from_step), 0) or 0
    max_errors = scheme.max_errors_before_fallback if scheme else 10
    if error_count < max_errors:
        return jsonify(error=f'错误次数（{error_count}）未达回退阈值（{max_errors}），无法回退'), 400

    # 找到上一步
    prev_step = None
    for s in enabled:
        if s >= from_step:
            break
        prev_step = s

    if prev_step is None:
        return jsonify(error='已是第一步，无法回退'), 400

    # 执行回退
    asm.current_step = prev_step
    unlocks = dict(asm.step_unlocks or {})
    unlocks[str(prev_step)] = True
    asm.step_unlocks = unlocks

    # 标记回退
    fb = dict(asm.step_fallen_back or {})
    fb[str(from_step)] = True
    asm.step_fallen_back = fb

    # 记录进入上一步时间（触发冷却）
    entered = dict(asm.step_entered_at or {})
    entered[str(prev_step)] = datetime.datetime.utcnow().isoformat()
    asm.step_entered_at = entered

    # 清除当前步错误计数
    ec.pop(str(from_step), None)
    asm.step_error_counts = ec

    # 清除上一步的完成记录（需重新巩固）
    completed = [s for s in (asm.completed_steps or []) if s != prev_step]
    asm.completed_steps = completed

    db.session.commit()

    return jsonify(
        fallback_to=prev_step,
        cooldown_minutes=scheme.cooldown_minutes if scheme else 5,
        message=f'已回退到 Step {prev_step}，请重新巩固后再试',
    )


@app.route('/api/v1/scheme/step/appeal', methods=['POST'])
@jwt_required()
def scheme_step_appeal():
    """听力大师人工复议（花费 2 金币）。"""
    u = current_user()
    data = request.get_json(silent=True) or {}
    course_id = int(data.get('course_id', 0))
    sentence_id = int(data.get('sentence_id', 0))
    question_type = (data.get('question_type') or '').strip()
    user_input = (data.get('user_input') or '').strip()
    standard_answer = (data.get('standard_answer') or '').strip()

    asm = _get_scheme_assignment(u.id, course_id)
    if not asm:
        return jsonify(error='未分配该课程'), 404

    s = Sentence.query.get(sentence_id) if sentence_id else None

    # 防重复
    dup = Appeal.query.filter_by(
        student_id=u.id, course_id=course_id, status='pending',
        scheme_id=asm.scheme_id,
    ).filter_by(sentence_id=sentence_id).first()
    if dup:
        return jsonify(error='该题目已申请复议，等待审核中', already=True)

    if (u.coin_balance or 0) < APPEAL_COST:
        return jsonify(error=f'金币不足，无法申请人工复议（需 {APPEAL_COST} 金币）'), 400

    add_coins(u.id, -APPEAL_COST,
              f'听力大师·人工复议（{question_type}）',
              category='appeal')

    step_map = {'step3': 3, 'step4_dictation': 4, 'step4_translation': 4}
    step_num = step_map.get(question_type, 4)

    db.session.add(Appeal(
        student_id=u.id, course_id=course_id, step=step_num,
        sentence_id=sentence_id, student_answer=user_input,
        standard_answer=standard_answer, status='pending',
        scheme_id=asm.scheme_id,
    ))
    db.session.commit()

    return jsonify(ok=True, cost=APPEAL_COST, balance=u.coin_balance,
                   message=f'已申请人工复议，扣除 {APPEAL_COST} 金币，等待管理员审核')


@app.route('/api/v1/scheme/step/progress', methods=['GET'])
@jwt_required()
def scheme_step_progress():
    """获取学生在某课程某步骤的逐题进度（用于金币状态展示）。"""
    u = current_user()
    course_id = int(request.args.get('course_id', 0))
    question_type = (request.args.get('question_type') or '').strip()

    asm = _get_scheme_assignment(u.id, course_id)
    if not asm:
        return jsonify(progress=[])

    items = SchemeStepProgress.query.filter_by(
        scheme_id=asm.scheme_id, student_id=u.id,
        course_id=course_id, question_type=question_type,
    ).all()

    return jsonify(progress=[{
        'sentence_id': p.sentence_id,
        'attempt_count': p.attempt_count,
        'ever_correct': p.ever_correct,
        'first_correct_attempt': p.first_correct_attempt,
        'coins_awarded': p.coins_awarded,
        'skipped': p.skipped,
    } for p in items])


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


# ---------------- 公开：系统版本与升级内容（登录页用，无需登录） ----------------
@app.route('/api/v1/version', methods=['GET'])
def api_version():
    return jsonify(version=VERSION, changelog=CHANGELOG)


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        try:
            from init_db import migrate
            migrate()
        except Exception as e:
            print('migrate skipped:', e)
    app.run(host='0.0.0.0', port=5000, debug=True)
