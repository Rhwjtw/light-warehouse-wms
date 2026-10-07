<script setup>
import { postingRequest } from '../postingRequest'
import { quantityUnits, quantityText, formatQuantity } from '../quantity'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Search, View } from '@element-plus/icons-vue'
import { api } from '../api'
import { auth } from '../auth'
import { canOperate } from '../permissions'
import SettlementTable from '../components/SettlementTable.vue'
import ProductionFlow from '../components/ProductionFlow.vue'
import DataPanel from '../components/ui/DataPanel.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import FilterBar from '../components/ui/FilterBar.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
import TableSkeleton from '../components/ui/TableSkeleton.vue'
import DocumentReversal from '../components/DocumentReversal.vue'

const registerVisible = ref(false), registering = ref(false), registeredQuantity = ref(''), registerError = ref('')
function openRegister() { registeredQuantity.value = formatQuantity(current.value.actual_quantity || ''); registerError.value = ''; registerVisible.value = true }
async function registerActual() {
  registering.value = true; registerError.value = ''
  try {
    current.value = (await api.put(`/production-orders/${current.value.id}/actual-quantity`, { actual_quantity: registeredQuantity.value })).data
    registerVisible.value = false; ElMessage.success('实际产量已登记，待确认完成生产'); await load()
  } catch (e) { registerError.value = e.message } finally { registering.value = false }
}
const completeVisible = ref(false), completing = ref(false), settling = ref(false)
const actualQuantity = ref(''), settlement = ref(null), settlementError = ref('')
const finishedBatches = ref([]), materialDocumentOpen = ref(false), materialDocumentId = ref(null)
function openComplete() {
  actualQuantity.value = formatQuantity(current.value?.actual_quantity || ''); settlement.value = null; settlementError.value = ''; completeVisible.value = true
  finishedBatches.value = [{ batch_no: current.value.order_no, quantity: actualQuantity.value, expiry_date: '' }]
}
watch(actualQuantity, (quantity, previous) => {
  settlement.value = null
  if (finishedBatches.value.length === 1 && finishedBatches.value[0].quantity === previous) finishedBatches.value[0].quantity = quantity
})
async function previewSettlement() {
  settlement.value = null; settlementError.value = ''; settling.value = true
  const quantity = actualQuantity.value
  // 只接受仍对应当前输入数量的响应，避免用户改数后显示上一笔试算结果。
  try {
    const result = (await api.post(`/production-orders/${current.value.id}/settlement-preview`, { actual_quantity: quantity })).data
    if (actualQuantity.value === quantity) settlement.value = result
  } catch (e) { settlementError.value = e.message }
  finally { settling.value = false }
}
async function finishProduction() {
  if (!settlement.value || completing.value) return
  completing.value = true; settlementError.value = ''
  try {
    // 正式完成会由后端重新汇总领补退、重算损耗并一次性生产入库，不能只信页面预览。
    current.value = (await api.post(`/production-orders/${current.value.id}/complete`, {
      actual_quantity: actualQuantity.value,
      finished_batches: finishedBatches.value.map(b => ({ ...b, expiry_date: b.expiry_date || null })),
    })).data
    completeVisible.value = false; ElMessage.success('生产已完成，成品已入库'); await load()
  } catch (e) { settlementError.value = e.message; settlement.value = null }
  finally { completing.value = false }
}

const route = useRoute()
const canCreate = computed(() => canOperate(auth.user?.role, 'production.order.write'))
const canManage = computed(() => canOperate(auth.user?.role, 'production.manage'))
const canPick = computed(() => canOperate(auth.user?.role, 'workshop.pick'))
const canReplenish = computed(() => canOperate(auth.user?.role, 'workshop.replenish'))
const canReturn = computed(() => canOperate(auth.user?.role, 'workshop.return'))
const canPostMaterial = computed(() => canPick.value || canReplenish.value || canReturn.value)
const rows = ref([]), total = ref(0), loading = ref(false), error = ref('')
const query = ref(''), status = ref(''), page = ref(1), pageSize = ref(20)
const products = ref([])
const detailVisible = ref(false), current = ref(null), detailLoading = ref(false)
const createVisible = ref(false), creating = ref(false), previewLoading = ref(false), preview = ref(null)
const createForm = reactive({ finished_product_id: null, planned_quantity: '', planned_date: '', notes: '' })
const editVisible = ref(false), editSaving = ref(false)
const editForm = reactive({ planned_date: '', notes: '' })
const actionVisible = ref(false), actionSaving = ref(false), candidateLoading = ref(false)
const action = reactive({ kind: 'issue', material: null, desired: '', unit: '', notes: '', allocations: [] })
let candidateGeneration = 0
const actionTotal = computed(() => quantityText(action.allocations.reduce(
  (sum, row) => sum + (validQuantity(String(row.quantity)) ? quantityUnits(row.quantity) : 0n), 0n)))
