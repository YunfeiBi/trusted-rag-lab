<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import DocumentLibrary from './DocumentLibrary.vue'
import SearchPanel from './SearchPanel.vue'
import QuestionPanel from './QuestionPanel.vue'
import TaskCenter from './TaskCenter.vue'
import EvaluationCenter from './EvaluationCenter.vue'
import RuntimeCenter from './RuntimeCenter.vue'
import TeamWorkspace from './TeamWorkspace.vue'
import emblem from './assets/cqupt-emblem.webp'
import './centers.css'

const pages = [
  { id:'projects', title:'团队与项目', heading:'团队与项目', eyebrow:'TEAM WORKSPACE', icon:'▦', description:'加入实验团队，在获授权的项目范围内协作。' },
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
const authMode = ref('login')
const registerEmail = ref('')
const registerIdentity = ref('student')
const confirmPassword = ref('')
const authNotice = ref('')
const profileForm = ref(false)
const profileName = ref('')
const profileEmail = ref('')
const profileIdentity = ref('')
const editingProject = ref(false)
const editName = ref('')
const editDescription = ref('')
const projects = ref([])
const teams = ref([])
const activeTeamId = ref('')
const activeTeam = computed(()=>teams.value.find(t=>t.id===activeTeamId.value)||null)
const scopeProjects = computed(()=>projects.value.filter(p=>p.team_id===activeTeamId.value))
const visibleProjects = computed(()=>scopeProjects.value.filter(p=>!p.is_team_library))
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
  error.value = ''; traceId.value = ''; projectForm.value = false; editingProject.value = false
  if (user.value) {
    const project = projects.value.find(p=>p.id===route.project) || selected.value || projects.value[0] || null
    await run(() => selectProject(project))
  }
  window.scrollTo({top:0})
}

function reset() {
  token.value = ''; user.value = null; projects.value = []; selected.value = null; members.value = []
  teams.value=[];activeTeamId.value=''
  password.value = ''; memberSearch.value = ''; projectForm.value = false
  profileForm.value = false; editingProject.value = false; confirmPassword.value = ''; authMode.value = 'login'; authNotice.value = ''
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

async function refreshProjects(preferred,preferredTeam) {
  teams.value = await api('/teams')
  projects.value = await api('/projects?include_libraries=true')
  const requested=projects.value.find(p=>p.id===(preferred||readRoute().project))
  activeTeamId.value=(teams.value.some(t=>t.id===preferredTeam)?preferredTeam:null) || requested?.team_id || (teams.value.some(t=>t.id===activeTeamId.value)?activeTeamId.value:teams.value[0]?.id||'')
  const project = (requested?.team_id===activeTeamId.value?requested:null) || scopeProjects.value.find(p=>!p.is_team_library) || scopeProjects.value[0] || null
  await selectProject(project)
}

function teamChanged(id){return run(()=>refreshProjects(null,id||activeTeamId.value))}
function teamCreated(id){return run(()=>refreshProjects(null,id))}
function openLibrary(id){return run(async()=>{await selectProject(projects.value.find(p=>p.id===id));goPage('documents')})}

async function selectProject(project) {
  members.value = []; selected.value = project; editingProject.value = false
  if(project)activeTeamId.value=project.team_id
  window.history.replaceState(null,'',pageUrl(page.value,project))
  if (page.value==='projects' && project) {
    const roster=await api(`/projects/${project.id}/members`)
    if(selected.value?.id===project.id&&page.value==='projects')members.value=roster
  }
}

function switchAuth() {
  authMode.value = authMode.value==='login'?'register':'login'
  password.value = ''; confirmPassword.value = ''; error.value = ''; traceId.value = ''; authNotice.value = ''
}

function register() {
  return run(async () => {
    if (password.value !== confirmPassword.value) throw new Error('两次输入的密码不一致')
    try {
      const account = await api('/auth/register',{method:'POST',body:JSON.stringify({username:username.value,password:password.value,qq_email:registerEmail.value,academic_identity:registerIdentity.value})})
      username.value = account.username; registerEmail.value = ''; authMode.value = 'login'
      authNotice.value = '注册成功，请登录后申请加入团队。教师身份需确认后才能创建团队。'
    } finally { password.value = ''; confirmPassword.value = '' }
  })
}

function openProfile() {
  profileName.value = user.value.display_name; profileEmail.value = user.value.qq_email || ''; profileIdentity.value=['teacher','student'].includes(user.value.academic_identity)?user.value.academic_identity:''; profileForm.value = true
}

function saveProfile() {
  return run(async () => {
    user.value = await api('/auth/me',{method:'PATCH',body:JSON.stringify({display_name:profileName.value,qq_email:profileEmail.value,academic_identity:profileIdentity.value})})
    profileForm.value = false
    await refreshProjects(selected.value?.id)
  })
}

function editProject() {
  editName.value = selected.value.name; editDescription.value = selected.value.description; editingProject.value = true
}

function saveProject() {
  return run(async () => {
    const id = selected.value.id
    await api('/projects/'+id,{method:'PATCH',body:JSON.stringify({name:editName.value,description:editDescription.value})})
    await refreshProjects(id)
    editingProject.value = false
  })
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
    if(!activeTeam.value)throw new Error('请先加入团队，或由已确认教师创建团队。')
    const result = await api('/projects', { method: 'POST', body: JSON.stringify({ name: name.value, description: description.value,team_id:activeTeam.value.id }) })
    projectForm.value = false; name.value = ''; description.value = ''
    await refreshProjects(result.id)
  })
}

