from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from word_data import WordDataManager
import random
import requests
import json
import re
import calendar
from datetime import datetime
from functools import wraps

VERSION = "0.2.0"

wdm = WordDataManager()
wordmaster_bp = Blueprint('wordmaster', __name__)

# 将 dm 和版本号注入模板全局，供 base.html 使用
@wordmaster_bp.context_processor
def inject_globals():
    return dict(dm=wdm, version=VERSION)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('wordmaster.login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('wordmaster.login'))
        if not wdm.is_admin(session['user']):
            return redirect(url_for('wordmaster.study'))
        return f(*args, **kwargs)
    return decorated_function


# ---------- AI 判断辅助函数 ----------
def check_answer_with_ai(api_key, base_url, model, user_answer, word_en):
    """
    英中模式：调用AI判断用户输入的中文是否为英文单词的正确释义。
    返回：(is_correct: bool|None, raw_response: str)
      - is_correct=None 表示调用失败/异常
      - raw_response 为 AI 的原始返回文本，用于前端展示和调试
    """
    prompt = (
        f"请判断以下用户输入的中文是否为英文单词的正确释义。\n"
        f"用户输入：{user_answer}\n"
        f"英文单词：{word_en}\n"
        f"只回答'正确'或'错误'，不要解释。"
    )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0
    }
    try:
        url = base_url.rstrip('/') + "/chat/completions"
        print(f"[AI DEBUG] 模型={model}, URL={url}")
        print(f"[AI DEBUG] 发送: user_answer={user_answer}, word_en={word_en}")
        print(f"[AI DEBUG] API Key={api_key[:10]}...{api_key[-4:]}")
        response = requests.post(url, headers=headers, json=data, timeout=8)
        print(f"[AI DEBUG] HTTP={response.status_code}, 响应={response.text[:300]}")
        result = response.json()["choices"][0]["message"]["content"].strip()
        print(f"[AI DEBUG] AI原始回答={repr(result)}")

        # 验证 AI 返回格式是否合规
        if '正确' in result and '错误' not in result:
            print(f"[AI DEBUG] 判定=正确")
            return True, result
        elif '错误' in result and '正确' not in result:
            print(f"[AI DEBUG] 判定=错误")
            return False, result
        else:
            # AI 返回内容不符合预期（同时包含两者或都不包含），视为无效响应
            print(f"[AI DEBUG] ⚠️ AI返回异常: {repr(result)}，按本地判定处理")
            return None, result
    except Exception as e:
        print(f"[AI DEBUG] 异常: {type(e).__name__}: {e}")
        return None, str(e)


def check_answer_keyword(user_answer, expected_meaning):
    user_chars = set(re.findall(r'[\u4e00-\u9fff]', user_answer))
    expected_chars = set(re.findall(r'[\u4e00-\u9fff]', expected_meaning))
    if not user_chars:
        return False
    overlap = user_chars & expected_chars
    return len(overlap) >= max(1, len(user_chars) * 0.7)


def judge_en2zh(user_answer, word_zh, word_en, config):
    """
    英中/音中判定逻辑（根据 config 中的 judge_mode 分流）：

    judge_mode 可选值：
      - 'local_then_ai'（默认）：精确→本地关键词→AI兜底
      - 'local_only'：仅本地判定（精确+关键词），不调 AI
      - 'ai_only'：直接调用AI，跳过本地关键词

    返回：(correct: bool, method: str, ai_response: str|None)
      method 取值: exact / keyword / ai / fallback
      ai_response: AI 原始返回文本（仅在 method='ai' 时有值）
    """
    user_answer = user_answer.strip()

    # 规则1：空答案 → 永远判错，不经过任何判断
    if not user_answer:
        return False, "fallback", None

    # 获取判定模式
    judge_mode = config.get('judge_mode', 'local_then_ai')
    api_key = config.get('chatgpt_api_key', '')

    # 精确匹配（所有模式都先走这一步）
    if user_answer == word_zh:
        return True, "exact", None

    # ---- 根据模式分流 ----
    if judge_mode == 'local_only':
        # 纯本地：只做关键词匹配
        local_result = check_answer_keyword(user_answer, word_zh)
        return (True, "keyword", None) if local_result else (False, "keyword", None)

    elif judge_mode == 'ai_only':
        # 纯 AI：跳过本地关键词
        if api_key:
            ai_correct, ai_raw = check_answer_with_ai(
                api_key,
                config.get('chatgpt_base_url', 'https://api.openai.com/v1'),
                config.get('ai_model', 'deepseek-chat'),
                user_answer, word_en
            )
            if ai_correct is not None:
                return (ai_correct, "ai", ai_raw)
            # AI 失败，降级到本地关键词
            print(f"[JUDGE] ai_only 模式但AI调用失败，降级到本地关键词")
        local_result = check_answer_keyword(user_answer, word_zh)
        return (True, "keyword", None) if local_result else (False, "fallback", None)

    else:
        # local_then_ai（默认）：本地优先，失败再 AI
        local_result = check_answer_keyword(user_answer, word_zh)
        if local_result:
            return True, "keyword", None

        # 本地判定错误，用 AI 二次判定
        if api_key:
            print(f"[JUDGE en2zh] 本地判定错误，启用AI二次判定: answer={user_answer}, word={word_en}")
            ai_correct, ai_raw = check_answer_with_ai(
                api_key,
                config.get('chatgpt_base_url', 'https://api.openai.com/v1'),
                config.get('ai_model', 'deepseek-chat'),
                user_answer, word_en
            )
            if ai_correct is not None:
                return (ai_correct, "ai", ai_raw)

        # AI 不可用或调用失败/返回异常，以本地判定为准（错误）
        return False, "fallback", None


def judge_zh2en(user_answer, word_en):
    """中英模式：只做精确匹配（字母数量确定，无误差空间）"""
    user_answer = user_answer.strip()
    if not user_answer:
        return False, "fallback"
    return user_answer.lower() == word_en.lower(), "exact"


# ---------- 首页路由 ----------
# ---------- 登录/注册 ----------
@wordmaster_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if wdm.verify_user(username, password):
            session['user'] = username
            # 标记今日已登录（用于金币页面手动领取判断）
            wdm.mark_checkin(username, 'login_visited')
            if wdm.is_admin(username):
                return redirect(url_for('wordmaster.coins'))
            return redirect(url_for('wordmaster.study'))
        else:
            return render_template('login.html', error='用户名或密码错误')
    return render_template('login.html')


@wordmaster_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if password != confirm:
            return render_template('register.html', error='两次密码不一致')
        success, msg = wdm.register_user(username, password)
        if success:
            session['user'] = username
            return redirect(url_for('wordmaster.study'))
        else:
            return render_template('register.html', error=msg)
    return render_template('register.html')


@wordmaster_bp.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('study_context', None)
    session.pop('review_context', None)
    session.pop('exam_context', None)
    return redirect(url_for('wordmaster.login'))


# ---------- 新背单词 ----------
@wordmaster_bp.route('/study', methods=['GET'])
@login_required
def study():
    words = wdm.load_words()
    list_word_count = wdm.get_list_word_count()
    history = wdm.load_user_history(session['user'])
    learned_lists = history.get("learned_lists", [])
    config, _ = wdm.get_effective_config(session['user'])
    review_count = wdm.get_review_count(session['user'])

    must_review = False
    review_mode = config.get('review_mode', 'none')
    # 向后兼容旧的 require_review_before_study 字段
    if review_mode == 'none' and config.get('require_review_before_study', False):
        review_mode = 'all'

    if review_mode == 'all' and review_count > 0:
        must_review = True
    elif review_mode == 'once' and review_count > 0:
        # once 模式：今天已经通过过一次则不强制
        if not wdm.is_review_once_cleared(session['user']):
            must_review = True

    # 计算冷却剩余时间
    cooldowns = {}
    for list_name in words.keys():
        remaining = wdm.get_list_cooldown_remaining(session['user'], list_name)
        if remaining > 0:
            cooldowns[list_name] = remaining

    # 每个词单最后一次 study 模式的成绩（percent, passed）
    quiz_results = history.get("quiz_results", [])
    last_study_result = {}  # { list_name: {"percent": float, "passed": bool} }
    for r in quiz_results:
        if r.get("mode") == "study":
            last_study_result[r["list"]] = {
                "percent": r.get("percent", 0),
                "passed": r.get("passed", False)
            }

    return render_template(
        'study.html',
        lists=list(words.keys()),
        list_word_count=list_word_count,
        learned_lists=learned_lists,
        must_review=must_review,
        review_count=review_count,
        review_mode=review_mode,
        cooldowns=cooldowns,
        last_study_result=last_study_result
    )


