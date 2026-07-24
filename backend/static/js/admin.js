/* ============ 管理员端页面 ============ */
let adminCourse = { courseId: null, folder: '' }

function renderAdmin(tab) {
  const parts = (location.hash.slice(1) || '/').split('/').filter(Boolean)
  tab = tab || parts[1] || 'courses'
  const param = parts[2]
  let inner = ''
  // 各板块对应的父级 Tab（用于高亮）
  const MAIN = ['courses', 'words', 'rewards', 'students']
  const parentOf = {
    coins: 'rewards', shop: 'rewards', wishes: 'rewards',
    report: 'courses', db: 'courses', api: 'courses', account: 'courses', settings: 'courses',
  }
  const parentTab = MAIN.includes(tab) ? tab : (parentOf[tab] || 'courses')

  if (tab === 'courses') {
    // 听说管理：课程管理 + 系统子工具
    inner = adminCoursesInner() + `
      <div class="card" style="margin-top:16px">
        <h3>系统工具</h3>
        <p class="muted" style="font-size:13px">报表、数据库、API 分享、账号与设置等系统功能（归属听说大师相关）。</p>
        <div class="row" style="flex-wrap:wrap;gap:8px">
          <a class="btn ghost sm" href="#/admin/report">📊 报表</a>
          <a class="btn ghost sm" href="#/admin/db">🗄️ 数据库</a>
          <a class="btn ghost sm" href="#/admin/api">🔑 API分享</a>
          <a class="btn ghost sm" href="#/admin/account">👤 账号</a>
          <a class="btn ghost sm" href="#/admin/settings">⚙️ 设置</a>
        </div>
      </div>`
  }
  else if (tab === 'words') {
    // 单词管理：跳转单词大师后台（服务端页面）
    inner = `<div class="card">
      <h3>单词管理</h3>
      <p class="muted">单词大师使用独立的后台页面，点击进入后可管理词库、导入单词、配置考试与免错券等。</p>
      <div class="row" style="flex-wrap:wrap;gap:10px;margin-top:10px">
        <a class="btn" href="/admin">📚 词库 / 导入 / 考试管理</a>
        <a class="btn ghost" href="/admin/exam">📝 考试配置</a>
      </div>
      <p class="hint" style="margin-top:12px">提示：单词大师后台与英语大师共用同一账号体系与金币 / 商城 / 许愿池。</p>
    </div>`
  }
  else if (tab === 'rewards') {
    // 奖励管理：金币流水 / 商店 / 许愿池
    const sub = param || 'coins'
    const subTabs = adminSubTabs([
      ['#/admin/rewards/coins', '金币流水'],
      ['#/admin/rewards/shop', '商店'],
      ['#/admin/rewards/wishes', '许愿池'],
    ], '#/admin/rewards/' + sub)
    inner = subTabs + `<div id="tab-body"><div class="empty">加载中…</div></div>`
  }
  else if (tab === 'students') inner = '<div id="tab-body"><div class="empty">加载中…</div></div>'
  else if (tab === 'coins') inner = '<div id="tab-body"><div class="empty">加载中…</div></div>'
  else if (tab === 'api') inner = '<div id="tab-body"><div class="empty">加载中…</div></div>'
  else if (tab === 'shop') inner = '<div id="tab-body"><div class="empty">加载中…</div></div>'
  else if (tab === 'wishes') inner = '<div id="tab-body"><div class="empty">加载中…</div></div>'
  else if (tab === 'db') inner = adminDbInner()
  else if (tab === 'report') inner = '<div id="tab-body"></div>'
  else if (tab === 'account') inner = adminAccountInner()
  else if (tab === 'settings') inner = '<div id="tab-body"><div class="empty">加载中…</div></div>'
  else inner = adminCoursesInner()

  el('app').innerHTML = adminFrame(inner, parentTab)

  if (tab === 'courses') loadCourseList()
  else if (tab === 'rewards') {
    if (param === 'shop') loadShopTab()
    else if (param === 'wishes') loadWishesTab()
    else loadAdminCoins()
  }
  else if (tab === 'students') loadStudents()
  else if (tab === 'coins') loadAdminCoins()
  else if (tab === 'api') loadApiTab()
  else if (tab === 'shop') loadShopTab()
  else if (tab === 'wishes') loadWishesTab()
  else if (tab === 'settings') loadSettingsTab()
  else if (tab === 'report') renderAdminReport(param)
}

