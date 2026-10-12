<script setup>
import { computed, onMounted, ref } from 'vue'
import { Box, Download, List, Search, Switch, Upload } from '@element-plus/icons-vue'
import { auth } from '../auth'
import { api } from '../api'
import { canOperate } from '../permissions'
import { dateTime, kindNames } from '../display'
import { formatQuantity } from '../quantity'
import { visibleMobileActions } from './routes'
const icons = { Box, Download, List, Search, Switch, Upload }
const actions = computed(() => visibleMobileActions(auth.user?.role, canOperate))
const summary = ref(null), transactions = ref([]), loading = ref(false)
const summaryError = ref(''), recordsError = ref('')
async function load() {
  if (loading.value) return
  loading.value = true; summaryError.value = ''; recordsError.value = ''
  // 独立展示两项读取结果；读取失败不冒充零库存或空记录，也不产生库存写入。
  const results = await Promise.allSettled([api.get('/dashboard/summary'), api.get('/dashboard/recent-transactions')])
  if (results[0].status === 'fulfilled') summary.value = results[0].value.data
  else { summary.value = null; summaryError.value = results[0].reason.message }
  if (results[1].status === 'fulfilled') transactions.value = results[1].value.data.items.slice(0, 3)
  else { transactions.value = []; recordsError.value = results[1].reason.message }
  loading.value = false
}
onMounted(load)
</script>

<template>
  <section class="mobile-greeting"><div><p class="mobile-muted">{{ auth.user?.role_name }}</p><h2>你好，{{ auth.user?.display_name }}</h2><p class="mobile-muted">{{ summary?.business_date || '仓库现场工作台' }}</p></div><button class="mobile-button compact" :disabled="loading" @click="load">{{ loading ? '加载中…' : '刷新' }}</button></section>
  <section class="mobile-card"><h2>常用入口</h2><div class="mobile-actions"><router-link v-for="action in actions" :key="action.key" :to="action.path"><component :is="icons[action.icon]" /><strong>{{ action.label }}</strong><span>{{ { inventory: '查看库存与批次', receipt: '原料 / 成品', outbound: '原料 / 成品', putaway: '入库后分配库位', transfer: '库位间调拨', count: '核对实际数量' }[action.key] }}</span></router-link></div><p v-if="actions.length === 1" class="mobile-muted mobile-small">当前账号可查询库存。</p></section>
  <p v-if="loading" class="mobile-status" role="status">正在读取工作台数据…</p>
  <section v-else class="mobile-card"><h2>仓库预警</h2><div v-if="summaryError" class="mobile-error" role="alert">{{ summaryError }}<button class="mobile-button" @click="load">重新读取</button></div><template v-else-if="summary"><dl class="mobile-metrics"><div><dt>已过期批次</dt><dd>{{ summary.expired_batches }}<small> 批</small></dd></div><div><dt>{{ summary.urgent_expiry_days }} 天内到期</dt><dd>{{ summary.urgent_batches }}<small> 批</small></dd></div><div><dt>{{ summary.warning_expiry_days }} 天内到期</dt><dd>{{ summary.warning_batches }}<small> 批</small></dd></div><div><dt>低库存原料</dt><dd>{{ summary.low_stock_materials }}<small> 种</small></dd></div></dl><details class="mobile-more"><summary>预警口径</summary><p class="mobile-muted mobile-small">到期预警仅统计有库存的已上架原料；一般临期包含严重临期。</p></details></template></section>
  <section v-if="!loading" class="mobile-card"><h2>最近库存操作</h2><div v-if="recordsError" class="mobile-error" role="alert">{{ recordsError }}<button class="mobile-button" @click="load">重新读取</button></div><p v-else-if="!transactions.length" class="mobile-empty">暂无库存操作记录</p><ul v-else class="mobile-recent"><li v-for="row in transactions" :key="row.id"><div><strong>{{ kindNames[row.kind] || row.kind }}</strong><span>{{ formatQuantity(row.change_quantity) }} {{ row.unit }}</span></div><p>{{ row.name }} · {{ row.document_no }}</p><time>{{ dateTime(row.created_at) }}</time></li></ul></section>
</template>
