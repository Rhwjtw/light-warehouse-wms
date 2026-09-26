<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import { api } from '../api'
import { auth } from '../auth'
import { canOperate } from '../permissions'
import DataPanel from '../components/ui/DataPanel.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import FilterBar from '../components/ui/FilterBar.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
import TableSkeleton from '../components/ui/TableSkeleton.vue'

const route = useRoute()
const resource = computed(() => route.meta.resource)
const receipt = computed(() => resource.value === 'material-receipts')
const outbound = computed(() => resource.value === 'material-outbounds')
const writePage = computed(() => receipt.value || outbound.value)
const canWrite = computed(() => canOperate(auth.user?.role, 'material.inventory.write'))
const form = reactive({ material_id: null, batch_no: '', batch_id: null, quantity: '', unit: 'kg', production_date: '', expiry_date: '', supplier_id: null, notes: '' })
const options = reactive({ materials: [], suppliers: [] })
const units = ref([])
const compatibleUnits = computed(() => {
  const selected = options.materials.find(row => row.id === form.material_id)
  const source = units.value.find(row => row.code === selected?.unit)
  return source ? units.value.filter(row => row.dimension === source.dimension) : selected ? [{ code: selected.unit }] : []
})
const candidates = ref([]), candidatePage = ref(1), candidateTotal = ref(0)
const saving = ref(false), error = ref(''), success = ref(null), rows = ref([]), total = ref(0), loading = ref(false)
const query = ref(''), page = ref(1), pageSize = ref(20)
let requestId = '', lastPayload = '', generation = 0, batchGeneration = 0
// generation 令牌丢弃晚到的旧查询响应，避免快速切页/换原料时旧数据覆盖当前页面。
const endpoint = computed(() => receipt.value ? 'material-receipts' : outbound.value ? 'material-transactions' : resource.value === 'batches' ? 'material-batches' : resource.value)
const panelTitle = computed(() => receipt.value ? '入库登记' : outbound.value ? '出库登记' : route.meta.title)
const listTitle = computed(() => receipt.value ? '最近入库记录' : outbound.value ? '最近出库记录' : route.meta.title)
const columns = computed(() => receipt.value ? [
  ['created_at', '入库时间'], ['document_no', '入库单号'], ['material_name', '原料名称'], ['batch_no', '批号'],
  ['received_quantity', '入库数量'], ['shelved_quantity', '已上架'], ['pending_quantity', '待上架'], ['unit', '单位'],
  ['supplier_name', '供应商'], ['status', '状态'],
] : resource.value === 'material-stock' ? [
  ['code', '原料编码'], ['name', '原料名称'], ['remaining_quantity', '总库存（含过期）'], ['unit', '单位'],
] : resource.value === 'batches' ? [
  ['material_code', '原料编码'], ['material_name', '原料名称'], ['batch_no', '批号'], ['received_quantity', '累计进入库位'],
  ['remaining_quantity', '剩余库存'], ['unit', '单位'], ['production_date', '生产日期'], ['expiry_date', '到期日期'],
  ['supplier_name', '供应商'], ['location_name', '库位'],
] : [
  ['created_at', '记账时间'], ['document_no', '单据号'], ['material_name', '原料'], ['batch_no', '批号'], ['kind', '类型'],
  ['input_quantity', '输入数量'], ['input_unit', '输入单位'], ['before_quantity', '变动前'], ['change_quantity', '变动量'],
  ['after_quantity', '变动后'], ['unit', '库存单位'], ['source_location_name', '原库位'],
  ['target_location_name', '目标库位'], ['production_order_no', '生产单'], ['operator_name', '操作人'], ['notes', '备注'],
])
function display(row, key) {
  if (key === 'kind') return ({ material_receipt: '原料入库', material_outbound: '原料出库', issue: '生产领料', supplement: '生产补料', return: '生产退料', putaway: '上架', transfer: '移库', count_gain: '盘盈', count_loss: '盘亏', adjustment: '库存调整' })[row[key]] || row[key]
  if (key === 'status') return ({ pending: '待上架', partial: '部分上架', shelved: '已上架' })[row[key]] || row[key]
  if (key === 'created_at') return new Date(row[key]).toLocaleString('zh-CN', { timeZone: auth.user?.business_timezone || 'Asia/Shanghai', hour12: false })
  return row[key]
}
function isQuantityColumn(key) { return ['received_quantity', 'remaining_quantity', 'shelved_quantity', 'pending_quantity', 'input_quantity', 'before_quantity', 'change_quantity', 'after_quantity'].includes(key) }
const optionGeneration = {}
async function searchOptions(name, q = '') {
  const ticket = optionGeneration[name] = (optionGeneration[name] || 0) + 1
  try {
    const result = await api.get('/' + name, { params: { q, is_active: true, page_size: 100 } })
    if (ticket === optionGeneration[name]) options[name] = result.data.items
  } catch (e) { error.value = e.message }
}
async function load() {
  const ticket = ++generation
  loading.value = true
  try {
    const params = { q: query.value, page: page.value, page_size: pageSize.value }
    if (outbound.value) params.kind = 'material_outbound'
    const result = await api.get('/' + endpoint.value, { params })
    if (ticket === generation) { rows.value = result.data.items; total.value = result.data.total }
  } catch (e) { if (ticket === generation) { error.value = e.message; rows.value = [] } }
  finally { if (ticket === generation) loading.value = false }
}
async function loadBatches(selectDefault = false) {
  const ticket = ++batchGeneration
  if (!form.material_id) { candidates.value = []; candidateTotal.value = 0; return }
  try {
    const result = await api.get('/material-fefo', { params: { material_id: form.material_id, page: candidatePage.value, page_size: 20 } })
    // 页面默认选中 FEFO 第一批只是操作建议；服务端提交时仍会锁定批次并复核有效期和余额。
    if (ticket !== batchGeneration) return
    candidates.value = result.data.items; candidateTotal.value = result.data.total
    if (selectDefault) form.batch_id = candidates.value[0]?.id ?? null
  } catch (e) { if (ticket === batchGeneration) { candidates.value = []; form.batch_id = null; error.value = e.message } }
}
async function materialChanged() {
  form.batch_id = null; candidatePage.value = 1
  const selected = options.materials.find(row => row.id === form.material_id)
  if (selected) form.unit = selected.unit
  if (outbound.value) await loadBatches(true)
}
async function submit() {
  if (saving.value) return
  error.value = ''; success.value = null
  if (!form.material_id || !/^\d{1,14}(\.\d{1,6})?$/.test(form.quantity) || !/[1-9]/.test(form.quantity)) {
    error.value = '请选择原料，数量必须大于零，最多六位小数'; return
  }
  if (receipt.value && (!form.batch_no.trim() || !form.production_date || !form.expiry_date || !form.supplier_id)) {
    error.value = '请完整填写批号、生产日期、到期日期和供应商'; return
  }
  if (outbound.value && !form.batch_id) { error.value = '请选择有库存且未过期的批次'; return }
  const payload = { material_id: form.material_id, quantity: form.quantity, unit: form.unit, notes: form.notes || null }
  if (receipt.value) Object.assign(payload, { batch_no: form.batch_no.trim(), production_date: form.production_date, expiry_date: form.expiry_date, supplier_id: form.supplier_id })
  else payload.batch_id = form.batch_id
  const signature = JSON.stringify(payload)
  // 网络错误后保持原输入会沿用 requestId；修改任一业务字段则视为新操作并换新标识。
  if (signature !== lastPayload || !requestId) { requestId = crypto.randomUUID(); lastPayload = signature }
  saving.value = true
  try {
    const result = await api.post('/' + resource.value, { ...payload, request_id: requestId })
    success.value = result.data; form.quantity = ''; requestId = ''; lastPayload = ''
    ElMessage.success(receipt.value ? '入库成功，等待上架' : '记账成功'); await load()
    if (outbound.value) { candidatePage.value = 1; await loadBatches(true) }
  } catch (e) { error.value = e.message + '；若连接中断，可保持原输入重试。' }
  finally { saving.value = false }
}
watch(resource, async () => {
  generation++; batchGeneration++; page.value = 1; query.value = ''; error.value = ''; success.value = null; rows.value = []
  form.quantity = ''; form.batch_id = null; requestId = ''; lastPayload = ''; candidatePage.value = 1
  await load()
  if (writePage.value) {
    await Promise.all([...['materials', 'suppliers'].map(name => searchOptions(name)),
      api.get('/units').then(result => { units.value = result.data }).catch(e => { error.value = e.message })])
    if (outbound.value) await loadBatches(true)
  }
}, { immediate: true })
</script>

