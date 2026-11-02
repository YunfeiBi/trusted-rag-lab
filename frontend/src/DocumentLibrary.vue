<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'

const props = defineProps({ project: { type: Object, required: true }, api: { type: Function, required: true } })
const documents = ref([])
const error = ref('')
const busy = ref(false)
const file = ref(null)
const fileInput = ref(null)
const consent = ref(false)
const target = ref(null)
const history = ref([])
const selectedDocument = ref(null)
const sourceUrl = ref('')
const sourceText = ref('')
const sourceName = ref('')
const sourcePdf = ref(false)
const chunks = ref([])
const previewTitle = ref('')
let timer
let alive = true
let polling = false
const admin = computed(() => props.project.role === 'admin')
const statuses = { queued: '等待处理', processing: '处理中', ready: '已就绪', failed: '处理失败', cancelled: '已取消', succeeded: '已完成' }
const stages = { queued: '等待 Worker', parsing: '解析与分块', embedding: '生成向量', publishing: '发布索引', done: '入库完成', failed: '失败', cancelled: '取消' }
const reasons = { INVALID_UTF8: 'Markdown 需使用 UTF-8 编码', NO_TEXT: '未提取到文字，扫描件需另做 OCR', INVALID_PDF: 'PDF 无法解析', ENCRYPTED_PDF: '不支持加密 PDF', TEXT_TOO_LARGE: '提取文本超过20万字符', TOO_MANY_PAGES: 'PDF 超过200页', PDF_PAGE_TOO_LARGE: 'PDF 单页内容流过大', EMBEDDING_UNAVAILABLE: '向量服务暂不可用', EMBEDDING_HTTP_ERROR: '向量服务返回错误', EMBEDDING_INVALID_RESPONSE: '向量返回格式或维度不匹配', MODEL_NOT_CONFIGURED: '向量服务未配置', LEASE_EXHAUSTED: 'Worker 多次超时，可手动重试', FILE_NOT_FOUND: '原文件不存在', FILE_CHANGED: '原文件校验失败' }

function clearSource() {
  if (sourceUrl.value) URL.revokeObjectURL(sourceUrl.value)
  sourceUrl.value = ''; sourceText.value = ''; sourceName.value = ''; chunks.value = []; previewTitle.value = ''
}

function clearDetails() {
  clearSource(); history.value = []; selectedDocument.value = null
}

async function load() {
  const result = await props.api(`/projects/${props.project.id}/documents`)
  if (!alive) return
  documents.value = result
  if (selectedDocument.value && !result.some(d => d.id === selectedDocument.value.id && d.is_active)) clearDetails()
  if (selectedDocument.value) {
    const id = selectedDocument.value.id
    const versions = await props.api(`/documents/${id}/versions`)
    if (alive && selectedDocument.value?.id === id) history.value = versions
  }
}

async function run(action) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await action() } catch (e) { if (alive) error.value = e.message }
  finally { if (alive) busy.value = false }
}

function upload() {
  return run(async () => {
    if (!file.value || !consent.value) throw new Error('请选择文件并确认资料授权与向量服务处理')
    const form = new FormData()
    form.append('file', file.value)
    form.append('allow_external_processing', 'true')
    if (!target.value) form.append('project_id', props.project.id)
    const path = target.value ? `/documents/${target.value.id}/versions` : '/documents'
    await props.api(path, { method: 'POST', body: form })
    file.value = null; consent.value = false; target.value = null
    if (fileInput.value) fileInput.value.value = ''
    clearDetails()
    await load()
  })
}

function chooseVersion(document) {
  target.value = document; consent.value = false
  fileInput.value?.focus()
}

async function loadHistory(document) {
  const result = await props.api(`/documents/${document.id}/versions`)
  if (!alive) return
  clearSource(); selectedDocument.value = document; history.value = result
}

async function viewSource(version) {
  const current = selectedDocument.value
  const blob = await props.api(`/documents/${current.id}/source?version_id=${version.id}`, {}, 'blob')
  if (!alive || selectedDocument.value?.id !== current.id) return
  clearSource()
  sourceName.value = version.filename
  sourcePdf.value = version.filename.toLowerCase().endsWith('.pdf')
  sourceUrl.value = URL.createObjectURL(blob)
  previewTitle.value = `${current.title} · v${version.number}`
  if (!sourcePdf.value) sourceText.value = await blob.text()
  if (version.status === 'ready') {
    const result = await props.api(`/documents/${current.id}/chunks?version_id=${version.id}&limit=30`)
    if (alive && selectedDocument.value?.id === current.id) chunks.value = result.items
  }
}

