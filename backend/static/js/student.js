/* ============ 学生端页面 ============ */

/* ---------- 首页：签到 + 课程 + API Key ---------- */
async function renderHome() {
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
      const pct = Math.round(((c.completed_steps || []).length / 5) * 100)
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
  const u = getUser()
  const ci = await api('/checkin/info')
  const info = ci.ok ? ci.data : { coin: 1, require_task: true, already: false, streak: 0, did_task_today: false }
  const checkedToday = info.already
  const checkinBtn = checkedToday
    ? `<button class="big-checkin done" disabled>已签到 ✓ (+${info.coin})</button>`
    : `<button class="big-checkin" onclick="doCheckin()">📅 每日签到 (+${info.coin})</button>`
  const taskHint = info.require_task
    ? `<p class="muted" style="margin-top:8px">${info.did_task_today ? '✅ 今日已完成学习任务，可签到' : '⚠️ 需先完成至少一个学习任务（任一 Step 提交）才能签到'}</p>`
    : `<p class="muted" style="margin-top:8px">连续签到越久奖励越高</p>`

  el('app').innerHTML = studentFrame(`
    <div class="card">
      <h3>每日签到</h3>
      ${checkinBtn}
      ${taskHint}
    </div>
    <h3 style="margin-top:18px">我的课程</h3>
    ${coursesHtml}
  `, 'home')
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
  }
  drawLearn()
}

