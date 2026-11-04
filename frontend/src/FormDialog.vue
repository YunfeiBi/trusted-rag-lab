<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'

const props = defineProps({ id:{type:String,required:true}, title:{type:String,required:true}, busy:Boolean })
const emit = defineEmits(['close'])
const dialog = ref(null)
let previousOverflow
let pressedOutside = false

function close() {
  if (!props.busy) emit('close')
}

function outside(event) {
  const bounds = dialog.value.getBoundingClientRect()
  return event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom
}

onMounted(() => {
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  dialog.value.showModal()
})
onBeforeUnmount(() => {
  dialog.value?.close()
  document.body.style.overflow = previousOverflow
})
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="form-dialog" :aria-labelledby="id+'-title'" :aria-busy="busy"
      @cancel.prevent="close" @pointerdown="pressedOutside=outside($event)" @click="pressedOutside && outside($event) && close()">
      <div class="dialog-heading"><h2 :id="id+'-title'">{{ title }}</h2><button type="button" class="dialog-close" aria-label="关闭弹窗" :disabled="busy" @click="close">×</button></div>
      <slot />
    </dialog>
  </Teleport>
</template>

<style scoped>
.form-dialog{width:min(540px,calc(100% - 32px));max-height:calc(100dvh - 48px);overflow:auto;margin:auto;border:1px solid #e3e9f0;border-radius:18px;padding:30px;background:#fff;color:#233343;box-shadow:0 25px 90px #18324c30}
.form-dialog::backdrop{background:#1d304560;backdrop-filter:blur(4px)}
.dialog-heading{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:20px}
.dialog-heading h2{font-size:22px;margin:0}
.dialog-close{display:grid;place-items:center;flex-shrink:0;width:32px;height:32px;border:0;border-radius:8px;background:#f1f4f8;color:#65758a;font-size:24px;line-height:1}
.dialog-close:hover{background:#e5ebf3}
@media(max-width:600px){.form-dialog{padding:22px;max-height:calc(100dvh - 32px)}.dialog-heading h2{font-size:20px}}
</style>