function remainingRequirement(material) {
  const remaining = quantityUnits(material.planned_required_quantity) - quantityUnits(material.net_issued_quantity)
  return quantityText(remaining > 0n ? remaining : 0n)
}
const positiveDecimal = /^\d{1,14}(\.\d{1,6})?$/
const actionNames = { issue: '领料', supplement: '补料', return: '退料' }
const statusNames = { draft: '未开始', ready: '待领料', in_progress: '生产中', completed: '已完成', received: '已入库', cancelled: '已取消' }
const statusTypes = { ready: 'warning', in_progress: 'primary', completed: 'success', received: 'success', cancelled: 'info' }
const kindNames = { issue: '生产领料', supplement: '生产补料', return: '生产退料' }

function dateTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { timeZone: auth.user?.business_timezone || 'Asia/Shanghai', hour12: false }) : '—'
}
function amount(value, unit) { return `${formatQuantity(value ?? '0')} ${unit || ''}` }
function validQuantity(value) { return positiveDecimal.test(String(value)) && /[1-9]/.test(String(value)) }
function requestId() { return crypto.randomUUID().replaceAll('-', '') }

async function load() {
  loading.value = true; error.value = ''
  try {
    const result = await api.get('/production-orders', { params: { q: query.value, status: status.value || undefined, page: page.value, page_size: pageSize.value } })
    rows.value = result.data.items; total.value = result.data.total
  } catch (e) { error.value = e.message; rows.value = [] }
  finally { loading.value = false }
}
async function loadProducts(q = '') {
  try { products.value = (await api.get('/products', { params: { q, is_active: true, page_size: 100 } })).data.items }
  catch (e) { error.value = e.message }
}
async function openDetail(row) {
  detailVisible.value = true; detailLoading.value = true; error.value = ''
  try { current.value = (await api.get(`/production-orders/${row.id}`)).data }
  catch (e) { error.value = e.message; detailVisible.value = false }
  finally { detailLoading.value = false }
}
async function refreshDetail() {
  if (current.value) await openDetail({ id: current.value.id })
}
function openCreate() {
  Object.assign(createForm, { finished_product_id: null, planned_quantity: '', planned_date: '', notes: '' })
  preview.value = null; createVisible.value = true; loadProducts()
}
async function calculatePreview() {
  preview.value = null
  if (!createForm.finished_product_id || !validQuantity(createForm.planned_quantity)) {
    error.value = '请选择成品并填写大于 0、最多六位小数的计划数量'; return
  }
  previewLoading.value = true; error.value = ''
  try {
    // 新建前的算料仅供核对；生产单创建时后端会再次读取并冻结当时的 ACTIVE BOM。
    preview.value = (await api.post('/boms/calculate', {
      finished_product_id: createForm.finished_product_id,
      planned_quantity: createForm.planned_quantity,
    })).data
  } catch (e) { error.value = e.message }
  finally { previewLoading.value = false }
}
async function createOrder() {
  if (!preview.value) { await calculatePreview(); if (!preview.value) return }
  creating.value = true; error.value = ''
  try {
    const result = await api.post('/production-orders', {
      finished_product_id: createForm.finished_product_id,
      planned_quantity: createForm.planned_quantity,
      planned_date: createForm.planned_date || null,
      notes: createForm.notes || null,
    })
    createVisible.value = false; ElMessage.success(`生产单 ${result.data.order_no} 已创建`)
    await load(); await openDetail(result.data)
  } catch (e) { error.value = e.message }
  finally { creating.value = false }
}
function openEdit() {
  Object.assign(editForm, { planned_date: current.value.planned_date || '', notes: current.value.notes || '' })
  editVisible.value = true
}
async function saveEdit() {
  editSaving.value = true
  try {
    current.value = (await api.put(`/production-orders/${current.value.id}`, {
      planned_date: editForm.planned_date || null, notes: editForm.notes || null,
    })).data
    editVisible.value = false; ElMessage.success('生产单信息已更新'); await load()
  } catch (e) { error.value = e.message }
  finally { editSaving.value = false }
}
async function cancelOrder() {
  try {
    await ElMessageBox.confirm('仅未发生领料、补料或退料的生产单可以取消。', '确认取消生产单', { type: 'warning' })
    current.value = (await api.post(`/production-orders/${current.value.id}/cancel`)).data
    ElMessage.success('生产单已取消'); await load()
  } catch (e) { if (e !== 'cancel' && e !== 'close') error.value = e.message }
}
function returnCandidates(material) {
  // 前端按逐笔记录计算每批最多可退量；服务端仍会基于数据库事实再次校验上限。
  const map = new Map()
  for (const record of current.value.material_records.filter(row => row.order_material_id === material.id && !row.is_reversed)) {
    const existing = map.get(record.batch_id) || { batch_id: record.batch_id, batch_no: record.batch_no, location_name: record.location_name, remaining_quantity: '—', expiry_date: '原领料批次', max_return: '0' }
    existing.max_return = quantityText(quantityUnits(existing.max_return) + (record.kind === 'return' ? -1n : 1n) * quantityUnits(record.quantity))
    map.set(record.batch_id, existing)
  }
  return [...map.values()].filter(row => quantityUnits(row.max_return) > 0n).map(row => ({ ...row, quantity: '' }))
}
async function openAction(kind, material) {
  candidateGeneration++
  candidateLoading.value = false
  Object.assign(action, { kind, material, desired: '', unit: material.base_unit, notes: '', allocations: [], requestKey: '', requestContent: '' })
  actionVisible.value = true; error.value = ''
  if (kind === 'return') {
    action.allocations = returnCandidates(material); return
  }
  await loadCandidates()
}
async function loadCandidates() {
  const ticket = ++candidateGeneration
  const desired = action.desired
  candidateLoading.value = true
  try {
    const params = validQuantity(action.desired) ? { quantity: action.desired } : {}
    const result = (await api.get(`/production-orders/${current.value.id}/materials/${action.material.material_id}/fefo-suggestion`, { params })).data
    if (ticket !== candidateGeneration || desired !== action.desired) return
    // Each visit starts with blank quantities; only an explicit current quantity is allocated.
    const suggestions = new Map(result.suggestions.map(row => [row.batch_id, row.quantity]))
    action.allocations = result.batches.map(row => ({ ...row, suggested_quantity: suggestions.get(row.id) || '0', quantity: desired ? suggestions.get(row.id) || '' : '' }))
  } catch (e) { if (ticket === candidateGeneration) { error.value = e.message; action.allocations = [] } }
  finally { if (ticket === candidateGeneration) candidateLoading.value = false }
}
async function applySuggestion() {
  if (!validQuantity(action.desired)) { error.value = '请填写大于 0、最多六位小数的本次数量'; return }
  error.value = ''
  await loadCandidates()
}
async function submitAction() {
  if (actionSaving.value || candidateLoading.value) return
  if (action.allocations.some(row => String(row.quantity).trim() !== '' && !validQuantity(row.quantity))) {
    error.value = '批次数量必须大于 0 且最多六位小数'; return
  }
  const allocations = action.allocations
    .filter(row => validQuantity(row.quantity))
    .map(row => ({ batch_id: row.batch_id ?? row.id, quantity: String(row.quantity) }))
  if (!allocations.length) { error.value = '请至少填写一个批次的正数量'; return }
  if (action.kind !== 'return' && action.desired && (!validQuantity(action.desired) || actionTotal.value !== quantityText(quantityUnits(action.desired)))) {
    error.value = '批次数量合计须等于本次总量，请按本次数量分配，或清空总量后直接填写批次实际数量'; return
  }
  if (action.kind === 'return' && allocations.some(row => {
    const candidate = action.allocations.find(item => (item.batch_id ?? item.id) === row.batch_id)
    return quantityUnits(row.quantity) > quantityUnits(candidate.max_return)
  })) { error.value = '退料数量不能超过该生产单在对应批次的累计净领出量'; return }
  actionSaving.value = true; error.value = ''
  try {
    const payload = { order_material_id: action.material.id, unit: action.unit, allocations, notes: action.notes || null }
    // 同一弹窗内失败重试沿用请求标识；修改批次/数量/备注后 postingRequest 会生成新标识。
    await api.post(`/production-orders/${current.value.id}/${action.kind}`, postingRequest(action, payload, requestId))
    actionVisible.value = false; ElMessage.success(`${actionNames[action.kind]}成功`)
    await refreshDetail(); await load()
  } catch (e) { error.value = e.message }
  finally { actionSaving.value = false }
}

