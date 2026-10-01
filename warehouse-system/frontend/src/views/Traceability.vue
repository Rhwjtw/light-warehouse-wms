<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import PageHeader from '../components/ui/PageHeader.vue'
import DataPanel from '../components/ui/DataPanel.vue'
import DocumentReversal from '../components/DocumentReversal.vue'

const router = useRouter()
const query = ref(''), candidates = ref([]), result = ref(null), loading = ref(false), error = ref(''), truncated = ref(false)
const documentOpen = ref(false), documentId = ref(null)
const entrances = { material: 'material-batches', finished: 'finished-batches', production: 'production-orders' }
const types = { material: '原材料逻辑批次', finished: '成品批次/历史库存', production: '生产单' }
const kinds = { material_receipt:'原材料入库', material_outbound:'原材料出库', putaway:'上架', transfer:'移库', issue:'领料', supplement:'补料', return:'退料', finished_receipt:'成品入库', finished_outbound:'成品出库', reversal:'冲销', adjustment:'调整', count_gain:'盘盈', count_loss:'盘亏', count_match:'盘点' }
let generation = 0
async function search() {
  if (!query.value.trim()) { error.value = '请输入批号、原料/成品名称或生产单号'; return }
  const ticket = ++generation
  loading.value = true; error.value = ''; result.value = null; candidates.value = []
  try {
    const response = await api.get('/traceability/search', { params: { q: query.value.trim() } })
    if (ticket === generation) { candidates.value = response.data.items; truncated.value = response.data.truncated }
  } catch (e) { if (ticket === generation) error.value = e.message }
  finally { if (ticket === generation) loading.value = false }
}
async function open(row) {
  const ticket = ++generation
  loading.value = true; error.value = ''; result.value = null
  try {
    const response = await api.get(`/traceability/${entrances[row.entry_type]}/${row.id}`)
    if (ticket === generation) result.value = response.data
  } catch (e) { if (ticket === generation) error.value = e.message }
  finally { if (ticket === generation) loading.value = false }
}
function openDocument(row) { documentId.value = row.id; documentOpen.value = true }
</script>

