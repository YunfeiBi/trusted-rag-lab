<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { downloadFile, openSource } from './files'

const props = defineProps({ project: { type:Object, required:true }, api: { type:Function, required:true } })
const query = ref('')
const mode = ref('hybrid')
const topK = ref(5)
const rerank = ref(true)
const consent = ref(false)
const busy = ref(false)
const result = ref(null)
const error = ref('')
const errorTrace = ref('')
const history = ref([])
const historyError = ref('')
let alive = true
let validating = false
let timer

async function search() {
  if (busy.value) return
  busy.value = true; error.value = ''; errorTrace.value = ''; result.value = null
  try {
    const data = await props.api('/search',{ method:'POST',body:JSON.stringify({ project_id:props.project.id,query:query.value,mode:mode.value,top_k:topK.value,candidate_limit:20,use_rerank:rerank.value,allow_external_processing:consent.value }) })
    if (alive) { result.value = data; await loadHistory() }
  } catch (e) { if (alive) { error.value = e.message; errorTrace.value = e.traceId || '' } }
  finally { if (alive) busy.value = false }
}

async function validate() {
  if (!alive || busy.value || validating || !result.value?.results.length || document.hidden) return
  validating = true
  const previous = result.value
  try {
    const data = await props.api('/search/validate',{ method:'POST',body:JSON.stringify({project_id:props.project.id,chunk_ids:previous.results.map(r=>r.chunk_id)}) })
    if (!alive || result.value !== previous) return
    const allowed = new Set(data.valid_chunk_ids)
    const filtered = previous.results.filter(r=>allowed.has(r.chunk_id))
    if (filtered.length < previous.results.length) {
      previous.results = filtered
      previous.status = 'degraded'
      if (!previous.warnings.some(w=>w.code==='SCOPE_CHANGED')) previous.warnings.push({code:'SCOPE_CHANGED',message:'部分资料或版本已变化，已清除失效结果，请重新搜索。'})
    }
  } catch (e) {
    if (alive && result.value===previous) { result.value = null; error.value = e.message }
  } finally { validating = false }
}

async function loadHistory() {
  try {
    const rows = await props.api(`/projects/${props.project.id}/search-history`)
    if (alive) { history.value = rows; historyError.value = '' }
  } catch (e) {
    if (alive) { history.value = []; historyError.value = e.message }
  }
}

function preview(item) {
  try { openSource(props.api, item) } catch (e) { error.value = e.message }
}

async function download(item) {
  try { await downloadFile(props.api, item) } catch (e) { error.value = e.message }
}

onMounted(() => { loadHistory(); timer = setInterval(validate,5000) })
onUnmounted(() => { alive = false; clearInterval(timer) })
</script>

<template>
  <section id="evidence-search" class="panel search-panel">
    <p class="eyebrow">EVIDENCE SEARCH</p><h2>在项目资料中寻找依据</h2><p class="muted">仅检索“{{ project.name }}”中你有权访问的当前可用版本。在这里核对证据片段，需要生成回答时请进入知识问答页面。</p>
    <form class="search-form" @submit.prevent="search">
      <label>检索问题<input v-model="query" class="search-query" required maxlength="500" placeholder="例如：项目资料通过什么方式控制访问权限？"></label>
      <div class="search-options"><label>检索方式<select v-model="mode" :disabled="busy"><option value="hybrid">混合检索</option><option value="vector">纯向量检索</option><option value="keyword">关键词检索（本地）</option></select></label><label>返回片段<select v-model.number="topK" :disabled="busy"><option :value="3">3 个</option><option :value="5">5 个</option><option :value="10">10 个</option></select></label><label class="option-check"><input v-model="rerank" type="checkbox" :disabled="mode !== 'hybrid' || busy">启用重排序</label></div>
      <p v-if="mode === 'keyword'" class="hint">关键词模式按片段正文在本机匹配，不调用向量或重排序服务；只出现在文件名中的词可能无法命中。</p>
      <label v-else class="search-consent"><input v-model="consent" type="checkbox" :required="mode !== 'keyword'">允许将问题发送至向量服务；启用重排序时，会将授权候选片段发送至重排序服务。</label>
      <button class="primary search-submit" :disabled="busy || !query.trim() || (mode !== 'keyword' && !consent)">{{ busy ? '正在检索…' : '检索资料 →' }}</button>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}<small v-if="errorTrace">追踪编号：{{ errorTrace }}</small></p>
    <details class="search-history">
      <summary>我的检索历史</summary>
      <p v-if="historyError" class="error">{{ historyError }}</p>
      <p v-else-if="!history.length" class="hint">暂无检索记录。</p>
      <button v-for="item in history" :key="item.id" type="button" class="ghost" :disabled="busy" @click="query=item.query">
        {{ item.query }} <small>{{ new Date(item.created_at).toLocaleString('zh-CN') }}</small>
      </button>
      <p v-if="history.length" class="hint">点击一条记录填回检索框。</p>
    </details>
    <template v-if="result">
      <div v-for="warning in result.warnings" :key="warning.code" class="search-warning">{{ warning.message }}</div>
      <div class="result-heading"><h3>{{ result.results.length ? `检索到 ${result.results.length} 个证据片段` : '暂无可展示的证据片段' }}</h3><span>{{ result.timings_ms.total }} ms</span></div>
      <p class="hint">相似度、融合与重排分数用于排序，不代表答案正确率，请对照原文核验。</p>
      <article v-for="item in result.results" :key="item.chunk_id" class="evidence-card">
        <div class="evidence-title"><div><span class="evidence-id">{{ item.evidence_id }}</span><strong>{{ item.title }}</strong><span class="badge">v{{ item.version_number }}</span></div><div><button class="ghost source-button" @click="preview(item)">查看原文 ↗</button><button class="ghost source-button" @click="download(item)">下载文件</button></div></div>
        <p class="evidence-position">{{ item.page ? `第 ${item.page} 页` : `第 ${item.line_start}—${item.line_end} 行` }} {{ item.section ? `· ${item.section}` : '' }} · {{ item.filename }}</p>
        <pre>{{ item.text }}</pre><div class="evidence-meta"><span>{{ item.channels.map(c=>c==='keyword'?'关键词':'向量').join(' ＋ ') }}召回</span><span v-if="item.scores.rerank_score != null">重排分数 {{ item.scores.rerank_score }}</span><span v-else-if="item.scores.vector_similarity != null">向量相似度 {{ item.scores.vector_similarity }}</span></div>
      </article>
      <details class="search-details"><summary>查看本次检索过程</summary><p>关键词召回 {{ result.candidates.keyword }} · 向量召回 {{ result.candidates.vector }} · 融合 {{ result.candidates.fused }} · 过滤失效 {{ result.candidates.filtered }}</p><p>实际召回方式：{{ result.effective_mode }} · 重排序状态：{{ result.rerank_status }}</p><p v-for="(value,key) in result.timings_ms" :key="key">{{ key }}：{{ value }} ms</p><p class="trace">追踪编号：{{ result.trace_id }}</p></details>
    </template>
  </section>
