<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { auth } from '../auth'

const admin = computed(() => auth.user?.role === 'admin')
const rows = ref([]), total = ref(0), page = ref(1), pageSize = ref(20), loading = ref(false)
const query = ref(''), status = ref(''), loadError = ref('')
const products = ref([]), materials = ref([]), units = ref([])
const dialog = ref(false), mode = ref('create'), saving = ref(false), formError = ref('')
const form = reactive({ id: null, product_id: null, product_unit: '', version: 1, status: 'draft', notes: '', items: [] })
const preview = reactive({ finished_product_id: null, product_unit: '', planned_quantity: '1000' })
const previewLoading = ref(false), previewError = ref(''), previewResult = ref(null)
const positiveDecimal = /^(?:0*[1-9]\d{0,13}|0*\.\d*[1-9]\d*|0*\d{1,14}\.\d*[1-9]\d*)$/
const statusText = { draft: '草稿', active: '启用', inactive: '停用', archived: '归档' }
const statusType = { draft: 'info', active: 'success', inactive: 'warning', archived: 'info' }

function mergeById(existing, incoming) {
  const result = new Map(existing.map(row => [row.id, row]))
  incoming.forEach(row => result.set(row.id, row))
  return [...result.values()]
}
async function loadProducts(search = '') {
  const result = await api.get('/products', { params: { q: search, is_active: true, page_size: 100 } })
  products.value = mergeById(products.value, result.data.items)
}
async function loadMaterials(search = '') {
  const result = await api.get('/materials', { params: { q: search, is_active: true, page_size: 100 } })
  materials.value = mergeById(materials.value, result.data.items)
}
async function load() {
  loading.value = true; loadError.value = ''
  try {
    const response = await api.get('/boms', { params: { q: query.value, status: status.value || undefined, page: page.value, page_size: pageSize.value } })
    rows.value = response.data.items; total.value = response.data.total
  } catch (error) { loadError.value = error.message; rows.value = []; total.value = 0 }
  finally { loading.value = false }
}
function search() { page.value = 1; load() }
function selectedProduct(id) { return products.value.find(row => row.id === id) }
function selectedMaterial(id) { return materials.value.find(row => row.id === id) }
function syncProduct() { form.product_unit = selectedProduct(form.product_id)?.unit || '' }
function syncPreviewProduct() { preview.product_unit = selectedProduct(preview.finished_product_id)?.unit || '' }
function compatibleUnits(item) {
  const material = selectedMaterial(item.material_id)
  const source = units.value.find(unit => unit.code === material?.unit)
  return source ? units.value.filter(unit => unit.dimension === source.dimension) : []
}
function addItem() { form.items.push({ material_id: null, quantity_per_unit: '', unit: '', notes: '' }) }
function selectMaterial(item) {
  const material = selectedMaterial(item.material_id)
  item.unit = material?.unit || ''
}
function duplicateMaterial(item) {
  return form.items.some(other => other !== item && other.material_id === item.material_id)
}
function toPayload(nextStatus = form.status) {
  return {
    product_id: form.product_id, version: Number(form.version), status: nextStatus,
    notes: form.notes || null,
    items: form.items.map((item, index) => ({
      material_id: item.material_id, quantity_per_unit: String(item.quantity_per_unit), unit: item.unit,
      sort_order: index + 1, notes: item.notes || null,
    })),
  }
}
async function open(nextMode, row = null) {
  formError.value = ''; mode.value = nextMode
  try {
    const data = row ? (await api.get(`/boms/${row.id}`)).data : null
    if (data) {
      products.value = mergeById(products.value, [{ id: data.product_id, code: data.product_code, name: data.product_name, unit: data.product_unit }])
      materials.value = mergeById(materials.value, data.items.map(item => ({ id: item.material_id, code: item.material_code, name: item.material_name, unit: item.material_stock_unit })))
      Object.assign(form, { id: data.id, product_id: data.product_id, product_unit: data.product_unit,
        version: data.version, status: data.status, notes: data.notes || '',
        items: data.items.map(item => ({ material_id: item.material_id, quantity_per_unit: item.quantity_per_unit, unit: item.unit, notes: item.notes || '' })) })
    } else {
      Object.assign(form, { id: null, product_id: null, product_unit: '', version: 1, status: 'draft', notes: '', items: [] })
      addItem()
    }
    dialog.value = true
  } catch (error) { ElMessage.error(error.message) }
}
async function save() {
  formError.value = ''
  if (!form.product_id || !Number.isInteger(Number(form.version)) || Number(form.version) <= 0) return formError.value = '请选择成品并填写有效版本号'
  if (!form.items.length) return formError.value = '请至少添加一条原材料明细'
  for (const item of form.items) {
    if (!item.material_id || !item.unit || !positiveDecimal.test(String(item.quantity_per_unit)) || duplicateMaterial(item)) {
      return formError.value = duplicateMaterial(item) ? '同一种原材料不能重复添加' : '请完整填写原材料、正数量和合法单位'
    }
  }
  saving.value = true
  try {
    if (form.id) await api.put(`/boms/${form.id}`, toPayload())
    else await api.post('/boms', toPayload())
    dialog.value = false; ElMessage.success('BOM保存成功'); await load()
  } catch (error) { formError.value = error.message }
  finally { saving.value = false }
}
async function setStatus(row, nextStatus) {
  try {
    const data = (await api.get(`/boms/${row.id}`)).data
    const payload = { product_id: data.product_id, version: data.version, status: nextStatus, notes: data.notes,
      items: data.items.map((item, index) => ({ material_id: item.material_id, quantity_per_unit: item.quantity_per_unit,
        unit: item.unit, sort_order: index + 1, notes: item.notes })) }
    await api.put(`/boms/${row.id}`, payload)
    ElMessage.success(nextStatus === 'active' ? 'BOM已启用' : 'BOM已停用'); await load()
  } catch (error) { ElMessage.error(error.message) }
}
function tryRow(row) {
  preview.finished_product_id = row.product_id; preview.product_unit = row.product_unit
  previewResult.value = null; previewError.value = ''
  document.getElementById('bom-preview')?.scrollIntoView({ behavior: 'smooth' })
}
async function calculate() {
  previewError.value = ''; previewResult.value = null
  if (!preview.finished_product_id || !positiveDecimal.test(String(preview.planned_quantity))) return previewError.value = '请选择成品并填写大于0的计划数量'
  previewLoading.value = true
  try {
    previewResult.value = (await api.post('/boms/calculate', {
      finished_product_id: preview.finished_product_id, planned_quantity: String(preview.planned_quantity),
    })).data
  } catch (error) { previewError.value = error.message }
  finally { previewLoading.value = false }
}
function quantityText(quantity, unit) { return `${quantity}${unit}` }

