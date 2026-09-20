<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'
const filters = reactive({ q: '', status: '', date_from: '', date_to: '' })
const rows = ref([]), total = ref(0), page = ref(1), loading = ref(false), error = ref(''), rules = ref({})
const names = { EXPIRED: '已过期', URGENT: '严重临期', WARNING: '一般临期', NORMAL: '正常' }
const colors = { EXPIRED: 'danger', URGENT: 'warning', WARNING: 'primary', NORMAL: 'success' }
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
  <div class="page-heading"><div><h1>有效期预警</h1><p>仅显示仍有库存的批次。严重临期 {{ rules.urgent_expiry_days ?? '—' }} 天，一般临期 {{ rules.warning_expiry_days ?? '—' }} 天；到期当天仍有效。</p></div></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" />
  <el-form inline @submit.prevent="load(true)">
    <el-form-item label="原材料"><el-input v-model="filters.q" clearable placeholder="搜索原材料名称" /></el-form-item>
    <el-form-item label="状态"><el-select v-model="filters.status" clearable placeholder="全部预警" style="width:150px"><el-option v-for="(label,value) in names" :key="value" :label="label" :value="value" /></el-select></el-form-item>
    <el-form-item label="到期日"><el-date-picker v-model="filters.date_from" value-format="YYYY-MM-DD" placeholder="开始日期" /></el-form-item>
    <el-form-item><el-date-picker v-model="filters.date_to" value-format="YYYY-MM-DD" placeholder="结束日期" /></el-form-item>
    <el-form-item><el-button type="primary" native-type="submit" :loading="loading">查询</el-button></el-form-item>
  </el-form>
  <el-table :data="rows" v-loading="loading" empty-text="暂无符合条件的库存批次">
    <el-table-column prop="material_name" label="原材料" min-width="150" /><el-table-column prop="batch_no" label="批号" min-width="140" />
    <el-table-column label="当前库存" min-width="140"><template #default="{row}">{{ row.remaining_quantity }} {{ row.unit }}</template></el-table-column>
    <el-table-column prop="production_date" label="生产日期" width="115" /><el-table-column prop="expiry_date" label="到期日期" width="115" />
    <el-table-column prop="days_remaining" label="剩余天数" width="100" />
    <el-table-column label="状态" width="105"><template #default="{row}"><el-tag :type="colors[row.status]">{{ names[row.status] }}</el-tag></template></el-table-column>
    <el-table-column prop="location_name" label="库位" min-width="120" /><el-table-column prop="supplier_name" label="供应商" min-width="140" />
  </el-table>
  <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="total, prev, pager, next" @current-change="load()" />
</template>