function addMember() {
  return run(async () => {
    const found = await api('/users?username=' + encodeURIComponent(memberSearch.value.trim()))
    if (!found.length) throw new Error('未找到该账号，请确认其已注册并填写准确用户名')
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
      <p class="eyebrow">{{ authMode==='register'?'CREATE ACCOUNT':'WELCOME BACK' }}</p><h2>{{ authMode==='register'?'注册账号':'进入工作空间' }}</h2><p class="muted">{{ authMode==='register'?'使用中文或英文用户名建立你的账号。':'使用已注册账号登录。' }}</p>
      <p v-if="authNotice" class="auth-success" role="status">{{ authNotice }}</p>
      <form class="auth-form" @submit.prevent="authMode==='register'?register():login()">
        <label>用户名<input v-model="username" required maxlength="64" autocomplete="username" placeholder="支持中文、英文、数字及_.-"></label>
        <label v-if="authMode==='register'">QQ邮箱<input v-model="registerEmail" class="register-email" type="email" required maxlength="80" autocomplete="email" placeholder="例如：你的QQ号@qq.com"></label>
        <label v-if="authMode==='register'">成员身份<select v-model="registerIdentity" class="register-identity"><option value="student">学生</option><option value="teacher">教师（需确认身份）</option></select></label>
        <label>密码<input v-model="password" type="password" required :minlength="authMode==='register'?8:1" maxlength="128" :autocomplete="authMode==='register'?'new-password':'current-password'" :placeholder="authMode==='register'?'8—128位密码':'输入密码'"></label>
        <label v-if="authMode==='register'">确认密码<input v-model="confirmPassword" class="confirm-password" type="password" required minlength="8" maxlength="128" autocomplete="new-password" placeholder="再次输入密码"></label>
        <p v-if="authMode==='register'" class="hint">QQ邮箱会用于团队入组审批及师生通讯录，当前不发送验证邮件。学生不能创建团队，自行选择教师也需本机管理员确认身份。</p>
        <div v-if="error" class="error" role="alert">{{ error }}<small v-if="traceId">追踪编号：{{ traceId }}</small></div>
        <button class="primary full" :disabled="busy">{{ busy ? '正在提交…' : authMode==='register'?'注册账号':'登录工作空间 →' }}</button>
      </form>
      <button class="auth-switch" :disabled="busy" @click="switchAuth">{{ authMode==='register'?'已有账号？返回登录':'还没有账号？注册账号' }}</button>
      <div class="service-status"><span :class="['dot', { green: serviceReady }]" />{{ serviceReady === null ? '检查服务中…' : serviceReady ? '本机服务已就绪' : '后端尚未就绪，请检查启动状态' }}</div>
    </section>
  </div>
  <div v-else class="workspace">
    <aside>
      <div class="brand workspace-brand"><img :src="emblem" alt="重庆邮电大学校徽" class="sidebar-emblem"><div>知序<small>重庆邮电大学 · 课程项目</small></div></div>
      <p class="sidebar-label">工作空间</p>
      <nav class="desktop-nav" aria-label="主导航"><button v-for="item in pages" :key="item.id" :data-nav="item.id" :class="['nav-link',{ 'nav-active':page===item.id }]" :aria-current="page===item.id ? 'page' : undefined" :disabled="busy" @click="goPage(item.id)">{{ item.icon }}　{{ item.title }}</button></nav>
      <div class="sidebar-bottom"><strong>{{ user.display_name }}</strong><span>{{ user.username }}</span><button class="ghost profile-button" :disabled="busy" @click="openProfile">个人资料</button><button class="ghost" :disabled="busy" @click="logout">退出登录</button></div>
    </aside>
    <main :data-page="page">
      <header><div><p class="eyebrow">{{ currentPage.eyebrow }}</p><h1>{{ currentPage.heading }}</h1><p class="muted">{{ currentPage.description }}</p></div><button v-if="page==='projects'" class="primary" :disabled="busy||!activeTeam" @click="projectForm = !projectForm">＋ 创建项目</button><button v-else class="ghost" :disabled="busy" @click="run(() => refreshProjects(selected?.id))">刷新项目</button></header>
      <div class="mobile-session"><span class="mobile-identity"><img :src="emblem" alt="重庆邮电大学校徽">{{ user.display_name }}</span><div class="mobile-account-actions"><button class="ghost" :disabled="busy" @click="openProfile">个人资料</button><button class="ghost" :disabled="busy" @click="logout">退出登录</button></div></div>
      <nav class="mobile-navigation" aria-label="移动导航"><button v-for="item in pages" :key="item.id" :data-mobile-nav="item.id" :class="{active:page===item.id}" :disabled="busy" @click="goPage(item.id)">{{ item.title }}</button></nav>
      <div v-if="error" class="error" role="alert">{{ error }}<small v-if="traceId">追踪编号：{{ traceId }}</small></div>
      <form v-if="profileForm" class="panel profile-form" @submit.prevent="saveProfile"><h2>个人资料</h2><p class="muted">用户名：{{ user.username }}。显示名称和QQ邮箱会出现在所属团队与项目的通讯录中。</p><p class="hint">身份：{{ user.teacher_verified?'教师（已确认）':user.academic_identity==='teacher'?'教师身份待本机管理员确认':user.academic_identity==='student'?'学生':'身份待补充' }}</p><label>成员身份<select v-model="profileIdentity" required :disabled="user.teacher_verified"><option value="" disabled>请选择你的身份</option><option value="student">学生</option><option value="teacher">教师（需本机管理员确认）</option></select></label><label>显示名称<input v-model="profileName" required maxlength="100"></label><label>QQ邮箱<input v-model="profileEmail" type="email" required maxlength="80" placeholder="你的QQ邮箱@qq.com"></label><p class="hint">邮箱由本人填写，尚未通过邮件验证。已有账号未填写时，通讯录显示“未填写”。</p><div class="actions"><button type="button" class="ghost" @click="profileForm=false">取消</button><button class="primary" :disabled="busy">保存个人资料</button></div></form>
      <div v-if="page !== 'projects' && selected" class="project-context"><label>{{ activeTeam?.name }} · 当前资料范围<select class="project-selector" :value="selected.id" :disabled="busy" @change="switchProject"><option v-for="project in scopeProjects" :key="project.id" :value="project.id">{{ project.name }}{{ project.is_team_library?'（全团队公共）':'' }}</option></select></label><span :class="['badge',{admin:isAdmin}]">{{ isAdmin ? '资料管理员' : '只读成员' }}</span></div>
      <template v-if="page==='projects'">
      <TeamWorkspace :teams="teams" :active="activeTeam" :user="user" :api="api" @select="teamChanged" @changed="teamChanged()" @created="teamCreated" @library="openLibrary" />
      <form v-if="projectForm" class="panel create-form" @submit.prevent="createProject">
        <h2>创建项目空间</h2><label>项目名称<input v-model="name" required maxlength="100" placeholder="例如：软件体系结构研究"></label>
        <label>项目说明<textarea v-model="description" maxlength="2000" rows="2" placeholder="介绍项目用途和资料范围" /></label>
        <div class="actions"><button type="button" class="ghost" @click="projectForm = false">取消</button><button class="primary" :disabled="busy">创建并进入</button></div>
      </form>
      <div v-if="activeTeam&&!visibleProjects.length&&!busy" class="panel empty"><h2>当前团队暂无你参与的内部项目</h2><p class="muted">你可以先访问团队公共资料；内部项目需由项目管理员授权，也可以在当前团队创建自己的项目。</p></div>
      <section class="project-grid" aria-label="项目列表">
        <button v-for="project in visibleProjects" :key="project.id" :class="['project-card', { selected: selected?.id === project.id }]" :disabled="busy" @click="run(() => selectProject(project))">
          <div class="card-top"><span class="folder">▱</span><span :class="['badge', { admin: project.role === 'admin' }]">{{ project.role === 'admin' ? '管理员' : '成员' }}</span></div>
          <h2>{{ project.name }}</h2><p>{{ project.description || '暂无项目说明' }}</p><span class="card-bottom">{{ selected?.id === project.id ? '当前项目' : '查看项目' }} →</span>
        </button>
      </section>
      <section v-if="selected&&!selected.is_team_library" class="panel">
        <div class="section-heading"><div><p class="eyebrow">ACCESS & MEMBERS</p><h2>{{ selected.name }} · 项目成员</h2></div><div class="project-actions"><button v-if="isAdmin" class="ghost edit-project-button" :disabled="busy" @click="editProject">编辑项目</button><button class="ghost" :disabled="busy" @click="run(() => refreshProjects(selected.id))">刷新</button></div></div>
        <form v-if="isAdmin&&editingProject" class="edit-project-form" @submit.prevent="saveProject"><label>项目名称<input v-model="editName" required maxlength="100"></label><label>项目说明<textarea v-model="editDescription" maxlength="2000" rows="3"></textarea></label><div class="actions"><button type="button" class="ghost" @click="editingProject=false">取消</button><button class="primary" :disabled="busy">保存项目</button></div></form>
        <p v-if="!isAdmin" class="muted">你可以查看本项目成员与联系邮箱，成员和项目设置由管理员维护。</p>
        <template v-if="isAdmin">
          <p class="muted">添加已存在的本机账号。撤销成员后，其现有会话也将失去本项目访问权限。</p>
          <form class="member-form" @submit.prevent="addMember"><label>精确用户名<input v-model="memberSearch" required maxlength="64" placeholder="例如 demo_member"></label><label>项目角色<select v-model="memberRole"><option value="member">成员</option><option value="admin">管理员</option></select></label><button class="primary" :disabled="busy">添加 / 更新</button></form>
        </template>
        <div class="table-wrap"><table class="members-table"><thead><tr><th>成员</th><th>用户名</th><th>QQ邮箱</th><th>项目角色</th><th v-if="isAdmin">操作</th></tr></thead><tbody><tr v-for="member in members" :key="member.user.id"><td data-label="成员">{{ member.user.display_name }} <small v-if="member.user.id===user.id">（你）</small></td><td data-label="用户名">{{ member.user.username }}</td><td data-label="QQ邮箱"><a v-if="member.user.qq_email" :href="'mailto:'+member.user.qq_email">{{ member.user.qq_email }}</a><span v-else class="muted">未填写</span></td><td data-label="项目角色"><span :class="['badge',{admin:member.role==='admin'}]">{{ member.role==='admin'?'管理员':'成员' }}</span></td><td v-if="isAdmin" data-label="操作"><button class="danger-link" :disabled="busy" @click="removeMember(member)">撤销访问</button></td></tr></tbody></table></div>
        <p class="hint">联系邮箱由成员本人填写，未验证邮箱归属；点击邮箱会打开本机邮件应用，系统不会自动发送邮件。</p>
        <p v-if="isAdmin" class="hint">系统会保护最后一位管理员。请先添加另一位管理员，再撤销或降级原管理员。</p>
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
.auth-switch{display:block;margin:20px auto 0;border:0;background:none;color:#476890;font-size:13px;padding:8px}.auth-success{padding:12px 15px;border:1px solid #d5e5db;background:#f3faf6;border-radius:7px;color:#4d7b63;font-size:12px}.project-actions,.mobile-account-actions{display:flex;gap:8px;flex-shrink:0}.edit-project-form{padding:20px;background:#f7f9fc;border:1px solid #e2e9f1;border-radius:8px;margin:20px 0}.members-table a{color:#476b96}.profile-form{max-width:700px}.mobile-account-actions button{padding:8px;font-size:11px}@media(max-width:600px){.members-table{min-width:620px}.project-actions{flex-wrap:wrap;justify-content:flex-end}.project-actions button{font-size:11px;padding:8px}}
:root{font-family:Inter,"Segoe UI","Microsoft YaHei",sans-serif;color:#233343;background:#f5f6f8;font-synthesis:none;font-size:14px}*{box-sizing:border-box}body{margin:0}button,input,textarea,select{font:inherit}button{cursor:pointer}button:disabled{cursor:wait;opacity:.65}button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible{outline:3px solid #acbdec;outline-offset:2px}button{transition:background .15s,box-shadow .15s}h1,h2,p{margin-top:0}h1{font-size:32px;letter-spacing:-1px;margin-bottom:12px}h2{font-size:19px;margin-bottom:12px}p{line-height:1.8}small{font-size:12px}.brand{display:flex;align-items:center;gap:10px;font-size:23px;font-weight:750;letter-spacing:2px}.brand small{font-weight:400;font-size:12px;letter-spacing:0;color:#6c7b8c}.brand-icon{display:grid;place-items:center;width:36px;height:36px;color:white;background:#344f78;border-radius:10px;font-size:20px}.eyebrow{font-size:11px;font-weight:700;letter-spacing:2px;color:#6b7e97;margin-bottom:12px}.muted{color:#6d7b8c}.hint{font-size:12px;line-height:1.8;color:#788595;margin:18px 0 0}.primary{border:0;border-radius:7px;background:#344f78;color:white;padding:12px 18px;font-weight:600}.primary:hover{background:#253e63}.full{width:100%;margin-top:8px}.ghost{border:1px solid #dce1e7;background:white;padding:9px 14px;border-radius:7px;color:#53647b}.ghost:hover{background:#f4f6f9}.login-shell{min-height:100vh;display:grid;grid-template-columns:1.2fr 1fr;align-items:center;gap:70px;max-width:1180px;padding:70px 50px;margin:auto}.intro .brand{margin-bottom:78px}.intro h1{font-size:43px;line-height:1.45;letter-spacing:-1.5px}.intro-text{max-width:440px;color:#6a7a8c;font-size:15px}.principles{display:flex;gap:24px;margin-top:35px;color:#47607c;font-size:12px}.stage-note{font-size:12px;margin-top:55px;color:#8590a0}.login-card{background:white;border:1px solid #e4e7ed;border-radius:16px;padding:40px;box-shadow:0 15px 60px #26395208}.login-card h2{font-size:25px}.login-card form{margin-top:30px}label{display:block;font-size:12px;font-weight:600;color:#526176;margin-bottom:18px}input,textarea,select{display:block;width:100%;margin-top:8px;border:1px solid #d9dfe7;border-radius:7px;padding:12px;background:white;color:#233343}input::placeholder,textarea::placeholder{color:#9aa4b1}textarea{resize:vertical}.service-status{border-top:1px solid #edf0f4;margin-top:26px;padding-top:19px;font-size:12px;color:#7c8796;display:flex;align-items:center;gap:8px}.dot{height:7px;width:7px;border-radius:50%;background:#c89448}.dot.green{background:#5b9e80}.error{border:1px solid #f1d4d1;background:#fff4f2;color:#9f4037;padding:12px 16px;border-radius:7px;line-height:1.7;margin-bottom:20px}.error small{display:block;overflow-wrap:anywhere}.workspace{min-height:100vh;display:grid;grid-template-columns:225px 1fr}.workspace aside{background:#fff;border-right:1px solid #e5e8ed;padding:30px 20px;display:flex;flex-direction:column}.workspace aside .brand{padding-left:12px}.sidebar-label{font-size:11px;color:#97a0ad;letter-spacing:2px;margin:48px 14px 16px}.nav-active,.nav-future{padding:14px;border-radius:7px;font-size:13px;margin-bottom:8px}.nav-active{background:#edf1f7;color:#344f78;font-weight:700}.nav-future{color:#98a1af}.nav-future small{font-size:10px;margin-left:8px}.sidebar-bottom{margin-top:auto;padding:55px 14px 0;display:flex;flex-direction:column;gap:8px}.sidebar-bottom span{color:#8792a2;font-size:12px}.sidebar-bottom button{margin-top:12px}.workspace main{padding:48px 50px;max-width:1350px;width:100%;margin:auto}header,.section-heading{display:flex;align-items:center;justify-content:space-between;gap:24px}header{margin-bottom:24px}.project-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:20px;margin-bottom:30px}.project-card{border:1px solid #e1e5eb;background:white;border-radius:11px;padding:24px;text-align:left;color:inherit}.project-card:hover{box-shadow:0 6px 22px #2333430b}.project-card.selected{border:1px solid #6382ac;box-shadow:0 0 0 2px #6382ac16}.card-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px}.folder{color:#607c9f;font-size:30px}.badge{border-radius:5px;background:#f0f2f5;padding:5px 9px;font-size:11px;color:#798698}.badge.admin{background:#edf2fa;color:#4b6c99}.project-card h2{font-size:18px}.project-card p{color:#7a8795;min-height:48px;font-size:12px;overflow-wrap:anywhere}.card-bottom{font-size:11px;color:#6d809a}.panel{background:white;border:1px solid #e1e5eb;padding:27px;border-radius:11px;margin-bottom:25px}.panel .section-heading h2{margin-bottom:10px}.member-form{display:grid;grid-template-columns:1fr 155px auto;gap:16px;align-items:end;margin:24px 0}.member-form label{margin:0}.member-form button{height:44px}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap}th,td{text-align:left;padding:16px 12px;border-bottom:1px solid #edf0f4}th{background:#f9fafc;color:#8b97a6;font-size:11px;font-weight:500}.danger-link{border:0;background:transparent;color:#a95e56;font-size:12px;padding:5px 0}.actions{display:flex;justify-content:flex-end;gap:12px}.empty{text-align:center;padding:45px}footer{font-size:11px;color:#9aa3af;margin-top:35px;line-height:1.8}@media(max-width:900px){.login-shell{grid-template-columns:1fr;max-width:550px;gap:30px;padding:35px 20px}.intro .brand{margin-bottom:32px}.intro h1{font-size:30px}.principles,.stage-note{display:none}.workspace{grid-template-columns:1fr}.workspace aside{display:none}.workspace main{padding:28px 18px}.member-form{grid-template-columns:1fr}.login-card{padding:28px}header{align-items:flex-start}header h1{font-size:27px}}
</style>
<style>
.workspace aside{position:sticky;top:0;height:100vh;align-self:start}.desktop-nav{display:flex;flex-direction:column;gap:8px}.nav-link{border:0;background:transparent;text-align:left;border-radius:7px;padding:14px;color:#6d7b8c;font-size:13px}.nav-link:hover{background:#f4f6f9}.nav-link.nav-active{background:#edf1f7;color:#344f78;font-weight:700;margin-bottom:0}.project-context{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:24px;background:#edf1f6;border:1px solid #e0e6ee;border-radius:9px;padding:17px 20px}.project-context label{margin:0;min-width:0;flex:1;max-width:550px}.project-context select{background:#fff}.project-context>.badge{flex-shrink:0}.mobile-navigation{display:none}@media(max-width:900px){.workspace aside{display:none}.mobile-navigation{display:flex;gap:8px;overflow:auto;margin-bottom:22px;padding-bottom:4px}.mobile-navigation button{border:1px solid #dce3ed;color:#708299;background:#fff;padding:10px;border-radius:6px;font-size:12px;white-space:nowrap}.mobile-navigation button.active{background:#344f78;border-color:#344f78;color:#fff}.project-context{padding:14px;gap:10px}.project-context>.badge{font-size:10px}}
</style>
<style>
.university-lockup{display:flex;align-items:center;gap:17px;margin-bottom:32px}.university-emblem{width:72px;height:72px;object-fit:contain}.university-lockup strong{display:block;font-size:19px;letter-spacing:3px;color:#365f4e}.university-lockup span{display:block;font-size:11px;color:#829287;margin-top:9px;letter-spacing:1px}.intro .brand{margin-bottom:40px!important}.sidebar-emblem{width:42px;height:48px;object-fit:contain;flex-shrink:0}.workspace-brand{gap:10px!important;padding-left:2px!important}.workspace-brand small{display:block;font-size:9px!important;letter-spacing:0;margin-top:7px;white-space:nowrap}.mobile-identity{display:flex;align-items:center;gap:10px}.mobile-identity img{width:28px;height:32px;object-fit:contain}.desktop-nav{gap:3px!important}.desktop-nav .nav-link{padding:12px 14px}.sidebar-label{margin-top:30px!important}.mobile-navigation{flex-wrap:wrap!important;overflow:visible!important}.mobile-navigation button{flex:1 0 auto}@media(max-width:650px){.university-lockup{margin-bottom:24px}.university-emblem{width:54px;height:54px}.university-lockup strong{font-size:16px}.intro .brand{margin-bottom:24px!important}}
.mobile-session { display: none; }
.sidebar-bottom strong,.sidebar-bottom span,.project-card h2,.section-heading h2{overflow-wrap:anywhere}.section-heading>div:first-child{min-width:0}.mobile-identity{min-width:0;word-break:break-all;flex:1}
@media(max-width:600px){.members-table{min-width:0!important;white-space:normal}.members-table thead{display:none}.members-table tbody,.members-table tbody tr,.members-table td{display:block;width:100%}.members-table tbody tr{padding:12px 16px;margin:12px 0;border:1px solid #e4eaf1;border-radius:8px}.members-table td{padding:8px 0;border:0;overflow-wrap:anywhere;white-space:normal}.members-table td::before{content:attr(data-label);display:block;font-size:10px;color:#96a2b0;margin-bottom:7px}.members-table a{overflow-wrap:anywhere}}
header > button { flex-shrink: 0; white-space: nowrap; }
.nav-library { border: 0; background: transparent; color: #53647b; text-align: left; padding: 14px; font-size: 13px; border-radius: 7px; }
.nav-library:hover { background: #f4f6f9; }
.workspace { grid-template-columns: 225px minmax(0, 1fr); }
.workspace main { min-width: 0; }
@media (max-width: 900px) { .workspace { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 900px) { .mobile-session { display: flex; justify-content: space-between; align-items: center; margin-bottom: 22px; } }
</style>
