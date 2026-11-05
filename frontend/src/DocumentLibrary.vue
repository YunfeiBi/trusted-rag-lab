<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import FormDialog from './FormDialog.vue'
import { openSource } from './files'

const props = defineProps({ project:Object, api:Function })
const documents = ref([])
const error = ref('')
const formError = ref('')
const limits = ref(null)
const runtime = ref(null)
const localTime = value => new Date(value).toLocaleString('zh-CN', {hour12:false})
const notice = ref('')
const busy = ref(false)
const dialog = ref('')
const target = ref(null)
const files = ref([])
const passwords = ref([])
const consent = ref(false)
const history = ref([])
const term = ref('')
const filter = ref('')
const offset = ref(0)
const admin = computed(() => props.project.role === 'admin')
const statuses = { queued:'等待处理', processing:'处理中', ready:'已就绪', failed:'处理失败', cancelled:'已取消' }
const actionLabels = { withdraw:'撤下资料', restore:'恢复访问', delete:'删除资料', retry:'重试处理' }
const reasons = {
  INVALID_UTF8:'文本需使用 UTF-8 编码', INVALID_OFFICE:'Office 文件损坏、加密或超出解包限制',
  NO_TEXT:'未提取到可索引文字', INVALID_PDF:'PDF 无法解析', TEXT_TOO_LARGE:'提取文本超过上限',
  TOO_MANY_CHUNKS:'片段数量超过上限', PROCESSING_TIMEOUT:'处理超时',
}
let alive = true
let requestNumber = 0
let timer

async function load() {
  const request = ++requestNumber
  const params = new URLSearchParams({ q:filter.value, offset:offset.value })
  try {
    const [result, health] = await Promise.all([
      props.api(`/projects/${props.project.id}/documents?${params}`),
      props.api(`/projects/${props.project.id}/ingestion-status`),
    ])
    if (alive && request === requestNumber) {
      documents.value = result
      runtime.value = health
      error.value = ''
    }
  } catch (e) {
    if (alive && request === requestNumber) {
      documents.value = []
      runtime.value = null
      error.value = e.message
    }
  }
}

function search() {
  offset.value = 0
  filter.value = term.value.trim()
  error.value = ''
  return load()
}

function changePage(step) {
  offset.value += step
  return load()
}

function openDialog(kind, doc = null) {
  target.value = doc
  files.value = []
  passwords.value = []
  consent.value = false
  formError.value = ''
  notice.value = ''
  history.value = []
  dialog.value = kind
  if (kind === 'history') loadHistory(doc)
}

function close() {
  dialog.value = ''
  target.value = null
  passwords.value = []
  files.value = []
}

async function loadHistory(doc) {
  try {
    const versions = await props.api(`/documents/${doc.id}/versions`)
    if (alive && dialog.value === 'history' && target.value?.id === doc.id) history.value = versions
  } catch (e) {
    if (alive && dialog.value === 'history' && target.value?.id === doc.id) formError.value = e.message
  }
}

function selectFiles(event) {
  files.value = Array.from(event.target.files || [])
  passwords.value = []
  formError.value = files.value.length > 20 ? '一次最多选择 20 份文件。' : ''
}

async function upload() {
  if (busy.value || !consent.value || !files.value.length || files.value.length > 20) return
  busy.value = true
  formError.value = ''
  const batch = files.value
  const doc = target.value
  const failed = []
  const messages = []
  let succeeded = 0
  try {
    for (const [index, file] of batch.entries()) {
      if (!alive) break
      const form = new FormData()
      form.append('file', file)
      form.append('allow_external_processing', 'true')
      if (!doc) form.append('project_id', props.project.id)
      if (passwords.value[index]) form.append('pdf_password', passwords.value[index])
      try {
        await props.api(doc ? `/documents/${doc.id}/versions` : '/documents', { method:'POST', body:form })
        succeeded++
      } catch (e) {
        failed.push(file)
        messages.push(`${file.name}：${e.message}`)
      }
    }
    if (!alive) return
    passwords.value = []
    notice.value = succeeded ? `已提交 ${succeeded} 份文件，等待后台处理。` : ''
    if (failed.length) {
      files.value = failed
      formError.value = messages.join('\n')
    } else close()
    await load()
  } finally {
    passwords.value = []
    busy.value = false
  }
}

async function confirm() {
  if (busy.value) return
  busy.value = true
  formError.value = ''
  const kind = dialog.value
  const doc = target.value
  const path = kind === 'retry' ? `/tasks/${doc.latest_version.task.id}/retry` :
    `/documents/${doc.id}${kind === 'delete' ? '' : '/' + kind}`
  try {
    await props.api(path, { method:kind === 'delete' ? 'DELETE' : 'POST' })
    close()
    await load()
    if (offset.value && !documents.value.length) await changePage(-100)
  } catch (e) {
    formError.value = e.message
  } finally {
    busy.value = false
  }
}

function preview(version) {
  try {
    openSource(props.api, { ...version, document_id:target.value.id, version_id:version.id })
  } catch (e) { formError.value = e.message }
}

