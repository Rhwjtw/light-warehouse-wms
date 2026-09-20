<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { dateTime } from '../display'
const filters = reactive({ username: '', module: '', action: '', q: '', date_from: '', date_to: '' })
const modules = ref({}), actions = ref({})
const rows = ref([]), page = ref(1), total = ref(0), loading = ref(false), error = ref('')
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
</script>
<template>
  <div class="page-heading"><div><h1>操作日志</h1><p>查询重要业务操作。日志只读，不能编辑或删除。</p></div></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" />
  <el-form inline @submit.prevent="load(true)">
    <el-form-item v-for="(label,key) in {username:'用户名',q:'对象或描述'}" :key="key" :label="label"><el-input v-model="filters[key]" clearable :placeholder="label" style="width:170px" /></el-form-item>
    <el-form-item label="模块"><el-select v-model="filters.module" clearable style="width:160px" placeholder="全部模块"><el-option v-for="(name,key) in modules" :key="key" :label="name" :value="key" /></el-select></el-form-item>
    <el-form-item label="操作类型"><el-select v-model="filters.action" clearable style="width:160px" placeholder="全部操作"><el-option v-for="(name,key) in actions" :key="key" :label="name" :value="key" /></el-select></el-form-item>
    <el-form-item label="操作日期"><el-date-picker v-model="filters.date_from" value-format="YYYY-MM-DD" placeholder="开始日期" /></el-form-item><el-form-item><el-date-picker v-model="filters.date_to" value-format="YYYY-MM-DD" placeholder="结束日期" /></el-form-item>
    <el-form-item><el-button type="primary" native-type="submit" :loading="loading">查询</el-button></el-form-item>
  </el-form>
  <el-table :data="rows" v-loading="loading" empty-text="暂无符合条件的操作日志">
    <el-table-column label="时间" min-width="180"><template #default="{row}">{{ dateTime(row.created_at) }}</template></el-table-column>
    <el-table-column prop="username_snapshot" label="用户" min-width="130" /><el-table-column prop="module_name" label="模块" min-width="140" />
    <el-table-column prop="action_name" label="操作" min-width="170" /><el-table-column label="对象" min-width="140"><template #default="{row}">{{ row.module_name }} #{{ row.target_id || '—' }}</template></el-table-column>
    <el-table-column prop="description" label="描述" min-width="230" />
  </el-table>
  <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="total, prev, pager, next" @current-change="load()" />
</template>
