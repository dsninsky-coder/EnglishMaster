"""数据库模型定义（SQLite + SQLAlchemy）。

约定：
- JSON 字段统一用 db.JSON（SQLite 下存为 TEXT）。
- 密码使用 werkzeug 加密（bcrypt 的纯 Python 等价方案，避免原生编译依赖）。
- course_assignments 额外补充 completed_steps / perfect_steps / completion_awarded，
  用于精确追踪"是否已发过奖励"，避免重复发币。

v2.0（听力大师）新增：
- CourseWord 增加 meaning / phonetic 字段（全文单词含音标释义）
- 课程方案系统：CourseScheme / CourseSchemeItem / CourseSchemeStudent / SchemeAssignment / SchemeStepProgress
- Appeal 增加 scheme_id 关联方案
"""
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import datetime
import json

db = SQLAlchemy()


def utcnow():
    return datetime.datetime.utcnow()


class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.Text, nullable=False)
    role = db.Column(db.String(20), default='student', nullable=False)  # student / admin
    private_api_key = db.Column(db.Text, nullable=True)
    shared_api_key_id = db.Column(db.Integer, db.ForeignKey('admin_share_keys.id'), nullable=True)
    coin_balance = db.Column(db.Integer, default=0)
    daily_streak = db.Column(db.Integer, default=0)
    last_checkin_date = db.Column(db.Date, nullable=True)
    last_task_date = db.Column(db.Date, nullable=True)   # 最近完成学习任务的日期（用于"签到需先完成任务"）
    total_perfect_steps = db.Column(db.Integer, default=0)
    allow_skip = db.Column(db.Boolean, default=False)    # 管理员为该生开启：一轮后可强制解锁下一步（即使仍有未通过句）
    last_active = db.Column(db.DateTime, default=utcnow)
    last_notified_at = db.Column(db.DateTime, nullable=True)  # 学生端「消息通知」已读时间点（用于只弹未读消息）
    created_at = db.Column(db.DateTime, default=utcnow)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class AdminShareKey(db.Model):
    __tablename__ = 'admin_share_keys'
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    api_key_value = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)


class Course(db.Model):
    __tablename__ = 'courses'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.Text, nullable=False)
    full_text = db.Column(db.Text, nullable=True)
    external_article_id = db.Column(db.Integer, nullable=True)  # 外部文章ID（上传JSON中的 article_id）
    created_by_admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    is_published = db.Column(db.Boolean, default=False)
    order_index = db.Column(db.Integer, default=0)      # 解锁式学习的课程顺序（越小越靠前）
    created_at = db.Column(db.DateTime, default=utcnow)


class Sentence(db.Model):
    __tablename__ = 'sentences'
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id'), nullable=False, index=True)
    sentence_order = db.Column(db.Integer, nullable=False)
    english = db.Column(db.Text, nullable=False)
    chinese = db.Column(db.Text, nullable=False)
    audio_url = db.Column(db.Text, default='')              # 本地上传后为 /uploads/courses/<id>/n.mp3；可空
    target_words = db.Column(db.JSON, default=list)         # ["word1","word2"]
    svo = db.Column(db.JSON, default=list)                  # ["He","see","cat"]
    chinese_keywords = db.Column(db.JSON, default=list)     # ["疲惫","下班"] 补充字段
    alignment = db.Column(db.JSON, default=dict)           # 词色对齐（一次性 AI 生成）：{units:[{en,pos,content,color,zh}]}


