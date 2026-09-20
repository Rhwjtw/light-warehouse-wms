<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { auth } from '../auth'
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
  <div class="page-heading"><div><h1>损耗统计</h1><p>查看已完成生产单的结算结果；负损耗保留为节余 / 负损耗。</p></div></div>
  <el-alert v-if="error" :title="error" type="error" show-icon />
  <el-card shadow="never">
    <el-form inline @submit.prevent="page=1; load()">
      <el-form-item v-for="[key,label] in [['order_no','生产单'],['product','成品'],['material','原材料']]" :key="key" :label="label"><el-input v-model="filters[key]" clearable maxlength="120" style="width:165px" /></el-form-item>
      <el-form-item label="完成日期"><el-date-picker v-model="filters.date_from" type="date" value-format="YYYY-MM-DD" placeholder="开始日期" /><span> 至 </span><el-date-picker v-model="filters.date_to" type="date" value-format="YYYY-MM-DD" placeholder="结束日期" /></el-form-item>
      <el-button native-type="submit" type="primary">查询</el-button>
    </el-form>
    <el-table :data="rows" v-loading="loading" stripe empty-text="暂无符合条件的结算记录">
      <el-table-column prop="order_no" label="生产单号" min-width="185" />
      <el-table-column prop="product_name" label="成品" min-width="140" />
      <el-table-column label="实际产量" min-width="125"><template #default="{row}">{{ row.actual_quantity }} {{ row.product_unit }}</template></el-table-column>
      <el-table-column prop="material_name" label="原材料" min-width="130" />
      <el-table-column v-for="[key,label] in [['final_theoretical_quantity','最终理论'],['actual_used_quantity','实际用量'],['loss_quantity','损耗数量']]" :key="key" :label="label" min-width="140"><template #default="{row}">{{ row[key] }} {{ row.base_unit }}<small v-if="key==='loss_quantity' && Number(row[key])<0">（节余 / 负损耗）</small></template></el-table-column>
      <el-table-column label="损耗率" width="105"><template #default="{row}">{{ row.loss_rate == null ? '—' : `${Number(row.loss_rate).toFixed(2)}%` }}</template></el-table-column>
      <el-table-column label="完成时间" min-width="185"><template #default="{row}">{{ new Date(row.completed_at).toLocaleString('zh-CN', { timeZone:auth.user?.business_timezone || 'Asia/Shanghai', hour12:false }) }}</template></el-table-column>
    </el-table>
    <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="total, prev, pager, next" class="pagination" @current-change="load" />
  </el-card>
</template>
