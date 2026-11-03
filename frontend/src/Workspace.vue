<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import DocumentLibrary from './DocumentLibrary.vue'
import SearchPanel from './SearchPanel.vue'
import QuestionPanel from './QuestionPanel.vue'
import TaskCenter from './TaskCenter.vue'
import EvaluationCenter from './EvaluationCenter.vue'
import RuntimeCenter from './RuntimeCenter.vue'
import emblem from './assets/cqupt-emblem.webp'
import './centers.css'

const pages = [
  { id:'projects', title:'项目管理', heading:'我的项目', eyebrow:'PROJECT WORKSPACE', icon:'▦', description:'选择项目并管理成员访问权限。' },
  { id:'documents', title:'资料与版本', heading:'资料与版本', eyebrow:'DOCUMENT LIBRARY', icon:'▤', description:'管理当前项目的资料、处理任务和历史版本。' },
  { id:'search', title:'证据检索', heading:'证据检索', eyebrow:'EVIDENCE SEARCH', icon:'⌕', description:'从当前项目的可用资料中检索和核对证据。' },
  { id:'questions', title:'知识问答', heading:'知识问答', eyebrow:'GROUNDED ANSWERS', icon:'◉', description:'依据项目资料回答问题，并查看引用与个人历史。' },
  { id:'tasks', title:'处理任务中心', heading:'处理任务中心', eyebrow:'INGESTION TASKS', icon:'↻', description:'跟踪异步处理、版本发布与失败恢复。' },
  { id:'evaluation', title:'评测中心', heading:'评测中心', eyebrow:'EVALUATION LAB', icon:'▥', description:'用人工标注的证据比较检索效果与实际耗时。' },
  { id:'runtime', title:'运行与审计', heading:'运行与审计', eyebrow:'OPERATIONS & AUDIT', icon:'◎', description:'查看运行活动、资源限制和项目操作记录。' },
]

