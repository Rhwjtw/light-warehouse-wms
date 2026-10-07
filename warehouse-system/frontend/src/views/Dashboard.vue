<script setup>
import { formatQuantity } from '../quantity'
import { computed, onMounted, ref } from 'vue'
import { Box, Download, Files, Finished, Goods, Plus, Refresh, Upload, Warning, ArrowRight } from '@element-plus/icons-vue'
import { api } from '../api'
import { dateTime, kindNames, statusNames } from '../display'
import DataPanel from '../components/ui/DataPanel.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import QuickActions from '../components/ui/QuickActions.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
import TableSkeleton from '../components/ui/TableSkeleton.vue'

const data = ref(null), transactions = ref([]), orders = ref([]), loading = ref(false), error = ref('')
const materialStock = ref([]), finishedStock = ref([]), stockLoading = ref(false)
const materialStockError = ref(''), finishedStockError = ref('')
const activeTab = ref('stock'), showAllOrders = ref(false)
const visibleOrders = computed(() => showAllOrders.value ? orders.value : orders.value.slice(0, 2))
const overview = computed(() => data.value ? [
  { label: '原材料种类', value: data.value.materials, unit: '种', to: '/materials', icon: Box },
  { label: '有库存原料批次', value: data.value.stocked_batches, unit: '批', to: '/batches', icon: Files },
  { label: '成品种类', value: data.value.products, unit: '种', to: '/finished-stock', icon: Goods },
  { label: '生产中生产单', value: data.value.in_progress_orders, unit: '单', to: '/orders', icon: Finished, primary: true },
] : [])
// 合并的是展示行，数量和单位保持接口原值，不在首页计算或混加库存。
const stockRows = computed(() => [
  ...materialStock.value.map(row => ({ ...row, rowKey: `material-${row.id}`, category: '原材料', quantity: row.remaining_quantity })),
  ...finishedStock.value.map(row => ({ ...row, rowKey: `finished-${row.id}`, category: '成品' })),
])
const actions = [
  { group: '仓库业务', to: '/material-receipts', label: '原料入库', icon: Download, permission: 'material.inventory.write' },
  { group: '仓库业务', to: '/material-outbounds', label: '原料出库', icon: Upload, permission: 'material.inventory.write' },
  { group: '仓库业务', to: '/finished-receipts', label: '成品入库', icon: Goods, permission: 'finished.inventory.write' },
  { group: '仓库业务', to: '/finished-outbounds', label: '成品出库', icon: Box, permission: 'finished.inventory.write' },
  { group: '生产业务', to: '/orders', label: '新建生产单', icon: Plus, permission: 'production.order.write', primary: true },
  { group: '车间用料', to: '/issues', label: '领料', icon: Files, permission: 'workshop.pick' },
  { group: '车间用料', to: '/supplements', label: '补料', icon: Finished, permission: 'workshop.replenish' },
  { group: '车间用料', to: '/returns', label: '退料', icon: Upload, permission: 'workshop.return' },
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
  return values.length ? values.map(row => `${formatQuantity(row.quantity)} ${row.unit}`).join(' / ') : '0'
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
  <div class="dashboard-home">
    <PageHeader class="dashboard-heading" title="仓库工作台" :description="data ? `${data.business_date} · 库存与生产概览` : '库存、生产和预警集中查看'">
      <template #actions><el-button :icon="Refresh" :loading="loading || stockLoading" @click="refresh">刷新数据</el-button></template>
    </PageHeader>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <div v-if="loading && !data" class="dashboard-stat-grid"><el-skeleton v-for="item in 4" :key="item" animated :rows="2" class="dashboard-skeleton-card" /></div>
    <div v-else-if="data" class="dashboard-stat-grid">
      <router-link v-for="item in overview" :key="item.label" :to="item.to" :class="['dashboard-stat', { 'dashboard-stat--primary': item.primary }]">
        <span class="dashboard-stat-label">{{ item.label }}<span class="dashboard-stat-icon"><el-icon><component :is="item.icon" /></el-icon></span></span>
        <strong class="dashboard-stat-value">{{ item.value }}<small>{{ item.unit }}</small></strong>
      </router-link>
    </div>
    <div v-if="data" :class="['dashboard-notice', { 'dashboard-notice--calm': !attention.length }]">
      <el-icon class="dashboard-notice-icon"><Warning /></el-icon>
      <div class="dashboard-notice-copy">
        <strong>{{ attention.length ? attention.join('；') : '当前没有过期、严重临期或低库存提醒' }}</strong>
        <p>{{ attention.length ? '关注批次余额、到期日期和最低库存，按业务需要处理。' : '可继续查看库存和生产任务。' }}</p>
      </div>
      <router-link class="dashboard-link" to="/expiry">查看预警 <el-icon><ArrowRight /></el-icon></router-link>
    </div>

    <div class="dashboard-work-grid">
      <section class="dashboard-task-panel" aria-labelledby="dashboard-task-title">
        <header class="dashboard-panel-head">
          <div class="dashboard-title-group"><h2 id="dashboard-task-title">生产任务</h2><span v-if="data" class="dashboard-count">生产中 {{ data.in_progress_orders }}</span></div>
          <router-link class="dashboard-link" to="/orders">查看全部 <el-icon><ArrowRight /></el-icon></router-link>
        </header>
        <TableSkeleton v-if="loading && !data" :rows="2" />
        <template v-else-if="data">
          <div class="dashboard-task-caption">最近 {{ orders.length }} 张生产单 · 按创建时间排列</div>
          <div v-for="order in visibleOrders" :key="order.id" class="dashboard-order">
            <span class="dashboard-order-icon"><el-icon><Files /></el-icon></span>
            <div class="dashboard-order-copy">
              <h3>{{ order.product_name }}</h3>
              <p>{{ order.order_no }} · 计划 {{ formatQuantity(order.planned_quantity) }} {{ order.unit }}</p>
              <p class="dashboard-order-meta">实际 {{ order.actual_quantity == null ? '—' : `${formatQuantity(order.actual_quantity)} ${order.unit}` }} · {{ dateTime(order.created_at, data.timezone) }}</p>
            </div>
            <div class="dashboard-order-actions">
              <StatusBadge :tone="statusTone(order.status)" :label="statusNames[order.status] || order.status" />
              <router-link class="dashboard-link" :to="{ path: '/orders', query: { order_id: order.id } }">查看生产单 <el-icon><ArrowRight /></el-icon></router-link>
            </div>
          </div>
          <EmptyState v-if="!orders.length" title="暂无生产任务" description="创建生产单后将在这里显示。" />
          <div v-if="orders.length > 2" class="dashboard-more"><button type="button" class="dashboard-more-button" :aria-expanded="showAllOrders" @click="showAllOrders = !showAllOrders">{{ showAllOrders ? '收起任务' : `展开最近 ${orders.length} 张生产单` }}</button></div>
          <div class="dashboard-alert-summary">
            <router-link v-for="item in alerts" :key="item.label" :to="item.to" class="dashboard-alert-item">
              <span>{{ item.label }}</span><strong :class="item.value > 0 ? `dashboard-tone--${item.tone || 'default'}` : ''">{{ item.value }}<small>{{ item.to === '/materials' ? '种' : '批' }}</small></strong>
            </router-link>
          </div>
          <p class="dashboard-task-note">{{ data.warning_expiry_days }} 天内到期包含 {{ data.urgent_expiry_days }} 天内到期批次；到期预警仅统计仍有库存的原料批次。</p>
        </template>
        <EmptyState v-else title="生产任务未加载" description="请刷新数据重试。" />
      </section>
      <DataPanel title="常用操作" class="dashboard-actions-panel"><QuickActions :actions="actions" /></DataPanel>
    </div>

    <DataPanel class="dashboard-data-panel">
      <template #actions><span class="dashboard-data-caption">{{ activeTab === 'stock' ? '原材料、成品各前 5 项 · 含零余额' : '最近 10 条库存流水' }}</span></template>
      <el-tabs v-model="activeTab" class="dashboard-tabs" aria-label="库存与流水">
        <el-tab-pane label="库存速览" name="stock">
          <div class="dashboard-stock-links"><router-link class="dashboard-link" to="/material-stock">原材料库存 <el-icon><ArrowRight /></el-icon></router-link><router-link class="dashboard-link" to="/finished-stock">成品库存 <el-icon><ArrowRight /></el-icon></router-link></div>
          <div class="dashboard-stock-errors"><el-alert v-if="materialStockError" :title="materialStockError" type="error" :closable="false" /><el-alert v-if="finishedStockError" :title="finishedStockError" type="error" :closable="false" /></div>
          <TableSkeleton v-if="stockLoading && !stockRows.length" :rows="3" />
          <el-table v-else :data="stockRows" row-key="rowKey">
            <el-table-column prop="name" label="物料 / 成品" min-width="170" show-overflow-tooltip />
            <el-table-column prop="code" label="编码" min-width="110" show-overflow-tooltip />
            <el-table-column prop="category" label="类别" width="100" />
            <el-table-column label="当前库存" min-width="140" align="right" show-overflow-tooltip><template #default="{row}"><strong class="data-value">{{ formatQuantity(row.quantity) }}</strong></template></el-table-column>
            <el-table-column prop="unit" label="单位" min-width="85" />
            <template #empty><EmptyState :title="materialStockError || finishedStockError ? '库存数据未加载' : '暂无库存档案'" description="可查看全部库存档案，或刷新重试。" /></template>
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="最近出入库" name="transactions">
          <template v-if="data">
            <div class="dashboard-today-grid" aria-label="今日出入库">
              <div><span>今日原材料入库</span><strong>{{ todayText('material_in') }}</strong></div>
              <div><span>今日原材料出库</span><strong>{{ todayText('material_out') }}</strong></div>
              <div><span>今日成品入库</span><strong>{{ todayText('finished_in') }}</strong></div>
              <div><span>今日成品出库</span><strong>{{ todayText('finished_out') }}</strong></div>
            </div>
            <p class="dashboard-ledger-note">按实际记账时间统计；原材料入含退料，出含领料与补料。不同单位分别显示。</p>
            <div class="dashboard-stock-links"><router-link class="dashboard-link" to="/material-transactions">查看原料流水 <el-icon><ArrowRight /></el-icon></router-link></div>
            <el-table :data="transactions" row-key="id">
              <el-table-column label="记账时间" min-width="175"><template #default="{row}">{{ dateTime(row.created_at, data.timezone) }}</template></el-table-column>
              <el-table-column prop="name" label="原材料 / 成品" min-width="145" show-overflow-tooltip />
              <el-table-column label="类型" width="125"><template #default="{row}"><StatusBadge :tone="transactionTone(row.kind)" :label="kindNames[row.kind] || row.kind" /></template></el-table-column>
              <el-table-column label="变动量" min-width="145" align="right" show-overflow-tooltip><template #default="{row}"><span class="data-value">{{ formatQuantity(row.change_quantity) }} {{ row.unit }}</span></template></el-table-column>
              <el-table-column prop="document_no" label="单据号" min-width="190" />
              <el-table-column prop="operator" label="操作人" min-width="110" />
              <template #empty><EmptyState title="暂无库存流水" description="完成入库、出库或生产用料后将在这里显示。" /></template>
            </el-table>
          </template>
          <TableSkeleton v-else-if="loading" />
          <EmptyState v-else title="流水未加载" description="请刷新数据重试。" />
        </el-tab-pane>
      </el-tabs>
    </DataPanel>
  </div>
</template>

<style scoped>
.dashboard-home { --wms-text: #172a42; --wms-text-secondary: #627187; --wms-text-tertiary: #758296; --wms-primary: #2563b8; --wms-primary-hover: #1c519c; --wms-primary-soft: #edf4fd; --wms-radius-card: 10px; --wms-shadow-card: none; --el-color-primary: var(--wms-primary); display: grid; gap: 16px; }
.dashboard-heading { margin-bottom: 2px; align-items: center; }
.dashboard-heading :deep(h1) { color: var(--wms-text); font-size: 26px; line-height: 1.3; letter-spacing: -.5px; }
.dashboard-heading :deep(p) { margin-top: 7px; font-size: 13px; }
.dashboard-home :deep(.panel-card + .panel-card) { margin-top: 0; }
.dashboard-home :deep(.el-card__header) { padding: 16px 20px; }
.dashboard-home :deep(.el-card__body) { padding: 18px 20px; }
.dashboard-home :deep(.panel-title) { color: var(--wms-text); font-size: 17px; }
.dashboard-stat-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
.dashboard-stat { min-width: 0; padding: 15px 20px; border: 1px solid var(--wms-border); border-radius: 10px; background: var(--wms-surface); text-decoration: none; transition: border-color 150ms ease; }
.dashboard-stat:hover { border-color: var(--wms-primary); }
.dashboard-stat-label { display: flex; align-items: center; justify-content: space-between; gap: 10px; color: var(--wms-text-secondary); font-size: 13px; }
.dashboard-stat-icon { display: grid; flex-shrink: 0; width: 35px; height: 35px; place-items: center; border-radius: 50%; color: var(--wms-primary); background: var(--wms-primary-soft); font-size: 20px; }
.dashboard-stat-value { display: flex; align-items: baseline; gap: 8px; margin-top: 4px; color: var(--wms-text); font-size: 30px; font-weight: 680; font-variant-numeric: tabular-nums; line-height: 1.2; }
.dashboard-stat-value small { color: var(--wms-text-secondary); font-size: 12px; font-weight: 400; }
.dashboard-stat--primary .dashboard-stat-value { color: var(--wms-primary); }
.dashboard-skeleton-card { padding: 20px; border-radius: 10px; background: var(--wms-surface); }
.dashboard-notice { display: flex; align-items: center; gap: 12px; padding: 13px 18px; border: 1px solid #f1d6ae; border-radius: 8px; background: #fff8ed; color: var(--wms-warning); }
.dashboard-notice-icon { flex-shrink: 0; font-size: 24px; }
.dashboard-notice-copy { min-width: 0; flex: 1; }
.dashboard-notice-copy strong { font-size: 14px; font-weight: 600; }
.dashboard-notice-copy p { margin: 4px 0 0; color: var(--wms-text-secondary); font-size: 12px; }
.dashboard-notice--calm { border-color: var(--wms-border); color: var(--wms-text-secondary); background: var(--wms-surface); }
.dashboard-link { display: inline-flex; align-items: center; gap: 6px; color: var(--wms-primary); font-size: 13px; font-weight: 500; text-decoration: none; white-space: nowrap; }
.dashboard-link:hover { text-decoration: underline; }
.dashboard-link:focus-visible, .dashboard-stat:focus-visible, .dashboard-alert-item:focus-visible { outline: 2px solid var(--wms-primary); outline-offset: 3px; }
.dashboard-work-grid { display: grid; grid-template-columns: minmax(0, 2fr) minmax(285px, 1fr); gap: 16px; align-items: start; }
.dashboard-task-panel { min-width: 0; overflow: hidden; border: 1px solid var(--wms-border); border-radius: 10px; background: var(--wms-surface); }
.dashboard-panel-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 16px 20px; }
.dashboard-title-group { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.dashboard-title-group h2 { margin: 0; color: var(--wms-text); font-size: 20px; font-weight: 650; }
.dashboard-count { padding: 3px 10px; border-radius: 14px; color: var(--wms-primary); background: var(--wms-primary-soft); font-size: 12px; }
.dashboard-task-caption { margin: -5px 20px 12px; color: var(--wms-text-tertiary); font-size: 12px; }
.dashboard-order { display: flex; align-items: center; gap: 13px; margin: 0 20px 10px; padding: 14px; border: 1px solid var(--wms-border); border-radius: 7px; }
.dashboard-order-icon { display: grid; width: 35px; height: 35px; flex-shrink: 0; place-items: center; color: var(--wms-primary); background: var(--wms-primary-soft); border-radius: 50%; font-size: 19px; }
.dashboard-order-copy { min-width: 0; flex: 1; overflow-wrap: anywhere; }
.dashboard-order-copy h3 { margin: 0; color: var(--wms-text); font-size: 14px; font-weight: 600; }
.dashboard-order-copy p { margin: 5px 0 0; color: var(--wms-text-secondary); font-size: 12px; }
.dashboard-order-copy .dashboard-order-meta { color: var(--wms-text-tertiary); font-size: 11px; }
.dashboard-order-actions { display: flex; flex-shrink: 0; align-items: flex-end; flex-direction: column; gap: 9px; }
.dashboard-more { padding: 0 20px 8px; text-align: center; }
.dashboard-more-button { padding: 4px 8px; border: 0; background: transparent; color: var(--wms-primary); font: inherit; font-size: 12px; cursor: pointer; }
.dashboard-more-button:hover { text-decoration: underline; }
.dashboard-more-button:focus-visible { outline: 2px solid var(--wms-primary); outline-offset: 2px; }
.dashboard-alert-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 18px 20px 0; padding: 15px 0 12px; border-top: 1px solid var(--wms-border); }
.dashboard-alert-item { display: flex; min-width: 0; flex-direction: column; gap: 6px; padding: 0 14px; border-right: 1px solid var(--wms-border); text-decoration: none; }
.dashboard-alert-item:first-child { padding-left: 0; }
.dashboard-alert-item:last-child { border-right: 0; }
.dashboard-alert-item > span { color: var(--wms-text-secondary); font-size: 12px; }
.dashboard-alert-item strong { color: var(--wms-text); font-size: 22px; font-weight: 650; font-variant-numeric: tabular-nums; }
.dashboard-alert-item small { margin-left: 6px; color: var(--wms-text-secondary); font-size: 12px; font-weight: 400; }
.dashboard-alert-item .dashboard-tone--danger { color: var(--wms-danger); }
.dashboard-alert-item .dashboard-tone--warning { color: var(--wms-warning); }
.dashboard-task-note { margin: 0; padding: 0 20px 16px; color: var(--wms-text-tertiary); font-size: 11px; line-height: 1.6; }
.dashboard-actions-panel { height: 100%; }
.dashboard-data-panel { position: relative; }
.dashboard-data-panel :deep(.el-card__header) { position: absolute; top: 19px; right: 20px; max-width: 48%; padding: 0; border: 0; z-index: 1; pointer-events: none; }
.dashboard-data-caption { color: var(--wms-text-tertiary); font-size: 12px; }
.dashboard-tabs :deep(.el-tabs__header) { margin-bottom: 14px; }
.dashboard-tabs :deep(.el-tabs__item) { font-size: 14px; font-weight: 600; }
.dashboard-stock-links { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 18px; margin-bottom: 12px; }
.dashboard-stock-errors { display: grid; gap: 10px; }
.dashboard-stock-errors:has(> *) { margin-bottom: 12px; }
.dashboard-today-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); padding: 8px 0 12px; }
.dashboard-today-grid > div { display: flex; min-width: 0; flex-direction: column; gap: 8px; padding: 0 16px; border-right: 1px solid var(--wms-border); }
.dashboard-today-grid > div:first-child { padding-left: 0; }
.dashboard-today-grid > div:last-child { border-right: 0; }
.dashboard-today-grid span { color: var(--wms-text-secondary); font-size: 12px; }
.dashboard-today-grid strong { color: var(--wms-text); font-size: 17px; font-variant-numeric: tabular-nums; font-weight: 600; overflow-wrap: anywhere; }
.dashboard-ledger-note { margin: 0 0 14px; color: var(--wms-text-tertiary); font-size: 12px; }
@media (max-width: 1450px) { .dashboard-home { gap: 14px; } .dashboard-stat { padding: 15px; } .dashboard-stat-label { font-size: 12px; } .dashboard-order { gap: 10px; padding: 12px; } .dashboard-order-icon { display: none; } }
@media (max-width: 1180px) { .dashboard-work-grid { grid-template-columns: minmax(0, 1fr); } .dashboard-actions-panel { height: auto; } .dashboard-alert-summary { margin-top: 14px; } .dashboard-today-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px 0; } .dashboard-today-grid > div:nth-child(2) { border-right: 0; } .dashboard-today-grid > div:nth-child(3) { padding-left: 0; } }
</style>
