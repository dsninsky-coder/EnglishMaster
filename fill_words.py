#!/usr/bin/env python3
"""
英语大师 — 单词音标/释义批量生成工具
======================================
独立脚本，直接操作 SQLite 数据库，无需 Flask 应用上下文。

功能：
  - 音标: eng-to-ipa 离线优先，AI (DeepSeek) 兜底
  - 释义: AI 直译（最常用意思，不依赖短文语境）
  - 跳过已有音标+释义的单词
  - 默认只处理实词（过滤虚词），--all 提取全文单词

用法:
  python fill_words.py                           # 默认: 实词，生成音标+释义
  python fill_words.py --all                     # 全文所有单词（含虚词）
  python fill_words.py --phonetic-only           # 只生成音标
  python fill_words.py --meaning-only            # 只生成释义
  python fill_words.py --delay 1.0               # API 间隔 1 秒（避免限流）
  python fill_words.py --db /path/to/english.db  # 指定数据库路径
  python fill_words.py --dry-run                 # 预览模式，不实际写入
"""

import sqlite3
import json
import re
import time
import os
import sys
import argparse
import traceback
import requests

# ─── 配置 ───
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"

# 英语虚词 / 功能词（默认过滤，--all 不过滤）
STOPWORDS = set("""
a an the is are was were be been being am
to of in on at for and or but not no so if
then than only also just now here there very
too up out down all some any each every both
few more most other such own same still while
when where how what which who whom whose one
two three many much after before between
through during because until without within
about into over under
he she it they we you i me my your his her
their our this that with as by from
do does did have has had will would can could
should may might
""".split())

# ─── 工具函数 ───

def get_ai_proxy(db_path):
    """从数据库读取 AI 代理配置（兼容 Flask SystemSetting 表）。"""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    try:
        c.execute("SELECT value FROM system_settings WHERE key='ai_proxy'")
        row = c.fetchone()
    except sqlite3.OperationalError:
        row = None
    conn.close()
    if row and row[0]:
        try:
            cfg = json.loads(row[0])
            return {
                'base_url': (cfg.get('base_url') or '').strip() or DEFAULT_BASE_URL,
                'model': (cfg.get('model') or '').strip() or DEFAULT_MODEL,
                'api_key': (cfg.get('api_key') or '').strip(),
            }
        except json.JSONDecodeError:
            pass
    return {'base_url': DEFAULT_BASE_URL, 'model': DEFAULT_MODEL, 'api_key': ''}


def is_content_word(word):
    """判断是否实词（非虚词 + 长度 > 1）。"""
    w = word.lower().strip("'\"")
    return w not in STOPWORDS and len(w) > 1


def call_ai(api_key, prompt, base_url=None, model=None, temperature=0.0, max_retries=3):
    """调用 DeepSeek Chat API，带重试逻辑。"""
    base = (base_url or DEFAULT_BASE_URL).rstrip("/")
    url = base + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    last_error = None
    for attempt in range(max_retries):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=30)
            if r.status_code == 429:
                wait = min(2 ** (attempt + 1), 12)
                last_error = f"429 限流, 等待{wait}s..."
                time.sleep(wait)
                continue
            if r.status_code == 401:
                return None, "401 认证失败 (API Key 无效)"
            if r.status_code >= 500:
                wait = min(2 ** attempt, 6)
                last_error = f"{r.status_code} 服务端错误, 等待{wait}s..."
                time.sleep(wait)
                continue
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return content, None
        except requests.exceptions.Timeout:
            last_error = "超时"
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
        except Exception as e:
            last_error = str(e)[:80]
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
    return None, last_error


# ─── 生成函数 ───

def generate_phonetic(word, proxy):
    """生成 IPA 音标: eng-to-ipa 离线优先, AI 兜底。
    返回 (phonetic_or_None, source_or_error_str)"""
    # 1) 离线 eng-to-ipa
    try:
        from eng_to_ipa import convert
        result = convert(word, keep_punct=False, retrieve_all=False)
        if result and result != word and '/' not in result:
            # eng-to-ipa 返回的不一定是 /.../ 格式，统一加斜杠
            return f"/{result}/", "eng-to-ipa"
    except Exception:
        pass

    # 2) AI 兜底
    api_key = proxy.get('api_key', '')
    if not api_key:
        return None, "无 API Key"
    prompt = (
        f'Provide ONLY the IPA phonetic transcription for the English word "{word}" '
        f'in American English. Output ONLY the IPA symbols between slashes, nothing else. '
        f'Example: /həˈloʊ/'
    )
    content, err = call_ai(api_key, prompt,
                           base_url=proxy['base_url'], model=proxy['model'],
                           temperature=0.1)
    if err:
        return None, err
    if content:
        match = re.search(r'/([^/]+)/', content.strip())
        if match:
            return f"/{match.group(1)}/", "ai"
        # 有时 AI 不返回斜杠
        stripped = content.strip().strip('/')
        if stripped and len(stripped) > 1:
            return f"/{stripped}/", "ai"
    return None, "AI 无输出"


