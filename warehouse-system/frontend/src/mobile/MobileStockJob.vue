<script setup>
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
  <section class="mobile-card"><span class="mobile-pill">{{ label }}</span><p v-if="task">状态：{{ taskLabels[task.status] }}</p><p v-if="!allowed" class="mobile-warning">当前角色可查询，无本业务写入权限。</p><p v-if="warning" class="mobile-error" role="alert">{{ warning }}</p><p v-if="error" class="mobile-error" role="alert">{{ error }}</p><p v-if="checking" role="status">正在重新核实记录与最新余额…</p></section>
  <section v-if="step === 'start'" class="mobile-card"><h2>{{ label }}</h2><p v-if="incoming">普通入库计入历史无批次归属库存；生产完工入库必须从生产单办理。</p><p v-else>明确选择具体库存，核对后提交；不自动分摊历史归属。</p><button class="mobile-button primary" :disabled="!allowed || Boolean(warning)" @click="start">开始{{ label }} / 恢复草稿</button><router-link v-for="draft in drafts" :key="draft.id" :to="stockPath(draft, lockedTask(draft) ? 'result' : 'form')" class="mobile-button">恢复{{ taskLabels[draft.status] }} · 货物ID {{ draft.fields.itemId || draft.snapshot?.itemId || '未选' }}</router-link></section>
  <section v-else-if="!task" class="mobile-card"><p>作业上下文不存在，不能重建原请求。</p><router-link to="/m/jobs" class="mobile-button">返回作业</router-link></section>
  <section v-else-if="locked" class="mobile-card"><h2>{{ task.status === 'succeeded' ? `${label}成功` : taskLabels[task.status] }}</h2><p>{{ task.reason }}</p><p>本次：{{ formatQuantity(task.snapshot.quantity) }} {{ task.snapshot.unit }} · 货物ID {{ task.snapshot.itemId }}</p><p v-if="raw">具体库位ID {{ task.snapshot.locationId }} · 批次ID {{ task.snapshot.batchId }}</p><p v-for="a in task.snapshot.allocations" :key="a.batchId">{{ a.scope === 'aggregate' ? '历史无批次归属库存' : '真实批次' }} ID {{ a.batchId }} · {{ formatQuantity(a.quantity) }} {{ task.snapshot.unit }}</p>
    <template v-if="task.status === 'succeeded'"><p class="mobile-success">本笔已成功，旧表单不可再次提交。</p><p>单据：{{ task.result.documentNo }}（ID {{ task.result.id }}）</p><p v-if="!result" class="mobile-warning">成功、待刷新：本笔单据、明细与相关库存待读取。</p><template v-else><p>本笔明细已核验，流水 {{ result.document.transactions.length }} 条。</p><p v-if="raw">所选库位批次余额（实查）：{{ formatQuantity(result.detail.remaining_quantity) }} {{ result.detail.unit }}</p><p v-else>成品汇总库存（实查）：{{ formatQuantity(result.detail.stock.quantity) }} {{ result.detail.stock.unit }}</p><details><summary>相关批次库存（实查）</summary><p v-for="b in result.stocks" :key="b.id">{{ b.stock_scope === 'aggregate' ? '历史无批次归属库存' : b.label || b.batch_no }} · ID {{ b.id }} {{ b.location_name || '' }} · {{ formatQuantity(b.quantity ?? b.remaining_quantity) }} {{ b.unit }}</p></details></template><button class="mobile-button" :disabled="checking" @click="refreshResult">重新读取本笔单据与库存</button><router-link :to="{ path: `/m/read/document/${task.result.id}`, query: { from: stockOrigin(task.origin) } }" class="mobile-button">查看本笔单据</router-link><button class="mobile-button primary" :disabled="!allowed || Boolean(warning)" @click="start">下一笔（清空旧数量的新作业）</button></template>
    <template v-else><p class="mobile-warning">结果待核实。保留原请求键与内容；空列表或相似记录不能确定结果，不自动补记，不可重交。</p><router-link to="/m/records" class="mobile-button">查询记录（仅辅助核对）</router-link></template><router-link :to="stockOrigin(task.origin)" class="mobile-button">返回原列表</router-link>
  </section>
  <section v-else-if="step === 'choose'" class="mobile-card"><h2>{{ batchChoice ? '明确选择成品批次或历史库存' : raw ? '选择具体库位批次' : '选择成品' }}</h2><form class="mobile-search" @submit.prevent="choicePage(1)"><input v-model="search" aria-label="搜索货物或批次" maxlength="120" /><button class="mobile-button primary">搜索</button></form><p v-if="loading" class="mobile-status" role="status">正在查询…</p><p v-if="choiceError" class="mobile-error" role="alert">{{ choiceError }}</p><button v-if="choiceError" class="mobile-button" @click="loadChoices">重试查询</button><div v-if="!loading && !choiceError" data-mobile-list-ready><article v-for="row in items" :id="`s5-${row.id}`" :key="row.id" data-mobile-anchor class="mobile-card"><h3>{{ row.name || row.material_name || row.label }}</h3><p>{{ row.code || row.material_code }} · ID {{ row.id }} · {{ row.unit }}</p><p v-if="raw">批号 {{ row.batch_no }} · 库位 {{ row.location_name }}（ID {{ row.location_id }}） · {{ row.supplier_name }} · 到期 {{ row.expiry_date }} {{ row.expired ? '已过期' : '' }}</p><p v-if="raw || batchChoice">余额 {{ formatQuantity(row.quantity ?? row.remaining_quantity) }} {{ row.unit }} {{ row.stock_scope === 'aggregate' ? '历史无批次归属，不推测生产单' : row.stock_scope === 'batch' ? '真实批次' : '' }}</p><button class="mobile-button" :disabled="busy" @click="select(row)">选择 ID {{ row.id }}</button></article><p v-if="!items.length">没有匹配记录。</p><p>共 {{ total }} 条 · 第 {{ page }} / {{ pages }} 页</p><div class="mobile-pagination"><button class="mobile-button" :disabled="page <= 1 || busy" @click="choicePage(page - 1)">上一页</button><button class="mobile-button" :disabled="page >= pages || busy" @click="choicePage(page + 1)">下一页</button></div></div><router-link :to="stockPath(task)" class="mobile-button">取消选择，返回表单</router-link></section>
  <section v-else class="mobile-card"><h2>{{ step === 'confirm' ? `核对${label}` : label }}</h2><p v-if="task.status === 'rejected'" class="mobile-error">明确拒绝：{{ task.reason }}。修改后重新核对。</p><p v-if="selection">{{ selection.item.name }}（{{ selection.item.code }}） · {{ checking ? '上次读取余额（正在核实）' : '最新余额' }} {{ formatQuantity(selection.balance) }} {{ form.unit }}</p><p v-if="raw && selection">批号 {{ selection.row.batch_no }} · 库位 {{ selection.row.location_name }} · 批次ID {{ form.batchId }} · 到期 {{ selection.row.expiry_date }}</p>
    <template v-if="step === 'confirm'"><p>本次{{ incoming ? '入库' : '出库' }}：{{ formatQuantity(form.quantity) }} {{ form.unit }}</p><p v-if="!raw">业务日期 {{ form.businessDate }} · 普通{{ incoming ? '入库（无批次归属）' : '出库' }}</p><p v-for="a in form.allocations" :key="a.batchId">{{ a.scope === 'aggregate' ? '历史无批次归属库存' : '真实批次' }} ID {{ a.batchId }}：{{ formatQuantity(a.quantity) }} {{ form.unit }}</p><p>备注：{{ form.notes || '—' }}</p><p>提交前重读最新余额；变化后必须重新核对，数量不会自动替换。</p><button class="mobile-button" :disabled="busy || checking" @click="confirm">重新核对</button><button class="mobile-button primary" :disabled="busy || checking || !allowed || Boolean(warning)" @click="submit">确认提交{{ label }}</button><router-link :to="stockPath(task)" class="mobile-button">返回修改</router-link></template>
    <form v-else class="mobile-form" @submit.prevent="confirm"><fieldset :disabled="busy" class="mobile-stock-fields"><button type="button" class="mobile-button" @click="choose(false)">{{ raw ? '手动查询具体库位批次' : '手动查询成品' }}</button><p>货物ID {{ form.itemId || '未选' }} · {{ form.unit || '先选择货物' }}</p><label>本次数量<input v-model="form.quantity" inputmode="decimal" maxlength="80" autocomplete="off" /></label><p class="mobile-muted">最多六位小数，不自动舍入。更换货物清空旧数量和分配。</p><template v-if="!raw"><label>业务日期<input v-model="form.businessDate" type="date" /></label><label>关联单据（选填）<input v-model="form.referenceNo" maxlength="120" /></label></template><template v-if="!raw && !incoming"><button type="button" class="mobile-button" :disabled="!form.itemId" @click="choose(true)">添加真实批次或历史库存分配</button><div v-for="(a, index) in form.allocations" :key="a.batchId"><label>{{ a.scope === 'aggregate' ? '历史无批次归属库存' : '真实批次' }} ID {{ a.batchId }} · 分配数量<input v-model="a.quantity" :aria-label="`批次 ${a.batchId} 分配数量`" inputmode="decimal" maxlength="80" /></label><button type="button" class="mobile-button" @click="form.allocations.splice(index, 1)">移除此分配</button></div></template><p v-if="incoming" class="mobile-warning">普通入库计入 aggregate；不能替代生产完工入库或指定生产批次。</p><label>备注<textarea v-model="form.notes" maxlength="2000" rows="3" /></label><button class="mobile-button primary" :disabled="busy || checking || !allowed || Boolean(warning)">下一步：核对{{ label }}</button></fieldset></form>
  </section>
</template>
<style scoped>
.mobile-stock-fields { border: 0; padding: 0; margin: 0; min-width: 0; display: grid; gap: 16px; }
.mobile-stock-fields label { display: grid; gap: 8px; }
summary { min-height: 44px; display: flex; align-items: center; cursor: pointer; }
</style>
