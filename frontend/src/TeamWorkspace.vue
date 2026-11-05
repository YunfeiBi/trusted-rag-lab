<script setup>
import {computed,onUnmounted,ref,watch} from 'vue'
import FormDialog from './FormDialog.vue'
const props=defineProps({teams:Array,active:Object,user:Object,api:Function})
const emit=defineEmits(['select','changed','created','library'])
const name=ref(''),description=ref(''),teamId=ref(''),message=ref(''),own=ref([]),members=ref([]),applications=ref([]),error=ref(''),trace=ref(''),busy=ref(false),notes=ref({}),edit=ref(false),editName=ref(''),editDescription=ref('')
const deleting=ref(false),deleteName=ref(''),deletePreview=ref(null)
const notice=ref('')
const creating=ref(false), applying=ref(false), confirmation=ref(null)
const directory=ref([]), teamQuery=ref(''), directoryOffset=ref(0), directoryLoading=ref(false)
const directoryError=ref('')
const chosenTeam=computed(()=>directory.value.find(team=>team.id===teamId.value))
let directoryTicket=0, searchTimer

async function findTeams() {
  const ticket=++directoryTicket
  directoryLoading.value=true
  directoryError.value=''
  const query=new URLSearchParams({q:teamQuery.value.trim(),offset:directoryOffset.value})
  try {
    const rows=await props.api('/team-directory?'+query)
    if (alive && applying.value && ticket===directoryTicket) directory.value=rows
  } catch (e) {
    if (alive && applying.value && ticket===directoryTicket) { directory.value=[];directoryError.value=e.message }
  } finally {
    if (ticket===directoryTicket) directoryLoading.value=false
  }
}

function searchTeams() {
  directoryOffset.value=0
  teamId.value=''
  directoryTicket++
  directoryLoading.value=true
  clearTimeout(searchTimer)
  searchTimer=setTimeout(findTeams,200)
}

function pageTeams(step) {
  teamId.value=''
  directoryOffset.value+=step
  findTeams()
}

function startApplication() {
  teamQuery.value='';teamId.value='';directoryOffset.value=0;directory.value=[];error.value=''
  applying.value=true
  findTeams()
}

function warn(title, text, action) {
  error.value='';trace.value=''
  confirmation.value={title,text,action}
}

function confirmAction() {
  const action=confirmation.value.action
  return run(async()=>{await action();confirmation.value=null})
}

