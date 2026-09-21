<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { auth } from '../auth'

const route = useRoute()
const resource = computed(() => route.meta.resource)
const isStock = computed(() => resource.value === 'finished-stock')
const isReceipt = computed(() => resource.value === 'finished-receipts')
const canWrite = computed(() => ['admin', 'warehouse'].includes(auth.user?.role))
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: auth.user?.business_timezone || 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())
const form = reactive({ product_id: null, quantity: '', business_date: today(), business_type: 'normal', reference_no: '', notes: '' })
const products = ref([]), selected = ref(null), selecting = ref(false), saving = ref(false)
const error = ref(''), success = ref(null), rows = ref([]), loading = ref(false), total = ref(0)
const q = ref(''), page = ref(1), pageSize = ref(20), stockTab = ref('balance')
const detail = ref(null), detailOpen = ref(false)
let listGeneration = 0, optionGeneration = 0, selectionGeneration = 0, lastPayload = '', requestId = ''
// 三类序号分别隔离列表、下拉选项和库存详情的异步响应，防止旧请求覆盖新选择。
const endpoint = computed(() => isStock.value && stockTab.value === 'ledger' ? 'finished-transactions' : resource.value)
const balanceView = computed(() => endpoint.value === 'finished-stock')
const columns = computed(() => balanceView.value ? [
  ['code', '成品编码'], ['name', '成品名称'], ['unit', '单位'], ['quantity', '当前库存'],
  ['last_receipt_at', '最近入库时间'], ['last_outbound_at', '最近出库时间'],
] : [
  ['business_date', '业务日期'], ['product_name', '成品名称'], ['business_type', '业务类型'], ['quantity', '数量'], ['unit', '单位'],
  ['change_quantity', '变动量'], ['after_quantity', '变动后库存'], ['reference_no', '关联单据'], ['operator_name', '操作人员'],
])
const detailFields = [['document_no', '库存单据号'], ['created_at', '记账时间'], ['business_date', '业务日期'],
  ['product_code', '成品编码'], ['product_name', '成品名称'], ['kind', '操作类型'], ['business_type', '业务类型'],
  ['quantity', '数量'], ['unit', '单位'], ['before_quantity', '变动前'], ['change_quantity', '变动量'], ['after_quantity', '变动后'],
  ['reference_no', '关联单据'], ['operator_name', '操作人员'], ['notes', '备注']]
function display(row, key) {
  if (row[key] == null || row[key] === '') return '—'
  if (key.endsWith('_at')) return new Date(row[key]).toLocaleString('zh-CN', { timeZone: auth.user?.business_timezone || 'Asia/Shanghai', hour12: false })
  if (key === 'kind') return row[key] === 'finished_receipt' ? '成品入库' : '成品出库'
  if (key === 'business_type') return row[key] === 'production' ? '生产入库' : row.kind === 'finished_receipt' ? '普通入库' : '普通出库'
  return row[key]
}
async function searchProducts(query = '') {
  const ticket = ++optionGeneration
  try {
    const result = await api.get('/products', { params: { q: query, is_active: true, page_size: 100 } })
    if (ticket === optionGeneration) products.value = result.data.items
  } catch (e) { if (ticket === optionGeneration) error.value = e.message }
}
async function selectProduct() {
  const ticket = ++selectionGeneration
  selected.value = null; selecting.value = true
  try {
    if (!form.product_id) return
    const result = await api.get('/finished-stock/' + form.product_id)
    if (ticket === selectionGeneration) selected.value = result.data
  } catch (e) { if (ticket === selectionGeneration) error.value = e.message }
  finally { if (ticket === selectionGeneration) selecting.value = false }
}
async function load() {
  const ticket = ++listGeneration
  loading.value = true
  try {
    const result = await api.get('/' + endpoint.value, { params: { q: q.value, page: page.value, page_size: pageSize.value } })
    if (ticket === listGeneration) { rows.value = result.data.items; total.value = result.data.total }
  } catch (e) { if (ticket === listGeneration) { error.value = e.message; rows.value = []; total.value = 0 } }
  finally { if (ticket === listGeneration) loading.value = false }
}
async function openDetail(row) {
  try {
    const path = row.kind === 'finished_receipt' ? 'finished-receipts' : 'finished-outbounds'
    const result = await api.get(`/${path}/${row.id}`)
    detail.value = result.data; detailOpen.value = true
  } catch (e) { error.value = e.message }
}
async function submit() {
  if (saving.value || selecting.value) return
  error.value = ''; success.value = null
  if (!selected.value || !form.business_date || !/^\d{1,14}(\.\d{1,6})?$/.test(form.quantity) || !/[1-9]/.test(form.quantity)) {
    error.value = '请选择成品和业务日期，数量必须大于零且最多六位小数'; return
  }
  const payload = { ...form, unit: selected.value.unit, reference_no: form.reference_no.trim() || null, notes: form.notes.trim() || null }
  // 成品单位始终取档案查询结果，页面不允许自行输入或猜测“盒”等固定单位。
  const signature = JSON.stringify(payload)
  // 同内容连接中断重试沿用 requestId，后端幂等校验确保库存至多变化一次。
  if (!requestId || signature !== lastPayload) { requestId = crypto.randomUUID(); lastPayload = signature }
  saving.value = true
  try {
    const result = await api.post('/' + resource.value, { ...payload, request_id: requestId })
    success.value = result.data; form.quantity = ''; requestId = ''; lastPayload = ''
    ElMessage.success('记账成功')
    await Promise.all([load(), selectProduct()])
  } catch (e) {
    error.value = e.message + (e.status ? '' : '；可保持原表单重试，刷新前请先核对历史记录。')
    await selectProduct()
  } finally { saving.value = false }
}
onBeforeRouteLeave(() => !saving.value)
onBeforeRouteUpdate(() => !saving.value)
watch(resource, async () => {
  listGeneration++; selectionGeneration++; optionGeneration++
  error.value = ''; success.value = null; detailOpen.value = false
  form.quantity = ''; form.product_id = null; selected.value = null; form.business_date = today()
  form.reference_no = ''; form.notes = ''; requestId = ''; lastPayload = ''
  q.value = ''; page.value = 1; stockTab.value = 'balance'; rows.value = []
  await Promise.all([load(), !isStock.value ? searchProducts() : Promise.resolve()])
}, { immediate: true })
</script>

