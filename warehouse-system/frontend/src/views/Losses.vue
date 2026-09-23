<script setup>
import { onMounted, reactive, ref } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import { api } from '../api'
import { auth } from '../auth'
import DataPanel from '../components/ui/DataPanel.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import FilterBar from '../components/ui/FilterBar.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
import TableSkeleton from '../components/ui/TableSkeleton.vue'
const filters = reactive({ order_no: '', product: '', material: '', date_from: '', date_to: '' })
const rows = ref([]), total = ref(0), page = ref(1), loading = ref(false), error = ref('')
async function load() {
  loading.value = true; error.value = ''
  try {
    const params = { page: page.value, page_size: 20 }
    for (const [key,value] of Object.entries(filters)) if (value) params[key] = value
    const result = (await api.get('/losses', { params })).data
    rows.value = result.items; total.value = result.total
  } catch (e) { error.value = e.message; rows.value = []; total.value = 0 }
  finally { loading.value = false }
}
onMounted(load)
</script>
<template>
  <PageHeader eyebrow="生产管理" title="损耗统计" description="查看已完成生产单的结算结果，理论、实际与差异均按原材料库存单位展示。">
    <template #actions><el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button></template>
  </PageHeader>
  <div v-if="error" class="feedback-stack"><el-alert :title="error" type="error" show-icon :closable="false" /></div>
  <DataPanel title="生产用料结算" description="差异（损耗数量）= 实际用量 − 最终理论用量；负数表示节余 / 负损耗。">
    <FilterBar><el-form inline @submit.prevent="page=1; load()">
      <el-form-item v-for="[key,label] in [['order_no','生产单'],['product','成品'],['material','原材料']]" :key="key" :label="label"><el-input v-model="filters[key]" clearable maxlength="120" style="width:165px" /></el-form-item>
      <el-form-item label="完成日期"><el-date-picker v-model="filters.date_from" type="date" value-format="YYYY-MM-DD" placeholder="开始日期" /><span> 至 </span><el-date-picker v-model="filters.date_to" type="date" value-format="YYYY-MM-DD" placeholder="结束日期" /></el-form-item>
      <el-form-item><el-button :icon="Search" native-type="submit" type="primary">查询</el-button></el-form-item>
    </el-form></FilterBar>
    <TableSkeleton v-if="loading && !rows.length" />
    <el-table v-else :data="rows" v-loading="loading" stripe>
      <el-table-column prop="order_no" label="生产单号" min-width="155" />
      <el-table-column prop="product_name" label="成品" min-width="110" show-overflow-tooltip />
      <el-table-column label="实际产量" min-width="130"><template #default="{row}"><span class="data-value">{{ row.actual_quantity }} {{ row.product_unit }}</span></template></el-table-column>
      <el-table-column prop="material_name" label="原材料" min-width="105" show-overflow-tooltip />
      <el-table-column label="最终理论用量" min-width="135"><template #default="{row}"><span class="data-value">{{ row.final_theoretical_quantity }} {{ row.base_unit }}</span></template></el-table-column>
      <el-table-column label="实际用量" min-width="135"><template #default="{row}"><span class="data-value">{{ row.actual_used_quantity }} {{ row.base_unit }}</span></template></el-table-column>
      <el-table-column label="差异 / 损耗数量" min-width="160"><template #default="{row}"><div class="loss-value"><span class="data-value">{{ row.loss_quantity }} {{ row.base_unit }}</span><StatusBadge v-if="Number(row.loss_quantity) < 0" tone="warning" label="节余 / 负损耗" /></div></template></el-table-column>
      <el-table-column label="损耗率" width="85"><template #default="{row}"><span class="data-value">{{ row.loss_rate == null ? '—' : `${Number(row.loss_rate).toFixed(2)}%` }}</span></template></el-table-column>
      <el-table-column label="完成时间" min-width="185"><template #default="{row}">{{ new Date(row.completed_at).toLocaleString('zh-CN', { timeZone:auth.user?.business_timezone || 'Asia/Shanghai', hour12:false }) }}</template></el-table-column>
      <template #empty><EmptyState title="暂无结算记录" description="完成生产后可按生产单、成品或原材料筛选结果。" /></template>
    </el-table>
    <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="total, prev, pager, next" class="pagination" @current-change="load" />
  </DataPanel>
</template>

<style scoped>
.loss-value { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
</style>