@wordmaster_bp.route('/study/preview', methods=['POST'])
@login_required
def study_preview():
    data = request.get_json()
    list_name = data.get('list_name')
    words = wdm.load_words()
    if list_name not in words:
        return jsonify({'success': False, 'error': '列表不存在'})
    word_list = words[list_name]
    preview = [{'word': w['word'], 'meaning': w['meaning']} for w in word_list]
    return jsonify({'success': True, 'preview': preview, 'list_name': list_name, 'total': len(preview)})


@wordmaster_bp.route('/study/start', methods=['POST'])
@login_required
def study_start():
    data = request.get_json()
    list_name = data.get('list_name')
    is_rechallenge = data.get('is_rechallenge', False)

    # 检查冷却
    remaining = wdm.get_list_cooldown_remaining(session['user'], list_name)
    if remaining > 0:
        return jsonify({'success': False, 'error': f'还需等待 {remaining} 秒后才能再次答题', 'cooldown': remaining})

    # "新学前必须复习"检查：仅"开始学习"受限制，"重新挑战"不受影响
    if not is_rechallenge:
        config, _ = wdm.get_effective_config(session['user'])
        review_mode = config.get('review_mode', 'none')
        # 向后兼容
        if review_mode == 'none' and config.get('require_review_before_study', False):
            review_mode = 'all'

        need_review = False
        if review_mode == 'all':
            review_count = wdm.get_review_count(session['user'])
            if review_count > 0:
                need_review = True
        elif review_mode == 'once':
            review_count = wdm.get_review_count(session['user'])
            if review_count > 0 and not wdm.is_review_once_cleared(session['user']):
                need_review = True

        if need_review:
            return jsonify({
                'success': False,
                'need_review': True,
                'review_count': review_count,
                'error': f'你有 {review_count} 个单词待复习，请先完成复习再开始新背'
            })

    words = wdm.load_words()
    if list_name not in words:
        return jsonify({'success': False, 'error': '列表不存在'})
    word_list = words[list_name][:]
    random.shuffle(word_list)
    session['study_context'] = {
        'mode': 'study',
        'list_name': list_name,
        'words': word_list,
        'current_index': 0,
        'correct_count': 0,
        'total': len(word_list),
        'wrong_words': [],
        'quiz_mode': 'en2zh'   # 当前答题方向
    }
    return jsonify({'success': True})


@wordmaster_bp.route('/study/next', methods=['GET'])
@login_required
def study_next():
    ctx = session.get('study_context')
    if not ctx or ctx['mode'] != 'study':
        return jsonify({'error': '无进行中的学习'}), 400
    if ctx['current_index'] >= ctx['total']:
        list_name = ctx['list_name']
        correct = ctx['correct_count']
        total = ctx['total']
        quiz_mode = ctx.get('quiz_mode', 'en2zh')
        passed = (correct / total >= 0.8) if total > 0 else False

        wdm.add_learned_list(session['user'], list_name)
        wdm.add_quiz_result(session['user'], list_name, correct, total, mode="study", quiz_mode=quiz_mode)

        if not passed:
            # 未通过：设置冷却
            admin_cfg = wdm.load_admin_config()
            cooldown = admin_cfg.get('retry_cooldown_seconds', 60)
            wdm.set_list_cooldown(session['user'], list_name, cooldown)

        session.pop('study_context', None)

        # 打卡金币：新背通过 → +3金币（每日首次通过）
        coin_grant = None
        if passed:
            granted, new_bal = wdm.try_grant_checkin(
                session['user'], 'study', 3, f'每日打卡-新背通过 ({list_name})')
            if granted:
                coin_grant = {'coins': 3, 'new_balance': new_bal,
                              'reason': '新背通过获得 +3 金币！'}

        return jsonify({
            'finished': True,
            'passed': passed,
            'message': f'完成 {list_name} 学习！',
            'correct': correct,
            'total': total,
            'percent': round(correct / total * 100, 1) if total > 0 else 0,
            'list_name': list_name,
            'coin_grant': coin_grant
        })
    word = ctx['words'][ctx['current_index']]
    return jsonify({
        'finished': False,
        'word': word,
        'index': ctx['current_index'] + 1,
        'total': ctx['total']
    })


@wordmaster_bp.route('/study/submit', methods=['POST'])
@login_required
def study_submit():
    ctx = session.get('study_context')
    if not ctx or ctx['mode'] != 'study':
        return jsonify({'error': '无进行中的学习'}), 400
    data = request.get_json()
    mode = data.get('mode', 'en2zh')
    user_answer = data.get('answer', '').strip()
    word = ctx['words'][ctx['current_index']]
    word_en = word['word']
    word_zh = word['meaning']
    config, _ = wdm.get_effective_config(session['user'])

    # 更新当前 quiz_mode
    ctx['quiz_mode'] = mode
    session['study_context'] = ctx

    if mode == 'en2zh' or mode == 'audio2zh':
        correct, method, ai_response = judge_en2zh(user_answer, word_zh, word_en, config)
    else:
        correct, method = judge_zh2en(user_answer, word_en)
        ai_response = None

    wdm.update_word_review(session['user'], ctx['list_name'], word_en, correct)

    if correct:
        ctx['correct_count'] += 1
        ctx['current_index'] += 1
        session['study_context'] = ctx
        resp_data = {'correct': True, 'message': '✓ 回答正确！', 'method': method}
        if ai_response is not None:
            resp_data['ai_response'] = ai_response
        return jsonify(resp_data)
    else:
        # 答错：不推进 index，返回正确答案，等待前端点"下一题"后再推进
        ctx.setdefault('wrong_words', [])
        if word_en not in ctx['wrong_words']:
            ctx['wrong_words'].append(word_en)
        session['study_context'] = ctx
        resp_data = {
            'correct': False,
            'expected': word_en if mode == 'zh2en' else word_zh,
            'message': '✗ 回答错误',
            'method': method,
        }
        if ai_response is not None:
            resp_data['ai_response'] = ai_response
        resp_data['need_advance'] = True   # 告知前端需要手动推进
        return jsonify(resp_data)


@wordmaster_bp.route('/study/advance', methods=['POST'])
@login_required
def study_advance():
    """答错后手动推进到下一题"""
    ctx = session.get('study_context')
    if not ctx or ctx['mode'] != 'study':
        return jsonify({'error': '无进行中的学习'}), 400
    ctx['current_index'] += 1
    session['study_context'] = ctx
    return jsonify({'success': True})


@wordmaster_bp.route('/study/restart', methods=['POST'])
@login_required
def study_restart():
    """切换答题模式：保留词单，重置进度，切换quiz_mode"""
    ctx = session.get('study_context')
    if not ctx or ctx['mode'] != 'study':
        return jsonify({'error': '无进行中的学习'}), 400
    data = request.get_json() or {}
    new_mode = data.get('mode', 'en2zh')
    if new_mode not in ('en2zh', 'zh2en', 'audio2zh'):
        new_mode = 'en2zh'
    # 保留词单，重新打乱，重置进度
    random.shuffle(ctx['words'])
    ctx['quiz_mode'] = new_mode
    ctx['current_index'] = 0
    ctx['correct_count'] = 0
    session['study_context'] = ctx
    return jsonify({'success': True})


# ---------- 复习 ----------
@wordmaster_bp.route('/review')
@login_required
def review():
    config, controlled_fields = wdm.get_effective_config(session['user'])
    review_count = wdm.get_review_count(session['user'])
    review_cooldown = wdm.get_list_cooldown_remaining(session['user'], 'review')
    return render_template(
        'review.html',
        review_count=review_count,
        default_limit=config.get('review_count', 20),
        review_cooldown=review_cooldown,
        review_count_controlled='review_count' in controlled_fields
    )