onMounted(async () => {
  try {
    const [unitResponse] = await Promise.all([api.get('/units'), loadProducts(), loadMaterials()])
    units.value = unitResponse.data
  } catch (error) { ElMessage.error(error.message) }
  await load()
})
</script>

<template>
  <div class="page-heading"><div><h1>BOM 配方</h1><p>按每 1 个成品档案单位维护原材料用量；试算只预览理论需求，不改变库存。</p></div><el-button v-if="admin" type="primary" @click="open('create')">新建BOM</el-button></div>
  <el-card shadow="never">
    <el-form inline class="search-bar" @submit.prevent="search">
      <el-form-item label="成品"><el-input v-model="query" placeholder="成品编码 / 名称" clearable @clear="search" /></el-form-item>
      <el-form-item label="状态"><el-select v-model="status" style="width:130px" @change="search"><el-option label="全部" value="" /><el-option label="草稿" value="draft" /><el-option label="启用" value="active" /><el-option label="停用" value="inactive" /></el-select></el-form-item>
      <el-form-item><el-button type="primary" native-type="submit">查询</el-button><el-button @click="query=''; status=''; search()">重置</el-button></el-form-item>
    </el-form>
    <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" show-icon />
    <el-table v-loading="loading" :data="rows" stripe row-key="id" empty-text="暂无BOM配方">
      <el-table-column prop="product_code" label="成品编码" min-width="130" />
      <el-table-column prop="product_name" label="成品名称" min-width="160" />
      <el-table-column prop="product_unit" label="成品单位" width="95" />
      <el-table-column prop="version" label="版本" width="80" />
      <el-table-column prop="item_count" label="原料数" width="90" />
      <el-table-column label="状态" width="90"><template #default="{row}"><el-tag :type="statusType[row.status]">{{ statusText[row.status] }}</el-tag></template></el-table-column>
      <el-table-column label="操作" :width="admin ? 245 : 125" fixed="right"><template #default="{row}">
        <el-button link type="primary" @click="open('view', row)">查看</el-button><el-button link type="primary" @click="tryRow(row)">试算</el-button>
        <el-button v-if="admin" link type="primary" @click="open('edit', row)">编辑</el-button>
        <el-button v-if="admin && row.status !== 'active'" link type="success" @click="setStatus(row, 'active')">启用</el-button>
        <el-button v-if="admin && row.status === 'active'" link type="warning" @click="setStatus(row, 'inactive')">停用</el-button>
      </template></el-table-column>
    </el-table>
    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[10,20,50,100]" layout="total, sizes, prev, pager, next" class="pagination" @current-change="load" @size-change="search" />
  </el-card>

  <el-card id="bom-preview" shadow="never" class="preview-card">
    <template #header><div class="section-heading"><div><strong>理论算料试算</strong><p>理论需求 = 计划成品数量 × BOM 单单位用量</p></div></div></template>
    <el-form inline @submit.prevent="calculate">
      <el-form-item label="成品"><el-select v-model="preview.finished_product_id" filterable remote reserve-keyword :remote-method="loadProducts" placeholder="搜索并选择成品" style="width:260px" @change="syncPreviewProduct"><el-option v-for="product in products" :key="product.id" :label="`${product.code} · ${product.name}`" :value="product.id" /></el-select></el-form-item>
      <el-form-item :label="`计划数量${preview.product_unit ? `（${preview.product_unit}）` : ''}`"><el-input v-model="preview.planned_quantity" style="width:180px" /></el-form-item>
      <el-form-item><el-button type="primary" :loading="previewLoading" native-type="submit">计算理论用量</el-button></el-form-item>
    </el-form>
    <el-alert v-if="previewError" :title="previewError" type="error" :closable="false" show-icon />
    <template v-if="previewResult">
      <div class="preview-summary">{{ previewResult.finished_product_name }} · 计划 {{ quantityText(previewResult.planned_quantity, previewResult.finished_product_unit) }} · BOM V{{ previewResult.bom_version }}</div>
      <el-table :data="previewResult.requirements" border>
        <el-table-column prop="material_name" label="原材料" min-width="180" />
        <el-table-column label="单单位用量" min-width="150"><template #default="{row}">{{ quantityText(row.quantity_per_unit, row.bom_unit) }} / {{ previewResult.finished_product_unit }}</template></el-table-column>
        <el-table-column label="理论需求" min-width="150"><template #default="{row}"><strong>{{ quantityText(row.suggested_quantity, row.suggested_unit) }}</strong><small v-if="row.suggested_unit !== row.total_unit">（{{ quantityText(row.total_quantity, row.total_unit) }}）</small></template></el-table-column>
      </el-table>
    </template>
  </el-card>

  <el-dialog v-model="dialog" :title="`${{create:'新建',edit:'编辑',view:'查看'}[mode]}BOM`" width="900px" destroy-on-close :close-on-click-modal="false">
    <el-form label-width="110px" :disabled="mode === 'view' || saving" @submit.prevent="save">
      <div class="bom-form-grid">
        <el-form-item label="成品" required><el-select v-model="form.product_id" filterable remote reserve-keyword :remote-method="loadProducts" placeholder="搜索并选择成品" @change="syncProduct"><el-option v-for="product in products" :key="product.id" :label="`${product.code} · ${product.name}`" :value="product.id" /></el-select></el-form-item>
        <el-form-item label="成品单位"><el-input :model-value="form.product_unit" disabled /></el-form-item>
        <el-form-item label="版本" required><el-input-number v-model="form.version" :min="1" :max="2147483647" controls-position="right" /></el-form-item>
        <el-form-item label="状态"><el-select v-model="form.status"><el-option label="草稿" value="draft" /><el-option label="启用" value="active" /><el-option label="停用" value="inactive" /></el-select></el-form-item>
      </div>
      <el-form-item label="备注"><el-input v-model="form.notes" type="textarea" :rows="2" maxlength="2000" show-word-limit /></el-form-item>
      <div class="detail-heading"><strong>BOM 明细</strong><el-button v-if="mode !== 'view'" type="primary" plain @click="addItem">+ 添加原材料</el-button></div>
      <el-table :data="form.items" border empty-text="请添加原材料">
        <el-table-column label="原材料" min-width="260"><template #default="{row}"><el-select v-model="row.material_id" filterable remote reserve-keyword :remote-method="loadMaterials" placeholder="搜索原材料" @change="selectMaterial(row)"><el-option v-for="material in materials" :key="material.id" :label="`${material.code} · ${material.name}（库存单位 ${material.unit}）`" :value="material.id" :disabled="form.items.some(other => other !== row && other.material_id === material.id)" /></el-select><div v-if="duplicateMaterial(row)" class="row-error">原材料重复</div></template></el-table-column>
        <el-table-column :label="`每1${form.product_unit || '成品单位'}用量`" min-width="165"><template #default="{row}"><el-input v-model="row.quantity_per_unit" placeholder="大于0，最多6位小数" /></template></el-table-column>
        <el-table-column label="单位" width="125"><template #default="{row}"><el-select v-model="row.unit"><el-option v-for="unit in compatibleUnits(row)" :key="unit.code" :label="unit.code" :value="unit.code" /></el-select></template></el-table-column>
        <el-table-column v-if="mode !== 'view'" label="操作" width="80"><template #default="{$index}"><el-button link type="danger" @click="form.items.splice($index,1)">删除</el-button></template></el-table-column>
      </el-table>
    </el-form>
    <el-alert v-if="formError" :title="formError" type="error" :closable="false" show-icon class="dialog-error" />
    <template #footer><el-button @click="dialog=false">{{ mode === 'view' ? '关闭' : '取消' }}</el-button><el-button v-if="mode !== 'view'" type="primary" :loading="saving" @click="save">保存BOM</el-button></template>
  </el-dialog>
</template>

<style scoped>
.preview-card { margin-top: 22px; }
.preview-summary { margin: 12px 0; padding: 11px 14px; background: #f3f7fd; color: #315f9c; border-radius: 6px; font-size: 14px; }
.preview-summary + .el-table small { margin-left: 8px; color: #8793a4; }
.bom-form-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 0 18px; }
.detail-heading { display: flex; align-items: center; justify-content: space-between; margin: 12px 0; }
.dialog-error { margin-top: 14px; }
.row-error { color: #f56c6c; font-size: 12px; line-height: 1.5; }
@media (max-width: 900px) { .bom-form-grid { grid-template-columns: 1fr; } }
</style>
