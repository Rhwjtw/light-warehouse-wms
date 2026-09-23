<script setup>
import { onMounted, reactive, ref } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { api } from '../api'
import { dateTime } from '../display'
import DataPanel from '../components/ui/DataPanel.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import FilterBar from '../components/ui/FilterBar.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
import TableSkeleton from '../components/ui/TableSkeleton.vue'
const filters = reactive({ username: '', module: '', action: '', q: '', date_from: '', date_to: '' })
const modules = ref({}), actions = ref({})
const rows = ref([]), page = ref(1), total = ref(0), loading = ref(false), error = ref('')
const selected = ref(null), detailVisible = ref(false)
async function load(reset = false) {
  if (reset) page.value = 1
  loading.value = true; error.value = ''
  try {
    const params = Object.fromEntries(Object.entries(filters).filter(([, v]) => v))
    const data = (await api.get('/operation-logs', { params: { ...params, page: page.value } })).data
    rows.value = data.items; total.value = data.total; modules.value = data.modules; actions.value = data.actions
  } catch (e) { error.value = e.message; rows.value = []; total.value = 0 }
  finally { loading.value = false }
}
onMounted(() => load())
function resetFilters() {
  Object.keys(filters).forEach(key => { filters[key] = '' })
  load(true)
}
function showDetail(row) {
  selected.value = row
  detailVisible.value = true
}
</script>
<template>
  <PageHeader eyebrow="系统管理" title="操作日志" description="按用户、操作和时间追查重要业务记录；日志只读。" />
  <div v-if="error" class="feedback-stack"><el-alert :title="error" type="error" :closable="false" show-icon /></div>
  <DataPanel title="日志记录" description="最新记录优先；点击查看可阅读完整描述。">
    <FilterBar><el-form inline class="log-filters" @submit.prevent="load(true)">
      <el-form-item label="用户"><el-input v-model="filters.username" clearable placeholder="用户名" class="log-filter-short" /></el-form-item>
      <el-form-item label="关键词"><el-input v-model="filters.q" clearable placeholder="对象或描述" class="log-filter-short" /></el-form-item>
      <el-form-item label="模块"><el-select v-model="filters.module" clearable class="log-filter-select" placeholder="全部模块"><el-option v-for="(name,key) in modules" :key="key" :label="name" :value="key" /></el-select></el-form-item>
      <el-form-item label="操作"><el-select v-model="filters.action" clearable class="log-filter-select" placeholder="全部操作"><el-option v-for="(name,key) in actions" :key="key" :label="name" :value="key" /></el-select></el-form-item>
      <el-form-item label="日期"><el-date-picker v-model="filters.date_from" value-format="YYYY-MM-DD" placeholder="开始日期" class="log-filter-date" /></el-form-item>
      <el-form-item><el-date-picker v-model="filters.date_to" value-format="YYYY-MM-DD" placeholder="结束日期" class="log-filter-date" /></el-form-item>
      <el-form-item><el-button type="primary" :icon="Search" native-type="submit" :loading="loading">查询</el-button><el-button @click="resetFilters">重置</el-button></el-form-item>
    </el-form></FilterBar>
    <TableSkeleton v-if="loading && !rows.length" />
    <el-table v-else :data="rows" v-loading="loading" stripe row-key="id">
      <el-table-column label="时间" width="178"><template #default="{ row }"><span class="log-time">{{ dateTime(row.created_at) }}</span></template></el-table-column>
      <el-table-column prop="username_snapshot" label="用户" min-width="120" show-overflow-tooltip />
      <el-table-column prop="module_name" label="模块" min-width="120" show-overflow-tooltip />
      <el-table-column label="操作类型" min-width="150"><template #default="{ row }"><span class="log-action">{{ row.action_name }}</span></template></el-table-column>
      <el-table-column label="业务对象" min-width="145"><template #default="{ row }"><span class="log-target">{{ row.target_id ? `${row.module_name} #${row.target_id}` : '—' }}</span></template></el-table-column>
      <el-table-column label="结果" width="90"><template #default="{ row }"><StatusBadge :tone="row.action === 'login_failed' ? 'danger' : 'success'" :label="row.action === 'login_failed' ? '失败' : '成功'" /></template></el-table-column>
      <el-table-column label="关键描述" min-width="230"><template #default="{ row }"><div class="log-description"><span class="log-description-text" :title="row.description || ''">{{ row.description || '—' }}</span><el-button v-if="row.description" link type="primary" @click="showDetail(row)">查看</el-button></div></template></el-table-column>
      <template #empty><EmptyState :title="error ? '日志加载失败' : '暂无符合条件的日志'" :description="error ? '请检查连接后重新查询。' : '可调整用户、时间或操作条件。'" /></template>
    </el-table>
    <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="total, prev, pager, next" class="pagination" @current-change="load()" />
  </DataPanel>
  <el-dialog v-model="detailVisible" title="日志详情" width="min(620px, 94vw)">
    <div v-if="selected" class="log-detail">
      <div><span>时间</span><strong>{{ dateTime(selected.created_at) }}</strong></div>
      <div><span>用户</span><strong>{{ selected.username_snapshot || '—' }}</strong></div>
      <div><span>操作</span><strong>{{ selected.action_name }}</strong></div>
      <div><span>业务对象</span><strong>{{ selected.target_id ? `${selected.module_name} #${selected.target_id}` : '—' }}</strong></div>
      <div><span>结果</span><StatusBadge :tone="selected.action === 'login_failed' ? 'danger' : 'success'" :label="selected.action === 'login_failed' ? '失败' : '成功'" /></div>
      <div class="log-detail-description"><span>完整描述</span><p>{{ selected.description || '—' }}</p></div>
    </div>
    <template #footer><el-button @click="detailVisible = false">关闭</el-button></template>
  </el-dialog>
</template>

<style scoped>
.log-filter-short { width: 155px; }
.log-filter-select { width: 150px; }
.log-filter-date { width: 150px; }
.log-time, .log-target { color: var(--wms-text-secondary); font-variant-numeric: tabular-nums; }
.log-action { color: var(--wms-text); font-weight: 600; }
.log-description { display: flex; align-items: center; gap: 8px; min-width: 0; }
.log-description-text { min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.log-description .el-button { flex: 0 0 auto; }
.log-detail { display: grid; gap: 16px; }
.log-detail > div { display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 14px; align-items: start; }
.log-detail span:first-child { color: var(--wms-text-tertiary); font-size: 12px; }
.log-detail strong { min-width: 0; overflow-wrap: anywhere; font-size: 13px; font-weight: 600; }
.log-detail-description p { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; }
</style>
