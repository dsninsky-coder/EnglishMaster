/* ============ 学生端页面 ============ */

/* ---------- 首页：英语大师三大入口选择器 ---------- */
async function renderHome() {
  const me = await api('/me')
  if (me.ok) setUser(me.data.user)
  const u = getUser() || {}
  const ci = await api('/checkin/info')
  const info = ci.ok ? ci.data : { coin: 1, require_task: true, already: false, streak: 0, did_task_today: false }
  const checkedToday = info.already
  const checkinBtn = checkedToday
    ? `<button class="big-checkin done" disabled>已签到 ✓ (+${info.coin})</button>`
    : `<button class="big-checkin" onclick="doCheckin()">📅 每日签到 (+${info.coin})</button>`
  const taskHint = info.require_task
    ? `<p class="muted" style="margin-top:8px">${info.did_task_today ? '✅ 今日已完成学习任务，可签到' : '⚠️ 需先完成至少一个学习任务（任一 Step 提交）才能签到'}</p>`
    : `<p class="muted" style="margin-top:8px">连续签到越久奖励越高</p>`

  const cards = [
    { icon: '🎧', title: '听说大师', desc: '五步法闯关 · 沉浸式听说深度学习', go: "nav('#/listen')", theme: 'a' },
    { icon: '📚', title: '单词大师', desc: '艾宾浩斯记忆曲线 · 背单词 / 复习 / 考试', go: "window.location.href='/study'", theme: 'b' },
    { icon: '🎁', title: '奖励中心', desc: '金币 · 商城 · 许愿池 · 用努力兑换奖励', go: "nav('#/rewards')", theme: 'c' },
  ].map(c => `<div class="entry-card entry-${c.theme}" onclick="${c.go}">
      <div class="entry-icon">${c.icon}</div>
      <div class="entry-title">${c.title}</div>
      <div class="entry-desc">${c.desc}</div>
      <div class="entry-go">进入 →</div>
    </div>`).join('')

  el('app').innerHTML = studentFrame(`
    <div class="card" style="margin-bottom:18px">
      <h3>每日签到</h3>
      ${checkinBtn}
      ${taskHint}
    </div>
    <h3 style="margin:6px 0 12px">选择学习模块</h3>
    <div class="entry-grid">${cards}</div>
  `, 'home')
}

/* ---------- 听说大师：课程列表（原首页主体） ---------- */
async function renderListenHome() {
  const me = await api('/me')
  if (me.ok) setUser(me.data.user)
  const r = await api('/courses')
  let coursesHtml
  if (!r.ok) coursesHtml = `<div class="empty">加载失败：${esc(r.data.error || '')}</div>`
  else {
    const cs = r.data.courses || []
    if (!cs.length) coursesHtml = `<div class="empty">等待老师分配课程 📭<br/>暂时还没有可学习的课程</div>`
    else coursesHtml = cs.map(c => {
      const status = c.status || (c.is_completed ? 'review' : 'start')
      const cur = c.current_step || 1
      const pct = Math.round(((c.completed_steps || []).length / 7) * 100)
      let badge = '', btn
      if (status === 'locked') {
        badge = '<span class="tag danger">🔒 未解锁</span>'
        btn = `<button class="btn" disabled style="opacity:.5;cursor:not-allowed">🔒 未解锁</button>`
      } else if (status === 'review') {
        badge = '<span class="done-badge">✅ 已通关</span>'
        btn = `<button class="btn ghost" onclick="nav('#/learn/${c.course_id}')">回顾</button>`
      } else {
        btn = `<button class="btn" onclick="nav('#/learn/${c.course_id}')">开始</button>`
      }
      return `<div class="card course-card">
        <div>
          <div style="font-weight:600">${esc(c.title)}</div>
          <div class="muted" style="font-size:13px">${status === 'locked' ? '完成上一门课程后解锁' : '当前进度 Step ' + cur + '/5 · 已通关 ' + pct + '%'}</div>
          ${badge}
        </div>
        ${btn}
      </div>`
    }).join('')
  }
  el('app').innerHTML = studentFrame(`
    <div class="spread" style="margin-bottom:12px">
      <h3>听说大师 · 我的课程</h3>
      <button class="btn ghost sm" onclick="nav('#/')">← 返回入口</button>
    </div>
    ${coursesHtml}
  `, 'listen')
}

/* ---------- 奖励中心：金币 / 商城 / 许愿池 总入口 ---------- */
async function renderRewards() {
  const u = getUser() || {}
  const inner = `<div class="spread" style="margin-bottom:12px">
      <h3>奖励中心</h3>
      <button class="btn ghost sm" onclick="nav('#/')">← 返回入口</button>
    </div>
    <div class="entry-grid">
      <div class="entry-card entry-c" onclick="nav('#/coins')">
        <div class="entry-icon">🪙</div>
        <div class="entry-title">我的金币</div>
        <div class="entry-desc">当前余额 ${u.coin_balance ?? 0} · 查看流水</div>
        <div class="entry-go">进入 →</div>
      </div>
      <div class="entry-card entry-b" onclick="nav('#/shop')">
        <div class="entry-icon">🛒</div>
        <div class="entry-title">奖励商城</div>
        <div class="entry-desc">用金币兑换免错券等好礼</div>
        <div class="entry-go">进入 →</div>
      </div>
      <div class="entry-card entry-a" onclick="nav('#/wishes')">
        <div class="entry-icon">🌟</div>
        <div class="entry-title">许愿池</div>
        <div class="entry-desc">发起心愿，或助力他人圆梦</div>
        <div class="entry-go">进入 →</div>
      </div>
    </div>`
  el('app').innerHTML = studentFrame(inner, 'rewards')
}

async function doCheckin() {
  const r = await api('/checkin', 'POST')
  if (!r.ok) { toast(r.data.error || '签到失败', true); return }
  if (r.data.already) { toast('今日已签到'); }
  else {
    toast(`签到成功 +${r.data.coins_gained} 金币${r.data.bonus ? '（连签奖励+' + r.data.bonus + '）' : ''}`)
    setBalance(r.data.balance)
    const u = getUser(); if (u) { u.last_checkin_date = new Date().toISOString().slice(0, 10); setUser(u) }
  }
  renderHome()
}
async function saveApiKey() {
  const v = el('apikey').value.trim()
  if (!v || v === '已设置（留空不改）') { toast('未修改'); return }
  const r = await api('/user/apikey', 'POST', { api_key: v })
  if (!r.ok) { toast(r.data.error || '保存失败', true); return }
  toast('已保存 API Key')
  const u = getUser(); if (u) { u.has_private_key = true; setUser(u) }
}

/* ---------- 设置页（原首页的 API Key + 改密 移入此处） ---------- */
async function renderSettings() {
  const me = await api('/me')
  if (me.ok) setUser(me.data.user)
  const u = getUser() || {}
  el('app').innerHTML = studentFrame(`
    <div class="spread" style="margin-bottom:12px">
      <h3>设置</h3>
      <button class="btn ghost sm" onclick="nav('#/')">← 首页</button>
    </div>
    <div class="card">
      <h3>我的 DeepSeek API Key</h3>
      <p class="muted" style="font-size:13px">填写后可用于更精准的语义评分；也可由老师分配共享 Key。</p>
      <input id="apikey" placeholder="sk-..." value="${u.has_private_key ? '已设置（留空不改）' : ''}" style="margin-top:8px" />
      <button class="btn block" style="margin-top:8px" onclick="saveApiKey()">保存 Key</button>
    </div>
    <div class="card" style="margin-top:12px">
      <h3>修改密码</h3>
      <p class="muted" style="font-size:13px">为保障账号安全，建议定期更换密码。</p>
      <button class="btn block" style="margin-top:8px" onclick="openChangePassword()">修改密码</button>
    </div>
  `, 'settings')
}

