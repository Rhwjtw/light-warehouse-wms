<script setup>
import MobileChoices from './MobileChoices.vue'
import MobileTaskHeading from './MobileTaskHeading.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { auth } from '../auth'
import { canOperate } from '../permissions'
import { formatQuantity } from '../quantity'
import { mobileRevision, mobileStore, touchMobile } from './session'
import { lockedTask, taskLabels } from './taskState'
import { readGate } from './readQueries'
import { returnToParent } from './navigation'
import { stockKinds, stockLabels, stockFields, stockSnapshot, stockPayload, stockPath, stockOrigin, readStockSelection, readStockResult } from './stockJobs'

const route = useRoute(), router = useRouter(), form = reactive(stockFields())
const kind = computed(() => route.meta.stockKind), label = computed(() => stockLabels[kind.value])
const raw = computed(() => kind.value === 'material-outbound'), incoming = computed(() => kind.value === 'finished-receipt')
const step = computed(() => route.params.step || 'start')
const task = computed(() => { mobileRevision.value; const t = mobileStore()?.get(route.params.jobId); return t?.kind === kind.value ? t : null })
const drafts = computed(() => { mobileRevision.value; return mobileStore()?.tasks().filter(t => t.kind === kind.value && t.status !== 'succeeded') || [] })
const allowed = computed(() => canOperate(auth.user?.role, raw.value ? 'material.inventory.write' : 'finished.inventory.write'))
const locked = computed(() => lockedTask(task.value))
const warning = computed(() => { mobileRevision.value; return mobileStore()?.warning || '' })
const error = ref(''), busy = ref(false), checking = ref(false), selection = ref(null), result = ref(null), confirmed = ref(null)
const items = ref([]), total = ref(0), search = ref(''), loading = ref(false), choiceError = ref('')
const gate = readGate(), choiceGate = readGate()
const page = computed(() => /^[1-9]\d*$/.test(route.query.page) && Number.isSafeInteger(Number(route.query.page)) ? Number(route.query.page) : 1)
const pages = computed(() => Math.max(1, Math.ceil(total.value / 20)))
const batchChoice = computed(() => !raw.value && route.query.kind === 'batch')
let restoring = false, disposed = false, editVersion = 0
const choices = new Map()
watch(form, () => { if (!restoring && task.value && !locked.value) { editVersion++; confirmed.value = null; mobileStore().edit(task.value.id, form); touchMobile() } }, { flush: 'sync' })
function start() {
  if (!allowed.value || warning.value || busy.value) return
  const opened = mobileStore().create(kind.value, 'new', stockOrigin(task.value?.origin || route.query.from))
  touchMobile(); router.push(stockPath(opened, lockedTask(opened) ? 'result' : 'form'))
}
async function check() {
  const original = task.value, store = mobileStore(), ticket = gate.begin(), version = editVersion
  if (!original || lockedTask(original)) return false
  // 保留已读卡片的位置，避免连点时按钮移动到“返回修改”；读取中明确标为上次读取。
  checking.value = true; error.value = ''
  try {
    const user = (await api.get('/auth/me')).data
    if (user.id !== auth.user?.id || store !== mobileStore()) throw new Error('账户已变化，请重新登录')
    auth.user = user
    const data = await readStockSelection(api, original.kind, original.fields)
    if (!gate.current(ticket) || disposed || store !== mobileStore() || task.value?.id !== original.id || editVersion !== version) return false
    selection.value = data
    return true
  } catch (e) { if (gate.current(ticket)) { error.value = e.message; selection.value = null } return false }
  finally { if (gate.current(ticket)) checking.value = false }
}
function confirmation() {
  const s = stockSnapshot(kind.value, task.value.fields)
  const data = selection.value
  return JSON.stringify({ id: task.value.id, snapshot: s, balance: data.balance, batches: s.allocations.map(a => [a.batchId, data.batches.find(b => String(b.id) === a.batchId)?.quantity]) })
}
async function confirm() {
  if (!task.value || locked.value || busy.value) return
  busy.value = true
  try {
    stockSnapshot(kind.value, form)
    if (!await check() || disposed) return
    if (!allowed.value) throw new Error('当前角色无本业务写入权限')
    confirmed.value = confirmation()
    if (step.value !== 'confirm') await router.push(stockPath(task.value, 'confirm'))
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
async function submit() {
  if (!task.value || locked.value || busy.value || step.value !== 'confirm') return
  busy.value = true; error.value = ''
  let started, store
  try {
    if (!await check() || disposed) return
    if (!confirmed.value || confirmation() !== confirmed.value) {
      confirmed.value = null
      throw new Error('余额或核对上下文已变化，请点击重新核对；原数量保留，不自动替换提交')
    }
    store = mobileStore(); started = store.begin(task.value.id, { ready: true, allowed: allowed.value }); touchMobile()
    const response = await api.post('/' + kind.value + 's', stockPayload(started))
    store.settle(started.id, 'succeeded', '', response.data)
  } catch (e) {
    if (started) store.settle(started.id, [403, 422].includes(e.status) ? 'rejected' : 'uncertain', e.message)
    else error.value = e.message
  } finally {
    touchMobile(); busy.value = false
    if (started && !disposed && mobileStore() === store && task.value?.id === started.id) {
      await router.replace(stockPath(started, 'result'))
      if (task.value?.status === 'succeeded') await refreshResult()
    }
  }
}
async function refreshResult() {
  const original = task.value, store = mobileStore(), ticket = gate.begin()
  if (original?.status !== 'succeeded') return
  checking.value = true; error.value = ''; result.value = null
  try {
    const data = await readStockResult(api, original)
    if (gate.current(ticket) && !disposed && mobileStore() === store && task.value?.id === original.id) result.value = data
  } catch (e) { if (gate.current(ticket)) error.value = `本笔已成功，待刷新：${e.message}` }
  finally { if (gate.current(ticket)) checking.value = false }
}
function choose(batch = false) {
  if (!task.value || locked.value || busy.value) return
  const saved = choices.get(`${task.value.id}:${batch}`) || {}
  router.push({ path: stockPath(task.value, 'choose'), query: { ...saved, kind: batch ? 'batch' : 'item' } })
}
function choicePage(value) { router.replace({ path: route.path, query: { kind: batchChoice.value ? 'batch' : 'item', q: search.value || undefined, page: String(value) } }) }
async function loadChoices() {
  if (step.value !== 'choose' || !task.value || locked.value) return
  const ticket = choiceGate.begin(), path = route.fullPath, original = task.value, store = mobileStore()
  loading.value = true; choiceError.value = ''; items.value = []; total.value = 0
  search.value = typeof route.query.q === 'string' ? route.query.q.slice(0, 120) : ''
  choices.set(`${original.id}:${batchChoice.value}`, { ...route.query })
  try {
    if (batchChoice.value) {
      // 原接口按成品返回完整批次数组；完整读取后分页，不截断首100条。
      const data = await readStockSelection(api, kind.value, original.fields)
      if (!choiceGate.current(ticket) || store !== mobileStore()) return
      selection.value = data
      const rows = data.batches.filter(b => (b.label || '').includes(search.value))
      total.value = rows.length; items.value = rows.slice((page.value - 1) * 20, page.value * 20)
    } else {
      const response = (await api.get(raw.value ? '/material-batches' : '/products', { params: { q: search.value, page: page.value, page_size: 20, ...(!raw.value ? { is_active: true } : {}) } })).data
      if (!choiceGate.current(ticket) || store !== mobileStore()) return
      items.value = response.items; total.value = response.total
    }
    if (page.value > pages.value) { choicePage(pages.value); return }
    loading.value = false
    await nextTick(); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    if (!choiceGate.current(ticket) || route.fullPath !== path) return
    const position = store.list(path), anchor = position.anchor && document.getElementById(position.anchor)
    window.scrollTo({ top: anchor ? window.scrollY + anchor.getBoundingClientRect().top - 80 : position.top })
  } catch (e) { if (choiceGate.current(ticket)) choiceError.value = e.message }
  finally { if (choiceGate.current(ticket)) loading.value = false }
}
async function select(row) {
  if (!task.value || locked.value || busy.value) return
  busy.value = true; choiceError.value = ''
  const original = task.value, store = mobileStore(), ticket = choiceGate.begin(), batch = batchChoice.value
  try {
    let fields
    if (batch) {
      const data = await readStockSelection(api, kind.value, original.fields), current = data.batches.find(b => b.id === row.id)
      if (!current || !['batch', 'aggregate'].includes(current.stock_scope) || current.unit !== form.unit) throw new Error('批次身份或归属无效')
      if (original.fields.allocations.some(a => a.batchId === String(row.id))) throw new Error('该批次已经选择')
      if (original.fields.allocations.length >= 100) throw new Error('原API每笔最多100条分配，请另办新单据')
      fields = { ...original.fields, allocations: [...original.fields.allocations, { batchId: String(row.id), scope: current.stock_scope, quantity: '' }] }
    } else if (raw.value) {
      const current = (await api.get(`/material-location-stock/${row.id}`)).data
      fields = stockFields({ itemId: current.material_id, batchId: current.id, locationId: current.location_id, unit: current.unit })
      await readStockSelection(api, kind.value, fields)
    } else {
      const current = (await api.get(`/products/${row.id}`)).data
      if (current.id !== row.id || !current.is_active) throw new Error('成品已停用或身份不符')
      fields = stockFields({ itemId: current.id, unit: current.unit })
      await readStockSelection(api, kind.value, fields)
    }
    if (disposed || !choiceGate.current(ticket) || store !== mobileStore() || task.value?.id !== original.id || locked.value) return
    const changed = store.selectStock(original.id, fields)
    touchMobile(); confirmed.value = null
    returnToParent(router, stockPath(changed, lockedTask(changed) ? 'result' : 'form'))
  } catch (e) { choiceError.value = e.message }
  finally { busy.value = false }
}
function restore() {
  gate.cancel(); choiceGate.cancel(); checking.value = false; loading.value = false; error.value = ''; selection.value = null; result.value = null
  restoring = true; Object.assign(form, stockFields(task.value?.fields)); restoring = false
  if (step.value === 'choose') loadChoices()
  else if (task.value?.status === 'succeeded') refreshResult()
  else if (task.value && !locked.value && form.itemId) check()
}
function resume() {
  confirmed.value = null
  if (step.value === 'choose') {
    const anchor = [...document.querySelectorAll('[data-mobile-anchor]')].find(el => el.getBoundingClientRect().bottom > 80)
    if (document.querySelector('[data-mobile-list-ready]')) mobileStore()?.saveList(route.fullPath, { top: window.scrollY, anchor: anchor?.id })
    loadChoices()
  } else if (task.value?.status === 'succeeded') refreshResult()
  else check()
}
watch(() => route.fullPath, restore)
onMounted(() => { restore(); window.addEventListener('wms-mobile-resume', resume) })
onBeforeUnmount(() => { disposed = true; gate.cancel(); choiceGate.cancel(); window.removeEventListener('wms-mobile-resume', resume) })
</script>

<template>

  <MobileTaskHeading :title="label" :task="task" :step="step" :allowed="allowed" :warning="warning" :error="error" :checking="checking" />
  <template v-if="step === 'start'"><div class="mobile-page-intro"><h2>开始{{ label }}</h2><p class="mobile-muted">{{ incoming ? '普通成品入库；生产完工入库从生产单办理。' : '选择具体库存，核对后办理普通出库。' }}</p></div><button class="mobile-button primary" :disabled="!allowed || Boolean(warning)" @click="start">新建{{ label }}</button><h3 v-if="drafts.length" class="mobile-section-title">未完成作业</h3><router-link v-for="draft in drafts" :key="draft.id" :to="stockPath(draft, lockedTask(draft) ? 'result' : 'form')" class="mobile-menu-item"><span><strong>{{ taskLabels[draft.status] }}</strong><small>货物 {{ draft.fields.itemId || draft.snapshot?.itemId || '尚未选择' }}</small></span></router-link></template>
  <section v-else-if="!task" class="mobile-card"><h2>这笔作业无法恢复</h2><p class="mobile-muted">上下文不存在，不能重建原请求。</p><router-link to="/m/jobs" class="mobile-button primary">返回作业入口</router-link></section>
  <template v-else-if="locked"><section class="mobile-card"><p class="mobile-big-number">{{ formatQuantity(task.snapshot.quantity) }} {{ task.snapshot.unit }}</p><dl class="mobile-details"><div v-if="task.result?.documentNo"><dt>单据号</dt><dd>{{ task.result.documentNo }}</dd></div><div v-if="raw && result"><dt>原料 / 批号</dt><dd>{{ result.detail.material_name }} / {{ result.detail.batch_no }}</dd></div><div v-if="raw && result"><dt>库位</dt><dd>{{ result.detail.location_name }}</dd></div><div v-if="raw && result"><dt>最新余额</dt><dd>{{ formatQuantity(result.detail.remaining_quantity) }} {{ result.detail.unit }}</dd></div><div v-if="!raw && result"><dt>成品</dt><dd>{{ result.detail.product_name || result.detail.stock.name }}</dd></div><div v-if="!raw && result"><dt>库存合计</dt><dd>{{ formatQuantity(result.detail.stock.quantity) }} {{ result.detail.stock.unit }}</dd></div></dl><details><summary>本次库存分配与请求信息</summary><p class="mobile-small">请求标识 {{ task.requestId }}</p><p>货物 ID {{ task.snapshot.itemId }} <span v-if="raw">· 库位 {{ task.snapshot.locationId }} · 批次 {{ task.snapshot.batchId }}</span></p><p v-for="a in task.snapshot.allocations" :key="a.batchId">{{ a.scope === 'aggregate' ? '历史无批次归属库存' : '真实批次' }} {{ a.batchId }} · {{ formatQuantity(a.quantity) }} {{ task.snapshot.unit }}</p></details></section>
    <template v-if="task.status === 'succeeded'"><p v-if="!result" class="mobile-warning">本笔已成功，记录与相关库存待刷新，不能重复提交。</p><details v-if="result" class="mobile-card"><summary>相关批次库存</summary><p v-for="b in result.stocks" :key="b.id">{{ b.stock_scope === 'aggregate' ? '历史无批次归属库存' : b.label || b.batch_no }} · {{ b.location_name || '' }} · {{ formatQuantity(b.quantity ?? b.remaining_quantity) }} {{ b.unit }}</p></details><button class="mobile-button primary" :disabled="!allowed || Boolean(warning)" @click="start">继续{{ label }}</button><button class="mobile-button" :disabled="checking" @click="refreshResult">刷新本笔单据与库存</button><router-link :to="{ path: '/m/read/document/' + task.result.id, query: { from: stockOrigin(task.origin) } }" class="mobile-button">查看本笔单据</router-link></template>
    <template v-else><p class="mobile-warning">{{ task.reason }}。原请求已保留，不能重交。空列表或相似记录不能确定结果。</p><router-link to="/m/records" class="mobile-button">查询记录（辅助核对）</router-link></template><router-link :to="stockOrigin(task.origin)" class="mobile-button">返回原列表</router-link>
  </template>
  <MobileChoices v-else-if="step === 'choose'" :title="batchChoice ? '选择成品库存' : raw ? '选择出库库存' : '选择成品'" v-model:search="search" :items="items" :total="total" :page="page" :pages="pages" :loading="loading" :error="choiceError" :busy="busy" prefix="s5-" @search="choicePage(1)" @page="choicePage" @select="select" @cancel="returnToParent(router, stockPath(task))"><template #default="{ row }"><h3>{{ row.name || row.material_name || row.label }}</h3><p>{{ row.code || row.material_code }} <span v-if="row.unit">· {{ row.unit }}</span></p><p v-if="raw">{{ row.batch_no }} · {{ row.location_name }} · {{ row.supplier_name }}</p><p v-if="raw" class="mobile-small">到期 {{ row.expiry_date }} {{ row.expired ? '（已过期）' : '' }}</p><p v-if="raw || batchChoice" class="mobile-choice-quantity">可用 {{ formatQuantity(row.quantity ?? row.remaining_quantity) }} {{ row.unit }}</p><p v-if="batchChoice">{{ row.stock_scope === 'aggregate' ? '历史无批次归属库存' : '真实批次' }}</p></template></MobileChoices>
  <section v-else class="mobile-card"><p v-if="task.status === 'rejected'" class="mobile-error">本次提交未通过：{{ task.reason }}。修改后重新核对。</p>
    <template v-if="step === 'confirm'"><p class="mobile-muted mobile-small">本次{{ label }}</p><p class="mobile-big-number">{{ formatQuantity(form.quantity) }} {{ form.unit }}</p><dl class="mobile-details"><div><dt>货物</dt><dd>{{ selection?.item.name || form.itemId }}</dd></div><div v-if="raw && selection"><dt>批号 / 库位</dt><dd>{{ selection.row.batch_no }} / {{ selection.row.location_name }}</dd></div><div v-if="selection"><dt>{{ checking ? '上次读取余额' : '最新余额' }}</dt><dd>{{ formatQuantity(selection.balance) }} {{ form.unit }}</dd></div><div v-if="!raw"><dt>业务日期</dt><dd>{{ form.businessDate }}</dd></div><div v-if="!raw"><dt>关联单据</dt><dd>{{ form.referenceNo || '—' }}</dd></div><div v-for="a in form.allocations" :key="a.batchId"><dt>{{ a.scope === 'aggregate' ? '历史库存' : '真实批次' }}</dt><dd>{{ selection?.batches.find(b => String(b.id) === a.batchId)?.batch_no || a.batchId }} · {{ formatQuantity(a.quantity) }} {{ form.unit }}</dd></div><div><dt>备注</dt><dd>{{ form.notes || '—' }}</dd></div></dl><p v-if="incoming" class="mobile-advisory">普通入库计入历史无批次归属库存。</p><p class="mobile-advisory">提交前读取最新余额；如有变化，需重新核对。</p><button class="mobile-button primary" :disabled="busy || checking || !allowed || Boolean(warning)" @click="submit">确认提交{{ label }}</button><button class="mobile-button" :disabled="busy || checking" @click="confirm">重新核对</button><router-link :to="stockPath(task)" class="mobile-button">返回修改</router-link></template>
    <form v-else class="mobile-form" @submit.prevent="confirm"><fieldset :disabled="busy" class="mobile-stock-fields"><label>{{ raw ? '出库库存' : '成品' }}<button type="button" class="mobile-pick" @click="choose(false)"><span>{{ selection?.item.name || (form.itemId ? '正在核对所选货物…' : raw ? '选择具体库位批次' : '选择成品') }}<small v-if="raw && selection">{{ selection.row.batch_no }} · {{ selection.row.location_name }}</small></span><span aria-hidden="true">›</span></button></label><p v-if="selection" class="mobile-advisory">{{ checking ? '上次读取余额（正在核实）' : '最新余额' }} {{ formatQuantity(selection.balance) }} {{ form.unit }}</p><label>{{ incoming ? '入库数量' : '出库数量' }} · {{ form.unit || '先选择货物' }}<input v-model="form.quantity" inputmode="decimal" placeholder="请输入数量" maxlength="80" autocomplete="off" /></label><template v-if="!raw"><label>业务日期<input v-model="form.businessDate" type="date" /></label><label>关联单据（选填）<input v-model="form.referenceNo" placeholder="请输入关联单据号" maxlength="120" /></label></template><template v-if="!raw && !incoming"><h3 class="mobile-section-title">库存分配</h3><button type="button" class="mobile-button" :disabled="!form.itemId" @click="choose(true)">添加真实批次或历史库存</button><div v-for="(a, index) in form.allocations" :key="a.batchId" class="mobile-allocation"><span class="mobile-pill">{{ a.scope === 'aggregate' ? '历史无批次归属库存' : '真实批次' }}</span><label>{{ selection?.batches.find(b => String(b.id) === a.batchId)?.batch_no || '库存 ' + a.batchId }} · 分配数量<input v-model="a.quantity" :aria-label="'批次 ' + a.batchId + ' 分配数量'" inputmode="decimal" maxlength="80" /></label><button type="button" class="mobile-text-button" @click="form.allocations.splice(index, 1)">移除此分配</button></div></template><p v-if="incoming" class="mobile-warning">普通入库计入历史无批次归属库存；生产完工入库从生产单办理。</p><details><summary>备注（选填）</summary><label>备注<textarea v-model="form.notes" maxlength="2000" rows="3" /></label></details><p class="mobile-advisory">最多六位小数，不自动舍入。</p><button class="mobile-button primary" :disabled="busy || checking || !allowed || Boolean(warning)">下一步：核对{{ label }}</button></fieldset></form>
  </section>

</template>
<style scoped>
.mobile-stock-fields { border: 0; padding: 0; margin: 0; min-width: 0; display: grid; gap: 16px; }
.mobile-stock-fields label { display: grid; gap: 8px; }
summary { min-height: 44px; display: flex; align-items: center; cursor: pointer; }
</style>
