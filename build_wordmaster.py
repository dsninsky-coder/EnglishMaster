"""一次性脚本：将 words/app.py 转换为 backend/wordmaster.py（Blueprint 形式）。

变换规则：
- 移除 Flask app 创建 / DataManager，改用 WordDataManager（wdm）+ Blueprint('wordmaster')。
- 所有 @app.route -> @wordmaster_bp.route；@app.context_processor -> @wordmaster_bp.context_processor。
- dm. -> wdm.
- 移除 index 路由（与 SPA '/' 冲突）；移除 __main__ 块。
- 修正 shop_buy 内置券判定。
- url_for('X') -> url_for('wordmaster.X')（跳过 'static'）。
"""
import re

SRC = r"F:/项目开发/英语大师/words/app.py"
OUT = r"F:/项目开发/英语大师/backend/wordmaster.py"

with open(SRC, "r", encoding="utf-8") as f:
    src = f.read()

src = src.replace("from data_manager import DataManager",
                  "from word_data import WordDataManager")
src = src.replace(
    "app = Flask(__name__)\n"
    "app.secret_key = 'wordmaster-secret-key-2026'\n"
    "dm = DataManager()\n",
    "wdm = WordDataManager()\n"
    "wordmaster_bp = Blueprint('wordmaster', __name__)\n")
src = src.replace("return dict(dm=dm, version=VERSION)",
                  "return dict(dm=wdm, version=VERSION)")
src = src.replace("dm.", "wdm.")
src = src.replace("if product.get('type') == 'builtin' and pid == 'no_wrong_ticket':",
                  "if product.get('type') == 'builtin':")
src = src.replace("@app.context_processor", "@wordmaster_bp.context_processor")
src = src.replace("@app.route", "@wordmaster_bp.route")

# 移除 index 路由（与 SPA 的 '/' 冲突）
idx_start = src.index("@wordmaster_bp.route('/')\ndef index():")
idx_end_marker = "# ---------- 登录/注册 ----------"
idx_end = src.index(idx_end_marker)
src = src[:idx_start] + src[idx_end:]

# 移除 __main__ 块
main_idx = src.rfind("if __name__ == '__main__':")
if main_idx != -1:
    src = src[:main_idx].rstrip() + "\n"

# url_for('X') -> url_for('wordmaster.X')（跳过 static / wordmaster）
def _urlfor_repl(m):
    name = m.group(1)
    if name in ('static', 'wordmaster'):
        return m.group(0)
    return f"url_for('wordmaster.{name}'"
src = re.sub(r"url_for\('([a-zA-Z_][\w]*)'", _urlfor_repl, src)

with open(OUT, "w", encoding="utf-8") as f:
    f.write(src)

print("written:", OUT, "bytes:", len(src))
