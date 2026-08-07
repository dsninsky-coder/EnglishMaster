"""DeepSeek API 客户端 + 语义判分辅助。

全局约定：AI 服务仅使用 DeepSeek (https://api.deepseek.com/v1)。
DeepSeek 只有 chat 接口、无 embeddings，因此"语义向量余弦相似度"退化为：
让模型对两段文本打一个 0~1 的语义相似度分数（JSON 返回）。
若未配置 API Key，则回退到本地 difflib 字符重叠相似度，保证系统离线可跑。
"""
import json
import re
import difflib
import requests

# 默认值保持 DeepSeek，但 base_url / model 均可由管理员在后台配置，
# 这样任何兼容 OpenAI Chat Completions 接口的模型（DeepSeek / OpenAI / 通义 / 本地 vLLM 等）都能接入。
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


def _chat(key, messages, base_url=None, model=None, temperature=0.0,
          raise_on_error=False, response_format=None):
    """通用 Chat 接口。response_format 默认 {"type": "json_object"}，传入 None 则不强制 JSON。"""
    if not key:
        return None
    base = (base_url or DEFAULT_BASE_URL).rstrip("/")
    url = base + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    fmt = {"type": "json_object"} if response_format is None else response_format
    if fmt:
        payload["response_format"] = fmt
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=40)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        # raise_on_error=True 时把真实异常（超时/429 限流/401/断网等）向上抛，
        # 让调用方能记录具体失败原因；默认仍吞掉返回 None，兼容旧调用方。
        if raise_on_error:
            raise
        return None


def call(key, prompt, base_url=None, model=None, temperature=0.0):
    """便捷接口：单个 prompt，纯文本输出（不强制 JSON）。key 为 None 时返回 None。"""
    if not key:
        return None
    messages = [{"role": "user", "content": prompt}]
    return _chat(key, messages, base_url=base_url, model=model,
                 temperature=temperature, response_format={})


def local_similarity(a, b):
    """本地字符级相似度（无 API Key 时回退）。"""
    if not a or not b:
        return 0.0
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 3)


def score_similarity(key, user_text, reference_text, base_url=None, model=None):
    """返回 0~1 的语义相似度。"""
    if not key:
        return local_similarity(user_text, reference_text)
    messages = [
        {"role": "system", "content": (
            "你是严谨的语言评分助手。请判断【用户输入】与【标准译文】在语义上的相似程度，"
            "忽略拼写/语法细节，只看意思是否一致。只输出 JSON：{\"score\": 0.0~1.0 的浮点数}。"
        )},
        {"role": "user", "content": json.dumps(
            {"user": user_text, "reference": reference_text}, ensure_ascii=False)},
    ]
    content = _chat(key, messages, base_url=base_url, model=model)
    if not content:
        return local_similarity(user_text, reference_text)
    try:
        data = json.loads(content)
        s = float(data.get("score", 0))
        return max(0.0, min(1.0, s))
    except Exception:
        return local_similarity(user_text, reference_text)


def analyze_error(key, user_input, correct_answer, step, base_url=None, model=None):
    """生成简短错因（中文，≤30 字）。"""
    if not key:
        return "与标准答案有差异，请对照学习。"
    step_name = {2: "英译中", 3: "音译中", 4: "中译英", 5: "延展叙述"}.get(step, "练习")
    messages = [
        {"role": "system", "content": (
            "你是耐心的英语老师。请对比学生的作答和标准答案，用一句简短中文（不超过30字）"
            "点出主要问题，语气温和、不批评。只输出 JSON：{\"reason\": \"...\"}。"
        )},
        {"role": "user", "content": json.dumps(
            {"step": step_name, "student": user_input, "standard": correct_answer},
            ensure_ascii=False)},
    ]
    content = _chat(key, messages, base_url=base_url, model=model)
    if not content:
        return "与标准答案有差异，请对照学习。"
    try:
        return json.loads(content).get("reason", "与标准答案有差异，请对照学习。")[:60]
    except Exception:
        return "与标准答案有差异，请对照学习。"


