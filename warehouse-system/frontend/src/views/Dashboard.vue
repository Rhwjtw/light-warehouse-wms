<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { dateTime, kindNames, statusNames } from '../display'
const data = ref(null), transactions = ref([]), orders = ref([]), loading = ref(false), error = ref('')
const metrics = { materials:'原材料种类', products:'成品种类', stocked_batches:'有库存原料批次', in_progress_orders:'生产中生产单', low_stock_materials:'库存不足原材料' }
const todayLabels = { material_in:'今日原材料入库', material_out:'今日原材料出库', finished_in:'今日成品入库', finished_out:'今日成品出库' }
async function load() {
  loading.value = true; error.value = ''
  try {
    const [summary, ledger, production] = await Promise.all([api.get('/dashboard/summary'), api.get('/dashboard/recent-transactions'), api.get('/dashboard/recent-production-orders')])
    data.value = summary.data; transactions.value = ledger.data.items; orders.value = production.data.items
  } catch (e) { error.value = e.message; data.value = null; transactions.value = []; orders.value = [] }
  finally { loading.value = false }
}
onMounted(load)
</script>
<template>
  <div class="page-heading"><div><h1>仓库工作台</h1><p>库存、生产与有效期一览。{{ data?.business_date }} {{ data?.timezone }}</p></div><el-button :loading="loading" @click="load">刷新数据</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" />
  <div v-loading="loading">
    <div class="metrics dashboard-counts"><el-card v-for="(label,key) in metrics" :key="key" shadow="never"><div class="metric-label">{{ label }}</div><div class="metric-value">{{ data?.[key] ?? '—' }}</div></el-card></div>
    <div class="metrics"><el-card v-for="(label,key) in todayLabels" :key="key" shadow="never"><div class="metric-label">{{ label }}</div><h3 v-if="data">{{ data.today[key].map(row => `${row.quantity} ${row.unit}`).join(' / ') || '0' }}</h3><h3 v-else>—</h3></el-card></div>
    <p>今日按实际记账时间统计；原材料入库含生产退料，出库含生产领料和补料，不同单位分别汇总。</p>
    <div class="section-heading"><h3>有效期预警</h3><el-button link type="primary" @click="$router.push('/expiry')">查看批次</el-button></div>
    <div class="metrics">
      <el-card shadow="never"><div>已过期批次</div><h2>{{ data?.expired_batches ?? '—' }}</h2></el-card>
      <el-card shadow="never"><div>{{ data?.urgent_expiry_days ?? '—' }} 天内到期批次</div><h2>{{ data?.urgent_batches ?? '—' }}</h2></el-card>
      <el-card shadow="never"><div>{{ data?.warning_expiry_days ?? '—' }} 天内到期批次（含严重临期）</div><h2>{{ data?.warning_batches ?? '—' }}</h2></el-card>
    </div>
    <el-card shadow="never"><template #header><strong>最近库存流水 · 10 条</strong></template>
      <el-table :data="transactions" empty-text="暂无库存流水">
        <el-table-column label="时间" min-width="170"><template #default="{row}">{{ dateTime(row.created_at, data?.timezone) }}</template></el-table-column>
        <el-table-column prop="name" label="原材料 / 成品" min-width="140" /><el-table-column label="类型" min-width="110"><template #default="{row}">{{ kindNames[row.kind] }}</template></el-table-column>
        <el-table-column label="数量变化" min-width="130"><template #default="{row}">{{ row.change_quantity }} {{ row.unit }}</template></el-table-column>
        <el-table-column prop="document_no" label="单据号" min-width="190" /><el-table-column prop="reference_no" label="关联单号" min-width="150" /><el-table-column prop="operator" label="操作人" min-width="100" />
      </el-table>
    </el-card>
    <el-card shadow="never" style="margin-top:20px"><template #header><strong>最近生产单 · 10 条</strong></template>
      <el-table :data="orders" empty-text="暂无生产单">
        <el-table-column prop="order_no" label="生产单号" min-width="180" /><el-table-column prop="product_name" label="成品" min-width="130" />
        <el-table-column label="计划数量" min-width="130"><template #default="{row}">{{ row.planned_quantity }} {{ row.unit }}</template></el-table-column>
        <el-table-column label="实际数量" min-width="130"><template #default="{row}">{{ row.actual_quantity ?? '—' }} {{ row.unit }}</template></el-table-column>
        <el-table-column label="状态" width="110"><template #default="{row}">{{ statusNames[row.status] }}</template></el-table-column>
        <el-table-column label="创建时间" min-width="180"><template #default="{row}">{{ dateTime(row.created_at, data?.timezone) }}</template></el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.dashboard-counts { grid-template-columns: repeat(auto-fit, minmax(155px, 1fr)); margin-bottom: 16px; }
.metric-value { color: #24364b; font-weight: 600; }
</style>
