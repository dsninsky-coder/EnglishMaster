#!/usr/bin/env python3
"""
英语大师 — 单词音标/释义生成 一键诊断脚本
在服务器上运行: python diagnose.py
（在 backend/ 目录下运行，或指定路径: python diagnose.py /path/to/backend）
"""

import sys
import os
import traceback
import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)

# ─── 颜色输出 ───
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
BOLD = '\033[1m'
RESET = '\033[0m'

passed = 0
failed = 0
warnings = 0

def ok(msg):
    global passed
    passed += 1
    print(f"  {GREEN}[PASS]{RESET} {msg}")

def fail(msg):
    global failed
    failed += 1
    print(f"  {RED}[FAIL]{RESET} {msg}")

def warn(msg):
    global warnings
    warnings += 1
    print(f"  {YELLOW}[WARN]{RESET} {msg}")

def header(title):
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN} {title}{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}")


# ─── 定位 backend 目录 ───
script_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = None

# 优先检查命令行参数
if len(sys.argv) > 1:
    candidate = os.path.abspath(sys.argv[1])
    if os.path.exists(os.path.join(candidate, 'app.py')):
        backend_dir = candidate

# 依次检查：脚本所在目录、脚本所在目录的 backend/ 子目录、脚本父目录的 backend/
if not backend_dir:
    for candidate in [script_dir, os.path.join(script_dir, 'backend'),
                      os.path.join(os.path.dirname(script_dir), 'backend')]:
        if os.path.exists(os.path.join(candidate, 'app.py')):
            backend_dir = candidate
            break

if not backend_dir:
    # 最后兜底：当前工作目录
    if os.path.exists(os.path.join(os.getcwd(), 'app.py')):
        backend_dir = os.getcwd()
    else:
        print(f"\n{RED}错误: 找不到 app.py{RESET}")
        print(f"用法: python diagnose.py [backend目录路径]")
        print(f"示例: python diagnose.py /opt/english-master/backend")
        sys.exit(1)

print(f"\n{BOLD}英语大师 单词生成诊断工具{RESET}")
print(f"后端目录: {backend_dir}")
print(f"Python: {sys.executable} ({sys.version})")

os.chdir(backend_dir)
sys.path.insert(0, backend_dir)


# ═══════════════════════════════════════════
# 1. Python 版本检查
# ═══════════════════════════════════════════
header("1. Python 版本检查")
pv = sys.version_info
if pv >= (3, 8):
    ok(f"Python {pv.major}.{pv.minor}.{pv.micro} — 版本满足要求 (>=3.8)")
else:
    fail(f"Python {pv.major}.{pv.minor}.{pv.micro} — 版本过低，需要 3.8+")


# ═══════════════════════════════════════════
# 2. 依赖包检查
# ═══════════════════════════════════════════
header("2. 依赖包检查")

required_packages = {
    'flask': 'Flask',
    'flask_cors': 'Flask-Cors',
    'flask_jwt_extended': 'Flask-JWT-Extended',
    'flask_sqlalchemy': 'Flask-SQLAlchemy',
    'sqlalchemy': 'SQLAlchemy',
    'requests': 'requests',
    'eng_to_ipa': 'eng-to-ipa',
}

for module, pkg_name in required_packages.items():
    try:
        mod = __import__(module)
        ver = getattr(mod, '__version__', '未知')
        ok(f"{pkg_name} ({module}) — 已安装, 版本: {ver}")
    except ImportError:
        fail(f"{pkg_name} ({module}) — {RED}未安装!{RESET}")
        if module == 'eng_to_ipa':
            print(f"         {YELLOW}修复方法:{RESET} pip install eng-to-ipa==0.0.2")
        else:
            print(f"         {YELLOW}修复方法:{RESET} pip install -r requirements.txt")