<template>
  <div class="page-heading"><div><h1>{{ route.meta.title }}</h1><p>单位沿用成品档案，确认后库存与流水同步记账。</p></div><el-button :disabled="saving" @click="load(); !isStock && selectProduct()">刷新</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="notice" />
  <el-alert v-if="success" :title="`记账成功：${success.document_no}，记账后库存 ${success.after_quantity} ${success.unit}`" type="success" :closable="false" class="notice" />
  <el-card v-if="!isStock && canWrite" shadow="never" class="notice">
    <el-form label-width="100px" :disabled="saving" @submit.prevent="submit">
      <el-row :gutter="24"><el-col :span="12"><el-form-item label="成品" required><el-select v-model="form.product_id" remote filterable :remote-method="searchProducts" placeholder="输入成品编码或名称搜索" @change="selectProduct"><el-option v-for="item in products" :key="item.id" :value="item.id" :label="`${item.code} · ${item.name}`" /></el-select></el-form-item></el-col>
        <el-col :span="12"><el-form-item label="当前库存"><span>{{ selecting ? '查询中…' : selected ? `${selected.quantity} ${selected.unit}` : '请先选择成品' }}</span></el-form-item></el-col></el-row>
      <el-row :gutter="24"><el-col :span="12"><el-form-item :label="isReceipt ? '入库数量' : '出库数量'" required><el-input v-model="form.quantity" inputmode="decimal" maxlength="21" placeholder="大于零，最多六位小数"><template #append>{{ selected?.unit || '单位' }}</template></el-input></el-form-item></el-col>
        <el-col :span="12"><el-form-item :label="isReceipt ? '入库日期' : '出库日期'" required><el-date-picker v-model="form.business_date" type="date" value-format="YYYY-MM-DD" /></el-form-item></el-col></el-row>
      <el-row :gutter="24"><el-col :span="12"><el-form-item :label="isReceipt ? '入库类型' : '出库类型'"><el-select v-model="form.business_type"><el-option value="normal" :label="isReceipt ? '普通入库' : '普通出库'" /><el-option v-if="isReceipt" value="production" label="生产入库（请从生产单完成）" disabled /></el-select></el-form-item></el-col>
        <el-col :span="12"><el-form-item label="关联单据"><el-input v-model="form.reference_no" maxlength="120" placeholder="可选，填写外部单据编号" /></el-form-item></el-col></el-row>
      <el-form-item label="备注"><el-input v-model="form.notes" maxlength="2000" type="textarea" :rows="2" /></el-form-item>
      <el-form-item><el-button type="primary" native-type="submit" :loading="saving" :disabled="selecting || !selected">{{ isReceipt ? '确认入库' : '确认出库' }}</el-button></el-form-item>
    </el-form>
  </el-card>
  <el-alert v-if="!isStock && !canWrite" title="当前角色仅可查看记录，库存操作由仓库人员或管理员执行。" type="info" :closable="false" class="notice" />
  <el-card shadow="never">
    <el-tabs v-if="isStock" v-model="stockTab" @tab-change="page=1; load()"><el-tab-pane label="当前库存" name="balance" /><el-tab-pane label="成品库存流水" name="ledger" /></el-tabs>
    <el-form inline @submit.prevent="page=1; load()"><el-form-item label="搜索"><el-input v-model="q" maxlength="120" clearable placeholder="成品名称或编码" @clear="page=1; load()" /></el-form-item><el-button type="primary" native-type="submit">查询</el-button></el-form>
    <el-table v-loading="loading" :data="rows" stripe row-key="id" empty-text="暂无记录"><el-table-column v-for="[key,label] in columns" :key="key" :label="label" :min-width="key.endsWith('_at') ? 175 : 120" show-overflow-tooltip><template #default="{row}">{{ display(row,key) }}</template></el-table-column><el-table-column v-if="!balanceView" label="详情" fixed="right" width="80"><template #default="{row}"><el-button type="primary" link @click="openDetail(row)">查看</el-button></template></el-table-column></el-table>
    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[10,20,50,100]" layout="total, sizes, prev, pager, next" class="pagination" @current-change="load" @size-change="page=1; load()" />
  </el-card>
  <el-dialog v-model="detailOpen" title="成品库存单据详情" width="650px"><el-descriptions v-if="detail" :column="1" border><el-descriptions-item v-for="[key,label] in detailFields" :key="key" :label="label">{{ display(detail,key) }}</el-descriptions-item></el-descriptions></el-dialog>
</template>

<style scoped>
.notice { margin-bottom: 20px; }
</style>
