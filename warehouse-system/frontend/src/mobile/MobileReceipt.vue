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
import { receiptFields, receiptKind, receiptOrigin, receiptPayload, receiptSnapshot, receiptTaskPath, readReceiptStock, verifyReceipt } from './receipt'
const route = useRoute(), router = useRouter(), form = reactive(receiptFields())
const error = ref(''), busy = ref(false), checking = ref(false), material = ref(null), supplier = ref(null), detail = ref(null), stock = ref(null), refreshed = ref('')
const gate = readGate(), choiceGate = readGate(), items = ref([]), total = ref(0), loading = ref(false), search = ref(''), choiceError = ref('')
const task = computed(() => { mobileRevision.value; const t = mobileStore()?.get(route.params.jobId); return t?.kind === receiptKind ? t : null })
const drafts = computed(() => { mobileRevision.value; return mobileStore()?.tasks().filter(t => t.kind === receiptKind && t.status !== 'succeeded') || [] })
const allowed = computed(() => canOperate(auth.user?.role, 'material.inventory.write'))
const locked = computed(() => lockedTask(task.value))
const warning = computed(() => { mobileRevision.value; return mobileStore()?.warning || '' })
const step = computed(() => route.params.step || 'start'), choice = computed(() => route.query.kind === 'supplier' ? 'supplier' : 'material')
const page = computed(() => /^[1-9]\d*$/.test(route.query.page) && Number.isSafeInteger(Number(route.query.page)) ? Number(route.query.page) : 1)
const pages = computed(() => Math.max(1, Math.ceil(total.value / 20)))
let restoring = false, disposed = false, editVersion = 0
function loadFields() { restoring = true; Object.assign(form, receiptFields(task.value?.fields)); restoring = false }
watch(form, () => {
  if (!restoring && task.value && !locked.value) { editVersion++; mobileStore().edit(task.value.id, form); touchMobile() }
}, { flush: 'sync' })
function start() {
  if (!allowed.value || warning.value) return
  const opened = mobileStore().create(receiptKind, 'new', receiptOrigin(route.query.from))
  touchMobile(); router.push(receiptTaskPath(opened, lockedTask(opened) ? 'result' : 'form'))
}
async function check() {
  const store = mobileStore(), original = task.value, ticket = gate.begin(), version = editVersion
  if (!original) return false
  checking.value = true; error.value = ''; material.value = null; supplier.value = null
  try {
    const user = (await api.get('/auth/me')).data
    if (user.id !== auth.user?.id || store !== mobileStore()) throw new Error('账户已变化，请重新登录')
    auth.user = user
    const fields = original.status === 'succeeded' ? original.snapshot : original.fields
    if (!fields.materialId || !fields.supplierId) throw new Error('请选择原料和供应商')
    const [m, s] = await Promise.all([api.get(`/materials/${fields.materialId}`), api.get(`/suppliers/${fields.supplierId}`)])
    if (!gate.current(ticket) || disposed || task.value?.id !== original.id || editVersion !== version) return false
    if (String(m.data.id) !== fields.materialId || String(s.data.id) !== fields.supplierId) throw new Error('档案身份不符，请重新选择')
    material.value = m.data; supplier.value = s.data
    if (original.status !== 'succeeded') {
      if (m.data.is_active !== true || s.data.is_active !== true) throw new Error('原料或供应商已停用，请重新选择')
      if (m.data.unit !== fields.unit) throw new Error('原料单位已变化，请重新选择并核对')
    }
    return true
  } catch (e) { if (gate.current(ticket)) error.value = e.message; return false }
  finally { if (gate.current(ticket)) checking.value = false }
}
async function confirm() {
  if (!task.value || locked.value || busy.value) return
  busy.value = true; error.value = ''
  try {
    receiptSnapshot(form)
    if (!await check()) return
    if (!allowed.value) throw new Error('当前角色无原料入库权限')
    await router.push(receiptTaskPath(task.value, 'confirm'))
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
async function submit() {
  if (!task.value || locked.value || busy.value || step.value !== 'confirm') return
  busy.value = true; error.value = ''
  let started, store
  try {
    if (!await check() || disposed) return
    store = mobileStore()
    started = store.begin(task.value.id, { ready: true, allowed: allowed.value })
    touchMobile()
    const response = await api.post('/material-receipts', receiptPayload(started))
    store.settle(started.id, 'succeeded', '', response.data)
  } catch (e) {
    if (started) {
      // 原API明确权限/字段校验拒绝才可修改；网关、超时、401或键冲突保留待核实。
      const state = [403, 422].includes(e.status) ? 'rejected' : 'uncertain'
      store.settle(started.id, state, e.message)
    } else error.value = e.message
  } finally {
    touchMobile(); busy.value = false
    if (started && !disposed && mobileStore() === store && task.value?.id === started.id) {
      await router.replace(receiptTaskPath(started, 'result'))
      if (task.value?.status === 'succeeded') await refreshResult()
    }
  }
}
async function refreshResult() {
  const original = task.value, store = mobileStore(), ticket = gate.begin()
  if (original?.status !== 'succeeded') return
  checking.value = true; error.value = ''; detail.value = null; stock.value = null; refreshed.value = ''
  try {
    const row = verifyReceipt(original, (await api.get(`/material-receipts/${original.result.id}`)).data)
    if (!gate.current(ticket) || disposed || mobileStore() !== store || task.value?.id !== original.id) return
    store.bindReceipt(original.id, row); touchMobile(); detail.value = row
    const m = (await api.get(`/materials/${original.snapshot.materialId}`)).data
    if (String(m.id) !== original.snapshot.materialId) throw new Error('原料档案身份不符')
    const summary = await readReceiptStock(api, m)
    if (!gate.current(ticket) || disposed || mobileStore() !== store) return
    if (summary.unit !== original.snapshot.unit) throw new Error('库存单位不符，暂停显示汇总')
    stock.value = summary; refreshed.value = new Date().toLocaleTimeString('zh-CN')
  } catch (e) { if (gate.current(ticket)) error.value = `入库已成功，记录或库存待刷新：${e.message}` }
  finally { if (gate.current(ticket)) checking.value = false }
}
function choose(kind) { if (!locked.value && !busy.value) router.push({ path: receiptTaskPath(task.value, 'choose'), query: { kind } }) }
function choicePage(value) { router.replace({ path: route.path, query: { kind: choice.value, q: search.value || undefined, page: String(value) } }) }
async function loadChoices(restore = true) {
  if (step.value !== 'choose' || !task.value || locked.value) return
  const ticket = choiceGate.begin(), path = route.fullPath
  loading.value = true; choiceError.value = ''; items.value = []; total.value = 0
  search.value = typeof route.query.q === 'string' ? route.query.q.slice(0, 120) : ''
  try {
    const result = (await api.get(choice.value === 'material' ? '/materials' : '/suppliers', { params: { q: search.value, page: page.value, page_size: 20, is_active: true } })).data
    if (!choiceGate.current(ticket)) return
    items.value = result.items; total.value = result.total
    if (page.value > pages.value) { choicePage(pages.value); return }
    loading.value = false
    if (restore) {
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
  const original = task.value, kind = choice.value, ticket = choiceGate.begin()
  try {
    const current = (await api.get(`/${kind === 'material' ? 'materials' : 'suppliers'}/${row.id}`)).data
    if (disposed || !choiceGate.current(ticket) || task.value?.id !== original.id || locked.value) return
    if (current.id !== row.id || current.is_active !== true) throw new Error('该档案身份不符或已停用，请重新选择')
    if (kind === 'material') { form.materialId = String(current.id); form.unit = current.unit; material.value = current }
    else { form.supplierId = String(current.id); supplier.value = current }
    returnToParent(router, receiptTaskPath(original))
  } catch (e) { choiceError.value = e.message }
  finally { busy.value = false }
}
function restore() {
  gate.cancel(); choiceGate.cancel(); checking.value = false; error.value = ''; detail.value = null; stock.value = null; material.value = null; supplier.value = null
  loadFields()
  if (step.value === 'choose') loadChoices()
  else if (task.value?.status === 'succeeded') refreshResult()
  else if (task.value && form.materialId && form.supplierId) check()
}
function resume() {
  if (step.value === 'choose') {
    if (document.querySelector('[data-mobile-list-ready]')) {
      const anchor = [...document.querySelectorAll('[data-mobile-anchor]')].find(el => el.getBoundingClientRect().bottom > 80)
      mobileStore()?.saveList(route.fullPath, { top: window.scrollY, anchor: anchor?.id })
    }
    loadChoices()
  } else if (task.value?.status === 'succeeded') refreshResult()
  else check()
}
watch(() => route.fullPath, restore)
onMounted(() => { restore(); window.addEventListener('wms-mobile-resume', resume) })
onBeforeUnmount(() => { disposed = true; gate.cancel(); choiceGate.cancel(); window.removeEventListener('wms-mobile-resume', resume) })
</script>

<template>
  <section class="mobile-card"><span class="mobile-pill">原料入库 · 入库后待上架</span><p v-if="task">状态：{{ taskLabels[task.status] }}</p><p v-if="!allowed" class="mobile-warning">当前角色可查询，无原料入库权限。</p><p v-if="warning" class="mobile-error" role="alert">{{ warning }}</p><p v-if="error" class="mobile-error" role="alert">{{ error }}</p><p v-if="checking" role="status">正在重新核实记录与权限…</p></section>
  <section v-if="step === 'start'" class="mobile-card"><h2>原料入库</h2><p>填写原料、供应商、批号、日期和数量，核对后记入待上架。手动上架将在 S4-B 实现。</p><button class="mobile-button primary" :disabled="!allowed || Boolean(warning)" @click="start">开始原料入库 / 恢复草稿</button><router-link v-for="draft in drafts" :key="draft.id" :to="receiptTaskPath(draft, lockedTask(draft) ? 'result' : 'form')" class="mobile-button">恢复{{ taskLabels[draft.status] }} · {{ draft.snapshot?.batchNo || draft.fields.batchNo || '未填批号' }}</router-link></section>
  <section v-else-if="!task" class="mobile-card"><p>作业上下文不存在，可能已清理或属于其他账号。不能重建原请求。</p><router-link to="/m/jobs/receipt" class="mobile-button">返回原料入库入口</router-link></section>
  <section v-else-if="locked" class="mobile-card">
    <h2>{{ task.status === 'succeeded' ? '原料入库成功' : taskLabels[task.status] }}</h2><p>{{ task.reason }}</p>
    <dl class="mobile-details"><div><dt>本次批号</dt><dd>{{ task.snapshot.batchNo }}</dd></div><div><dt>本次入库</dt><dd>{{ formatQuantity(task.snapshot.quantity) }} {{ task.snapshot.unit }}</dd></div><div><dt>原料 / 供应商ID</dt><dd>{{ task.snapshot.materialId }} / {{ task.snapshot.supplierId }}</dd></div><div><dt>生产 / 到期日期</dt><dd>{{ task.snapshot.productionDate }} / {{ task.snapshot.expiryDate }}</dd></div><div><dt>请求时间</dt><dd>{{ task.submittedAt }}</dd></div><div><dt>备注</dt><dd>{{ task.snapshot.notes || '—' }}</dd></div></dl>
    <template v-if="task.status === 'succeeded'">
      <p class="mobile-success">本笔已成功，旧表单不可再次提交。</p><p>单据：{{ task.result.documentNo }}（ID {{ task.result.id }}）</p>
      <p v-if="!detail" class="mobile-warning">本笔记录与入库明细待读取；请刷新核对。</p>
      <dl v-if="detail" class="mobile-details"><div><dt>原料 / 供应商</dt><dd>{{ detail.material_name }} / {{ detail.supplier_name }}</dd></div><div><dt>本笔明细ID</dt><dd>{{ detail.receipt_movement_id }}</dd></div><div><dt>本笔已上架量</dt><dd>{{ formatQuantity(detail.shelved_quantity) }} {{ detail.unit }}</dd></div><div><dt>本笔待上架量（实查）</dt><dd>{{ formatQuantity(detail.pending_quantity) }} {{ detail.unit }}</dd></div><div><dt>当前状态</dt><dd>{{ detail.status === 'reversed' ? '本笔已冲销' : detail.status === 'shelved' ? '已全部上架' : '待上架' }}</dd></div></dl>
      <p v-if="stock">该原料已上架库存汇总（实查）：{{ formatQuantity(stock.remaining_quantity) }} {{ stock.unit }}。本笔待上架量不计入此汇总。</p><p v-else class="mobile-warning">相关库存待刷新，不能用旧量代替。</p><p v-if="refreshed" class="mobile-muted">读取时间：{{ refreshed }}</p>
      <button class="mobile-button" :disabled="checking" @click="refreshResult">重新读取本笔记录与库存</button>
      <router-link :to="{ path: `/m/read/receipt/${task.result.id}`, query: { from: receiptOrigin(task.origin) } }" class="mobile-button">查看本笔入库详情</router-link>
      <p class="mobile-muted">手动上架留待 S4-B；本页没有上架提交。</p>
      <button class="mobile-button primary" :disabled="!allowed || Boolean(warning)" @click="start">继续入库（新作业）</button>
    </template>
    <template v-else><p class="mobile-warning">结果待核实。原请求内容及请求键保留；查询为空或相似记录都不能确定结果，不自动补记，也不能重新提交。</p><router-link to="/m/records?view=receipts" class="mobile-button">查询入库记录（仅辅助核对）</router-link></template>
    <router-link :to="receiptOrigin(task.origin)" class="mobile-button">返回原列表</router-link>
  </section>
  <section v-else-if="step === 'choose'" class="mobile-card">
    <h2>选择{{ choice === 'material' ? '原料' : '供应商' }}</h2><form class="mobile-search" @submit.prevent="choicePage(1)"><input v-model="search" :aria-label="choice === 'material' ? '搜索原料名称或编码' : '搜索供应商名称或编码'" maxlength="120" /><button class="mobile-button primary">搜索</button></form><p v-if="loading" class="mobile-status" role="status">正在查询…</p><p v-if="choiceError" class="mobile-error" role="alert">{{ choiceError }}</p><button v-if="choiceError" class="mobile-button" @click="loadChoices">重试查询</button>
    <div v-if="!loading && !choiceError" data-mobile-list-ready><article v-for="row in items" :id="`s4a-${choice}-${row.id}`" :key="row.id" data-mobile-anchor class="mobile-card"><h3>{{ row.name }}</h3><p>{{ row.code }} <span v-if="row.unit">· {{ row.unit }}</span></p><button class="mobile-button" :disabled="busy" @click="select(row)">选择{{ row.name }}</button></article><p v-if="!items.length">没有匹配的可用档案。</p><p>共 {{ total }} 条 · 第 {{ page }} / {{ pages }} 页</p><div class="mobile-pagination"><button class="mobile-button" :disabled="page <= 1 || busy" @click="choicePage(page - 1)">上一页</button><button class="mobile-button" :disabled="page >= pages || busy" @click="choicePage(page + 1)">下一页</button></div></div>
    <router-link :to="receiptTaskPath(task)" class="mobile-button">取消选择，返回表单</router-link>
  </section>
  <section v-else-if="step === 'confirm'" class="mobile-card"><h2>核对原料入库</h2><dl class="mobile-details"><div><dt>原料</dt><dd>{{ material?.name || form.materialId }}（{{ material?.code }}）</dd></div><div><dt>供应商</dt><dd>{{ supplier?.name || form.supplierId }}</dd></div><div><dt>批号</dt><dd>{{ form.batchNo }}</dd></div><div><dt>入库数量</dt><dd>{{ formatQuantity(form.quantity) }} {{ form.unit }}</dd></div><div><dt>生产 / 到期日期</dt><dd>{{ form.productionDate }} / {{ form.expiryDate }}</dd></div><div><dt>备注</dt><dd>{{ form.notes || '—' }}</dd></div></dl><p>本笔将记入待上架，尚不计入可用库位库存；日期、档案及事务由原后台最终校验。</p><button class="mobile-button primary" :disabled="busy || checking || !allowed || Boolean(warning)" @click="submit">确认提交原料入库</button><router-link :to="receiptTaskPath(task)" class="mobile-button">返回修改</router-link></section>
  <section v-else class="mobile-card"><p v-if="task.status === 'rejected'" class="mobile-error">明确拒绝：{{ task.reason }}。可返回修改，相同内容保留原请求键。</p><form class="mobile-form" @submit.prevent="confirm"><fieldset :disabled="busy" class="mobile-receipt-fields"><div class="mobile-choice-field">原料<button type="button" class="mobile-button" @click="choose('material')">{{ material?.name || (form.materialId ? `原料ID ${form.materialId}` : '手动查询原料') }}</button></div><div class="mobile-choice-field">供应商<button type="button" class="mobile-button" @click="choose('supplier')">{{ supplier?.name || (form.supplierId ? `供应商ID ${form.supplierId}` : '手动查询供应商') }}</button></div><label>批号<input v-model="form.batchNo" maxlength="80" autocomplete="off" /></label><label>入库数量 · {{ form.unit || '先选择原料' }}<input v-model="form.quantity" inputmode="decimal" maxlength="80" autocomplete="off" /></label><p class="mobile-muted mobile-small">最多六位小数；不自动舍入。单位取所选原料档案。</p><label>生产日期<input v-model="form.productionDate" type="date" /></label><label>到期日期<input v-model="form.expiryDate" type="date" /></label><details><summary>备注（选填）</summary><label>备注<textarea v-model="form.notes" maxlength="2000" rows="3"></textarea></label></details><button class="mobile-button primary" :disabled="busy || checking || !allowed || Boolean(warning)">下一步：核对入库</button></fieldset></form><p class="mobile-muted mobile-small">编辑时保存草稿；恢复后重新读取权限与档案，不会自动提交。</p></section>
</template>

<style scoped>
.mobile-receipt-fields { border: 0; padding: 0; margin: 0; min-width: 0; display: grid; gap: 16px; }
.mobile-receipt-fields label, .mobile-choice-field { display: grid; gap: 8px; }
summary { min-height: 44px; display: flex; align-items: center; cursor: pointer; }
.mobile-details div { display: grid; gap: 4px; }
.mobile-details dt { width: auto; font-size: 14px; }
</style>
