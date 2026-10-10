<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { auth } from '../auth'
import { canOperate } from '../permissions'
import { formatQuantity, quantityText, quantityUnits } from '../quantity'
import { mobileRevision, mobileStore, touchMobile } from './session'
import { lockedTask, taskLabels } from './taskState'
import { readGate } from './readQueries'
import { returnToParent } from './navigation'
import { locationLabels, locationFields, locationSnapshot, locationPayload, locationPath, locationOrigin, locationConfirmation, readLocationSelection, readLocationResult } from './locationJobs'

const route = useRoute(), router = useRouter(), form = reactive({})
const kind = computed(() => route.meta.locationKind), label = computed(() => locationLabels[kind.value]), transfer = computed(() => kind.value === 'material-transfer')
const step = computed(() => route.params.step || 'start')
const task = computed(() => { mobileRevision.value; const t = mobileStore()?.get(route.params.jobId); return t?.kind === kind.value ? t : null })
const drafts = computed(() => { mobileRevision.value; return mobileStore()?.tasks().filter(t => t.kind === kind.value && t.status !== 'succeeded') || [] })
const allowed = computed(() => canOperate(auth.user?.role, 'material.inventory.write')), locked = computed(() => lockedTask(task.value))
const warning = computed(() => { mobileRevision.value; return mobileStore()?.warning || '' })
const error = ref(''), busy = ref(false), checking = ref(false), selection = ref(null), result = ref(null), confirmed = ref(null)
const items = ref([]), total = ref(0), search = ref(''), loading = ref(false), choiceError = ref('')
const gate = readGate(), choiceGate = readGate(), choices = new Map()
const targetChoice = computed(() => route.query.kind === 'target')
const page = computed(() => /^[1-9]\d*$/.test(route.query.page) && Number.isSafeInteger(Number(route.query.page)) ? Number(route.query.page) : 1)
const pages = computed(() => Math.max(1, Math.ceil(total.value / 20)))
const difference = computed(() => { try { return quantityText(quantityUnits(form.quantity) - quantityUnits(confirmed.value?.book ?? selection.value?.row.remaining_quantity)) } catch { return '—' } })
let restoring = false, disposed = false, editVersion = 0
watch(form, () => { if (!restoring && task.value && !locked.value) { editVersion++; confirmed.value = null; mobileStore().edit(task.value.id, form); touchMobile() } }, { flush: 'sync' })
async function start() {
  if (!allowed.value || warning.value || busy.value) return
  busy.value = true; error.value = ''
  const store = mobileStore()
  try {
    let opened = store.create(kind.value, 'new', locationOrigin(task.value?.origin || route.query.from))
    // 从具体库存详情进入仍重读稳定ID；不按名称/批号认领另一批。
    if (!lockedTask(opened) && /^[1-9]\d{0,9}$/.test(String(route.query.batch_id || ''))) {
      const row = (await api.get(`/material-location-stock/${route.query.batch_id}`)).data
      const fields = locationFields(kind.value, { itemId: row.material_id, batchId: row.id, locationId: row.location_id, unit: row.unit })
      await readLocationSelection(api, kind.value, fields)
      if (disposed || store !== mobileStore()) return
      opened = store.selectLocation(opened.id, fields)
    }
    touchMobile(); await router.push(locationPath(opened, lockedTask(opened) ? 'result' : 'form'))
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
async function check() {
  const original = task.value, store = mobileStore(), ticket = gate.begin(), version = editVersion
  if (!original || lockedTask(original)) return false
  checking.value = true; error.value = ''
  try {
    const user = (await api.get('/auth/me')).data
    if (user.id !== auth.user?.id || store !== mobileStore()) throw new Error('账户已变化，请重新登录')
    auth.user = user
    const data = await readLocationSelection(api, original.kind, original.fields)
    if (!gate.current(ticket) || disposed || store !== mobileStore() || task.value?.id !== original.id || editVersion !== version) return false
    selection.value = data; return true
  } catch (e) { if (gate.current(ticket)) { error.value = e.message; selection.value = null } return false }
  finally { if (gate.current(ticket)) checking.value = false }
}
async function confirm() {
  if (!task.value || locked.value || busy.value) return
  busy.value = true
  try {
    // 先校验输入，账面量待真实读取后再参与核对。
    locationSnapshot(kind.value, form, '0')
    if (!await check() || disposed) return
    if (!allowed.value) throw new Error('当前角色无本业务写入权限')
    confirmed.value = { signature: locationConfirmation(task.value, selection.value), book: selection.value.row.remaining_quantity }
    if (step.value !== 'confirm') await router.push(locationPath(task.value, 'confirm'))
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
async function submit() {
  if (!task.value || locked.value || busy.value || step.value !== 'confirm') return
  busy.value = true; error.value = ''
  let started, store
  try {
    if (!await check() || disposed) return
    if (!confirmed.value || locationConfirmation(task.value, selection.value) !== confirmed.value.signature) throw new Error('余额、账面或目标批次已变化，请点击重新核对；原输入保留，不自动替换提交')
    store = mobileStore(); started = store.begin(task.value.id, { ready: true, allowed: allowed.value, bookQuantity: confirmed.value.book }); touchMobile()
    const response = await api.post(transfer.value ? '/material-transfers' : '/material-counts', locationPayload(started))
    store.settle(started.id, 'succeeded', '', response.data)
  } catch (e) {
    if (started) store.settle(started.id, [403, 422].includes(e.status) ? 'rejected' : 'uncertain', e.message)
    else error.value = e.message
  } finally {
    touchMobile(); busy.value = false
    if (started && !disposed && mobileStore() === store && task.value?.id === started.id) {
      await router.replace(locationPath(started, 'result'))
      if (task.value?.status === 'succeeded') await refreshResult()
    }
  }
}
async function refreshResult() {
  const original = task.value, store = mobileStore(), ticket = gate.begin()
  if (original?.status !== 'succeeded') return
  checking.value = true; error.value = ''; result.value = null
  try { const data = await readLocationResult(api, original); if (gate.current(ticket) && !disposed && mobileStore() === store && task.value?.id === original.id) result.value = data }
  catch (e) { if (gate.current(ticket)) error.value = `本笔已成功，待刷新：${e.message}` }
  finally { if (gate.current(ticket)) checking.value = false }
}
function choose(target = false) {
  if (!task.value || locked.value || busy.value) return
  const saved = choices.get(`${task.value.id}:${target}`) || {}
  router.push({ path: locationPath(task.value, 'choose'), query: { ...saved, kind: target ? 'target' : 'source' } })
}
function choicePage(value) { router.replace({ path: route.path, query: { kind: targetChoice.value ? 'target' : 'source', q: search.value || undefined, page: String(value) } }) }
async function loadChoices() {
  if (step.value !== 'choose' || !task.value || locked.value) return
  const ticket = choiceGate.begin(), path = route.fullPath, original = task.value, store = mobileStore()
  loading.value = true; choiceError.value = ''; items.value = []; total.value = 0
  search.value = typeof route.query.q === 'string' ? route.query.q.slice(0, 120) : ''
  choices.set(`${original.id}:${targetChoice.value}`, { ...route.query })
  try {
    const data = (await api.get(targetChoice.value ? '/locations' : '/material-batches', { params: { q: search.value, page: page.value, page_size: 20, ...(targetChoice.value ? { is_active: true } : {}) } })).data
    if (!choiceGate.current(ticket) || store !== mobileStore()) return
    items.value = data.items; total.value = data.total
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
  const original = task.value, store = mobileStore(), ticket = choiceGate.begin(), target = targetChoice.value
  try {
    let fields
    if (target) fields = locationFields(kind.value, { ...original.fields, targetLocationId: row.id })
    else {
      const current = (await api.get(`/material-location-stock/${row.id}`)).data
      if (current.id !== row.id) throw new Error('来源批次身份不符')
      fields = locationFields(kind.value, { itemId: current.material_id, batchId: current.id, locationId: current.location_id, unit: current.unit })
    }
    await readLocationSelection(api, kind.value, fields)
    if (disposed || !choiceGate.current(ticket) || store !== mobileStore() || task.value?.id !== original.id || locked.value) return
    const changed = target ? store.edit(original.id, fields) : store.selectLocation(original.id, fields)
    touchMobile(); confirmed.value = null
    returnToParent(router, locationPath(changed, lockedTask(changed) ? 'result' : 'form'))
  } catch (e) { choiceError.value = e.message }
  finally { busy.value = false }
}
function restore() {
  gate.cancel(); choiceGate.cancel(); checking.value = false; loading.value = false; error.value = ''; selection.value = null; result.value = null
  restoring = true; for (const key of Object.keys(form)) delete form[key]; Object.assign(form, locationFields(kind.value, task.value?.fields)); restoring = false
  if (step.value === 'choose') loadChoices()
  else if (task.value?.status === 'succeeded') refreshResult()
  else if (task.value && !locked.value && form.batchId) check()
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
  <section v-if="step === 'start'" class="mobile-card"><h2>{{ label }}</h2><p>{{ transfer ? '明确选择来源库位批次和不同目标库位；按原规则合并或新建目标批次，总量不变。' : '每笔盘点针对一个具体已上架库位批次，含零余额；读取最新账面，不代替整库位盘点单。' }}</p><button class="mobile-button primary" :disabled="busy || !allowed || Boolean(warning)" @click="start">开始{{ label }} / 恢复草稿</button><router-link v-for="draft in drafts" :key="draft.id" :to="locationPath(draft, lockedTask(draft) ? 'result' : 'form')" class="mobile-button">恢复{{ taskLabels[draft.status] }} · 批次ID {{ draft.fields.batchId || draft.snapshot?.batchId || '未选' }}</router-link></section>
  <section v-else-if="!task" class="mobile-card"><p>作业上下文不存在，不能重建原请求。</p><router-link to="/m/jobs" class="mobile-button">返回作业</router-link></section>
  <section v-else-if="locked" class="mobile-card"><h2>{{ task.status === 'succeeded' ? `${label}成功` : taskLabels[task.status] }}</h2><p>{{ task.reason }}</p><p>具体库位ID {{ task.snapshot.locationId }} · 批次ID {{ task.snapshot.batchId }} · {{ transfer ? '本次移库' : '本次实盘' }} {{ formatQuantity(task.snapshot.quantity) }} {{ task.snapshot.unit }}</p><p v-if="transfer">目标库位ID {{ task.snapshot.targetLocationId }}</p><p v-else>本笔账面 {{ formatQuantity(task.snapshot.bookQuantity) }} · 差异 {{ formatQuantity(quantityText(quantityUnits(task.snapshot.quantity) - quantityUnits(task.snapshot.bookQuantity))) }} {{ task.snapshot.unit }}</p>
    <template v-if="task.status === 'succeeded'"><p class="mobile-success">本笔已成功，旧表单不可再次提交。</p><p>单据：{{ task.result.documentNo }}（ID {{ task.result.id }}）</p><p v-if="!result" class="mobile-warning">成功、待刷新：本笔单据、明细与相关库存待读取。</p><template v-else><p>本笔明细已核验，流水 {{ result.document.transactions.length }} 条。</p><p>来源库位批次余额（实查）：{{ formatQuantity(result.source.remaining_quantity) }} {{ result.source.unit }}</p><p v-if="result.target">目标批次ID {{ result.target.id }} · 库位 {{ result.target.location_name }} · 余额（实查）{{ formatQuantity(result.target.remaining_quantity) }} {{ result.target.unit }}</p><p v-if="result.count">盘点明细ID {{ result.count.id }} · {{ result.count.difference === '0.000000' ? '零差盘点保留事实，无库存变动流水' : '差异已沿原规则记账' }}</p><details><summary>相关库位批次库存（实查）</summary><p v-for="b in result.stocks" :key="b.id">{{ b.batch_no }} · ID {{ b.id }} · {{ b.location_name }} · {{ formatQuantity(b.remaining_quantity) }} {{ b.unit }}</p></details></template><button class="mobile-button" :disabled="checking" @click="refreshResult">重新读取本笔单据与库存</button><router-link :to="{ path: `/m/read/document/${task.result.id}`, query: { from: locationOrigin(task.origin) } }" class="mobile-button">查看本笔单据</router-link><button class="mobile-button primary" :disabled="busy || !allowed || Boolean(warning)" @click="start">下一笔（清空旧数量的新作业）</button></template>
    <template v-else><p class="mobile-warning">结果待核实。保留原请求键与内容；空列表或相似记录不能确定结果，不自动补记，不可重交。</p><router-link to="/m/records" class="mobile-button">查询记录（仅辅助核对）</router-link></template><router-link :to="locationOrigin(task.origin)" class="mobile-button">返回原列表</router-link>
  </section>
  <section v-else-if="step === 'choose'" class="mobile-card"><h2>{{ targetChoice ? '选择目标库位' : '选择具体库位批次' }}</h2><form class="mobile-search" @submit.prevent="choicePage(1)"><input v-model="search" :aria-label="targetChoice ? '搜索目标库位名称' : '搜索原料编码名称或批号'" maxlength="120" /><button class="mobile-button primary">搜索</button></form><p v-if="loading" class="mobile-status" role="status">正在查询…</p><p v-if="choiceError" class="mobile-error" role="alert">{{ choiceError }}</p><button v-if="choiceError" class="mobile-button" @click="loadChoices">重试查询</button><div v-if="!loading && !choiceError" data-mobile-list-ready><article v-for="row in items" :id="`s6-${row.id}`" :key="row.id" data-mobile-anchor class="mobile-card"><h3>{{ row.name || row.material_name }}</h3><p>ID {{ row.id }} · {{ row.material_code }}</p><template v-if="!targetChoice"><p>批号 {{ row.batch_no }} · 库位 {{ row.location_name }}（ID {{ row.location_id }}） · {{ row.supplier_name }}</p><p>账面 {{ formatQuantity(row.remaining_quantity) }} {{ row.unit }} · 到期 {{ row.expiry_date }} {{ row.expired ? '已过期（移库/盘点沿原规则）' : '' }}</p></template><button class="mobile-button" :disabled="busy" @click="select(row)">选择 ID {{ row.id }}</button></article><p v-if="!items.length">没有匹配记录。</p><p>共 {{ total }} 条 · 第 {{ page }} / {{ pages }} 页</p><div class="mobile-pagination"><button class="mobile-button" :disabled="page <= 1 || busy" @click="choicePage(page - 1)">上一页</button><button class="mobile-button" :disabled="page >= pages || busy" @click="choicePage(page + 1)">下一页</button></div></div><router-link :to="locationPath(task)" class="mobile-button">取消选择，返回表单</router-link></section>
  <section v-else class="mobile-card"><h2>{{ step === 'confirm' ? `核对${label}` : label }}</h2><p v-if="task.status === 'rejected'" class="mobile-error">明确拒绝：{{ task.reason }}。修改后重新核对。</p><template v-if="selection"><p>{{ selection.item.name }}（{{ selection.item.code }}） · 批号 {{ selection.row.batch_no }} · 供应商 {{ selection.supplier.name }}</p><p>来源 {{ selection.location.name }}（库位ID {{ form.locationId }}，批次ID {{ form.batchId }}）</p><p>{{ checking ? '上次读取账面（正在核实）' : '最新账面' }} {{ formatQuantity(selection.row.remaining_quantity) }} {{ form.unit }} · 读取时间 {{ selection.readAt }}</p><p v-if="selection.target">目标 {{ selection.target.name }}（库位ID {{ form.targetLocationId }}） · {{ selection.targetBatch ? `合并同归属批次 ID ${selection.targetBatch.id}` : '按原规则新建目标批次' }}</p></template>
    <template v-if="step === 'confirm'"><p>本次{{ transfer ? '移库' : '实盘' }}：{{ formatQuantity(form.quantity) }} {{ form.unit }}</p><p v-if="!transfer">本次核对账面 {{ confirmed ? formatQuantity(confirmed.book) : '待重新核对' }} · 差异 {{ difference }} {{ form.unit }}</p><p>{{ transfer ? '备注' : '盘点原因' }}：{{ form.notes || '—' }}</p><p>提交前重新读取；余额/账面变化必须显式重新核对，原输入不会自动替换。</p><button class="mobile-button" :disabled="busy || checking" @click="confirm">重新核对</button><button class="mobile-button primary" :disabled="busy || checking || !allowed || Boolean(warning)" @click="submit">确认提交{{ label }}</button><router-link :to="locationPath(task)" class="mobile-button">返回修改</router-link></template>
    <form v-else class="mobile-form" @submit.prevent="confirm"><fieldset :disabled="busy" class="mobile-location-fields"><button type="button" class="mobile-button" @click="choose(false)">手动查询具体库位批次</button><p>货物ID {{ form.itemId || '未选' }} · 批次ID {{ form.batchId || '未选' }} · {{ form.unit || '先选择货物' }}</p><button v-if="transfer" type="button" class="mobile-button" :disabled="!form.batchId" @click="choose(true)">手动搜索目标库位</button><label>{{ transfer ? '本次移库量' : '本次实盘量（允许零）' }}<input v-model="form.quantity" inputmode="decimal" maxlength="80" autocomplete="off" /></label><p class="mobile-muted">最多六位小数，不自动舍入；更换来源清空旧数量与目标。</p><p v-if="!transfer">差异预览：{{ difference }} {{ form.unit }}（以本次读取账面为准）</p><label>{{ transfer ? '备注（选填）' : '盘点原因（选填）' }}<textarea v-model="form.notes" maxlength="2000" rows="3" /></label><button class="mobile-button primary" :disabled="busy || checking || !allowed || Boolean(warning)">下一步：核对{{ label }}</button></fieldset></form>
  </section>
</template>
<style scoped>
.mobile-location-fields { border: 0; padding: 0; margin: 0; min-width: 0; display: grid; gap: 16px; }
.mobile-location-fields label { display: grid; gap: 8px; }
summary { min-height: 44px; display: flex; align-items: center; cursor: pointer; }
</style>
