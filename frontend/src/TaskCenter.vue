<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
const props = defineProps({project:Object,api:Function})
const tasks=ref([]), status=ref(''), offset=ref(0), busy=ref(false), error=ref(''), trace=ref('')
const states={queued:'等待处理',processing:'处理中',succeeded:'已完成',failed:'失败',cancelled:'已取消'}
const stages={queued:'等待领取',parsing:'解析与分块',embedding:'生成向量',publishing:'发布索引',done:'处理完成',failed:'处理失败',cancelled:'已取消'}
let alive=true, timer, loading=false
const time=v=>v?new Date(v).toLocaleString('zh-CN',{hour12:false}):'—'
async function load(){if(loading)return;loading=true;try{const data=await props.api(`/projects/${props.project.id}/tasks?offset=${offset.value}&limit=30`+(status.value?'&status='+status.value:''));if(alive){tasks.value=data;error.value=''}}catch(e){if(alive){tasks.value=[];error.value=e.message;trace.value=e.traceId||''}}finally{loading=false}}
async function retry(task){busy.value=true;try{await props.api('/tasks/'+task.id+'/retry',{method:'POST'});await load()}catch(e){error.value=e.message;trace.value=e.traceId||''}finally{busy.value=false}}
async function page(step){offset.value=Math.max(0,offset.value+step);await load()}
onMounted(async()=>{await load();if(alive)timer=setInterval(()=>{if(!document.hidden&&!busy.value)load()},5000)})
onUnmounted(()=>{alive=false;clearInterval(timer)})
</script>
<template>
  <section class="panel task-center"><div class="section-heading"><div><p class="eyebrow">INGESTION QUEUE</p><h2>处理任务中心</h2></div><button class="ghost" @click="load">刷新</button></div><p class="muted">查看当前项目各版本的入库任务。新版本失败时，原有可用版本继续生效。</p>
    <label class="center-filter">任务状态<select v-model="status" @change="offset=0;load()"><option value="">全部状态</option><option v-for="(label,value) in states" :key="value" :value="value">{{ label }}</option></select></label>
    <p v-if="error" class="error">{{ error }}<small>{{ trace }}</small></p>
    <p v-if="!tasks.length&&!error" class="center-empty">当前筛选下暂无任务。</p>
    <div v-else class="table-wrap"><table class="center-table"><thead><tr><th>资料 / 版本</th><th>状态与阶段</th><th>重试与租约</th><th>操作</th></tr></thead><tbody><tr v-for="task in tasks" :key="task.id"><td><strong>{{ task.document_title }}</strong><small>v{{ task.version_number }}</small><small>任务 {{ task.id }}</small></td><td><span class="badge">{{ states[task.status] }}</span><small>{{ stages[task.stage]||task.stage }} · {{ task.attempts }}/{{ task.max_attempts }} 次尝试</small><small v-if="task.error_code" class="task-failure">{{ task.error_code }}</small></td><td><small>最近更新：{{ time(task.updated_at) }}</small><small v-if="task.retry_at">自动重试不早于：{{ time(task.retry_at) }}</small><small v-if="task.lease_until">中断后等待租约到期：{{ time(task.lease_until) }}</small></td><td><button v-if="project.role==='admin'&&task.status==='failed'" class="ghost" :disabled="busy" @click="retry(task)">重试</button><span v-else class="muted">—</span></td></tr></tbody></table></div>
    <div class="center-pagination"><button class="ghost" :disabled="offset===0" @click="page(-30)">上一页</button><span>第 {{ offset/30+1 }} 页 · 每5秒刷新</span><button class="ghost" :disabled="tasks.length<30" @click="page(30)">下一页</button></div>
  </section>
</template>
