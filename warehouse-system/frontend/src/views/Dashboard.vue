<script setup>
import { computed, onMounted, ref } from 'vue'
import { Box, Calendar, Download, Files, Finished, Goods, Plus, Refresh, Upload, Warning } from '@element-plus/icons-vue'
import { api } from '../api'
import { dateTime, kindNames, statusNames } from '../display'
import DataPanel from '../components/ui/DataPanel.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import QuickActions from '../components/ui/QuickActions.vue'
import StatCard from '../components/ui/StatCard.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
import TableSkeleton from '../components/ui/TableSkeleton.vue'

const data = ref(null), transactions = ref([]), orders = ref([]), loading = ref(false), error = ref('')
const materialStock = ref([]), finishedStock = ref([]), stockLoading = ref(false)
const materialStockError = ref(''), finishedStockError = ref('')
const actions = [
  { resource: 'material-receipts', to: '/material-receipts', label: '原材料入库', description: '按批次登记', icon: Download, permission: 'material.inventory.write' },
  { resource: 'material-outbounds', to: '/material-outbounds', label: '原材料出库', description: '选择可用批次', icon: Upload, permission: 'material.inventory.write' },
  { resource: 'finished-receipts', to: '/finished-receipts', label: '成品入库', description: '普通入库记账', icon: Goods, permission: 'finished.inventory.write' },
  { resource: 'finished-outbounds', to: '/finished-outbounds', label: '成品出库', description: '核对当前余额', icon: Box, permission: 'finished.inventory.write' },
  { resource: 'orders', to: '/orders', label: '新建生产单', description: '核对 BOM 与计划数量', icon: Plus, permission: 'production.order.write' },
  { resource: 'orders', to: '/orders', label: '生产任务', description: '查看与处理生产单', icon: Files },
  { resource: 'issues', to: '/issues', label: '生产领料', description: '按批次领取', icon: Files, permission: 'workshop.pick' },
  { resource: 'supplements', to: '/supplements', label: '生产补料', description: '登记追加用料', icon: Finished, permission: 'workshop.replenish' },
  { resource: 'returns', to: '/returns', label: '生产退料', description: '退回原领用批次', icon: Upload, permission: 'workshop.return' },
  { resource: 'expiry', to: '/expiry', label: '有效期预警', description: '查看临期批次', icon: Calendar },
]
const alerts = computed(() => data.value ? [
  { label: '已过期批次', value: data.value.expired_batches, tone: 'danger', detail: '仍有库存的原料批次', to: '/expiry' },
  { label: `${data.value.urgent_expiry_days} 天内到期`, value: data.value.urgent_batches, tone: 'warning', detail: '不含已过期批次', to: '/expiry' },
  { label: `${data.value.warning_expiry_days} 天内到期`, value: data.value.warning_batches, detail: '含严重临期批次', to: '/expiry' },
  { label: '低库存原材料', value: data.value.low_stock_materials, tone: 'warning', detail: '按档案最低库存阈值', to: '/materials' },
] : [])
const attention = computed(() => data.value ? [
  data.value.expired_batches > 0 ? `有 ${data.value.expired_batches} 个仍有库存的批次已过期` : '',
  data.value.urgent_batches > 0 ? `有 ${data.value.urgent_batches} 个批次在 ${data.value.urgent_expiry_days} 天内到期` : '',
  data.value.low_stock_materials > 0 ? `有 ${data.value.low_stock_materials} 种原材料低于最低库存` : '',
].filter(Boolean) : [])

// 今日变动由后端按实际记账时间和单位聚合；前端只格式化，绝不混加 kg、L、个。
function todayText(key) {
  const values = data.value?.today?.[key] || []
  return values.length ? values.map(row => `${row.quantity} ${row.unit}`).join(' / ') : '0'
}
function statusTone(value) { return ({ ready: 'warning', in_progress: 'primary', completed: 'success', received: 'success', cancelled: 'info' })[value] || 'info' }
function transactionTone(value) { return ['material_receipt', 'return', 'finished_receipt'].includes(value) ? 'success' : 'primary' }
async function load() {
  loading.value = true; error.value = ''
  try {
    const [summary, ledger, production] = await Promise.all([api.get('/dashboard/summary'), api.get('/dashboard/recent-transactions'), api.get('/dashboard/recent-production-orders')])
    data.value = summary.data; transactions.value = ledger.data.items; orders.value = production.data.items
  } catch (e) { error.value = e.message; data.value = null; transactions.value = []; orders.value = [] }
  finally { loading.value = false }
}
// 库存速览独立加载：单个库存列表失败时，不阻断首页已有的统计与快捷操作。
async function loadStock() {
  stockLoading.value = true; materialStockError.value = ''; finishedStockError.value = ''
  const [materials, finished] = await Promise.allSettled([
    api.get('/material-stock', { params: { page: 1, page_size: 5 } }),
    api.get('/finished-stock', { params: { page: 1, page_size: 5 } }),
  ])
  if (materials.status === 'fulfilled') materialStock.value = materials.value.data.items
  else { materialStock.value = []; materialStockError.value = materials.reason?.message || '原材料库存加载失败' }
  if (finished.status === 'fulfilled') finishedStock.value = finished.value.data.items
  else { finishedStock.value = []; finishedStockError.value = finished.reason?.message || '成品库存加载失败' }
  stockLoading.value = false
}
function refresh() { load(); loadStock() }
onMounted(refresh)
</script>