# ═══════════════════════════════════════════
# 3. eng-to-ipa 功能测试
# ═══════════════════════════════════════════
header("3. eng-to-ipa 离线音标功能测试")
try:
    from eng_to_ipa import convert
    test_words = ['hello', 'world', 'beautiful', 'technology']
    all_ok = True
    for w in test_words:
        result = convert(w, keep_punct=False, retrieve_all=False)
        if result and result != w:
            print(f"  {w} → {GREEN}{result}{RESET}")
        else:
            print(f"  {w} → {RED}无结果{RESET}")
            all_ok = False
    if all_ok:
        ok("eng-to-ipa 离线音标生成正常")
    else:
        warn("eng-to-ipa 部分单词无法生成音标（不影响整体，AI 会兜底）")
except ImportError:
    fail("eng-to-ipa 未安装，离线音标不可用（将完全依赖 AI）")
except Exception as e:
    fail(f"eng-to-ipa 运行异常: {e}")
    traceback.print_exc()


# ═══════════════════════════════════════════
# 4. Flask 应用 & 数据库加载
# ═══════════════════════════════════════════
header("4. Flask 应用 & 数据库加载")
try:
    from app import app, db
    ok("Flask 应用导入成功 (app.py)")
except Exception as e:
    fail(f"Flask 应用导入失败: {e}")
    traceback.print_exc()
    print(f"\n{RED}无法继续诊断，请先修复 app.py 导入错误。{RESET}")
    sys.exit(1)

# 检查数据库文件
db_path = os.path.join(backend_dir, 'instance', 'english.db')
if os.path.exists(db_path):
    size = os.path.getsize(db_path)
    ok(f"数据库文件存在: instance/english.db ({size:,} bytes)")
else:
    fail(f"数据库文件不存在: {db_path}")
    warn("请先运行 python init_db.py 创建数据库")


# ═══════════════════════════════════════════
# 5. 数据库表 & 数据检查
# ═══════════════════════════════════════════
header("5. 数据库表 & 数据检查")
try:
    with app.app_context():
        from models import Course, Sentence, CourseWord, SystemSetting

        # 课程数
        courses = Course.query.all()
        ok(f"课程数: {len(courses)}")
        if courses:
            for c in courses[:3]:
                print(f"       - id={c.id}, title={c.title}")

        # 句子数
        sent_count = Sentence.query.count()
        ok(f"句子总数: {sent_count}")

        # 单词数
        word_count = CourseWord.query.count()
        ok(f"单词总数: {word_count}")

        # 检查 course_words 表是否有 meaning/phonetic 列
        from sqlalchemy import inspect as sa_inspect
        inspector = sa_inspect(db.engine)
        if 'course_words' in inspector.get_table_names():
            cols = [c['name'] for c in inspector.get_columns('course_words')]
            if 'meaning' in cols and 'phonetic' in cols:
                ok(f"course_words 表有 meaning/phonetic 列: {cols}")
            else:
                fail(f"course_words 表缺少 meaning/phonetic 列! 当前列: {cols}")
                warn("修复方法: python init_db.py --no-backup (运行迁移)")
        else:
            fail("course_words 表不存在")

        # 检查已有音标/释义的单词数
        if word_count > 0:
            with_meaning = CourseWord.query.filter(
                CourseWord.meaning.isnot(None),
                CourseWord.meaning != ''
            ).count()
            with_phonetic = CourseWord.query.filter(
                CourseWord.phonetic.isnot(None),
                CourseWord.phonetic != ''
            ).count()
            ok(f"已有释义的单词: {with_meaning}/{word_count}")
            ok(f"已有音标的单词: {with_phonetic}/{word_count}")
            if with_meaning == 0 and with_phonetic == 0:
                warn("所有单词都没有音标/释义 — 这可能是首次生成")
            elif with_meaning < word_count * 0.5:
                warn(f"仅 {with_meaning}/{word_count} 个单词有释义，生成可能不完整")

except Exception as e:
    fail(f"数据库检查失败: {e}")
    traceback.print_exc()