onMounted(async () => {
  await load()
  if (!alive) return
  try { limits.value = await props.api('/file-limits') } catch (e) { error.value = e.message }
  if (alive) timer = setInterval(() => { if (!busy.value && !document.hidden) load() }, 3000)
})
onUnmounted(() => { alive = false; clearInterval(timer); passwords.value = [] })
</script>

<template>
  <section id="document-library" class="panel document-library">
    <div class="section-heading">
      <div><p class="eyebrow">DOCUMENTS &amp; VERSIONS</p><h2>{{ project.is_team_library ? '团队公共资料库' : '项目资料库' }}</h2></div>
      <button v-if="admin" class="primary upload-button" @click="openDialog('upload')">上传新资料</button>
    </div>
    <div v-if="runtime" :class="['worker-status', { warning:runtime.worker_status!=='recent_heartbeat' }]" :data-worker-status="runtime.worker_status" role="status"><strong>{{ runtime.worker_status==='recent_heartbeat' ? '入库服务最近有心跳' : '暂未检测到入库服务的近期心跳' }}</strong><p>{{ runtime.worker_status==='recent_heartbeat' ? '这表示最近有 Worker 活动，不保证单个任务已完成。' : `最近${runtime.freshness_seconds}秒没有可用心跳；请检查 Worker 是否已启动、数据库是否可连接。长时间解析也可能延迟心跳。` }}</p><span>本项目任务：等待 {{ runtime.tasks.queued }} · 处理中 {{ runtime.tasks.processing }} · 失败 {{ runtime.tasks.failed }}</span><small v-if="runtime.oldest_pending_at">最早待完成任务：{{ localTime(runtime.oldest_pending_at) }}</small></div>
    <form class="file-search" @submit.prevent="search">
      <label>按文件名搜索<input v-model="term" type="search" maxlength="200" placeholder="输入文件名的一部分" @search="search"></label>
      <button class="ghost">搜索</button>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="notice" class="hint" role="status">{{ notice }}</p>
    <div v-if="!documents.length" class="docs-empty">{{ filter ? '没有匹配的文件。' : '当前没有可展示的资料。' }}</div>
    <div v-else class="table-wrap"><table class="documents-table">
      <thead><tr><th>资料</th><th>最近上传者</th><th>最新版本</th><th>当前可用</th><th>操作</th></tr></thead>
      <tbody><tr v-for="doc in documents" :key="doc.id">
        <td><strong>{{ doc.title }}</strong><small class="file-meta">{{ doc.latest_version?.filename }}</small></td>
        <td>{{ doc.latest_version?.uploader || '未知' }}</td>
        <td><span class="badge">{{ !doc.is_active ? '已撤下' : `v${doc.latest_version?.number} · ${statuses[doc.latest_version?.status] || '未知'}` }}</span>
          <small v-if="doc.is_active && doc.latest_version?.task?.retry_at" class="file-meta">重试不早于 {{ localTime(doc.latest_version.task.retry_at) }}</small>
          <small v-if="doc.is_active && doc.latest_version?.task?.lease_until" class="file-meta">处理中断后可于 {{ localTime(doc.latest_version.task.lease_until) }} 重新领取</small>
          <small v-if="doc.is_active && doc.latest_version?.task?.error_code" class="task-error">{{ reasons[doc.latest_version.task.error_code] || doc.latest_version.task.error_code }}</small>
        </td>
        <td>{{ !doc.is_active ? '已停止访问' : doc.current_version_id ? `v${doc.current_version_number} 正在使用` : '尚无可用版本' }}</td>
        <td><div class="doc-actions">
          <template v-if="doc.is_active">
            <button class="text-button" @click="openDialog('history', doc)">版本与原文</button>
            <template v-if="admin">
              <button class="text-button" @click="openDialog('upload', doc)">更新</button>
              <button v-if="doc.latest_version?.task?.status === 'failed'" class="text-button" @click="openDialog('retry', doc)">重试</button>
              <button class="danger-link" @click="openDialog('withdraw', doc)">撤下</button>
            </template>
          </template>
          <template v-else-if="admin">
            <button class="text-button" @click="openDialog('restore', doc)">恢复访问</button>
            <button class="danger-link" @click="openDialog('delete', doc)">删除</button>
          </template>
        </div></td>
      </tr></tbody>
    </table></div>
    <div v-if="offset || documents.length === 100" class="center-pagination">
      <button class="ghost" :disabled="!offset" @click="changePage(-100)">上一页</button>
      <button class="ghost" :disabled="documents.length < 100" @click="changePage(100)">下一页</button>
    </div>

    <FormDialog v-if="dialog === 'upload'" id="upload-dialog" :title="target ? '上传新版本' : '上传新资料'" :busy="busy" @close="close">
      <form class="upload-area" @submit.prevent="upload">
        <fieldset class="dialog-fields" :disabled="busy">
          <p v-if="target" class="muted">{{ target.title }}</p>
          <label>选择文件<input type="file" accept=".pdf,.md,.txt,.csv,.docx,.xlsx,.pptx" :multiple="!target" @change="selectFiles"></label>
          <p class="hint">支持 PDF、Word、Excel、PowerPoint、Markdown、TXT 和 CSV。一次最多 20 份，每份上限 {{ limits ? limits.max_upload_bytes/1048576 : '…' }} MB。</p>
          <div v-if="files.length <= 20" class="upload-files">
            <div v-for="(file,index) in files" :key="index">
              <strong>{{ file.name }}</strong>
              <label v-if="file.name.toLowerCase().endsWith('.pdf')">PDF 密码（仅加密文件填写）<input v-model="passwords[index]" class="pdf-password" type="password" autocomplete="off" maxlength="256"></label>
            </div>
          </div>
          <p v-if="files.some(file => file.name.toLowerCase().endsWith('.pdf'))" class="hint">加密 PDF 在本机解密后保存可访问副本，密码在提交后清空。</p>
          <label class="consent"><input v-model="consent" type="checkbox" required>我有权上传这些资料，并允许将提取文本发送至已配置的向量服务。</label>
          <p v-if="formError" class="error upload-errors" role="alert">{{ formError }}</p>
          <p v-if="notice" class="hint">{{ notice }} 可重试剩余失败文件。</p>
          <div class="actions"><button type="button" class="ghost" @click="close">取消</button><button class="primary" :disabled="!files.length || files.length > 20 || !consent">{{ busy ? '正在提交…' : '上传并入库' }}</button></div>
        </fieldset>
      </form>
    </FormDialog>

    <FormDialog v-else-if="dialog === 'history'" id="version-dialog" title="版本与原文" @close="close">
      <p class="muted">{{ target.title }}</p>
      <p v-if="formError" class="error" role="alert">{{ formError }}</p>
      <div class="version-panel">
        <div v-for="version in history" :key="version.id" class="version-row">
          <div><strong>v{{ version.number }} · {{ statuses[version.status] }}</strong><small class="file-meta">{{ version.filename }} · {{ version.uploader || '未知上传者' }}</small></div>
          <button class="ghost" @click="preview(version)">查看原文 ↗</button>
        </div>
      </div>
    </FormDialog>

    <FormDialog v-else-if="dialog" id="document-action-dialog" :title="actionLabels[dialog]" :busy="busy" @close="close">
      <p v-if="dialog === 'withdraw'">撤下“{{ target.title }}”后，成员无法访问，未完成任务会停止。管理员可以恢复访问。</p>
      <p v-else-if="dialog === 'restore'">恢复“{{ target.title }}”后，成员可再次访问已有版本。被撤下中断的最新版本会重新排队。</p>
      <p v-else-if="dialog === 'delete'">删除“{{ target.title }}”后，资料会从列表移除，无法在系统内恢复。后台保留文件和审计记录。</p>
      <p v-else>重新处理“{{ target.title }}”的最新版本。</p>
      <p v-if="formError" class="error" role="alert">{{ formError }}</p>
      <div class="actions"><button class="ghost" :disabled="busy" @click="close">取消</button><button class="primary" :disabled="busy" @click="confirm">{{ actionLabels[dialog] }}</button></div>
    </FormDialog>
  </section>