@wordmaster_bp.route('/review/start', methods=['POST'])
@login_required
def review_start():
    # 检查冷却
    remaining = wdm.get_list_cooldown_remaining(session['user'], 'review')
    if remaining > 0:
        return jsonify({'success': False, 'message': f'复习未通过，还需等待 {remaining} 秒后才能再次复习', 'cooldown': remaining})

    data = request.get_json()
    config, _ = wdm.get_effective_config(session['user'])
    limit = data.get('limit', config.get('review_count', 20))
    review_words = wdm.get_review_words(session['user'], limit)
    if not review_words:
        return jsonify({'success': False, 'message': '当前没有需要复习的单词 🎉'})
    shuffled = review_words[:]
    random.shuffle(shuffled)
    session['review_context'] = {
        'mode': 'review',
        'words': shuffled,
        'current_index': 0,
        'correct_count': 0,
        'total': len(shuffled),
        'wrong_words': [],
        'quiz_mode': 'en2zh'
    }
    return jsonify({'success': True, 'total': len(shuffled)})


@wordmaster_bp.route('/review/next', methods=['GET'])
@login_required
def review_next():
    ctx = session.get('review_context')
    if not ctx or ctx['mode'] != 'review':
        return jsonify({'error': '无进行中的复习'}), 400
    if ctx['current_index'] >= ctx['total']:
        correct = ctx['correct_count']
        total = ctx['total']
        correct_rate = correct / total if total > 0 else 0
        passed = correct_rate >= 0.8
        quiz_mode = ctx.get('quiz_mode', 'en2zh')
        wrong_words = ctx.get('wrong_words', [])
        wdm.add_quiz_result(session['user'], 'review', correct, total, mode="review", quiz_mode=quiz_mode)

        if not passed:
            # 未通过：设置冷却时间（限制立即重试）
            admin_cfg = wdm.load_admin_config()
            cooldown = admin_cfg.get('retry_cooldown_seconds', 60)
            wdm.set_list_cooldown(session['user'], 'review', cooldown)
        else:
            # 通过：若当前是 once 模式，记录今日已完成
            config, _ = wdm.get_effective_config(session['user'])
            if config.get('review_mode', 'none') == 'once':
                wdm.set_review_once_cleared(session['user'])

        session.pop('review_context', None)

        # 本轮结束后，待复习池中仍剩余的单词数（包括答错的+未被抽到的）
        remaining_count = wdm.get_review_count(session['user'])

        # 打卡金币：复习通过 → +5金币（每日首次通过）
        coin_grant = None
        if passed:
            granted, new_bal = wdm.try_grant_checkin(
                session['user'], 'review', 5, '每日打卡-复习任务通过')
            if granted:
                coin_grant = {'coins': 5, 'new_balance': new_bal,
                              'reason': '复习通过获得 +5 金币！'}

        return jsonify({
            'finished': True,
            'passed': passed,
            'correct': correct,
            'total': total,
            'percent': round(correct_rate * 100, 1),
            'wrong_count': len(wrong_words),
            'remaining_review': remaining_count,
            'coin_grant': coin_grant,
            'message': f'复习{"通过" if passed else "未通过"}！正确率 {correct_rate*100:.1f}%'
        })
    word = ctx['words'][ctx['current_index']]
    return jsonify({
        'finished': False,
        'word': word,
        'index': ctx['current_index'] + 1,
        'total': ctx['total']
    })


@wordmaster_bp.route('/review/submit', methods=['POST'])
@login_required
def review_submit():
    ctx = session.get('review_context')
    if not ctx or ctx['mode'] != 'review':
        return jsonify({'error': '无进行中的复习'}), 400
    data = request.get_json()
    mode = data.get('mode', 'en2zh')
    user_answer = data.get('answer', '').strip()
    word = ctx['words'][ctx['current_index']]
    word_en = word['word']
    word_zh = word['meaning']
    config, _ = wdm.get_effective_config(session['user'])

    ctx['quiz_mode'] = mode
    session['review_context'] = ctx

    if mode == 'en2zh' or mode == 'audio2zh':
        correct, method, ai_response = judge_en2zh(user_answer, word_zh, word_en, config)
    else:
        correct, method = judge_zh2en(user_answer, word_en)
        ai_response = None

    wdm.update_word_review(session['user'], word['list'], word_en, correct)

    if correct:
        ctx['correct_count'] += 1
        ctx['current_index'] += 1
        session['review_context'] = ctx
        resp_data = {'correct': True, 'message': '✓ 回答正确！', 'method': method}
        if ai_response is not None:
            resp_data['ai_response'] = ai_response
        return jsonify(resp_data)
    else:
        ctx.setdefault('wrong_words', [])
        if word_en not in ctx['wrong_words']:
            ctx['wrong_words'].append(word_en)
        session['review_context'] = ctx
        resp_data = {
            'correct': False,
            'expected': word_en if mode == 'zh2en' else word_zh,
            'message': '✗ 回答错误',
            'method': method,
        }
        if ai_response is not None:
            resp_data['ai_response'] = ai_response
        resp_data['need_advance'] = True
        return jsonify(resp_data)


@wordmaster_bp.route('/review/advance', methods=['POST'])
@login_required
def review_advance():
    """答错后手动推进到下一题"""
    ctx = session.get('review_context')
    if not ctx or ctx['mode'] != 'review':
        return jsonify({'error': '无进行中的复习'}), 400
    ctx['current_index'] += 1
    session['review_context'] = ctx
    return jsonify({'success': True})


@wordmaster_bp.route('/review/restart', methods=['POST'])
@login_required
def review_restart():
    """切换答题模式：保留词单，重置进度，切换quiz_mode"""
    ctx = session.get('review_context')
    if not ctx or ctx['mode'] != 'review':
        return jsonify({'error': '无进行中的复习'}), 400
    data = request.get_json() or {}
    new_mode = data.get('mode', 'en2zh')
    if new_mode not in ('en2zh', 'zh2en', 'audio2zh'):
        new_mode = 'en2zh'
    random.shuffle(ctx['words'])
    ctx['quiz_mode'] = new_mode
    ctx['current_index'] = 0
    ctx['correct_count'] = 0
    session['review_context'] = ctx
    return jsonify({'success': True})


# ---------- 单词导入 ----------
@wordmaster_bp.route('/import', methods=['GET'])
@login_required
def import_words():
    list_info = wdm.get_list_word_count()
    return render_template('import.html', list_info=list_info)


@wordmaster_bp.route('/import/upload', methods=['POST'])
@login_required
def import_upload():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '未选择文件'})
    file = request.files['file']
    if not file.filename:
        return jsonify({'success': False, 'error': '未选择文件'})

    filename = file.filename.lower()
    content = file.read()

    try:
        if filename.endswith('.csv'):
            rows = wdm.parse_csv_content(content)
        elif filename.endswith('.txt'):
            rows = wdm.parse_txt_content(content)
        else:
            return jsonify({'success': False, 'error': '只支持 CSV 或 TXT 文件'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'文件解析失败：{str(e)}'})

    if not rows:
        return jsonify({'success': False, 'error': '未解析到有效数据，请检查文件格式'})

    preview = rows[:50]
    return jsonify({
        'success': True,
        'total': len(rows),
        'preview': preview,
        'rows_json': json.dumps(rows)
    })


@wordmaster_bp.route('/import/confirm', methods=['POST'])
@login_required
def import_confirm():
    data = request.get_json()
    rows_json = data.get('rows_json', '[]')
    overwrite = data.get('overwrite', False)
    try:
        rows = json.loads(rows_json)
    except Exception:
        return jsonify({'success': False, 'error': '数据格式错误'})

    imported, skipped, list_names = wdm.import_words_from_data(rows, overwrite=overwrite)
    return jsonify({
        'success': True,
        'imported': imported,
        'skipped': skipped,
        'list_names': list_names,
        'message': f'成功导入 {imported} 个单词，跳过 {skipped} 个'
    })