/* ---------- 学习页：Step 0~5 ---------- */
let learn = { courseId: null, course: null, sentences: [], unlocks: {}, step: 1, idx: 0, results: [], view: 'show' }

async function renderLearn(courseId) {
  const r = await api(`/courses/${courseId}/sentences`)
  const cr = await api('/courses')
  if (!r.ok) { el('app').innerHTML = studentFrame(`<div class="empty">${esc(r.data.error || '加载失败')}</div>`); return }
  const courseInfo = (cr.ok && (cr.data.courses || []).find(c => String(c.course_id) === String(courseId))) || {}
  if (courseInfo.status === 'locked') {
    toast('该课程尚未解锁，完成上一门课程后解锁', true)
    nav('#/')
    return
  }
  learn = {
    courseId, course: r.data.course, sentences: r.data.sentences || [],
    unlocks: courseInfo.step_unlocks || { '1': true }, step: 1, idx: 0, results: [], view: 'show',
    showOverview: false, queue: null, queueStep: -1, wrongSet: new Set(),
    curSentIdx: -1, passNo: 1, hadRedo: false,
    allowSkip: !!(cr.ok && cr.data.allow_skip),   // 该生是否被管理员允许"强制解锁下一步"
    enHint: r.data.en_hint || { words: 3, changes: 5 },  // 中译英提示配置（管理员后台设置）
    words: [], wordResults: [], wordIdx: 0,   // Step7 单词巩固状态
    appealLocked: !!courseInfo.appeal_locked, appealLockStep: courseInfo.appeal_lock_step || null,
    appealedSet: new Set(),   // 已申请复议的句子/单词，防重复
  }
  drawLearn()
}

const STEP_NAMES = {0: '词汇', 1: '沉浸', 2: '英译中', 3: '听音', 4: '跟读', 5: '中译英', 6: '续写', 7: '单词'}

function drawLearn() {
  const total = learn.sentences.length
  const unlocks = learn.unlocks
  const stepsHtml = [0, 1, 2, 3, 4, 5, 6, 7].map(n => {
    if (n === 0) return `<div class="step-pill ${learn.step === 0 ? 'active' : ''}" onclick="goStep(0)">词汇</div>`
    const locked = !unlocks[String(n)]
    const cls = learn.step === n ? 'active' : (locked ? 'locked' : 'done')
    return `<div class="step-pill ${cls}" ${locked ? '' : `onclick="goStep(${n})"`}>${locked ? '🔒 ' : ''}Step ${n} · ${STEP_NAMES[n]}</div>`
  }).join('')
  const banner = learn.appealLocked
    ? `<div class="card" style="border:1px solid #e74c3c;background:#fff5f5">
         <b>⚠️ 课程已锁定</b>
         <p class="muted" style="margin-top:6px">人工复议被驳回，需重新完成 <b>Step ${learn.appealLockStep}</b> 才能解锁后续内容。已完成的步骤不受影响。</p>
       </div>` : ''
  el('app').innerHTML = studentFrame(`
    ${banner}
    <div class="card">
      <h3>${esc(learn.course.title)}</h3>
      <div class="muted">共 ${total} 句${learn.sentences.length < (learn.course_sentence_total || total) ? '（优先练习未掌握句）' : ''}</div>
      <div class="steps">${stepsHtml}</div>
    </div>
    <div id="step-body"></div>
  `, 'home')
  if (learn.showOverview) {
    el('step-body').innerHTML = `<div class="card center">
      <h3>选择步骤开始练习</h3>
      <p class="muted">点击上方任一已解锁步骤开始。中途退出不保留进度，再次进入将从头开始。</p>
    </div>`
    return
  }
  drawStepBody()
}

function goStep(n, skipFull) {
  if (learn.appealLocked && n > learn.appealLockStep) {
    toast('课程已锁定，请先重新完成 Step ' + learn.appealLockStep, true); return
  }
  if (n > 0 && !learn.unlocks[String(n)]) { toast('该步骤尚未解锁', true); return }
  // 进入步骤6（续写）之前，先展示全文回顾（预学），每次进入都显示
  if (n === 6 && !skipFull) { drawFullText(el('step-body')); return }
  // 进入步骤7（单词巩固）：重置单词分批状态，从头开始整表乱序取词
  if (n === 7) {
    learn.words = null; learn.wordBatch = 0
    learn.wordCorrectTotal = 0; learn.wordTotal = 0; learn.wordItems = null
  }
  learn.step = n; learn.idx = 0; learn.results = []; learn.view = 'show'
  learn.queue = null; learn.wrongSet = new Set()   // 重置本步练习状态（重新开始）
  learn.showOverview = false; learn.hadRedo = false
  drawLearn()   // 重新渲染顶部步骤条，保证解锁状态同步
}

function backToSteps() {
  learn.showOverview = true
  learn.idx = 0; learn.results = []; learn.queue = null; learn.wrongSet = new Set()
  drawLearn()
}

function drawStepBody() {
  const body = el('step-body')
  if (learn.step === 0) return drawStep0(body)
  if (learn.step === 1) return drawStep1(body)
  if (learn.step === 7) return drawStep7(body)
  return drawStepN(body)
}

/* Step 0：词汇预览 */
function drawStep0(body) {
  const words = {}
  learn.sentences.forEach(s => (s.target_words || []).forEach(w => words[w] = true))
  const tags = Object.keys(words).map(w => `<span class="tag tw">${esc(w)}</span>`).join('') || '<span class="muted">本课无核心词</span>'
  body.innerHTML = `<div class="card">
    <div class="spread"><h3>Step 0 · 词汇预览</h3><button class="btn ghost sm" onclick="backToSteps()">← 步骤</button></div>
    <p class="muted">先熟悉本课核心词，进入正式学习再听音跟读。</p>
    <div>${tags}</div>
    <button class="btn block" style="margin-top:12px" onclick="goStep(1)">进入 Step 1 →</button>
  </div>`
}

/* Step 1：沉浸输入（无评分） */
function drawStep1(body) {
  const s = learn.sentences[learn.idx]
  if (!s) { finishStepView(body); return }
  const tw = s.target_words || []
  const hasAudio = !!s.audio_url
  const isLast = learn.idx + 1 >= learn.sentences.length
  body.innerHTML = `<div class="card">
    <div class="spread">
      <span class="muted">第 ${learn.idx + 1}/${learn.sentences.length} 句</span>
      <button class="btn ghost sm" onclick="backToSteps()">← 步骤</button>
    </div>
    <div class="cn" style="margin-top:10px">${esc(s.chinese)}</div>
    <div id="en1" style="margin-top:12px">
      <div class="sentence">${hl(s.english, tw)}</div>
      ${hasAudio ? `<button class="btn ghost sm aud-btn" style="margin-top:8px" data-label="🔊 再听这句" onclick="playAudio('${esc(s.audio_url)}', 1, this)">🔊 再听这句</button>` : ''}
    </div>
    ${hasAudio ? `<div class="rate-btns" style="margin-top:12px">${rateButtons(s.audio_url)}</div>` : '<div class="muted" style="margin-top:12px">无音频</div>'}
    <button class="btn block" style="margin-top:12px" id="toggleEn1" onclick="toggleEnglish1()">隐藏原文</button>
    <div class="req-hint">学习要求：请仔细听录音，阅读英文，并看翻译。搞懂后，隐藏英文再听一遍。不看英文也能听懂后，进入下一句。</div>
    <div class="row" style="margin-top:12px">
      ${learn.idx > 0 ? `<button class="btn ghost" style="flex:1" onclick="prevStep1()">← 上一句</button>` : '<span style="flex:1"></span>'}
      <button class="btn" style="flex:1" onclick="nextStep1()">${isLast ? '完成浏览 →' : '下一句 →'}</button>
    </div>
  </div>`
}
function toggleEnglish1() {
  const e = el('en1'); const btn = el('toggleEn1')
  if (!e || !btn) return
  if (e.style.display === 'none') { e.style.display = 'block'; btn.textContent = '隐藏原文' }
  else { e.style.display = 'none'; btn.textContent = '显示英文' }
}
function prevStep1() {
  if (learn.idx > 0) { learn.idx--; drawStep1(el('step-body')) }
}
function nextStep1() {
  learn.idx++
  if (learn.idx >= learn.sentences.length) finishStepView(el('step-body'))
  else drawStep1(el('step-body'))
}

