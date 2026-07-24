# 英语大师 · 锁定式闯关英语学习平台

基于「五步法」的英语学习系统：沉浸输入 → 英译中 → 音译中 → 中译英 → 延展叙述。
学生端为**锁定式闯关**界面，管理员端拥有课程、学员、商店、许愿、报表等完整后台。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | 原生 HTML/CSS/JavaScript（**无框架、无构建、无 Node.js**） |
| 后端 | Python Flask + Flask-JWT-Extended（标准 `templates/` + `static/` 布局） |
| ORM | SQLAlchemy 2.x |
| 数据库 | SQLite（单文件 `backend/instance/english.db`） |
| AI | 仅 DeepSeek (`https://api.deepseek.com/v1`) |

> 前端为纯静态资源，由 Flask 直接以 `/static/` 托管、`render_template` 渲染 `templates/index.html`，**无需 npm / 打包 / 构建**，改完 JS 刷新浏览器即生效。

## 目录结构

```
英语大师/
├── backend/
│   ├── app.py                # Flask 主程序 + 全部 API + 页面托管
│   ├── models.py             # 12 张表定义
│   ├── deepseek_client.py    # DeepSeek 判分 / 错因 / 关键词生成
│   ├── init_db.py            # 建表 + 默认管理员 + 可选灌 demo
│   ├── requirements.txt
│   ├── demo_course.json      # 示例课程（The Beautiful World）
│   ├── templates/
│   │   └── index.html        # SPA 外壳（加载 /static/js/*.js）
│   ├── static/
│   │   ├── css/styles.css
│   │   └── js/{common,student,admin}.js
│   ├── uploads/courses/<课程ID>/   # 课程音频（管理员上传）
│   └── instance/english.db   # 运行时生成
└── （无 frontend/ —— 前端已并入 backend/static 与 backend/templates）
```

## 快速开始

### 1. 后端（前后端一体，一条命令即可）

```bash
cd backend
pip install -r requirements.txt
python init_db.py --seed     # 建表 + 默认管理员 + 灌入 demo 课程
python app.py                # 监听 http://localhost:5000
```

默认超级管理员：**admin / admin123**

浏览器直接访问 **http://localhost:5000** 即可使用（页面与接口同源，无需跨域、无需构建）。

## 核心约定（务必了解）

- **TTS**：播放每句的音频文件。音频由**管理员在后台上传**到服务器（`uploads/courses/<课程ID>/<数字>.<mp3|wav>`），后端按「文件名数字 = sentence_id」自动生成链接并写入 `sentences.audio_url`。系统**不调用任何 TTS API**，也不使用浏览器语音合成。
- **STT**：不通过浏览器 API。所有需要「语音输入」的框均为普通文本输入框，并提示用户使用**手机/电脑系统自带的语音输入法（麦克风图标）**转文字。
- **AI 判分（本地优先）**：Step4 中译英 / Step5 延展叙述先在本地进行**英文单词对比**（命中 `target_words` 或标准句实词即判过）；Step2 英译中先用本地字符相似度。本地判断不通过时，再交给 DeepSeek 按「完整度 + 准确度」打 0~1 分（`deepseek_client.ai_score_english` / `ai_score_chinese`）。**无 API Key 时完全本地判分**，保证离线可跑。
- **API Key 优先级**：学生调用 AI 时，若 `shared_api_key_id` 非空则用管理员共享 Key；否则用学生私有 Key；都为空则提示设置 Key。

## 金币规则

| 行为 | 奖励 |
|---|---|
| 每日签到 | +1（**需先完成至少一个学习任务**，否则拦截；数值可在后台「设置」调整） |
| 连续签到奖励 | **由管理员在后台「设置」配置**：每日奖励 × min(连续天数, 封顶天数)，仅连续 ≥2 天且每日奖励>0 时发放 |
| Step 通关 | +20 |
| Step 首次完美（100%） | +30（并累计 `total_perfect_steps`） |
| 课程全 5 步通关 | +50 |
| 错题复习通过 | +5 |

## 主要 API（`/api/v1` 前缀）

- `POST /auth/register` · `POST /auth/login` · `GET /me` · `POST /user/apikey` · `POST /auth/change-password`（改密）· `GET /checkin/info`（签到配置/状态）
- `POST /checkin`
- `GET /courses` · `GET /courses/<id>/sentences`
- `POST /step/submit` · `POST /step/finish` · `GET /review/flashcards` · `POST /review/submit`
- `GET /shop/items` · `POST /shop/buy`
- `POST /wish/create` · `POST /wish/support` · `GET /wishes` · `GET /wishes/public`（许愿池）· `GET /wish/<id>`
- `GET /reports/student/<id>`
- 管理员：`POST /admin/share-key` · `GET /admin/share-keys` · `POST /admin/set-share` · `GET /admin/students` · `POST /admin/reset-password` · `POST /admin/adjust-coins` · `POST /admin/delete-student`（二次密码校验）· `GET/POST /admin/settings`（签到/金币配置）· `POST /admin/upload-course`（容错：支持 markdown 栅栏/JSON 数组/详细错误）· `POST /admin/upload-audio`（批量音频，按课程上传）· `POST /admin/publish-course` · `POST /admin/unpublish-course` · `DELETE /admin/course/<id>` · `POST /admin/update-course` · `GET /admin/courses`（管理列表：句数/音频数/缺音频/是否有错）· `GET /admin/course/<id>/errors`（检查缺失）· `POST /admin/assign-course` · `GET /admin/sentences/<id>` · `GET /admin/db-view?table=` · `POST /admin/shop-item` · `GET /admin/shop-items` · `GET /admin/orders` · `POST /admin/ship-order` · `POST /admin/archive-order`（存档完成）· `POST /admin/reject-order`（驳回退款）· `POST /admin/toggle-shelf`（上下架）· `POST /admin/wish/process`（approve/reject/complete）· `GET /admin/coin-transactions`（金币发放/扣除流水）
- 学生：`GET /coin/transactions`（银行流水式金币获取/支出记录，含管理员奖励原因）· `GET /shop/orders`（我的订单与状态）