watch(() => route.meta.resource, () => { page.value = 1; load() })
watch(() => route.query.order_id, id => { if (id && /^\d+$/.test(String(id))) openDetail({ id: Number(id) }) }, { immediate: true })
onMounted(() => { load(); loadProducts() })
</script>

<template>
  <PageHeader eyebrow="生产管理" :title="route.meta.title" description="生产单保存 BOM 快照；领料、补料和退料按实际批次即时记账。">
    <template #actions><el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button><el-button v-if="canCreate" :icon="Plus" type="primary" @click="openCreate">新建生产单</el-button></template>
  </PageHeader>
  <div v-if="error" class="feedback-stack"><el-alert :title="error" type="error" :closable="true" show-icon @close="error=''" /></div>
  <DataPanel title="生产任务" description="按单号、成品和状态查询；打开详情可直接办理用料及完工。">
    <FilterBar><el-form inline @submit.prevent="page=1; load()">
      <el-form-item label="搜索"><el-input v-model="query" clearable maxlength="120" placeholder="生产单号或成品名称" @clear="page=1; load()" /></el-form-item>
      <el-form-item label="状态"><el-select v-model="status" clearable style="width:150px"><el-option v-for="(label,key) in statusNames" :key="key" :value="key" :label="label" /></el-select></el-form-item>
      <el-form-item><el-button :icon="Search" native-type="submit" type="primary">查询</el-button></el-form-item>
    </el-form></FilterBar>
    <TableSkeleton v-if="loading && !rows.length" />
    <el-table v-else :data="rows" v-loading="loading" stripe row-key="id">
      <el-table-column prop="order_no" label="生产单号" min-width="190" />
      <el-table-column prop="finished_product_name" label="成品" min-width="170" />
      <el-table-column label="计划生产" min-width="135"><template #default="{row}"><span class="data-value">{{ amount(row.planned_quantity, row.finished_product_unit) }}</span></template></el-table-column>
      <el-table-column label="BOM版本" width="100"><template #default="{row}">V{{ row.bom_version }}</template></el-table-column>
      <el-table-column label="状态" width="115"><template #default="{row}"><StatusBadge :tone="statusTypes[row.status] || 'info'" :label="statusNames[row.status] || row.status" /></template></el-table-column>
      <el-table-column label="创建时间" min-width="185"><template #default="{row}">{{ dateTime(row.created_at) }}</template></el-table-column>
      <el-table-column label="操作" width="100" fixed="right"><template #default="{row}"><el-button :icon="View" link type="primary" @click="openDetail(row)">查看</el-button></template></el-table-column>
      <template #empty><EmptyState title="暂无生产单" description="可调整筛选条件；管理员可直接新建生产单。" /></template>
    </el-table>
    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[10,20,50,100]" layout="total, sizes, prev, pager, next" class="pagination" @current-change="load" @size-change="page=1; load()" />
  </DataPanel>

  <el-dialog v-model="createVisible" title="新建生产单" width="820px" destroy-on-close :close-on-click-modal="false">
    <p class="dialog-intro">先填写成品和计划数量，再核对当前启用 BOM 的理论用料。</p>
    <el-alert v-if="error" :title="error" type="error" show-icon class="dialog-feedback" />
    <el-form label-width="110px" :disabled="creating">
      <el-form-item label="成品" required><el-select v-model="createForm.finished_product_id" filterable remote :remote-method="loadProducts" placeholder="输入成品编码或名称"><el-option v-for="item in products" :key="item.id" :value="item.id" :label="`${item.code} · ${item.name} (${item.unit})`" /></el-select></el-form-item>
      <el-form-item label="计划数量" required><el-input v-model="createForm.planned_quantity" inputmode="decimal" placeholder="如 1000" /></el-form-item>
      <el-form-item label="计划生产日期"><el-date-picker v-model="createForm.planned_date" type="date" value-format="YYYY-MM-DD" /></el-form-item>
      <el-form-item label="备注"><el-input v-model="createForm.notes" type="textarea" maxlength="2000" show-word-limit /></el-form-item>
      <el-form-item><el-button :loading="previewLoading" @click="calculatePreview">预览理论用料</el-button></el-form-item>
    </el-form>
    <TableSkeleton v-if="previewLoading" :rows="3" />
    <DataPanel v-if="preview" title="理论用料预览" description="创建生产单时系统会重新读取并保存 BOM 快照。" class="preview-panel">
      <div class="preview-title">当前有效 BOM V{{ preview.bom_version }} · {{ amount(preview.planned_quantity, preview.finished_product_unit) }}</div>
      <el-table :data="preview.requirements" size="small">
        <el-table-column prop="material_name" label="原材料" />
        <el-table-column label="单单位用量"><template #default="{row}">{{ formatQuantity(row.quantity_per_unit) }}{{ row.bom_unit }} / {{ preview.finished_product_unit }}</template></el-table-column>
        <el-table-column label="计划理论需求"><template #default="{row}"><span class="data-value">{{ amount(row.suggested_quantity, row.suggested_unit) }}</span> <small>（{{ formatQuantity(row.total_quantity) }}{{ row.total_unit }}）</small></template></el-table-column>
      </el-table>
    </DataPanel>
    <template #footer><el-button @click="createVisible=false">取消</el-button><el-button type="primary" :loading="creating" @click="createOrder">创建生产单</el-button></template>
  </el-dialog>

  <el-dialog v-model="detailVisible" title="生产单详情" width="min(1280px, 95vw)" destroy-on-close :close-on-click-modal="false">
    <div v-loading="detailLoading">
      <template v-if="current">
        <div class="detail-toolbar"><div class="detail-identity"><strong>{{ current.order_no }}</strong><StatusBadge :tone="statusTypes[current.status] || 'info'" :label="statusNames[current.status] || current.status" /><span>{{ current.finished_product_name }} · BOM V{{ current.bom_version }}</span></div><div><el-button v-if="canCreate && !['cancelled','completed','received'].includes(current.status)" @click="openEdit">编辑信息</el-button><el-button v-if="canCreate && !['cancelled','completed','received'].includes(current.status)" type="danger" plain @click="cancelOrder">取消生产单</el-button></div></div>
        <ProductionFlow :order="current" />
        <div v-if="['ready','in_progress'].includes(current.status)" class="production-primary-actions">
          <span>下一步操作</span>
          <el-button v-if="canManage" @click="openRegister">登记实际产量</el-button>
          <el-button v-if="canManage" type="primary" @click="openComplete">预览并完成生产</el-button>
        </div>
        <h3>原材料需求与当前净领用</h3>
        <el-table :data="current.materials" border stripe row-key="id">
          <el-table-column prop="material_name" label="原材料" min-width="150" />
          <el-table-column label="单位用量" min-width="135"><template #default="{row}">{{ formatQuantity(row.quantity_per_unit) }}{{ row.bom_unit }} / {{ current.finished_product_unit }}</template></el-table-column>
          <el-table-column label="计划理论需求" min-width="145"><template #default="{row}"><span class="data-value">{{ amount(row.planned_required_quantity, row.base_unit) }}</span></template></el-table-column>
          <el-table-column label="已领" min-width="110"><template #default="{row}"><span class="data-value">{{ amount(row.issued_quantity, row.base_unit) }}</span></template></el-table-column>
          <el-table-column label="补料" min-width="110"><template #default="{row}"><span class="data-value">{{ amount(row.supplemented_quantity, row.base_unit) }}</span></template></el-table-column>
          <el-table-column label="退料" min-width="110"><template #default="{row}"><span class="data-value">{{ amount(row.returned_quantity, row.base_unit) }}</span></template></el-table-column>
          <el-table-column label="当前净领用" min-width="145"><template #default="{row}"><strong class="data-value">{{ amount(row.net_issued_quantity, row.base_unit) }}</strong></template></el-table-column>
          <el-table-column v-if="canPostMaterial && ['ready','in_progress'].includes(current.status)" label="操作" width="175" fixed="right"><template #default="{row}"><el-button v-if="canPick" link type="primary" @click="openAction('issue',row)">领料</el-button><el-button v-if="canReplenish" link type="warning" @click="openAction('supplement',row)">补料</el-button><el-button v-if="canReturn" link type="success" @click="openAction('return',row)">退料</el-button></template></el-table-column>
        </el-table>
        <template v-if="current.status === 'completed'"><h3>原材料结算</h3><SettlementTable :rows="current.materials" /></template>
        <h3>生产单信息</h3>
        <el-descriptions :column="3" border>
          <el-descriptions-item label="成品">{{ current.finished_product_name }}</el-descriptions-item>
          <el-descriptions-item label="计划生产">{{ amount(current.planned_quantity, current.finished_product_unit) }}</el-descriptions-item>
          <el-descriptions-item label="实际生产">{{ current.actual_quantity == null ? '—' : amount(current.actual_quantity, current.finished_product_unit) }}</el-descriptions-item>
          <el-descriptions-item label="完成时间">{{ dateTime(current.completed_at) }}</el-descriptions-item>
          <el-descriptions-item label="BOM版本">V{{ current.bom_version }}</el-descriptions-item>
          <el-descriptions-item label="计划日期">{{ current.planned_date || '—' }}</el-descriptions-item>
          <el-descriptions-item label="创建人">{{ current.created_by_name }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ dateTime(current.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="备注" :span="3">{{ current.notes || '—' }}</el-descriptions-item>
        </el-descriptions>
        <h3>物料操作记录</h3>
        <el-table :data="current.material_records" stripe>
          <el-table-column label="单据/状态" min-width="200"><template #default="{row}"><el-button link type="primary" @click="materialDocumentId=row.document_id; materialDocumentOpen=true">{{ row.document_no }}</el-button><el-tag v-if="row.is_reversed" type="danger">已冲销</el-tag></template></el-table-column>
          <el-table-column label="时间" min-width="175"><template #default="{row}">{{ dateTime(row.created_at) }}</template></el-table-column>
          <el-table-column prop="document_no" label="单据号" min-width="190" />
          <el-table-column label="类型" width="110"><template #default="{row}">{{ kindNames[row.kind] }}</template></el-table-column>
          <el-table-column prop="batch_no" label="批次" min-width="130" />
          <el-table-column prop="location_name" label="库位名称" min-width="130" />
          <el-table-column label="数量" min-width="105"><template #default="{row}">{{ amount(row.quantity, row.unit) }}</template></el-table-column>
          <el-table-column label="库存变化" min-width="190"><template #default="{row}">{{ formatQuantity(row.before_quantity) }} → {{ formatQuantity(row.after_quantity) }} {{ row.unit }}</template></el-table-column>
          <el-table-column prop="operator_name" label="操作人" width="105" />
          <el-table-column prop="notes" label="备注" min-width="140" show-overflow-tooltip />
          <template #empty><EmptyState title="尚无物料操作" description="领料、补料或退料后将在这里显示批次记录。" /></template>
        </el-table>
      </template>
    </div>
    <template #footer><el-button @click="detailVisible=false">关闭</el-button></template>
  </el-dialog>

  <el-dialog v-model="completeVisible" title="完成生产" width="min(1280px, 95vw)" :close-on-click-modal="false" :close-on-press-escape="!completing" :show-close="!completing">
    <el-alert v-if="settlementError" :title="settlementError" type="error" show-icon class="dialog-feedback" />
    <el-form label-width="120px" :disabled="completing"><el-form-item label="实际完成数量" required><el-input v-model="actualQuantity" inputmode="decimal" placeholder="填写最终实际产量"><template #append>{{ current?.finished_product_unit }}</template></el-input></el-form-item></el-form>
    <el-alert title="所有批次数量合计必须等于实际产量。完成后生产事实不可撤销。" type="info" :closable="false" />
    <el-table :data="finishedBatches"><el-table-column label="成品批次号"><template #default="{row}"><el-input v-model="row.batch_no" maxlength="80" :disabled="completing" /></template></el-table-column><el-table-column label="批次数量"><template #default="{row}"><el-input v-model="row.quantity" inputmode="decimal" :disabled="completing" /></template></el-table-column><el-table-column label="到期日期（可选）"><template #default="{row}"><el-date-picker v-model="row.expiry_date" type="date" value-format="YYYY-MM-DD" :disabled="completing" /></template></el-table-column><el-table-column width="90"><template #default="{ $index }"><el-button :disabled="completing || finishedBatches.length === 1" @click="finishedBatches.splice($index,1)">移除</el-button></template></el-table-column></el-table>
    <el-button :disabled="completing" @click="finishedBatches.push({batch_no:'',quantity:'',expiry_date:''})">增加成品批次</el-button>
    <el-button :loading="settling" :disabled="completing" @click="previewSettlement">预览结算</el-button>
    <TableSkeleton v-if="settling" :rows="3" />
    <template v-if="settlement"><el-alert class="settlement-notice" type="info" :closable="false" show-icon :title="`确认后成品入库 ${formatQuantity(settlement.actual_quantity)} ${settlement.finished_product_unit}，并冻结生产单。提交时系统会重新核对物料记录。`" /><SettlementTable :rows="settlement.materials" /></template>
    <template #footer><el-button :disabled="completing" @click="completeVisible=false">取消</el-button><el-button type="primary" :disabled="!settlement || settling" :loading="completing" @click="finishProduction">确认完成</el-button></template>
  </el-dialog>

  <DocumentReversal v-model="materialDocumentOpen" :document-id="materialDocumentId" @changed="refreshDetail(); load()" />

  <el-dialog v-model="editVisible" title="编辑生产单信息" width="520px" :close-on-click-modal="false">
    <el-alert v-if="error" :title="error" type="error" show-icon class="dialog-feedback" />
    <el-form label-width="100px"><el-form-item label="计划日期"><el-date-picker v-model="editForm.planned_date" type="date" value-format="YYYY-MM-DD" /></el-form-item><el-form-item label="备注"><el-input v-model="editForm.notes" type="textarea" maxlength="2000" /></el-form-item></el-form>
    <template #footer><el-button @click="editVisible=false">取消</el-button><el-button type="primary" :loading="editSaving" @click="saveEdit">保存</el-button></template>
  </el-dialog>

  <el-dialog v-model="actionVisible" :title="`${actionNames[action.kind]} · ${action.material?.material_name || ''}`" width="820px" :close-on-click-modal="false" :close-on-press-escape="!actionSaving" :show-close="!actionSaving">
    <el-alert v-if="error" :title="error" type="error" show-icon class="dialog-feedback" />
    <el-alert v-if="action.kind !== 'return'" title="支持今天领一点、明天继续领。填写本次原料数量后按批次分配，也可直接填写各批次实际数量；无需一次领满计划理论需求。" type="info" :closable="false" class="dialog-feedback" />
    <el-alert v-else title="退料只能回到本生产单实际领用过的原批次，并且不能超过该批次累计净领出量。" type="info" :closable="false" class="dialog-feedback" />
    <div v-if="action.material" class="action-summary">
      <span>理论 {{ amount(action.material.planned_required_quantity, action.unit) }}</span>
      <span>已领 {{ amount(action.material.issued_quantity, action.unit) }}</span>
      <span>当前净领用 {{ amount(action.material.net_issued_quantity, action.unit) }}</span>
      <span>理论缺口（参考）{{ amount(remainingRequirement(action.material), action.unit) }}</span>
      <strong>本次合计 {{ amount(actionTotal, action.unit) }}</strong>
    </div>
    <el-form v-if="action.kind !== 'return'" inline :disabled="actionSaving || candidateLoading" @submit.prevent="applySuggestion"><el-form-item label="本次原料总量"><el-input v-model="action.desired" inputmode="decimal" placeholder="仅填今天实际需要量" style="width:240px"><template #append>{{ action.unit }}</template></el-input></el-form-item><el-button @click="applySuggestion">按本次数量分配</el-button></el-form>
    <TableSkeleton v-if="candidateLoading && !action.allocations.length" :rows="3" />
    <el-table v-else :data="action.allocations" v-loading="candidateLoading" border>
      <el-table-column prop="batch_no" label="批次" min-width="150" />
      <el-table-column prop="location_name" label="库位名称" min-width="135" />
      <el-table-column v-if="action.kind !== 'return'" prop="expiry_date" label="到期日期" width="130" />
      <el-table-column v-if="action.kind !== 'return'" label="建议领用数量" min-width="130"><template #default="{row}">{{ amount(row.suggested_quantity, action.unit) }}</template></el-table-column>
      <el-table-column v-if="action.kind !== 'return'" label="库存余额" min-width="130"><template #default="{row}"><span class="data-value">{{ amount(row.remaining_quantity, row.unit) }}</span></template></el-table-column>
      <el-table-column v-else label="最多可退" min-width="130"><template #default="{row}"><span class="data-value">{{ amount(row.max_return, action.unit) }}</span></template></el-table-column>
      <el-table-column :label="`本次${actionNames[action.kind]}数量 (${action.unit})`" min-width="180"><template #default="{row}"><el-input v-model="row.quantity" :disabled="actionSaving || candidateLoading" inputmode="decimal" placeholder="不使用则留空" /></template></el-table-column>
      <template #empty><EmptyState :title="action.kind === 'return' ? '暂无可退批次' : '暂无可用批次'" description="请核查原料库存、有效期和当前生产单物料记录。" /></template>
    </el-table>
    <el-form label-width="70px" style="margin-top:16px" :disabled="actionSaving"><el-form-item label="备注"><el-input v-model="action.notes" maxlength="2000" /></el-form-item></el-form>
    <template #footer><el-button :disabled="actionSaving" @click="actionVisible=false">取消</el-button><el-button type="primary" :disabled="candidateLoading" :loading="actionSaving" @click="submitAction">确认{{ actionNames[action.kind] }}</el-button></template>
  </el-dialog>
  <el-dialog v-model="registerVisible" title="登记实际产量" width="480px" :close-on-click-modal="false">
    <el-alert title="登记不会自动入库；由仓库预览结算并完成生产。" type="info" :closable="false" class="dialog-feedback" />
    <el-alert v-if="registerError" :title="registerError" type="error" :closable="false" class="dialog-feedback" />
    <el-form @submit.prevent="registerActual"><el-form-item label="实际数量"><el-input v-model="registeredQuantity" :disabled="registering" inputmode="decimal"><template #append>{{ current?.finished_product_unit }}</template></el-input></el-form-item></el-form>
    <template #footer><el-button @click="registerVisible = false" :disabled="registering">取消</el-button><el-button type="primary" @click="registerActual" :loading="registering">保存产量</el-button></template>
  </el-dialog>
</template>

<style scoped>
.dialog-intro { margin: 0 0 16px; color: var(--wms-text-secondary); font-size: 12px; }
.dialog-feedback { margin-bottom: 16px; }
.preview-panel { margin: 15px 0 0; }
.preview-title { margin-bottom: 12px; color: #225a9d; font-weight: 650; }
.detail-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.detail-identity { display: flex; flex-wrap: wrap; align-items: center; gap: 9px; }
.detail-identity strong { font-size: 19px; font-weight: 700; }
.detail-identity > span:last-child { color: var(--wms-text-secondary); font-size: 12px; }
.production-primary-actions { display: flex; align-items: center; gap: 9px; margin: 0 0 16px; padding: 11px 13px; border: 1px solid rgba(22,119,232,.12); border-radius: 12px; background: #f6faff; }
.production-primary-actions span { margin-right: auto; color: #4d647e; font-size: 12px; font-weight: 650; }
.action-summary { display: flex; flex-wrap: wrap; gap: 12px 24px; padding: 12px 15px; margin-bottom: 16px; border: 1px solid var(--wms-border); border-radius: 11px; background: var(--wms-surface-soft); color: var(--wms-text-secondary); font-size: 13px; font-variant-numeric: tabular-nums; }
.settlement-notice { margin: 16px 0; }
h3 { margin: 23px 0 12px; font-size: 15px; font-weight: 680; }
small { color: var(--wms-text-tertiary); }
</style>