function withdraw(document) {
  if (!window.confirm(`撤下“${document.title}”？该资料将停止访问，未完成任务会取消。`)) return
  return run(async () => {
    await props.api(`/documents/${document.id}/withdraw`, { method: 'POST' })
    if (target.value?.id === document.id) target.value = null
    clearDetails(); await load()
  })
}

function retry(task) {
  return run(async () => { await props.api(`/tasks/${task.id}/retry`, { method: 'POST' }); clearDetails(); await load() })
}

onMounted(async () => {
  await run(load)
  timer = setInterval(async () => {
    if (polling || busy.value || document.hidden) return
    polling = true
    try { await load() } catch (e) { if (alive) { documents.value = []; clearDetails(); error.value = e.message } }
    finally { polling = false }
  }, 3000)
})
onUnmounted(() => { alive = false; clearInterval(timer); clearSource() })
</script>

<template>
  <section id="document-library" class="panel document-library">
    <div class="section-heading"><div><p class="eyebrow">DOCUMENTS & VERSIONS</p><h2>项目资料库</h2></div><button class="ghost" :disabled="busy" @click="run(load)">刷新资料</button></div>
    <p class="muted">上传、处理与版本记录在这里汇总。新版本完成后生效，构建期间继续使用原有可用版本。</p>
    <div v-if="error" class="error" role="alert">{{ error }}</div>
    <form v-if="admin" class="upload-area" @submit.prevent="upload">
      <div class="upload-heading"><strong>{{ target ? `更新：${target.title}` : '上传新资料' }}</strong><button v-if="target" type="button" class="text-button" @click="target = null">取消更新</button></div>
      <label>文件<input ref="fileInput" type="file" accept=".pdf,.md" :disabled="busy" @change="file = $event.target.files?.[0] || null"></label>
      <p class="hint">支持文本型 PDF、UTF-8 Markdown；文件不超过20MB，PDF不超过200页。暂不支持扫描件OCR及加密PDF。</p>
      <label class="consent"><input v-model="consent" type="checkbox" required>我有权上传此资料，允许将提取文本发送至已配置的向量服务。</label>
      <button class="primary" :disabled="busy || !file || !consent">{{ busy ? '正在提交…' : target ? '提交新版本' : '上传并入库' }}</button>
    </form>
    <p v-else class="hint">当前为普通成员，可查看本项目资料。上传和版本维护由项目管理员负责。</p>
    <div v-if="!documents.length" class="docs-empty">暂无可查看资料。{{ admin ? '上传一份公开或获授权资料开始。' : '请联系项目管理员添加资料。' }}</div>
    <div v-else class="table-wrap"><table class="documents-table"><thead><tr><th>资料</th><th>最新版本与处理</th><th>当前可用</th><th>操作</th></tr></thead><tbody>
      <tr v-for="doc in documents" :key="doc.id">
        <td><strong>{{ doc.title }}</strong><small class="file-meta">{{ doc.latest_version?.filename }} · {{ ((doc.latest_version?.file_size || 0)/1024).toFixed(1) }} KB</small></td>
        <td><span :class="['badge', { admin: doc.latest_version?.status === 'ready' }]">{{ !doc.is_active ? '已撤下' : `v${doc.latest_version?.number} · ${statuses[doc.latest_version?.status] || '未知'}` }}</span>
          <small v-if="doc.is_active" class="file-meta">{{ stages[doc.latest_version?.task?.stage] || '' }} · 已尝试 {{ doc.latest_version?.task?.attempts || 0 }} 次</small>
          <small v-if="doc.is_active && doc.latest_version?.task?.error_code" class="task-error">{{ reasons[doc.latest_version.task.error_code] || '处理异常，请联系管理员检查任务' }}</small>
        </td>
        <td>{{ !doc.is_active ? '已停止访问' : doc.current_version_id ? `v${doc.current_version_number} 正在使用` : '尚无可用版本' }}<small v-if="doc.latest_version?.status === 'ready'" class="file-meta">{{ doc.latest_version.chunk_count }} 个片段</small></td>
        <td><div v-if="doc.is_active" class="doc-actions"><button class="text-button" :disabled="busy" @click="run(() => loadHistory(doc))">版本与原文</button><template v-if="admin"><button class="text-button" :disabled="busy" @click="chooseVersion(doc)">更新</button><button v-if="doc.latest_version?.task?.status === 'failed'" class="text-button" :disabled="busy" @click="retry(doc.latest_version.task)">重试</button><button class="danger-link" :disabled="busy" @click="withdraw(doc)">撤下</button></template></div><span v-else class="muted">已停止访问</span></td>
      </tr>
    </tbody></table></div>
    <p class="hint">每3秒更新处理状态；列表最多显示最近100份资料。任务等待时请确认独立 Worker 已启动。</p>
    <section v-if="selectedDocument" class="version-panel">
      <div class="section-heading"><h3>{{ selectedDocument.title }} · 版本记录</h3><button class="ghost" @click="clearDetails">关闭</button></div>
      <div v-for="version in history" :key="version.id" class="version-row"><div><strong>v{{ version.number }}</strong> <span class="badge">{{ statuses[version.status] }}</span><small class="file-meta">{{ version.filename }} · {{ version.chunk_count }} 个片段 {{ version.embedding_model ? `· ${version.embedding_model}` : '' }}</small></div><button class="ghost" :disabled="busy" @click="run(() => viewSource(version))">查看原文</button></div>
    </section>
    <section v-if="sourceUrl" class="source-panel"><div class="section-heading"><h3>{{ previewTitle }}</h3><a :href="sourceUrl" :download="sourceName">下载原文件</a></div>
      <iframe v-if="sourcePdf" :src="sourceUrl" title="PDF原文" class="pdf-source" />
      <pre v-else class="markdown-source">{{ sourceText }}</pre>
      <h3 v-if="chunks.length">片段预览与原文位置</h3><p v-if="chunks.length" class="hint">显示前30个片段。Markdown位置为所在段落行范围，PDF为页码；长段落切分后的多个片段可能共享位置。</p>
      <article v-for="chunk in chunks" :key="chunk.id" class="chunk-card"><small>{{ chunk.page ? `第 ${chunk.page} 页` : `第 ${chunk.line_start}—${chunk.line_end} 行` }} {{ chunk.section ? `· ${chunk.section}` : '' }} · 片段 {{ chunk.ordinal+1 }}</small><pre>{{ chunk.text }}</pre></article>
    </section>
  </section>