/* Step 2~5：答题步骤（做错反复重练，直到全部做对才解锁） */
function drawStepN(body) {
  const step = learn.step
  if (step === 7) return drawStep7(body)
  // 进入本步时初始化练习队列
  if (learn.queueStep !== step || !learn.queue) {
    learn.queueStep = step
    const seq = learn.sentences.map((_, i) => i)
    // Step3 听音写中文 / Step5 中译英 / Step6 续写 随机出题；Step4 跟读按原顺序
    learn.order = (step === 3 || step === 5 || step === 6) ? shuffle(seq) : seq
    learn.queue = learn.order.slice()
    learn.wrongSet = new Set()
    learn.idx = 0
    learn.results = []
    learn.passNo = 1
    learn.hadRedo = false
  }
  if (learn.idx >= learn.queue.length) { finishStepView(body); return }
  const sentIdx = learn.queue[learn.idx]
  learn.curSentIdx = sentIdx
  const s = learn.sentences[sentIdx]

  // Step4 跟读：非评分，听音跟读 + 上一句/下一句导航
  if (step === 4) return drawFollow(body, s)

  let promptHtml = '', inputHint = '', audioCtl = ''
  if (step === 2) {
    promptHtml = `<div class="sentence">${hl(s.english, s.target_words || [])}</div>`
    inputHint = '请输入中文翻译'
  } else if (step === 3) {
    const hasAudio = !!s.audio_url
    if (hasAudio) {
      promptHtml = `<div class="rate-btns">${rateButtons(s.audio_url)}</div>
        <p class="muted" style="font-size:12px;margin-top:6px">仅听音（不显示文字），写出你听到的中文意思</p>`
    } else {
      promptHtml = `<div class="cn">（本句无音频，直接写出中文意思）</div>`
    }
    inputHint = '听音后写出中文意思'
  } else if (step === 5) {
    promptHtml = `<div class="cn">${esc(s.chinese)}</div>`
    inputHint = '请输入英文（中译英）'
  } else if (step === 6) {
    const prevIdx = learn.sentences.findIndex(x => x.sentence_order === s.sentence_order)
    const nxt = learn.sentences[prevIdx + 1]
    if (!nxt) { learn.idx++; return drawStepN(body) }
    promptHtml = `<div class="sentence">${hl(s.english, s.target_words || [])}</div>
      <p class="muted">↑ 这是上文，请写出它的<b>下一句</b>英文：</p>`
    inputHint = '请输入下一句英文'
  }
  // Step5 中译英：随机单词提示（可更换，更换足够多次即可揭示全句）
  let hintHtml = ''
  if (step === 5) {
    if (!learn.hintFor || learn.hintFor.idx !== sentIdx) {
      learn.hintFor = { idx: sentIdx, revealed: new Set(), changes: 0 }
    }
    const cfg = learn.enHint || { words: 3, changes: 5 }
    const words = s.english.split(/\s+/).filter(Boolean)
    const shown = words.map((w, i) => learn.hintFor.revealed.has(i)
      ? `<b>${esc(w)}</b>` : '<span class="hw">_____</span>').join(' ')
    const done = learn.hintFor.revealed.size >= words.length
    hintHtml = `<div class="hint-box">
      <div class="spread"><span class="muted">单词提示（${learn.hintFor.revealed.size}/${words.length}，已更换 ${learn.hintFor.changes}/${cfg.changes} 次）</span>
        <button class="btn ghost sm" onclick="changeEnHint()" ${learn.hintFor.changes >= cfg.changes || done ? 'disabled' : ''}>换一批提示</button></div>
      <div class="en-hint">${shown}</div>
    </div>`
  }
  const totalQ = learn.queue.length
  const showSkip = (step === 2 || step === 3 || step === 5 || step === 6)
  body.innerHTML = `<div class="card">
    <div class="spread">
      <span class="muted">第 ${learn.idx + 1}/${totalQ} 句 · Step ${step}${learn.passNo > 1 ? ' · 第' + learn.passNo + '轮' : ''}</span>
      <button class="btn ghost sm" onclick="backToSteps()">← 步骤</button>
    </div>
    <div style="margin-top:10px">${promptHtml}</div>
    ${audioCtl}
    ${hintHtml}
    <textarea id="uin" rows="2" placeholder="${inputHint}" style="margin-top:12px"></textarea>
    <div class="req-hint">${stepHint(step)}</div>
    ${showSkip ? `<div class="row" style="margin-top:12px">
      <button class="btn" style="flex:2" onclick="submitStepN(${s.id}, ${step})">提交</button>
      <button class="btn ghost" style="flex:1" onclick="skipStepN(${s.id}, ${step})">跳过看答案</button>
    </div>` : `<button class="btn block" style="margin-top:12px" onclick="submitStepN(${s.id}, ${step})">提交</button>`}
    <div id="fb"></div>
  </div>`
}

/* Step4 跟读：屏幕上显示英文原文，学生用语音（或键盘）输入英文原文，本地逐字对比 */
function drawFollow(body, s) {
  const tw = s.target_words || []
  const hasAudio = !!s.audio_url
  const isLast = learn.idx + 1 >= learn.queue.length
  const totalQ = learn.queue.length
  body.innerHTML = `<div class="card">
    <div class="spread">
      <span class="muted">第 ${learn.idx + 1}/${totalQ} 句 · Step 4 跟读</span>
      <button class="btn ghost sm" onclick="backToSteps()">← 步骤</button>
    </div>
    <div class="sentence" style="margin-top:10px">${hl(s.english, tw)}</div>
    <div class="muted" style="margin-top:6px">${esc(s.chinese)}</div>
    ${hasAudio ? `<div class="rate-btns" style="margin-top:10px"><button class="btn ghost sm aud-btn" data-label="🔊 听原音" onclick="playAudio('${esc(s.audio_url)}', 1, this)">🔊 听原音</button></div>` : ''}
    <div class="req-hint">学习要求：看着英文原文，用麦克风跟读这句（或直接在下方输入）。系统会逐字对比你的输入与原句（不区分大小写）。读错了会显示原句并播放原音。</div>
    <div class="row" style="margin-top:12px">
      <button class="btn ghost" style="flex:1" onclick="startFollowRecognition('foll_${s.id}')">🎤 开始跟读</button>
    </div>
    <textarea id="foll_${s.id}" rows="2" placeholder="点「开始跟读」用语音输入，或直接打字输入英文原文" style="margin-top:10px"></textarea>
    <button class="btn block" style="margin-top:10px" onclick="submitFollow('foll_${s.id}', ${s.id})">提交对比</button>
    <div id="fb"></div>
    <div class="row" style="margin-top:12px">
      ${learn.idx > 0 ? `<button class="btn ghost" style="flex:1" onclick="prevStepN()">← 上一句</button>` : '<span style="flex:1"></span>'}
      <button class="btn" style="flex:1" onclick="nextStepN()">${isLast ? '完成跟读 →' : '下一句 →'}</button>
    </div>
  </div>`
}