class CourseAssignment(db.Model):
    __tablename__ = 'course_assignments'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id'), nullable=False, index=True)
    assigned_at = db.Column(db.DateTime, default=utcnow)
    current_step = db.Column(db.Integer, default=1)
    step_1_unlocked = db.Column(db.Boolean, default=True)
    step_2_unlocked = db.Column(db.Boolean, default=False)
    step_3_unlocked = db.Column(db.Boolean, default=False)
    step_4_unlocked = db.Column(db.Boolean, default=False)
    step_5_unlocked = db.Column(db.Boolean, default=False)
    step_6_unlocked = db.Column(db.Boolean, default=False)
    step_7_unlocked = db.Column(db.Boolean, default=False)  # 单词巩固（v0.5 新增）
    is_completed = db.Column(db.Boolean, default=False)
    # 奖励追踪
    completed_steps = db.Column(db.JSON, default=list)      # [2,3,...]
    perfect_steps = db.Column(db.JSON, default=list)        # [2,...]
    completion_awarded = db.Column(db.Boolean, default=False)
    # 解锁式学习：free=自由学习（已解锁即可开始）/ locked=解锁式（需完成上一门才解锁下一门）
    unlock_mode = db.Column(db.String(16), default='free')
    # 人工复议：被驳回后课程重新上锁，需重学该步
    appeal_locked = db.Column(db.Boolean, default=False)
    # 复议待审期间，本步奖励暂扣；appeal_suppressed 存 [step]，appeal_suppressed_perfect 存 {step: 是否完美}
    appeal_suppressed = db.Column(db.JSON, default=list)
    appeal_suppressed_perfect = db.Column(db.JSON, default=dict)

    __table_args__ = (db.UniqueConstraint('student_id', 'course_id', name='uq_assignment'),)


class CourseWord(db.Model):
    """课程单词库（v2.0 听力大师全文单词）。

    - v2.0 升级：保留全部单词（含冠词/介词/助词等虚词），不再仅保留实词；
    - meaning：中文释义（AI 根据文章上下文生成，管理员可编辑）；
    - phonetic：IPA 音标（eng-to-ipa 离线优先 + AI 兜底）；
    - is_custom=True 表示管理员手动添加（重新提取不会删掉它）；
    - 学生做题时直接从本表读取，不实时提取，提升效率。
    """
    __tablename__ = 'course_words'
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id'), nullable=False, index=True)
    word = db.Column(db.String(80), nullable=False)
    is_custom = db.Column(db.Boolean, default=False)   # 管理员手动添加
    meaning = db.Column(db.Text, nullable=True)        # v2.0: 中文释义
    phonetic = db.Column(db.Text, nullable=True)       # v2.0: IPA 音标
    created_at = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (db.UniqueConstraint('course_id', 'word', name='uq_course_word'),)


class StudentSentenceProgress(db.Model):
    __tablename__ = 'student_sentence_progress'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    sentence_id = db.Column(db.Integer, db.ForeignKey('sentences.id'), nullable=False)
    step = db.Column(db.Integer, nullable=False)            # 2~5
    proficiency = db.Column(db.Integer, default=0)          # 0~100
    last_reviewed = db.Column(db.DateTime, default=utcnow)
    is_mastered = db.Column(db.Boolean, default=False)

    __table_args__ = (db.UniqueConstraint('student_id', 'sentence_id', 'step', name='uq_ssp'),)


class WrongAnswer(db.Model):
    __tablename__ = 'wrong_answers'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    sentence_id = db.Column(db.Integer, db.ForeignKey('sentences.id'), nullable=False)
    step = db.Column(db.Integer, nullable=False)
    user_input = db.Column(db.Text, nullable=True)
    correct_answer = db.Column(db.Text, nullable=True)
    error_type = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)


class CoinTransaction(db.Model):
    __tablename__ = 'coin_transactions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    amount = db.Column(db.Integer, nullable=False)          # 正=增加 负=消耗
    reason = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(20), nullable=True)      # checkin/study/reward/penalty/shop/wish/support/refund
    operator_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # 操作管理员
    created_at = db.Column(db.DateTime, default=utcnow)


class Appeal(db.Model):
    """学生人工复议（对系统判错的题目申请人工复核）。

    - 学生答错后申请，花费 2 金币；题目暂记为"默认通过"以便继续；
    - 管理员判定：通过(学生没错)→ 返还 2 金币、补发本步被暂扣奖励、标记该句掌握；
      驳回(系统没错)→ 没收 2 金币、课程重新上锁(仅该错误步需重学)。
    """
    __tablename__ = 'appeals'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id'), nullable=False, index=True)
    step = db.Column(db.Integer, nullable=False)
    sentence_id = db.Column(db.Integer, db.ForeignKey('sentences.id'), nullable=True)  # Step7 单词巩固可为空
    student_answer = db.Column(db.Text, nullable=True)
    standard_answer = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(16), default='pending')   # pending / approved / rejected
    admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    admin_note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)
    scheme_id = db.Column(db.Integer, nullable=True)       # v2.0: 关联课程方案（听力大师）


