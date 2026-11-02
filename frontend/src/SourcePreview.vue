<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'

const props = defineProps({ evidence: { type: Object, required: true }, api: { type: Function, required: true } })
const emit = defineEmits(['close'])
const loading = ref(false)
const error = ref('')
const url = ref('')
const text = ref('')
const container = ref(null)
let generation = 0
const isPdf = computed(() => props.evidence.filename.toLowerCase().endsWith('.pdf'))
const lines = computed(() => text.value.slice(0,250000).split(/\r?\n/).slice(0,2000))

function clear() {
  if (url.value) URL.revokeObjectURL(url.value)
  url.value = ''; text.value = ''
}

watch(() => props.evidence, async evidence => {
  const current = ++generation
  clear(); error.value = ''; loading.value = true
  try {
    // Build path from identifiers; never navigate to an arbitrary server-returned URL.
    const path = `/documents/${encodeURIComponent(evidence.document_id)}/source?version_id=${encodeURIComponent(evidence.version_id)}`
    const blob = await props.api(path,{},'blob')
    if (current !== generation) return
    url.value = URL.createObjectURL(blob)
    if (!isPdf.value) {
      const content = await blob.text()
      if (current !== generation) return
      text.value = content
      await nextTick()
      container.value?.querySelector('.focused-line')?.scrollIntoView({ block:'nearest' })
    }
  } catch (e) { if (current===generation) { clear(); error.value = e.message } }
  finally { if (current===generation) loading.value = false }
}, { immediate:true })
onUnmounted(() => { generation++; clear() })
</script>

<template>
  <section class="source-preview">
    <div class="section-heading"><div><p class="eyebrow">SOURCE EVIDENCE</p><h3>{{ evidence.title }} · v{{ evidence.version_number }}</h3><p class="hint">{{ evidence.page ? `第 ${evidence.page} 页` : `第 ${evidence.line_start}—${evidence.line_end} 行` }} {{ evidence.section ? `· ${evidence.section}` : '' }}</p></div><button class="ghost" @click="emit('close')">关闭原文</button></div>
    <p v-if="loading" class="muted">正在重新验证权限并加载原文…</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template v-if="url">
      <p class="download"><a :href="url" :download="evidence.filename">下载原文件</a></p>
      <iframe v-if="isPdf" :src="url + (evidence.page ? `#page=${evidence.page}` : '')" title="证据PDF原文" />
      <template v-else><p class="hint">高亮对应段落行范围，原文以纯文本展示。预览最多2000行和25万字符，完整内容请下载。</p><div ref="container" class="source-lines"><div v-for="(line,index) in lines" :key="index" :class="['source-line', { 'focused-line': index+1 >= evidence.line_start && index+1 <= evidence.line_end }]"><span>{{ index+1 }}</span><code>{{ line || ' ' }}</code></div></div></template>
    </template>
  </section>
</template>

<style scoped>
.source-preview{border-top:1px solid #e4e9f0;margin-top:28px;padding-top:24px}.source-preview h3{margin:0;overflow-wrap:anywhere}.section-heading{align-items:flex-start}.section-heading>div{min-width:0}.section-heading button{flex-shrink:0}.source-lines{background:#f8fafc;border:1px solid #e5eaf1;border-radius:7px;padding:14px 0;max-height:420px;overflow:auto;margin-top:12px}.source-line{display:flex;padding:3px 12px;gap:16px;line-height:1.8;font-size:12px}.source-line span{min-width:30px;color:#a0adba;text-align:right;user-select:none}.source-line code{font-family:inherit;white-space:pre-wrap;overflow-wrap:anywhere;min-width:0}.focused-line{background:#e8f0fb}.download{text-align:right;font-size:12px}.download a{color:#43658c}iframe{border:1px solid #e4e9f0;width:100%;height:560px;border-radius:6px}
</style>
