<script setup>
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
    if (!fields.receiptId) throw new Error('请手动选择具体入库明细')
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
  gate.cancel(); choiceGate.cancel(); checking.value = false; error.value = ''; receipt.value = null; location.value = null; result.value = null
  loadFields()
  if (step.value === 'choose') loadChoices()
  else if (task.value?.status === 'succeeded') refreshResult()
  else if (task.value && form.receiptId) check()
}
function resume() {
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
onBeforeUnmount(() => { disposed = true; gate.cancel(); choiceGate.cancel(); window.removeEventListener('wms-mobile-resume', resume) })
</script>

<template>
  <section class="mobile-card"><span class="mobile-pill">原料手动上架</span><p v-if="task">状态：{{ taskLabels[task.status] }}</p><p v-if="!allowed" class="mobile-warning">当前角色可查询，无原料上架权限。</p><p v-if="warning" class="mobile-error" role="alert">{{ warning }}</p><p v-if="error" class="mobile-error" role="alert">{{ error }}</p><p v-if="checking" role="status">正在重新核实记录、库位与剩余量…</p></section>
  <section v-if="step === 'start'" class="mobile-card"><h2>手动上架</h2><p>选择具体入库明细和库位，支持部分上架与同件多库位作业。</p><button class="mobile-button primary" :disabled="!allowed || Boolean(warning) || busy" @click="start(false)">开始手动上架 / 恢复草稿</button><router-link v-for="draft in drafts" :key="draft.id" :to="putawayTaskPath(draft, lockedTask(draft) ? 'result' : 'form')" class="mobile-button">恢复{{ taskLabels[draft.status] }} · 入库明细 {{ draft.snapshot?.movementId || draft.fields.movementId || '未选' }}</router-link></section>
  <section v-else-if="!task" class="mobile-card"><p>作业上下文不存在，可能已清理或属于其他账号。不能重建原请求。</p><router-link to="/m/jobs/putaway" class="mobile-button">返回手动上架入口</router-link></section>
  <section v-else-if="locked" class="mobile-card">
    <h2>{{ task.status === 'succeeded' ? '原料上架成功' : taskLabels[task.status] }}</h2><p>{{ task.reason }}</p>
    <dl class="mobile-details"><div><dt>入库单 / 明细ID</dt><dd>{{ task.snapshot.receiptId }} / {{ task.snapshot.movementId }}</dd></div><div><dt>目标库位ID</dt><dd>{{ task.snapshot.locationId }}</dd></div><div><dt>本次上架量</dt><dd>{{ formatQuantity(task.snapshot.quantity) }} {{ task.snapshot.unit }}</dd></div><div><dt>请求时间</dt><dd>{{ task.submittedAt }}</dd></div><div><dt>备注</dt><dd>{{ task.snapshot.notes || '—' }}</dd></div></dl>
    <template v-if="task.status === 'succeeded'">
      <p class="mobile-success">本笔已成功，旧表单不可再次提交。</p><p>上架单：{{ task.result.documentNo }}（ID {{ task.result.id }}）</p>
      <p v-if="!result" class="mobile-warning">成功、待刷新。本笔记录、分配与库位库存待读取，不能用旧量继续。</p>
      <template v-else><h3>{{ result.receipt.material_name }} · {{ result.receipt.batch_no }}</h3><p>本笔入库单：{{ result.receipt.document_no }} · 明细 {{ result.receipt.receipt_movement_id }}</p><p>本笔已上架量（实查）：{{ formatQuantity(result.receipt.shelved_quantity) }} {{ result.receipt.unit }}</p><p>本笔待上架量（实查）：{{ formatQuantity(result.receipt.pending_quantity) }} {{ result.receipt.unit }}</p><p>目标库位 {{ result.target.location_name }} · 库位批次余额（实查）：{{ formatQuantity(result.target.remaining_quantity) }} {{ result.target.unit }}</p><details><summary>本笔全部上架分配与相关库位库存</summary><p v-for="a in result.receipt.allocations" :key="a.id">上架单ID {{ a.document_id }} · {{ a.location_name }} · {{ formatQuantity(a.quantity) }} {{ result.receipt.unit }} {{ a.reversed_by_document_id ? '（已冲销）' : '' }}</p><p v-for="s in result.stocks" :key="s.id">库位 {{ s.location_name }} · 批次ID {{ s.id }} · {{ s.batch_no }} · {{ formatQuantity(s.remaining_quantity) }} {{ s.unit }}</p></details><p v-if="!canContinue" class="mobile-warning">本记录已全部上架或已冲销，不得同件继续。</p></template>
      <p v-if="refreshed" class="mobile-muted">读取时间：{{ refreshed }}</p><button class="mobile-button" :disabled="checking" @click="refreshResult">重新读取本笔记录、分配与库存</button>
      <router-link :to="{ path: `/m/read/document/${task.result.id}`, query: { from: putawayOrigin(task.origin) } }" class="mobile-button">查看本笔上架单</router-link><router-link :to="{ path: `/m/read/receipt/${task.snapshot.receiptId}`, query: { from: putawayOrigin(task.origin) } }" class="mobile-button">查看具体入库明细</router-link>
      <button v-if="canContinue" class="mobile-button primary" :disabled="busy || checking || !allowed || Boolean(warning)" @click="start(true)">同一货物继续（重新读取剩余量）</button><button class="mobile-button primary" :disabled="busy || !allowed || Boolean(warning)" @click="start(false)">下一件（手动选择新作业）</button>
    </template>
    <template v-else><p class="mobile-warning">结果待核实。原请求键与内容保留；空列表或相似记录不能确定结果，不自动补记，不可重交。</p><router-link to="/m/records?view=putaways" class="mobile-button">查询上架记录（仅辅助核对）</router-link></template>
    <router-link :to="putawayOrigin(task.origin)" class="mobile-button">返回原列表</router-link>
  </section>
  <section v-else-if="step === 'choose'" class="mobile-card"><h2>选择{{ choice === 'location' ? '库位' : '具体待上架明细' }}</h2><p v-if="choice === 'receipt'" class="mobile-warning">换另一明细会清空旧库位、数量和请求状态。</p><form class="mobile-search" @submit.prevent="choicePage(1)"><input v-model="search" :aria-label="choice === 'location' ? '搜索库位名称' : '搜索原料编码、名称、批号或入库单号'" :maxlength="choice === 'location' ? 80 : 120" /><button class="mobile-button primary">搜索</button></form><p v-if="loading" class="mobile-status" role="status">正在查询…</p><p v-if="choiceError" class="mobile-error" role="alert">{{ choiceError }}</p><button v-if="choiceError" class="mobile-button" @click="loadChoices">重试查询</button><div v-if="!loading && !choiceError" data-mobile-list-ready><article v-for="row in items" :id="`s4b-${choice}-${row.id}`" :key="row.id" data-mobile-anchor class="mobile-card"><h3>{{ row.name || row.material_name }}</h3><template v-if="choice === 'receipt'"><p>{{ row.document_no }} · 入库单ID {{ row.id }} · 明细ID {{ row.receipt_movement_id }}</p><p>{{ row.batch_no }} · {{ row.supplier_name }} · {{ row.production_date }} / {{ row.expiry_date }}</p><p>待上架 {{ formatQuantity(row.pending_quantity) }} {{ row.unit }}</p></template><p v-else>库位ID {{ row.id }}</p><button class="mobile-button" :disabled="busy" @click="select(row)">选择{{ row.name || row.document_no }}</button></article><p v-if="!items.length">没有匹配的可用记录。</p><p>共 {{ total }} 条 · 第 {{ page }} / {{ pages }} 页</p><div class="mobile-pagination"><button class="mobile-button" :disabled="page <= 1 || busy" @click="choicePage(page - 1)">上一页</button><button class="mobile-button" :disabled="page >= pages || busy" @click="choicePage(page + 1)">下一页</button></div></div><router-link :to="putawayTaskPath(task)" class="mobile-button">取消选择，返回表单</router-link></section>
  <section v-else class="mobile-card"><p v-if="task.status === 'rejected'" class="mobile-error">明确拒绝：{{ task.reason }}。可修改后重新核对。</p><h2>{{ step === 'confirm' ? '核对原料上架' : '上架表单' }}</h2><dl v-if="receipt" class="mobile-details"><div><dt>原料 / 批号</dt><dd>{{ receipt.material_name }}（{{ receipt.material_code }}） / {{ receipt.batch_no }}</dd></div><div><dt>供应商 / 日期</dt><dd>{{ receipt.supplier_name }} / {{ receipt.production_date }}～{{ receipt.expiry_date }}</dd></div><div><dt>入库单 / 明细</dt><dd>{{ receipt.document_no }} / {{ receipt.receipt_movement_id }}</dd></div><div><dt>最新待上架量</dt><dd>{{ formatQuantity(receipt.pending_quantity) }} {{ receipt.unit }}</dd></div></dl>
    <template v-if="step === 'confirm'"><p>库位：{{ location?.name || form.locationId }}</p><p>本次上架：{{ formatQuantity(form.quantity) }} {{ form.unit }}</p><p>备注：{{ form.notes || '—' }}</p><p>提交前再次读取；最终剩余量由原后台事务校验。</p><button class="mobile-button primary" :disabled="busy || checking || !allowed || Boolean(warning)" @click="submit">确认提交原料上架</button><router-link :to="putawayTaskPath(task)" class="mobile-button">返回修改</router-link></template>
    <form v-else class="mobile-form" @submit.prevent="confirm"><fieldset :disabled="busy" class="mobile-putaway-fields"><div>具体入库明细<button type="button" class="mobile-button" @click="choose('receipt')">手动查询待上架明细</button></div><div>目标库位<button type="button" class="mobile-button" @click="choose('location')">{{ location?.name || (form.locationId ? `库位ID ${form.locationId}` : '手动查询库位') }}</button></div><label>本次上架数量 · {{ form.unit || '先选择明细' }}<input v-model="form.quantity" inputmode="decimal" maxlength="80" autocomplete="off" /></label><p class="mobile-muted mobile-small">最多六位小数，不自动舍入；连续作业清空旧数量。</p><details><summary>备注（选填）</summary><label>备注<textarea v-model="form.notes" maxlength="2000" rows="3"></textarea></label></details><button class="mobile-button primary" :disabled="busy || checking || !allowed || Boolean(warning)">下一步：核对上架</button></fieldset></form>
  </section>
</template>
<style scoped>
.mobile-putaway-fields { border: 0; padding: 0; margin: 0; min-width: 0; display: grid; gap: 16px; }
.mobile-putaway-fields label { display: grid; gap: 8px; }
summary { min-height: 44px; display: flex; align-items: center; cursor: pointer; }
.mobile-details div { display: grid; gap: 4px; }
.mobile-details dt { width: auto; font-size: 14px; }
</style>
