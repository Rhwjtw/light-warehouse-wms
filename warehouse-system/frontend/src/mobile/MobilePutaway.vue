<script setup>
import MobileChoices from './MobileChoices.vue'
import MobileTaskHeading from './MobileTaskHeading.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { auth } from '../auth'
import { canOperate } from '../permissions'
import { formatQuantity, quantityUnits } from '../quantity'
import { mobileRevision, mobileStore, touchMobile } from './session'
import { lockedTask, taskLabels } from './taskState'
import { readGate, positiveId } from './readQueries'
import { returnToParent } from './navigation'
import { putawayFields, putawayKind, putawayOrigin, putawayPayload, putawaySnapshot, putawayTaskPath, verifyPutawayReceipt, readPutawayResult } from './putaway'
import { qrInstance, readQrObject } from './qr.js'
import MobileScanner from './MobileScanner.vue'
const route = useRoute(), router = useRouter(), form = reactive(putawayFields())
const error = ref(''), busy = ref(false), checking = ref(false), receipt = ref(null), location = ref(null), result = ref(null), refreshed = ref('')
const gate = readGate(), choiceGate = readGate(), items = ref([]), total = ref(0), loading = ref(false), search = ref(''), choiceError = ref('')
const task = computed(() => { mobileRevision.value; const t = mobileStore()?.get(route.params.jobId); return t?.kind === putawayKind ? t : null })
const drafts = computed(() => { mobileRevision.value; return mobileStore()?.tasks().filter(t => t.kind === putawayKind && t.status !== 'succeeded') || [] })
const allowed = computed(() => canOperate(auth.user?.role, 'material.inventory.write'))
const locked = computed(() => lockedTask(task.value))
const warning = computed(() => { mobileRevision.value; return mobileStore()?.warning || '' })
const step = computed(() => route.params.step || 'start'), choice = computed(() => route.query.kind === 'location' ? 'location' : 'receipt')
const page = computed(() => /^[1-9]\d*$/.test(route.query.page) && Number.isSafeInteger(Number(route.query.page)) ? Number(route.query.page) : 1)
const pages = computed(() => Math.max(1, Math.ceil(total.value / 20)))
const canContinue = computed(() => result.value && result.value.receipt.status !== 'reversed' && quantityUnits(result.value.receipt.pending_quantity) > 0n)
const scanError = ref(''), scanner = ref(null)
const qrConfig = computed(() => { try { return { instance: qrInstance(), error: '' } } catch (e) { return { instance: '', error: e.message } } })
let restoring = false, disposed = false, editVersion = 0
function loadFields() { restoring = true; Object.assign(form, putawayFields(task.value?.fields)); restoring = false }
watch(form, () => { if (!restoring && task.value && !locked.value) { editVersion++; mobileStore().edit(task.value.id, form); touchMobile() } }, { flush: 'sync' })
async function start(same = false) {
  if (!allowed.value || warning.value || busy.value) return
  busy.value = true; error.value = ''
  const store = mobileStore(), original = task.value, path = route.fullPath
  try {
    let row
    if (same) {
      if (original?.status !== 'succeeded') return
      row = verifyPutawayReceipt(original.snapshot, (await api.get(`/material-receipts/${original.snapshot.receiptId}`)).data, true)
    } else if (step.value === 'start' && (route.query.receipt_id || route.query.movement_id)) {
      const id = positiveId(route.query.receipt_id), movement = positiveId(route.query.movement_id)
      if (!id || !movement) throw new Error('入库单与明细ID必须同时有效')
      row = (await api.get(`/material-receipts/${id}`)).data
      if (String(row.id) !== id || String(row.receipt_movement_id) !== movement) throw new Error('入库单与明细不匹配，不按批号猜记录')
      verifyPutawayReceipt(putawayFields({ receiptId: row.id, movementId: row.receipt_movement_id, sourceBatchId: row.batch_id, unit: row.unit }), row, true)
    }
    if (disposed || mobileStore() !== store || route.fullPath !== path) return
    let opened = store.create(putawayKind, original?.status === 'succeeded' ? `new:${original.id}` : 'new', putawayOrigin(original?.origin || route.query.from))
    if (row) opened = store.selectPutaway(opened.id, row)
    touchMobile(); await router.push(putawayTaskPath(opened, lockedTask(opened) ? 'result' : 'form'))
  } catch (e) { error.value = e.message; if (same) result.value = null }
  finally { busy.value = false }
}
async function check() {
  const store = mobileStore(), original = task.value, ticket = gate.begin(), version = editVersion
  if (!original) return false
  checking.value = true; error.value = ''; receipt.value = null; location.value = null
  try {
    const user = (await api.get('/auth/me')).data
    if (user.id !== auth.user?.id || store !== mobileStore()) throw new Error('账户已变化，请重新登录')
    auth.user = user
    const fields = original.snapshot && lockedTask(original) ? original.snapshot : original.fields
    if (!fields.receiptId) throw new Error('请选择具体入库明细')
    const row = verifyPutawayReceipt(fields, (await api.get(`/material-receipts/${fields.receiptId}`)).data, !lockedTask(original))
    const [material, supplier, target] = await Promise.all([api.get(`/materials/${row.material_id}`), api.get(`/suppliers/${row.supplier_id}`), fields.locationId ? api.get(`/locations/${fields.locationId}`) : Promise.resolve(null)])
    if (!gate.current(ticket) || disposed || store !== mobileStore() || task.value?.id !== original.id || editVersion !== version) return false
    receipt.value = row; location.value = target?.data || null
    if (target && String(target.data.id) !== fields.locationId) throw new Error('库位身份不符')
    if (String(material.data.id) !== String(row.material_id) || String(supplier.data.id) !== String(row.supplier_id)) throw new Error('原料或供应商身份不符')
    if (!lockedTask(original)) {
      if (!material.data.is_active || !supplier.data.is_active || material.data.unit !== fields.unit) throw new Error('原料、供应商已停用或单位已变化')
      if (target && target.data.is_active !== true) throw new Error('库位已停用，请重新选择')
      if (fields.quantity && quantityUnits(fields.quantity) > quantityUnits(row.pending_quantity)) throw new Error('本次数量超过最新待上架量，请修改后重新核对')
    }
    return true
  } catch (e) { if (gate.current(ticket)) error.value = e.message; return false }
  finally { if (gate.current(ticket)) checking.value = false }
}
async function confirm() {
  if (!task.value || locked.value || busy.value) return
  busy.value = true; error.value = ''
  try {
    putawaySnapshot(form)
    if (!await check() || disposed) return
    if (!allowed.value) throw new Error('当前角色无原料上架权限')
    await router.push(putawayTaskPath(task.value, 'confirm'))
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
async function submit() {
  if (!task.value || locked.value || busy.value || step.value !== 'confirm') return
  busy.value = true; error.value = ''
  let started, store
  try {
    if (!await check() || disposed) return
    store = mobileStore(); started = store.begin(task.value.id, { ready: true, allowed: allowed.value }); touchMobile()
    const response = await api.post('/material-putaways', putawayPayload(started))
    store.settle(started.id, 'succeeded', '', response.data)
  } catch (e) {
    if (started) store.settle(started.id, [403, 422].includes(e.status) ? 'rejected' : 'uncertain', e.message)
    else error.value = e.message
  } finally {
    touchMobile(); busy.value = false
    if (started && !disposed && mobileStore() === store && task.value?.id === started.id) {
      await router.replace(putawayTaskPath(started, 'result'))
      if (task.value?.status === 'succeeded') await refreshResult()
    }
  }
}
async function refreshResult() {
  const original = task.value, store = mobileStore(), ticket = gate.begin()
  if (original?.status !== 'succeeded') return
  checking.value = true; error.value = ''; result.value = null; refreshed.value = ''
  try {
    const data = await readPutawayResult(api, original)
    if (!gate.current(ticket) || disposed || mobileStore() !== store || task.value?.id !== original.id) return
    result.value = data; refreshed.value = new Date().toLocaleTimeString('zh-CN')
  } catch (e) { if (gate.current(ticket)) error.value = `上架已成功，待刷新：${e.message}` }
  finally { if (gate.current(ticket)) checking.value = false }
}
function scan(kind) {
  if (!task.value || locked.value || busy.value || !allowed.value || warning.value || !qrConfig.value.instance) return
  scanError.value = ''
  router.push({ path: putawayTaskPath(task.value, 'scan'), query: { kind } })
}
function cancelScan() { scanner.value?.stop(); returnToParent(router, putawayTaskPath(task.value)) }
async function scanned(text) {
  if (!task.value || locked.value || busy.value || step.value !== 'scan') return
  scanner.value?.stop(); busy.value = true; scanError.value = ''
  const original = task.value, kind = choice.value, store = mobileStore(), ticket = choiceGate.begin(), path = route.fullPath, version = editVersion, userId = auth.user?.id
  try {
    const me = (await api.get('/auth/me')).data
    if (me.id !== userId || store !== mobileStore()) throw new Error('账户已变化，请重新登录')
    auth.user = me
    if (!allowed.value) throw new Error('当前角色无原料上架权限')
    const current = await readQrObject(api, text, kind, qrConfig.value.instance)
    if (disposed || !choiceGate.current(ticket) || route.fullPath !== path || task.value?.id !== original.id || store !== mobileStore() || auth.user?.id !== userId || locked.value || editVersion !== version) return
    let selected = original
    if (kind === 'location') {
      form.locationId = String(current.id); location.value = current
    } else {
      if (original.fields.receiptId && (original.fields.receiptId !== String(current.id) || original.fields.movementId !== String(current.receipt_movement_id)) && !window.confirm('换为此入库明细会清空旧库位、数量和请求状态，确认更换？')) return
      selected = store.selectPutaway(original.id, current); touchMobile()
    }
    returnToParent(router, putawayTaskPath(selected, lockedTask(selected) ? 'result' : 'form'))
  } catch (e) { if (choiceGate.current(ticket) && route.fullPath === path) scanError.value = e.message }
  finally { busy.value = false }
}
function choose(kind) { if (!locked.value && !busy.value) router.push({ path: putawayTaskPath(task.value, 'choose'), query: { kind } }) }
function choicePage(value) { router.replace({ path: route.path, query: { kind: choice.value, q: search.value || undefined, page: String(value) } }) }
async function loadChoices(restorePosition = true) {
  if (step.value !== 'choose' || !task.value || locked.value) return
  const ticket = choiceGate.begin(), path = route.fullPath
  loading.value = true; choiceError.value = ''; items.value = []; total.value = 0
  search.value = typeof route.query.q === 'string' ? route.query.q.slice(0, choice.value === 'location' ? 80 : 120) : ''
  try {
    const data = (await api.get(choice.value === 'location' ? '/locations' : '/material-receipts', { params: { q: search.value, page: page.value, page_size: 20, ...(choice.value === 'location' ? { is_active: true } : { status: 'open' }) } })).data
    if (!choiceGate.current(ticket)) return
    items.value = data.items; total.value = data.total
    if (page.value > pages.value) { choicePage(pages.value); return }
    loading.value = false
    if (restorePosition) {
      await nextTick(); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
      if (!choiceGate.current(ticket) || route.fullPath !== path) return
      const position = mobileStore()?.list(path), anchor = position?.anchor && document.getElementById(position.anchor)
      window.scrollTo({ top: anchor ? window.scrollY + anchor.getBoundingClientRect().top - 80 : position?.top || 0 })
    }
  } catch (e) { if (choiceGate.current(ticket)) choiceError.value = e.message }
  finally { if (choiceGate.current(ticket)) loading.value = false }
}
async function select(row) {
  if (!task.value || locked.value || busy.value) return
  busy.value = true; choiceError.value = ''
  const original = task.value, kind = choice.value, store = mobileStore(), ticket = choiceGate.begin()
  try {
    const current = (await api.get(`/${kind === 'location' ? 'locations' : 'material-receipts'}/${row.id}`)).data
    if (disposed || !choiceGate.current(ticket) || task.value?.id !== original.id || store !== mobileStore() || locked.value) return
    if (current.id !== row.id) throw new Error('所选记录身份不符')
    let selected = original
    if (kind === 'location') {
      if (current.is_active !== true) throw new Error('库位已停用，请重新选择')
      form.locationId = String(current.id); location.value = current
    } else {
      if (current.receipt_movement_id !== row.receipt_movement_id) throw new Error('具体入库明细不匹配')
      selected = store.selectPutaway(original.id, current); touchMobile()
    }
    returnToParent(router, putawayTaskPath(selected, lockedTask(selected) ? 'result' : 'form'))
  } catch (e) { choiceError.value = e.message }
  finally { busy.value = false }
}
function restore() {
  scanner.value?.stop(); scanError.value = ''; gate.cancel(); choiceGate.cancel(); checking.value = false; error.value = ''; receipt.value = null; location.value = null; result.value = null
  loadFields()
  if (step.value === 'scan') return
  if (step.value === 'choose') loadChoices()
  else if (task.value?.status === 'succeeded') refreshResult()
  else if (task.value && form.receiptId) check()
}
function resume() {
  if (step.value === 'scan') return
  if (step.value === 'choose') {
    if (document.querySelector('[data-mobile-list-ready]')) {
      const anchor = [...document.querySelectorAll('[data-mobile-anchor]')].find(el => el.getBoundingClientRect().bottom > 80)
      mobileStore()?.saveList(route.fullPath, { top: window.scrollY, anchor: anchor?.id })
    }
    loadChoices()
  } else if (task.value?.status === 'succeeded') refreshResult()
  else if (task.value) check()
}
watch(() => route.fullPath, restore)
onMounted(() => { restore(); window.addEventListener('wms-mobile-resume', resume) })
onBeforeUnmount(() => { scanner.value?.stop(); disposed = true; gate.cancel(); choiceGate.cancel(); window.removeEventListener('wms-mobile-resume', resume) })
</script>

<template>

  <MobileTaskHeading title="原料上架" :task="task" :step="step" :allowed="allowed" :warning="warning" :error="error" :checking="checking" />
  <template v-if="step === 'start'"><div class="mobile-page-intro"><h2>开始原料上架</h2><p class="mobile-muted">选择具体入库明细，支持部分上架和多库位。</p></div><button class="mobile-button primary" :disabled="!allowed || Boolean(warning) || busy" @click="start(false)">新建原料上架</button><h3 v-if="drafts.length" class="mobile-section-title">未完成作业</h3><router-link v-for="draft in drafts" :key="draft.id" :to="putawayTaskPath(draft, lockedTask(draft) ? 'result' : 'form')" class="mobile-menu-item"><span><strong>{{ taskLabels[draft.status] }}</strong><small>入库明细 {{ draft.snapshot?.movementId || draft.fields.movementId || '尚未选择' }}</small></span></router-link></template>
  <section v-else-if="!task" class="mobile-card"><h2>这笔作业无法恢复</h2><p class="mobile-muted">可能已清理，或属于其他账号。不能重建原请求。</p><router-link to="/m/jobs/putaway" class="mobile-button primary">返回上架入口</router-link></section>
  <template v-else-if="locked"><section class="mobile-card"><p class="mobile-big-number">{{ formatQuantity(task.snapshot.quantity) }} {{ task.snapshot.unit }}</p><dl class="mobile-details"><div v-if="task.result?.documentNo"><dt>上架单号</dt><dd>{{ task.result.documentNo }}</dd></div><div v-if="result"><dt>原料 / 批号</dt><dd>{{ result.receipt.material_name }} / {{ result.receipt.batch_no }}</dd></div><div v-if="result"><dt>目标库位</dt><dd>{{ result.target.location_name }}</dd></div><div v-if="result"><dt>本笔待上架</dt><dd>{{ formatQuantity(result.receipt.pending_quantity) }} {{ result.receipt.unit }}</dd></div><div v-if="result"><dt>库位批次余额</dt><dd>{{ formatQuantity(result.target.remaining_quantity) }} {{ result.target.unit }}</dd></div></dl><details><summary>本笔明细与请求信息</summary><p class="mobile-small">请求标识 {{ task.requestId }}</p><p>入库单 {{ task.snapshot.receiptId }} · 明细 {{ task.snapshot.movementId }} · 库位 {{ task.snapshot.locationId }}</p><p>请求时间 {{ task.submittedAt }}</p><p>备注 {{ task.snapshot.notes || '—' }}</p></details></section>
    <template v-if="task.status === 'succeeded'"><p v-if="!result" class="mobile-warning">本笔已成功，记录、分配和库存待刷新，不能用旧量继续。</p><p v-else-if="!canContinue" class="mobile-warning">本记录已全部上架或已冲销，不能同件继续。</p><button v-if="canContinue" class="mobile-button primary" :disabled="busy || checking || !allowed || Boolean(warning)" @click="start(true)">同一货物继续上架</button><button :class="['mobile-button', { primary: !canContinue }]" :disabled="busy || !allowed || Boolean(warning)" @click="start(false)">下一件上架</button><button class="mobile-button" :disabled="checking" @click="refreshResult">刷新本笔记录与库存</button><router-link :to="{ path: '/m/read/document/' + task.result.id, query: { from: putawayOrigin(task.origin) } }" class="mobile-button">查看本笔上架单</router-link><router-link :to="{ path: '/m/read/receipt/' + task.snapshot.receiptId, query: { from: putawayOrigin(task.origin) } }" class="mobile-button">查看具体入库明细</router-link><details v-if="result" class="mobile-card"><summary>全部上架分配与相关库存</summary><p v-for="a in result.receipt.allocations" :key="a.id">{{ a.location_name }} · {{ formatQuantity(a.quantity) }} {{ result.receipt.unit }} · 单据 {{ a.document_id }} {{ a.reversed_by_document_id ? '（已冲销）' : '' }}</p><p v-for="s in result.stocks" :key="s.id">{{ s.location_name }} · {{ s.batch_no }} · {{ formatQuantity(s.remaining_quantity) }} {{ s.unit }}</p><p class="mobile-small mobile-muted">读取时间 {{ refreshed }}</p></details></template>
    <template v-else><p class="mobile-warning">{{ task.reason }}。原请求已保留，不能重交；空列表或相似记录不能确定结果。</p><router-link to="/m/records?view=putaways" class="mobile-button">查询上架记录（辅助核对）</router-link></template><router-link :to="putawayOrigin(task.origin)" class="mobile-button">返回原列表</router-link>
  </template>
  <MobileScanner v-else-if="step === 'scan' && qrConfig.instance" ref="scanner" :type="choice" :busy="busy" :error="scanError" @decoded="scanned" @cancel="cancelScan" />
  <section v-else-if="step === 'scan'" class="mobile-card"><p class="mobile-warning">{{ qrConfig.error }}</p><button class="mobile-button" @click="cancelScan">返回原表单</button></section>
  <MobileChoices :maxlength="choice === 'location' ? 80 : 120" v-else-if="step === 'choose'" :title="choice === 'location' ? '选择上架库位' : '选择待上架明细'" v-model:search="search" :placeholder="choice === 'location' ? '库位名称' : '原料 / 批号 / 入库单号'" :items="items" :total="total" :page="page" :pages="pages" :loading="loading" :error="choiceError" :busy="busy" :prefix="'s4b-' + choice + '-'" @search="choicePage(1)" @page="choicePage" @select="select" @cancel="returnToParent(router, putawayTaskPath(task))"><template #default="{ row }"><h3>{{ row.name || row.material_name }}</h3><template v-if="choice === 'receipt'"><p>{{ row.document_no }} · 明细 {{ row.receipt_movement_id }}</p><p>{{ row.batch_no }} · {{ row.supplier_name }}</p><p class="mobile-choice-quantity">待上架 {{ formatQuantity(row.pending_quantity) }} {{ row.unit }}</p><p>生产 {{ row.production_date }} / 到期 {{ row.expiry_date }}</p></template><p v-else>库位 {{ row.id }}</p></template></MobileChoices>
  <section v-else class="mobile-card"><p v-if="task.status === 'rejected'" class="mobile-error">本次提交未通过：{{ task.reason }}。修改后重新核对。</p>
    <template v-if="step === 'confirm'"><p class="mobile-muted mobile-small">本次原料上架</p><p class="mobile-big-number">{{ formatQuantity(form.quantity) }} {{ form.unit }}</p><dl class="mobile-details"><div><dt>原料 / 批号</dt><dd>{{ receipt?.material_name || '待读取' }} / {{ receipt?.batch_no }}</dd></div><div><dt>供应商</dt><dd>{{ receipt?.supplier_name }}</dd></div><div><dt>入库单 / 明细</dt><dd>{{ receipt?.document_no || form.receiptId }} / {{ form.movementId }}</dd></div><div><dt>目标库位</dt><dd>{{ location?.name || form.locationId }}</dd></div><div v-if="receipt"><dt>最新待上架</dt><dd>{{ formatQuantity(receipt.pending_quantity) }} {{ receipt.unit }}</dd></div><div><dt>备注</dt><dd>{{ form.notes || '—' }}</dd></div></dl><p class="mobile-advisory">提交前再次读取待上架量，后台最终校验。</p><button class="mobile-button primary" :disabled="busy || checking || !allowed || Boolean(warning)" @click="submit">确认提交原料上架</button><router-link :to="putawayTaskPath(task)" class="mobile-button">返回修改</router-link></template>
    <form v-else class="mobile-form" @submit.prevent="confirm"><fieldset :disabled="busy" class="mobile-putaway-fields"><label>具体入库明细<button type="button" class="mobile-pick" @click="choose('receipt')"><span>{{ receipt?.material_name || '选择待上架明细' }}<small v-if="receipt">{{ receipt.document_no }} · 明细 {{ receipt.receipt_movement_id }} · {{ receipt.batch_no }}</small></span><span aria-hidden="true">›</span></button></label><button type="button" class="mobile-text-button" :disabled="!qrConfig.instance || !allowed || Boolean(warning)" @click="scan('receipt')">扫描货物标签</button><p v-if="receipt" class="mobile-advisory">最新待上架 {{ formatQuantity(receipt.pending_quantity) }} {{ receipt.unit }}</p><label>目标库位<button type="button" class="mobile-pick" @click="choose('location')"><span>{{ location?.name || '选择上架库位' }}</span><span aria-hidden="true">›</span></button></label><button type="button" class="mobile-text-button" :disabled="!qrConfig.instance || !allowed || Boolean(warning)" @click="scan('location')">扫描库位标签</button><p v-if="qrConfig.error" class="mobile-advisory">{{ qrConfig.error }}，可使用手动查询。</p><label>本次上架数量 · {{ form.unit || '先选择明细' }}<input v-model="form.quantity" inputmode="decimal" placeholder="请输入数量" maxlength="80" autocomplete="off" /></label><details><summary>备注（选填）</summary><label>备注<textarea v-model="form.notes" maxlength="2000" rows="3" /></label></details><p class="mobile-advisory">最多六位小数，不自动舍入；连续作业清空旧数量。</p><button class="mobile-button primary" :disabled="busy || checking || !allowed || Boolean(warning)">下一步：核对上架</button></fieldset></form>
  </section>

</template>
<style scoped>
.mobile-putaway-fields { border: 0; padding: 0; margin: 0; min-width: 0; display: grid; gap: 16px; }
.mobile-putaway-fields label { display: grid; gap: 8px; }
summary { min-height: 44px; display: flex; align-items: center; cursor: pointer; }


</style>
