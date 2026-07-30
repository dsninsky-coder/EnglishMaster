"""单词大师数据管理器（数据库版）。

与原 words/data_manager.py 的 DataManager 保持一致的方法签名，
底层由 JSON 文件改为 SQLAlchemy：
- 账号 / 金币：复用听说大师共享表（User / CoinTransaction）。
- 单词库 / 用户学习历史 / 考试配置：单词大师独立表
  （WordList / Word / WordUserState / WordExamConfig）。
- 全局 / 超管配置：存于 SystemSetting（wm_config / wm_admin_config）。
- DeepSeek API：复用共享来源（User.private_api_key / AdminShareKey）。

所有方法均需在 Flask app_context 内调用。
"""
import csv
import io
import re
import random
from datetime import datetime, timedelta

from models import (
    db, User, AdminShareKey, CoinTransaction,
    WordList, Word, WordUserState, WordExamConfig, SystemSetting,
    ShopItem, PurchaseOrder, Wish, WishSupport,
)

DEFAULT_HISTORY = {
    "learned_lists": [],
    "word_reviews": {},
    "quiz_results": [],
    "daily_stats": {},
    "user_prefs": {},
    "list_cooldowns": {},
    "review_once_cleared_date": "",
    "exam_attempts": {},
    "no_wrong_tickets": 0,
    "checkin": {},
}


