<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import SourcePreview from './SourcePreview.vue'

const props = defineProps({ project:{type:Object,required:true},api:{type:Function,required:true} })
const question = ref('')
const mode = ref('hybrid')
const consent = ref(false)
const busy = ref(false)
const error = ref('')
const errorTrace = ref('')
const record = ref(null)
const history = ref([])
const source = ref(null)
let alive = true
let polling = false
let timer
const statuses = {answered:'已生成引用回答',insufficient_evidence:'依据不足',generation_unavailable:'生成服务暂不可用',invalid_citations:'生成内容校验未通过',evidence_changed:'依据已变化'}
const visibleWarnings = computed(() => {
  const seen = new Set(record.value?.status === 'answered' ? [] : [record.value?.answer])
  return (record.value?.details.warnings || []).filter(warning => {
    if (seen.has(warning.message)) return false
    seen.add(warning.message)
    return true
  })
})

async function loadHistory() {
  const data = await props.api('/questions?project_id='+props.project.id)
  if (alive) history.value = data
}

async function run(action) {
  if (busy.value) return
  busy.value = true; error.value = ''; errorTrace.value = ''
  try { await action() } catch (e) { if (alive) { error.value = e.message; errorTrace.value = e.traceId || '' } }
  finally { if (alive) busy.value = false }
}

function ask() {
  return run(async () => {
    record.value = null; source.value = null
    const data = await props.api('/questions',{method:'POST',body:JSON.stringify({project_id:props.project.id,question:question.value,retrieval_mode:mode.value,allow_external_processing:consent.value})})
    if (alive) record.value = data
    await loadHistory()
  })
}

function openRecord(item) {
  return run(async () => {
    source.value = null
    const data = await props.api('/questions/'+item.id)
    if (alive) { record.value = data; question.value = data.question }
  })
}

function openCitation(id) {
  source.value = record.value?.evidence.find(e=>e.evidence_id===id) || null
}

function time(value) {
  return new Date(value).toLocaleString('zh-CN',{hour12:false})
}

onMounted(async () => {
  await run(loadHistory)
  if (!alive) return
  timer = setInterval(async () => {
    if (!alive || busy.value || polling || document.hidden) return
    polling = true
    const previous = record.value
    try {
      await loadHistory()
      if (previous) {
        const current = await props.api('/questions/'+previous.id)
        if (alive && record.value===previous) {
          record.value = current
          if (!current.evidence.some(e=>e.chunk_id===source.value?.chunk_id)) source.value = null
        }
      }
    } catch (e) { if (alive) { record.value = null; source.value = null; history.value = []; error.value = e.message } }
    finally { polling = false }
  },7000)
})
onUnmounted(() => { alive = false; clearInterval(timer) })
</script>

<template>
  <div class="qa-layout">
    <section class="panel question-panel">
      <p class="eyebrow">ASK WITH EVIDENCE</p><h2>向项目资料提问</h2><p class="muted">根据“{{ project.name }}”的当前可用资料回答。每次独立检索，不将旧回答当作新问题的证据。</p>
      <form class="question-form" @submit.prevent="ask"><label>你的问题<textarea v-model="question" class="question-input" required maxlength="500" rows="3" placeholder="例如：项目管理员怎样调整成员访问权限？" /></label><label>证据检索方式<select v-model="mode" :disabled="busy"><option value="hybrid">混合检索与重排序</option><option value="keyword">关键词检索</option><option value="vector">纯向量检索</option></select></label><label class="qa-consent"><input v-model="consent" type="checkbox" required>允许将问题和有效证据发送至已配置的生成服务，以及所选的向量和重排序服务。</label><button class="primary ask-submit" :disabled="busy || !question.trim() || !consent">{{ busy ? '正在检索与生成…' : '生成引用回答 →' }}</button></form>
      <p v-if="error" class="error" role="alert">{{ error }}<small v-if="errorTrace">追踪编号：{{ errorTrace }}</small></p>
      <section v-if="record" class="answer-block" :data-status="record.status">
        <div class="answer-heading"><h3>{{ statuses[record.status] }}</h3><span>{{ time(record.created_at) }}</span></div><p class="answered-question">{{ record.question }}</p>
        <p v-if="record.status==='answered'" class="verification-note">回答由大模型结合下方检索证据生成；引用匹配不等于事实正确，请核对原文是否支持结论。</p>
        <div v-if="record.status==='answered'" class="answer-claims"><p v-for="(claim,index) in record.claims" :key="index">{{ claim.text }} <button v-for="id in claim.evidence_ids" :key="id" class="citation-button" @click="openCitation(id)">[{{ id }}]</button></p></div>
        <p v-else class="qa-notice">{{ record.answer }}</p>
        <details v-if="record.citations.length" class="quote-details"><summary>查看引用摘录</summary><blockquote v-for="(citation,index) in record.citations" :key="index"><button class="citation-button" @click="openCitation(citation.evidence_id)">[{{ citation.evidence_id }}]</button> {{ citation.quote }}</blockquote></details>
        <div v-for="(warning,index) in visibleWarnings" :key="index" class="qa-warning">{{ warning.message }}</div>
        <section v-if="record.evidence.length" class="qa-evidence"><h3>本次可核对的证据</h3><article v-for="evidence in record.evidence" :key="evidence.chunk_id"><div><span class="badge">{{ evidence.evidence_id }}</span> <strong>{{ evidence.title }}</strong> <small>v{{ evidence.version_number }}</small></div><p>{{ evidence.page ? `第 ${evidence.page} 页` : `第 ${evidence.line_start}—${evidence.line_end} 行` }} {{ evidence.section ? `· ${evidence.section}` : '' }}</p><button class="ghost" @click="source = evidence">查看原文</button></article></section>
        <details class="qa-details"><summary>查看生成与检索记录</summary><p>模型：{{ record.details.chat_model }} · 提示版本：{{ record.details.prompt_version }}</p><p>检索：{{ record.details.retrieval?.effective_mode }} · 重排：{{ record.details.retrieval?.rerank_status }}</p><p>生成耗时：{{ record.details.generation_ms }} ms · 服务内总耗时：{{ record.details.total_ms }} ms</p><p>追踪编号：{{ record.trace_id }}</p></details>
      </section>
      <div v-else-if="!busy" class="qa-empty">回答将在这里展示，引用可直接打开对应原文。</div>
      <SourcePreview v-if="source" :evidence="source" :api="api" @close="source = null" />
    </section>
    <aside class="panel question-history"><div class="section-heading"><h2>我的问答记录</h2><button class="ghost" :disabled="busy" @click="run(loadHistory)">刷新</button></div><p class="hint">仅展示你在当前项目的最近20条记录，其他成员不能查看。依据失效时隐藏历史回答。</p><p v-if="!history.length" class="muted history-empty">暂无记录</p><button v-for="item in history" :key="item.id" :class="['history-item',{selected:record?.id===item.id}]" :disabled="busy" @click="openRecord(item)"><strong>{{ item.question }}</strong><span>{{ statuses[item.status] }}</span><small>{{ time(item.created_at) }}</small></button></aside>
  </div>
