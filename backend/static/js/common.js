/* ============ 英语大师 · 原生 JS 前端（无构建，Flask 直接托管） ============ */
const API_BASE = '/api/v1'

/* ---------- 状态 ---------- */
function getToken() { return localStorage.getItem('em_token') }
function setToken(t) { t ? localStorage.setItem('em_token', t) : localStorage.removeItem('em_token') }
function getUser() { const r = localStorage.getItem('em_user'); return r ? JSON.parse(r) : null }
function setUser(u) { u ? localStorage.setItem('em_user', JSON.stringify(u)) : localStorage.removeItem('em_user') }
function setBalance(b) { const u = getUser(); if (u) { u.coin_balance = b; setUser(u) } }
function doLogout() {
  modalConfirm('确定要退出登录吗？', '退出', '取消').then(ok => {
    if (ok) { clearLeaveGuard(); setToken(null); setUser(null); location.hash = '#/login' }
  })
}

/* ---------- API 客户端 ---------- */
async function api(path, method = 'GET', body) {
  const headers = {}
  const t = getToken()
  if (t) headers['Authorization'] = 'Bearer ' + t
  if (body) headers['Content-Type'] = 'application/json'
  let data = {}
  let res
  try {
    res = await fetch(API_BASE + path, {
      method, headers,
      body: body ? JSON.stringify(body) : undefined,
    })
    try { data = await res.json() } catch (e) { data = {} }
  } catch (e) {
    return { ok: false, status: 0, data: { error: '网络错误：' + e.message } }
  }
  if (res.status === 401 && !['/auth/login', '/auth/register'].includes(path)) {
    if (location.hash !== '#/login') {   // 已在登录页则不再重复跳转，避免死循环
      toast('登录已过期，请重新登录', true)
      setToken(null); setUser(null); location.hash = '#/login'
    }
  }
  return { ok: res.ok, status: res.status, data }
}