function normFollow(t) {
  return (t || '').toLowerCase().replace(/[^a-z0-9\s]/g, '').replace(/\s+/g, ' ').trim()
}
function compareFollow(student, original) {
  const a = normFollow(student), b = normFollow(original)
  if (a === b) return { passed: true, ratio: 1, html: esc(original) }
  const arrA = a.split(''), arrB = b.split('')
  const n = Math.max(arrA.length, arrB.length)
  let match = 0, html = ''
  for (let i = 0; i < n; i++) {
    const ca = arrA[i], cb = arrB[i]
    if (ca !== undefined && ca === cb) { match++; html += `<span class="cm">${esc(cb)}</span>` }
    else { html += `<span class="cw">${esc(cb === undefined ? '·' : cb)}</span>` }
  }
  const ratio = n ? match / n : 0
  return { passed: ratio >= 0.9, ratio, html }
}
function startFollowRecognition(taId) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition
  if (!SR) { toast('当前浏览器不支持语音识别，请直接打字输入', true); return }
  if (window.__followRec) { try { window.__followRec.stop() } catch (e) {} return }
  const rec = new SR()
  rec.lang = 'en-US'; rec.interimResults = false; rec.maxAlternatives = 1
  const ta = el(taId)
  rec.onresult = (e) => { if (ta) ta.value = e.results[0][0].transcript; toast('语音已识别，可点「提交对比」') }
  rec.onerror = (e) => { toast('语音识别失败：' + (e.error || '') + '，请直接打字', true); window.__followRec = null }
  rec.onend = () => { window.__followRec = null }
  window.__followRec = { stop: () => { try { rec.stop() } catch (e) {}; window.__followRec = null } }
  try { rec.start() } catch (e) { toast('无法启动语音，请直接打字', true); window.__followRec = null }
}
function submitFollow(taId, sentenceId) {
  const s = learn.sentences[learn.curSentIdx]
  const ta = el(taId)
  if (!ta || !ta.value.trim()) { toast('请先输入或语音跟读', true); return }
  const res = compareFollow(ta.value, s.english)
  const fb = el('fb')
  if (res.passed) {
    playTone('ok')
    fb.innerHTML = `<div class="feedback ok">✅ 跟读正确！（匹配度 ${Math.round(res.ratio * 100)}%）</div>`
  } else {
    playTone('err')
    const audioBtn = s.audio_url
      ? `<button class="btn ghost sm aud-btn" style="margin-top:8px" data-label="🔊 听原音" onclick="playAudio('${esc(s.audio_url)}', 1, this)">🔊 听原音</button>` : ''
    fb.innerHTML = `<div class="feedback retry">🙂 跟读有偏差，对照一下原句（绿=对，红=错）：<br/>
      <div class="sentence" style="margin-top:6px">${res.html}</div>
      <div class="muted" style="margin-top:6px">你的输入：${esc(ta.value)}</div>${audioBtn}</div>`
    if (s.audio_url) playAudio(s.audio_url, 1, null)
  }
}
function nextStepN() {
  learn.idx++
  if (learn.idx >= learn.queue.length) finishStepView(el('step-body'))
  else drawStepN(el('step-body'))
}
function prevStepN() {
  if (learn.idx > 0) { learn.idx--; drawStepN(el('step-body')) }
}
function changeEnHint() {
  const s = learn.sentences[learn.curSentIdx]
  const hs = learn.hintFor
  if (!hs) return
  const words = s.english.split(/\s+/).filter(Boolean)
  const cfg = learn.enHint || { words: 3, changes: 5 }
  if (hs.changes >= cfg.changes) { toast('已达最大更换次数', true); return }
  hs.changes++
  const hidden = []
  words.forEach((w, i) => { if (!hs.revealed.has(i)) hidden.push(i) })
  shuffle(hidden).slice(0, cfg.words).forEach(i => hs.revealed.add(i))
  if (hs.changes >= cfg.changes) { words.forEach((w, i) => hs.revealed.add(i)) }  // 最后一次揭示全句答案
  drawStepN(el('step-body'))
}

function stepHint(step) {
  if (step === 2) return '学习要求：将上面的英语句子翻译为中文。'
  if (step === 3) return '学习要求：讲听到的英语句子的中文意思写出来。如果不会了，请回到步骤1。'
  if (step === 4) return '学习要求：看着英文原文，用麦克风跟读或用键盘输入这句英文，系统会逐字对比。'
  if (step === 5) return '学习要求：把上面的中文句子翻译成英文（可用「换一批提示」逐步揭示单词）。'
  if (step === 6) return '学习要求：根据上文学写出连贯的下一句英文。'
  return ''
}

async function submitStepN(sentenceId, step) {
  const uin = el('uin').value.trim()
  if (!uin) { toast('请输入内容', true); return }
  const r = await api('/step/submit', 'POST', { sentence_id: sentenceId, step, user_input: uin })
  if (!r.ok) { toast(r.data.error || '提交失败', true); return }
  const d = r.data
  learn.results.push(d.correct)
  const sentIdx = learn.curSentIdx
  if (d.correct) learn.wrongSet.delete(sentIdx)
  else { learn.wrongSet.add(sentIdx); learn.hadRedo = true }
  const fb = el('fb')
  const std = d.standard_answer || ''
  let head, cls
  if (d.correct) { head = '✅ 正确！'; cls = 'ok'; playTone('ok') }
  else { head = '🙂 再体会一下'; cls = 'retry'; playTone('err') }
  let extra = ''
  if (step === 4 && d.local_match) extra += `<br/><span class="muted">本地匹配 ${esc(d.local_match)}</span>`
  if (!d.correct) extra += `<br/>标准答案：${esc(std)}${d.error_type ? '<br/>提示：' + esc(d.error_type) : ''}`
  const lastOfPass = learn.idx + 1 >= learn.queue.length
  let appealBtn = ''
  if (!d.correct && [2, 3, 5, 6].includes(step) && !learn.appealedSet.has(sentenceId)) {
    appealBtn = `<button class="btn ghost block" style="margin-top:8px" id="appealBtn"
      onclick="appealSentence(${sentenceId}, ${step}, '${esc(std)}')">⚖️ 申请人工复议（花费 2 金币）</button>`
  }
  fb.innerHTML = `<div class="feedback ${cls}">${head}${extra}</div>
    ${appealBtn}
    <button class="btn block" style="margin-top:10px" onclick="afterStepSubmit()">${lastOfPass ? '本轮结束 →' : '下一句 →'}</button>`
}

/* 学生申请人工复议：扣 2 金币，该题暂记通过以便继续 */
async function appealSentence(sentenceId, step, std) {
  const uin = (el('uin') && el('uin').value || '').trim()
  const btn = el('appealBtn')
  if (btn) { btn.disabled = true; btn.textContent = '申请中…' }
  const r = await api('/step/appeal', 'POST', { sentence_id: sentenceId, step, user_input: uin, standard_answer: std })
  if (!r.ok) {
    toast(r.data.error || '申请失败', true)
    if (btn) { btn.disabled = false; btn.textContent = '⚖️ 申请人工复议（花费 2 金币）' }
    return
  }
  toast(r.data.message || '已申请人工复议', false)
  if (learn.curSentIdx != null) learn.wrongSet.delete(learn.curSentIdx)
  learn.appealedSet.add(sentenceId)
  afterStepSubmit()
}