</template>

<style scoped>
.qa-layout{display:grid;grid-template-columns:minmax(0,1fr) 270px;gap:22px;align-items:start}.question-panel,.question-history{min-width:0}.question-history{position:static!important;height:auto!important;display:block!important;padding:22px!important;border:1px solid #e1e5eb!important;background:#fff!important}.question-history h2{font-size:15px;margin:0}.question-history .section-heading{gap:8px}.question-history .ghost{padding:7px 10px;font-size:11px}.question-form{background:#f8fafc;border:1px solid #e4eaf2;border-radius:8px;padding:20px;margin:24px 0}.question-form textarea{line-height:1.8}.qa-consent{display:flex;gap:8px;align-items:flex-start;font-weight:400;line-height:1.8}.qa-consent input{width:auto;flex-shrink:0;margin-top:5px}.answer-block{border-top:1px solid #e5eaf1;padding-top:24px}.answer-heading{display:flex;align-items:center;justify-content:space-between;gap:16px}.answer-heading h3{font-size:16px}.answer-heading>span{font-size:11px;color:#97a4b2}.answered-question{font-size:12px;color:#708299;overflow-wrap:anywhere}.verification-note{font-size:11px;color:#8a98a9;border-left:2px solid #c8d5e7;padding-left:12px}.answer-claims{font-size:14px;line-height:2;overflow-wrap:anywhere;margin:24px 0}.citation-button{border:0;border-radius:4px;background:#eaf0f9;color:#41658e;padding:3px 6px;margin:0 2px;font-size:12px}.qa-notice,.qa-warning{background:#fbf8f1;border:1px solid #e9e0cd;color:#8c744a;border-radius:6px;padding:13px;font-size:12px;line-height:1.8}.qa-warning{margin-top:12px}.quote-details{font-size:12px;color:#718399;margin:20px 0}.quote-details summary,.qa-details summary{cursor:pointer}.quote-details blockquote{margin:14px 0;border-left:2px solid #d8e2ee;padding:8px 12px;background:#f8fafc;line-height:1.8;overflow-wrap:anywhere}.qa-evidence{margin-top:26px}.qa-evidence h3{font-size:14px}.qa-evidence article{border:1px solid #e5ebf2;border-radius:6px;padding:16px;margin:12px 0;overflow-wrap:anywhere}.qa-evidence article>div{font-size:12px}.qa-evidence article p{font-size:11px;color:#95a1b1;margin:12px 0}.qa-evidence article .ghost{padding:7px 10px;font-size:11px}.qa-details{font-size:11px;color:#8898aa;margin-top:22px;overflow-wrap:anywhere}.qa-details p{margin:12px 0}.qa-empty{padding:30px 12px;text-align:center;color:#9ba6b4;font-size:12px}.history-item{display:flex;flex-direction:column;gap:9px;text-align:left;border:1px solid #e6ebf2;background:white;padding:14px;border-radius:7px;width:100%;margin-top:12px}.history-item strong{font-size:12px;font-weight:500;line-height:1.8;overflow-wrap:anywhere}.history-item span{font-size:10px;color:#7087a3}.history-item small{font-size:10px;color:#a0acba}.history-item.selected{background:#f4f7fb;border-color:#a9bbd3}.history-empty{padding:25px 0;text-align:center;font-size:12px}@media(max-width:1100px){.qa-layout{grid-template-columns:minmax(0,1fr)}.question-history{max-height:430px;overflow:auto}}@media(max-width:600px){.question-panel{padding:20px}.question-form{padding:16px}.answer-heading{display:block}.answer-heading>span{display:block;margin-bottom:16px}}
</style>