# ---------- 用户设置 ----------
@wordmaster_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    username = session['user']
    config, controlled_fields = wdm.get_effective_config(username)
    msg = None

    if request.method == 'POST':
        action = request.form.get('action', 'save_config')
        if action == 'save_config':
            user_history = wdm.load_user_history(username)
            prefs = user_history.get("user_prefs", {})

            # 只更新未被管控的字段
            if 'review_count' not in controlled_fields:
                try:
                    prefs['review_count'] = int(request.form.get('review_count', 20))
                except ValueError:
                    prefs['review_count'] = 20

            if 'chatgpt_api_key' not in controlled_fields:
                prefs['chatgpt_api_key'] = request.form.get('api_key', '').strip()
                prefs['chatgpt_base_url'] = request.form.get('base_url', 'https://api.openai.com/v1').strip()
                prefs['ai_model'] = request.form.get('ai_model', 'deepseek-chat').strip()

            if 'require_review_before_study' not in controlled_fields:
                prefs['require_review_before_study'] = 'require_review' in request.form

            if 'require_both_modes' not in controlled_fields:
                prefs['require_both_modes'] = 'require_both_modes' in request.form

            wdm.save_user_prefs(username, prefs)
            # 刷新
            config, controlled_fields = wdm.get_effective_config(username)
            msg = ('success', '设置已保存！')

        elif action == 'change_password':
            old_pwd = request.form.get('old_password', '')
            new_pwd = request.form.get('new_password', '')
            confirm_pwd = request.form.get('confirm_password', '')
            if new_pwd != confirm_pwd:
                msg = ('error', '两次新密码不一致')
            else:
                success, text = wdm.change_password(username, old_pwd, new_pwd)
                msg = ('success' if success else 'error', text)

    is_api_controlled = 'chatgpt_api_key' in controlled_fields
    return render_template(
        'settings.html',
        config=config,
        controlled_fields=controlled_fields,
        is_api_controlled=is_api_controlled,
        msg=msg
    )


# ---------- 超级管理员 ----------
@wordmaster_bp.route('/admin', methods=['GET', 'POST'])
@admin_required
def admin():
    admin_cfg = wdm.load_admin_config()
    all_users = wdm.get_all_usernames()
    msg = None

    if request.method == 'POST':
        action = request.form.get('action', '')

        if action == 'save_api':
            admin_cfg['shared_api_key'] = request.form.get('shared_api_key', '').strip()
            admin_cfg['shared_base_url'] = request.form.get('shared_base_url', 'https://api.openai.com/v1').strip()
            admin_cfg['shared_ai_model'] = request.form.get('shared_ai_model', 'deepseek-chat').strip()
            admin_cfg['retry_cooldown_seconds'] = int(request.form.get('retry_cooldown_seconds', 60))
            # 音译汉 & 英译汉发音设置
            admin_cfg['tts_in_en2zh'] = 'tts_in_en2zh' in request.form
            admin_cfg['audio2zh_enabled'] = 'audio2zh_enabled' in request.form
            # 判定方式
            judge_mode = request.form.get('judge_mode', 'local_then_ai').strip()
            if judge_mode in ('local_only', 'ai_only', 'local_then_ai'):
                admin_cfg['judge_mode'] = judge_mode
            # 允许使用共享API的用户
            allowed = request.form.getlist('allowed_api_users')
            admin_cfg['allowed_api_users'] = allowed
            wdm.save_admin_config(admin_cfg)
            msg = ('success', 'API 设置已保存！')

        elif action == 'save_user_control':
            target_user = request.form.get('target_user', '')
            if target_user in all_users:
                # 构建管控字段
                controlled = {}
                field_enable = request.form.getlist('control_fields')

                if 'review_count' in field_enable:
                    try:
                        controlled['review_count'] = int(request.form.get('ctrl_review_count', 20))
                    except ValueError:
                        controlled['review_count'] = 20

                if 'require_review_before_study' in field_enable:
                    controlled['require_review_before_study'] = 'ctrl_require_review' in request.form

                if 'review_mode' in field_enable:
                    controlled['review_mode'] = request.form.get('ctrl_review_mode', 'none')

                if 'require_both_modes' in field_enable:
                    controlled['require_both_modes'] = 'ctrl_require_both_modes' in request.form

                # 更新
                if not admin_cfg.get('controlled_users'):
                    admin_cfg['controlled_users'] = {}
                admin_cfg['controlled_users'][target_user] = controlled
                wdm.save_admin_config(admin_cfg)
                msg = ('success', f'已对用户 {target_user} 应用管控设置！')

        elif action == 'remove_user_control':
            target_user = request.form.get('target_user', '')
            if target_user in admin_cfg.get('controlled_users', {}):
                del admin_cfg['controlled_users'][target_user]
                wdm.save_admin_config(admin_cfg)
                msg = ('success', f'已取消对用户 {target_user} 的管控！')

        elif action == 'reset_user_password':
            target_user = request.form.get('target_user', '').strip()
            new_pwd = request.form.get('new_password', '')
            confirm_pwd = request.form.get('confirm_password', '')
            if not target_user or target_user not in all_users:
                msg = ('error', '目标用户不存在')
            elif new_pwd != confirm_pwd:
                msg = ('error', '两次输入的新密码不一致')
            else:
                success, text = wdm.admin_reset_password(target_user, new_pwd)
                msg = ('success' if success else 'error', text)

    # 刷新
    admin_cfg = wdm.load_admin_config()
    all_users = wdm.get_all_usernames()
    all_users_all = wdm.get_all_usernames_with_admin()
    return render_template(
        'admin.html',
        admin_cfg=admin_cfg,
        all_users=all_users,
        all_users_all=all_users_all,
        msg=msg
    )


@wordmaster_bp.route('/admin/user_control_data', methods=['GET'])
@admin_required
def admin_user_control_data():
    """获取指定用户当前管控设置（AJAX）"""
    target_user = request.args.get('user', '')
    admin_cfg = wdm.load_admin_config()
    controlled = admin_cfg.get('controlled_users', {}).get(target_user, {})
    config, _ = wdm.get_effective_config(target_user)
    return jsonify({
        'controlled': controlled,
        'effective_config': config
    })

@wordmaster_bp.route('/admin/grant_coins', methods=['POST'])
@admin_required
def admin_grant_coins():
    """管理员发放金币给用户（需验证管理员密码）"""
    admin_user = session['user']
    data = request.get_json()
    target_user = data.get('target_user', '').strip()
    amount = int(data.get('amount', 0))
    message = data.get('message', '').strip()
    admin_password = data.get('admin_password', '')

    # 验证管理员密码
    if not wdm.verify_user(admin_user, admin_password):
        return jsonify({'success': False, 'message': '管理员密码错误'})

    # 校验目标用户
    all_users = wdm.get_all_usernames_with_admin()
    if not target_user or target_user not in all_users:
        return jsonify({'success': False, 'message': '目标用户不存在'})

    if amount == 0:
        return jsonify({'success': False, 'message': '金币数量不能为 0'})

    # 组装流水备注
    reason = f'管理员 {admin_user} 发放：{message}' if message else f'管理员 {admin_user} 发放'
    new_bal = wdm.add_coins(target_user, amount, reason)
    if new_bal is False:
        return jsonify({'success': False, 'message': '操作失败，用户金币余额不足（扣款时）'})

    return jsonify({
        'success': True,
        'new_balance': new_bal,
        'message': f'成功向 {target_user} {"发放" if amount > 0 else "扣除"} {abs(amount)} 金币！'
    })


