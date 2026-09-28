<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
import { auth } from '../auth'
import DataPanel from '../components/ui/DataPanel.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import DocumentReversal from '../components/DocumentReversal.vue'

const rows = ref([]), total = ref(0), page = ref(1), q = ref(''), loading = ref(false), error = ref('')
const detailOpen = ref(false), detailId = ref(null)
async function load() {
  loading.value = true; error.value = ''
  try {
    const result = await api.get('/stock-documents/reversals', { params: { q: q.value, page: page.value, page_size: 20 } })
    rows.value = result.data.items; total.value = result.data.total
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
}
function detail(id) { detailId.value = id; detailOpen.value = true }
function time(value) { return new Date(value).toLocaleString('zh-CN', { timeZone: auth.user?.business_timezone || 'Asia/Shanghai', hour12: false }) }
onMounted(load)
</script>

<template>
  <PageHeader eyebrow="库存单据" title="冲销记录" description="保留原单和原始流水，通过关联的冲销单查询更正历史。" />
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
  <DataPanel title="冲销单查询">
    <el-form inline @submit.prevent="page = 1; load()"><el-form-item label="单据号"><el-input v-model="q" clearable placeholder="原单号或冲销单号" @clear="page = 1; load()" /></el-form-item><el-form-item><el-button type="primary" native-type="submit">查询</el-button></el-form-item></el-form>
    <el-table :data="rows" v-loading="loading" stripe>
      <el-table-column prop="document_no" label="冲销单号" min-width="210" />
      <el-table-column prop="original_document_no_snapshot" label="原单号" min-width="210" />
      <el-table-column prop="reverses_kind" label="原业务" min-width="125"><template #default="{ row }">{{ ({ material_receipt: '待上架入库', putaway: '上架', transfer: '移库', finished_receipt: '成品普通入库', adjustment: '库存调整', material_outbound: '原料普通出库', finished_outbound: '成品普通出库' })[row.reverses_kind] || row.reverses_kind }}</template></el-table-column>
      <el-table-column prop="reversal_reason" label="冲销原因" min-width="200" show-overflow-tooltip />
      <el-table-column prop="operator_name" label="操作人" min-width="100" />
      <el-table-column prop="created_at" label="冲销时间" min-width="190"><template #default="{ row }">{{ time(row.created_at) }}</template></el-table-column>
      <el-table-column label="详情" width="90" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="detail(row.id)">查看</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="total, prev, pager, next" @current-change="load" />
  </DataPanel>
  <DocumentReversal v-model="detailOpen" :document-id="detailId" @changed="load" />
</template>