_PRONOUN_POOL = {
    "he": "he", "him": "he", "his": "he", "himself": "he", "she": "she",
    "her": "she", "hers": "she", "herself": "she", "they": "they", "them": "they",
    "their": "they", "themselves": "they", "i": "i", "me": "i", "my": "i",
    "we": "we", "us": "we", "our": "we", "you": "you", "your": "you",
    "it": "it", "its": "it",
}

# 常见动词时态变体（粗略）：映射到原形
_VERB_FORMS = {
    "went": "go", "gone": "go", "goes": "go", "going": "go",
    "did": "do", "done": "do", "does": "do", "doing": "do",
    "saw": "see", "seen": "see", "seeing": "see", "sees": "see",
    "ate": "eat", "eaten": "eat", "eating": "eat", "eats": "eat",
    "got": "get", "getting": "get", "gets": "get",
    "made": "make", "making": "make", "makes": "make",
    "came": "come", "coming": "come", "comes": "come",
    "took": "take", "taken": "take", "taking": "take", "takes": "take",
    "had": "have", "has": "have", "having": "have",
    "was": "be", "were": "be", "been": "be", "is": "be", "are": "be", "am": "be", "being": "be",
    "said": "say", "says": "say", "saying": "say",
    "found": "find", "finding": "find", "finds": "find",
    "bought": "buy", "buys": "buy", "buying": "buy",
    "thought": "think", "thinks": "think", "thinking": "think",
    "liked": "like", "likes": "like", "liking": "like",
    "wanted": "want", "wants": "want", "wanting": "want",
    "needed": "need", "needs": "need", "needing": "need",
}


def _norm_token(tok):
    return re.sub(r"[^a-z']", "", tok.lower())


def _match_word(target, tokens):
    """检测目标词是否出现在 tokens 中（容忍时态/代词变体）。"""
    t = _norm_token(target)
    if not t:
        return True
    for tok in tokens:
        n = _norm_token(tok)
        if not n:
            continue
        if n == t:
            return True
        # 代词归一
        if t in _PRONOUN_POOL and n in _PRONOUN_POOL and _PRONOUN_POOL[t] == _PRONOUN_POOL[n]:
            return True
        # 动词时态归一
        if t in _VERB_FORMS and n in _VERB_FORMS and _VERB_FORMS[t] == _VERB_FORMS[n]:
            return True
        if t in _VERB_FORMS and _VERB_FORMS[t] == n:
            return True
        # 词干包含（避免漏判）
        if t in n or n in t:
            return True
    return False


def check_svo(user_text, svo):
    """Step4 中译英：svo 主干三命中即通过（虚词/时态/冠词忽略）。"""
    if not svo:
        return True
    tokens = re.findall(r"[A-Za-z']+", user_text or "")
    results = []
    for idx, comp in enumerate(svo[:3]):
        results.append(_match_word(comp, tokens))
    # 主干出现几个算几个；要求全部命中（svo 至多3个）
    needed = [r for r in results]
    return all(needed) if needed else True


def check_step5(user_text, prev_sentence_text, core_words, key, base_url=None, model=None):
    """Step5 延展叙述：逻辑连贯 + 至少命中1个核心词 + 长度<=20词。"""
    if not user_text or not user_text.strip():
        return False, 0.0
    words = re.findall(r"[A-Za-z']+", user_text)
    if len(words) > 20:
        return False, 0.0
    # 逻辑连贯：user_text 与 上文句子 的语义相似度 >= 0.5
    sim = score_similarity(key, user_text, prev_sentence_text or "", base_url=base_url, model=model)
    coherence = sim >= 0.5
    # 核心词召回
    hit = 0
    for w in (core_words or []):
        if _match_word(w, words):
            hit += 1
    recall_ok = hit >= 1
    passed = coherence and recall_ok
    return passed, round(sim, 3)


_STOPWORDS = set(
    "a an the is are was were be been being am are to of in on at for and or but "
    "he she it they we you i my your his her their our this that with as by from "
    "do does did have has had will would can could should may might into about over under"
    .split()
)


def _content_words(english):
    """从英文句中抽取实词（去停用词），作为本地对比的兜底核心词。"""
    toks = re.findall(r"[A-Za-z']+", english or "")
    return [t for t in toks if t.lower() not in _STOPWORDS and len(t) > 1]