# ---------- 统计 ----------
@wordmaster_bp.route('/stats')
@login_required
def stats():
    username = session['user']

    # 管理员可以查看指定用户的统计
    view_user = request.args.get('user', '').strip()
    if view_user and wdm.is_admin(username) and view_user != username:
        # 验证目标用户存在
        if view_user not in wdm.get_all_usernames():
            return redirect(url_for('wordmaster.stats'))
        target_user = view_user
        is_viewing_other = True
    else:
        target_user = username
        is_viewing_other = False
    history = wdm.load_user_history(target_user)
    daily_status = wdm.get_daily_status(target_user)

    now = datetime.now()
    try:
        year = int(request.args.get('year', now.year))
        month = int(request.args.get('month', now.month))
    except ValueError:
        year, month = now.year, now.month

    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1

    days_in_month = calendar.monthrange(year, month)[1]
    first_weekday = calendar.weekday(year, month, 1)

    cal = []
    week = [''] * first_weekday
    for d in range(1, days_in_month + 1):
        week.append(d)
        if len(week) == 7:
            cal.append(week)
            week = []
    if week:
        while len(week) < 7:
            week.append('')
        cal.append(week)

    quiz_results = history.get('quiz_results', [])

    # 分模式统计
    en2zh_results = [r for r in quiz_results if r.get('quiz_mode', 'en2zh') == 'en2zh']
    zh2en_results = [r for r in quiz_results if r.get('quiz_mode', 'en2zh') == 'zh2en']
    audio2zh_results = [r for r in quiz_results if r.get('quiz_mode', 'en2zh') == 'audio2zh']

    # 分类统计（新背/复习/考试）
    study_results = [r for r in quiz_results if r.get('mode', 'study') == 'study']
    review_results = [r for r in quiz_results if r.get('mode', 'study') == 'review']
    exam_results = [r for r in quiz_results if r.get('mode', 'study') == 'exam']

    def calc_stats(results):
        total_q = len(results)
        total_w = sum(r.get('total', 0) for r in results)
        total_c = sum(r.get('correct', 0) for r in results)
        avg_p = round(total_c / total_w * 100, 1) if total_w > 0 else 0
        passed = sum(1 for r in results if r.get('passed', False))
        pass_rate = round(passed / total_q * 100, 1) if total_q > 0 else 0
        return {'count': total_q, 'words': total_w, 'correct': total_c,
                'avg_percent': avg_p, 'passed': passed, 'pass_rate': pass_rate}

    total_quizzes = len(quiz_results)
    total_words = sum(r.get('total', 0) for r in quiz_results)
    total_correct = sum(r.get('correct', 0) for r in quiz_results)
    avg_percent = round(total_correct / total_words * 100, 1) if total_words > 0 else 0
    learned_lists = history.get('learned_lists', [])

    return render_template(
        'stats.html',
        history=history,
        daily_status=daily_status,
        year=year,
        month=month,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
        calendar_cells=cal,
        total_quizzes=total_quizzes,
        total_words=total_words,
        avg_percent=avg_percent,
        learned_lists=learned_lists,
        en2zh_stats=calc_stats(en2zh_results),
        zh2en_stats=calc_stats(zh2en_results),
        audio2zh_stats=calc_stats(audio2zh_results),
        study_stats=calc_stats(study_results),
        review_stats=calc_stats(review_results),
        exam_stats=calc_stats(exam_results),
        view_user=target_user if is_viewing_other else None,
        all_users=wdm.get_all_usernames() if wdm.is_admin(username) else []
    )


# ---------- 学习/复习/考试统一界面 ----------
@wordmaster_bp.route('/learn')
@login_required
def learn():
    quiz_type = request.args.get('type', 'study')
    if quiz_type not in ['study', 'review', 'exam']:
        return redirect(url_for('wordmaster.coins'))
    quiz_mode = request.args.get('mode', 'en2zh')
    if quiz_mode not in ['en2zh', 'zh2en', 'audio2zh']:
        quiz_mode = 'en2zh'
    admin_cfg = wdm.load_admin_config()
    tts_in_en2zh = admin_cfg.get('tts_in_en2zh', False)
    audio2zh_enabled = admin_cfg.get('audio2zh_enabled', True)
    ticket_active = wdm.is_ticket_active()
    # 考试模式：读取 slot 参数
    exam_slot = request.args.get('slot', '')
    # 考试模式：没有考试上下文时重定向到考试列表
    if quiz_type == 'exam' and not session.get('exam_context'):
        return redirect(url_for('wordmaster.exam'))

    # 考试模式：从 exam_context 获取多模式列表和通过率
    exam_quiz_modes = []
    exam_pass_rate = 80
    if quiz_type == 'exam':
        ctx = session.get('exam_context', {})
        exam_quiz_modes = ctx.get('quiz_modes', [quiz_mode])
        exam_pass_rate = ctx.get('pass_rate', 80)

    return render_template('check.html', type=quiz_type, mode=quiz_mode,
                           tts_in_en2zh=tts_in_en2zh, audio2zh_enabled=audio2zh_enabled,
                           ticket_active=ticket_active, exam_slot=exam_slot,
                           exam_quiz_modes=exam_quiz_modes, exam_pass_rate=exam_pass_rate)


# ============================================================
# 考试系统
# ============================================================
@wordmaster_bp.route('/exam')
@login_required
def exam():
    username = session['user']
    exams = wdm.get_user_exams(username)
    today = datetime.now().strftime("%Y-%m-%d")
    # 格式化显示信息
    exam_display = []
    for i, ex in enumerate(exams):
        if ex:
            # 获取今日考试次数信息
            attempt_info = wdm.get_exam_attempt_info(username, i + 1)
            daily_limit = ex.get('daily_limit', 99)
            today_count = attempt_info['count']
            remaining = max(0, daily_limit - today_count)

            # 格式化考核方式显示
            modes = ex.get('quiz_modes', [ex.get('quiz_mode', 'en2zh')])
            mode_tags = []
            for m in modes:
                if m == 'en2zh':
                    mode_tags.append('🔤 英→中')
                elif m == 'zh2en':
                    mode_tags.append('🔡 中→英')
                elif m == 'audio2zh':
                    mode_tags.append('🎧 音→中')
            modes_display = ' / '.join(mode_tags) if mode_tags else '🔤 英→中'

            exam_display.append({
                'slot': i + 1,
                'ranges_display': wdm.format_ranges_display(ex['ranges']),
                'capacity': ex['capacity'],
                'quiz_modes': modes,
                'modes_display': modes_display,
                'pass_rate': ex.get('pass_rate', 80),
                'daily_limit': daily_limit,
                'today_count': today_count,
                'remaining': remaining,
                'created_at': ex.get('created_at', ''),
                'active': True
            })
        else:
            exam_display.append({'slot': i + 1, 'active': False})
    # 构建 JS 配置字典（避免 onclick 中 JSON 双引号冲突）
    exam_configs = {}
    for ex in exam_display:
        if ex.get('active'):
            exam_configs[ex['slot']] = {
                'modes': ex['quiz_modes'],
                'pass_rate': ex['pass_rate'],
                'remaining': ex['remaining']
            }

    ticket_active = wdm.is_ticket_active()
    return render_template('exam.html', exams=exam_display, exam_configs=exam_configs,
                           ticket_active=ticket_active)


@wordmaster_bp.route('/exam/start', methods=['POST'])
@login_required
def exam_start():
    username = session['user']
    data = request.get_json()
    slot = int(data.get('slot', 0))
    if slot < 1 or slot > 5:
        return jsonify({'success': False, 'message': '无效的考试槽位'})

    exams = wdm.get_user_exams(username)
    exam_cfg = exams[slot - 1]
    if not exam_cfg:
        return jsonify({'success': False, 'message': '该考试槽位未配置'})

    # 检查每日考试次数限制
    daily_limit = exam_cfg.get('daily_limit', 99)
    attempt_info = wdm.get_exam_attempt_info(username, slot)
    today_count = attempt_info['count']
    if today_count >= daily_limit:
        return jsonify({
            'success': False,
            'message': f'今日已参加 {today_count} 次考试，已达每日上限 {daily_limit} 次，请明天再试'
        })

    # 每次考试都重新随机生成试卷
    words = wdm.generate_exam_words(exam_cfg['ranges'], exam_cfg['capacity'])
    if not words:
        return jsonify({'success': False, 'message': '考试范围内没有单词'})

    # 记录一次考试开始
    new_count = wdm.record_exam_attempt(username, slot)
    remaining = max(0, daily_limit - new_count)

    # 使用第一种模式作为初始模式（如果指定了模式且在允许列表中则用指定的）
    quiz_modes = exam_cfg.get('quiz_modes', ['en2zh'])
    requested_mode = data.get('mode', '')
    if requested_mode in quiz_modes:
        initial_mode = requested_mode
    else:
        initial_mode = quiz_modes[0]

    session['exam_context'] = {
        'mode': 'exam',
        'slot': slot,
        'words': words,
        'current_index': 0,
        'correct_count': 0,
        'total': len(words),
        'wrong_words': [],
        'quiz_mode': initial_mode,
        'quiz_modes': quiz_modes,
        'pass_rate': exam_cfg.get('pass_rate', 80),
        'ranges_display': wdm.format_ranges_display(exam_cfg['ranges']),
        'today_count': new_count,
        'daily_limit': daily_limit
    }
    return jsonify({
        'success': True,
        'total': len(words),
        'quiz_mode': initial_mode,
        'quiz_modes': quiz_modes,
        'today_count': new_count,
        'daily_limit': daily_limit,
        'remaining': remaining
    })