<template>
  <PageHeader eyebrow="原材料库存" :title="route.meta.title" description="批次记账与库存流水同步保存，关键数量按库存单位清晰呈现。">
    <template #actions><el-button :icon="Refresh" :loading="loading" @click="error = ''; load()">刷新</el-button></template>
  </PageHeader>

  <div v-if="error || success" class="feedback-stack">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-alert v-if="success" type="success" :closable="false" show-icon :title="receipt ? `入库单 ${success.document_no} 已创建，待上架 ${success.after_quantity} ${success.unit}` : `已记账：${success.document_no}，批次余额 ${success.after_quantity} ${success.unit}`" />
  </div>

  <DataPanel v-if="writePage && canWrite" :title="panelTitle" :description="receipt ? '入库后形成待上架数量，在上架页面选择实际库位。' : '默认推荐最早到期的可用批次，也可选择其他合法批次。'" kind="action">
    <el-form label-width="105px" :disabled="saving" @submit.prevent="submit">
      <el-row :gutter="20"><el-col :span="12"><el-form-item label="原材料" required><el-select v-model="form.material_id" filterable remote :remote-method="q => searchOptions('materials', q)" placeholder="输入编码或名称搜索" @change="materialChanged"><el-option v-for="item in options.materials" :key="item.id" :value="item.id" :label="`${item.code} · ${item.name} (${item.unit})`" /></el-select></el-form-item></el-col>
        <el-col :span="12"><el-form-item label="数量 / 单位" required><div class="quantity-input"><el-input v-model="form.quantity" inputmode="decimal" maxlength="21" placeholder="如 10.500001" /><el-select v-model="form.unit"><el-option v-for="u in compatibleUnits" :key="u.code" :value="u.code" :label="u.code" /></el-select></div></el-form-item></el-col></el-row>
      <template v-if="receipt"><el-row :gutter="20"><el-col :span="12"><el-form-item label="批号" required><el-input v-model="form.batch_no" maxlength="80" placeholder="请输入批号" /></el-form-item></el-col>
        <el-col :span="12"><el-form-item label="供应商" required><el-select v-model="form.supplier_id" filterable remote :remote-method="q => searchOptions('suppliers',q)" placeholder="输入编码或名称搜索"><el-option v-for="item in options.suppliers" :key="item.id" :value="item.id" :label="`${item.code} · ${item.name}`" /></el-select></el-form-item></el-col></el-row>
        <el-row :gutter="20"><el-col :span="12"><el-form-item label="生产日期" required><el-date-picker v-model="form.production_date" value-format="YYYY-MM-DD" type="date" /></el-form-item></el-col><el-col :span="12"><el-form-item label="到期日期" required><el-date-picker v-model="form.expiry_date" value-format="YYYY-MM-DD" type="date" /></el-form-item></el-col></el-row>
      </template>
      <template v-if="outbound"><el-alert title="默认选择最早到期的可用批次；到期当天仍有效。可手动选择其他合法批次。" type="info" :closable="false" class="form-notice" />
        <el-form-item label="出库批次" required><el-select v-model="form.batch_id" placeholder="先选择原材料"><el-option v-for="(b,i) in candidates" :key="b.id" :value="b.id" :label="`${candidatePage === 1 && i === 0 ? '推荐 · ' : ''}${b.batch_no} · 到期 ${b.expiry_date} · 余额 ${b.remaining_quantity} ${b.unit} · ${b.location_name}`" /></el-select></el-form-item>
        <el-pagination v-if="candidateTotal > 20" v-model:current-page="candidatePage" :total="candidateTotal" :page-size="20" layout="prev, pager, next" @current-change="form.batch_id=null; loadBatches(false)" />
        <EmptyState v-if="form.material_id && !candidateTotal" title="暂无可用批次" description="请先入库，或核查批次有效期与相关档案状态。" />
      </template>
      <el-form-item label="备注"><el-input v-model="form.notes" maxlength="2000" placeholder="可选" /></el-form-item>
      <el-form-item><el-button native-type="submit" type="primary" size="large" :loading="saving">{{ receipt ? '确认入库' : '确认出库' }}</el-button></el-form-item>
    </el-form>
  </DataPanel>

  <el-alert v-if="writePage && !canWrite" title="当前账号仅有查看权限。" type="info" :closable="false" class="permission-notice" />

  <DataPanel :title="listTitle" description="支持按原料编码、名称或批号快速检索。">
    <FilterBar><el-form inline @submit.prevent="page=1; load()"><el-form-item label="搜索"><el-input v-model="query" maxlength="120" clearable placeholder="原料编码、名称；明细支持批号" @clear="page=1; load()" /></el-form-item><el-form-item><el-button :icon="Search" native-type="submit" type="primary">查询</el-button></el-form-item></el-form></FilterBar>
    <TableSkeleton v-if="loading && !rows.length" />
    <el-table v-else :data="rows" v-loading="loading" stripe row-key="id"><el-table-column v-for="[key,label] in columns" :key="key" :label="label" :min-width="key === 'created_at' || key === 'document_no' ? 200 : 130" show-overflow-tooltip><template #default="{row}"><StatusBadge v-if="key === 'status'" :tone="row.status === 'shelved' ? 'success' : row.status === 'partial' ? 'warning' : 'info'" :label="display(row,key)" /><span v-else :class="{ 'data-value': isQuantityColumn(key) }">{{ display(row,key) ?? '—' }}</span></template></el-table-column><el-table-column v-if="resource==='batches'" label="有效状态" width="110"><template #default="{row}"><StatusBadge :tone="row.expired ? 'danger' : 'success'" :label="row.expired ? '已过期' : '未过期'" /></template></el-table-column><template #empty><EmptyState title="暂无库存记录" description="当前查询条件下没有匹配的库存数据。" /></template></el-table>
    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[10,20,50,100]" layout="total, sizes, prev, pager, next" class="pagination" @current-change="load" @size-change="page=1; load()" />
  </DataPanel>
</template>

<style scoped>
.quantity-input { display: grid; width: 100%; grid-template-columns: minmax(0, 1fr) 104px; }
.quantity-input :deep(.el-input__wrapper) { border-radius: 10px 0 0 10px; }
.quantity-input :deep(.el-select__wrapper) { border-radius: 0 10px 10px 0; }
.form-notice { margin-bottom: 16px; }
.permission-notice { margin-bottom: 18px; }
</style>