# ═══════════════════════════════════════════
# 6. AI 代理配置检查（最关键！）
# ═══════════════════════════════════════════
header("6. AI 代理配置检查 (DeepSeek API Key)")
try:
    with app.app_context():
        from app import get_ai_proxy
        proxy = get_ai_proxy()
        base_url = proxy.get('base_url', '')
        model = proxy.get('model', '')
        api_key = proxy.get('api_key', '')

        print(f"  Base URL: {base_url}")
        print(f"  Model:    {model}")
        print(f"  API Key:  {'*' * 8 + api_key[-8:] if api_key else '(空!)'}")

        if not api_key:
            fail("{BOLD}API Key 未配置!{RESET} 这是生成释义失败的最可能原因")
            warn("修复方法: 登录管理后台 → AI设置 → 填写 DeepSeek API Key")
            warn("  或在数据库 system_settings 表中设置 ai_proxy 的 api_key 字段")
        else:
            ok("API Key 已配置")
            if 'deepseek' in base_url.lower():
                ok("Base URL 指向 DeepSeek")
            else:
                warn(f"Base URL 不是 DeepSeek 官方地址: {base_url}")

except Exception as e:
    fail(f"AI 代理配置检查失败: {e}")
    traceback.print_exc()


# ═══════════════════════════════════════════
# 7. DeepSeek API 连通性测试
# ═══════════════════════════════════════════
header("7. DeepSeek API 连通性测试")
try:
    with app.app_context():
        from app import get_ai_proxy
        import deepseek_client as ds
        proxy = get_ai_proxy()
        api_key = proxy.get('api_key', '')

        if not api_key:
            fail("无法测试 API 连通性 — API Key 为空")
        else:
            print(f"  正在向 {proxy['base_url']} 发送测试请求...")
            try:
                # 直接用 raise_on_error=True 获取真实错误
                result = ds._chat(
                    api_key,
                    [{"role": "user", "content": "请回复 OK"}],
                    base_url=proxy['base_url'],
                    model=proxy['model'],
                    temperature=0.0,
                    response_format={},
                    raise_on_error=True,
                )
                if result:
                    ok(f"API 响应成功: {result[:50]}")
                else:
                    fail("API 返回空内容（但无异常抛出）")
            except Exception as api_err:
                fail(f"API 调用失败: {api_err}")
                err_str = str(api_err).lower()
                if '401' in err_str or 'unauthorized' in err_str:
                    warn("→ 401 认证失败: API Key 无效或过期")
                elif '429' in err_str or 'rate' in err_str:
                    warn("→ 429 限流: 请求过于频繁或额度用尽")
                elif 'timeout' in err_str or 'timed out' in err_str:
                    warn("→ 超时: 服务器网络到 DeepSeek API 不通，检查防火墙/代理")
                elif 'connection' in err_str or 'resolve' in err_str:
                    warn("→ 连接失败: 服务器无法访问 DeepSeek API，检查网络/DNS")
                else:
                    warn(f"→ 其他错误，详情: {api_err}")

except Exception as e:
    fail(f"连通性测试异常: {e}")
    traceback.print_exc()


# ═══════════════════════════════════════════
# 8. 模拟生成音标 & 释义（真实调用）
# ═══════════════════════════════════════════
header("8. 模拟生成音标 & 释义 (真实调用)")
try:
    with app.app_context():
        from app import _generate_phonetic, _generate_meaning

        test_word = "beautiful"
        test_context = "The beautiful sunset painted the sky with vibrant colors."

        # 测试音标生成
        print(f"\n  测试单词: {BOLD}{test_word}{RESET}")
        phonetic, p_source = _generate_phonetic(test_word)
        if phonetic:
            ok(f"音标生成成功: {phonetic} (来源: {p_source})")
        else:
            fail(f"音标生成失败 — eng-to-ipa 和 AI 均未返回结果")
            if p_source:
                print(f"         来源标记: {p_source}")

        # 测试释义生成
        meaning, m_source = _generate_meaning(test_word, test_context)
        if meaning:
            ok(f"释义生成成功: {meaning} (来源: {m_source})")
        else:
            fail("释义生成失败 — AI 未返回结果")
            warn("→ 请检查上方第6、7步的 API Key 和连通性")

        # 如果有课程数据，测试真实单词
        from models import Course, Sentence, CourseWord
        courses = Course.query.all()
        if courses:
            course = courses[0]
            sents = Sentence.query.filter_by(course_id=course.id).all()
            if sents:
                ctx = ' '.join(s.english for s in sents[:5])[:500]
                words = CourseWord.query.filter_by(course_id=course.id).all()
                if words:
                    test_w = words[0].word if words[0].word else "hello"
                    print(f"\n  用课程数据测试: 课程='{course.title}', 单词='{test_w}'")
                    p, ps = _generate_phonetic(test_w)
                    m, ms = _generate_meaning(test_w, ctx)
                    if p:
                        ok(f"课程单词音标: {p} ({ps})")
                    else:
                        fail(f"课程单词音标生成失败")
                    if m:
                        ok(f"课程单词释义: {m} ({ms})")
                    else:
                        fail(f"课程单词释义生成失败")