@wordmaster_bp.route('/exam/next', methods=['GET'])
@login_required
def exam_next():
    ctx = session.get('exam_context')
    if not ctx or ctx['mode'] != 'exam':
        return jsonify({'error': '无进行中的考试'}), 400
    if ctx['current_index'] >= ctx['total']:
        correct = ctx['correct_count']
        total = ctx['total']
        quiz_mode = ctx.get('quiz_mode', 'en2zh')
        pass_rate = ctx.get('pass_rate', 80)
        percent = round(correct / total * 100, 1) if total > 0 else 0
        passed = (correct / total * 100 >= pass_rate) if total > 0 else False
        slot = ctx.get('slot', 0)

        wdm.add_quiz_result(session['user'], f'考试{slot}', correct, total,
                           mode="exam", quiz_mode=quiz_mode)

        # 标记本次考试为已完成
        wdm.finish_exam_attempt(session['user'], slot, percent, passed)

        session.pop('exam_context', None)

        return jsonify({
            'finished': True,
            'passed': passed,
            'pass_rate': pass_rate,
            'message': f'完成考试{slot}！',
            'correct': correct,
            'total': total,
            'percent': percent,
            'slot': slot
        })
    word = ctx['words'][ctx['current_index']]
    return jsonify({
        'finished': False,
        'word': word,
        'index': ctx['current_index'] + 1,
        'total': ctx['total']
    })


@wordmaster_bp.route('/exam/submit', methods=['POST'])
@login_required
def exam_submit():
    ctx = session.get('exam_context')
    if not ctx or ctx['mode'] != 'exam':
        return jsonify({'error': '无进行中的考试'}), 400
    data = request.get_json()
    mode = data.get('mode', 'en2zh')
    user_answer = data.get('answer', '').strip()
    word = ctx['words'][ctx['current_index']]
    word_en = word['word']
    word_zh = word['meaning']
    config, _ = wdm.get_effective_config(session['user'])

    ctx['quiz_mode'] = mode
    session['exam_context'] = ctx

    if mode == 'en2zh' or mode == 'audio2zh':
        correct, method, ai_response = judge_en2zh(user_answer, word_zh, word_en, config)
    else:
        correct, method = judge_zh2en(user_answer, word_en)
        ai_response = None

    if correct:
        ctx['correct_count'] += 1
        ctx['current_index'] += 1
        session['exam_context'] = ctx
        resp_data = {'correct': True, 'message': '✓ 回答正确！', 'method': method}
        if ai_response is not None:
            resp_data['ai_response'] = ai_response
        return jsonify(resp_data)
    else:
        ctx.setdefault('wrong_words', [])
        if word_en not in ctx['wrong_words']:
            ctx['wrong_words'].append(word_en)
        session['exam_context'] = ctx
        resp_data = {
            'correct': False,
            'expected': word_en if mode == 'zh2en' else word_zh,
            'message': '✗ 回答错误',
            'method': method,
        }
        if ai_response is not None:
            resp_data['ai_response'] = ai_response
        resp_data['need_advance'] = True
        return jsonify(resp_data)


@wordmaster_bp.route('/exam/advance', methods=['POST'])
@login_required
def exam_advance():
    """答错后手动推进到下一题"""
    ctx = session.get('exam_context')
    if not ctx or ctx['mode'] != 'exam':
        return jsonify({'error': '无进行中的考试'}), 400
    ctx['current_index'] += 1
    session['exam_context'] = ctx
    return jsonify({'success': True})


@wordmaster_bp.route('/exam/restart', methods=['POST'])
@login_required
def exam_restart():
    """考试重新开始/切换模式：重新随机生成试卷，并记录一次新的考试次数"""
    ctx = session.get('exam_context')
    if not ctx or ctx['mode'] != 'exam':
        return jsonify({'error': '无进行中的考试'}), 400
    slot = ctx.get('slot', 0)
    username = session['user']

    exams = wdm.get_user_exams(username)
    exam_cfg = exams[slot - 1] if slot > 0 else None
    if not exam_cfg:
        return jsonify({'error': '考试配置不存在'}), 400

    # 检查每日考试次数限制
    daily_limit = exam_cfg.get('daily_limit', 99)
    attempt_info = wdm.get_exam_attempt_info(username, slot)
    today_count = attempt_info['count']
    if today_count >= daily_limit:
        return jsonify({
            'error': f'今日已参加 {today_count} 次考试，已达每日上限 {daily_limit} 次'
        }), 400

    # 重新生成试卷
    words = wdm.generate_exam_words(exam_cfg['ranges'], exam_cfg['capacity'])

    # 记录一次新的考试
    new_count = wdm.record_exam_attempt(username, slot)
    remaining = max(0, daily_limit - new_count)

    # 获取请求的模式
    data = request.get_json() or {}
    quiz_modes = exam_cfg.get('quiz_modes', ['en2zh'])
    requested_mode = data.get('mode', '')
    if requested_mode in quiz_modes:
        new_mode = requested_mode
    else:
        new_mode = quiz_modes[0]

    ctx['words'] = words
    ctx['current_index'] = 0
    ctx['correct_count'] = 0
    ctx['wrong_words'] = []
    ctx['quiz_mode'] = new_mode
    ctx['quiz_modes'] = quiz_modes
    ctx['pass_rate'] = exam_cfg.get('pass_rate', 80)
    ctx['today_count'] = new_count
    ctx['daily_limit'] = daily_limit
    session['exam_context'] = ctx
    return jsonify({
        'success': True,
        'today_count': new_count,
        'daily_limit': daily_limit,
        'remaining': remaining
    })


@wordmaster_bp.route('/exam/ticket_override', methods=['POST'])
@login_required
def exam_ticket_override():
    """免错券：将当前答错的题目计为正确，并推进"""
    ctx = session.get('exam_context')
    if not ctx or ctx['mode'] != 'exam':
        return jsonify({'error': '无进行中的考试'}), 400
    ctx['correct_count'] = ctx.get('correct_count', 0) + 1
    ctx['current_index'] += 1
    word = ctx['words'][ctx['current_index'] - 1] if ctx['current_index'] > 0 else None
    if word:
        ctx.setdefault('wrong_words', [])
        if word['word'] in ctx['wrong_words']:
            ctx['wrong_words'].remove(word['word'])
    session['exam_context'] = ctx
    return jsonify({'success': True})


# ============================================================
# 管理员：考试管理
# ============================================================
@wordmaster_bp.route('/admin/exam')
@admin_required
def admin_exam():
    all_users = wdm.get_all_usernames()
    sorted_lists = wdm.get_sorted_list_names()
    # 构建 list 索引映射 (1-based)
    list_index_map = [(i + 1, name) for i, name in enumerate(sorted_lists)]
    return render_template('exam_admin.html', all_users=all_users, list_index_map=list_index_map)


@wordmaster_bp.route('/admin/exam_data', methods=['GET'])
@admin_required
def admin_exam_data():
    """获取指定用户的考试配置（AJAX）+ 今日考试次数信息"""
    target_user = request.args.get('user', '')
    if not target_user:
        return jsonify({'exams': [None] * 5})
    exams = wdm.get_user_exams(target_user)
    # 附加每个槽位的今日考试次数信息
    exams_with_attempts = []
    for i, ex in enumerate(exams):
        if ex:
            attempt_info = wdm.get_exam_attempt_info(target_user, i + 1)
            ex['today_count'] = attempt_info['count']
            ex['today_attempts'] = attempt_info['attempts']
            exams_with_attempts.append(ex)
        else:
            exams_with_attempts.append(None)
    return jsonify({'exams': exams_with_attempts})