</template>

<style scoped>
.document-library{scroll-margin-top:24px}.upload-area{background:#f8fafd;border:1px dashed #cbd5e2;border-radius:8px;padding:22px;margin:24px 0}.upload-heading{display:flex;justify-content:space-between;gap:16px;margin-bottom:15px}.upload-area input[type=file]{background:white}.consent{display:flex;gap:9px;align-items:flex-start;margin-top:20px;line-height:1.7;font-weight:400}.consent input{width:auto;margin-top:5px;flex-shrink:0}.docs-empty{padding:35px 16px;text-align:center;color:#8491a2}.file-meta{display:block;color:#8a96a5;font-size:11px;margin-top:8px;overflow-wrap:anywhere}.documents-table{white-space:normal}.documents-table th:first-child{width:28%}.documents-table td{vertical-align:top;min-width:140px;overflow-wrap:anywhere}.task-error{display:block;color:#a95e56;font-size:11px;margin-top:8px}.doc-actions{display:flex;gap:12px;flex-wrap:wrap}.text-button{border:0;background:transparent;padding:5px 0;color:#43658c;font-size:12px}.version-panel,.source-panel{border-top:1px solid #e4e9f0;padding-top:24px;margin-top:28px}.version-row{display:flex;justify-content:space-between;align-items:center;gap:20px;border-bottom:1px solid #edf0f4;padding:16px 0}.version-row strong{margin-right:10px}.pdf-source{width:100%;height:560px;border:1px solid #e1e5eb;border-radius:6px}.markdown-source,.chunk-card pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.8 "Microsoft YaHei",sans-serif}.markdown-source{background:#f8fafc;padding:20px;max-height:480px;overflow:auto;border-radius:6px}.chunk-card{padding:16px;background:#f8fafc;border:1px solid #edf0f4;border-radius:6px;margin-top:12px}.chunk-card small{color:#6b7e97}.chunk-card pre{margin-bottom:0}.source-panel a{color:#43658c;font-size:12px}@media(max-width:600px){.upload-area{padding:16px}.document-library{padding:20px}.version-row{align-items:flex-start}.documents-table{min-width:700px}}
</style>