except Exception as e:
    fail(f"模拟生成测试异常: {e}")
    traceback.print_exc()


# ═══════════════════════════════════════════
# 9. requirements.txt 对比
# ═══════════════════════════════════════════
header("9. requirements.txt 检查")
req_path = os.path.join(backend_dir, 'requirements.txt')
if os.path.exists(req_path):
    with open(req_path, 'r', encoding='utf-8') as f:
        req_content = f.read()
    print(f"  requirements.txt 内容:")
    for line in req_content.strip().split('\n'):
        print(f"    {line}")

    if 'eng-to-ipa' in req_content:
        ok("requirements.txt 包含 eng-to-ipa")
    else:
        fail("requirements.txt 缺少 eng-to-ipa! 服务器需要: pip install eng-to-ipa==0.0.2")
else:
    fail(f"requirements.txt 不存在: {req_path}")


# ═══════════════════════════════════════════
# 10. pip freeze 对比
# ═══════════════════════════════════════════
header("10. 已安装包列表 (pip freeze)")
try:
    import subprocess
    result = subprocess.run(
        [sys.executable, '-m', 'pip', 'freeze'],
        capture_output=True, text=True, timeout=15
    )
    installed = result.stdout.strip().split('\n')
    key_packages = ['Flask', 'SQLAlchemy', 'requests']
    for pkg in key_packages:
        found = [p for p in installed if pkg.lower() in p.lower()]
        if found:
            ok(f"已安装: {found[0]}")
        else:
            fail(f"未安装: {pkg}")
    # eng-to-ipa 在 pip freeze 中可能显示为 eng_to_ipa（下划线）
    eng_found = [p for p in installed if 'eng' in p.lower() and 'ipa' in p.lower()]
    if eng_found:
        ok(f"已安装: {eng_found[0]}")
    else:
        fail("未安装: eng-to-ipa")
    print(f"\n  完整已安装列表:")
    for p in installed:
        if p:
            print(f"    {p}")
except Exception as e:
    warn(f"无法获取 pip freeze: {e}")


# ═══════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════
header("诊断汇总")
total = passed + failed + warnings
print(f"\n  {GREEN}通过: {passed}{RESET}  {RED}失败: {failed}{RESET}  {YELLOW}警告: {warnings}{RESET}  总计: {total}")

if failed == 0:
    print(f"\n  {GREEN}{BOLD}✓ 所有关键检查通过!{RESET}")
    print(f"  如果生成仍然失败，请检查:")
    print(f"    - 浏览器 F12 控制台是否有 JS 错误")
    print(f"    - Flask 日志是否有异常输出")
    print(f"    - 服务器到 DeepSeek API 的网络连通性")
else:
    print(f"\n  {RED}{BOLD}✗ 发现 {failed} 个问题!{RESET}")
    print(f"\n  {BOLD}常见修复步骤:{RESET}")
    print(f"  1. {YELLOW}pip install -r requirements.txt{RESET}  — 安装所有依赖")
    print(f"  2. {YELLOW}python init_db.py --no-backup{RESET}     — 运行数据库迁移")
    print(f"  3. 登录管理后台 → AI设置 → 确认 DeepSeek API Key 已填写")
    print(f"  4. 确认服务器能访问 https://api.deepseek.com")

print(f"\n{'='*60}\n")