@wordmaster_bp.route('/admin/exam_save', methods=['POST'])
@admin_required
def admin_exam_save():
    data = request.get_json()
    target_user = data.get('target_user', '').strip()
    slot = int(data.get('slot', 0))
    ranges = data.get('ranges', [])
    capacity = int(data.get('capacity', 10))
    quiz_modes = data.get('quiz_modes', [])
    pass_rate = int(data.get('pass_rate', 80))
    daily_limit = int(data.get('daily_limit', 99))

    if not target_user:
        return jsonify({'success': False, 'message': '请选择用户'})
    if slot < 1 or slot > 5:
        return jsonify({'success': False, 'message': '无效的考试槽位'})
    if not ranges:
        return jsonify({'success': False, 'message': '请至少添加一个考试范围'})
    if capacity < 1:
        return jsonify({'success': False, 'message': '考试容量至少为 1'})
    if not quiz_modes or not isinstance(quiz_modes, list):
        return jsonify({'success': False, 'message': '请至少选择一种考核方式'})
    # 验证 quiz_modes 内容
    valid_modes = [m for m in quiz_modes if m in ('en2zh', 'zh2en', 'audio2zh')]
    if not valid_modes:
        return jsonify({'success': False, 'message': '考核方式无效'})
    quiz_modes = valid_modes
    # 验证通过率
    if pass_rate < 1 or pass_rate > 100:
        return jsonify({'success': False, 'message': '通过率必须在 1~100 之间'})
    # 验证每日次数
    if daily_limit < 1:
        return jsonify({'success': False, 'message': '每日考试次数至少为 1'})

    # 验证 ranges 格式（支持两种格式：[[1,3]] 或 [{"start":1,"end":3}]）
    clean_ranges = []
    for r in ranges:
        if isinstance(r, (list, tuple)) and len(r) >= 2:
            start, end = int(r[0]), int(r[1])
        elif isinstance(r, dict):
            start = int(r.get('start', 0))
            end = int(r.get('end', 0))
        else:
            return jsonify({'success': False, 'message': f'无效的范围格式：{r}'})
        if start < 1 or end < 1 or start > end:
            return jsonify({'success': False, 'message': f'无效的范围：{start}~{end}'})
        clean_ranges.append([start, end])

    wdm.save_user_exam(target_user, slot, clean_ranges, capacity, quiz_modes,
                      pass_rate=pass_rate, daily_limit=daily_limit)
    return jsonify({'success': True, 'message': f'已保存考试{slot}并推送给 {target_user}'})


@wordmaster_bp.route('/admin/exam_reset_attempts', methods=['POST'])
@admin_required
def admin_exam_reset_attempts():
    """管理员重置学生今日考试次数"""
    data = request.get_json()
    target_user = data.get('target_user', '').strip()
    slot = int(data.get('slot', 0))
    new_count = int(data.get('new_count', 0))
    if not target_user or slot < 1 or slot > 5:
        return jsonify({'success': False, 'message': '参数无效'})
    if new_count < 0:
        return jsonify({'success': False, 'message': '次数不能为负数'})
    wdm.reset_exam_attempts(target_user, slot, new_count)
    return jsonify({'success': True, 'message': f'已将 {target_user} 考试{slot} 今日次数重置为 {new_count}'})


@wordmaster_bp.route('/admin/exam_delete', methods=['POST'])
@admin_required
def admin_exam_delete():
    data = request.get_json()
    target_user = data.get('target_user', '').strip()
    slot = int(data.get('slot', 0))
    if not target_user or slot < 1 or slot > 5:
        return jsonify({'success': False, 'message': '参数无效'})
    wdm.delete_user_exam(target_user, slot)
    return jsonify({'success': True, 'message': f'已清除考试{slot}'})


# ============================================================
# 金币 / 打卡
# ============================================================
@wordmaster_bp.route('/coins')
@login_required
def coins():
    username = session['user']
    balance = wdm.get_coins_balance(username)
    ledger = wdm.get_coins_ledger(username, limit=50)
    checkin_done = wdm.get_checkin_status(username)
    ticket_count = wdm.get_ticket_count(username)
    return render_template('coins.html',
                           balance=balance,
                           ledger=ledger,
                           checkin_done=checkin_done,
                           ticket_count=ticket_count)


@wordmaster_bp.route('/coins/checkin_login', methods=['POST'])
@login_required
def coins_checkin_login():
    """首页手动领取每日登录金币"""
    username = session['user']
    # 必须当日已登录才能领取
    done = wdm.get_checkin_status(username)
    if 'login_visited' not in done:
        return jsonify({'success': False, 'message': '请先登录后再领取'})
    granted, new_bal = wdm.try_grant_checkin(username, 'login', 1, '每日首次登录签到')
    if granted:
        return jsonify({'success': True, 'coins': 1, 'new_balance': new_bal,
                        'message': '成功领取登录奖励 +1 金币！'})
    return jsonify({'success': False, 'message': '今日登录奖励已领取'})


@wordmaster_bp.route('/coins/balance', methods=['GET'])
@login_required
def coins_balance():
    return jsonify({'balance': wdm.get_coins_balance(session['user'])})


# ============================================================
# 商城
# ============================================================
@wordmaster_bp.route('/shop')
@login_required
def shop():
    username = session['user']
    products = wdm.get_products(active_only=True)
    my_orders = wdm.get_orders(username=username)
    balance = wdm.get_coins_balance(username)
    ticket_count = wdm.get_ticket_count(username)
    return render_template('shop.html',
                           products=products,
                           my_orders=my_orders,
                           balance=balance,
                           ticket_count=ticket_count)


@wordmaster_bp.route('/shop/buy', methods=['POST'])
@login_required
def shop_buy():
    username = session['user']
    data = request.get_json()
    pid = data.get('product_id')

    products = wdm.get_products(active_only=True)
    product = next((p for p in products if p['id'] == pid), None)
    if not product:
        return jsonify({'success': False, 'message': '商品不存在或已下架'})

    price = product['price']
    balance = wdm.get_coins_balance(username)
    if balance < price:
        return jsonify({'success': False, 'message': f'金币不足，当前余额 {balance} 个'})

    # 扣金币
    new_bal = wdm.add_coins(username, -price, f'购买 {product["name"]}')
    if new_bal is False:
        return jsonify({'success': False, 'message': '金币不足'})

    # 内置商品（免错券）直接发放，无需管理员审核
    if product.get('type') == 'builtin':
        wdm.add_tickets(username, 1)
        return jsonify({'success': True,
                        'message': f'购买成功！免错券 +1，当前余额 {new_bal} 金币',
                        'new_balance': new_bal,
                        'builtin': True})

    # 自定义商品：创建订单等待管理员发货
    oid = wdm.create_order(username, pid, product['name'], price)
    return jsonify({'success': True,
                    'message': f'购买成功，请等待管理员发货。当前余额 {new_bal} 金币',
                    'order_id': oid,
                    'new_balance': new_bal})


@wordmaster_bp.route('/shop/confirm_receipt', methods=['POST'])
@login_required
def shop_confirm_receipt():
    username = session['user']
    data = request.get_json()
    oid = data.get('order_id')
    orders = wdm.get_orders(username=username)
    order = next((o for o in orders if o['id'] == oid), None)
    if not order:
        return jsonify({'success': False, 'message': '订单不存在'})
    if order['status'] != 'shipped':
        return jsonify({'success': False, 'message': '商品尚未发货'})
    wdm.update_order_status(oid, 'done')
    return jsonify({'success': True, 'message': '确认收货成功，交易完成！'})


# 管理员：商品管理
@wordmaster_bp.route('/admin/shop_products', methods=['GET'])
@admin_required
def admin_shop_products():
    products = wdm.get_products(active_only=False)
    orders = wdm.get_orders()
    return jsonify({'products': products, 'orders': orders})


@wordmaster_bp.route('/admin/shop_add_product', methods=['POST'])
@admin_required
def admin_shop_add_product():
    data = request.get_json()
    name = data.get('name', '').strip()
    desc = data.get('desc', '').strip()
    price = int(data.get('price', 1))
    if not name:
        return jsonify({'success': False, 'message': '商品名称不能为空'})
    pid = wdm.add_product(name, desc, price)
    return jsonify({'success': True, 'product_id': pid})