def generate_meaning(word, proxy):
    """生成中文释义: AI 直译（最常用意思，不依赖短文语境）。
    返回 (meaning_or_None, source_or_error_str)"""
    api_key = proxy.get('api_key', '')
    if not api_key:
        return None, "无 API Key"
    prompt = (
        f'请给出英文单词 "{word}" 的最常用、最直接的中文释义。\n\n'
        f'规则:\n'
        f'1. 只输出中文（2-8 个字）\n'
        f'2. 不要括号、不要拼音、不要英文、不要解释\n'
        f'3. 输出该单词最基础、最常见的字面意思\n'
        f'4. 不要受任何文章上下文影响\n'
        f'5. 如果是名词就输出名词义，动词就输出动词义（选最常见的词性）'
    )
    content, err = call_ai(api_key, prompt,
                           base_url=proxy['base_url'], model=proxy['model'],
                           temperature=0.2)
    if err:
        return None, err
    if content:
        meaning = content.strip().strip('"\'。，, \n\r\t• ·')
        if meaning and len(meaning) <= 20:
            return meaning, "ai"
        # 如果太长，截取前 15 个字符尝试
        if meaning:
            short = re.split(r'[，,;；\s]', meaning)[0][:15]
            if short:
                return short, "ai"
    return None, "AI 无输出"


# ─── 数据库操作 ───