<template>
  <PageHeader eyebrow="工作台" title="仓库工作台" :description="data ? `${data.business_date} · ${data.timezone} · 库存、生产和预警` : '库存、生产和预警集中查看'">
    <template #actions><el-button :icon="Refresh" :loading="loading || stockLoading" @click="refresh">刷新数据</el-button></template>
  </PageHeader>
  <div v-if="error" class="feedback-stack"><el-alert :title="error" type="error" show-icon :closable="false" /></div>

  <DataPanel title="常用操作" description="按当前角色展示可访问入口，进入页面即可办理。" class="dashboard-panel">
    <QuickActions :actions="actions" />
  </DataPanel>

  <template v-if="loading && !data"><div class="dashboard-stat-grid"><el-skeleton v-for="item in 4" :key="item" animated :rows="2" class="dashboard-skeleton-card" /></div><TableSkeleton /></template>
  <template v-else-if="data">
    <div class="dashboard-section-title"><h2>库存概况</h2><span>来自当前库存和生产单</span></div>
    <div class="dashboard-stat-grid">
      <StatCard label="原材料种类" :value="data.materials" detail="未删除的原料档案" to="/materials" />
      <StatCard label="有库存原料批次" :value="data.stocked_batches" detail="可按批次查看余额" to="/batches" />
      <StatCard label="成品种类" :value="data.products" detail="未删除的成品档案" to="/finished-stock" />
      <StatCard label="生产中生产单" :value="data.in_progress_orders" detail="等待用料或完工处理" to="/orders" tone="primary" />
    </div>

    <div class="dashboard-section-title"><h2>当前需要关注</h2><router-link to="/expiry">查看有效期预警</router-link></div>
    <div class="dashboard-stat-grid"><StatCard v-for="item in alerts" :key="item.label" v-bind="item" /></div>
    <el-alert v-if="attention.length" type="warning" show-icon :closable="false" class="dashboard-attention">
      <template #title>{{ attention.join('；') }}</template>
    </el-alert>
    <p v-else class="dashboard-calm"><el-icon><Warning /></el-icon> 当前没有过期、严重临期或低库存提醒。</p>

    <div class="dashboard-inventory-grid">
      <DataPanel title="原材料库存" description="库存档案前 5 项（含零余额）；数量按各自单位展示。" class="dashboard-panel">
        <template #actions><router-link class="panel-link" to="/material-stock">查看全部</router-link></template>
        <el-alert v-if="materialStockError" :title="materialStockError" type="error" :closable="false" class="inventory-feedback" />
        <TableSkeleton v-if="stockLoading && !materialStock.length" :rows="3" />
        <el-table v-else :data="materialStock" stripe row-key="id">
          <el-table-column prop="code" label="编码" min-width="100" show-overflow-tooltip />
          <el-table-column prop="name" label="原材料" min-width="130" show-overflow-tooltip />
          <el-table-column label="当前库存" min-width="145"><template #default="{row}"><strong class="data-value">{{ row.remaining_quantity }} {{ row.unit }}</strong></template></el-table-column>
          <template #empty><EmptyState title="暂无原材料库存" description="可前往原材料库存查看全部档案。" /></template>
        </el-table>
      </DataPanel>
      <DataPanel title="成品库存" description="库存档案前 5 项（含零余额）；数量按各自单位展示。" class="dashboard-panel">
        <template #actions><router-link class="panel-link" to="/finished-stock">查看全部</router-link></template>
        <el-alert v-if="finishedStockError" :title="finishedStockError" type="error" :closable="false" class="inventory-feedback" />
        <TableSkeleton v-if="stockLoading && !finishedStock.length" :rows="3" />
        <el-table v-else :data="finishedStock" stripe row-key="id">
          <el-table-column prop="code" label="编码" min-width="100" show-overflow-tooltip />
          <el-table-column prop="name" label="成品" min-width="130" show-overflow-tooltip />
          <el-table-column label="当前库存" min-width="145"><template #default="{row}"><strong class="data-value">{{ row.quantity }} {{ row.unit }}</strong></template></el-table-column>
          <template #empty><EmptyState title="暂无成品库存" description="可前往成品库存查看全部档案。" /></template>
        </el-table>
      </DataPanel>
    </div>

    <DataPanel title="今日出入库" description="按实际记账时间统计；原材料入含退料，出含领料与补料。不同单位分别显示。" class="dashboard-panel">
      <div class="dashboard-today-grid">
        <div><span>原材料入库</span><strong>{{ todayText('material_in') }}</strong></div>
        <div><span>原材料出库</span><strong>{{ todayText('material_out') }}</strong></div>
        <div><span>成品入库</span><strong>{{ todayText('finished_in') }}</strong></div>
        <div><span>成品出库</span><strong>{{ todayText('finished_out') }}</strong></div>
      </div>
    </DataPanel>

    <DataPanel title="最近出入库" description="最近 10 条原材料与成品库存流水。" class="dashboard-panel">
      <template #actions><router-link class="panel-link" to="/material-transactions">查看原料流水</router-link></template>
      <el-table :data="transactions" stripe row-key="id">
        <el-table-column label="记账时间" min-width="175"><template #default="{row}">{{ dateTime(row.created_at, data.timezone) }}</template></el-table-column>
        <el-table-column prop="name" label="原材料 / 成品" min-width="145" show-overflow-tooltip />
        <el-table-column label="类型" width="125"><template #default="{row}"><StatusBadge :tone="transactionTone(row.kind)" :label="kindNames[row.kind] || row.kind" /></template></el-table-column>
        <el-table-column label="变动量" min-width="145"><template #default="{row}"><span class="data-value">{{ row.change_quantity }} {{ row.unit }}</span></template></el-table-column>
        <el-table-column prop="document_no" label="单据号" min-width="190" />
        <el-table-column prop="operator" label="操作人" min-width="110" />
        <template #empty><EmptyState title="暂无库存流水" description="完成入库、出库或生产用料后将在这里显示。" /></template>
      </el-table>
    </DataPanel>

    <DataPanel title="最近生产任务" description="最近 10 张生产单，状态和计划产量一目了然。" class="dashboard-panel">
      <template #actions><router-link class="panel-link" to="/orders">查看生产单</router-link></template>
      <el-table :data="orders" stripe row-key="id">
        <el-table-column prop="order_no" label="生产单号" min-width="175" />
        <el-table-column prop="product_name" label="成品" min-width="145" show-overflow-tooltip />
        <el-table-column label="计划数量" min-width="140"><template #default="{row}"><span class="data-value">{{ row.planned_quantity }} {{ row.unit }}</span></template></el-table-column>
        <el-table-column label="实际数量" min-width="140"><template #default="{row}">{{ row.actual_quantity == null ? '—' : `${row.actual_quantity} ${row.unit}` }}</template></el-table-column>
        <el-table-column label="状态" width="110"><template #default="{row}"><StatusBadge :tone="statusTone(row.status)" :label="statusNames[row.status] || row.status" /></template></el-table-column>
        <el-table-column label="创建时间" min-width="175"><template #default="{row}">{{ dateTime(row.created_at, data.timezone) }}</template></el-table-column>
        <template #empty><EmptyState title="暂无生产任务" description="创建生产单后将在这里显示。" /></template>
      </el-table>
    </DataPanel>
  </template>