function drawLearn() {
  const total = learn.sentences.length
  const unlocks = learn.unlocks
  const stepsHtml = [0, 1, 2, 3, 4, 5].map(n => {
    if (n === 0) return `<div class="step-pill ${learn.step === 0 ? 'active' : ''}" onclick="goStep(0)">词汇</div>`
    const locked = !unlocks[String(n)]
    const cls = learn.step === n ? 'active' : (locked ? 'locked' : 'done')
    return `<div class="step-pill ${cls}" ${locked ? '' : `onclick="goStep(${n})"`}>${locked ? '🔒 ' : ''}Step ${n}</div>`
  }).join('')
  el('app').innerHTML = studentFrame(`
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
  if (n > 0 && !learn.unlocks[String(n)]) { toast('该步骤尚未解锁', true); return }
  // 进入步骤5之前，先展示全文回顾（Step5 预学），每次进入都显示
  if (n === 5 && !skipFull) { drawFullText(el('step-body')); return }
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
      ${hasAudio ? `<button class="btn ghost sm" style="margin-top:8px" onclick="playAudio('${esc(s.audio_url)}', curRate('rate1'))">🔊 再听这句</button>` : ''}
    </div>
    ${hasAudio ? `<div class="row" style="margin-top:12px">
        <button class="btn" style="flex:1;white-space:nowrap" onclick="playAudio('${esc(s.audio_url)}', curRate('rate1'))">🔊 播放</button>
        <select id="rate1" class="mini" style="flex:0 0 auto;width:88px">${rateOptions(1)}</select>
      </div>` : '<div class="muted" style="margin-top:12px">无音频</div>'}
    <button class="btn block" style="margin-top:12px" id="toggleEn1" onclick="toggleEnglish1()">隐藏原文</button>
    <div class="req-hint">学习要求：请仔细听录音，阅读英文，并看翻译。搞懂后，隐藏英文再听一遍。不看英文也能听懂后，进入下一句。</div>
    <button class="btn block" style="margin-top:12px" onclick="nextStep1()">${isLast ? '完成浏览 →' : '下一句 →'}</button>
  </div>`
}
function toggleEnglish1() {
  const e = el('en1'); const btn = el('toggleEn1')
  if (!e || !btn) return
  if (e.style.display === 'none') { e.style.display = 'block'; btn.textContent = '隐藏原文' }
  else { e.style.display = 'none'; btn.textContent = '显示英文' }
}
function nextStep1() {
  learn.idx++
  if (learn.idx >= learn.sentences.length) finishStepView(el('step-body'))
  else drawStep1(el('step-body'))
}

/* Step 2~5：答题步骤（做错反复重练，直到全部做对才解锁） */
function drawStepN(body) {
  const step = learn.step
  // 进入本步时初始化练习队列
  if (learn.queueStep !== step || !learn.queue) {
    learn.queueStep = step
    const seq = learn.sentences.map((_, i) => i)
    learn.order = (step === 3 || step === 4 || step === 5) ? shuffle(seq) : seq
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
  let promptHtml = '', inputHint = '', audioCtl = ''
  if (step === 2) {
    promptHtml = `<div class="sentence">${hl(s.english, s.target_words || [])}</div>`
    inputHint = '请输入中文翻译'
  } else if (step === 3) {
    const hasAudio = !!s.audio_url
    if (hasAudio) {
      promptHtml = `<button class="btn block" onclick="playAudio('${esc(s.audio_url)}', curRate('rate3'))">🔊 仅听音（不显示文字）</button>`
      audioCtl = `<select id="rate3" class="mini" style="margin-top:8px">${rateOptions(1)}</select>
        <div class="muted" style="font-size:12px">选择倍速后点击上方播放</div>`
    } else {
      promptHtml = `<div class="cn">（本句无音频，直接写出中文意思）</div>`
    }
    inputHint = '听音后写出中文意思'
  } else if (step === 4) {
    promptHtml = `<div class="cn">${esc(s.chinese)}</div>`
    inputHint = '请输入英文（中译英）'
  } else if (step === 5) {
    const prevIdx = learn.sentences.findIndex(x => x.sentence_order === s.sentence_order)
    const nxt = learn.sentences[prevIdx + 1]
    if (!nxt) { learn.idx++; return drawStepN(body) }
    promptHtml = `<div class="sentence">${hl(s.english, s.target_words || [])}</div>
      <p class="muted">↑ 这是上文，请写出它的<b>下一句</b>英文：</p>`
    inputHint = '请输入下一句英文'
  }
  const totalQ = learn.queue.length
  body.innerHTML = `<div class="card">
    <div class="spread">
      <span class="muted">第 ${learn.idx + 1}/${totalQ} 句 · Step ${step}${learn.passNo > 1 ? ' · 第' + learn.passNo + '轮' : ''}</span>
      <button class="btn ghost sm" onclick="backToSteps()">← 步骤</button>
    </div>
    <div style="margin-top:10px">${promptHtml}</div>
    ${audioCtl}
    <textarea id="uin" rows="2" placeholder="${inputHint}" style="margin-top:12px"></textarea>
    <div class="req-hint">${stepHint(step)}</div>
    ${(step === 2 || step === 5) ? `<div class="row" style="margin-top:12px">
      <button class="btn" style="flex:2" onclick="submitStepN(${s.id}, ${step})">提交</button>
      <button class="btn ghost" style="flex:1" onclick="skipStepN(${s.id}, ${step})">跳过看答案</button>
    </div>` : `<button class="btn block" style="margin-top:12px" onclick="submitStepN(${s.id}, ${step})">提交</button>`}
    <div id="fb"></div>
  </div>`
}

function stepHint(step) {
  if (step === 2) return '学习要求：将上面的英语句子翻译为中文。'
  if (step === 3) return '学习要求：讲听到的英语句子的中文意思写出来。如果不会了，请回到步骤1。'
  if (step === 4) return '学习要求：把上面的中文句子翻译成英文。'
  if (step === 5) return '学习要求：根据上文学写出连贯的下一句英文。'
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
  if (d.correct) { head = '✅ 正确！'; cls = 'ok' }
  else { head = '🙂 再体会一下'; cls = 'retry' }
  let extra = ''
  if (step === 4 && d.local_match) extra += `<br/><span class="muted">本地匹配 ${esc(d.local_match)}</span>`
  if (!d.correct) extra += `<br/>标准答案：${esc(std)}${d.error_type ? '<br/>提示：' + esc(d.error_type) : ''}`
  const lastOfPass = learn.idx + 1 >= learn.queue.length
  fb.innerHTML = `<div class="feedback ${cls}">${head}${extra}</div>
    <button class="btn block" style="margin-top:10px" onclick="afterStepSubmit()">${lastOfPass ? '本轮结束 →' : '下一句 →'}</button>`
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
    ? `<button class="btn ghost block" style="margin-top:10px" onclick="forceUnlock(${step})">${step < 5 ? '仍有未掌握，强制解锁下一步 →' : '仍有未掌握，强制完成课程 →'}</button>
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
  toast('已强制解锁' + (step < 5 ? '下一步' : '并完成课程'))
  // 刷新解锁状态
  const cr = await api('/courses')
  if (cr.ok) {
    const info = (cr.data.courses || []).find(c => String(c.course_id) === String(learn.courseId))
    if (info) learn.unlocks = info.step_unlocks || learn.unlocks
  }
  if (step === 5) { setTimeout(() => nav('#/'), 800); return }
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
  const nextLabel = step < 5 ? `解锁/进入步骤${step + 1}` : '完成课程'
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
  if (d.awards && d.awards.length) {
    if (d.balance != null) setBalance(d.balance)
    celebrate(d.awards.join('、'), d.balance)
  } else {
    toast('已记录进度')
  }
  // 刷新解锁状态
  const cr = await api('/courses')
  if (cr.ok) {
    const info = (cr.data.courses || []).find(c => String(c.course_id) === String(learn.courseId))
    if (info) learn.unlocks = info.step_unlocks || learn.unlocks
  }
  // 步骤5完成：记录进度后自动返回首页（课程浏览界面）
  if (step === 5) {
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
  // 步骤4完成进入步骤5时，goStep(5) 会先展示全文回顾（Step5 预学）
  if (step < 5 && learn.unlocks[String(step + 1)]) {
    goStep(step + 1)
  } else {
    drawLearn()
  }
}

/* Step 5 预学：全文回顾（中英文对照，按原文顺序） */
function drawFullText(body) {
  const ordered = learn.sentences.slice().sort((a, b) => (a.sentence_order || 0) - (b.sentence_order || 0))
  body.innerHTML = `<div class="card">
    <div class="spread">
      <h3>全文回顾 · Step 5 预学</h3>
      <button class="btn ghost sm" onclick="backToSteps()">← 步骤</button>
    </div>
    <p class="muted">进入第五步前，先按顺序通读整篇，建立整体语感。一句英文，一句中文。</p>
    ${ordered.map(s => `<div class="ft-en">${hl(s.english, s.target_words || [])}</div>
      <div class="ft-cn">${esc(s.chinese)}</div>`).join('')}
    <button class="btn block" style="margin-top:16px" onclick="goStep(5, true)">进入 Step 5 →</button>
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
    const bars = [2, 3, 4, 5].map(st => {
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
function playAudio(url, rate) {
  if (!url) { toast('该句暂无音频', true); return }
  try {
    const a = new Audio(url); a.playbackRate = rate || 1; a.play().catch(() => toast('音频播放失败', true))
  } catch (e) { toast('音频播放失败', true) }
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
