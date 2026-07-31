"""初始化数据库：建表 + 创建超级管理员(admin/admin123) + 可选灌入 demo 课程。

用法：
    cd backend
    python init_db.py            # 仅建表 + 默认管理员
    python init_db.py --seed     # 额外灌入 demo_course.json
"""
import os
import sys
import json
import argparse
from sqlalchemy import text

from app import app, db
import models
from models import User, Course, Sentence, SystemSetting

DEFAULT_ADMIN = 'admin'
DEFAULT_PASSWORD = 'admin123'


def migrate():
    """兼容旧库：补齐新增列 / 放宽约束（保留数据）。"""
    with app.app_context():
        # courses: 增加 external_article_id
        cols = [r[1] for r in db.session.execute(text("PRAGMA table_info(courses)")).fetchall()]
        if 'external_article_id' not in cols:
            db.session.execute(text("ALTER TABLE courses ADD COLUMN external_article_id INTEGER"))
            db.session.commit()
            print('已为 courses 增加 external_article_id 列。')
        # sentences: 若 audio_url 仍为 NOT NULL，则重建为可空（保留数据）
        info = db.session.execute(text("PRAGMA table_info(sentences)")).fetchall()
        audio_row = next((r for r in info if r[1] == 'audio_url'), None)
        if audio_row and audio_row[3] == 1:  # notnull == 1
            db.session.execute(text("""
                CREATE TABLE sentences_new (
                    id INTEGER PRIMARY KEY,
                    course_id INTEGER NOT NULL,
                    sentence_order INTEGER NOT NULL,
                    english TEXT NOT NULL,
                    chinese TEXT NOT NULL,
                    audio_url TEXT,
                    target_words JSON,
                    svo JSON,
                    chinese_keywords JSON
                )
            """))
            db.session.execute(text("""
                INSERT INTO sentences_new
                    (id, course_id, sentence_order, english, chinese, audio_url, target_words, svo, chinese_keywords)
                SELECT id, course_id, sentence_order, english, chinese, audio_url, target_words, svo, chinese_keywords
                FROM sentences
            """))
            db.session.execute(text("DROP TABLE sentences"))
            db.session.execute(text("ALTER TABLE sentences_new RENAME TO sentences"))
            db.session.commit()
            print('已将 sentences.audio_url 改为可空（数据已保留）。')
        # users: 增加 last_task_date（用于"签到需先完成任务"）
        ucols = [r[1] for r in db.session.execute(text("PRAGMA table_info(users)")).fetchall()]
        if 'last_task_date' not in ucols:
            db.session.execute(text("ALTER TABLE users ADD COLUMN last_task_date DATE"))
            db.session.commit()
            print('已为 users 增加 last_task_date 列。')
        # coin_transactions: 增加 category / operator_id
        ctcols = [r[1] for r in db.session.execute(text("PRAGMA table_info(coin_transactions)")).fetchall()]
        if 'category' not in ctcols:
            db.session.execute(text("ALTER TABLE coin_transactions ADD COLUMN category VARCHAR(20)"))
            db.session.commit()
            print('已为 coin_transactions 增加 category 列。')
        if 'operator_id' not in ctcols:
            db.session.execute(text("ALTER TABLE coin_transactions ADD COLUMN operator_id INTEGER"))
            db.session.commit()
            print('已为 coin_transactions 增加 operator_id 列。')
        # purchase_orders: 增加 shipped_at / completed_at / reject_reason
        pcols = [r[1] for r in db.session.execute(text("PRAGMA table_info(purchase_orders)")).fetchall()]
        for col, ctype in [('shipped_at', 'DATETIME'), ('completed_at', 'DATETIME'), ('reject_reason', 'TEXT')]:
            if col not in pcols:
                db.session.execute(text(f"ALTER TABLE purchase_orders ADD COLUMN {col} {ctype}"))
                db.session.commit()
                print(f'已为 purchase_orders 增加 {col} 列。')
        # wishes: 增加 completed_at
        wcols = [r[1] for r in db.session.execute(text("PRAGMA table_info(wishes)")).fetchall()]
        if 'completed_at' not in wcols:
            db.session.execute(text("ALTER TABLE wishes ADD COLUMN completed_at DATETIME"))
            db.session.commit()
            print('已为 wishes 增加 completed_at 列。')
        # shop_items: 增加 product_type（custom / builtin 内置免错券）
        scols = [r[1] for r in db.session.execute(text("PRAGMA table_info(shop_items)")).fetchall()]
        if 'product_type' not in scols:
            db.session.execute(text("ALTER TABLE shop_items ADD COLUMN product_type VARCHAR(20) DEFAULT 'custom'"))
            db.session.commit()
            print('已为 shop_items 增加 product_type 列。')
        # wishes: 增加单词大师扩展字段（title/desc/is_public/lit/pledges/source）
        for col, ctype in [
            ('title', 'TEXT'), ('desc', 'TEXT'),
            ('is_public', 'BOOLEAN'), ('lit', 'BOOLEAN'),
            ('pledges', 'JSON'), ('source', 'VARCHAR(10)'),
        ]:
            if col not in wcols:
                db.session.execute(text(f"ALTER TABLE wishes ADD COLUMN {col} {ctype}"))
                db.session.commit()
                print(f'已为 wishes 增加 {col} 列。')
        # 种子：内置免错券商品（单词大师与奖励中心共享）
        from models import ShopItem
        if ShopItem.query.filter_by(product_type='builtin').count() == 0:
            db.session.add(ShopItem(
                name='免错机会券',
                description='答题（新背/复习/考试）答错时可消耗此券抵消本次错误，不计入成绩',
                price_coins=2, stock=-1, is_on_shelf=True, product_type='builtin'))
            db.session.commit()
            print('已种子内置免错券商品。')
        # 默认系统配置（签到/金币）
        defaults = {
            'checkin_coin': 1,            # 每日签到金币
            'checkin_require_task': True, # 签到前需先完成至少一个学习任务
            'streak_bonus_per_day': 0,    # 连续签到每日奖励（0=关闭，由管理员设置）
            'streak_bonus_cap': 10,       # 连续签到奖励封顶天数
        }
        for k, v in defaults.items():
            if SystemSetting.query.get(k) is None:
                db.session.add(SystemSetting(key=k, value=json.dumps(v)))
        db.session.commit()
        print('已初始化系统配置（签到/金币）。')
        # courses: 增加 order_index（解锁式学习排序）
        ccols = [r[1] for r in db.session.execute(text("PRAGMA table_info(courses)")).fetchall()]
        if 'order_index' not in ccols:
            db.session.execute(text("ALTER TABLE courses ADD COLUMN order_index INTEGER DEFAULT 0"))
            db.session.commit()
            print('已为 courses 增加 order_index 列。')
        # 回填：未显式设置顺序的课程，按 id 作为顺序（先创建的排在前面）
        db.session.execute(text("UPDATE courses SET order_index = id WHERE order_index IS NULL OR order_index = 0"))
        db.session.commit()
        # course_assignments: 增加 unlock_mode（free/locked）
        acols = [r[1] for r in db.session.execute(text("PRAGMA table_info(course_assignments)")).fetchall()]
        if 'unlock_mode' not in acols:
            db.session.execute(text("ALTER TABLE course_assignments ADD COLUMN unlock_mode VARCHAR(16) DEFAULT 'free'"))
            db.session.commit()
            print('已为 course_assignments 增加 unlock_mode 列。')
        # users: 增加 allow_skip（允许一轮后强制解锁下一步）
        if 'allow_skip' not in ucols:
            db.session.execute(text("ALTER TABLE users ADD COLUMN allow_skip BOOLEAN DEFAULT 0"))
            db.session.commit()
            print('已为 users 增加 allow_skip 列。')
        # 单词大师默认配置（存 SystemSetting）
        wm_defaults = {
            'wm_admin_config': {
                'shared_base_url': 'https://api.deepseek.com/v1',
                'shared_ai_model': 'deepseek-chat',
                'retry_cooldown_seconds': 60,
                'tts_in_en2zh': False,
                'audio2zh_enabled': True,
                'judge_mode': 'local_then_ai',
                'controlled_users': {},
            },
            'wm_config': {
                'review_count': 20,
                'review_mode': 'none',
                'require_both_modes': False,
            },
        }
        for k, v in wm_defaults.items():
            if SystemSetting.query.get(k) is None:
                db.session.add(SystemSetting(key=k, value=json.dumps(v)))
        db.session.commit()
        print('已初始化单词大师配置。')
        # ---- v0.4 步骤重编号：在 Step3(听音) 与 Step4(中译英) 之间插入「跟读」Step4 ----
        # 原 Step4 中译英 -> 新 Step5；原 Step5 续写 -> 新 Step6。旧数据一次性位移，幂等（靠标记）。
        if SystemSetting.query.get('migrated_step6') is None:
            from models import CourseAssignment, StudentSentenceProgress, WrongAnswer
            acols = [r[1] for r in db.session.execute(
                text("PRAGMA table_info(course_assignments)")).fetchall()]
            if 'step_6_unlocked' not in acols:
                db.session.execute(text(
                    "ALTER TABLE course_assignments ADD COLUMN step_6_unlocked BOOLEAN DEFAULT 0"))
                db.session.commit()
            # 进度/错题：旧 step >= 4（中译英/续写）整体 +1（跟读无旧数据）
            db.session.execute(text(
                "UPDATE student_sentence_progress SET step = step + 1 WHERE step >= 4"))
            db.session.execute(text(
                "UPDATE wrong_answers SET step = step + 1 WHERE step >= 4"))
            for a in CourseAssignment.query.all():
                # completed_steps：旧 >=4 元素 +1（2,3 不变）
                if a.completed_steps:
                    a.completed_steps = [x + 1 if (x or 0) >= 4 else x
                                         for x in a.completed_steps]
                # unlock 列映射：新4(跟读)=旧3或旧4；新5(中译英)=旧4；新6(续写)=旧5
                old4 = bool(a.step_4_unlocked)
                old5 = bool(a.step_5_unlocked)
                a.step_4_unlocked = old4 or bool(a.step_3_unlocked)
                a.step_5_unlocked = old4
                a.step_6_unlocked = old5
                # current_step 由解锁列推导（解锁为顺序，最高解锁步即当前进度）
                unlocked = [n for n in (1, 2, 3, 4, 5, 6)
                            if getattr(a, f'step_{n}_unlocked')]
                a.current_step = max(unlocked) if unlocked else 1
            db.session.commit()
            db.session.add(SystemSetting(key='migrated_step6', value=json.dumps(True)))
            db.session.commit()
            print('已完成步骤重编号数据迁移（插入跟读 Step4）。')
        # ---- v0.5 新增 Step7 单词巩固：course_assignments 增加 step_7_unlocked 列 ----
        # （course_words 表由 main() 中的 db.create_all() 自动建表；此处仅补列，幂等）
        acols7 = [r[1] for r in db.session.execute(
            text("PRAGMA table_info(course_assignments)")).fetchall()]
        if 'step_7_unlocked' not in acols7:
            db.session.execute(text(
                "ALTER TABLE course_assignments ADD COLUMN step_7_unlocked BOOLEAN DEFAULT 0"))
            db.session.commit()
            print('已为 course_assignments 增加 step_7_unlocked 列。')
        # db.create_all() 已建单词大师新表（word_lists / words / word_user_states / word_exam_configs）