/* ---------- 工具 ---------- */
function esc(s) {
  if (s == null) return ''
  return String(s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
}
function $(sel) { return document.querySelector(sel) }
function el(id) { return document.getElementById(id) }
function toast(msg, isErr, sticky, id) {
  const root = el('toast-root')
  let t
  if (id && (t = el('toast-' + id))) {
    // 同一 id 复用：更新内容与样式，不重复堆叠
    t.className = 'toast' + (isErr ? ' err' : '') + (sticky ? ' sticky' : '')
    const span = t.querySelector('.toast-msg')
    if (span) span.textContent = msg
    return
  }
  t = document.createElement('div')
  if (id) t.id = 'toast-' + id
  t.className = 'toast' + (isErr ? ' err' : '') + (sticky ? ' sticky' : '')
  const span = document.createElement('span')
  span.className = 'toast-msg'
  span.textContent = msg
  t.appendChild(span)
  if (sticky) {
    const x = document.createElement('span')
    x.className = 'toast-x'
    x.textContent = '✕'
    t.appendChild(x)
  }
  // 点击（含 ✕）即关闭；sticky 不自动消失，必须管理员手动点掉
  t.addEventListener('click', () => t.remove())
  root.appendChild(t)
  if (!sticky) setTimeout(() => t.remove(), 2600)
}
function modal(html) {
  const root = el('modal-root')
  root.innerHTML = `<div class="modal-mask" onclick="if(event.target===this)closeModal()">
    <div class="modal">${html}</div></div>`
}
function closeModal() { el('modal-root').innerHTML = '' }

/* ---------- 离开确认守卫（做题/任务界面离开时二次确认）---------- */
let _leaveGuard = null
function setLeaveGuard(fn) { _leaveGuard = fn }
function clearLeaveGuard() { _leaveGuard = null }

/* Promise 化的二次确认弹窗；返回 true=确认 false=取消 */
function modalConfirm(message, okText, cancelText) {
  return new Promise(resolve => {
    const ok = okText || '确定'
    const cancel = cancelText || '取消'
    modal(`<p style="margin:0 0 16px;line-height:1.5">${esc(message)}</p>
      <div class="row" style="justify-content:flex-end;gap:10px;margin:0">
        <button class="btn ghost" onclick="__mcResolve(false)">${esc(cancel)}</button>
        <button class="btn" onclick="__mcResolve(true)">${esc(ok)}</button>
      </div>`)
    window.__mcResolve = (v) => { closeModal(); resolve(v) }
  })
}

async function nav(hash) {
  if (_leaveGuard && typeof _leaveGuard === 'function') {
    const msg = _leaveGuard()
    if (msg) {
      const cur = (location.hash.slice(1) || '/').split('/').filter(Boolean)
      const tgt = (hash.slice(1) || '/').split('/').filter(Boolean)
      // 同一门课程内部跳转（如步骤间、返回步骤概览）不算“离开”
      const sameLearn = cur[0] === 'learn' && tgt[0] === 'learn' && cur[1] === tgt[1]
      if (!sameLearn) {
        const ok = await modalConfirm(msg, '确定离开', '再想想')
        if (!ok) return
        clearLeaveGuard()
      }
    }
  }
  location.hash = hash
}

/* ---------- 框架 ---------- */
function studentFrame(inner, active) {
  const u = getUser() || {}
  return `<div class="app">
    <div class="topbar">
      <h1>英语大师</h1>
      <div class="row">
        <span class="coin">🪙 ${u.coin_balance ?? 0}</span>
        <button class="btn ghost sm" onclick="nav('#/settings')">设置</button>
        <button class="btn ghost sm" onclick="doLogout()">退出</button>
      </div>
    </div>
    <div class="content">${inner}</div>
    <nav class="navbar">
      <a href="javascript:void(0)" onclick="nav('#/')" class="${active === 'home' ? 'active' : ''}"><span class="ico">🏠</span>首页</a>
      <a href="javascript:void(0)" onclick="nav('#/coins')" class="${active === 'coins' ? 'active' : ''}"><span class="ico">🪙</span>金币</a>
      <a href="javascript:void(0)" onclick="nav('#/shop')" class="${active === 'shop' ? 'active' : ''}"><span class="ico">🛒</span>商店</a>
      <a href="javascript:void(0)" onclick="nav('#/wishes')" class="${active === 'wishes' ? 'active' : ''}"><span class="ico">🌟</span>许愿</a>
      <a href="javascript:void(0)" onclick="nav('#/report')" class="${active === 'report' ? 'active' : ''}"><span class="ico">📊</span>报表</a>
    </nav>
  </div>`
}
function adminFrame(inner, activeTab) {
  const tabs = [
    ['courses', '🎧 听说管理'], ['schemes', '🎯 听力大师'],
    ['words', '📚 单词管理'],
    ['rewards', '🎁 奖励管理'], ['students', '👥 学员管理'],
    ['appeals', '⚖️ 人工复议'], ['system', '🛠️ 系统工具'],
  ]
  const labels = {}
  tabs.forEach(([k, l]) => { labels[k] = l })
  const nav = tabs.map(([k, l]) => {
    const badge = k === 'appeals'
      ? ` <span id="appealBadge" style="display:none;background:#e74c3c;color:#fff;border-radius:10px;padding:0 6px;font-size:11px;margin-left:4px"></span>` : ''
    return `<a class="admin-nav-item ${activeTab === k ? 'active' : ''}" href="#/admin/${k}">${l}${badge}</a>`
  }).join('')
  const u = getUser() || {}
  return `<div class="app admin-app">
    <aside class="admin-side">
      <div class="admin-brand">英语大师<span>管理后台</span></div>
      <nav class="admin-nav">${nav}</nav>
      <div class="admin-side-foot">
        <a class="btn ghost sm block" href="#/">返回学生端</a>
        <button class="btn ghost sm block" onclick="openChangePassword()">修改密码</button>
        <button class="btn ghost sm block" onclick="doLogout()">退出登录</button>
      </div>
    </aside>
    <main class="admin-main">
      <div class="admin-head">
        <h2>${labels[activeTab] || '管理后台'}</h2>
        <div class="row">
          <span class="coin">🪙 ${u.coin_balance ?? 0}</span>
          <span class="muted">${esc(u.username || '')}</span>
        </div>
      </div>
      <div class="content admin-content">${inner}</div>
    </main>
  </div>`
}

/* 管理后台内嵌二级工具栏（用于各板块下的子功能导航） */
function adminSubTabs(items, active) {
  return `<div class="subtabs">` + items.map(([href, label]) =>
    `<a class="subtab ${active === href ? 'active' : ''}" href="${href}">${label}</a>`).join('') + `</div>`
}

/* ---------- 登录 ---------- */
function renderLogin() {
  el('app').innerHTML = `<div class="app"><div class="auth-wrap"><div class="auth-inner">
    <div class="center" style="margin-bottom:24px">
      <div style="font-size:48px">📚</div>
      <h2>英语大师</h2>
      <p class="muted">锁定式闯关 · 六步法深度学习</p>
    </div>
    <form class="card" onsubmit="return loginSubmit(event)">
      <input id="lu" placeholder="用户名" style="margin-bottom:12px" />
      <input id="lp" type="password" placeholder="密码" style="margin-bottom:12px" />
      <p id="lerr" class="error-box"></p>
      <button class="btn block" type="submit">登录</button>
    </form>
    <p class="center muted">还没有账号？<a href="#/register" style="color:var(--primary)">注册学生号</a></p>
    <p class="center muted" style="font-size:13px">管理员默认账号 admin / admin123</p>
    <div id="versionFooter" class="version-footer"><span class="muted" style="font-size:12px">系统版本加载中…</span></div>
  </div></div></div>`
  refreshVersionFooter()
}
async function refreshVersionFooter() {
  try {
    const r = await fetch('/api/v1/version')
    if (!r.ok) return
    const d = await r.json()
    const box = document.getElementById('versionFooter')
    if (!box) return
    let html = `<button class="version-toggle" type="button" onclick="toggleChangelog()">系统版本 ${d.version} ▾</button>`
    html += `<div id="changelogBox" class="changelog" style="display:none">`
    ;(d.changelog || []).forEach(c => {
      html += `<div class="cl-item"><div class="cl-ver">${c.version} · ${c.title}</div><ul>`
      ;(c.items || []).forEach(it => { html += `<li>${it}</li>` })
      html += `</ul></div>`
    })
    html += `</div>`
    box.innerHTML = html
  } catch (e) { /* 忽略：版本信息非关键 */ }
}
function toggleChangelog() {
  const b = document.getElementById('changelogBox')
  if (b) b.style.display = b.style.display === 'none' ? 'block' : 'none'
}
async function loginSubmit(e) {
  e.preventDefault()
  const username = el('lu').value.trim()
  const password = el('lp').value
  if (!username || !password) { el('lerr').textContent = '请输入用户名和密码'; return false }
  const r = await api('/auth/login', 'POST', { username, password })
  if (!r.ok) { el('lerr').textContent = r.data.error || '登录失败'; return false }
  setToken(r.data.access_token)
  setUser(r.data.user)
  // 路由到对应首页
  location.hash = r.data.user.role === 'admin' ? '#/admin' : '#/'
  return false
}

/* ---------- 注册 ---------- */
function renderRegister() {
  el('app').innerHTML = `<div class="app"><div class="auth-wrap"><div class="auth-inner">
    <div class="center" style="margin-bottom:24px"><h2>注册学生号</h2></div>
    <form class="card" onsubmit="return registerSubmit(event)">
      <input id="ru" placeholder="用户名" style="margin-bottom:12px" />
      <input id="rp" type="password" placeholder="密码（至少6位）" style="margin-bottom:12px" />
      <p id="rerr" class="error-box"></p>
      <button class="btn block" type="submit">注册</button>
    </form>
    <p class="center muted"><a href="#/login" style="color:var(--primary)">已有账号？去登录</a></p>
  </div></div>`
}
async function registerSubmit(e) {
  e.preventDefault()
  const username = el('ru').value.trim()
  const password = el('rp').value
  if (!username || !password) { el('rerr').textContent = '请输入用户名和密码'; return false }
  if (password.length < 6) { el('rerr').textContent = '密码至少6位'; return false }
  const r = await api('/auth/register', 'POST', { username, password })
  if (!r.ok) { el('rerr').textContent = r.data.error || '注册失败'; return false }
  toast('注册成功，请登录')
  location.hash = '#/login'
  return false
}

/* ---------- 修改密码 ---------- */
function openChangePassword() {
  modal(`<h3>修改密码</h3>
    <input id="cp_old" type="password" placeholder="原密码" style="margin-bottom:10px" />
    <input id="cp_new" type="password" placeholder="新密码（至少6位）" style="margin-bottom:12px" />
    <p id="cp_msg" class="error-box"></p>
    <div class="row">
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn" onclick="changePasswordSubmit()">确认修改</button>
    </div>`)
}
async function changePasswordSubmit() {
  const old_password = el('cp_old').value
  const new_password = el('cp_new').value
  if (!new_password || new_password.length < 6) { el('cp_msg').textContent = '新密码至少6位'; return }
  const r = await api('/auth/change-password', 'POST', { old_password, new_password })
  if (!r.ok) { el('cp_msg').textContent = r.data.error || '修改失败'; return }
  closeModal()
  toast('密码已修改，请重新登录')
  setToken(null); setUser(null)
  location.hash = '#/login'
}

/* ---------- 路由器 ---------- */
function route() {
  const token = getToken()
  const user = getUser()
  const parts = (location.hash.slice(1) || '/').split('/').filter(Boolean)
  const app = el('app')
  if (!token || !user) {
    if (parts[0] === 'register') return renderRegister()
    return renderLogin()
  }
  if (user.role === 'admin') {
    if (parts[0] !== 'admin') return renderAdmin('courses')
    return renderAdmin(parts[1] || 'courses')
  }
  // 学生
  if (parts[0] === 'admin') return renderHome()
  if (parts[0] === 'listen') return renderListenHome()
  if (parts[0] === 'scheme') {
    if (parts[1] === 'learn' && parts[2]) return renderSchemeLearn(parts[2])
    return renderSchemeHome()
  }
  if (parts[0] === 'rewards') return renderRewards()
  if (parts[0] === 'learn' && parts[1]) return renderLearn(parts[1])
  if (parts[0] === 'settings') return renderSettings()
  if (parts[0] === 'coins') return renderCoins()
  if (parts[0] === 'shop') return renderShop()
  if (parts[0] === 'wishes') return renderWishes()
  if (parts[0] === 'report') return renderReport()
  return renderHome()
}

// 移动端：输入框聚焦时自动滚入可视区域，避免被输入法遮挡
document.addEventListener('focusin', e => {
  const t = e.target
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')) {
    setTimeout(() => { try { t.scrollIntoView({ block: 'center', behavior: 'smooth' }) } catch (e2) {} }, 300)
  }
})

window.addEventListener('hashchange', route)
document.addEventListener('DOMContentLoaded', route)