function readRoute() {
  const [path,query=''] = window.location.hash.slice(1).split('?')
  const name = path?.replace(/^\//,'')
  return { page:pages.some(p=>p.id===name)?name:'projects', project:new URLSearchParams(query).get('project') }
}

const page = ref(readRoute().page)
const currentPage = computed(() => pages.find(p=>p.id===page.value))

const user = ref(null)
const token = ref('')
const username = ref('')
const password = ref('')
const projects = ref([])
const selected = ref(null)
const members = ref([])
const busy = ref(false)
const error = ref('')
const traceId = ref('')
const serviceReady = ref(null)
const projectForm = ref(false)
const name = ref('')
const description = ref('')
const memberSearch = ref('')
const memberRole = ref('member')
const isAdmin = computed(() => selected.value?.role === 'admin')

function pageUrl(name, project=selected.value) {
  return '#/'+name+(project ? '?project='+encodeURIComponent(project.id) : '')
}

function goPage(name) {
  window.location.hash = pageUrl(name)
}

async function syncRoute() {
  const route = readRoute()
  page.value = route.page
  error.value = ''; traceId.value = ''; projectForm.value = false
  if (user.value) {
    const project = projects.value.find(p=>p.id===route.project) || selected.value || projects.value[0] || null
    await run(() => selectProject(project))
  }
  window.scrollTo({top:0})
}

function reset() {
  token.value = ''; user.value = null; projects.value = []; selected.value = null; members.value = []
  password.value = ''; memberSearch.value = ''; projectForm.value = false
  page.value = 'projects'; window.history.replaceState(null,'','#/projects')
}

async function api(path, options = {}, format = 'json') {
  const requestToken = token.value
  const response = await fetch('/api' + path, {
    ...options,
    headers: { ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...(requestToken ? { Authorization: 'Bearer ' + requestToken } : {}), ...options.headers },
  })
  // Ignore responses from a previous identity instead of exposing stale project state.
  if (requestToken !== token.value) throw new Error('身份已变化，请重新操作')
  const payload = response.status === 204 ? null : response.ok && format === 'blob' ? await response.blob() : await response.json()
  if (!response.ok) {
    traceId.value = payload?.trace_id || response.headers.get('x-request-id') || ''
    if (response.status === 401 && path !== '/auth/login') reset()
    const failure = new Error(payload?.error?.message || '请求失败，请检查后端服务')
    failure.traceId = traceId.value
    throw failure
  }
  return payload
}

async function run(action) {
  if (busy.value) return
  busy.value = true; error.value = ''; traceId.value = ''
  try { await action() } catch (e) { error.value = e instanceof TypeError ? '无法连接服务，请检查后端是否已启动' : e.message }
  finally { busy.value = false }
}

async function refreshProjects(preferred) {
  projects.value = await api('/projects')
  const project = projects.value.find(p => p.id === (preferred || readRoute().project)) || projects.value[0] || null
  await selectProject(project)
}

async function selectProject(project) {
  members.value = []; selected.value = project
  window.history.replaceState(null,'',pageUrl(page.value,project))
  if (page.value==='projects' && project?.role === 'admin') members.value = await api(`/projects/${project.id}/members`)
}

function switchProject(event) {
  return run(() => selectProject(projects.value.find(p=>p.id===event.target.value) || null))
}

function login() {
  return run(async () => {
    const result = await api('/auth/login', { method: 'POST', body: JSON.stringify({ username: username.value, password: password.value }) })
    token.value = result.access_token; user.value = result.user; password.value = ''
    await refreshProjects()
  })
}

function logout() {
  return run(async () => {
    try { await api('/auth/logout', { method: 'POST' }) } finally { reset() }
  })
}

function createProject() {
  return run(async () => {
    const result = await api('/projects', { method: 'POST', body: JSON.stringify({ name: name.value, description: description.value }) })
    projectForm.value = false; name.value = ''; description.value = ''
    await refreshProjects(result.id)
  })
}

function addMember() {
  return run(async () => {
    const found = await api('/users?username=' + encodeURIComponent(memberSearch.value.trim()))
    if (!found.length) throw new Error('未找到该账号，请确认已通过本地工具创建')
    await api(`/projects/${selected.value.id}/members/${found[0].id}`, { method: 'PUT', body: JSON.stringify({ role: memberRole.value }) })
    memberSearch.value = ''
    await refreshProjects(selected.value.id)
  })
}

function removeMember(member) {
  if (!window.confirm(`确认撤销 ${member.user.display_name} 对当前项目的访问？`)) return
  return run(async () => {
    await api(`/projects/${selected.value.id}/members/${member.user.id}`, { method: 'DELETE' })
    await refreshProjects(selected.value.id)
  })
}

onMounted(async () => {
  window.addEventListener('hashchange',syncRoute)
  try { serviceReady.value = (await api('/health/ready')).status === 'ok' }
  catch { serviceReady.value = false }
})
onUnmounted(() => window.removeEventListener('hashchange',syncRoute))
</script>

<template>
  <div v-if="!user" class="login-shell">
    <section class="intro">
      <div class="university-lockup"><img :src="emblem" alt="重庆邮电大学校徽" class="university-emblem"><div><strong>重庆邮电大学</strong><span>软件体系结构课程项目</span></div></div>
      <div class="brand"><span class="brand-icon">知</span> 知序 <small>Knowledge Workspace</small></div>
      <p class="eyebrow">软件体系结构课程 · 可信 RAG</p>
      <h1>让知识有据可循，<br>让协作有序发生。</h1>
      <p class="intro-text">从项目空间开始，建立资料的访问边界。逐步连接文档、检索和有出处的回答。</p>
      <div class="principles"><span>01 项目隔离</span><span>02 证据溯源</span><span>03 版本可追踪</span></div>
      <p class="stage-note">七个独立板块连接项目、资料、检索、问答、任务、评测与运行审计。</p>
    </section>
    <section class="login-card">
      <p class="eyebrow">WELCOME BACK</p><h2>进入工作空间</h2><p class="muted">使用本机已创建的账号登录。</p>
      <form @submit.prevent="login">
        <label>用户名<input v-model="username" required maxlength="64" autocomplete="username" placeholder="输入用户名"></label>
        <label>密码<input v-model="password" type="password" required maxlength="128" autocomplete="current-password" placeholder="输入密码"></label>
        <div v-if="error" class="error" role="alert">{{ error }}<small v-if="traceId">追踪编号：{{ traceId }}</small></div>
        <button class="primary full" :disabled="busy">{{ busy ? '正在登录…' : '登录工作空间 →' }}</button>
      </form>
      <p class="hint">演示账号由本地初始化工具创建，密码见项目启动说明中的本地账号文件。</p>
      <div class="service-status"><span :class="['dot', { green: serviceReady }]" />{{ serviceReady === null ? '检查服务中…' : serviceReady ? '本机服务已就绪' : '后端尚未就绪，请检查启动状态' }}</div>
    </section>
  </div>
  <div v-else class="workspace">
    <aside>
      <div class="brand workspace-brand"><img :src="emblem" alt="重庆邮电大学校徽" class="sidebar-emblem"><div>知序<small>重庆邮电大学 · 课程项目</small></div></div>
      <p class="sidebar-label">工作空间</p>
      <nav class="desktop-nav" aria-label="主导航"><button v-for="item in pages" :key="item.id" :data-nav="item.id" :class="['nav-link',{ 'nav-active':page===item.id }]" :aria-current="page===item.id ? 'page' : undefined" :disabled="busy" @click="goPage(item.id)">{{ item.icon }}　{{ item.title }}</button></nav>
      <div class="sidebar-bottom"><strong>{{ user.display_name }}</strong><span>{{ user.username }}</span><button class="ghost" :disabled="busy" @click="logout">退出登录</button></div>
    </aside>
    <main :data-page="page">
      <header><div><p class="eyebrow">{{ currentPage.eyebrow }}</p><h1>{{ currentPage.heading }}</h1><p class="muted">{{ currentPage.description }}</p></div><button v-if="page==='projects'" class="primary" :disabled="busy" @click="projectForm = !projectForm">＋ 创建项目</button><button v-else class="ghost" :disabled="busy" @click="run(() => refreshProjects(selected?.id))">刷新项目</button></header>
      <div class="mobile-session"><span class="mobile-identity"><img :src="emblem" alt="重庆邮电大学校徽">{{ user.display_name }}</span><button class="ghost" :disabled="busy" @click="logout">退出登录</button></div>
      <nav class="mobile-navigation" aria-label="移动导航"><button v-for="item in pages" :key="item.id" :data-mobile-nav="item.id" :class="{active:page===item.id}" :disabled="busy" @click="goPage(item.id)">{{ item.title }}</button></nav>
      <div v-if="error" class="error" role="alert">{{ error }}<small v-if="traceId">追踪编号：{{ traceId }}</small></div>
      <div v-if="page !== 'projects' && selected" class="project-context"><label>当前项目<select class="project-selector" :value="selected.id" :disabled="busy" @change="switchProject"><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option></select></label><span :class="['badge',{admin:isAdmin}]">{{ isAdmin ? '项目管理员' : '项目成员' }}</span></div>
      <template v-if="page==='projects'">
      <form v-if="projectForm" class="panel create-form" @submit.prevent="createProject">
        <h2>创建项目空间</h2><label>项目名称<input v-model="name" required maxlength="100" placeholder="例如：软件体系结构研究"></label>
        <label>项目说明<textarea v-model="description" maxlength="2000" rows="2" placeholder="介绍项目用途和资料范围" /></label>
        <div class="actions"><button type="button" class="ghost" @click="projectForm = false">取消</button><button class="primary" :disabled="busy">创建并进入</button></div>
      </form>
      <div v-if="!projects.length && !busy" class="panel empty"><h2>还没有可访问的项目</h2><p class="muted">创建一个项目，或请项目管理员添加你的账号。</p></div>
      <section class="project-grid" aria-label="项目列表">
        <button v-for="project in projects" :key="project.id" :class="['project-card', { selected: selected?.id === project.id }]" :disabled="busy" @click="run(() => selectProject(project))">
          <div class="card-top"><span class="folder">▱</span><span :class="['badge', { admin: project.role === 'admin' }]">{{ project.role === 'admin' ? '管理员' : '成员' }}</span></div>
          <h2>{{ project.name }}</h2><p>{{ project.description || '暂无项目说明' }}</p><span class="card-bottom">{{ selected?.id === project.id ? '当前项目' : '查看项目' }} →</span>
        </button>
      </section>
      <section v-if="selected" class="panel">
        <div class="section-heading"><div><p class="eyebrow">ACCESS & MEMBERS</p><h2>{{ selected.name }} · 项目访问</h2></div><button class="ghost" :disabled="busy" @click="run(() => refreshProjects(selected.id))">刷新</button></div>
        <p v-if="!isAdmin" class="muted">你是本项目成员，成员管理由管理员负责。可通过导航进入资料、检索和问答页面。</p>
        <template v-else>
          <p class="muted">添加已存在的本机账号。撤销成员后，其现有会话也将失去本项目访问权限。</p>
          <form class="member-form" @submit.prevent="addMember"><label>精确用户名<input v-model="memberSearch" required maxlength="64" placeholder="例如 demo_member"></label><label>项目角色<select v-model="memberRole"><option value="member">成员</option><option value="admin">管理员</option></select></label><button class="primary" :disabled="busy">添加 / 更新</button></form>
          <div class="table-wrap"><table><thead><tr><th>成员</th><th>用户名</th><th>项目角色</th><th>操作</th></tr></thead><tbody><tr v-for="member in members" :key="member.user.id"><td>{{ member.user.display_name }} <small v-if="member.user.id === user.id">（你）</small></td><td>{{ member.user.username }}</td><td><span :class="['badge', { admin: member.role === 'admin' }]">{{ member.role === 'admin' ? '管理员' : '成员' }}</span></td><td><button class="danger-link" :disabled="busy" @click="removeMember(member)">撤销访问</button></td></tr></tbody></table></div>
          <p class="hint">系统会保护最后一位管理员。请先添加另一位管理员，再撤销或降级原管理员。</p>
        </template>
      </section>
      </template>
      <template v-else-if="selected">
        <DocumentLibrary v-if="page==='documents'" :key="'docs-'+selected.id" :project="selected" :api="api" />
        <SearchPanel v-else-if="page==='search'" :key="'search-'+selected.id" :project="selected" :api="api" />
        <QuestionPanel v-else-if="page==='questions'" :key="'qa-'+selected.id" :project="selected" :api="api" />
        <TaskCenter v-else-if="page==='tasks'" :key="'tasks-'+selected.id" :project="selected" :api="api" />
        <EvaluationCenter v-else-if="page==='evaluation'" :key="'eval-'+selected.id" :project="selected" :api="api" />
        <RuntimeCenter v-else-if="page==='runtime'" :key="'runtime-'+selected.id" :project="selected" :api="api" />
      </template>
      <section v-else class="panel empty"><h2>请先选择一个项目</h2><p class="muted">创建或加入项目后，才能访问相应资料和问答。</p><button class="primary" @click="goPage('projects')">前往项目管理</button></section>
      <footer>本机课程原型 · AI 辅助开发及人工核验记录见项目文档</footer>
    </main>
  </div>
</template>

<style>
:root{font-family:Inter,"Segoe UI","Microsoft YaHei",sans-serif;color:#233343;background:#f5f6f8;font-synthesis:none;font-size:14px}*{box-sizing:border-box}body{margin:0}button,input,textarea,select{font:inherit}button{cursor:pointer}button:disabled{cursor:wait;opacity:.65}button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible{outline:3px solid #acbdec;outline-offset:2px}button{transition:background .15s,box-shadow .15s}h1,h2,p{margin-top:0}h1{font-size:32px;letter-spacing:-1px;margin-bottom:12px}h2{font-size:19px;margin-bottom:12px}p{line-height:1.8}small{font-size:12px}.brand{display:flex;align-items:center;gap:10px;font-size:23px;font-weight:750;letter-spacing:2px}.brand small{font-weight:400;font-size:12px;letter-spacing:0;color:#6c7b8c}.brand-icon{display:grid;place-items:center;width:36px;height:36px;color:white;background:#344f78;border-radius:10px;font-size:20px}.eyebrow{font-size:11px;font-weight:700;letter-spacing:2px;color:#6b7e97;margin-bottom:12px}.muted{color:#6d7b8c}.hint{font-size:12px;line-height:1.8;color:#788595;margin:18px 0 0}.primary{border:0;border-radius:7px;background:#344f78;color:white;padding:12px 18px;font-weight:600}.primary:hover{background:#253e63}.full{width:100%;margin-top:8px}.ghost{border:1px solid #dce1e7;background:white;padding:9px 14px;border-radius:7px;color:#53647b}.ghost:hover{background:#f4f6f9}.login-shell{min-height:100vh;display:grid;grid-template-columns:1.2fr 1fr;align-items:center;gap:70px;max-width:1180px;padding:70px 50px;margin:auto}.intro .brand{margin-bottom:78px}.intro h1{font-size:43px;line-height:1.45;letter-spacing:-1.5px}.intro-text{max-width:440px;color:#6a7a8c;font-size:15px}.principles{display:flex;gap:24px;margin-top:35px;color:#47607c;font-size:12px}.stage-note{font-size:12px;margin-top:55px;color:#8590a0}.login-card{background:white;border:1px solid #e4e7ed;border-radius:16px;padding:40px;box-shadow:0 15px 60px #26395208}.login-card h2{font-size:25px}.login-card form{margin-top:30px}label{display:block;font-size:12px;font-weight:600;color:#526176;margin-bottom:18px}input,textarea,select{display:block;width:100%;margin-top:8px;border:1px solid #d9dfe7;border-radius:7px;padding:12px;background:white;color:#233343}input::placeholder,textarea::placeholder{color:#9aa4b1}textarea{resize:vertical}.service-status{border-top:1px solid #edf0f4;margin-top:26px;padding-top:19px;font-size:12px;color:#7c8796;display:flex;align-items:center;gap:8px}.dot{height:7px;width:7px;border-radius:50%;background:#c89448}.dot.green{background:#5b9e80}.error{border:1px solid #f1d4d1;background:#fff4f2;color:#9f4037;padding:12px 16px;border-radius:7px;line-height:1.7;margin-bottom:20px}.error small{display:block;overflow-wrap:anywhere}.workspace{min-height:100vh;display:grid;grid-template-columns:225px 1fr}.workspace aside{background:#fff;border-right:1px solid #e5e8ed;padding:30px 20px;display:flex;flex-direction:column}.workspace aside .brand{padding-left:12px}.sidebar-label{font-size:11px;color:#97a0ad;letter-spacing:2px;margin:48px 14px 16px}.nav-active,.nav-future{padding:14px;border-radius:7px;font-size:13px;margin-bottom:8px}.nav-active{background:#edf1f7;color:#344f78;font-weight:700}.nav-future{color:#98a1af}.nav-future small{font-size:10px;margin-left:8px}.sidebar-bottom{margin-top:auto;padding:55px 14px 0;display:flex;flex-direction:column;gap:8px}.sidebar-bottom span{color:#8792a2;font-size:12px}.sidebar-bottom button{margin-top:12px}.workspace main{padding:48px 50px;max-width:1350px;width:100%;margin:auto}header,.section-heading{display:flex;align-items:center;justify-content:space-between;gap:24px}header{margin-bottom:24px}.project-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:20px;margin-bottom:30px}.project-card{border:1px solid #e1e5eb;background:white;border-radius:11px;padding:24px;text-align:left;color:inherit}.project-card:hover{box-shadow:0 6px 22px #2333430b}.project-card.selected{border:1px solid #6382ac;box-shadow:0 0 0 2px #6382ac16}.card-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px}.folder{color:#607c9f;font-size:30px}.badge{border-radius:5px;background:#f0f2f5;padding:5px 9px;font-size:11px;color:#798698}.badge.admin{background:#edf2fa;color:#4b6c99}.project-card h2{font-size:18px}.project-card p{color:#7a8795;min-height:48px;font-size:12px;overflow-wrap:anywhere}.card-bottom{font-size:11px;color:#6d809a}.panel{background:white;border:1px solid #e1e5eb;padding:27px;border-radius:11px;margin-bottom:25px}.panel .section-heading h2{margin-bottom:10px}.member-form{display:grid;grid-template-columns:1fr 155px auto;gap:16px;align-items:end;margin:24px 0}.member-form label{margin:0}.member-form button{height:44px}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap}th,td{text-align:left;padding:16px 12px;border-bottom:1px solid #edf0f4}th{background:#f9fafc;color:#8b97a6;font-size:11px;font-weight:500}.danger-link{border:0;background:transparent;color:#a95e56;font-size:12px;padding:5px 0}.actions{display:flex;justify-content:flex-end;gap:12px}.empty{text-align:center;padding:45px}footer{font-size:11px;color:#9aa3af;margin-top:35px;line-height:1.8}@media(max-width:900px){.login-shell{grid-template-columns:1fr;max-width:550px;gap:30px;padding:35px 20px}.intro .brand{margin-bottom:32px}.intro h1{font-size:30px}.principles,.stage-note{display:none}.workspace{grid-template-columns:1fr}.workspace aside{display:none}.workspace main{padding:28px 18px}.member-form{grid-template-columns:1fr}.login-card{padding:28px}header{align-items:flex-start}header h1{font-size:27px}}
</style>
<style>
.workspace aside{position:sticky;top:0;height:100vh;align-self:start}.desktop-nav{display:flex;flex-direction:column;gap:8px}.nav-link{border:0;background:transparent;text-align:left;border-radius:7px;padding:14px;color:#6d7b8c;font-size:13px}.nav-link:hover{background:#f4f6f9}.nav-link.nav-active{background:#edf1f7;color:#344f78;font-weight:700;margin-bottom:0}.project-context{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:24px;background:#edf1f6;border:1px solid #e0e6ee;border-radius:9px;padding:17px 20px}.project-context label{margin:0;min-width:0;flex:1;max-width:550px}.project-context select{background:#fff}.project-context>.badge{flex-shrink:0}.mobile-navigation{display:none}@media(max-width:900px){.workspace aside{display:none}.mobile-navigation{display:flex;gap:8px;overflow:auto;margin-bottom:22px;padding-bottom:4px}.mobile-navigation button{border:1px solid #dce3ed;color:#708299;background:#fff;padding:10px;border-radius:6px;font-size:12px;white-space:nowrap}.mobile-navigation button.active{background:#344f78;border-color:#344f78;color:#fff}.project-context{padding:14px;gap:10px}.project-context>.badge{font-size:10px}}
</style>
<style>
.university-lockup{display:flex;align-items:center;gap:17px;margin-bottom:32px}.university-emblem{width:72px;height:72px;object-fit:contain}.university-lockup strong{display:block;font-size:19px;letter-spacing:3px;color:#365f4e}.university-lockup span{display:block;font-size:11px;color:#829287;margin-top:9px;letter-spacing:1px}.intro .brand{margin-bottom:40px!important}.sidebar-emblem{width:42px;height:48px;object-fit:contain;flex-shrink:0}.workspace-brand{gap:10px!important;padding-left:2px!important}.workspace-brand small{display:block;font-size:9px!important;letter-spacing:0;margin-top:7px;white-space:nowrap}.mobile-identity{display:flex;align-items:center;gap:10px}.mobile-identity img{width:28px;height:32px;object-fit:contain}.desktop-nav{gap:3px!important}.desktop-nav .nav-link{padding:12px 14px}.sidebar-label{margin-top:30px!important}.mobile-navigation{flex-wrap:wrap!important;overflow:visible!important}.mobile-navigation button{flex:1 0 auto}@media(max-width:650px){.university-lockup{margin-bottom:24px}.university-emblem{width:54px;height:54px}.university-lockup strong{font-size:16px}.intro .brand{margin-bottom:24px!important}}
.mobile-session { display: none; }
header > button { flex-shrink: 0; white-space: nowrap; }
.nav-library { border: 0; background: transparent; color: #53647b; text-align: left; padding: 14px; font-size: 13px; border-radius: 7px; }
.nav-library:hover { background: #f4f6f9; }
.workspace { grid-template-columns: 225px minmax(0, 1fr); }
.workspace main { min-width: 0; }
@media (max-width: 900px) { .workspace { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 900px) { .mobile-session { display: flex; justify-content: space-between; align-items: center; margin-bottom: 22px; } }
</style>
