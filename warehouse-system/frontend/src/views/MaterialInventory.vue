<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { auth } from '../auth'

const route = useRoute()
const resource = computed(() => route.meta.resource)
const receipt = computed(() => resource.value === 'material-receipts')
const outbound = computed(() => resource.value === 'material-outbounds')
const writePage = computed(() => receipt.value || outbound.value)
const canWrite = computed(() => ['admin', 'warehouse'].includes(auth.user?.role))
const form = reactive({ material_id: null, batch_no: '', batch_id: null, quantity: '', unit: 'kg', production_date: '', expiry_date: '', supplier_id: null, location_id: null, notes: '' })
const options = reactive({ materials: [], suppliers: [], locations: [] })
const candidates = ref([]), candidatePage = ref(1), candidateTotal = ref(0)
const saving = ref(false), error = ref(''), success = ref(null), rows = ref([]), total = ref(0), loading = ref(false)
const query = ref(''), page = ref(1), pageSize = ref(20)
let requestId = '', lastPayload = '', generation = 0, batchGeneration = 0
const endpoint = computed(() => writePage.value ? 'material-transactions' : resource.value === 'batches' ? 'material-batches' : resource.value)
const columns = computed(() => resource.value === 'material-stock' ? [
  ['code', '原料编码'], ['name', '原料名称'], ['remaining_quantity', '总库存（含过期）'], ['unit', '单位'],
] : resource.value === 'batches' ? [
  ['material_code', '原料编码'], ['material_name', '原料名称'], ['batch_no', '批号'], ['received_quantity', '累计入库'],
  ['remaining_quantity', '剩余库存'], ['unit', '单位'], ['production_date', '生产日期'], ['expiry_date', '到期日期'],
  ['supplier_name', '供应商'], ['location_name', '库位'],
] : [
  ['created_at', '记账时间'], ['document_no', '单据号'], ['material_name', '原料'], ['batch_no', '批号'], ['kind', '类型'],
  ['input_quantity', '输入数量'], ['input_unit', '输入单位'], ['before_quantity', '变动前'], ['change_quantity', '变动量'],
  ['after_quantity', '变动后'], ['unit', '库存单位'], ['operator_name', '操作人'], ['notes', '备注'],
])
function display(row, key) {
  if (key === 'kind') return row[key] === 'material_receipt' ? '原料入库' : '原料出库'
  if (key === 'created_at') return new Date(row[key]).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
  return row[key]
}
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
    if (writePage.value) params.kind = receipt.value ? 'material_receipt' : 'material_outbound'
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
  if (receipt.value && (!form.batch_no.trim() || !form.production_date || !form.expiry_date || !form.supplier_id || !form.location_id)) {
    error.value = '请完整填写批号、生产日期、到期日期、供应商和库位'; return
  }
  if (outbound.value && !form.batch_id) { error.value = '请选择有库存且未过期的批次'; return }
  const payload = { material_id: form.material_id, quantity: form.quantity, unit: form.unit, notes: form.notes || null }
  if (receipt.value) Object.assign(payload, { batch_no: form.batch_no.trim(), production_date: form.production_date, expiry_date: form.expiry_date, supplier_id: form.supplier_id, location_id: form.location_id })
  else payload.batch_id = form.batch_id
  const signature = JSON.stringify(payload)
  if (signature !== lastPayload || !requestId) { requestId = crypto.randomUUID(); lastPayload = signature }
  saving.value = true
  try {
    const result = await api.post('/' + resource.value, { ...payload, request_id: requestId })
    success.value = result.data; form.quantity = ''; requestId = ''; lastPayload = ''
    ElMessage.success('记账成功'); await load()
    if (outbound.value) { candidatePage.value = 1; await loadBatches(true) }
  } catch (e) { error.value = e.message + '；若连接中断，可保持原输入重试。' }
  finally { saving.value = false }
}
watch(resource, async () => {
  generation++; batchGeneration++; page.value = 1; query.value = ''; error.value = ''; success.value = null; rows.value = []
  form.quantity = ''; form.batch_id = null; requestId = ''; lastPayload = ''; candidatePage.value = 1
  await load()
  if (writePage.value) {
    await Promise.all(['materials', 'suppliers', 'locations'].map(name => searchOptions(name)))
    if (outbound.value) await loadBatches(true)
  }
}, { immediate: true })
</script>