/* ============ Step7 单词巩固（v0.6：整表乱序 → 每批10个顺序取 → 音汉/英汉交替） ============ */
const WORD_PER_ROUND = 10
async function drawStep7(body) {
  if (!learn.words || !learn.words.length) {
    const r = await api(`/courses/${learn.courseId}/words`)
    if (!r.ok) {
      body.innerHTML = `<div class="card empty">${esc(r.data.error || '单词库为空，请管理员先提取单词')}</div>`
      return
    }
    learn.words = r.data.words || []      // 后端已对整个单词表乱序
    learn.wordBatch = 0
    learn.wordCorrectTotal = 0
    learn.wordTotal = 0
  }
  if (!learn.words.length) {
    body.innerHTML = `<div class="card center"><h3>本课暂无单词</h3>
      <p class="muted">请管理员在课程管理中点「提取单词」生成单词库。</p>
      <button class="btn" style="margin-top:12px" onclick="backToSteps()">返回步骤</button></div>`
    return
  }
  const start = learn.wordBatch * WORD_PER_ROUND
  const batch = learn.words.slice(start, start + WORD_PER_ROUND)
  if (!batch.length) { finishStep7(); return }   // 全部取完 → 结算
  // 本批 10 个：音译中 / 英译中 交替（首词从「音译中」开始）
  const items = batch.map((w, i) => ({
    word: w,
    mode: (i % 2 === 0) ? 'audio2zh' : 'en2zh',
    answered: false, correct: null, reason: ''
  }))
  learn.wordItems = items
  const total = learn.words.length
  const cards = items.map((it, i) => {
    const head = it.mode === 'en2zh'
      ? `<div class="word-en">${esc(it.word)}</div>`
      : `<div class="word-audio">
           <button class="btn ghost sm" onclick="playYoudao('${esc(it.word)}','us')">🇺🇸 美音</button>
           <button class="btn ghost sm" onclick="playYoudao('${esc(it.word)}','uk')">🇬🇧 英音</button>
           <span class="muted">（听发音，写出中文意思）</span>
         </div>`
    return `<div class="word-card" id="wc_${i}">
      <div class="spread"><span class="muted">第 ${start + i + 1}/${total} 词 · ${it.mode === 'en2zh' ? '英译中' : '音译中'}</span></div>
      ${head}
      <input id="wa_${i}" class="word-input" placeholder="写出中文意思" />
      <button class="btn ghost sm" style="margin-top:8px" onclick="judgeWord(${i})">判断</button>
      <div id="wr_${i}"></div>
    </div>`
  }).join('')
  const lastBatch = (start + WORD_PER_ROUND) >= total
  body.innerHTML = `<div class="card">
    <div class="spread"><h3>Step 7 · 单词巩固</h3><button class="btn ghost sm" onclick="backToSteps()">← 步骤</button></div>
    <p class="muted">本课单词已整体乱序，每批 10 个、音译中/英译中交替（从听音开始）。已完成 ${start}/${total} 词。</p>
    ${cards}
    <button class="btn block" style="margin-top:16px" id="wnext" disabled
      onclick="${lastBatch ? 'finishStep7()' : 'nextWordBatch()'}">${lastBatch ? '完成本步' : '下一批单词 →'}</button>
  </div>`
}

function nextWordBatch() {
  learn.wordBatch += 1
  drawStep7(el('step-body'))
}

async function judgeWord(i) {
  const it = learn.wordItems[i]
  if (!it || it.answered) return
  const ans = (el('wa_' + i).value || '').trim()
  if (!ans) { toast('请先写出意思', true); return }
  const r = await api('/step/word-judge', 'POST', { word: it.word, answer: ans, mode: it.mode })
  if (!r.ok) { toast(r.data.error || '判分失败', true); return }
  const d = r.data
  it.answered = true; it.correct = d.correct; it.reason = d.reason || ''
  const wr = el('wr_' + i)
  if (d.correct === true) {
    learn.wordCorrectTotal = (learn.wordCorrectTotal || 0) + 1
    learn.wordTotal = (learn.wordTotal || 0) + 1
    playTone('ok')
    wr.innerHTML = `<div class="feedback ok" style="margin-top:6px">✅ 正确！</div>`
    el('wc_' + i).classList.add('done')
  } else if (d.correct === false) {
    learn.wordTotal = (learn.wordTotal || 0) + 1
    playTone('err')
    const added = d.added_error ? '<div class="muted" style="margin-top:4px">📕 已自动加入生词表</div>' : ''
    const appealed = learn.appealedSet.has('w' + i)
    const appealBtn = appealed ? '' : `<button class="btn ghost sm" style="margin-top:6px" id="wapp_${i}" onclick="appealWord(${i}, '${esc(it.word)}')">⚖️ 人工复议(2金币)</button>`
    wr.innerHTML = `<div class="feedback retry" style="margin-top:6px">❌ 不正确<br/><b>解析：</b>${esc(d.reason || '与标准意思有差异，请对照学习。')}</div>${added}${appealBtn}`
    el('wc_' + i).classList.add('wrong')
  } else {
    learn.wordTotal = (learn.wordTotal || 0) + 1
    playTone('warn')
    wr.innerHTML = `<div class="feedback" style="margin-top:6px">⚠️ ${esc(d.reason || '暂无法判分')}</div>`
  }
  if (learn.wordItems.every(x => x.answered)) {
    const fb = el('wnext'); if (fb) fb.disabled = false
  }
}

function finishStep7() {
  const total = learn.wordTotal || 0
  const correct = learn.wordCorrectTotal || 0
  const acc = total ? correct / total : 0
  const perfect = total > 0 && correct === total
  finishStep(7, acc, perfect)
}

/* Step7 单词巩固：学生对判错的单词申请人工复议（2 金币），暂记通过 */
async function appealWord(i, word) {
  const it = learn.wordItems[i]
  const ans = (el('wa_' + i).value || '').trim()
  const btn = el('wapp_' + i)
  if (btn) { btn.disabled = true; btn.textContent = '申请中…' }
  const r = await api('/step/appeal', 'POST',
    { sentence_id: null, course_id: learn.courseId, step: 7, user_input: ans, standard_answer: word })
  if (!r.ok) {
    toast(r.data.error || '申请失败', true)
    if (btn) { btn.disabled = false; btn.textContent = '⚖️ 人工复议(2金币)' }
    return
  }
  toast(r.data.message || '已申请人工复议', false)
  it.answered = true; it.correct = true; it.appeal = true
  learn.wordCorrectTotal = (learn.wordCorrectTotal || 0) + 1
  learn.wordTotal = (learn.wordTotal || 0) + 1
  learn.appealedSet.add('w' + i)
  el('wr_' + i).innerHTML = `<div class="feedback ok" style="margin-top:6px">✅ 已申请人工复议，暂记通过，等待审核</div>`
  el('wc_' + i).classList.add('done')
  if (learn.wordItems.every(x => x.answered)) { const fb = el('wnext'); if (fb) fb.disabled = false }
}