const admin=computed(()=>['owner','admin'].includes(props.active?.role))
const canCreate=computed(()=>props.user.teacher_verified&&props.user.academic_identity==='teacher')
const roles={owner:'团队所有者',admin:'团队管理员',member:'普通成员'}
const states={pending:'待审批',approved:'已批准',rejected:'已拒绝',cancelled:'已撤回'}
const identity=u=>u.teacher_verified?'教师（已确认）':u.academic_identity==='teacher'?'教师身份待确认':u.academic_identity==='student'?'学生':'身份待补充'
let alive=true,sequence=0
async function load(){const ticket=++sequence;try{const mine=await props.api('/team-applications');const id=props.active?.id;const people=id?await props.api('/teams/'+id+'/members'):[];const requests=id&&admin.value?await props.api('/teams/'+id+'/applications?limit=100'):[];if(alive&&ticket===sequence){own.value=mine;members.value=people;applications.value=requests}}catch(e){if(alive&&ticket===sequence){members.value=[];applications.value=[];error.value=e.message;trace.value=e.traceId||''}}}
async function run(action){if(busy.value)return;busy.value=true;error.value='';trace.value='';notice.value='';try{await action()}catch(e){if(alive){error.value=e.message;trace.value=e.traceId||''}}finally{if(alive)busy.value=false}}
function create(){return run(async()=>{const team=await props.api('/teams',{method:'POST',body:JSON.stringify({name:name.value,description:description.value})});name.value='';description.value='';creating.value=false;emit('created',team.id)})}
function apply(){return run(async()=>{await props.api('/team-applications',{method:'POST',body:JSON.stringify({team_id:teamId.value,message:message.value})});teamId.value='';message.value='';applying.value=false;await load()})}
function cancel(item) {
  warn('撤回申请', `撤回加入“${item.team_name}”的申请？`, async()=>{
    await props.api('/team-applications/'+item.id,{method:'DELETE'})
    await load()
  })
}
function review(item, decision) {
  const label=decision==='approved'?'批准加入':'拒绝申请'
  warn(label, `确认${label}（${item.user.display_name}）？`, async()=>{
    await props.api(`/teams/${props.active.id}/applications/${item.id}/review`, {
      method:'POST',body:JSON.stringify({decision,note:notes.value[item.id]||''})
    })
    emit('changed'); await load()
  })
  confirmation.value.applicationId=item.id
}
function role(member, value) {
  warn('修改团队角色', `将 ${member.user.display_name} 的角色改为${roles[value]}？`, async()=>{
    await props.api(`/teams/${props.active.id}/members/${member.user.id}`, {
      method:'PUT',body:JSON.stringify({role:value})
    })
    emit('changed'); await load()
  })
}
function remove(member) {
  warn('移除团队成员', `移除 ${member.user.display_name} 后，其团队及下属项目的访问权会撤销。`, async()=>{
    await props.api(`/teams/${props.active.id}/members/${member.user.id}`, {method:'DELETE'})
    emit('changed'); await load()
  })
}
function leave() {
  warn('退出团队', '退出会撤销团队及项目授权，重新加入需要审批。', async()=>{
    await props.api(`/teams/${props.active.id}/leave`, {method:'POST'})
    members.value=[]; applications.value=[]; emit('changed')
  })
}
function transfer(member) {
  warn('转交团队所有权', `将所有权转交给 ${member.user.display_name} 后，你将成为团队管理员。`, async()=>{
    await props.api(`/teams/${props.active.id}/transfer/${member.user.id}`, {method:'POST'})
    emit('changed'); await load()
  })
}
function startEdit(){error.value='';editName.value=props.active.name;editDescription.value=props.active.description;edit.value=true}
function save(){return run(async()=>{await props.api('/teams/'+props.active.id,{method:'PATCH',body:JSON.stringify({name:editName.value,description:editDescription.value})});edit.value=false;emit('changed')})}
function beginDelete(){return run(async()=>{const preview=await props.api(`/teams/${props.active.id}/deletion-preview`);if(alive){deletePreview.value=preview;deleteName.value='';deleting.value=true}})}
function deleteTeam(){return run(async()=>{const deletedName=props.active.name;await props.api('/teams/'+props.active.id,{method:'DELETE',body:JSON.stringify({confirm_name:deleteName.value})});sequence++;members.value=[];applications.value=[];deleting.value=false;deletePreview.value=null;deleteName.value='';notice.value=`团队“${deletedName}”已删除，所有成员的团队及项目访问权已撤销。`;emit('changed')})}
watch(()=>[props.active?.id,props.active?.role],()=>{edit.value=false;deleting.value=false;deletePreview.value=null;deleteName.value='';error.value='';load()},{immediate:true})
onUnmounted(()=>{alive=false;sequence++;clearTimeout(searchTimer)})
</script>
<template>
  <div class="team-workspace">
    <section class="panel"><div class="section-heading"><div><p class="eyebrow">LABORATORY TEAMS</p><h2>团队空间</h2></div><button class="ghost" :disabled="busy" @click="emit('changed');load()">刷新团队</button></div>
      <label v-if="teams.length">当前团队<select class="team-selector" :value="active?.id||''" :disabled="busy" @change="emit('select',$event.target.value)"><option v-for="team in teams" :key="team.id" :value="team.id">{{ team.name }} · {{ roles[team.role] }}</option></select></label>
      <p v-else class="team-empty">尚未加入实验团队。请选择团队并提交申请，等待管理员审批；只有已确认教师可创建团队。</p>
      <p v-if="error && !creating && !applying && !edit && !deleting && !confirmation" class="error" role="alert">{{ error }}<small>{{ trace }}</small></p>
      <p v-if="notice" class="auth-success team-notice" role="status">{{ notice }}</p>
      <div class="team-entry-actions">
        <button class="ghost team-apply-button" :disabled="busy" @click="startApplication">申请加入团队</button>
        <button v-if="canCreate" class="primary team-create-button" :disabled="busy" @click="error='';creating=true">创建实验团队</button>
      </div>
      <div v-if="own.length" class="own-applications"><h3>我的申请</h3><article v-for="item in own" :key="item.id"><div><strong>{{ item.team_name }}</strong><span class="badge">{{ item.team_deleted?'团队已删除':states[item.status] }}</span><p v-if="item.review_note">{{ item.review_note }}</p></div><button v-if="item.status==='pending'&&!item.team_deleted" class="ghost" :disabled="busy" @click="cancel(item)">撤回申请</button></article><p class="hint">这里保留申请结果，当前成员资格以“我的团队”为准；退出后重新入组仍需审批。</p></div>
    </section>
    <template v-if="active"><section class="panel team-overview"><div class="section-heading"><div><h2>{{ active.name }}</h2><p class="muted">{{ active.description||'暂无团队说明' }}</p></div><button v-if="admin" class="ghost" :disabled="busy" @click="startEdit">编辑团队</button><button v-else class="ghost" :disabled="busy" @click="leave">退出团队</button></div><p>团队编号：<strong class="team-code">{{ active.code }}</strong> <span class="badge">{{ roles[active.role] }}</span></p><p class="hint">公共资料对已批准团队成员开放；项目内部资料仍需项目管理员授权，教师身份本身不会授予全部项目访问权。</p><button class="primary team-library-button" @click="emit('library',active.library_project_id)">进入团队公共资料 →</button><button v-if="active.role==='admin'" class="ghost team-leave-button" :disabled="busy" @click="leave">退出团队</button>
      <div v-if="active.role==='owner'" class="team-danger-zone"><button class="danger-link delete-team-button" :disabled="busy" @click="beginDelete">删除团队</button></div>
    </section><section v-if="admin" class="panel team-review"><h2>入组审批</h2><p v-if="!applications.length" class="muted">暂无申请。</p><article v-for="item in applications" :key="item.id" class="team-application"><div><strong>{{ item.user.display_name }}</strong><span class="badge">{{ identity(item.user) }}</span><span class="badge">{{ states[item.status] }}</span><p>{{ item.user.username }} · {{ item.user.qq_email||'未填写联系邮箱' }}</p><p>{{ item.message||'未填写申请说明' }}</p></div><template v-if="item.status==='pending'"><div class="actions"><button class="ghost" :disabled="busy" @click="review(item,'rejected')">拒绝</button><button class="primary" :disabled="busy" @click="review(item,'approved')">批准加入</button></div></template><p v-else class="hint">{{ item.review_note }}</p></article></section>
    <section class="panel"><h2>团队成员与联系方式</h2><p class="hint">教师/学生身份与管理权限分别显示。邮箱由本人填写，尚未验证归属。</p><div class="team-people"><article v-for="member in members" :key="member.user.id" class="team-person"><div><strong>{{ member.user.display_name }}</strong><span class="badge">{{ identity(member.user) }}</span><span class="badge">{{ roles[member.role] }}</span><p>{{ member.user.username }}</p><a v-if="member.user.qq_email" :href="'mailto:'+member.user.qq_email">{{ member.user.qq_email }}</a><span v-else class="muted">QQ邮箱未填写</span></div><div v-if="active.role==='owner'&&member.role!=='owner'" class="team-person-actions"><button class="ghost" :disabled="busy" @click="role(member,member.role==='admin'?'member':'admin')">{{ member.role==='admin'?'取消管理员':'设为管理员' }}</button><button v-if="member.user.teacher_verified" class="ghost" :disabled="busy" @click="transfer(member)">转交所有权</button><button class="danger-link" :disabled="busy" @click="remove(member)">移除成员</button></div><button v-else-if="active.role==='admin'&&member.role==='member'" class="danger-link" :disabled="busy" @click="remove(member)">移除成员</button></article></div></section></template>
    <FormDialog v-if="creating" id="create-team-dialog" title="创建实验团队" :busy="busy" @close="creating=false">
      <form class="team-create-form" @submit.prevent="create">
        <fieldset class="dialog-fields" :disabled="busy">
          <label>团队名称<input v-model="name" required maxlength="100" autofocus></label>
          <label>团队简介<textarea v-model="description" maxlength="2000" rows="3"></textarea></label>
          <p v-if="error" class="error" role="alert">{{ error }}</p>
          <div class="actions"><button type="button" class="ghost" @click="creating=false">取消</button><button class="primary">创建团队</button></div>
        </fieldset>
      </form>
    </FormDialog>
    <FormDialog v-else-if="applying" id="apply-team-dialog" title="申请加入团队" :busy="busy" @close="applying=false">
      <form class="team-apply-form" @submit.prevent="apply">
        <fieldset class="dialog-fields" :disabled="busy">
          <label>搜索团队<input v-model="teamQuery" type="search" maxlength="100" placeholder="输入团队名称或简介中的关键字" autofocus @input="searchTeams"></label>
          <label>选择团队<select v-model="teamId" required :disabled="directoryLoading">
            <option value="" disabled>{{ directoryLoading ? '正在查找团队…' : directory.length ? '请选择一个团队' : '没有可申请的团队' }}</option>
            <option v-for="team in directory" :key="team.id" :value="team.id">{{ team.name }} · {{ team.member_count }} 人</option>
          </select></label>
          <div v-if="directoryOffset || directory.length === 50" class="center-pagination">
            <button type="button" class="ghost" :disabled="!directoryOffset || directoryLoading" @click="pageTeams(-50)">上一页</button>
            <button type="button" class="ghost" :disabled="directory.length < 50 || directoryLoading" @click="pageTeams(50)">下一页</button>
          </div>
          <p v-if="chosenTeam" class="team-directory-description">{{ chosenTeam.description || '暂无团队简介' }}</p>
          <label>申请说明<textarea v-model="message" maxlength="1000" rows="2" placeholder="说明你的姓名、年级或与团队的关系"></textarea></label>
          <p class="hint">申请信息和联系邮箱对该团队管理员可见。</p>
          <p v-if="error || directoryError" class="error" role="alert">{{ error || directoryError }}</p>
          <div class="actions"><button type="button" class="ghost" @click="applying=false">取消</button><button class="primary" :disabled="!teamId || directoryLoading">提交入组申请</button></div>
        </fieldset>
      </form>
    </FormDialog>
    <FormDialog v-else-if="edit && admin" id="edit-team-dialog" title="编辑团队" :busy="busy" @close="edit=false">
      <form class="edit-team-form" @submit.prevent="save"><label>团队名称<input v-model="editName" required maxlength="100"></label><label>团队说明<textarea v-model="editDescription" maxlength="2000"></textarea></label><p v-if="error" class="error" role="alert">{{ error }}</p><div class="actions"><button type="button" class="ghost" @click="edit=false">取消</button><button class="primary" :disabled="busy">保存团队</button></div></form>
    </FormDialog>
    <FormDialog v-else-if="deleting && active" id="delete-team-dialog" title="删除团队" :busy="busy" @close="deleting=false">
      <form class="delete-team-form" @submit.prevent="deleteTeam"><h3>删除团队“{{ active.name }}”</h3><p>将关闭整个团队及所有项目的访问，撤销成员权限并停止未完成的处理任务。底层资料、历史与审计记录保留，不会物理清除；当前没有恢复入口。</p><p v-if="deletePreview">当前范围：{{ deletePreview.projects }} 个内部项目、{{ deletePreview.documents }} 份资料、{{ deletePreview.members }} 位成员、{{ deletePreview.pending_tasks }} 个未完成任务。范围以实际删除时为准。</p><label>输入完整团队名称确认<input v-model="deleteName" class="delete-team-name" required maxlength="100" autocomplete="off" :placeholder="active.name"></label><p v-if="error" class="error" role="alert">{{ error }}</p><div class="actions"><button type="button" class="ghost" :disabled="busy" @click="deleting=false;deleteName=''">取消</button><button class="confirm-delete-team" :disabled="busy||!deletePreview||deleteName!==active.name">确认删除团队</button></div></form>
    </FormDialog>
    <FormDialog v-else-if="confirmation" id="team-confirm-dialog" :title="confirmation.title" :busy="busy" @close="confirmation=null">
      <p>{{ confirmation.text }}</p>
      <label v-if="confirmation.applicationId">审批说明<input v-model="notes[confirmation.applicationId]" :disabled="busy" maxlength="500" placeholder="可选"></label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <div class="actions"><button class="ghost" :disabled="busy" @click="confirmation=null">取消</button><button class="primary" :disabled="busy" @click="confirmAction">确认</button></div>
    </FormDialog>
  </div>