def seed_demo():
    if Course.query.count() > 0:
        print('已有课程，跳过 demo 灌入。')
        return
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'demo_course.json')
    if not os.path.exists(path):
        print('未找到 demo_course.json，跳过。')
        return
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    c = Course(title=data['title'], full_text=data.get('full_text'),
               external_article_id=int(data['article_id']) if data.get('article_id') else None,
               created_by_admin_id=1, is_published=True)
    db.session.add(c)
    db.session.flush()
    for idx, item in enumerate(data['sentences']):
        db.session.add(Sentence(
            course_id=c.id,
            sentence_order=int(item.get('sentence_id') or idx + 1),
            english=item['english'], chinese=item['chinese'],
            audio_url='',
            target_words=item.get('target_words', []),
            svo=item.get('svo', []),
            chinese_keywords=item.get('chinese_keywords', [])))
    db.session.commit()
    print(f'已灌入 demo 课程「{c.title}」，含 {len(data["sentences"])} 句。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', action='store_true', help='灌入 demo 课程')
    args = parser.parse_args()

    with app.app_context():
        db.create_all()
        migrate()
        # 创建默认管理员
        admin = User.query.filter_by(username=DEFAULT_ADMIN).first()
        if not admin:
            admin = User(username=DEFAULT_ADMIN, role='admin')
            admin.set_password(DEFAULT_PASSWORD)
            db.session.add(admin)
            db.session.commit()
            print(f'已创建超级管理员：{DEFAULT_ADMIN} / {DEFAULT_PASSWORD}')
        else:
            print('超级管理员已存在，跳过。')
        if args.seed:
            seed_demo()
    print('数据库初始化完成。')


if __name__ == '__main__':
    main()