@wordmaster_bp.route('/admin/shop_toggle_product', methods=['POST'])
@admin_required
def admin_shop_toggle_product():
    data = request.get_json()
    pid = data.get('product_id')
    active = bool(data.get('active', True))
    wdm.toggle_product(pid, active)
    return jsonify({'success': True})


@wordmaster_bp.route('/admin/shop_delete_product', methods=['POST'])
@admin_required
def admin_shop_delete_product():
    data = request.get_json()
    pid = data.get('product_id')
    wdm.delete_product(pid)
    return jsonify({'success': True})


@wordmaster_bp.route('/admin/shop_ship', methods=['POST'])
@admin_required
def admin_shop_ship():
    data = request.get_json()
    oid = data.get('order_id')
    wdm.update_order_status(oid, 'shipped')
    return jsonify({'success': True, 'message': '已标记发货'})


# ============================================================
# 许愿池
# ============================================================
@wordmaster_bp.route('/wishes')
@login_required
def wishes():
    username = session['user']
    is_admin = wdm.is_admin(username)
    wish_list = wdm.get_wishes(requester=username, is_admin=is_admin,
                              status_filter=['open', 'approved', 'fulfilled'])
    archived = wdm.get_wishes(requester=username, is_admin=is_admin,
                             status_filter=['archived', 'rejected'])
    balance = wdm.get_coins_balance(username)
    return render_template('wishes.html',
                           wishes=wish_list,
                           archived=archived,
                           balance=balance,
                           is_admin=is_admin)


@wordmaster_bp.route('/wishes/create', methods=['POST'])
@login_required
def wishes_create():
    username = session['user']
    data = request.get_json()
    title = data.get('title', '').strip()
    desc = data.get('desc', '').strip()
    coins = int(data.get('coins', 1))
    is_public = bool(data.get('is_public', True))

    if not title:
        return jsonify({'success': False, 'message': '愿望标题不能为空'})
    if coins < 1:
        return jsonify({'success': False, 'message': '金币数量至少为 1'})

    balance = wdm.get_coins_balance(username)
    if balance < coins:
        return jsonify({'success': False, 'message': f'金币不足，当前余额 {balance} 个'})

    new_bal = wdm.add_coins(username, -coins, f'许愿：{title}')
    if new_bal is False:
        return jsonify({'success': False, 'message': '金币不足'})

    wid = wdm.create_wish(username, title, desc, coins, is_public)
    return jsonify({'success': True, 'wish_id': wid,
                    'message': f'愿望已发出！当前余额 {new_bal} 金币',
                    'new_balance': new_bal})


@wordmaster_bp.route('/wishes/pledge', methods=['POST'])
@login_required
def wishes_pledge():
    username = session['user']
    data = request.get_json()
    wid = data.get('wish_id')
    coins = int(data.get('coins', 1))

    wish = wdm.get_wish_by_id(wid)
    if not wish:
        return jsonify({'success': False, 'message': '愿望不存在'})
    if wish['status'] not in ('open', 'approved'):
        return jsonify({'success': False, 'message': '该愿望已结束，无法助力'})

    balance = wdm.get_coins_balance(username)
    if balance < coins:
        return jsonify({'success': False, 'message': f'金币不足，当前余额 {balance} 个'})

    new_bal = wdm.add_coins(username, -coins, f'助力愿望：{wish["title"]}')
    if new_bal is False:
        return jsonify({'success': False, 'message': '金币不足'})

    wdm.pledge_wish(wid, username, coins)
    return jsonify({'success': True,
                    'message': f'助力成功！当前余额 {new_bal} 金币',
                    'new_balance': new_bal})


@wordmaster_bp.route('/wishes/fulfill', methods=['POST'])
@login_required
def wishes_fulfill():
    """用户标记自己的愿望已实现，归档"""
    username = session['user']
    data = request.get_json()
    wid = data.get('wish_id')
    wish = wdm.get_wish_by_id(wid)
    if not wish:
        return jsonify({'success': False, 'message': '愿望不存在'})
    if wish['user'] != username:
        return jsonify({'success': False, 'message': '只能操作自己的愿望'})
    if wish['status'] not in ('open', 'approved'):
        return jsonify({'success': False, 'message': '该愿望状态不可归档'})
    wdm.update_wish_status(wid, 'archived')
    return jsonify({'success': True, 'message': '愿望已归档，恭喜你实现了愿望！'})


# 管理员：审批许愿池
@wordmaster_bp.route('/admin/wish_approve', methods=['POST'])
@admin_required
def admin_wish_approve():
    data = request.get_json()
    wid = data.get('wish_id')
    wdm.update_wish_status(wid, 'approved')
    return jsonify({'success': True, 'message': '愿望已点亮'})


@wordmaster_bp.route('/admin/wish_reject', methods=['POST'])
@admin_required
def admin_wish_reject():
    data = request.get_json()
    wid = data.get('wish_id')
    reason = data.get('reason', '').strip()
    wish = wdm.get_wish_by_id(wid)
    if not wish:
        return jsonify({'success': False, 'message': '愿望不存在'})
    wdm.refund_wish_coins(wid)
    wdm.update_wish_status(wid, 'rejected', reason)
    return jsonify({'success': True, 'message': '已驳回并退还金币'})


@wordmaster_bp.route('/admin/wish_fulfill', methods=['POST'])
@admin_required
def admin_wish_fulfill():
    """管理员线下完成后标记已实现并归档"""
    data = request.get_json()
    wid = data.get('wish_id')
    wish = wdm.get_wish_by_id(wid)
    if not wish:
        return jsonify({'success': False, 'message': '愿望不存在'})
    wdm.update_wish_status(wid, 'archived')
    return jsonify({'success': True, 'message': '已将愿望标记为实现并归档'})


@wordmaster_bp.route('/admin/wishes_data', methods=['GET'])
@admin_required
def admin_wishes_data():
    """管理员获取全部愿望（含私密）"""
    all_wishes = wdm.get_wishes(is_admin=True,
                               status_filter=['open', 'approved', 'fulfilled', 'rejected', 'archived'])
    return jsonify({'wishes': all_wishes})


# 免错券使用接口
@wordmaster_bp.route('/coins/use_ticket', methods=['POST'])
@login_required
def use_ticket():
    username = session['user']
    # 检查免错券是否已下架
    if not wdm.is_ticket_active():
        return jsonify({'success': False, 'message': '免错券已被管理员下架，暂时无法使用'})
    success = wdm.use_ticket(username)
    if success:
        remaining = wdm.get_ticket_count(username)
        return jsonify({'success': True, 'remaining': remaining,
                        'message': '免错券已使用，本题错误不计入成绩！'})
    return jsonify({'success': False, 'message': '没有可用的免错券'})


@wordmaster_bp.route('/study/ticket_override', methods=['POST'])
@login_required
def study_ticket_override():
    """免错券：将当前答错的题目计为正确，并推进"""
    ctx = session.get('study_context')
    if not ctx or ctx['mode'] != 'study':
        return jsonify({'error': '无进行中的学习'}), 400
    # 把错误计数修正：正确数+1，推进index
    ctx['correct_count'] = ctx.get('correct_count', 0) + 1
    ctx['current_index'] += 1
    # 从 wrong_words 中移除（如果有）
    word = ctx['words'][ctx['current_index'] - 1] if ctx['current_index'] > 0 else None
    if word:
        ctx.setdefault('wrong_words', [])
        if word['word'] in ctx['wrong_words']:
            ctx['wrong_words'].remove(word['word'])
    session['study_context'] = ctx
    return jsonify({'success': True})


@wordmaster_bp.route('/review/ticket_override', methods=['POST'])
@login_required
def review_ticket_override():
    """免错券：将当前答错的题目计为正确，并推进"""
    ctx = session.get('review_context')
    if not ctx or ctx['mode'] != 'review':
        return jsonify({'error': '无进行中的复习'}), 400
    ctx['correct_count'] = ctx.get('correct_count', 0) + 1
    ctx['current_index'] += 1
    word = ctx['words'][ctx['current_index'] - 1] if ctx['current_index'] > 0 else None
    if word:
        ctx.setdefault('wrong_words', [])
        if word['word'] in ctx['wrong_words']:
            ctx['wrong_words'].remove(word['word'])
    session['review_context'] = ctx
    return jsonify({'success': True})
