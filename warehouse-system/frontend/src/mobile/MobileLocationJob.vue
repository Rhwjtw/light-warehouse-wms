<script setup>
import MobileChoices from './MobileChoices.vue'
import MobileTaskHeading from './MobileTaskHeading.vue'
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

  <MobileTaskHeading :title="label" :task="task" :step="step" :allowed="allowed" :warning="warning" :error="error" :checking="checking" />
  <template v-if="step === 'start'"><div class="mobile-page-intro"><h2>开始{{ label }}</h2><p class="mobile-muted">{{ transfer ? '选择来源批次与目标库位，移库总量保持不变。' : '每笔盘点一个已上架库位批次，实盘量允许零。' }}</p></div><button class="mobile-button primary" :disabled="busy || !allowed || Boolean(warning)" @click="start">新建{{ label }}</button><h3 v-if="drafts.length" class="mobile-section-title">未完成作业</h3><router-link v-for="draft in drafts" :key="draft.id" :to="locationPath(draft, lockedTask(draft) ? 'result' : 'form')" class="mobile-menu-item"><span><strong>{{ taskLabels[draft.status] }}</strong><small>批次 {{ draft.fields.batchId || draft.snapshot?.batchId || '尚未选择' }}</small></span></router-link></template>
  <section v-else-if="!task" class="mobile-card"><h2>这笔作业无法恢复</h2><p class="mobile-muted">上下文不存在，不能重建原请求。</p><router-link to="/m/jobs" class="mobile-button primary">返回作业入口</router-link></section>
  <template v-else-if="locked"><section class="mobile-card"><p class="mobile-big-number">{{ formatQuantity(task.snapshot.quantity) }} {{ task.snapshot.unit }}</p><dl class="mobile-details"><div v-if="task.result?.documentNo"><dt>单据号</dt><dd>{{ task.result.documentNo }}</dd></div><div v-if="result"><dt>原料 / 批号</dt><dd>{{ result.source.material_name }} / {{ result.source.batch_no }}</dd></div><div v-if="result"><dt>来源库位</dt><dd>{{ result.source.location_name }}</dd></div><div v-if="result"><dt>来源最新余额</dt><dd>{{ formatQuantity(result.source.remaining_quantity) }} {{ result.source.unit }}</dd></div><div v-if="result?.target"><dt>目标库位</dt><dd>{{ result.target.location_name }}</dd></div><div v-if="result?.target"><dt>目标最新余额</dt><dd>{{ formatQuantity(result.target.remaining_quantity) }} {{ result.target.unit }}</dd></div><div v-if="!transfer"><dt>本次账面</dt><dd>{{ formatQuantity(task.snapshot.bookQuantity) }} {{ task.snapshot.unit }}</dd></div><div v-if="!transfer"><dt>差异</dt><dd>{{ formatQuantity(quantityText(quantityUnits(task.snapshot.quantity) - quantityUnits(task.snapshot.bookQuantity))) }} {{ task.snapshot.unit }}</dd></div></dl><details><summary>本笔请求信息</summary><p class="mobile-small">请求标识 {{ task.requestId }}</p><p>来源库位 {{ task.snapshot.locationId }} · 批次 {{ task.snapshot.batchId }}</p><p v-if="transfer">目标库位 {{ task.snapshot.targetLocationId }}</p><p>{{ task.snapshot.notes || '无备注' }}</p></details></section>
    <template v-if="task.status === 'succeeded'"><p v-if="!result" class="mobile-warning">本笔已成功，单据与相关库存待刷新，不能重复提交。</p><p v-if="result?.count" class="mobile-advisory">{{ result.count.difference === '0.000000' ? '零差盘点保留事实，不产生库存变动流水。' : '差异已记入库存流水。' }}</p><button class="mobile-button primary" :disabled="busy || !allowed || Boolean(warning)" @click="start">继续{{ label }}</button><button class="mobile-button" :disabled="checking" @click="refreshResult">刷新本笔单据与库存</button><router-link :to="{ path: '/m/read/document/' + task.result.id, query: { from: locationOrigin(task.origin) } }" class="mobile-button">查看本笔单据</router-link><details v-if="result" class="mobile-card"><summary>相关库位批次库存</summary><p v-for="b in result.stocks" :key="b.id">{{ b.batch_no }} · {{ b.location_name }} · {{ formatQuantity(b.remaining_quantity) }} {{ b.unit }}</p></details></template>
    <template v-else><p class="mobile-warning">{{ task.reason }}。原请求已保留，不能重交。空列表或相似记录不能确定结果。</p><router-link to="/m/records" class="mobile-button">查询记录（辅助核对）</router-link></template><router-link :to="locationOrigin(task.origin)" class="mobile-button">返回原列表</router-link>
  </template>
  <MobileChoices :maxlength="targetChoice ? 80 : 120" v-else-if="step === 'choose'" :title="targetChoice ? '选择目标库位' : transfer ? '选择来源批次' : '选择盘点批次'" v-model:search="search" :placeholder="targetChoice ? '库位名称' : '原料 / 编码 / 批号'" :items="items" :total="total" :page="page" :pages="pages" :loading="loading" :error="choiceError" :busy="busy" prefix="s6-" @search="choicePage(1)" @page="choicePage" @select="select" @cancel="returnToParent(router, locationPath(task))"><template #default="{ row }"><h3>{{ row.name || row.material_name }}</h3><template v-if="!targetChoice"><p>{{ row.material_code }} · {{ row.batch_no }} · {{ row.location_name }}</p><p>{{ row.supplier_name }} · 到期 {{ row.expiry_date }}</p><p class="mobile-choice-quantity">账面 {{ formatQuantity(row.remaining_quantity) }} {{ row.unit }}</p><p v-if="row.expired">已过期（移库与盘点沿原规则）</p></template><p v-else>库位 {{ row.id }}</p></template></MobileChoices>
  <section v-else class="mobile-card"><p v-if="task.status === 'rejected'" class="mobile-error">本次提交未通过：{{ task.reason }}。修改后重新核对。</p>
    <template v-if="step === 'confirm'"><p class="mobile-muted mobile-small">本次{{ label }}</p><p class="mobile-big-number">{{ formatQuantity(form.quantity) }} {{ form.unit }}</p><dl class="mobile-details"><div><dt>原料 / 批号</dt><dd>{{ selection?.item.name || form.itemId }} / {{ selection?.row.batch_no }}</dd></div><div><dt>供应商</dt><dd>{{ selection?.supplier.name }}</dd></div><div><dt>来源库位</dt><dd>{{ selection?.location.name || form.locationId }}</dd></div><div v-if="transfer"><dt>目标库位</dt><dd>{{ selection?.target?.name || form.targetLocationId }}</dd></div><div v-if="selection"><dt>{{ checking ? '上次读取账面' : '最新账面' }}</dt><dd>{{ formatQuantity(selection.row.remaining_quantity) }} {{ form.unit }}</dd></div><div v-if="!transfer"><dt>核对账面</dt><dd>{{ confirmed ? formatQuantity(confirmed.book) : '需重新核对' }}</dd></div><div v-if="!transfer"><dt>差异</dt><dd>{{ difference }} {{ form.unit }}</dd></div><div><dt>{{ transfer ? '备注' : '盘点原因' }}</dt><dd>{{ form.notes || '—' }}</dd></div></dl><p class="mobile-advisory">提交前重新读取；账面变化需要重新核对。</p><button class="mobile-button primary" :disabled="busy || checking || !allowed || Boolean(warning)" @click="submit">确认提交{{ label }}</button><button class="mobile-button" :disabled="busy || checking" @click="confirm">重新核对</button><router-link :to="locationPath(task)" class="mobile-button">返回修改</router-link></template>
    <form v-else class="mobile-form" @submit.prevent="confirm"><fieldset :disabled="busy" class="mobile-location-fields"><label>{{ transfer ? '来源库存' : '盘点库存' }}<button type="button" class="mobile-pick" @click="choose(false)"><span>{{ selection?.item.name || '选择具体库位批次' }}<small v-if="selection">{{ selection.row.batch_no }} · {{ selection.location.name }}</small></span><span aria-hidden="true">›</span></button></label><p v-if="selection" class="mobile-advisory">{{ checking ? '上次读取账面（正在核实）' : '最新账面' }} {{ formatQuantity(selection.row.remaining_quantity) }} {{ form.unit }}</p><label v-if="transfer">目标库位<button type="button" class="mobile-pick" :disabled="!form.batchId" @click="choose(true)"><span>{{ selection?.target?.name || '选择目标库位' }}</span><span aria-hidden="true">›</span></button></label><label>{{ transfer ? '移库数量' : '实盘数量（允许零）' }} · {{ form.unit || '先选择货物' }}<input v-model="form.quantity" inputmode="decimal" placeholder="请输入数量" maxlength="80" autocomplete="off" /></label><p v-if="!transfer" class="mobile-advisory">差异预览 {{ difference }} {{ form.unit }}（以本次读取账面为准）</p><details><summary>{{ transfer ? '备注（选填）' : '盘点原因（选填）' }}</summary><label>{{ transfer ? '备注' : '盘点原因' }}<textarea v-model="form.notes" maxlength="2000" rows="3" /></label></details><p class="mobile-advisory">最多六位小数，不自动舍入；更换来源会清空旧数量。</p><button class="mobile-button primary" :disabled="busy || checking || !allowed || Boolean(warning)">下一步：核对{{ label }}</button></fieldset></form>
  </section>

</template>
<style scoped>
.mobile-location-fields { border: 0; padding: 0; margin: 0; min-width: 0; display: grid; gap: 16px; }
.mobile-location-fields label { display: grid; gap: 8px; }
summary { min-height: 44px; display: flex; align-items: center; cursor: pointer; }
</style>