</template>
<style scoped>
.team-danger-zone{border-top:1px solid #ecdfdd;margin-top:24px;padding-top:18px}.delete-team-form{border:1px solid #edcfca;background:#fff7f5;border-radius:8px;padding:20px}.delete-team-form h3{font-size:15px;color:#93493e}.delete-team-form p{font-size:12px;color:#956c63;overflow-wrap:anywhere}.confirm-delete-team{background:#a94d40;color:white;border:0;border-radius:7px;padding:11px 16px;font-weight:600}.delete-team-form .actions{flex-wrap:wrap}
.team-entry summary{cursor:pointer;color:#57718e;font-size:12px;padding:12px 0}.team-entry{margin-top:18px}
.team-entry-grid{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:24px}.team-entry-grid>form,.teacher-rule{padding:22px;border:1px solid #e1e8ef;border-radius:8px;background:#f8fafc}.team-entry-grid h3{font-size:15px}.teacher-rule p{font-size:12px;color:#7c8c9c}.team-empty{background:#f5f8fb;border-radius:7px;padding:18px;color:#6f8196;font-size:13px}.own-applications{border-top:1px solid #e5ebf2;margin-top:22px;padding-top:20px}.own-applications h3{font-size:14px}.own-applications article{display:flex;justify-content:space-between;gap:15px;padding:14px 0;align-items:center}.own-applications strong{font-size:13px;margin-right:12px}.own-applications p{font-size:12px;color:#8192a4}.team-code{letter-spacing:2px;color:#476987}.team-overview p{overflow-wrap:anywhere}.team-leave-button{margin-left:14px}.edit-team-form{border-top:1px solid #e5eaf0;margin-top:22px;padding-top:20px}.team-application{border-top:1px solid #e4ebf2;padding:20px 0;overflow-wrap:anywhere}.team-application p{font-size:12px;color:#8192a4}.team-application .badge,.team-person .badge{margin-left:8px;display:inline-block}.team-people{display:grid;gap:14px}.team-person{display:flex;justify-content:space-between;gap:20px;border:1px solid #e1e8ef;border-radius:8px;padding:18px;align-items:center;min-width:0}.team-person>div{min-width:0;overflow-wrap:anywhere}.team-person strong{font-size:13px}.team-person p,.team-person a{font-size:12px;margin:10px 0;color:#6681a0}.team-person-actions{display:flex;gap:9px;flex-wrap:wrap}.team-person-actions button{font-size:11px;padding:8px}@media(max-width:700px){.team-entry-grid{grid-template-columns:1fr}.team-person{flex-direction:column;align-items:flex-start}.team-workspace .panel{padding:20px}.team-code{letter-spacing:1px}.team-overview .section-heading{align-items:flex-start}}
.team-entry-actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:20px}.team-directory-description{padding:12px;background:#f3f6fa;border-radius:6px;overflow-wrap:anywhere;white-space:pre-wrap}
</style>
