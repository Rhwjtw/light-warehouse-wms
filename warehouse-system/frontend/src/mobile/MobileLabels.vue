<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { auth } from '../auth'
import { qrInstance } from './qr.js'
import { makeLabel } from './labels.js'
import QrLabel from './QrLabel.vue'
const type = ref('receipt'), search = ref(''), page = ref(1), items = ref([]), total = ref(0), labels = ref([]), copies = ref(1), busy = ref(false), error = ref('')
const pages = computed(() => Math.max(1, Math.ceil(total.value / 20)))
const config = computed(() => { try { return { instance: qrInstance(), error: '' } } catch (e) { return { instance: '', error: e.message } } })
let generation = 0, disposed = false
async function load() {
  const ticket = ++generation, kind = type.value
  busy.value = true; error.value = ''; items.value = []
  try {
    const data = (await api.get(kind === 'receipt' ? '/material-receipts' : '/locations', { params: { q: search.value, page: page.value, page_size: 20, ...(kind === 'receipt' ? { status: 'open' } : { is_active: true }) } })).data
    if (disposed || ticket !== generation) return
    items.value = data.items; total.value = data.total
    if (page.value > pages.value) { page.value = pages.value; await load() }
  } catch (e) { if (ticket === generation) error.value = e.message }
  finally { if (ticket === generation) busy.value = false }
}
async function add(row) {
  if (busy.value || !config.value.instance) return
  const kind = type.value, user = auth.user?.id, ticket = ++generation
  busy.value = true; error.value = ''
  try {
    const label = await makeLabel(api, kind, row.id, row.receipt_movement_id, config.value.instance)
    if (disposed || ticket !== generation || auth.user?.id !== user) return
    if (!labels.value.some(item => item.key === label.key)) labels.value.push(label)
  } catch (e) { if (ticket === generation) error.value = e.message }
  finally { if (ticket === generation) busy.value = false }
}
async function print() {
  if (busy.value || !labels.value.length || !Number.isInteger(copies.value) || copies.value < 1 || copies.value > 20) return
  busy.value = true; error.value = ''
  const user = auth.user?.id, ticket = ++generation
  try {
    const me = (await api.get('/auth/me')).data
    if (me.id !== user) throw new Error('账户已变化，请重新登录')
    const fresh = []
    for (const label of labels.value) fresh.push(await makeLabel(api, label.type, label.id, label.movementId, config.value.instance))
    if (disposed || ticket !== generation || auth.user?.id !== user) return
    labels.value = fresh
    await nextTick()
    await Promise.all([...document.querySelectorAll('.wms-qr-print img')].map(img => img.decode()))
    if (disposed || ticket !== generation || auth.user?.id !== user) return
    const elements = [...document.querySelectorAll('.wms-qr-print .wms-qr-label')]
    if (!elements.length || elements.some(el => el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1)) throw new Error('人工可读信息超过60×40毫米版面，请调整标签尺寸后打印；本次未打印')
    window.print()
  } catch (e) { if (ticket === generation) error.value = e.message }
  finally { if (ticket === generation) busy.value = false }
}
function reset() { page.value = 1; load() }
watch(type, () => { search.value = ''; reset() })
onMounted(load)
onBeforeUnmount(() => { disposed = true; generation++ })
</script>
<template>
  <section class="mobile-card"><h2>制作二维码标签</h2><p class="mobile-muted">选择货物明细或库位，加入下方打印清单。</p><details class="mobile-more"><summary>标签说明</summary><p>60×40毫米，一张一页。标签只定位对象，不代表箱号、余量或操作权限。</p></details><p v-if="config.error" class="mobile-warning">{{ config.error }}</p><details v-if="config.instance" class="mobile-more"><summary>所属仓库实例</summary><p class="mobile-small">{{ config.instance }}</p></details><p v-if="error" class="mobile-error" role="alert">{{ error }}</p><p v-if="busy" role="status">正在读取并核对…</p></section>
  <section class="mobile-card"><fieldset :disabled="busy" class="label-controls"><div class="mobile-view-tabs"><button :class="{ active: type === 'receipt' }" @click="type = 'receipt'">货物明细</button><button :class="{ active: type === 'location' }" @click="type = 'location'">库位</button></div><form class="mobile-search" @submit.prevent="reset"><input v-model="search" :placeholder="type === 'receipt' ? '入库单 / 物料 / 批号' : '库位名称'" :aria-label="type === 'receipt' ? '搜索入库单、物料或批号' : '搜索库位名称'" :maxlength="type === 'receipt' ? 120 : 80" /><button class="mobile-button">搜索</button></form><article v-for="row in items" :key="row.id" class="mobile-card"><h3>{{ row.material_name || row.name }}</h3><p>{{ type === 'receipt' ? `${row.document_no} · 明细 ${row.receipt_movement_id} · ${row.batch_no} · ${row.supplier_name}` : `库位ID ${row.id}` }}</p><button class="mobile-button" :disabled="!config.instance || labels.length >= 100" @click="add(row)">加入标签</button></article><p>第 {{ page }} / {{ pages }} 页 · 共 {{ total }} 条</p><div class="mobile-pagination"><button class="mobile-button" :disabled="page <= 1" @click="page--; load()">上一页</button><button class="mobile-button" :disabled="page >= pages" @click="page++; load()">下一页</button></div><button v-if="error" class="mobile-button" @click="load">重试查询</button></fieldset></section>
  <section v-if="labels.length" class="mobile-card"><h2>打印预览 · {{ labels.length }} 个对象</h2><p>选择60×40毫米纸、100%比例、关闭页眉页脚。打印前重新读取当前对象；改名以新的人工信息打印。</p><label>每个对象份数（同明细副本，不区分箱）<input v-model.number="copies" type="number" min="1" max="20" /></label><button class="mobile-button primary" :disabled="busy || !Number.isInteger(copies) || copies < 1 || copies > 20" @click="print">重新核对并打印标签</button><div v-for="label in labels" :key="label.key" class="label-preview"><QrLabel :label="label" /><details><summary>标签原文</summary><p>{{ label.text }}</p></details><button class="mobile-button" :disabled="busy" @click="labels = labels.filter(item => item.key !== label.key)">移除此标签</button></div></section>
  <Teleport v-if="labels.length && Number.isInteger(copies) && copies >= 1 && copies <= 20" to="body"><div class="wms-qr-print"><template v-for="label in labels" :key="label.key"><QrLabel v-for="copy in copies" :key="copy" :label="label" /></template></div></Teleport>
</template>
<style scoped>
.label-controls { border: 0; padding: 0; min-width: 0; }
.label-controls label { display: grid; gap: 8px; margin-bottom: 16px; }
.label-preview { margin-top: 20px; overflow-wrap: anywhere; }
.label-preview .wms-qr-label { outline: 1px solid #9aa; margin: 6px; }
</style>