<template>
  <div class="page-heading"><div><h1>{{ route.meta.title }}</h1><p>批次记账，数量与流水同步保存。所有数量均以原料库存单位汇总。</p></div><el-button @click="error = ''; load()">刷新</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon style="margin-bottom:16px" />
  <el-alert v-if="success" type="success" :closable="false" :title="`已记账：${success.document_no}，批次余额 ${success.after_quantity} ${success.unit}`" style="margin-bottom:16px" />
  <el-card v-if="writePage && canWrite" shadow="never" style="margin-bottom:20px">
    <el-form label-width="105px" :disabled="saving" @submit.prevent="submit">
      <el-row :gutter="24"><el-col :span="12"><el-form-item label="原材料" required><el-select v-model="form.material_id" filterable remote :remote-method="q => searchOptions('materials', q)" placeholder="输入编码或名称搜索" @change="materialChanged"><el-option v-for="item in options.materials" :key="item.id" :value="item.id" :label="`${item.code} · ${item.name} (${item.unit})`" /></el-select></el-form-item></el-col>
        <el-col :span="12"><el-form-item label="数量 / 单位" required><el-input v-model="form.quantity" inputmode="decimal" maxlength="21" style="width:65%" placeholder="如 10.500001" /><el-select v-model="form.unit" style="width:35%"><el-option v-for="u in ['kg','g','L','mL','个']" :key="u" :value="u" :label="u" /></el-select></el-form-item></el-col></el-row>
      <template v-if="receipt"><el-row :gutter="24"><el-col :span="12"><el-form-item label="批号" required><el-input v-model="form.batch_no" maxlength="80" /></el-form-item></el-col>
        <el-col :span="12"><el-form-item label="供应商" required><el-select v-model="form.supplier_id" filterable remote :remote-method="q => searchOptions('suppliers',q)" placeholder="输入编码或名称搜索"><el-option v-for="item in options.suppliers" :key="item.id" :value="item.id" :label="`${item.code} · ${item.name}`" /></el-select></el-form-item></el-col></el-row>
        <el-row :gutter="24"><el-col :span="12"><el-form-item label="生产日期" required><el-date-picker v-model="form.production_date" value-format="YYYY-MM-DD" type="date" /></el-form-item></el-col><el-col :span="12"><el-form-item label="到期日期" required><el-date-picker v-model="form.expiry_date" value-format="YYYY-MM-DD" type="date" /></el-form-item></el-col></el-row>
        <el-form-item label="库位" required><el-select v-model="form.location_id" filterable remote :remote-method="q => searchOptions('locations',q)" placeholder="输入编码或名称搜索"><el-option v-for="item in options.locations" :key="item.id" :value="item.id" :label="`${item.code} · ${item.name}`" /></el-select></el-form-item>
      </template>
      <template v-if="outbound"><el-alert title="默认选择最早到期的可用批次；到期当天仍有效。可手动选择其他合法批次。" type="info" :closable="false" style="margin-bottom:16px" />
        <el-form-item label="出库批次" required><el-select v-model="form.batch_id" placeholder="先选择原材料"><el-option v-for="(b,i) in candidates" :key="b.id" :value="b.id" :label="`${candidatePage === 1 && i === 0 ? '推荐 · ' : ''}${b.batch_no} · 到期 ${b.expiry_date} · 余额 ${b.remaining_quantity} ${b.unit} · ${b.location_name}`" /></el-select></el-form-item>
        <el-pagination v-if="candidateTotal > 20" v-model:current-page="candidatePage" :total="candidateTotal" :page-size="20" layout="prev, pager, next" @current-change="form.batch_id=null; loadBatches(false)" />
        <p v-if="form.material_id && !candidateTotal">暂无可用批次，请先入库或核查档案状态。</p>
      </template>
      <el-form-item label="备注"><el-input v-model="form.notes" maxlength="2000" /></el-form-item>
      <el-form-item><el-button native-type="submit" type="primary" :loading="saving">{{ receipt ? '确认入库' : '确认出库' }}</el-button></el-form-item>
    </el-form>
  </el-card>
  <el-alert v-if="writePage && !canWrite" title="当前角色可查看记录，实际库存操作由仓库人员或管理员执行。" type="info" :closable="false" />
  <el-card shadow="never"><el-form inline @submit.prevent="page=1; load()"><el-form-item label="搜索"><el-input v-model="query" maxlength="120" clearable placeholder="原料编码、名称；明细支持批号" @clear="page=1; load()" /></el-form-item><el-button native-type="submit" type="primary">查询</el-button></el-form>
    <el-table :data="rows" v-loading="loading" stripe row-key="id" empty-text="暂无库存记录"><el-table-column v-for="[key,label] in columns" :key="key" :label="label" :min-width="key === 'created_at' || key === 'document_no' ? 200 : 130" show-overflow-tooltip><template #default="{row}">{{ display(row,key) }}</template></el-table-column><el-table-column v-if="resource==='batches'" label="有效状态" width="100"><template #default="{row}"><el-tag :type="row.expired?'danger':'success'">{{ row.expired?'已过期':'未过期' }}</el-tag></template></el-table-column></el-table>
    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[10,20,50,100]" layout="total, sizes, prev, pager, next" class="pagination" @current-change="load" @size-change="page=1; load()" />
  </el-card>
</template>
