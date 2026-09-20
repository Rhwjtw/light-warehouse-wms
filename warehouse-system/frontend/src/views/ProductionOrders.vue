<script setup>
import { postingRequest } from '../postingRequest'
import { quantityUnits, quantityText } from '../quantity'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { auth } from '../auth'
import { canAdjustProduction } from '../permissions'
import SettlementTable from '../components/SettlementTable.vue'

const registerVisible = ref(false), registering = ref(false), registeredQuantity = ref(''), registerError = ref('')
function openRegister() { registeredQuantity.value = current.value.actual_quantity || ''; registerError.value = ''; registerVisible.value = true }
async function registerActual() {
  registering.value = true; registerError.value = ''
  try {
    current.value = (await api.put(`/production-orders/${current.value.id}/actual-quantity`, { actual_quantity: registeredQuantity.value })).data
    registerVisible.value = false; ElMessage.success('实际产量已登记，待仓库确认完成生产'); await load()
  } catch (e) { registerError.value = e.message } finally { registering.value = false }
}
const completeVisible = ref(false), completing = ref(false), settling = ref(false)
const actualQuantity = ref(''), settlement = ref(null), settlementError = ref('')
function openComplete() {
  actualQuantity.value = current.value?.actual_quantity || ''; settlement.value = null; settlementError.value = ''; completeVisible.value = true
}
watch(actualQuantity, () => { settlement.value = null })
async function previewSettlement() {
  settlement.value = null; settlementError.value = ''; settling.value = true
  const quantity = actualQuantity.value
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
    current.value = (await api.post(`/production-orders/${current.value.id}/complete`, { actual_quantity: actualQuantity.value })).data
    completeVisible.value = false; ElMessage.success('生产已完成，成品已入库'); await load()
  } catch (e) { settlementError.value = e.message; settlement.value = null }
  finally { completing.value = false }
}

const route = useRoute()
const canCreate = computed(() => auth.user?.role === 'admin')
const canPost = computed(() => ['admin', 'warehouse'].includes(auth.user?.role))
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
const positiveDecimal = /^\d{1,14}(\.\d{1,6})?$/
const actionNames = { issue: '领料', supplement: '补料', return: '退料' }
const statusNames = { draft: '未开始', ready: '待领料', in_progress: '生产中', completed: '已完成', received: '已入库', cancelled: '已取消' }
const statusTypes = { ready: 'warning', in_progress: 'primary', completed: 'success', received: 'success', cancelled: 'info' }
const kindNames = { issue: '生产领料', supplement: '生产补料', return: '生产退料' }

function dateTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { timeZone: auth.user?.business_timezone || 'Asia/Shanghai', hour12: false }) : '—'
}
function amount(value, unit) { return `${value ?? '0'} ${unit || ''}` }
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
  const map = new Map()
  for (const record of current.value.material_records.filter(row => row.order_material_id === material.id)) {
    const existing = map.get(record.batch_id) || { batch_id: record.batch_id, batch_no: record.batch_no, remaining_quantity: '—', expiry_date: '原领料批次', max_return: '0' }
    existing.max_return = quantityText(quantityUnits(existing.max_return) + (record.kind === 'return' ? -1n : 1n) * quantityUnits(record.quantity))
    map.set(record.batch_id, existing)
  }
  return [...map.values()].filter(row => quantityUnits(row.max_return) > 0n).map(row => ({ ...row, quantity: '' }))
}
async function openAction(kind, material) {
  Object.assign(action, { kind, material, desired: '', unit: material.base_unit, notes: '', allocations: [], requestKey: '', requestContent: '' })
  actionVisible.value = true; error.value = ''
  if (kind === 'return') {
    action.allocations = returnCandidates(material); return
  }
  await loadCandidates()
}
async function loadCandidates() {
  candidateLoading.value = true
  try {
    const params = validQuantity(action.desired) ? { quantity: action.desired } : {}
    const result = (await api.get(`/production-orders/${current.value.id}/materials/${action.material.material_id}/fefo-suggestion`, { params })).data
    if (!action.desired && action.kind === 'issue' && Number(result.remaining_to_issue) > 0) action.desired = result.remaining_to_issue
    const suggestions = new Map(result.suggestions.map(row => [row.batch_id, row.quantity]))
    action.allocations = result.batches.map(row => ({ ...row, quantity: suggestions.get(row.id) || '' }))
  } catch (e) { error.value = e.message; action.allocations = [] }
  finally { candidateLoading.value = false }
}
async function applySuggestion() {
  if (!validQuantity(action.desired)) { error.value = '请填写大于 0、最多六位小数的本次数量'; return }
  await loadCandidates()
}
async function submitAction() {
  if (action.allocations.some(row => String(row.quantity).trim() !== '' && !validQuantity(row.quantity))) {
    error.value = '批次数量必须大于 0 且最多六位小数'; return
  }
  const allocations = action.allocations
    .filter(row => validQuantity(row.quantity))
    .map(row => ({ batch_id: row.batch_id ?? row.id, quantity: String(row.quantity) }))
  if (!allocations.length) { error.value = '请至少填写一个批次的正数量'; return }
  if (action.kind === 'return' && allocations.some(row => {
    const candidate = action.allocations.find(item => (item.batch_id ?? item.id) === row.batch_id)
    return quantityUnits(row.quantity) > quantityUnits(candidate.max_return)
  })) { error.value = '退料数量不能超过该生产单在对应批次的累计净领出量'; return }
  actionSaving.value = true; error.value = ''
  try {
    const payload = { order_material_id: action.material.id, unit: action.unit, allocations, notes: action.notes || null }
    await api.post(`/production-orders/${current.value.id}/${action.kind}`, postingRequest(action, payload, requestId))
    actionVisible.value = false; ElMessage.success(`${actionNames[action.kind]}成功`)
    await refreshDetail(); await load()
  } catch (e) { error.value = e.message }
  finally { actionSaving.value = false }
}

watch(() => route.meta.resource, () => { page.value = 1; load() })
onMounted(() => { load(); loadProducts() })
</script>