let _audioCtx = null
function playTone(type) {
  /* 浏览器内置音效（Web Audio 合成，无 MP3 文件，零网络开销） */
  try {
    _audioCtx = _audioCtx || new (window.AudioContext || window.webkitAudioContext)()
    const ctx = _audioCtx
    if (ctx.state === 'suspended') ctx.resume()
    const now = ctx.currentTime
    if (type === 'ok') {
      [523.25, 659.25, 783.99].forEach((f, k) => {
        const o = ctx.createOscillator(), g = ctx.createGain()
        o.type = 'sine'; o.frequency.value = f
        o.connect(g); g.connect(ctx.destination)
        const t0 = now + k * 0.08
        g.gain.setValueAtTime(0.0001, t0)
        g.gain.exponentialRampToValueAtTime(0.25, t0 + 0.01)
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.18)
        o.start(t0); o.stop(t0 + 0.2)
      })
    } else if (type === 'err') {
      const o = ctx.createOscillator(), g = ctx.createGain()
      o.type = 'square'; o.frequency.value = 160
      o.connect(g); g.connect(ctx.destination)
      g.gain.setValueAtTime(0.0001, now)
      g.gain.exponentialRampToValueAtTime(0.25, now + 0.01)
      g.gain.exponentialRampToValueAtTime(0.0001, now + 0.35)
      o.start(now); o.stop(now + 0.36)
    } else {
      const o = ctx.createOscillator(), g = ctx.createGain()
      o.type = 'triangle'; o.frequency.value = 330
      o.connect(g); g.connect(ctx.destination)
      g.gain.setValueAtTime(0.0001, now)
      g.gain.exponentialRampToValueAtTime(0.2, now + 0.01)
      g.gain.exponentialRampToValueAtTime(0.0001, now + 0.2)
      o.start(now); o.stop(now + 0.22)
    }
  } catch (e) {}
}

function playYoudao(word, variant) {
  /* 有道词典发音 API（type=0 美音 / type=1 英音），跨域音频可直接播放，无需本地文件。
     不使用浏览器 speechSynthesis（移动端兼容性差）。 */
  const t = (variant === 'uk') ? 1 : 0
  const url = `https://dict.youdao.com/dictvoice?audio=${encodeURIComponent(word)}&type=${t}`
  try {
    const a = new Audio(url)
    a.play().catch(() => { toast('发音加载失败，请检查网络', true) })
  } catch (e) { toast('当前环境不支持音频播放', true) }
}

/* 跳过：不会做时直接看答案，本句判为未通过并纳入下一轮复习循环 */
async function skipStepN(sentenceId, step) {
  const r = await api('/step/submit', 'POST', { sentence_id: sentenceId, step, user_input: '', skipped: true })
  if (!r.ok) { toast(r.data.error || '操作失败', true); return }
  const d = r.data
  learn.results.push(false)
  const sentIdx = learn.curSentIdx
  learn.wrongSet.add(sentIdx); learn.hadRedo = true
  const std = d.standard_answer || ''
  const lastOfPass = learn.idx + 1 >= learn.queue.length
  el('fb').innerHTML = `<div class="feedback retry">⏭️ 已跳过 · 标准答案：<br/><b>${esc(std)}</b><br/><span class="muted">本句已纳入下一轮复习</span></div>
    <button class="btn block" style="margin-top:10px" onclick="afterStepSubmit()">${lastOfPass ? '本轮结束 →' : '下一句 →'}</button>`
}

function afterStepSubmit() {
  learn.idx++
  if (learn.idx < learn.queue.length) { drawStepN(el('step-body')); return }
  // 本轮结束
  if (learn.wrongSet.size === 0) { finishStepView(el('step-body')); return }
  // 还有做错的，进入下一轮只重练做错的句
  learn.queue = learn.order.filter(i => learn.wrongSet.has(i))
  learn.idx = 0
  learn.passNo++
  drawPassBreak(el('step-body'))
}

function drawPassBreak(body) {
  const n = learn.wrongSet.size
  const step = learn.step
  const skipBtn = learn.allowSkip
    ? `<button class="btn ghost block" style="margin-top:10px" onclick="forceUnlock(${step})">${step < 7 ? '仍有未掌握，强制解锁下一步 →' : '仍有未掌握，强制完成课程 →'}</button>
       <p class="muted" style="font-size:12px;margin-top:6px">强制解锁不发放金币奖励；未掌握的句子仍会保留在错题中。</p>`
    : ''
  body.innerHTML = `<div class="card center">
    <h3>本轮有 ${n} 句还没掌握</h3>
    <p class="muted">系统会自动把做错的句子再练一遍，直到全部做对。准备好了就继续。</p>
    <button class="btn block" style="margin-top:12px" onclick="drawStepN(el('step-body'))">继续重练 →</button>
    ${skipBtn}
  </div>`
}

/* 强制解锁下一步（需管理员为该生开启 allow_skip） */
async function forceUnlock(step) {
  const r = await api('/step/finish', 'POST', { course_id: learn.courseId, step, accuracy: 0, perfect: false, force: true })
  if (!r.ok) { toast(r.data.error || '强制解锁失败', true); return }
  toast('已强制解锁' + (step < 7 ? '下一步' : '并完成课程'))
  // 刷新解锁状态
  const cr = await api('/courses')
  if (cr.ok) {
    const info = (cr.data.courses || []).find(c => String(c.course_id) === String(learn.courseId))
    if (info) learn.unlocks = info.step_unlocks || learn.unlocks
  }
  if (step === 7) { setTimeout(() => nav('#/'), 800); return }
  if (learn.unlocks[String(step + 1)]) goStep(step + 1)
  else drawLearn()
}

function rateOptions(def) {
  return [0.5, 0.8, 1, 1.2, 1.5].map(r => `<option value="${r}" ${r === def ? 'selected' : ''}>${r}x</option>`).join('')
}
function curRate(id) {
  const s = el(id)
  return s ? parseFloat(s.value) : 1
}

/* 完成本步 */
function finishStepView(body) {
  const step = learn.step
  const total = (learn.order || learn.sentences).length || 1
  const correct = learn.results.filter(Boolean).length
  const acc = step === 1 ? 1 : (learn.wrongSet.size === 0 ? 1 : correct / total)
  const perfect = step >= 2 && !learn.hadRedo && learn.wrongSet.size === 0
  const nextLabel = step < 7 ? `解锁/进入步骤${step + 1}` : '完成课程'
  body.innerHTML = `<div class="card center">
    <h3>Step ${step} 完成</h3>
    ${step === 1 ? '<p>沉浸浏览完成</p>' : `<p>全部句子已做对，正确率 100%</p>`}
    ${step >= 2 && perfect ? '<p style="color:var(--accent);font-weight:600;margin-top:4px">🌟 一次性完美通关！</p>' : ''}
    <div class="row" style="margin-top:12px;justify-content:center">
      <button class="btn ghost" onclick="backToSteps()">返回步骤</button>
      <button class="btn" onclick="finishStep(${step}, ${acc}, ${perfect})">${nextLabel}</button>
    </div>
  </div>`
}
async function finishStep(step, accuracy, perfect) {
  const r = await api('/step/finish', 'POST', { course_id: learn.courseId, step, accuracy, perfect: !!perfect })
  if (!r.ok) {
    toast(r.data.error || '提交失败', true)
    if (r.data && r.data.threshold) toast(`正确率需达 ${Math.round(r.data.threshold * 100)}%`, true)
    return
  }
  const d = r.data
  if (d.passed === false) {
    toast(`正确率需达 ${Math.round((d.threshold || 0) * 100)}%，继续练习吧`, true)
    drawLearn()
    return
  }
  if (d.awards && d.awards.length) {
    if (d.balance != null) setBalance(d.balance)
    celebrate(d.awards.join('、'), d.balance)
  } else {
    toast('已记录进度')
  }
  // 刷新解锁状态与人工复议重锁状态
  const cr = await api('/courses')
  if (cr.ok) {
    const info = (cr.data.courses || []).find(c => String(c.course_id) === String(learn.courseId))
    if (info) {
      learn.unlocks = info.step_unlocks || learn.unlocks
      learn.appealLocked = !!info.appeal_locked
      learn.appealLockStep = info.appeal_lock_step || null
    }
  }
  // 步骤6/7完成：记录进度后自动返回首页（课程浏览界面）
  if (step === 6 || step === 7) {
    if (d.awards && d.awards.length) {
      if (d.balance != null) setBalance(d.balance)
      celebrate(d.awards.join('、'), d.balance)
    } else {
      toast('已记录进度')
    }
    setTimeout(() => {
      const m = document.querySelector('.celebrate')
      if (m && m.parentElement) m.parentElement.remove()
      nav('#/')
    }, 2400)
    return
  }
  // 解锁后自动进入下一步（步骤1 → 直接进入步骤2，无需再点）
  // 步骤5完成进入步骤6时，goStep(6) 会先展示全文回顾（Step6 预学）
  if (step < 7 && learn.unlocks[String(step + 1)]) {
    goStep(step + 1)
  } else {
    drawLearn()
  }
}

