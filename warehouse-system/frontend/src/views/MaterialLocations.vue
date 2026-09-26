<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import { api } from '../api'
import { auth } from '../auth'
import { canOperate } from '../permissions'
import { quantityUnits, quantityText } from '../quantity'
import { postingRequest } from '../postingRequest'
import PageHeader from '../components/ui/PageHeader.vue'
import DataPanel from '../components/ui/DataPanel.vue'
import FilterBar from '../components/ui/FilterBar.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
import TableSkeleton from '../components/ui/TableSkeleton.vue'

const route = useRoute()
const mode = computed(() => route.meta.resource)
const canWrite = computed(() => canOperate(auth.user?.role, 'material.inventory.write'))
const rows = ref([]), total = ref(0), page = ref(1), pageSize = ref(20)
const loading = ref(false), error = ref(''), saving = ref(false)
const locations = ref([]), materials = ref([]), stockOptions = ref([])
const filters = reactive({ q: '', location_id: null, material_id: null, batch_no: '' })
const selected = ref(null), actionVisible = ref(false), actionType = ref(''), detail = ref(null), detailVisible = ref(false), detailPage = ref(1)
const action = reactive({ source_batch_id: null, location_id: null, quantity: '', actual_quantity: '', change_quantity: '', reason: '', notes: '', requestKey: '', requestContent: '' })
let generation = 0

function dateTime(value) { return value ? new Date(value).toLocaleString('zh-CN', { timeZone: auth.user?.business_timezone || 'Asia/Shanghai', hour12: false }) : '—' }
function positive(value) { try { return quantityUnits(value) > 0n } catch { return false } }
function nonnegative(value) { try { return quantityUnits(value) >= 0n } catch { return false } }
function statusText(value) { return ({ pending: '待上架', partial: '部分上架', shelved: '已上架' })[value] || value }
function kindText(value) { return ({ material_receipt: '原材料入库', material_outbound: '原材料出库', putaway: '上架', transfer: '移库', issue: '车间领料', supplement: '补料', return: '退料', count_gain: '盘盈', count_loss: '盘亏', adjustment: '库存调整' })[value] || value }
function formatted(value, unit) { return `${value} ${unit || ''}` }