class ShopItem(db.Model):
    __tablename__ = 'shop_items'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=True)
    price_coins = db.Column(db.Integer, nullable=False)
    stock = db.Column(db.Integer, default=-1)               # -1 = 无限
    is_on_shelf = db.Column(db.Boolean, default=True)
    product_type = db.Column(db.String(20), default='custom')  # custom / builtin（内置免错券）
    admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)


class PurchaseOrder(db.Model):
    __tablename__ = 'purchase_orders'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey('shop_items.id'), nullable=False)
    status = db.Column(db.String(20), default='pending')   # pending / shipped / completed / rejected
    created_at = db.Column(db.DateTime, default=utcnow)
    shipped_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    reject_reason = db.Column(db.Text, nullable=True)
    admin_note = db.Column(db.Text, nullable=True)


class Wish(db.Model):
    __tablename__ = 'wishes'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    total_coins_invested = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='pending')   # 听说大师: pending/approved/rejected/completed
                                                         # 单词大师: open/approved/rejected/fulfilled/archived
    admin_reply = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    # ---- 单词大师许愿池扩展字段（nullable，不影响听说大师） ----
    title = db.Column(db.Text, nullable=True)
    desc = db.Column(db.Text, nullable=True)
    is_public = db.Column(db.Boolean, default=True)
    lit = db.Column(db.Boolean, default=False)             # 管理员是否已点亮
    pledges = db.Column(db.JSON, default=list)             # [{user, coins, time}]
    source = db.Column(db.String(10), default='listen')    # listen=听说大师 / word=单词大师


class WishSupport(db.Model):
    __tablename__ = 'wish_supports'
    id = db.Column(db.Integer, primary_key=True)
    wish_id = db.Column(db.Integer, db.ForeignKey('wishes.id'), nullable=False, index=True)
    supporter_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    coins = db.Column(db.Integer, nullable=False)
    supported_at = db.Column(db.DateTime, default=utcnow)


# ============================================================
# 听力大师（v2.0）课程方案系统
# 说明：课程方案独立于素材管理，管理员手动为每篇课程勾选步骤，
#       极高自由度（可任意排列课程、任意勾选步骤组合）。
# ============================================================

class CourseScheme(db.Model):
    """课程方案（如方案 A / B / C...）。"""
    __tablename__ = 'course_schemes'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    max_errors_before_fallback = db.Column(db.Integer, default=10)  # 回退错误阈值
    cooldown_minutes = db.Column(db.Integer, default=5)             # 冷却时长（分钟）
    is_active = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)


class CourseSchemeItem(db.Model):
    """方案内每篇课程的步骤配置。"""
    __tablename__ = 'course_scheme_items'
    id = db.Column(db.Integer, primary_key=True)
    scheme_id = db.Column(db.Integer, db.ForeignKey('course_schemes.id'), nullable=False, index=True)
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id'), nullable=False, index=True)
    order_index = db.Column(db.Integer, default=0)        # 从 1 开始排序
    steps = db.Column(db.JSON, default=list)              # 如 [1,2,3] / [1,2,3,4] / [3,4] / [1,3,4]

    __table_args__ = (db.UniqueConstraint('scheme_id', 'course_id', name='uq_scheme_item'),)