/* Step 6 预学：全文回顾（中英文对照，按原文顺序） */
function drawFullText(body) {
  const ordered = learn.sentences.slice().sort((a, b) => (a.sentence_order || 0) - (b.sentence_order || 0))
  body.innerHTML = `<div class="card">
    <div class="spread">
      <h3>全文回顾 · Step 6 预学</h3>
      <button class="btn ghost sm" onclick="backToSteps()">← 步骤</button>
    </div>
    <p class="muted">进入第六步前，先按顺序通读整篇，建立整体语感。一句英文，一句中文。</p>
    ${ordered.map(s => `<div class="ft-en">${hl(s.english, s.target_words || [])}</div>
      <div class="ft-cn">${esc(s.chinese)}</div>`).join('')}
    <button class="btn block" style="margin-top:16px" onclick="goStep(6, true)">进入 Step 6 →</button>
  </div>`
}
function celebrate(awards, balance) {
  const colors = ['#ffcf5c', '#4f8cff', '#3ecf8e', '#ff8fab', '#a0e7ff']
  let conf = ''
  for (let i = 0; i < 60; i++) {
    const left = Math.random() * 100
    const delay = Math.random() * 0.6
    const c = colors[i % colors.length]
    conf += `<i style="left:${left}%;background:${c};animation-delay:${delay}s"></i>`
  }
  const mask = document.createElement('div')
  mask.innerHTML = `<div class="confetti">${conf}</div>
    <div class="celebrate"><div class="box">
      <div class="big">🎉</div>
      <h3>闯关成功！</h3>
      <p>获得：${esc(awards)}</p>
      <p class="muted">当前金币：🪙 ${balance != null ? balance : '—'}</p>
      <button class="btn block" onclick="this.closest('.celebrate').parentElement.remove()">继续</button>
    </div></div>`
  document.body.appendChild(mask)
  setTimeout(() => mask.remove(), 6000)
}

/* ---------- 金币流水（银行流水） ---------- */
const STU_COIN_LABEL = {
  checkin: '签到', study: '学习奖励', reward: '管理员奖励', penalty: '管理员扣减',
  shop: '购物', wish: '许愿投入', support: '助力愿望', refund: '退款',
}
async function renderCoins() {
  const r = await api('/coin/transactions')
  let body
  if (!r.ok) body = `<div class="empty">${esc(r.data.error || '加载失败')}</div>`
  else {
    const txns = r.data.transactions || []
    const balance = r.data.balance ?? 0
    body = `<div class="card">
      <div class="spread"><h3>金币流水</h3><span class="coin big">🪙 ${balance}</span></div>
      <p class="muted" style="font-size:13px">每一笔金币的获取与支出，像银行流水一样清晰可查；管理员奖励也会显示原因。</p>
    </div>
    ${txns.length ? `<div class="card" style="padding:0">
      <div class="txn-head"><span>时间</span><span>类别</span><span>变动</span><span>说明</span></div>
      ${txns.map(t => `<div class="txn ${t.amount >= 0 ? 'in' : 'out'}">
        <span class="t-time">${esc(t.created_at)}</span>
        <span class="t-cat">${STU_COIN_LABEL[t.category] || (t.category || '—')}</span>
        <span class="t-amt">${t.amount >= 0 ? '+' : ''}${t.amount}</span>
        <span class="t-reason">${esc(t.reason)}</span>
      </div>`).join('')}
    </div>` : '<div class="empty">还没有金币记录</div>'}`
  }
  el('app').innerHTML = studentFrame(body, 'coins')
}

/* ---------- 商店 ---------- */
async function renderShop() {
  const r = await api('/shop/items')
  const ord = await api('/shop/orders')
  let html
  if (!r.ok) html = `<div class="empty">${esc(r.data.error || '加载失败')}</div>`
  else {
    const items = r.data.items || []
    if (!items.length) html = `<div class="empty">商店暂无上架商品</div>`
    else html = items.map(i => `<div class="card course-card">
      <div>
        <div style="font-weight:600">${esc(i.name)}</div>
        <div class="muted" style="font-size:13px">${esc(i.description || '')}</div>
        <div class="muted" style="font-size:13px">🪙 ${i.price_coins} · 库存 ${i.stock < 0 ? '∞' : i.stock}</div>
      </div>
      <button class="btn" onclick="buyItem(${i.id})">兑换</button>
    </div>`).join('')
  }
  // 我的订单
  const orders = ord.ok ? (ord.data.orders || []) : []
  const orderMap = { pending: '<span class="tag">待发货</span>', shipped: '<span class="tag warn">已发货</span>',
    completed: '<span class="tag ok">交易完成</span>', rejected: '<span class="tag danger">已驳回·已退款</span>' }
  const ordersHtml = `<h3 style="margin-top:18px">我的订单</h3>` + (orders.length ? orders.map(o => `<div class="card">
    <div class="spread"><b>${esc(o.item_name)}</b>${orderMap[o.status] || o.status}</div>
    <div class="muted" style="font-size:13px">🪙 ${o.price} · 下单 ${esc(o.created_at)}</div>
    ${o.status === 'shipped' ? '<div class="hint">老师已发货，线下交付后会在此标记为「交易完成」。</div>' : ''}
    ${o.status === 'rejected' && o.reject_reason ? `<div class="error-box">驳回原因：${esc(o.reject_reason)}（金币已退回）</div>` : ''}
    ${o.status === 'completed' && o.admin_note ? `<div class="hint">备注：${esc(o.admin_note)}</div>` : ''}
  </div>`).join('') : '<div class="empty">还没有订单，去兑换心仪商品吧 🛍️</div>')
  el('app').innerHTML = studentFrame(`<h3>金币商店</h3>${html}${ordersHtml}`, 'shop')
}
async function buyItem(id) {
  const r = await api('/shop/buy', 'POST', { item_id: id })
  if (!r.ok) { toast(r.data.error || '购买失败', true); return }
  toast('兑换成功，等待老师发货 📦')
  if (r.data.balance != null) setBalance(r.data.balance)
  renderShop()
}