</template>

<style scoped>
.search-panel{scroll-margin-top:24px}.search-form{padding:20px;background:#f8fafc;border:1px solid #e5eaf1;border-radius:8px;margin:23px 0}.search-options{display:flex;align-items:flex-end;gap:18px;flex-wrap:wrap}.search-options label:not(.option-check){min-width:145px}.option-check{display:flex;align-items:center;gap:8px;padding-bottom:12px;font-weight:400}.option-check input,.search-consent input{width:auto;flex-shrink:0;margin:0}.search-consent{display:flex;align-items:flex-start;gap:8px;font-weight:400;line-height:1.8;margin:8px 0 18px}.search-consent input{margin-top:5px}.search-submit{margin-top:6px}.search-warning{padding:13px 16px;margin-bottom:12px;border:1px solid #e7dcc6;background:#fcf9f2;color:#897144;font-size:12px;line-height:1.8;border-radius:6px}.result-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-top:25px}.result-heading h3{margin:0;font-size:16px}.result-heading>span{color:#8b98a7;font-size:12px;flex-shrink:0}.evidence-card{border:1px solid #e2e8f0;border-radius:8px;padding:20px;margin-top:16px}.evidence-title{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}.evidence-title>div{display:flex;flex-wrap:wrap;align-items:center;gap:10px;min-width:0}.evidence-title strong{overflow-wrap:anywhere}.evidence-id{font-size:11px;font-weight:700;color:#57749a;background:#edf2fa;padding:5px 7px;border-radius:5px}.source-button{flex-shrink:0}.evidence-position{font-size:11px;color:#8a98a9;margin:12px 0}.evidence-card pre{font:13px/1.9 inherit;white-space:pre-wrap;overflow-wrap:anywhere;max-height:270px;overflow:auto;margin-bottom:16px}.evidence-meta{display:flex;flex-wrap:wrap;gap:20px;color:#8c99a7;font-size:11px;border-top:1px solid #edf0f4;padding-top:12px}.search-details{margin-top:24px;color:#7c8da2;font-size:11px}.search-details summary{cursor:pointer;font-size:12px}.search-details p{margin:10px 0}.trace{overflow-wrap:anywhere}@media(max-width:600px){.search-panel{padding:20px}.search-form{padding:16px}.search-options{gap:12px}.search-options label:not(.option-check){min-width:120px;flex:1}.evidence-card{padding:14px}.evidence-title{gap:8px}.source-button{padding:8px;font-size:11px}}
</style>
<style scoped>
.evidence-card pre { font-family: inherit; font-size: 13px; line-height: 1.9; }
</style>

<style scoped>
.search-history summary{cursor:pointer}.search-history button{display:block;margin:8px 0;max-width:100%;text-align:left;overflow-wrap:anywhere}.search-history small{display:block;color:#69798a}
</style>