def get_words_to_process(db_path, content_only=True):
    """获取所有缺少音标或释义的单词。
    返回列表: [(word_id, course_id, word, meaning, phonetic, course_title), ...]"""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        SELECT cw.id, cw.course_id, cw.word, cw.meaning, cw.phonetic, c.title
        FROM course_words cw
        JOIN courses c ON cw.course_id = c.id
        WHERE (cw.meaning IS NULL OR cw.meaning = ''
               OR cw.phonetic IS NULL OR cw.phonetic = '')
        ORDER BY cw.course_id, cw.id
    """)
    all_words = [(row[0], row[1], row[2], row[3] or '', row[4] or '', row[5])
                 for row in c.fetchall()]
    conn.close()

    if content_only:
        all_words = [w for w in all_words if is_content_word(w[2])]

    return all_words


def update_word(db_path, word_id, meaning=None, phonetic=None):
    """更新单个单词的 meaning 和/或 phonetic。"""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    updates = []
    params = []
    if meaning is not None:
        updates.append("meaning = ?")
        params.append(meaning)
    if phonetic is not None:
        updates.append("phonetic = ?")
        params.append(phonetic)
    if updates:
        params.append(word_id)
        c.execute(f"UPDATE course_words SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    conn.close()


def count_stats(db_path, content_only=True):
    """统计数据库单词状态。"""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    total = c.execute("SELECT COUNT(*) FROM course_words").fetchone()[0]
    with_both = c.execute(
        "SELECT COUNT(*) FROM course_words WHERE meaning IS NOT NULL AND meaning != '' "
        "AND phonetic IS NOT NULL AND phonetic != ''"
    ).fetchone()[0]
    with_meaning = c.execute(
        "SELECT COUNT(*) FROM course_words WHERE meaning IS NOT NULL AND meaning != ''"
    ).fetchone()[0]
    with_phonetic = c.execute(
        "SELECT COUNT(*) FROM course_words WHERE phonetic IS NOT NULL AND phonetic != ''"
    ).fetchone()[0]
    conn.close()
    return total, with_both, with_meaning, with_phonetic


# ─── 主流程 ───

def main():
    parser = argparse.ArgumentParser(
        description='英语大师 — 单词音标/释义批量生成工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python fill_words.py                           # 默认: 实词，生成音标+释义
  python fill_words.py --all                     # 全文所有单词（含虚词）
  python fill_words.py --phonetic-only           # 只生成音标
  python fill_words.py --meaning-only            # 只生成释义
  python fill_words.py --delay 1.0               # API 调用间隔 1 秒
  python fill_words.py --db /opt/english/backend/instance/english.db
  python fill_words.py --dry-run                 # 预览，不写入
        """
    )
    parser.add_argument('--db', default=None, help='数据库路径')
    parser.add_argument('--all', dest='all_words', action='store_true',
                        help='处理全文所有单词（默认过滤虚词，仅实词）')
    parser.add_argument('--phonetic-only', action='store_true', help='只生成音标')
    parser.add_argument('--meaning-only', action='store_true', help='只生成释义')
    parser.add_argument('--delay', type=float, default=0.5,
                        help='API 调用间隔（秒，默认 0.5）')
    parser.add_argument('--dry-run', action='store_true',
                        help='预览模式，不实际写入数据库')
    args = parser.parse_args()

    # ── 定位数据库 ──
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if args.db:
        db_path = args.db
    else:
        candidates = [
            os.path.join(script_dir, 'backend', 'instance', 'english.db'),
            os.path.join(script_dir, 'instance', 'english.db'),
            'instance/english.db',
            'backend/instance/english.db',
        ]
        db_path = next((c for c in candidates if os.path.exists(c)), None)
        if not db_path:
            print("❌ 找不到数据库文件！请用 --db 指定路径")
            print(f"   已检查: {', '.join(candidates)}")
            sys.exit(1)

    if not os.path.exists(db_path):
        print(f"❌ 数据库不存在: {db_path}")
        sys.exit(1)

    # ── 打印头部 ──
    print()
    print("=" * 60)
    print("  英语大师 — 单词音标/释义批量生成工具")
    print("=" * 60)
    print(f"  数据库: {db_path}")
    print(f"  模式:   {'全文所有单词' if args.all_words else '仅实词（已过滤虚词）'}")
    do_phonetic = not args.meaning_only
    do_meaning = not args.phonetic_only
    tasks = []
    if do_phonetic: tasks.append("音标")
    if do_meaning: tasks.append("释义")
    print(f"  任务:   生成 {' + '.join(tasks)}")
    print(f"  API间隔: {args.delay}s")
    if args.dry_run:
        print(f"  ⚠️  DRY RUN 模式 — 不写入数据库")

    # ── 读取 AI 配置 ──
    print()
    print("🔑 读取 AI 配置...")
    proxy = get_ai_proxy(db_path)
    print(f"   Base URL: {proxy['base_url']}")
    print(f"   Model:    {proxy['model']}")
    if proxy['api_key']:
        k = proxy['api_key']
        print(f"   API Key:  {k[:6]}{'*' * max(0, len(k)-10)}{k[-4:]}")
    else:
        print(f"   ⚠️  API Key 未配置！释义生成将全部失败")

    # ── 检查 eng-to-ipa ──
    if do_phonetic:
        try:
            from eng_to_ipa import convert
            test = convert("hello", keep_punct=False, retrieve_all=False)
            if test:
                print(f"   eng-to-ipa: ✓ 可用（离线音标优先）")
            else:
                print(f"   eng-to-ipa: ⚠ 已安装但返回空，将走 AI 兜底")
        except ImportError:
            print(f"   eng-to-ipa: ✗ 未安装，音标完全依赖 AI")
            print(f"       安装: pip install eng-to-ipa==0.0.2")

    # ── 当前状态 ──
    total_all, with_both, with_meaning, with_phonetic = count_stats(db_path, not args.all_words)
    print()
    print(f"📊 数据库概览:")
    print(f"   总单词数:     {total_all}")
    print(f"   已有音标:     {with_phonetic} ({with_phonetic*100//max(total_all,1)}%)")
    print(f"   已有释义:     {with_meaning} ({with_meaning*100//max(total_all,1)}%)")
    print(f"   完整(音+义):  {with_both} ({with_both*100//max(total_all,1)}%)")
    missing = total_all - with_both
    print(f"   待处理:       {missing}")

    # ── 获取待处理单词 ──
    words = get_words_to_process(db_path, content_only=not args.all_words)

    if not words:
        print(f"\n✅ 所有单词都已有音标和释义，无需处理！")
        return

    # 按课程分组
    courses = {}
    for wid, cid, word, meaning, phonetic, title in words:
        courses.setdefault(cid, {'title': title, 'words': []})
        courses[cid]['words'].append((wid, word, meaning, phonetic))

    total_words = len(words)
    print(f"\n🔍 待处理: {len(courses)} 个课程, {total_words} 个单词")
    print()
    for cid in sorted(courses):
        info = courses[cid]
        ws = info['words']
        need_p = sum(1 for _, _, _, p in ws if not p)
        need_m = sum(1 for _, _, m, _ in ws if not m)
        print(f"   课程 [{cid}] {info['title']}: {len(ws) if do_phonetic and do_meaning else ''}")
        print(f"     → 缺音标: {need_p}, 缺释义: {need_m}")

    if not args.dry_run:
        print(f"\n  即将开始... (按 Ctrl+C 可安全中断)")
        time.sleep(1.5)

    # ── 开始处理 ──
    print()
    print("=" * 60)
    print("  开始处理")
    print("=" * 60)

    total_ok = 0
    total_fail = 0
    p_ok, p_fail = 0, 0
    m_ok, m_fail = 0, 0
    processed = 0
    last_api_time = 0

    for cid in sorted(courses):
        info = courses[cid]
        print(f"\n📖 [{cid}] {info['title']} — {len(info['words'])} 个单词")

        for wid, word, existing_meaning, existing_phonetic in info['words']:
            need_p = do_phonetic and not existing_phonetic
            need_m = do_meaning and not existing_meaning
            if not need_p and not need_m:
                continue

            processed += 1

            # 速率控制
            now = time.time()
            if last_api_time > 0:
                elapsed = now - last_api_time
                if elapsed < args.delay:
                    time.sleep(args.delay - elapsed)
            last_api_time = time.time()

            # 显示进度
            miss = []
            if need_p: miss.append("音标")
            if need_m: miss.append("释义")
            print(f"  [{processed}/{total_words}] {word:20s} ← {'+'.join(miss):8s}", end="", flush=True)

            new_phonetic = None
            new_meaning = None
            results = []

            # 生成音标
            if need_p:
                val, src = generate_phonetic(word, proxy)
                if val:
                    new_phonetic = val
                    p_ok += 1
                    results.append(f"音标={val}")
                else:
                    p_fail += 1
                    results.append(f"音标✗({src})")

            # 生成释义
            if need_m:
                val, src = generate_meaning(word, proxy)
                if val:
                    new_meaning = val
                    m_ok += 1
                    results.append(f"释义={val}")
                else:
                    m_fail += 1
                    results.append(f"释义✗({src})")

            # 写入数据库
            if not args.dry_run and (new_phonetic or new_meaning):
                try:
                    update_word(db_path, wid,
                                meaning=new_meaning if need_m else None,
                                phonetic=new_phonetic if need_p else None)
                except Exception as e:
                    print(f"\n     ⚠️ 数据库写入失败: {e}", file=sys.stderr)

            if new_phonetic or new_meaning:
                total_ok += 1
            else:
                total_fail += 1

            print(f"  → {' | '.join(results)}")

    # ── 汇总 ──
    print()
    print("=" * 60)
    print("  处理完成!")
    print("=" * 60)
    print(f"  处理单词数: {processed}")
    if do_phonetic:
        print(f"  音标: ✓ {p_ok}   ✗ {p_fail}")
    if do_meaning:
        print(f"  释义: ✓ {m_ok}   ✗ {m_fail}")
    print(f"  成功写入: {total_ok}")
    print(f"  失败/跳过: {total_fail}")

    # 再次统计
    _, with_both2, with_m2, with_p2 = count_stats(db_path, not args.all_words)
    print(f"\n📊 更新后:")
    print(f"   已有音标: {with_p2}  (+{with_p2 - with_phonetic})")
    print(f"   已有释义: {with_m2}  (+{with_m2 - with_meaning})")
    print(f"   完整:     {with_both2}  (+{with_both2 - with_both})")
    print(f"   仍缺:     {total_all - with_both2}")

    if total_fail > 0:
        print(f"\n💡 有 {total_fail} 次失败，可能原因:")
        print(f"   - DeepSeek API 限流 (429) / 超时 / 额度用尽")
        print(f"   - 网络不通")
        print(f"   - 直接重新运行本脚本即可，已成功的会自动跳过")
    print()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n⚠️ 用户中断，已完成的处理已保存到数据库。可重新运行继续。")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 致命错误: {e}")
        traceback.print_exc()
        sys.exit(1)