</template>

<style scoped>
.worker-status{border:1px solid #d9e7df;background:#f5faf7;border-radius:7px;padding:15px 18px;font-size:12px;color:#537565;margin:20px 0;overflow-wrap:anywhere}.worker-status.warning{border-color:#eadfc5;background:#fffbf1;color:#8b7448}.worker-status p{margin:7px 0;font-size:11px}.worker-status span,.worker-status small{display:block;font-size:11px;line-height:1.9}
.file-search { display:flex; align-items:end; gap:12px; margin:20px 0 }
.file-search label { flex:1; max-width:420px; margin:0 }
.file-search button,.upload-button { flex-shrink:0 }
.upload-files { display:grid; gap:10px; max-height:240px; overflow:auto; overflow-wrap:anywhere }
.upload-files > div { padding:10px; background:#f5f7fa; border-radius:6px }
.upload-files label { margin:8px 0 0 }
.upload-errors { white-space:pre-wrap; overflow-wrap:anywhere }
.consent { display:flex; gap:8px; margin-top:20px }
.consent input { width:auto; margin:3px 0 0; flex-shrink:0 }
.docs-empty { padding:35px 12px; text-align:center; color:#69798a }
.file-meta { display:block; margin-top:6px; color:#69798a; overflow-wrap:anywhere }
.documents-table { white-space:normal; min-width:660px }
.documents-table td { vertical-align:top; overflow-wrap:anywhere }
.task-error { display:block; color:#a04739; margin-top:6px }
.doc-actions { display:flex; gap:10px; flex-wrap:wrap }
.text-button { border:0; background:none; padding:5px 0; color:#43658c; font-size:12px }
.version-row { display:flex; justify-content:space-between; align-items:center; gap:14px; padding:14px 0; border-top:1px solid #e5ebf2 }
.version-row button { flex-shrink:0 }
</style>