</template>

<style scoped>
.dashboard-panel { margin-bottom: 18px; }
.dashboard-section-title { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin: 24px 0 13px; }
.dashboard-section-title h2 { margin: 0; font-size: 18px; font-weight: 670; }
.dashboard-section-title span { color: var(--wms-text-tertiary); font-size: 12px; }
.dashboard-section-title a, .panel-link { color: var(--wms-primary); font-size: 13px; font-weight: 600; text-decoration: none; }
.dashboard-stat-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
.dashboard-skeleton-card { min-height: 112px; padding: 18px; border-radius: var(--wms-radius-card); background: #fff; }
.dashboard-attention { margin-top: 14px; }
.dashboard-calm { display: flex; align-items: center; gap: 7px; margin: 13px 0 0; color: var(--wms-text-secondary); font-size: 12px; }
.dashboard-inventory-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 20px; }
.inventory-feedback { margin-bottom: 12px; }
.dashboard-today-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0; }
.dashboard-today-grid > div { display: flex; min-width: 0; flex-direction: column; gap: 9px; padding: 5px 16px; border-right: 1px solid var(--wms-border); }
.dashboard-today-grid > div:first-child { padding-left: 0; }
.dashboard-today-grid > div:last-child { border-right: 0; }
.dashboard-today-grid span { color: var(--wms-text-secondary); font-size: 12px; }
.dashboard-today-grid strong { font-size: 18px; font-variant-numeric: tabular-nums; font-weight: 670; }
@media (max-width: 1450px) { .dashboard-stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .dashboard-today-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px 0; } .dashboard-today-grid > div:nth-child(2) { border-right: 0; } .dashboard-today-grid > div:nth-child(3) { padding-left: 0; } }
</style>