/* ---------- 许愿池 ---------- */
async function renderWishes() {
  const pub = await api('/wishes/public')
  const mine = await api('/wishes')
  let poolHtml
  if (!pub.ok) poolHtml = `<div class="empty">${esc(pub.data.error || '')}</div>`
  else {
    const ws = pub.data.wishes || []
    poolHtml = ws.length ? ws.map(w => {
      const canSupport = w.status === 'pending'
      return `<div class="card wish-pool-card">
        <div class="spread"><b>${esc(w.content)}</b><span class="tag ${w.status === 'approved' ? 'ok' : ''}">${w.status === 'approved' ? '已实现' : '募集中'}</span></div>
        <div class="muted" style="font-size:13px">许愿人：${esc(w.student)} · 已筹 🪙 ${w.total_coins_invested} · ${w.supporters} 人助力</div>
        ${canSupport ? `<button class="btn block" style="margin-top:8px" onclick="supportWish(${w.id})">💛 助力这个愿望 🪙</button>`
          : `<div class="hint" style="margin-top:8px">该愿望已批准，感谢大家的支持 🎉</div>`}
      </div>`
    }).join('') : `<div class="empty">许愿池还空空如也，去发布第一个愿望吧 🌟</div>`
  }
  let mineHtml
  if (!mine.ok) mineHtml = ''
  else {
    const ms = mine.data.wishes || []
    const wmap = { pending: '<span class="tag">审核中</span>', approved: '<span class="tag ok">已批准·处理中</span>',
      completed: '<span class="tag ok">已完成</span>', rejected: '<span class="tag danger">已驳回·已退款</span>' }
    mineHtml = `<h3 style="margin-top:18px">我的心愿</h3>` + (ms.length ? ms.map(w => `<div class="card">
      <div class="spread"><b>${esc(w.content)}</b>${wmap[w.status] || w.status}</div>
      <div class="muted" style="font-size:13px">已投 🪙 ${w.total_coins_invested} · ${w.supporters} 人助力</div>
      ${w.admin_reply ? `<div class="hint">老师回复：${esc(w.admin_reply)}</div>` : ''}
      ${w.status === 'rejected' ? '<div class="error-box">愿望被驳回，你投入及他人助力的金币已退回。</div>' : ''}
      ${w.status === 'completed' ? '<div class="hint">愿望已实现并交付，感谢老师与同学 🎉</div>' : ''}
    </div>`).join('') : `<div class="empty">还没有发布愿望</div>`)
  }
  el('app').innerHTML = studentFrame(`<h3>🌟 许愿池</h3>
    <div class="card">
      <p class="muted" style="font-size:13px">写下你的愿望并投入金币（≥10），发布后会出现在下方许愿池，所有同学都能看见并助力。</p>
      <textarea id="wish_c" rows="2" placeholder="写下你的愿望（至少投入 10 金币）"></textarea>
      <input id="wish_coins" type="number" placeholder="投入金币数" style="margin-top:8px" />
      <button class="btn block" style="margin-top:8px" onclick="createWish()">发布愿望</button>
    </div>
    <h3 style="margin-top:18px">所有人的愿望（可助力）</h3>
    ${poolHtml}
    ${mineHtml}`, 'wishes')
}
async function createWish() {
  const content = el('wish_c').value.trim()
  const coins = parseInt(el('wish_coins').value || '0', 10)
  if (!content) { toast('请填写愿望内容', true); return }
  const r = await api('/wish/create', 'POST', { content, coins })
  if (!r.ok) { toast(r.data.error || '发布失败', true); return }
  toast('愿望已发布，等待审核')
  renderWishes()
}
async function supportWish(id) {
  const coins = parseInt(prompt('助力投入金币数：') || '0', 10)
  if (!coins || coins <= 0) return
  const r = await api('/wish/support', 'POST', { wish_id: id, coins })
  if (!r.ok) { toast(r.data.error || '助力失败', true); return }
  toast('助力成功，感谢！')
  if (r.data.balance != null) setBalance(r.data.balance)
  renderWishes()
}

/* ---------- 报表 ---------- */
function reportInnerHtml(rep) {
  const ov = rep.overview
  const acc = rep.step_accuracy || []
  const accHtml = acc.length ? acc.map(a => {
    const bars = [2, 3, 4, 5, 6, 7].map(st => {
      const v = Math.round((a['' + st] || 0) * 100)
      return `<div class="col" style="height:${Math.max(4, v)}%" title="Step${st}:${v}%"><span>S${st}</span></div>`
    }).join('')
    return `<div class="card"><div style="font-weight:600;margin-bottom:6px">${esc(a.course)}</div>
      <div class="bar">${bars}</div>
      <div class="muted" style="font-size:11px;text-align:center;margin-top:20px">正确率(%)</div></div>`
  }).join('') : '<div class="empty">暂无学习数据</div>'
  const wt = rep.wrong_top10 || []
  const wtHtml = wt.length ? wt.map(w => `<div class="card">
    <div class="sentence" style="font-size:16px">${esc(w.english)}</div>
    <div class="muted" style="font-size:14px">${esc(w.chinese)}</div>
    <div class="muted" style="font-size:12px">出错 ${w.count} 次</div>
  </div>`).join('') : '<div class="empty">暂无错题 🎉</div>'
  const cal = rep.calendar || {}
  const dates = Object.keys(cal).sort()
  const heatHtml = dates.length ? dates.map(d => {
    const lv = cal[d] >= 5 ? 3 : cal[d] >= 2 ? 2 : 1
    return `<div class="d l${lv}" title="${d}:${cal[d]}次"></div>`
  }).join('') : '<div class="empty">暂无活跃记录</div>'
  return `<h3>学习报表</h3>
    <div class="grid2">
      <div class="kpi"><div class="v">${ov.assigned_count}</div><div class="l">已分配课程</div></div>
      <div class="kpi"><div class="v">${ov.completed_count}</div><div class="l">已完成</div></div>
      <div class="kpi"><div class="v">${ov.total_study_days}</div><div class="l">学习天数</div></div>
      <div class="kpi"><div class="v">${ov.total_coins}</div><div class="l">总金币</div></div>
    </div>
    <h3 style="margin-top:16px">各 Step 正确率</h3>${accHtml}
    <h3 style="margin-top:16px">错题高频 Top${wt.length}</h3>${wtHtml}
    <h3 style="margin-top:16px">活跃日历</h3><div class="cal-heat">${heatHtml}</div>`
}

async function renderReport() {
  const u = getUser()
  const r = await api(`/reports/student/${u.id}`)
  if (!r.ok) { el('app').innerHTML = studentFrame(`<div class="empty">${esc(r.data.error || '加载失败')}</div>`); return }
  el('app').innerHTML = studentFrame(reportInnerHtml(r.data.report), 'report')
}

/* ---------- 通用工具 ---------- */
let _playingBtn = null
function playAudio(url, rate, btn) {
  if (!url) { toast('该句暂无音频', true); return }
  if (_playingBtn) return  // 防连点：上一句播放完前不再响应
  try {
    const a = new Audio(url); a.playbackRate = rate || 1
    document.querySelectorAll('.aud-btn').forEach(b => { b.disabled = true })
    if (btn) btn.textContent = '⏳ 播放中…'
    const restore = () => {
      document.querySelectorAll('.aud-btn').forEach(b => {
        b.disabled = false
        if (b.dataset.label) b.textContent = b.dataset.label
      })
      _playingBtn = null
    }
    a.onended = restore
    a.onerror = () => { toast('音频播放失败', true); restore() }
    a.play().catch(() => { toast('音频播放失败', true); restore() })
    _playingBtn = btn || true
  } catch (e) { toast('音频播放失败', true) }
}
function rateButtons(url) {
  const rates = [0.5, 0.8, 1, 1.2, 1.5]
  return rates.map(r =>
    `<button class="btn ghost sm aud-btn" data-label="${r}x" onclick="playAudio('${esc(url)}', ${r}, this)">${r}x</button>`
  ).join('')
}
function hl(eng, tws) {
  if (!eng) return ''
  let s = esc(eng)
  ;(tws || []).forEach(w => {
    if (!w) return
    const re = new RegExp('\\b' + w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b', 'gi')
    s = s.replace(re, '<span class="tw">' + w + '</span>')
  })
  return s
}
function shuffle(arr) {
  const a = arr.slice()
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));[a[i], a[j]] = [a[j], a[i]]
  }
  return a
}