## 数据库表

`users` · `admin_share_keys` · `courses` · `sentences` · `course_assignments` ·
`student_sentence_progress` · `wrong_answers` · `coin_transactions` · `shop_items` ·
`purchase_orders` · `wishes` · `wish_supports`

> `sentences` 表额外包含 `chinese_keywords`（Step2 判分用关键词），`course_assignments` 额外包含 `completed_steps` / `perfect_steps` / `completion_awarded` 用于精确追踪奖励、避免重复发币。

## 课程上传与课程管理列表

1. **上传 JSON**：在后台「课程管理」选 JSON 文件**或粘贴 JSON 文本**。后端容错解析：
   - 支持单篇 `{"title":...,"sentences":[...]}`；
   - 支持**含多篇的 JSON 数组**（一次创建多门课程）；
   - 支持 LLM 输出的 ```` ```json ... ``` ```` 栅栏文本（自动剥离前言/代码栅栏）；
   - 解析失败会**返回具体原因与字段提示**（如「第 3 句缺少 english 或 chinese」），并在界面展示，不再静默失败。
   - 页面提供「下载课程模板」按钮，一键获取标准格式。
   - JSON 结构（**无需 audio_url**）：
   ```json
   {
     "article_id": 50,
     "title": "The Beautiful World",
     "full_text": "Our world is full of wonderful places. ...",
     "sentences": [
       {"sentence_id": 1, "english": "Our world is full of wonderful places.",
        "chinese": "我们的世界充满了奇妙的地方。", "target_words": ["world", "wonderful"]}
     ]
   }
   ```
   提交后课程**立即进入下方「课程管理列表」**，句子 `audio_url` 暂空，并自动建文件夹 `uploads/courses/<课程ID>/`。
2. **在管理列表中逐课操作**（解析后即可看到，无需切页面）：
   - **发布 / 撤销**：控制课程对学生是否可见、可分配。
   - **分配**：勾选学生分配给其学习。
   - **补充音频**：点开本课程的音频上传框，选 `1.mp3`、`2.mp3`…（文件名数字 = `sentence_id`）多选上传，后端写入每句 `audio_url` 为 `/uploads/courses/<课程ID>/<文件名>`。**音频上传严格绑定当前课程，不会再串到别的课程**（已修复「只能传第一个课程」的 bug）。
   - **检查错误**：一键列出缺音频 / 缺字段（english/chinese）的句子序号，便于补全。
   - **编辑**：修改课程标题、正文。
   - **删除**：二次确认删除该课程及其全部句子。

## 金币流水（银行流水）

- **学生端「🪙 金币」页**：像银行流水一样展示每一笔金币的获取与支出（时间 / 类别 / 变动 / 原因），**管理员奖励会显示具体原因**。
- **管理员端「金币流水」页**：汇总所有学员的金币发放 / 扣除记录（含签到、学习、奖励、扣减、购物、退款等），并显示操作管理员。

## 商店与订单生命周期

- 学生在「商店」兑换后生成**订单**；学生端「我的订单」实时显示状态：
  - `待发货` → 管理员**发货**（后台「商店」标签）→ `已发货` → 线下交付后管理员**存档完成** → `交易完成`（学生端同步显示）。
  - 若**缺货**，管理员可**驳回退款**：订单变 `已驳回`，金币自动退回学生，学生端显示「已驳回·已退款」及原因。
- 管理员可在「商店」标签对商品**上架 / 下架**。

## 许愿池生命周期

- 学生发布愿望（投入金币）→ `审核中`；任何人可在许愿池**助力**投币。
- 管理员在「许愿池」标签：**批准**（→ `已批准·处理中`）→ 线下交付后**完成归档**（→ `已完成`）；或**驳回退款**（→ `已驳回`，创建人原始投入 + 所有助力金币自动退回，学生端显示「已驳回·已退款」）。

> 退出：学生端/管理员端顶栏均有「退出」按钮（清空本地登录态）。改密：学生首页「修改密码」卡片、管理员「账号」标签页。
> 学员管理：每行有「删除」按钮，点击后需**二次输入管理员密码**确认，防止误删（同时级联清理其学习进度/错题/金币流水/订单/愿望）。
> 后台「设置」标签：可配置每日签到金币、是否要求先完成任务、连续签到每日奖励与封顶天数。
> 许愿池：学生端「许愿」页的「🌟 许愿池」展示**所有同学**的愿望（审核中/已批准），任何人可点「助力」投币支持。

## 测试流程建议

1. 用 admin/admin123 登录后台 → 上传 JSON 课程 → 批量上传音频 → 发布 → 分配给自己新建的学生账号。
2. 学生端登录 → 每日签到 → 进入课程按 Step 1→5 闯关。
3. （可选）在「设置 API Key」填入 DeepSeek Key 后，本地判分不通过时由 AI 按完整度+准确度二次打分，更精准。
4. 商店上架商品、发布愿望并互相助力、查看报表。
