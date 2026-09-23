<script setup>
import { onMounted, reactive, ref } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import { api } from '../api'
import DataPanel from '../components/ui/DataPanel.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import FilterBar from '../components/ui/FilterBar.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
import TableSkeleton from '../components/ui/TableSkeleton.vue'
const filters = reactive({ q: '', status: '', date_from: '', date_to: '' })
const rows = ref([]), total = ref(0), page = ref(1), loading = ref(false), error = ref(''), rules = ref({})
const names = { EXPIRED: '已过期', URGENT: '严重临期', WARNING: '一般临期', NORMAL: '正常' }
const colors = { EXPIRED: 'danger', URGENT: 'warning', WARNING: 'primary', NORMAL: 'success' }
// 状态和剩余天数由后端按业务时区、实时阈值和正库存统一计算，前端只负责筛选与展示。
async function load(reset = false) {
  if (reset) page.value = 1
  loading.value = true; error.value = ''
  try {
    const params = Object.fromEntries(Object.entries(filters).filter(([, v]) => v))
    const data = (await api.get('/alerts/expiry', { params: { ...params, page: page.value } })).data
    rows.value = data.items; total.value = data.total; rules.value = data
  } catch (e) { error.value = e.message; rows.value = []; total.value = 0 }
  finally { loading.value = false }
}
onMounted(() => load())
</script>
<template>
  <PageHeader eyebrow="预警中心" title="有效期预警" description="仅查看仍有库存的批次；到期当天仍有效，状态由现行预警规则计算。">
    <template #actions><el-button :icon="Refresh" :loading="loading" @click="load()">刷新</el-button></template>
  </PageHeader>
  <div class="expiry-rule-note"><StatusBadge tone="danger" label="已过期" /><span>到期日在今天之前</span><StatusBadge tone="warning" label="严重临期" /><span>{{ rules.urgent_expiry_days ?? '—' }} 天内</span><StatusBadge tone="primary" label="一般临期" /><span>{{ rules.warning_expiry_days ?? '—' }} 天内</span></div>
  <div v-if="error" class="feedback-stack"><el-alert :title="error" type="error" :closable="false" show-icon /></div>
  <DataPanel title="批次预警明细" description="优先核查过期与临期批次的余额、批号和有效期。">
    <FilterBar><el-form inline @submit.prevent="load(true)">
    <el-form-item label="原材料"><el-input v-model="filters.q" clearable placeholder="搜索原材料名称" style="width:180px" /></el-form-item>
    <el-form-item label="状态"><el-select v-model="filters.status" clearable placeholder="全部预警" style="width:150px"><el-option v-for="(label,value) in names" :key="value" :label="label" :value="value" /></el-select></el-form-item>
    <el-form-item label="到期日"><el-date-picker v-model="filters.date_from" value-format="YYYY-MM-DD" placeholder="开始日期" style="width:155px" /></el-form-item>
    <el-form-item><el-date-picker v-model="filters.date_to" value-format="YYYY-MM-DD" placeholder="结束日期" style="width:155px" /></el-form-item>
    <el-form-item><el-button :icon="Search" type="primary" native-type="submit" :loading="loading">查询</el-button></el-form-item>
  </el-form></FilterBar>
  <TableSkeleton v-if="loading && !rows.length" />
  <el-table v-else :data="rows" v-loading="loading" stripe row-key="id">
    <el-table-column prop="material_name" label="原材料" min-width="120" show-overflow-tooltip /><el-table-column prop="batch_no" label="批号" min-width="125" />
    <el-table-column label="当前库存" min-width="130"><template #default="{row}"><span class="data-value">{{ row.remaining_quantity }} {{ row.unit }}</span></template></el-table-column>
    <el-table-column prop="production_date" label="生产日期" width="110" /><el-table-column prop="expiry_date" label="到期日期" width="110" />
    <el-table-column label="剩余天数" width="85"><template #default="{row}"><span :class="['expiry-days', `expiry-days--${colors[row.status] || 'info'}`]">{{ row.days_remaining }}</span></template></el-table-column>
    <el-table-column label="状态" width="110"><template #default="{row}"><StatusBadge :tone="colors[row.status] || 'info'" :label="names[row.status] || row.status" /></template></el-table-column>
    <el-table-column prop="location_name" label="库位" min-width="80" /><el-table-column prop="supplier_name" label="供应商" min-width="120" />
    <template #empty><EmptyState title="暂无符合条件的批次" description="可调整筛选条件；默认仅显示已过期和临期库存。" /></template>
  </el-table>
  <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="total, prev, pager, next" class="pagination" @current-change="load()" />
  </DataPanel>
</template>

<style scoped>
.expiry-rule-note { display: flex; flex-wrap: wrap; align-items: center; gap: 9px 12px; margin-bottom: 16px; color: var(--wms-text-secondary); font-size: 12px; }
.expiry-days { font-variant-numeric: tabular-nums; font-weight: 700; }
.expiry-days--danger { color: var(--wms-danger); }
.expiry-days--warning { color: var(--wms-warning); }
.expiry-days--primary { color: #075fc5; }
</style>