def local_english_match(user_text, reference_english, target_words=None):
    """本地英文单词对比（Step4/Step5 优先使用）。

    返回 (passed, matched_count, total)：
    - 若提供了 target_words，要求全部命中才算本地通过；
    - 否则用标准句的实词兜底，命中≥60%即本地通过；
    - 无核心词时直接通过（避免卡死）。
    """
    tokens = re.findall(r"[A-Za-z']+", user_text or "")
    if target_words and len(target_words):
        core = [str(w) for w in target_words]
        matched = sum(1 for w in core if _match_word(w, tokens))
        passed = matched == len(core)
        return (passed, matched, len(core))
    core = _content_words(reference_english)
    if not core:
        return (True, 0, 0)
    matched = sum(1 for w in core if _match_word(w, tokens))
    passed = matched >= max(1, int(0.6 * len(core)))
    return (passed, matched, len(core))


def ai_score_english(key, user_text, standard_text, task='en', base_url=None, model=None):
    """用 AI 对英文作答按「完整度+准确度」打 0~1 分。无 key 返回 None。

    task='en'  : 中译英（与标准英文比对）
    task='cont': 延展叙述（续写是否连贯、完整、准确）
    """
    if not key:
        return None
    sys = {
        'en': ("你是英语评分助手。请评估学生英文作答与标准英文在【完整度】和【准确度】上的表现，"
               "忽略时态/冠词/介词/拼写细节，只看核心意思是否到位。只输出 JSON：{\"score\": 0.0~1.0 的浮点数}。"),
        'cont': ("你是英语写作评分助手。给定【上文句】和学生的【续写句】，请评估续写是否在逻辑上连贯、"
                 "且核心意思完整准确。只输出 JSON：{\"score\": 0.0~1.0 的浮点数}。"),
    }.get(task, "你是英语评分助手。只输出 JSON：{\"score\": 0.0~1.0}。")
    messages = [
        {"role": "system", "content": sys},
        {"role": "user", "content": json.dumps(
            {"standard": standard_text, "student": user_text}, ensure_ascii=False)},
    ]
    content = _chat(key, messages, base_url=base_url, model=model)
    if not content:
        return None
    try:
        return max(0.0, min(1.0, float(json.loads(content).get("score", 0))))
    except Exception:
        return None


def ai_score_chinese(key, user_text, standard_text, base_url=None, model=None):
    """用 AI 对中文翻译按「完整度+准确度」打 0~1 分。无 key 返回 None。"""
    if not key:
        return None
    messages = [
        {"role": "system", "content": (
            "你是中文翻译评分助手。请评估学生中文翻译与标准中文在【完整度】和【准确度】上的表现，"
            "忽略措辞差异，只看意思是否到位。只输出 JSON：{\"score\": 0.0~1.0 的浮点数}。"
        )},
        {"role": "user", "content": json.dumps(
            {"standard": standard_text, "student": user_text}, ensure_ascii=False)},
    ]
    content = _chat(key, messages, base_url=base_url, model=model)
    if not content:
        return None
    try:
        return max(0.0, min(1.0, float(json.loads(content).get("score", 0))))
    except Exception:
        return None


def generate_meta(key, english, chinese, base_url=None, model=None):
    if not key:
        return [], []
    messages = [
        {"role": "system", "content": (
            "你是英语教学助手。给定一句英文及中文翻译，请输出 JSON："
            "{\"keywords\": [中文关键概念词, 最多5个], "
            "\"svo\": [主语(英文), 谓语(英文原形), 宾语/表语(英文)]}。"
            "svo 缺失项用空字符串占位。"
        )},
        {"role": "user", "content": json.dumps(
            {"english": english, "chinese": chinese}, ensure_ascii=False)},
    ]
    content = _chat(key, messages, base_url=base_url, model=model)
    if not content:
        return [], []
    try:
        data = json.loads(content)
        kw = [str(x) for x in data.get("keywords", []) if x][:5]
        svo = [str(x) for x in data.get("svo", []) if x][:3]
        return kw, svo
    except Exception:
        return [], []