<template>
  <div class="page-heading">
    <div><h1>{{ route.meta.title }}</h1><p>生产单创建时固化 BOM 配方和计划理论需求；领料、补料、退料按批次即时联动库存。</p></div>
    <div><el-button @click="load">刷新</el-button><el-button v-if="canCreate" type="primary" @click="openCreate">新建生产单</el-button></div>
  </div>
  <el-alert v-if="error" :title="error" type="error" :closable="true" show-icon style="margin-bottom:16px" @close="error=''" />
  <el-card shadow="never">
    <el-form inline @submit.prevent="page=1; load()">
      <el-form-item label="搜索"><el-input v-model="query" clearable maxlength="120" placeholder="生产单号或成品名称" @clear="page=1; load()" /></el-form-item>
      <el-form-item label="状态"><el-select v-model="status" clearable style="width:150px"><el-option v-for="(label,key) in statusNames" :key="key" :value="key" :label="label" /></el-select></el-form-item>
      <el-button native-type="submit" type="primary">查询</el-button>
    </el-form>
    <el-table :data="rows" v-loading="loading" stripe row-key="id" empty-text="暂无生产单">
      <el-table-column prop="order_no" label="生产单号" min-width="190" />
      <el-table-column prop="finished_product_name" label="成品" min-width="170" />
      <el-table-column label="计划生产" min-width="130"><template #default="{row}">{{ amount(row.planned_quantity, row.finished_product_unit) }}</template></el-table-column>
      <el-table-column label="BOM版本" width="100"><template #default="{row}">V{{ row.bom_version }}</template></el-table-column>
      <el-table-column label="状态" width="110"><template #default="{row}"><el-tag :type="statusTypes[row.status]">{{ statusNames[row.status] || row.status }}</el-tag></template></el-table-column>
      <el-table-column label="创建时间" min-width="185"><template #default="{row}">{{ dateTime(row.created_at) }}</template></el-table-column>
      <el-table-column label="操作" width="100" fixed="right"><template #default="{row}"><el-button link type="primary" @click="openDetail(row)">查看</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[10,20,50,100]" layout="total, sizes, prev, pager, next" class="pagination" @current-change="load" @size-change="page=1; load()" />
  </el-card>

  <el-dialog v-model="createVisible" title="新建生产单" width="820px" destroy-on-close :close-on-click-modal="false">
    <el-alert v-if="error" :title="error" type="error" show-icon style="margin-bottom:16px" />
    <el-form label-width="110px" :disabled="creating">
      <el-form-item label="成品" required><el-select v-model="createForm.finished_product_id" filterable remote :remote-method="loadProducts" placeholder="输入成品编码或名称"><el-option v-for="item in products" :key="item.id" :value="item.id" :label="`${item.code} · ${item.name} (${item.unit})`" /></el-select></el-form-item>
      <el-form-item label="计划数量" required><el-input v-model="createForm.planned_quantity" inputmode="decimal" placeholder="如 1000" /></el-form-item>
      <el-form-item label="计划生产日期"><el-date-picker v-model="createForm.planned_date" type="date" value-format="YYYY-MM-DD" /></el-form-item>
      <el-form-item label="备注"><el-input v-model="createForm.notes" type="textarea" maxlength="2000" show-word-limit /></el-form-item>
      <el-form-item><el-button :loading="previewLoading" @click="calculatePreview">预览理论用料</el-button></el-form-item>
    </el-form>
    <el-card v-if="preview" shadow="never" class="preview-panel">
      <div class="preview-title">当前有效 BOM V{{ preview.bom_version }} · {{ amount(preview.planned_quantity, preview.finished_product_unit) }}</div>
      <el-table :data="preview.requirements" size="small">
        <el-table-column prop="material_name" label="原材料" />
        <el-table-column label="单单位用量"><template #default="{row}">{{ row.quantity_per_unit }}{{ row.bom_unit }} / {{ preview.finished_product_unit }}</template></el-table-column>
        <el-table-column label="计划理论需求"><template #default="{row}">{{ amount(row.suggested_quantity, row.suggested_unit) }} <small>（{{ row.total_quantity }}{{ row.total_unit }}）</small></template></el-table-column>
      </el-table>
    </el-card>
    <template #footer><el-button @click="createVisible=false">取消</el-button><el-button type="primary" :loading="creating" @click="createOrder">创建生产单</el-button></template>
  </el-dialog>

  <el-dialog v-model="detailVisible" title="生产单详情" width="min(1280px, 95vw)" destroy-on-close :close-on-click-modal="false">
    <div v-loading="detailLoading">
      <template v-if="current">
        <div class="detail-toolbar"><div><strong>{{ current.order_no }}</strong><el-tag :type="statusTypes[current.status]">{{ statusNames[current.status] }}</el-tag></div><div><el-button v-if="canCreate && !['cancelled','completed','received'].includes(current.status)" @click="openEdit">编辑信息</el-button><el-button v-if="canCreate && !['cancelled','completed','received'].includes(current.status)" type="danger" plain @click="cancelOrder">取消生产单</el-button></div></div>
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
        <el-button v-if="canAdjustProduction(auth.user?.role) && ['ready','in_progress'].includes(current.status)" style="margin-top:16px" @click="openRegister">登记实际产量</el-button>
        <el-button v-if="canPost && ['ready','in_progress'].includes(current.status)" type="primary" style="margin-top:16px" @click="openComplete">完成生产</el-button>
        <template v-if="current.status === 'completed'"><h3>原材料结算</h3><SettlementTable :rows="current.materials" /></template>
        <h3>原材料需求与当前净领用</h3>
        <el-table :data="current.materials" border stripe row-key="id">
          <el-table-column prop="material_name" label="原材料" min-width="150" />
          <el-table-column label="单位用量" min-width="135"><template #default="{row}">{{ row.quantity_per_unit }}{{ row.bom_unit }} / {{ current.finished_product_unit }}</template></el-table-column>
          <el-table-column label="计划理论需求" min-width="130"><template #default="{row}">{{ amount(row.planned_required_quantity, row.base_unit) }}</template></el-table-column>
          <el-table-column label="已领" min-width="100"><template #default="{row}">{{ amount(row.issued_quantity, row.base_unit) }}</template></el-table-column>
          <el-table-column label="补料" min-width="100"><template #default="{row}">{{ amount(row.supplemented_quantity, row.base_unit) }}</template></el-table-column>
          <el-table-column label="退料" min-width="100"><template #default="{row}">{{ amount(row.returned_quantity, row.base_unit) }}</template></el-table-column>
          <el-table-column label="当前净领用" min-width="125"><template #default="{row}"><strong>{{ amount(row.net_issued_quantity, row.base_unit) }}</strong></template></el-table-column>
          <el-table-column v-if="canAdjustProduction(auth.user?.role) && ['ready','in_progress'].includes(current.status)" label="操作" width="175" fixed="right"><template #default="{row}"><el-button v-if="canPost" link type="primary" @click="openAction('issue',row)">领料</el-button><el-button link type="warning" @click="openAction('supplement',row)">补料</el-button><el-button link type="success" @click="openAction('return',row)">退料</el-button></template></el-table-column>
        </el-table>
        <h3>物料操作记录</h3>
        <el-table :data="current.material_records" stripe empty-text="尚未发生物料业务">
          <el-table-column label="时间" min-width="175"><template #default="{row}">{{ dateTime(row.created_at) }}</template></el-table-column>
          <el-table-column prop="document_no" label="单据号" min-width="190" />
          <el-table-column label="类型" width="110"><template #default="{row}">{{ kindNames[row.kind] }}</template></el-table-column>
          <el-table-column prop="batch_no" label="批次" min-width="130" />
          <el-table-column label="数量" min-width="105"><template #default="{row}">{{ amount(row.quantity, row.unit) }}</template></el-table-column>
          <el-table-column label="库存变化" min-width="190"><template #default="{row}">{{ row.before_quantity }} → {{ row.after_quantity }} {{ row.unit }}</template></el-table-column>
          <el-table-column prop="operator_name" label="操作人" width="105" />
          <el-table-column prop="notes" label="备注" min-width="140" show-overflow-tooltip />
        </el-table>
      </template>
    </div>
    <template #footer><el-button @click="detailVisible=false">关闭</el-button></template>
  </el-dialog>

  <el-dialog v-model="completeVisible" title="完成生产" width="min(1200px, 95vw)" :close-on-click-modal="false" :close-on-press-escape="!completing" :show-close="!completing">
    <el-alert v-if="settlementError" :title="settlementError" type="error" show-icon style="margin-bottom:16px" />
    <el-form label-width="120px" :disabled="completing"><el-form-item label="实际完成数量" required><el-input v-model="actualQuantity" inputmode="decimal" placeholder="填写最终实际产量"><template #append>{{ current?.finished_product_unit }}</template></el-input></el-form-item></el-form>
    <el-button :loading="settling" :disabled="completing" @click="previewSettlement">预览结算</el-button>
    <template v-if="settlement"><p>确认后成品入库 {{ settlement.actual_quantity }} {{ settlement.finished_product_unit }}，并冻结生产单。提交时系统会重新核对物料记录。</p><SettlementTable :rows="settlement.materials" /></template>
    <template #footer><el-button :disabled="completing" @click="completeVisible=false">取消</el-button><el-button type="primary" :disabled="!settlement || settling" :loading="completing" @click="finishProduction">确认完成</el-button></template>
  </el-dialog>

  <el-dialog v-model="editVisible" title="编辑生产单信息" width="520px" :close-on-click-modal="false">
    <el-alert v-if="error" :title="error" type="error" show-icon style="margin-bottom:16px" />
    <el-form label-width="100px"><el-form-item label="计划日期"><el-date-picker v-model="editForm.planned_date" type="date" value-format="YYYY-MM-DD" /></el-form-item><el-form-item label="备注"><el-input v-model="editForm.notes" type="textarea" maxlength="2000" /></el-form-item></el-form>
    <template #footer><el-button @click="editVisible=false">取消</el-button><el-button type="primary" :loading="editSaving" @click="saveEdit">保存</el-button></template>
  </el-dialog>

  <el-dialog v-model="actionVisible" :title="`${actionNames[action.kind]} · ${action.material?.material_name || ''}`" width="820px" :close-on-click-modal="false">
    <el-alert v-if="error" :title="error" type="error" show-icon style="margin-bottom:16px" />
    <el-alert v-if="action.kind !== 'return'" title="批次按到期日、入库时间、批次 ID 依次推荐；可修改各批次数量，但过期或库存不足会由服务端拒绝。" type="info" :closable="false" style="margin-bottom:16px" />
    <el-alert v-else title="退料只能回到本生产单实际领用过的原批次，并且不能超过该批次累计净领出量。" type="info" :closable="false" style="margin-bottom:16px" />
    <div v-if="action.material" class="action-summary">
      <span>理论 {{ amount(action.material.planned_required_quantity, action.unit) }}</span>
      <span>已领 {{ amount(action.material.issued_quantity, action.unit) }}</span>
      <span>当前净领用 {{ amount(action.material.net_issued_quantity, action.unit) }}</span>
    </div>
    <el-form v-if="action.kind !== 'return'" inline @submit.prevent="applySuggestion"><el-form-item label="本次总量"><el-input v-model="action.desired" inputmode="decimal" style="width:180px"><template #append>{{ action.unit }}</template></el-input></el-form-item><el-button @click="applySuggestion">重新按 FEFO 分配</el-button></el-form>
    <el-table :data="action.allocations" v-loading="candidateLoading" border empty-text="暂无可用批次">
      <el-table-column prop="batch_no" label="批次" min-width="150" />
      <el-table-column v-if="action.kind !== 'return'" prop="expiry_date" label="到期日期" width="130" />
      <el-table-column v-if="action.kind !== 'return'" label="库存余额" min-width="130"><template #default="{row}">{{ amount(row.remaining_quantity, row.unit) }}</template></el-table-column>
      <el-table-column v-else label="最多可退" min-width="130"><template #default="{row}">{{ amount(row.max_return, action.unit) }}</template></el-table-column>
      <el-table-column :label="`${actionNames[action.kind]}数量 (${action.unit})`" min-width="180"><template #default="{row}"><el-input v-model="row.quantity" inputmode="decimal" placeholder="不使用则留空" /></template></el-table-column>
    </el-table>
    <el-form label-width="70px" style="margin-top:16px"><el-form-item label="备注"><el-input v-model="action.notes" maxlength="2000" /></el-form-item></el-form>
    <template #footer><el-button @click="actionVisible=false">取消</el-button><el-button type="primary" :loading="actionSaving" @click="submitAction">确认{{ actionNames[action.kind] }}</el-button></template>
  </el-dialog>
  <el-dialog v-model="registerVisible" title="登记实际产量" width="480px" :close-on-click-modal="false">
    <el-alert title="登记不会自动入库；由仓库预览结算并完成生产。" type="info" :closable="false" />
    <el-alert v-if="registerError" :title="registerError" type="error" :closable="false" />
    <el-form @submit.prevent="registerActual"><el-form-item label="实际数量"><el-input v-model="registeredQuantity" :disabled="registering" inputmode="decimal"><template #append>{{ current?.finished_product_unit }}</template></el-input></el-form-item></el-form>
    <template #footer><el-button @click="registerVisible = false" :disabled="registering">取消</el-button><el-button type="primary" @click="registerActual" :loading="registering">保存产量</el-button></template>
  </el-dialog>
</template>

<style scoped>
.preview-panel { margin: 10px 0; }
.preview-title { margin-bottom: 12px; font-weight: 600; }
.detail-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 18px; }
.detail-toolbar strong { margin-right: 12px; font-size: 19px; }
.action-summary { display: flex; gap: 24px; padding: 12px 15px; margin-bottom: 16px; background: #f6f8fb; border-radius: 6px; color: #53647a; font-size: 13px; }
h3 { margin-top: 24px; }
small { color: #8b96a6; }
</style>