class WordDataManager:
    # ---------------- 用户（复用共享 User 表） ----------------
    def _user(self, username):
        return User.query.filter_by(username=username).first()

    def verify_user(self, username, password):
        u = self._user(username)
        return bool(u and u.check_password(password))

    def is_admin(self, username):
        u = self._user(username)
        return bool(u and u.role == 'admin')

    def register_user(self, username, password):
        if not username or len(username) < 2:
            return False, "用户名至少2个字符"
        if not password or len(password) < 4:
            return False, "密码至少4个字符"
        if self._user(username):
            return False, "用户名已存在"
        u = User(username=username, role='student')
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        return True, "注册成功"

    def change_password(self, username, old_password, new_password):
        u = self._user(username)
        if not u or not u.check_password(old_password):
            return False, "原密码错误"
        if len(new_password) < 4:
            return False, "新密码至少4个字符"
        u.set_password(new_password)
        db.session.commit()
        return True, "密码修改成功"

    def admin_reset_password(self, target_username, new_password):
        u = self._user(target_username)
        if not u:
            return False, f"用户 {target_username} 不存在"
        if len(new_password) < 4:
            return False, "新密码至少4个字符"
        u.set_password(new_password)
        db.session.commit()
        return True, f"已成功重置用户 {target_username} 的密码"

    def get_all_usernames(self):
        return [u.username for u in User.query.filter_by(role='student').all()]

    def get_all_usernames_with_admin(self):
        return [u.username for u in User.query.all()]

    # ---------------- 配置（SystemSetting） ----------------
    def load_config(self):
        cfg = SystemSetting.get('wm_config', {}) or {}
        cfg.setdefault('review_count', 20)
        cfg.setdefault('review_mode', 'none')
        cfg.setdefault('require_both_modes', False)
        return cfg

    def save_config(self, config):
        SystemSetting.set('wm_config', config)

    def load_admin_config(self):
        cfg = SystemSetting.get('wm_admin_config', {}) or {}
        # 统一全局 AI 代理：管理员在「系统工具 → API分享」里设置的模型，
        # 若单词大师未单独配置 shared_base_url/shared_ai_model，则回退到全局 ai_proxy。
        g = SystemSetting.get('ai_proxy', {}) or {}
        cfg['shared_base_url'] = cfg.get('shared_base_url') or g.get('base_url') or 'https://api.deepseek.com/v1'
        cfg['shared_ai_model'] = cfg.get('shared_ai_model') or g.get('ai_model') or 'deepseek-chat'
        cfg.setdefault('retry_cooldown_seconds', 60)
        cfg.setdefault('tts_in_en2zh', False)
        cfg.setdefault('audio2zh_enabled', True)
        cfg.setdefault('judge_mode', 'local_then_ai')
        cfg.setdefault('controlled_users', {})
        return cfg

    def save_admin_config(self, cfg):
        SystemSetting.set('wm_admin_config', cfg)

    def _resolve_shared_api_key(self, username):
        """DeepSeek API 复用共享来源：分享 Key > 私有 Key > None。"""
        u = self._user(username)
        if not u:
            return None
        if u.shared_api_key_id:
            sk = AdminShareKey.query.get(u.shared_api_key_id)
            if sk and sk.is_active:
                return sk.api_key_value
        if u.private_api_key:
            return u.private_api_key
        return None

    def get_effective_config(self, username):
        """返回 (config_dict, controlled_fields_set)。"""
        base = self.load_config()
        history = self.load_user_history(username)
        merged = dict(base)
        for k, v in history.get("user_prefs", {}).items():
            merged[k] = v

        admin_cfg = self.load_admin_config()
        controlled = admin_cfg.get("controlled_users", {}).get(username, {})
        controlled_fields = set(controlled.keys())
        for k, v in controlled.items():
            merged[k] = v

        # AI 判分：统一从共享来源取 API Key + 超管的 base_url/model
        shared_key = self._resolve_shared_api_key(username)
        if shared_key:
            merged['chatgpt_api_key'] = shared_key
            merged['chatgpt_base_url'] = admin_cfg.get('shared_base_url', 'https://api.deepseek.com/v1')
            merged['ai_model'] = admin_cfg.get('shared_ai_model', 'deepseek-chat')
        else:
            merged.setdefault('chatgpt_api_key', '')
            merged.setdefault('chatgpt_base_url', admin_cfg.get('shared_base_url', 'https://api.deepseek.com/v1'))
            merged.setdefault('ai_model', admin_cfg.get('shared_ai_model', 'deepseek-chat'))
        # judge_mode 统一由超管配置控制
        merged['judge_mode'] = admin_cfg.get('judge_mode', 'local_then_ai')
        return merged, controlled_fields

    def save_user_prefs(self, username, prefs):
        history = self.load_user_history(username)
        history["user_prefs"] = prefs
        self.save_user_history(username, history)

    # ---------------- 单词库（WordList / Word） ----------------
    def _sorted_lists(self):
        return WordList.query.order_by(WordList.order_index, WordList.id).all()

    def load_words(self):
        result = {}
        for wl in self._sorted_lists():
            items = Word.query.filter_by(list_id=wl.id).order_by(Word.order_index, Word.id).all()
            result[wl.name] = [{"word": w.word, "meaning": w.meaning} for w in items]
        return result

    def _get_or_create_list(self, name):
        wl = WordList.query.filter_by(name=name).first()
        if not wl:
            max_order = db.session.query(db.func.max(WordList.order_index)).scalar() or 0
            wl = WordList(name=name, order_index=max_order + 1)
            db.session.add(wl)
            db.session.flush()
        return wl

    def save_words(self, words):
        """整体替换单词库（words: {list_name: [{word, meaning}]}）。"""
        Word.query.delete()
        WordList.query.delete()
        db.session.flush()
        for order, (name, items) in enumerate(words.items()):
            wl = WordList(name=name, order_index=order + 1)
            db.session.add(wl)
            db.session.flush()
            for i, it in enumerate(items):
                db.session.add(Word(list_id=wl.id, word=it["word"],
                                    meaning=it["meaning"], order_index=i + 1))
        db.session.commit()

    def import_words_from_data(self, rows, overwrite=False):
        imported = 0
        skipped = 0
        list_names = set()
        for row in rows:
            list_name = (row.get("list") or "").strip()
            word = (row.get("word") or "").strip()
            meaning = (row.get("meaning") or "").strip()
            if not list_name or not word or not meaning:
                skipped += 1
                continue
            wl = self._get_or_create_list(list_name)
            existing = Word.query.filter(
                Word.list_id == wl.id, db.func.lower(Word.word) == word.lower()).first()
            if existing:
                if overwrite:
                    existing.meaning = meaning
                    imported += 1
                else:
                    skipped += 1
                continue
            max_o = db.session.query(db.func.max(Word.order_index)).filter_by(list_id=wl.id).scalar() or 0
            db.session.add(Word(list_id=wl.id, word=word, meaning=meaning, order_index=max_o + 1))
            list_names.add(list_name)
            imported += 1
        db.session.commit()
        return imported, skipped, list(list_names)

    def parse_csv_content(self, content, encoding='utf-8'):
        rows = []
        try:
            text = content.decode(encoding) if isinstance(content, bytes) else content
        except UnicodeDecodeError:
            text = content.decode('gbk', errors='replace')
        reader = csv.DictReader(io.StringIO(text))
        headers = [h.strip().lower() for h in (reader.fieldnames or [])]
        field_map = {}
        for h in headers:
            if h in ['list', 'list_name', '列表', '分组']:
                field_map['list'] = h
            elif h in ['word', '单词', 'english', '英文', 'en']:
                field_map['word'] = h
            elif h in ['meaning', '释义', '中文', 'chinese', 'zh', '翻译']:
                field_map['meaning'] = h
        for row in reader:
            clean_row = {k.strip().lower(): v.strip() for k, v in row.items() if v}
            entry = {
                'list': clean_row.get(field_map.get('list', ''), '').strip(),
                'word': clean_row.get(field_map.get('word', ''), '').strip(),
                'meaning': clean_row.get(field_map.get('meaning', ''), '').strip(),
            }
            if entry['word'] and entry['meaning']:
                rows.append(entry)
        return rows

    def parse_txt_content(self, content, default_list='list1'):
        rows = []
        try:
            text = content.decode('utf-8') if isinstance(content, bytes) else content
        except UnicodeDecodeError:
            text = content.decode('gbk', errors='replace')
        current_list = default_list
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('[') and line.endswith(']'):
                current_list = line[1:-1].strip()
                continue
            for sep in ['=', '\t', ',']:
                if sep in line:
                    parts = line.split(sep, 1)
                    if len(parts) == 2:
                        word, meaning = parts[0].strip(), parts[1].strip()
                        if word and meaning:
                            rows.append({"list": current_list, "word": word, "meaning": meaning})
                        break
        return rows

    def get_list_names(self):
        return [wl.name for wl in self._sorted_lists()]

    def get_list_word_count(self):
        result = {}
        for wl in self._sorted_lists():
            result[wl.name] = Word.query.filter_by(list_id=wl.id).count()
        return result

    def get_sorted_list_names(self):
        names = [wl.name for wl in WordList.query.all()]

        def natural_key(s):
            return [int(t) if t.isdigit() else t.lower()
                    for t in re.split(r'(\d+)', str(s))]
        names.sort(key=natural_key)
        return names

    # ---------------- 用户历史（WordUserState.data JSON） ----------------
    def _state(self, username, create=False):
        u = self._user(username)
        if not u:
            return None
        st = WordUserState.query.get(u.id)
        if not st and create:
            st = WordUserState(user_id=u.id, data={})
            db.session.add(st)
            db.session.flush()
        return st

    def load_user_history(self, username):
        st = self._state(username)
        data = dict(st.data) if (st and st.data) else {}
        for k, v in DEFAULT_HISTORY.items():
            if k not in data:
                data[k] = v.copy() if isinstance(v, (dict, list)) else v
        return data

    def save_user_history(self, username, history):
        st = self._state(username, create=True)
        if st is None:
            return
        # SQLAlchemy JSON 需整体重新赋值才能触发脏检测
        st.data = dict(history)
        db.session.add(st)
        db.session.commit()

    def add_learned_list(self, username, list_name):
        history = self.load_user_history(username)
        if list_name not in history["learned_lists"]:
            history["learned_lists"].append(list_name)
        self.save_user_history(username, history)

    def is_list_learned(self, username, list_name):
        return list_name in self.load_user_history(username)["learned_lists"]

    # ---------------- "复习一次即可"模式 ----------------
    def set_review_once_cleared(self, username):
        history = self.load_user_history(username)
        history["review_once_cleared_date"] = datetime.now().strftime("%Y-%m-%d")
        self.save_user_history(username, history)

    def is_review_once_cleared(self, username):
        history = self.load_user_history(username)
        return history.get("review_once_cleared_date", "") == datetime.now().strftime("%Y-%m-%d")

    # ---------------- 答题冷却 ----------------
    def set_list_cooldown(self, username, list_name, seconds):
        history = self.load_user_history(username)
        cooldown_until = (datetime.now() + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
        history["list_cooldowns"][list_name] = cooldown_until
        self.save_user_history(username, history)

    def get_list_cooldown_remaining(self, username, list_name):
        history = self.load_user_history(username)
        cooldown_str = history.get("list_cooldowns", {}).get(list_name)
        if not cooldown_str:
            return 0
        try:
            cooldown_until = datetime.strptime(cooldown_str, "%Y-%m-%d %H:%M:%S")
            return max(0, int((cooldown_until - datetime.now()).total_seconds()))
        except Exception:
            return 0

    def clear_list_cooldown(self, username, list_name):
        history = self.load_user_history(username)
        history.get("list_cooldowns", {}).pop(list_name, None)
        self.save_user_history(username, history)

    # ---------------- 遗忘曲线 ----------------
    def update_word_review(self, username, list_name, word, correct):
        history = self.load_user_history(username)
        key = f"{list_name}:{word}"
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        if key not in history["word_reviews"]:
            history["word_reviews"][key] = {
                "last_review": today_str, "review_count": 0, "correct_count": 0,
                "ease_factor": 2.5, "interval": 1, "next_review": today_str,
            }
        rec = history["word_reviews"][key]
        rec["last_review"] = today_str
        rec["review_count"] += 1
        if correct:
            rec["correct_count"] += 1
            if rec["review_count"] == 1:
                rec["interval"] = 1
            elif rec["review_count"] == 2:
                rec["interval"] = 6
            else:
                rec["interval"] = max(1, int(rec["interval"] * rec["ease_factor"]))
            rec["interval"] = min(rec["interval"], 180)
            rec["next_review"] = (now + timedelta(days=rec["interval"])).strftime("%Y-%m-%d")
        else:
            rec["interval"] = 1
            rec["next_review"] = today_str
            rec["ease_factor"] = max(1.3, rec["ease_factor"] - 0.2)
        self.save_user_history(username, history)

    def get_review_words(self, username, limit=20):
        history = self.load_user_history(username)
        now = datetime.now().strftime("%Y-%m-%d")
        words_data = self.load_words()
        review_list = []
        for key, rec in history["word_reviews"].items():
            if rec.get("next_review", "9999-99-99") <= now:
                parts = key.split(":", 1)
                if len(parts) != 2:
                    continue
                list_name, word = parts
                if list_name in words_data:
                    for item in words_data[list_name]:
                        if item["word"] == word:
                            review_list.append({
                                "list": list_name, "word": word, "meaning": item["meaning"],
                                "next_review": rec.get("next_review", ""),
                                "interval": rec.get("interval", 1),
                            })
                            break
        review_list.sort(key=lambda x: x["next_review"])
        if len(review_list) > limit:
            review_list = random.sample(review_list, limit)
        return review_list

    def get_review_count(self, username):
        history = self.load_user_history(username)
        now = datetime.now().strftime("%Y-%m-%d")
        return sum(1 for rec in history["word_reviews"].values()
                   if rec.get("next_review", "9999-99-99") <= now)

    # ---------------- 成绩记录 ----------------
    def add_quiz_result(self, username, list_name, correct, total, mode="study", quiz_mode="en2zh"):
        history = self.load_user_history(username)
        now = datetime.now()
        record = {
            "time": now.strftime("%Y-%m-%d %H:%M"), "list": list_name,
            "correct": correct, "total": total, "score": f"{correct}/{total}",
            "percent": round(correct / total * 100, 1) if total > 0 else 0,
            "mode": mode, "quiz_mode": quiz_mode,
            "passed": (correct / total >= 0.8) if total > 0 else False,
        }
        history["quiz_results"].append(record)
        today = now.strftime("%Y-%m-%d")
        daily = history["daily_stats"].setdefault(today, {
            "studied_lists": [], "review_passed": False, "review_done": False, "quiz_count": 0})
        if mode == "study" and list_name not in daily["studied_lists"]:
            daily["studied_lists"].append(list_name)
        if mode == "review":
            daily["review_done"] = True
            if record["passed"]:
                daily["review_passed"] = True
        daily["quiz_count"] += 1
        self.save_user_history(username, history)

    def get_daily_status(self, username):
        history = self.load_user_history(username)
        result = {}
        for date_str, daily in history.get("daily_stats", {}).items():
            studied = len(daily.get("studied_lists", [])) > 0
            review_done = daily.get("review_done", False)
            review_passed = daily.get("review_passed", False)
            if studied:
                result[date_str] = "green" if (not review_done or review_passed) else "red"
            elif review_done:
                result[date_str] = "green" if review_passed else "red"
        return result

    # ---------------- 金币（复用共享 User.coin_balance） ----------------
    def get_coins_balance(self, username):
        u = self._user(username)
        return u.coin_balance or 0 if u else 0

    def add_coins(self, username, amount, reason):
        u = self._user(username)
        if not u:
            return False
        if (u.coin_balance or 0) + amount < 0:
            return False
        u.coin_balance = (u.coin_balance or 0) + amount
        db.session.add(CoinTransaction(user_id=u.id, amount=amount, reason=reason,
                                       category='word'))
        db.session.add(u)
        db.session.commit()
        return u.coin_balance

    # ---------------- 每日打卡（存于用户历史） ----------------
    def get_checkin_status(self, username):
        history = self.load_user_history(username)
        today = datetime.now().strftime("%Y-%m-%d")
        return set(history.get("checkin", {}).get(today, []))

    def mark_checkin(self, username, item):
        history = self.load_user_history(username)
        today = datetime.now().strftime("%Y-%m-%d")
        history.setdefault("checkin", {})
        history["checkin"].setdefault(today, [])
        if item not in history["checkin"][today]:
            history["checkin"][today].append(item)
        self.save_user_history(username, history)

    def try_grant_checkin(self, username, item, coins, reason):
        done = self.get_checkin_status(username)
        if item in done:
            return False, self.get_coins_balance(username)
        new_bal = self.add_coins(username, coins, reason)
        self.mark_checkin(username, item)
        return True, new_bal

    # ---------------- 免错券 ----------------
    def get_ticket_count(self, username):
        return self.load_user_history(username).get("no_wrong_tickets", 0)

    def add_tickets(self, username, n):
        history = self.load_user_history(username)
        history["no_wrong_tickets"] = history.get("no_wrong_tickets", 0) + n
        self.save_user_history(username, history)

    def is_ticket_active(self):
        """免错券（内置商品）是否上架：读共享 ShopItem 表。"""
        item = ShopItem.query.filter_by(product_type='builtin').first()
        return bool(item and item.is_on_shelf)

    def set_ticket_active(self, active):
        item = ShopItem.query.filter_by(product_type='builtin').first()
        if item:
            item.is_on_shelf = bool(active)
            db.session.commit()
        return True

    def use_ticket(self, username):
        if not self.is_ticket_active():
            return False
        history = self.load_user_history(username)
        cnt = history.get("no_wrong_tickets", 0)
        if cnt <= 0:
            return False
        history["no_wrong_tickets"] = cnt - 1
        self.save_user_history(username, history)
        return True

    # ---------------- 金币流水（共享 CoinTransaction） ----------------
    def get_coins_ledger(self, username, limit=50):
        u = self._user(username)
        if not u:
            return []
        rows = CoinTransaction.query.filter_by(user_id=u.id) \
            .order_by(CoinTransaction.created_at.asc()).all()
        # 还原每笔的累计余额，保持与原 JSON 流水一致的语义
        running = 0
        out = []
        for r in rows:
            running += (r.amount or 0)
            out.append({
                "time": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
                "amount": r.amount or 0,
                "reason": r.reason or "",
                "balance": running,
            })
        return list(reversed(out[-limit:]))

    # ---------------- 商城（共享 ShopItem / PurchaseOrder） ----------------
    def get_products(self, active_only=True):
        q = ShopItem.query
        if active_only:
            q = q.filter_by(is_on_shelf=True)
        items = q.order_by(ShopItem.id.asc()).all()
        return [{
            "id": p.id,
            "name": p.name,
            "desc": p.description or "",
            "price": p.price_coins,
            "type": p.product_type or "custom",
            "active": bool(p.is_on_shelf),
            "unlimited": p.stock == -1,
        } for p in items]

    def add_product(self, name, desc, price):
        p = ShopItem(name=name, description=desc, price_coins=price,
                     product_type='custom', is_on_shelf=True)
        db.session.add(p)
        db.session.commit()
        return p.id

    def toggle_product(self, pid, active):
        p = ShopItem.query.get(int(pid))
        if p:
            p.is_on_shelf = bool(active)
            db.session.commit()
            return True
        return False

    def delete_product(self, pid):
        p = ShopItem.query.get(int(pid))
        if p:
            db.session.delete(p)
            db.session.commit()
            return True
        return False

    def create_order(self, username, product_id, product_name, price):
        u = self._user(username)
        if not u:
            return None
        o = PurchaseOrder(student_id=u.id, item_id=int(product_id), status='pending')
        db.session.add(o)
        db.session.commit()
        return o.id

    def get_orders(self, username=None, status=None):
        q = PurchaseOrder.query
        if username:
            u = self._user(username)
            if not u:
                return []
            q = q.filter_by(student_id=u.id)
        if status:
            q = q.filter_by(status=status)
        orders = q.order_by(PurchaseOrder.created_at.desc()).all()
        result = []
        for o in orders:
            item = ShopItem.query.get(o.item_id)
            result.append({
                "id": o.id,
                "user": self._username(o.student_id),
                "product_name": item.name if item else "",
                "price": item.price_coins if item else 0,
                "status": o.status,
                "created_at": o.created_at.strftime("%Y-%m-%d %H:%M:%S") if o.created_at else "",
            })
        return result

    def update_order_status(self, oid, status):
        o = PurchaseOrder.query.get(int(oid))
        if not o:
            return False
        o.status = status
        from datetime import datetime as _dt
        if status == 'shipped':
            o.shipped_at = _dt.now()
        elif status == 'completed':
            o.completed_at = _dt.now()
        db.session.commit()
        return True

    def _username(self, user_id):
        u = User.query.get(user_id)
        return u.username if u else ""

    # ---------------- 许愿池（共享 Wish / WishSupport，source='word'） ----------------
    def get_wishes(self, requester=None, is_admin=False, status_filter=None):
        q = Wish.query.filter_by(source='word')
        wishes = q.order_by(Wish.created_at.desc()).all()
        result = []
        for w in wishes:
            if status_filter and w.status not in status_filter:
                continue
            is_public = bool(w.is_public)
            if not is_public and not is_admin and self._username(w.student_id) != requester:
                continue
            result.append(self._wish_to_dict(w))
        return result

    def get_wish_by_id(self, wid):
        w = Wish.query.get(int(wid))
        return self._wish_to_dict(w) if w else None

    def _wish_to_dict(self, w):
        pledges = w.pledges or []
        pledged = sum(p.get("coins", 0) for p in pledges)
        return {
            "id": w.id,
            "user": self._username(w.student_id),
            "title": w.title or w.content or "",
            "desc": w.desc or "",
            "coins": w.total_coins_invested or 0,
            "pledged_coins": pledged,
            "pledges": pledges,
            "is_public": bool(w.is_public),
            "status": w.status,
            "lit": bool(w.lit),
            "source": w.source,
            "created_at": w.created_at.strftime("%Y-%m-%d %H:%M:%S") if w.created_at else "",
            "reject_reason": w.admin_reply or "",
        }

    def create_wish(self, username, title, desc, coins, is_public):
        u = self._user(username)
        if not u:
            return None
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        w = Wish(
            student_id=u.id, content=title, total_coins_invested=coins,
            status='open', title=title, desc=desc, is_public=bool(is_public),
            lit=False, pledges=[{"user": username, "coins": coins, "time": now}],
            source='word',
        )
        db.session.add(w)
        db.session.commit()
        return w.id

    def pledge_wish(self, wid, username, coins):
        w = Wish.query.get(int(wid))
        if not w:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pledges = list(w.pledges or [])
        pledges.append({"user": username, "coins": coins, "time": now})
        w.pledges = pledges
        w.total_coins_invested = (w.total_coins_invested or 0) + coins
        db.session.commit()

    def update_wish_status(self, wid, status, reason=""):
        w = Wish.query.get(int(wid))
        if not w:
            return False
        w.status = status
        if status == 'approved':
            w.lit = True
        if reason:
            w.admin_reply = reason
        db.session.commit()
        return True

    def refund_wish_coins(self, wid):
        w = Wish.query.get(int(wid))
        if not w:
            return False
        for pledge in (w.pledges or []):
            self.add_coins(pledge["user"], pledge["coins"],
                           f"愿望被驳回退款：{w.title or w.content}")
        return True

    # ---------------- 考试配置（WordExamConfig.data JSON） ----------------
    def _exam_cfg(self, username, create=False):
        u = self._user(username)
        if not u:
            return None
        ec = WordExamConfig.query.get(u.id)
        if not ec and create:
            ec = WordExamConfig(user_id=u.id, data=[None] * 5)
            db.session.add(ec)
            db.session.flush()
        return ec

    def get_user_exams(self, username):
        ec = self._exam_cfg(username)
        exams = list(ec.data) if (ec and ec.data) else []
        result = []
        for ex in exams:
            if ex is None:
                result.append(None)
            else:
                if "quiz_modes" not in ex:
                    old_mode = ex.get("quiz_mode", "en2zh")
                    ex["quiz_modes"] = [old_mode] if old_mode else ["en2zh"]
                if not ex.get("quiz_modes"):
                    ex["quiz_modes"] = ["en2zh"]
                ex.setdefault("pass_rate", 80)
                ex.setdefault("daily_limit", 99)
                result.append(ex)
        while len(result) < 5:
            result.append(None)
        return result[:5]

    def save_user_exam(self, username, slot, ranges, capacity, quiz_modes,
                       pass_rate=80, daily_limit=99):
        ec = self._exam_cfg(username, create=True)
        data = list(ec.data) if ec.data else [None] * 5
        while len(data) < 5:
            data.append(None)
        slot_idx = slot - 1
        old = data[slot_idx]
        created_at = old.get("created_at", "") if old else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data[slot_idx] = {
            "slot": slot, "ranges": ranges, "capacity": capacity,
            "quiz_modes": quiz_modes, "pass_rate": pass_rate,
            "daily_limit": daily_limit, "created_at": created_at,
        }
        ec.data = data
        db.session.add(ec)
        db.session.commit()
        return True

    def delete_user_exam(self, username, slot):
        ec = self._exam_cfg(username)
        if not ec or not ec.data:
            return False
        data = list(ec.data)
        slot_idx = slot - 1
        if slot_idx < len(data):
            data[slot_idx] = None
            ec.data = data
            db.session.add(ec)
            db.session.commit()
            return True
        return False

    # ---------------- 考试次数记录（存于用户历史） ----------------
    def record_exam_attempt(self, username, slot):
        history = self.load_user_history(username)
        history.setdefault("exam_attempts", {})
        slot_key = str(slot)
        today = datetime.now().strftime("%Y-%m-%d")
        history["exam_attempts"].setdefault(slot_key, {})
        history["exam_attempts"][slot_key].setdefault(today, {"count": 0, "attempts": []})
        day_data = history["exam_attempts"][slot_key][today]
        day_data["count"] += 1
        day_data["attempts"].append({
            "started_at": datetime.now().strftime("%H:%M:%S"),
            "finished": False, "percent": 0, "passed": False})
        self.save_user_history(username, history)
        return day_data["count"]

    def finish_exam_attempt(self, username, slot, percent, passed):
        history = self.load_user_history(username)
        slot_key = str(slot)
        today = datetime.now().strftime("%Y-%m-%d")
        attempts_data = history.get("exam_attempts", {}).get(slot_key, {}).get(today)
        if attempts_data and attempts_data["attempts"]:
            last = attempts_data["attempts"][-1]
            last["finished"] = True
            last["percent"] = round(percent, 1)
            last["passed"] = passed
            self.save_user_history(username, history)
            return True
        return False

    def get_exam_attempt_info(self, username, slot):
        history = self.load_user_history(username)
        slot_key = str(slot)
        today = datetime.now().strftime("%Y-%m-%d")
        day_data = history.get("exam_attempts", {}).get(slot_key, {}).get(today)
        if day_data:
            return {"count": day_data["count"], "attempts": day_data["attempts"]}
        return {"count": 0, "attempts": []}

    def reset_exam_attempts(self, username, slot, new_count):
        history = self.load_user_history(username)
        slot_key = str(slot)
        today = datetime.now().strftime("%Y-%m-%d")
        history.setdefault("exam_attempts", {})
        history["exam_attempts"].setdefault(slot_key, {})
        history["exam_attempts"][slot_key].setdefault(today, {"count": 0, "attempts": []})
        day_data = history["exam_attempts"][slot_key][today]
        attempts = day_data["attempts"]
        if new_count < len(attempts):
            day_data["attempts"] = attempts[:new_count]
        elif new_count > len(attempts):
            for _ in range(new_count - len(attempts)):
                day_data["attempts"].append({
                    "started_at": "--:--:--", "finished": False,
                    "percent": 0, "passed": False, "admin_adjusted": True})
        day_data["count"] = new_count
        self.save_user_history(username, history)
        return True

    def generate_exam_words(self, ranges, capacity):
        words_data = self.load_words()
        sorted_names = self.get_sorted_list_names()
        pool = []
        for start, end in ranges:
            for i in range(start, end + 1):
                if 1 <= i <= len(sorted_names):
                    list_name = sorted_names[i - 1]
                    if list_name in words_data:
                        for w in words_data[list_name]:
                            pool.append({"word": w["word"], "meaning": w["meaning"], "list": list_name})
        random.shuffle(pool)
        if len(pool) > capacity:
            pool = pool[:capacity]
        return pool

    def format_ranges_display(self, ranges):
        parts = []
        for start, end in ranges:
            parts.append(str(start) if start == end else f"{start}~{end}")
        return ", ".join(parts)