/* ---------- 课程管理（两步上传） ---------- */
function adminCoursesInner() {
  return `<div class="card">
    <h3>① 上传课程 JSON</h3>
    <p class="muted" style="font-size:13px">支持单篇 {"title":...,"sentences":[...]}，也可直接粘贴含多篇的 JSON 数组；
    允许 LLM 输出的 <code>\`\`\`json ... \`\`\`</code> 栅栏文本。</p>
    <input type="file" id="courseFile" accept=".json,.csv" />
    <p class="muted" style="font-size:12px;margin-top:6px">— 或粘贴 JSON —</p>
    <textarea id="courseText" rows="4" placeholder='粘贴 JSON：{"article_id":50,"title":"...","full_text":"...","sentences":[{"sentence_id":1,"english":"...","chinese":"...","target_words":["word"]}]}'></textarea>
    <div class="row" style="margin-top:8px">
      <button class="btn" onclick="uploadCourseJson()">解析并创建课程</button>
      <button class="btn ghost" onclick="downloadTemplate()">下载课程模板</button>
    </div>
    <p class="hint">字段：article_id, title, full_text, sentences[{sentence_id, english, chinese, target_words}]。音频稍后可在课程列表中逐课「补充音频」。</p>
    <div id="uploadStatus"></div>
  </div>
  <div class="card">
    <div class="spread">
      <h3>课程管理列表</h3>
      <button class="btn" onclick="openBatchAssign()">📦 批量推送课程</button>
    </div>
    <p class="muted" style="font-size:13px">解析后课程即进入列表，可在此发布 / 撤销 / 分配 / 补充音频 / 检查错误 / 编辑 / 删除。一次给多名学生推送多门课程，请用右上角「批量推送」。</p>
    <div id="courseList"><div class="empty">加载中…</div></div>
  </div>`
}
/* 批量推送：一次选多门课程 + 多名学生 */
async function openBatchAssign() {
  const [cr, sr] = await Promise.all([api('/admin/courses'), api('/admin/students')])
  if (!cr.ok || !sr.ok) { toast('加载失败', true); return }
  const courses = (cr.data.courses || []).filter(c => c.is_published)
  const students = sr.data.students || []
  if (!courses.length) { toast('暂无已发布课程，请先发布', true); return }
  const courseRows = courses.map(c => `<label style="display:block;padding:5px 0"><input type="checkbox" class="ba-course" value="${c.id}" style="width:auto;margin-right:8px"/> #${c.id} ${esc(c.title)}（${c.sentence_count}句）</label>`).join('')
  const studentRows = students.map(s => `<label style="display:block;padding:5px 0"><input type="checkbox" class="ba-student" value="${s.id}" style="width:auto;margin-right:8px"/> ${esc(s.username)}</label>`).join('')
  modal(`<h3>📦 批量推送课程</h3>
    <div class="card" style="margin-bottom:10px">
      <div style="font-weight:600;margin-bottom:6px">学习模式</div>
      <label style="display:block;padding:4px 0"><input type="radio" name="ba_mode" value="free" checked style="width:auto;margin-right:8px"/> 解锁推送（自由学习）</label>
      <label style="display:block;padding:4px 0"><input type="radio" name="ba_mode" value="locked" style="width:auto;margin-right:8px"/> 加锁推送（解锁式学习：完成一门解锁下一门）</label>
    </div>
    <div class="spread"><b>选择课程</b><button class="btn ghost sm" onclick="baToggleAll('ba-course')">全选/取消</button></div>
    <div style="max-height:28vh;overflow:auto;border:1px solid var(--border);border-radius:8px;padding:6px;margin:6px 0">${courseRows}</div>
    <div class="spread"><b>选择学生</b><button class="btn ghost sm" onclick="baToggleAll('ba-student')">全选/取消</button></div>
    <div style="max-height:28vh;overflow:auto;border:1px solid var(--border);border-radius:8px;padding:6px;margin:6px 0">${studentRows || '<div class="muted">暂无学生</div>'}</div>
    <div class="row"><button class="btn ghost" onclick="closeModal()">取消</button>
    <button class="btn" onclick="doBatchAssign()">推送</button></div>`)
}
function baToggleAll(cls) {
  const boxes = [...document.querySelectorAll('#modal-root .' + cls)]
  const target = !boxes.every(b => b.checked)
  boxes.forEach(b => { b.checked = target })
}
async function doBatchAssign() {
  const course_ids = [...document.querySelectorAll('#modal-root .ba-course:checked')].map(b => +b.value)
  const student_ids = [...document.querySelectorAll('#modal-root .ba-student:checked')].map(b => +b.value)
  if (!course_ids.length) { toast('请选择至少一门课程', true); return }
  if (!student_ids.length) { toast('请选择至少一名学生', true); return }
  const modeEl = document.querySelector('#modal-root input[name=ba_mode]:checked')
  const unlock_mode = modeEl ? modeEl.value : 'free'
  const r = await api('/admin/assign-courses-batch', 'POST', { course_ids, student_ids, unlock_mode })
  if (!r.ok) { toast(r.data.error || '推送失败', true); return }
  toast(r.data.message); closeModal()
}
async function uploadCourseJson() {
  const f = el('courseFile').files[0]
  const text = (el('courseText').value || '').trim()
  const status = el('uploadStatus')
  status.innerHTML = '上传中…'
  let r
  if (f) {
    const fd = new FormData()
    fd.append('file', f)
    const res = await fetch(API_BASE + '/admin/upload-course', {
      method: 'POST', headers: { Authorization: 'Bearer ' + getToken() }, body: fd,
    })
    r = { ok: res.ok, data: await res.json().catch(() => ({})) }
  } else if (text) {
    r = await api('/admin/upload-course', 'POST', { raw_text: text })
  } else {
    status.textContent = '请先选择文件或粘贴 JSON'; return
  }
  if (!r.ok) {
    const d = r.data || {}
    let msg = '失败：' + (d.error || JSON.stringify(d))
    if (d.hint) msg += '<br/><span class="hint">提示：' + esc(d.hint) + '</span>'
    if (d.skipped && d.skipped.length) {
      msg += '<br/>跳过：' + d.skipped.map(s => `第${s.index}篇(${s.reason})`).join('；')
    }
    status.innerHTML = msg
    return
  }
  let ok = `✅ 已创建 ${r.data.created_count} 门课程`
  if (r.data.titles && r.data.titles.length) ok += '：' + r.data.titles.map(esc).join('、')
  if (r.data.skipped && r.data.skipped.length)
    ok += '<br/>⚠️ 跳过 ' + r.data.skipped.length + ' 篇：' + r.data.skipped.map(s => `第${s.index}篇(${s.reason})`).join('；')
  status.innerHTML = ok
  loadCourseList()
}
function downloadTemplate() {
  const tpl = {
    article_id: 50,
    title: 'The Beautiful World',
    full_text: 'Our world is full of wonderful places.',
    sentences: [
      { sentence_id: 1, english: 'Our world is full of wonderful places.', chinese: '我们的世界充满了奇妙的地方。', target_words: ['world', 'wonderful'] },
      { sentence_id: 2, english: 'There are high mountains and deep oceans.', chinese: '有高山和深海。', target_words: ['mountain', 'ocean'] }
    ]
  }
  const blob = new Blob([JSON.stringify(tpl, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = 'course_template.json'
  a.click()
  URL.revokeObjectURL(a.href)
  toast('已下载课程模板')
}
async function loadCourseList() {
  const r = await api('/admin/courses')
  const box = el('courseList')
  if (!box) return
  if (!r.ok) { box.innerHTML = `<div class="empty">${esc(r.data.error || '')}</div>`; return }
  const cs = r.data.courses || []
  if (!cs.length) { box.innerHTML = '<div class="empty">还没有课程，先在上方解析 JSON 创建</div>'; return }
  box.innerHTML = `<div class="tablewrap"><table class="tbl">
    <tr><th>ID</th><th>标题</th><th>句子</th><th>音频</th><th>状态</th><th>操作</th></tr>
    ${cs.map(c => {
      const audioTxt = `${c.audio_count}/${c.sentence_count}`
      const errMark = c.has_error ? ' ⚠️缺字段' : (c.missing_audio.length ? ' ⚠️缺音频' : ' ✅')
      return `<tr>
        <td>${c.id}</td>
        <td>${esc(c.title)}</td>
        <td>${c.sentence_count}</td>
        <td>${audioTxt}${errMark}</td>
        <td>${c.is_published ? '<span class="tag ok">已发布</span>' : '<span class="tag">未发布</span>'}</td>
        <td class="ops">
          ${c.is_published ? `<button class="btn ghost sm" onclick="unpublishCourse(${c.id})">撤销</button>`
            : `<button class="btn ok sm" onclick="publishCourse(${c.id})">发布</button>`}
          <button class="btn ghost sm" onclick="openAssign(${c.id})">分配</button>
          <button class="btn ghost sm" onclick="openAudioUpload(${c.id})">补充音频</button>
          <button class="btn ghost sm" onclick="openCheckErrors(${c.id})">检查错误</button>
          <button class="btn ghost sm" onclick="openEditCourse(${c.id})">编辑</button>
          <button class="btn danger sm" onclick="deleteCourse(${c.id}, '${esc(c.title)}')">删除</button>
        </td>
      </tr>`
    }).join('')}
  </table></div>`
}
async function unpublishCourse(id) {
  const r = await api('/admin/unpublish-course', 'POST', { course_id: id })
  if (!r.ok) { toast(r.data.error || '撤销失败', true); return }
  toast('已撤销发布'); loadCourseList()
}
async function deleteCourse(id, title) {
  if (!confirm(`确定删除课程「${title}」吗？\n该课程及其所有句子将被永久删除，且不可恢复！`)) return
  const r = await api('/admin/course/' + id, 'DELETE')
  if (!r.ok) { toast(r.data.error || '删除失败', true); return }
  toast('课程已删除'); loadCourseList()
}
async function openAudioUpload(courseId) {
  modal(`<h3>补充音频 · 课程 #${courseId}</h3>
    <p class="muted" style="font-size:13px">文件名数字 = 句子序号（1.mp3 → 第1句）。支持 .mp3 / .wav，可多选。</p>
    <input type="file" id="audioFiles" multiple accept=".mp3,.wav" />
    <div id="audioStatus"></div>
    <div class="row" style="margin-top:10px">
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn" onclick="submitAudioUpload(${courseId})">上传音频</button>
    </div>`)
}
async function submitAudioUpload(courseId) {
  const f = el('audioFiles').files
  if (!f.length) { toast('请选择音频文件', true); return }
  const fd = new FormData()
  fd.append('course_id', String(courseId))
  for (const file of f) fd.append('audio', file)
  const res = await fetch(API_BASE + '/admin/upload-audio', {
    method: 'POST', headers: { Authorization: 'Bearer ' + getToken() }, body: fd,
  })
  const d = await res.json().catch(() => ({}))
  if (!res.ok) { el('audioStatus').textContent = '失败：' + (d.error || ''); return }
  const lines = (d.results || []).map(x => `${x.file}: ${x.status === 'ok' ? '✅' + (x.url || '') : '⚠️' + (x.reason || '')}`).join('<br/>')
  el('audioStatus').innerHTML = `完成：${esc(d.message)}<br/>${lines}`
  loadCourseList()
}
async function openCheckErrors(courseId) {
  const r = await api('/admin/course/' + courseId + '/errors')
  if (!r.ok) { toast(r.data.error || '查询失败', true); return }
  const d = r.data
  let msg = `<h3>检查错误 · #${d.course_id} ${esc(d.title)}</h3>`
  msg += `<p class="muted">共 ${d.total} 句</p>`
  msg += d.missing_audio.length ? `<p>缺音频的句子序号：${d.missing_audio.join('、')}</p>` : '<p>✅ 音频齐全</p>'
  msg += d.missing_fields.length ? `<p class="error-box">缺字段(english/chinese)的句子序号：${d.missing_fields.join('、')}</p>` : '<p>✅ 字段完整</p>'
  modal(msg + `<div class="row" style="margin-top:10px"><button class="btn" onclick="closeModal()">关闭</button></div>`)
}
async function openEditCourse(courseId) {
  const c = await api('/admin/courses')
  const course = (c.ok ? c.data.courses : []).find(x => x.id === courseId)
  const title = course ? course.title : ''
  modal(`<h3>编辑课程 #${courseId}</h3>
    <input id="ed_title" value="${esc(title)}" placeholder="课程标题" style="margin-bottom:8px" />
    <p class="muted" style="font-size:13px">修改标题后保存；句子内容修订可重新上传 JSON（覆盖该课）或通过「补充音频」补充音频。</p>
    <div class="row">
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn" onclick="saveEditCourse(${courseId})">保存</button>
    </div>`)
}
async function saveEditCourse(courseId) {
  const title = el('ed_title').value.trim()
  if (!title) { toast('标题不能为空', true); return }
  const r = await api('/admin/update-course', 'POST', { course_id: courseId, title })
  if (!r.ok) { toast(r.data.error || '保存失败', true); return }
  toast('已保存'); closeModal(); loadCourseList()
}
async function publishCourse(id) {
  const r = await api('/admin/publish-course', 'POST', { course_id: id })
  if (!r.ok) { toast(r.data.error || '发布失败', true); return }
  toast('课程已发布，可分配给学员')
  loadCourseList()
}
function openAssign(courseId) {
  api('/admin/students').then(async r => {
    if (!r.ok) { toast(r.data.error || '', true); return }
    const students = r.data.students
    const rows = students.map(s => `<label style="display:block;padding:6px 0"><input type="checkbox" value="${s.id}" style="width:auto;margin-right:8px"/> ${esc(s.username)}</label>`).join('')
    modal(`<h3>分配课程</h3>
      <div class="card" style="margin-bottom:10px">
        <div style="font-weight:600;margin-bottom:6px">学习模式</div>
        <label style="display:block;padding:4px 0"><input type="radio" name="assign_mode" value="free" checked style="width:auto;margin-right:8px"/> 解锁推送（自由学习）：学生可自由学习所有已分配课程</label>
        <label style="display:block;padding:4px 0"><input type="radio" name="assign_mode" value="locked" style="width:auto;margin-right:8px"/> 加锁推送（解锁式学习）：完成一门才解锁下一门</label>
      </div>
      <div style="max-height:50vh;overflow:auto">${rows || '<div class="muted">暂无学生</div>'}</div>
      <div class="row"><button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn" onclick="doAssign(${courseId})">分配</button></div>`)
  })
}
async function doAssign(courseId) {
  const boxes = document.querySelectorAll('#modal-root input[type=checkbox]:checked')
  const ids = [...boxes].map(b => +b.value)
  if (!ids.length) { toast('请选择至少一名学生', true); return }
  const modeEl = document.querySelector('#modal-root input[name=assign_mode]:checked')
  const unlock_mode = modeEl ? modeEl.value : 'free'
  const r = await api('/admin/assign-course', 'POST', { course_id: courseId, student_ids: ids, unlock_mode })
  if (!r.ok) { toast(r.data.error || '分配失败', true); return }
  toast(r.data.message); closeModal()
}

/* ---------- 学员管理 ---------- */
async function loadStudents() {
  const r = await api('/admin/students')
  const box = el('tab-body')
  if (!box) return
  if (!r.ok) { box.innerHTML = `<div class="empty">${esc(r.data.error || '')}</div>`; return }
  const st = r.data.students || []
  box.innerHTML = st.length ? `<div class="tablewrap"><table class="tbl">
    <tr><th>ID</th><th>用户名</th><th>金币</th><th>连签</th><th>共享Key</th><th>允许跳过</th><th>操作</th></tr>
    ${st.map(s => `<tr>
      <td>${s.id}</td><td>${esc(s.username)}</td><td>${s.coin_balance}</td><td>${s.daily_streak}</td>
      <td>${s.has_shared_key ? '✅' : '—'}</td>
      <td>
        <button class="btn ${s.allow_skip ? 'ok' : 'ghost'} sm" onclick="toggleAllowSkip(${s.id}, ${s.allow_skip ? 0 : 1})">${s.allow_skip ? '✅ 已开启' : '关闭'}</button>
      </td>
      <td>
        <button class="btn ghost sm" onclick="resetPw(${s.id})">改密</button>
        <button class="btn ghost sm" onclick="adjustCoins(${s.id})">金币</button>
        <a class="btn ghost sm" href="#/admin/report/${s.id}">报表</a>
        <button class="btn danger sm" onclick="deleteStudent(${s.id}, '${esc(s.username)}')">删除</button>
      </td></tr>`).join('')}
  </table></div>
  <p class="hint">「允许跳过」开启后，该生做完一轮仍有未通过句时，可在学习页强制解锁下一步（不发金币）。</p>` : '<div class="empty">暂无学生</div>'
}
async function toggleAllowSkip(id, allow) {
  const r = await api('/admin/set-allow-skip', 'POST', { student_id: id, allow_skip: !!allow })
  if (!r.ok) { toast(r.data.error || '设置失败', true); return }
  toast(r.data.message); loadStudents()
}
async function resetPw(id) {
  const pw = prompt('输入新密码（至少6位）：')
  if (!pw || pw.length < 6) { if (pw) toast('密码至少6位', true); return }
  const r = await api('/admin/reset-password', 'POST', { student_id: id, new_password: pw })
  toast(r.ok ? '密码已重置' : (r.data.error || '失败'))
}
async function adjustCoins(id) {
  const amt = parseInt(prompt('调整金币（正为加，负为减）：') || '0', 10)
  if (!amt) return
  const reason = prompt('调整原因：') || '管理员调整'
  const r = await api('/admin/adjust-coins', 'POST', { student_id: id, amount: amt, reason })
  toast(r.ok ? `已调整，余额 ${r.data.balance}` : (r.data.error || '失败'))
  loadStudents()
}
async function deleteStudent(id, username) {
  if (!confirm(`确定删除学员「${username}」吗？\n该操作将同时删除其学习进度、错题、金币流水、订单与愿望，且不可恢复！`)) return
  const pwd = prompt('为防止误操作，请再次输入你的管理员密码以确认删除：')
  if (!pwd) { toast('已取消删除', true); return }
  const r = await api('/admin/delete-student', 'POST', { student_id: id, admin_password: pwd })
  if (!r.ok) { toast(r.data.error || '删除失败', true); return }
  toast(r.data.message)
  loadStudents()
}

/* ---------- API 分享 ---------- */
async function loadApiTab() {
  const r = await api('/admin/share-keys')
  const st = await api('/admin/students')
  const box = el('tab-body')
  if (!box) return
  const keys = r.ok ? r.data.keys : []
  const students = st.ok ? st.data.students : []
  const keyOpts = keys.map(k => `<option value="${k.id}">Key#${k.id} ${k.masked} ${k.is_active ? '' : '(停用)'}</option>`).join('')
  const shareRows = students.map(s => `<div class="card course-card">
    <div>${esc(s.username)} ${s.has_shared_key ? '<span class="tag">已用共享Key</span>' : ''}</div>
    <div class="row">
      <select id="shk_${s.id}" style="width:auto">
        <option value="">（用自己的Key）</option>${keyOpts}
      </select>
      <button class="btn ghost sm" onclick="setShare(${s.id})">保存</button>
    </div>
  </div>`).join('')
  box.innerHTML = `<div class="card">
    <h3>添加分享 Key</h3>
    <input id="newKey" placeholder="sk-..." />
    <button class="btn block" style="margin-top:8px" onclick="addShareKey()">添加</button>
    <p class="hint">学生使用共享 Key 时，自己的私有 Key 自动失效。</p>
  </div>
  <div class="card">
    <h3>已创建的 Key</h3>
    ${keys.length ? keys.map(k => `<div class="tag">#${k.id} ${k.masked} ${k.is_active ? '✅' : '⏸'}</div>`).join('') : '<div class="muted">还没有</div>'}
  </div>
  <h3>为学员指定共享 Key</h3>
  ${shareRows || '<div class="empty">暂无学生</div>'}`
}
async function addShareKey() {
  const v = el('newKey').value.trim()
  if (!v) { toast('请输入 Key', true); return }
  const r = await api('/admin/share-key', 'POST', { api_key_value: v })
  if (!r.ok) { toast(r.data.error || '失败', true); return }
  toast('已添加'); loadApiTab()
}
async function setShare(id) {
  const sel = el('shk_' + id)
  const keyId = sel.value ? parseInt(sel.value, 10) : null
  const r = await api('/admin/set-share', 'POST', { student_id: id, share_key_id: keyId })
  toast(r.ok ? (keyId ? '已启用共享Key' : '已取消共享Key') : (r.data.error || '失败'))
  loadApiTab()
}

/* ---------- 商店管理 ---------- */
async function loadShopTab() {
  const items = await api('/admin/shop-items')
  const orders = await api('/admin/orders')
  const box = el('tab-body')
  if (!box) return
  const it = items.ok ? items.data.items : []
  const od = orders.ok ? orders.data.orders : []
  box.innerHTML = `<div class="card">
    <h3>新增 / 编辑商品</h3>
    <input id="si_name" placeholder="商品名称" style="margin-bottom:8px" />
    <input id="si_desc" placeholder="描述" style="margin-bottom:8px" />
    <input id="si_price" type="number" placeholder="价格(金币)" style="margin-bottom:8px" />
    <input id="si_stock" type="number" placeholder="库存(-1=无限)" style="margin-bottom:8px" />
    <label class="row" style="margin-bottom:8px"><input type="checkbox" id="si_on" checked style="width:auto" /> 上架</label>
    <button class="btn block" onclick="saveShopItem()">保存商品</button>
  </div>
  <div class="card">
    <h3>全部商品</h3>
    ${it.length ? it.map(i => `<div class="card course-card"><div>
      <div style="font-weight:600">${esc(i.name)} ${i.is_on_shelf ? '<span class="tag ok">在售</span>' : '<span class="tag">已下架</span>'}</div>
      <div class="muted" style="font-size:13px">🪙${i.price_coins} · 库存${i.stock < 0 ? '∞' : i.stock}</div></div>
      <div class="row">
        <button class="btn ghost sm" onclick="toggleShelf(${i.id})">${i.is_on_shelf ? '下架' : '上架'}</button>
      </div></div>`).join('') : '<div class="muted">暂无商品</div>'}
  </div>
  <div class="card">
    <h3>订单（${od.length}）</h3>
    <div class="tablewrap"><table class="tbl">
      <tr><th>学生</th><th>商品</th><th>状态</th><th>操作</th></tr>
      ${od.map(o => `<tr><td>${esc(o.student)}</td><td>${esc(o.item)}</td>
        <td>${orderStatusLabel(o)}</td>
        <td class="ops">${orderOps(o)}</td></tr>`).join('')}
    </table></div>
  </div>`
}
function orderStatusLabel(o) {
  const map = { pending: '<span class="tag">待发货</span>', shipped: '<span class="tag warn">已发货</span>',
    completed: '<span class="tag ok">交易完成</span>', rejected: '<span class="tag danger">已驳回</span>' }
  let s = map[o.status] || o.status
  if (o.status === 'rejected' && o.reject_reason) s += `<div class="muted" style="font-size:11px">原因：${esc(o.reject_reason)}</div>`
  if (o.status === 'completed' && o.admin_note) s += `<div class="muted" style="font-size:11px">${esc(o.admin_note)}</div>`
  return s
}
function orderOps(o) {
  if (o.status === 'pending') return `<button class="btn ghost sm" onclick="shipOrder(${o.id})">发货</button>`
  if (o.status === 'shipped') return `<button class="btn ok sm" onclick="archiveOrder(${o.id})">存档完成</button>`
  if (o.status === 'pending' || o.status === 'shipped')
    return `<button class="btn danger sm" onclick="rejectOrder(${o.id})">驳回退款</button>`
  return '<span class="muted">—</span>'
}
async function saveShopItem() {
  const name = el('si_name').value.trim()
  const price = parseInt(el('si_price').value || '0', 10)
  if (!name || !price) { toast('请填写名称和价格', true); return }
  const r = await api('/admin/shop-item', 'POST', {
    name, description: el('si_desc').value.trim(),
    price_coins: price, stock: parseInt(el('si_stock').value || '-1', 10),
    is_on_shelf: el('si_on').checked,
  })
  if (!r.ok) { toast(r.data.error || '失败', true); return }
  toast('商品已保存'); loadShopTab()
}
async function toggleShelf(id) {
  const r = await api('/admin/toggle-shelf', 'POST', { item_id: id })
  if (!r.ok) { toast(r.data.error || '失败', true); return }
  toast(r.data.message); loadShopTab()
}
async function shipOrder(id) {
  const note = prompt('发货备注（选填）：') || ''
  const r = await api('/admin/ship-order', 'POST', { order_id: id, admin_note: note })
  toast(r.ok ? '已发货' : (r.data.error || '失败')); loadShopTab()
}
async function archiveOrder(id) {
  if (!confirm('线下已交付给学生？存档后该订单标记为「交易完成」。')) return
  const r = await api('/admin/archive-order', 'POST', { order_id: id })
  toast(r.ok ? '已存档，交易完成' : (r.data.error || '失败')); loadShopTab()
}
async function rejectOrder(id) {
  const reason = prompt('驳回原因（会退还金币给学生）：') || ''
  if (!reason) { toast('请填写驳回原因', true); return }
  const r = await api('/admin/reject-order', 'POST', { order_id: id, reason })
  toast(r.ok ? '已驳回，金币已退回' : (r.data.error || '失败')); loadShopTab()
}

/* ---------- 许愿池管理 ---------- */
async function loadWishesTab() {
  const r = await api('/wishes')
  const box = el('tab-body')
  if (!box) return
  if (!r.ok) { box.innerHTML = `<div class="empty">${esc(r.data.error || '')}</div>`; return }
  const ws = r.data.wishes || []
  const map = { pending: '<span class="tag">审核中</span>', approved: '<span class="tag ok">已批准·处理中</span>',
    completed: '<span class="tag ok">已完成</span>', rejected: '<span class="tag danger">已驳回</span>' }
  box.innerHTML = ws.length ? ws.map(w => `<div class="card">
    <div class="spread"><b>${esc(w.content)}</b>${map[w.status] || w.status}</div>
    <div class="muted" style="font-size:13px">${esc(w.student || '')} · 已筹 🪙${w.total_coins_invested} · ${w.supporters}人助力</div>
    ${w.admin_reply ? `<div class="hint">回复：${esc(w.admin_reply)}</div>` : ''}
    ${w.status === 'pending' ? `<div class="row" style="margin-top:8px">
      <button class="btn ok sm" onclick="processWish(${w.id},'approve')">批准</button>
      <button class="btn danger sm" onclick="processWish(${w.id},'reject')">驳回</button>
    </div>` : ''}
    ${w.status === 'approved' ? `<div class="row" style="margin-top:8px">
      <button class="btn ok sm" onclick="processWish(${w.id},'complete')">完成归档</button>
      <button class="btn danger sm" onclick="processWish(${w.id},'reject')">驳回退款</button>
    </div>` : ''}
  </div>`).join('') : '<div class="empty">暂无愿望</div>'
}
async function processWish(id, action) {
  let reply = prompt('管理员回复（选填）：') || ''
  if (action === 'complete' && !confirm('线下已交付给学生？归档后该愿望标记为「已完成」。')) return
  if (action === 'reject' && !reply) reply = prompt('驳回原因（会退回所有投入的金币）：') || ''
  if (action === 'reject' && !reply) { toast('请填写驳回原因', true); return }
  const r = await api('/admin/wish/process', 'POST', { wish_id: id, action, reply })
  toast(r.ok ? '已处理' : (r.data.error || '失败')); loadWishesTab()
}

/* ---------- 数据库只读 ---------- */
function adminDbInner() {
  const tables = ['users', 'admin_share_keys', 'courses', 'sentences', 'course_assignments',
    'student_sentence_progress', 'wrong_answers', 'coin_transactions', 'shop_items',
    'purchase_orders', 'wishes', 'wish_supports']
  const opts = tables.map(t => `<option value="${t}">${t}</option>`).join('')
  return `<div class="card">
    <h3>数据库浏览（只读，前 100 条）</h3>
    <select id="dbTable" onchange="loadDbView()">${opts}</select>
    <div id="dbBody" style="margin-top:12px"></div>
  </div>`
}
async function loadDbView() {
  const t = el('dbTable').value
  const r = await api('/admin/db-view?table=' + encodeURIComponent(t))
  const box = el('dbBody')
  if (!box) return
  if (!r.ok) { box.innerHTML = `<div class="empty">${esc(r.data.error || '')}</div>`; return }
  const cols = r.data.columns || []
  const rows = r.data.rows || []
  box.innerHTML = `<div class="tablewrap"><table class="tbl">
    <tr>${cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr>
    ${rows.map(row => `<tr>${cols.map(c => `<td>${esc(row[c])}</td>`).join('')}</tr>`).join('')}
  </table></div><div class="muted">共 ${rows.length} 条</div>`
}

/* ---------- 报表（管理员查看任意学员） ---------- */
function renderAdminReport(studentId) {
  api('/admin/students').then(async r => {
    const students = r.ok ? r.data.students : []
    const sel = students.map(s => `<option value="${s.id}" ${String(s.id) === String(studentId) ? 'selected' : ''}>${esc(s.username)}</option>`).join('')
    el('tab-body').innerHTML = `<div class="card">
      <h3>学员报表</h3>
      <select id="repStu" onchange="nav('#/admin/report/'+this.value)" style="margin-bottom:8px">${sel}</select>
      <div id="repBody"><div class="empty">加载中…</div></div>
    </div>`
    if (studentId) loadAdminReport(studentId)
  })
}
async function loadAdminReport(id) {
  const r = await api('/reports/student/' + id)
  const box = el('repBody')
  if (!box) return
  if (!r.ok) { box.innerHTML = `<div class="empty">${esc(r.data.error || '')}</div>`; return }
  box.innerHTML = reportInnerHtml(r.data.report)
}

/* ---------- 账号 ---------- */
function adminAccountInner() {
  const u = getUser() || {}
  return `<div class="card">
    <h3>管理员账号</h3>
    <p>用户名：<b>${esc(u.username)}</b></p>
    <p class="muted">角色：${esc(u.role)}</p>
    <button class="btn" onclick="openChangePassword()">修改密码</button>
  </div>`
}

/* ---------- 系统设置（签到 / 金币） ---------- */
async function loadSettingsTab() {
  const r = await api('/admin/settings')
  const box = el('tab-body')
  if (!box) return
  if (!r.ok) { box.innerHTML = `<div class="empty">${esc(r.data.error || '')}</div>`; return }
  const s = r.data.settings || {}
  box.innerHTML = `<div class="card">
    <h3>签到与金币设置</h3>
    <label class="field"><span>每日签到金币</span>
      <input id="set_coin" type="number" value="${s.checkin_coin ?? 1}" /></label>
    <label class="field"><span>签到前须先完成至少一个学习任务</span>
      <input id="set_reqtask" type="checkbox" ${s.checkin_require_task ? 'checked' : ''} style="width:auto" /></label>
    <label class="field"><span>连续签到每日奖励（0=关闭，由管理员设定）</span>
      <input id="set_perday" type="number" value="${s.streak_bonus_per_day ?? 0}" /></label>
    <label class="field"><span>连续签到奖励封顶天数</span>
      <input id="set_cap" type="number" value="${s.streak_bonus_cap ?? 10}" /></label>
    <p class="hint">连续签到奖励 = 每日奖励 × min(连续天数, 封顶天数)，仅在连续 ≥2 天且每日奖励&gt;0 时发放。</p>
    <button class="btn block" style="margin-top:8px" onclick="saveSettings()">保存设置</button>
  </div>`
}
async function saveSettings() {
  const payload = {
    checkin_coin: parseInt(el('set_coin').value || '1', 10),
    checkin_require_task: el('set_reqtask').checked,
    streak_bonus_per_day: parseInt(el('set_perday').value || '0', 10),
    streak_bonus_cap: parseInt(el('set_cap').value || '10', 10),
  }
  const r = await api('/admin/settings', 'POST', payload)
  if (!r.ok) { toast(r.data.error || '保存失败', true); return }
  toast('设置已保存')
  loadSettingsTab()
}

/* ---------- 金币流水（管理员） ---------- */
const COIN_CAT_LABEL = {
  checkin: '签到', study: '学习', reward: '奖励', penalty: '扣减',
  shop: '购物', wish: '许愿', support: '助力', refund: '退款',
}
async function loadAdminCoins() {
  const r = await api('/admin/coin-transactions')
  const box = el('tab-body')
  if (!box) return
  if (!r.ok) { box.innerHTML = `<div class="empty">${esc(r.data.error || '')}</div>`; return }
  const txns = r.data.transactions || []
  const sum = (txns.reduce((a, t) => a + (t.amount || 0), 0))
  box.innerHTML = `<div class="card">
    <h3>金币发放 / 扣除流水</h3>
    <p class="muted" style="font-size:13px">所有学员的金币变动记录（含管理员奖励/扣减、学习奖励、消费、退款）。</p>
    <p>累计净变动：<b>${sum >= 0 ? '+' : ''}${sum}</b> 笔数 ${txns.length}</p>
  </div>
  <div class="tablewrap"><table class="tbl">
    <tr><th>时间</th><th>学员</th><th>类别</th><th>变动</th><th>原因</th><th>操作人</th></tr>
    ${txns.length ? txns.map(t => `<tr>
      <td>${esc(t.created_at)}</td>
      <td>${esc(t.user)}</td>
      <td>${COIN_CAT_LABEL[t.category] || (t.category || '—')}</td>
      <td class="${t.amount >= 0 ? 'amt-in' : 'amt-out'}">${t.amount >= 0 ? '+' : ''}${t.amount}</td>
      <td>${esc(t.reason)}</td>
      <td>${esc(t.operator || '—')}</td>
    </tr>`).join('') : '<tr><td colspan="6" class="muted">暂无流水</td></tr>'}
  </table></div>`
}