<template>
  <PageHeader eyebrow="只读查询" title="批次追溯" description="基于实际领料、补料、退料及完整冲销事实双向追溯，不反推历史批次归属。" />
  <el-alert v-if="error" :title="error" type="error" :closable="false" />
  <DataPanel title="统一搜索">
    <el-form inline @submit.prevent="search"><el-form-item label="批次/生产单"><el-input v-model="query" maxlength="120" clearable placeholder="原料批号、成品批号、生产单号或名称" style="width:360px" /></el-form-item><el-button native-type="submit" type="primary" :loading="loading">查询</el-button></el-form>
    <el-alert v-if="truncated" title="结果较多，请输入更完整的批号或名称缩小范围。" type="info" :closable="false" />
    <el-table :data="candidates" v-loading="loading"><el-table-column label="入口" min-width="140"><template #default="{row}">{{ types[row.entry_type] }}</template></el-table-column><el-table-column label="原料/成品" min-width="160"><template #default="{row}">{{ row.material_name || row.product_name }}</template></el-table-column><el-table-column label="批号/单号" min-width="190"><template #default="{row}">{{ row.order_no || row.batch_no || '历史无批次归属库存' }}</template></el-table-column><el-table-column prop="supplier_name" label="供应商" min-width="140" /><el-table-column prop="production_date" label="生产日期" width="120" /><el-table-column prop="expiry_date" label="到期日期" width="120" /><el-table-column width="90"><template #default="{row}"><el-button link type="primary" @click="open(row)">追溯</el-button></template></el-table-column></el-table>
  </DataPanel>
  <template v-if="result">
    <DataPanel title="追溯对象">
      <el-descriptions :column="3" border><el-descriptions-item label="批次/生产单">{{ result.subject.order_no || result.subject.batch_no || '历史无批次归属库存' }}</el-descriptions-item><el-descriptions-item label="原料/成品">{{ result.subject.material_name || result.subject.product_name || '见生产单详情' }}</el-descriptions-item><el-descriptions-item label="供应商">{{ result.subject.supplier_name || '—' }}</el-descriptions-item><el-descriptions-item label="生产日期">{{ result.subject.production_date || '历史无法确定' }}</el-descriptions-item><el-descriptions-item label="实际产量">{{ result.subject.production_quantity ?? '见生产单' }}</el-descriptions-item><el-descriptions-item label="当前库存">{{ result.subject.current_quantity ?? '—' }} {{ result.subject.unit }}</el-descriptions-item></el-descriptions>
      <el-table v-if="result.subject.locations" :data="result.subject.locations"><el-table-column prop="location_name" label="库位/待上架" /><el-table-column prop="quantity" label="当前数量" /><el-table-column prop="batch_id" label="物理库存行" /></el-table>
      <el-alert v-for="warning in result.warnings" :key="warning" :title="warning" type="info" :closable="false" style="margin-top:12px" />
    </DataPanel>
    <DataPanel v-for="order in result.production_orders" :key="order.id" :title="`生产单 ${order.order_no}`" description="实际原料使用按生产单展示，数量全部由后端事实计算。">
      <p><el-button link type="primary" @click="router.push({path:'/orders',query:{order_id:order.id}})">打开生产单详情</el-button> · {{ order.product_name }} · 实际产量 {{ order.actual_quantity ?? '尚未完工' }} {{ order.unit }}</p>
      <el-table :data="order.materials"><el-table-column prop="material_name" label="原料" min-width="140" /><el-table-column label="原材料批次" min-width="150"><template #default="{row}"><el-button link type="primary" @click="open({entry_type:'material',id:row.id})">{{ row.batch_no }}</el-button></template></el-table-column><el-table-column prop="supplier_name" label="供应商" min-width="130" /><el-table-column prop="issued_quantity" label="领料" /><el-table-column prop="supplemented_quantity" label="补料" /><el-table-column prop="returned_quantity" label="退料" /><el-table-column prop="net_used_quantity" label="实际净使用" min-width="120" /><el-table-column prop="unit" label="单位" width="65" /><el-table-column label="有效关系" width="100"><template #default="{row}">{{ row.effective ? '有效' : '净使用为零' }}</template></el-table-column></el-table>
      <h4>产出的成品批次</h4>
      <el-table :data="order.finished_batches"><el-table-column label="批次" min-width="170"><template #default="{row}"><el-button v-if="row.stock_scope === 'batch'" link type="primary" @click="open({entry_type:'finished',id:row.id})">{{ row.batch_no }}</el-button><span v-else>{{ row.label }}</span></template></el-table-column><el-table-column prop="production_date" label="生产日期" /><el-table-column prop="production_quantity" label="实际生产数量" /><el-table-column label="该批当前库存"><template #default="{row}">{{ row.stock_scope === 'batch' ? `${row.current_quantity} ${row.unit}` : '历史批次无法确定' }}</template></el-table-column></el-table>
    </DataPanel>
    <DataPanel v-if="result.excluded_orders?.length" title="不计入有效追溯的历史生产关系"><el-table :data="result.excluded_orders"><el-table-column prop="order_no" label="生产单" /><el-table-column prop="net_used_quantity" label="实际净使用" /><el-table-column prop="reason" label="原因" /></el-table></DataPanel>
    <DataPanel title="相关业务单据" description="已完整冲销的原单与冲销单仍保留，原单不计入有效数量。"><el-table :data="result.documents"><el-table-column label="单据" min-width="240"><template #default="{row}"><el-button link type="primary" @click="openDocument(row)">{{ row.document_no }}</el-button></template></el-table-column><el-table-column label="类型"><template #default="{row}">{{ kinds[row.kind] || row.kind }}</template></el-table-column><el-table-column label="状态"><template #default="{row}">{{ {normal:'有效',reversed:'已冲销',reversal:'冲销单',incomplete_reversal:'冲销证据不完整，仍计入原事实'}[row.status] }}</template></el-table-column><el-table-column prop="reference_no" label="关联单号" min-width="180" /></el-table></DataPanel>
  </template>
  <DocumentReversal v-model="documentOpen" :document-id="documentId" read-only />
</template>