class CourseSchemeStudent(db.Model):
    """方案分配的学生。"""
    __tablename__ = 'course_scheme_students'
    id = db.Column(db.Integer, primary_key=True)
    scheme_id = db.Column(db.Integer, db.ForeignKey('course_schemes.id'), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    __table_args__ = (db.UniqueConstraint('scheme_id', 'student_id', name='uq_scheme_student'),)


class SchemeAssignment(db.Model):
    """学生方案进度（替代旧 CourseAssignment 用于听力大师）。"""
    __tablename__ = 'scheme_assignments'
    id = db.Column(db.Integer, primary_key=True)
    scheme_id = db.Column(db.Integer, db.ForeignKey('course_schemes.id'), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id'), nullable=False, index=True)
    current_step = db.Column(db.Integer, default=1)
    step_unlocks = db.Column(db.JSON, default=dict)        # {1:true, 2:false, 3:false, 4:false}
    completed_steps = db.Column(db.JSON, default=list)     # [1,2,...]
    perfect_steps = db.Column(db.JSON, default=list)       # [1,2,...]
    is_completed = db.Column(db.Boolean, default=False)
    step_error_counts = db.Column(db.JSON, default=dict)   # {"3":5,"4":2}
    step_entered_at = db.Column(db.JSON, default=dict)     # {"3":"2026-08-06T10:00:00"}
    step_fallen_back = db.Column(db.JSON, default=dict)    # {"3":true}
    appeal_locked = db.Column(db.Boolean, default=False)
    appeal_suppressed = db.Column(db.JSON, default=list)
    appeal_suppressed_perfect = db.Column(db.JSON, default=dict)
    assigned_at = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (db.UniqueConstraint('scheme_id', 'student_id', 'course_id', name='uq_scheme_assignment'),)


class SchemeStepProgress(db.Model):
    """听力大师逐题进度追踪（用于金币计算 + 跳过题目 + 出错计数）。"""
    __tablename__ = 'scheme_step_progress'
    id = db.Column(db.Integer, primary_key=True)
    scheme_id = db.Column(db.Integer, db.ForeignKey('course_schemes.id'), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id'), nullable=False, index=True)
    sentence_id = db.Column(db.Integer, db.ForeignKey('sentences.id'), nullable=False)
    question_type = db.Column(db.String(30), nullable=False)  # step3 / step4_dictation / step4_translation
    attempt_count = db.Column(db.Integer, default=0)           # 本题总尝试次数
    ever_correct = db.Column(db.Boolean, default=False)        # 是否曾答对（重复答题无金币）
    first_correct_attempt = db.Column(db.Integer, nullable=True)  # 首次答对是第几次
    coins_awarded = db.Column(db.Integer, default=0)           # 已发金币
    skipped = db.Column(db.Boolean, default=False)             # 是否被跳过等待回头
    last_attempt_at = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (db.UniqueConstraint('scheme_id', 'student_id', 'course_id', 'sentence_id', 'question_type',
                                          name='uq_scheme_step_progress'),)
# 说明：单词大师的「单词库 / 用户学习历史 / 考试配置」与听说大师完全独立；
#       账号 / 金币 / 商店 / 许愿池 复用上方共享表（users / coin_transactions 等）。
# ============================================================
class WordList(db.Model):
    """单词词单（全局共享的单词库，按 list 名分组）。"""
    __tablename__ = 'word_lists'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False, index=True)
    order_index = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)


class Word(db.Model):
    """单词条目（word=英文, meaning=中文释义）。"""
    __tablename__ = 'words'
    id = db.Column(db.Integer, primary_key=True)
    list_id = db.Column(db.Integer, db.ForeignKey('word_lists.id'), nullable=False, index=True)
    word = db.Column(db.Text, nullable=False)
    meaning = db.Column(db.Text, nullable=False)
    order_index = db.Column(db.Integer, default=0)


class WordUserState(db.Model):
    """单词大师用户学习状态（整体镜像原 history JSON）。

    data 结构（与原 words/data_manager 的 history 文件一致）：
    { learned_lists, word_reviews, quiz_results, daily_stats, user_prefs,
      list_cooldowns, review_once_cleared_date, exam_attempts, no_wrong_tickets }
    """
    __tablename__ = 'word_user_states'
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), primary_key=True)
    data = db.Column(db.JSON, default=dict)


class WordExamConfig(db.Model):
    """单词大师考试配置（每用户 5 个槽位，整体存 JSON）。"""
    __tablename__ = 'word_exam_configs'
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), primary_key=True)
    data = db.Column(db.JSON, default=list)   # 长度 5 的列表，None 表示空槽位


class SystemSetting(db.Model):
    """系统配置（键值对，value 以 JSON 存储）。用于签到/金币等可后台配置项。"""
    __tablename__ = 'system_settings'
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.Text, nullable=True)           # JSON 编码的任意值

    @classmethod
    def get(cls, key, default=None):
        row = cls.query.get(key)
        if row is None:
            return default
        try:
            return json.loads(row.value)
        except (TypeError, json.JSONDecodeError):
            return default

    @classmethod
    def set(cls, key, value):
        row = cls.query.get(key)
        if row is None:
            row = cls(key=key)
        row.value = json.dumps(value)
        db.session.add(row)
        db.session.commit()
