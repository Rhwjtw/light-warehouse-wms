<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { createQrCamera } from './camera.js'
const emit = defineEmits(['decoded', 'cancel'])
defineProps({ type: { type: String, required: true }, busy: Boolean, error: { type: String, default: '' } })
const video = ref(null), canvas = ref(null), state = ref('stopped'), cameraError = ref(''), devices = ref([]), device = ref('')
let camera
function stop() { camera?.stop() }
function close() { stop(); emit('cancel') }
function start() { cameraError.value = ''; camera?.start(device.value) }
function hidden() { if (document.visibilityState === 'hidden') stop() }
onMounted(() => {
  camera = createQrCamera({ video: video.value, canvas: canvas.value, mediaDevices: navigator.mediaDevices, secure: window.isSecureContext, visible: () => document.visibilityState !== 'hidden', onState: next => state.value = next, onError: value => cameraError.value = value, onText: text => emit('decoded', text), onDevices: values => devices.value = values })
  window.addEventListener('wms-mobile-stop-resources', stop)
  window.addEventListener('pagehide', stop)
  document.addEventListener('visibilitychange', hidden)
})
onBeforeUnmount(() => { stop(); window.removeEventListener('wms-mobile-stop-resources', stop); window.removeEventListener('pagehide', stop); document.removeEventListener('visibilitychange', hidden) })
defineExpose({ stop })
</script>
<template>
  <section class="mobile-card"><h2>{{ type === 'location' ? '扫描库位标签' : '扫描货物标签' }}</h2><p>对准二维码标签，识别后返回表单。</p><div class="mobile-scan-frame"><video ref="video" muted playsinline class="qr-video" aria-label="扫码相机预览" /><span class="mobile-scan-guide" aria-hidden="true"></span><span v-if="state === 'stopped'" class="mobile-scan-hint">打开相机后，将标签对准框内</span></div><canvas ref="canvas" hidden /><p v-if="busy" role="status">正在读取标签对象…</p><p v-if="error || cameraError" class="mobile-error" role="alert">{{ error || cameraError }}</p><label v-if="devices.length > 1">相机<select v-model="device" :disabled="state !== 'stopped' || busy"><option value="">优先后摄</option><option v-for="(item,index) in devices" :key="item.id" :value="item.id">{{ item.label || `相机 ${index+1}` }}</option></select></label><button class="mobile-button primary" :disabled="state !== 'stopped' || busy" @click="start">{{ state === 'requesting' ? '正在等待相机授权…' : state === 'scanning' ? '正在识别…' : '打开 / 重开相机' }}</button><button v-if="state !== 'stopped'" class="mobile-button" @click="stop">停止相机</button><details class="mobile-more"><summary>扫码说明</summary><p>识别后关闭相机并读取当前对象，不会自动提交；切后台后需手动重开。</p></details><button class="mobile-button" @click="close">取消扫码，返回原表单</button></section>
</template>
<style scoped>
.qr-video { width: 100%; max-height: 50vh; aspect-ratio: 4 / 3; object-fit: cover; background: #111; border-radius: 8px; }
label { display: grid; gap: 8px; }
</style>