async function loadOptions() {
  try {
    const [l, m] = await Promise.all([
      api.get('/locations', { params: { is_active: true, page_size: 100 } }),
      api.get('/materials', { params: { is_active: true, page_size: 100 } }),
    ])
    locations.value = l.data.items; materials.value = m.data.items
  } catch (e) { error.value = e.message }
}
async function loadStockOptions() {
  try {
    const result = await api.get('/material-location-stock', { params: { page_size: 100 } })
    stockOptions.value = result.data.items
  } catch (e) { error.value = e.message }
}
async function load() {
  const ticket = ++generation
  loading.value = true; error.value = ''
  try {
    const common = { page: page.value, page_size: pageSize.value }
    let path, params
    if (mode.value === 'material-putaways') {
      path = '/material-receipts'; params = { ...common, status: 'open', q: filters.q }
    } else if (mode.value === 'material-location-stock') {
      path = '/material-location-stock'; params = { ...common, location_id: filters.location_id || undefined,
        material_id: filters.material_id || undefined, batch_no: filters.batch_no || undefined }
    } else if (mode.value === 'material-transfers') {
      path = '/material-transfers'; params = { ...common, kind: 'transfer' }
    } else {
      path = '/material-counts'; params = common
    }
    const result = await api.get(path, { params })
    if (ticket === generation) { rows.value = result.data.items; total.value = result.data.total }
  } catch (e) { if (ticket === generation) { rows.value = []; total.value = 0; error.value = e.message } }
  finally { if (ticket === generation) loading.value = false }
}
function search() { page.value = 1; load() }
async function openAction(type, row = null) {
  actionType.value = type; selected.value = row
  Object.assign(action, { source_batch_id: null, location_id: null, quantity: '', actual_quantity: '', change_quantity: '', reason: '', notes: '', requestKey: '', requestContent: '' })
  if (type !== 'putaway') await loadStockOptions()
  if (type === 'count' && row) action.actual_quantity = row.remaining_quantity
  actionVisible.value = true; error.value = ''
}
function selectedStock() { return selected.value || stockOptions.value.find(row => row.id === action.source_batch_id) }
async function submitAction() {
  const row = selectedStock()
  if (!row) { error.value = '请选择库存记录'; return }
  let path, payload, confirmation
  if (actionType.value === 'putaway') {
    if (!action.location_id || !positive(action.quantity) || quantityUnits(action.quantity) > quantityUnits(row.pending_quantity)) {
      error.value = '请选择目标库位；上架数量必须大于零且不能超过待上架数量'; return
    }
    path = '/material-putaways'
    payload = { receipt_movement_id: row.receipt_movement_id, location_id: action.location_id,
      quantity: action.quantity, notes: action.notes || null }
    confirmation = `确认将 ${row.material_name} / ${row.batch_no} 的 ${action.quantity} ${row.unit} 上架？`
  } else if (actionType.value === 'transfer') {
    if (!action.location_id || action.location_id === row.location_id || !positive(action.quantity) || quantityUnits(action.quantity) > quantityUnits(row.remaining_quantity)) {
      error.value = '请选择不同的目标库位，移库数量必须大于零且不能超过原库位库存'; return
    }
    path = '/material-transfers'
    payload = { source_batch_id: row.id, target_location_id: action.location_id,
      quantity: action.quantity, notes: action.notes || null }
    confirmation = `确认将 ${row.material_name} / ${row.batch_no} 从 ${row.location_name} 移出 ${action.quantity} ${row.unit}？`
  } else if (actionType.value === 'count') {
    if (!nonnegative(action.actual_quantity)) { error.value = '实际盘点数量必须为非负数，最多六位小数'; return }
    path = '/material-counts'
    payload = { batch_id: row.id, book_quantity: row.remaining_quantity,
      actual_quantity: action.actual_quantity, reason: action.reason || null }
    const difference = quantityText(quantityUnits(action.actual_quantity) - quantityUnits(row.remaining_quantity))
    confirmation = `确认 ${row.location_name} / ${row.material_name} / ${row.batch_no} 实盘 ${action.actual_quantity} ${row.unit}，差异 ${difference} ${row.unit}？`
  } else {
    let change
    try { change = quantityUnits(action.change_quantity) } catch { error.value = '调整变化量必须是最多六位小数的十进制数'; return }
    if (change === 0n || quantityUnits(row.remaining_quantity) + change < 0n || !action.reason.trim()) {
      error.value = '调整量不能为零或造成负库存，并且必须填写原因'; return
    }
    path = '/material-adjustments'
    payload = { batch_id: row.id, book_quantity: row.remaining_quantity,
      change_quantity: action.change_quantity, reason: action.reason.trim() }
    confirmation = `确认调整 ${row.location_name} / ${row.material_name} / ${row.batch_no} ${action.change_quantity} ${row.unit}？`
  }
  try { await ElMessageBox.confirm(confirmation, '确认库存操作', { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' }) }
  catch { return }
  saving.value = true; error.value = ''
  try {
    await api.post(path, postingRequest(action, payload, () => crypto.randomUUID()))
    ElMessage.success('操作成功'); actionVisible.value = false
    await load(); if (actionType.value !== 'putaway') await loadStockOptions()
  } catch (e) { error.value = e.message + '；如连接中断，保持原输入重试。' }
  finally { saving.value = false }
}
async function showDetail(row) {
  try { detail.value = (await api.get(`/material-location-stock/${row.id}`)).data; detailPage.value = 1; detailVisible.value = true }
  catch (e) { error.value = e.message }
}
async function loadDetailLedger() {
  if (!detail.value) return
  try { detail.value.transactions = (await api.get('/material-transactions', {
    params: { batch_id: detail.value.id, page: detailPage.value, page_size: 20 },
  })).data }
  catch (e) { error.value = e.message }
}
watch(mode, async () => {
  generation++; page.value = 1; rows.value = []; total.value = 0; error.value = ''
  Object.assign(filters, { q: '', location_id: null, material_id: null, batch_no: '' })
  await Promise.all([load(), loadOptions()])
}, { immediate: true })
</script>

<template>
  <PageHeader eyebrow="原材料库位管理" :title="route.meta.title" description="按批号追踪待上架、实际库位余额和每次库存变化。">
    <template #actions><el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button></template>
  </PageHeader>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="location-feedback" />

  <DataPanel v-if="mode === 'material-transfers' && canWrite" title="原材料移库" description="选择原库位批次和目标库位；移库不会改变原材料总库存。" kind="action">
    <el-form inline @submit.prevent="openAction('transfer')"><el-form-item><el-button type="primary" @click="openAction('transfer')">发起移库</el-button></el-form-item></el-form>
  </DataPanel>
  <DataPanel v-if="mode === 'material-counts' && canWrite" title="库位库存盘点" description="核对账面数量，确认后生成盘点记录和差异流水。" kind="action">
    <el-button type="primary" @click="openAction('count')">发起盘点</el-button>
    <el-button @click="openAction('adjustment')">库存调整</el-button>
  </DataPanel>
  <el-alert v-if="!canWrite && mode !== 'material-location-stock'" title="当前账号仅有查看权限。" type="info" :closable="false" class="location-feedback" />

  <DataPanel :title="mode === 'material-putaways' ? '待上架入库单' : mode === 'material-location-stock' ? '库位库存' : mode === 'material-transfers' ? '移库记录' : '盘点记录'">
    <FilterBar v-if="mode === 'material-putaways'"><el-form inline @submit.prevent="search"><el-form-item label="搜索"><el-input v-model="filters.q" placeholder="原料、批号或入库单号" clearable @clear="search" /></el-form-item><el-form-item><el-button type="primary" :icon="Search" native-type="submit">查询</el-button></el-form-item></el-form></FilterBar>
    <FilterBar v-if="mode === 'material-location-stock'"><el-form inline @submit.prevent="search"><el-form-item label="库位"><el-select v-model="filters.location_id" clearable filterable placeholder="全部库位" @change="search"><el-option v-for="item in locations" :key="item.id" :value="item.id" :label="item.name" /></el-select></el-form-item><el-form-item label="原材料"><el-select v-model="filters.material_id" clearable filterable placeholder="全部原材料" @change="search"><el-option v-for="item in materials" :key="item.id" :value="item.id" :label="item.name" /></el-select></el-form-item><el-form-item label="批号"><el-input v-model="filters.batch_no" clearable placeholder="输入批号" @clear="search" /></el-form-item><el-form-item><el-button type="primary" :icon="Search" native-type="submit">查询</el-button></el-form-item></el-form></FilterBar>
    <TableSkeleton v-if="loading && !rows.length" />
    <el-table v-else-if="mode === 'material-putaways'" :data="rows" v-loading="loading" stripe row-key="id">
      <el-table-column prop="document_no" label="入库单" min-width="190" /><el-table-column prop="material_name" label="原材料" min-width="145" /><el-table-column prop="batch_no" label="批号" min-width="130" /><el-table-column prop="supplier_name" label="供应商" min-width="130" /><el-table-column label="入库 / 已上架 / 待上架" min-width="220"><template #default="{row}">{{ row.received_quantity }} / {{ row.shelved_quantity }} / <strong>{{ row.pending_quantity }} {{ row.unit }}</strong></template></el-table-column><el-table-column label="状态" width="110"><template #default="{row}"><StatusBadge :tone="row.status === 'partial' ? 'warning' : 'info'" :label="statusText(row.status)" /></template></el-table-column><el-table-column label="操作" width="100"><template #default="{row}"><el-button v-if="canWrite" link type="primary" @click="openAction('putaway',row)">上架</el-button></template></el-table-column>
      <template #empty><EmptyState title="暂无待上架入库单" description="原材料入库后会在这里显示待上架数量。" /></template>
    </el-table>
    <el-table v-else-if="mode === 'material-location-stock'" :data="rows" v-loading="loading" stripe row-key="id">
      <el-table-column prop="location_name" label="库位名称" min-width="145" /><el-table-column prop="material_name" label="原材料" min-width="145" /><el-table-column prop="material_code" label="原料编码" min-width="120" /><el-table-column prop="batch_no" label="批号" min-width="130" /><el-table-column label="当前数量" min-width="130"><template #default="{row}"><strong>{{ formatted(row.remaining_quantity,row.unit) }}</strong></template></el-table-column><el-table-column prop="production_date" label="生产日期" min-width="125" /><el-table-column prop="expiry_date" label="有效期" min-width="125" /><el-table-column label="到期状态" width="105"><template #default="{row}"><StatusBadge :tone="row.expired ? 'danger' : 'success'" :label="row.expired ? '已过期' : '未过期'" /></template></el-table-column><el-table-column label="操作" width="170"><template #default="{row}"><el-button link type="primary" @click="showDetail(row)">详情 / 流水</el-button><el-button v-if="canWrite" link type="warning" @click="openAction('transfer',row)">移库</el-button><el-button v-if="canWrite" link type="danger" @click="openAction('count',row)">盘点</el-button></template></el-table-column>
      <template #empty><EmptyState title="暂无库位库存" description="可调整库位、原材料或批号筛选。" /></template>
    </el-table>
    <el-table v-else-if="mode === 'material-transfers'" :data="rows" v-loading="loading" stripe row-key="id"><el-table-column label="时间" min-width="175"><template #default="{row}">{{ dateTime(row.created_at) }}</template></el-table-column><el-table-column prop="document_no" label="移库单" min-width="185" /><el-table-column prop="material_name" label="原材料" min-width="140" /><el-table-column prop="batch_no" label="批号" min-width="130" /><el-table-column prop="source_location_name" label="原库位" min-width="130" /><el-table-column prop="target_location_name" label="目标库位" min-width="130" /><el-table-column label="数量" min-width="120"><template #default="{row}">{{ formatted(row.quantity,row.unit) }}</template></el-table-column><el-table-column prop="operator_name" label="操作人" min-width="110" /><template #empty><EmptyState title="暂无移库记录" /></template></el-table>
    <el-table v-else :data="rows" v-loading="loading" stripe row-key="id"><el-table-column label="盘点时间" min-width="175"><template #default="{row}">{{ dateTime(row.counted_at) }}</template></el-table-column><el-table-column prop="counted_by_name" label="盘点人" min-width="100" /><el-table-column prop="location_name" label="库位" min-width="130" /><el-table-column prop="material_name" label="原材料" min-width="135" /><el-table-column prop="batch_no" label="批号" min-width="120" /><el-table-column label="账面 / 实盘 / 差异" min-width="230"><template #default="{row}">{{ row.book_quantity }} / {{ row.actual_quantity }} / <strong>{{ row.difference }} {{ row.unit }}</strong></template></el-table-column><el-table-column prop="reason" label="原因 / 备注" min-width="170" show-overflow-tooltip /><template #empty><EmptyState title="暂无盘点记录" /></template></el-table>
    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[10,20,50,100]" layout="total, sizes, prev, pager, next" class="pagination" @current-change="load" @size-change="search" />
  </DataPanel>

  <el-dialog v-model="actionVisible" :title="actionType === 'putaway' ? '原材料上架' : actionType === 'transfer' ? '原材料移库' : actionType === 'count' ? '库位库存盘点' : '库存调整'" width="min(620px, 94vw)" :close-on-click-modal="false">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="location-feedback" />
    <el-descriptions v-if="selectedStock()" :column="2" border class="action-summary"><el-descriptions-item label="原材料">{{ selectedStock().material_name }}</el-descriptions-item><el-descriptions-item label="批号">{{ selectedStock().batch_no }}</el-descriptions-item><el-descriptions-item :label="actionType === 'putaway' ? '待上架' : '账面库存'">{{ formatted(actionType === 'putaway' ? selectedStock().pending_quantity : selectedStock().remaining_quantity, selectedStock().unit) }}</el-descriptions-item><el-descriptions-item v-if="actionType !== 'putaway'" label="原库位">{{ selectedStock().location_name }}</el-descriptions-item><el-descriptions-item v-if="actionType === 'putaway'" label="供应商">{{ selectedStock().supplier_name }}</el-descriptions-item><el-descriptions-item v-if="actionType === 'putaway'" label="生产日期">{{ selectedStock().production_date }}</el-descriptions-item><el-descriptions-item v-if="actionType === 'putaway'" label="有效期">{{ selectedStock().expiry_date }}</el-descriptions-item></el-descriptions>
    <el-form label-width="105px" :disabled="saving" @submit.prevent="submitAction">
      <el-form-item v-if="actionType !== 'putaway' && !selected" label="库位批次" required><el-select v-model="action.source_batch_id" filterable placeholder="选择库位、原材料和批号"><el-option v-for="item in stockOptions" :key="item.id" :value="item.id" :label="`${item.location_name} · ${item.material_name} · ${item.batch_no} · ${item.remaining_quantity} ${item.unit}`" /></el-select></el-form-item>
      <el-form-item v-if="['putaway','transfer'].includes(actionType)" label="目标库位" required><el-select v-model="action.location_id" filterable placeholder="选择目标库位"><el-option v-for="item in locations.filter(loc => loc.id !== selectedStock()?.location_id)" :key="item.id" :value="item.id" :label="item.name" /></el-select></el-form-item>
      <el-form-item v-if="['putaway','transfer'].includes(actionType)" label="本次数量" required><el-input v-model="action.quantity" inputmode="decimal" maxlength="21"><template #append>{{ selectedStock()?.unit }}</template></el-input></el-form-item>
      <el-form-item v-if="actionType === 'count'" label="实际数量" required><el-input v-model="action.actual_quantity" inputmode="decimal" maxlength="21"><template #append>{{ selectedStock()?.unit }}</template></el-input></el-form-item>
      <el-form-item v-if="actionType === 'adjustment'" label="调整变化量" required><el-input v-model="action.change_quantity" inputmode="decimal" maxlength="22" placeholder="正数增加，负数减少"><template #append>{{ selectedStock()?.unit }}</template></el-input></el-form-item>
      <el-form-item :label="['count','adjustment'].includes(actionType) ? '原因 / 备注' : '备注'" :required="actionType === 'adjustment'"><el-input v-if="['count','adjustment'].includes(actionType)" v-model="action.reason" type="textarea" :rows="2" maxlength="2000" /><el-input v-else v-model="action.notes" type="textarea" :rows="2" maxlength="2000" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="actionVisible=false" :disabled="saving">取消</el-button><el-button type="primary" :loading="saving" @click="submitAction">确认{{ actionType === 'putaway' ? '上架' : actionType === 'transfer' ? '移库' : actionType === 'count' ? '盘点' : '调整' }}</el-button></template>
  </el-dialog>

  <el-dialog v-model="detailVisible" title="库位库存详情与流水" width="min(1000px, 96vw)">
    <el-descriptions v-if="detail" :column="3" border><el-descriptions-item label="库位">{{ detail.location_name }}</el-descriptions-item><el-descriptions-item label="原材料">{{ detail.material_name }}</el-descriptions-item><el-descriptions-item label="批号">{{ detail.batch_no }}</el-descriptions-item><el-descriptions-item label="当前数量">{{ formatted(detail.remaining_quantity,detail.unit) }}</el-descriptions-item><el-descriptions-item label="生产日期">{{ detail.production_date }}</el-descriptions-item><el-descriptions-item label="有效期">{{ detail.expiry_date }}</el-descriptions-item></el-descriptions>
    <el-table v-if="detail" :data="detail.transactions.items.slice(0,20)" stripe class="detail-ledger"><el-table-column label="时间" min-width="170"><template #default="{row}">{{ dateTime(row.created_at) }}</template></el-table-column><el-table-column prop="document_no" label="关联单据" min-width="175" /><el-table-column label="业务类型" min-width="125"><template #default="{row}">{{ kindText(row.kind) }}</template></el-table-column><el-table-column label="数量变化" min-width="115"><template #default="{row}">{{ formatted(row.change_quantity,row.unit) }}</template></el-table-column><el-table-column prop="source_location_name" label="原库位" min-width="120" /><el-table-column prop="target_location_name" label="目标库位" min-width="120" /><el-table-column prop="operator_name" label="操作人" min-width="105" /></el-table>
    <el-pagination v-if="detail && detail.transactions.total > 20" v-model:current-page="detailPage" :total="detail.transactions.total" :page-size="20" layout="total, prev, pager, next" @current-change="loadDetailLedger" />
    <template #footer><el-button @click="detailVisible=false">关闭</el-button></template>
  </el-dialog>
</template>

<style scoped>
.location-feedback { margin-bottom: 16px; }
.action-summary { margin: 14px 0 20px; }
.detail-ledger { margin-top: 20px; }
</style>
