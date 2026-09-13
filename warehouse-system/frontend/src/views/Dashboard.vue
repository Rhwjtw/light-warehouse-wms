<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const loading = ref(false)
const backend = ref(null)
const database = ref(null)
const failures = ref([])
const checkedAt = ref('尚未检查')
const counts = ref({})
async function refresh() {
  loading.value = true
  failures.value = []
  backend.value = null
  database.value = null
  counts.value = {}
  const results = await Promise.allSettled([api.get('/health'), api.get('/health/db'), api.get('/materials', { params: { page_size: 1 } }), api.get('/products', { params: { page_size: 1 } })])
  if (results[0].status === 'fulfilled') backend.value = results[0].value.data
  else failures.value.push(results[0].reason.message)
  if (results[1].status === 'fulfilled') database.value = results[1].value.data
  else failures.value.push(results[1].reason.message)
  if (results[2].status === 'fulfilled') counts.value['原材料种类'] = results[2].value.data.total
  else failures.value.push(results[2].reason.message)
  if (results[3].status === 'fulfilled') counts.value['成品种类'] = results[3].value.data.total
  else failures.value.push(results[3].reason.message)
  checkedAt.value = new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
  loading.value = false
}
const metrics = ['原材料种类', '成品种类', '当前生产单', '今日入库', '今日出库', '临期批次', '过期批次', '库存不足']
onMounted(refresh)
</script>

<template>
  <div class="page-heading"><div><h1>仓库工作台</h1><p>库存清楚，生产有据。仓库与车间的日常工作从这里开始。</p></div><el-button :loading="loading" @click="refresh">刷新连接状态</el-button></div>
  <el-alert title="原材料库存已开放" description="可以办理原料入出库、查询批次和库存流水。成品库存、生产业务与完整预警看板将在后续阶段开放。" type="info" show-icon :closable="false" />
  <div class="status-grid">
    <el-card shadow="never"><div class="card-label">API 服务 <el-tag :type="backend ? 'success' : 'warning'">{{ loading ? '检查中' : backend ? '运行正常' : '未连接' }}</el-tag></div><h2>FastAPI <small>{{ backend?.version || '—' }}</small></h2><p>统一接口 · UTC 时间 · Swagger 文档</p></el-card>
    <el-card shadow="never"><div class="card-label">数据库 <el-tag :type="database ? 'success' : 'warning'">{{ loading ? '检查中' : database ? '连接正常' : '待检查' }}</el-tag></div><h2>MySQL <small>{{ database?.version || '—' }}</small></h2><p>迁移版本：{{ database?.migration || '尚未验证' }}</p></el-card>
    <el-card shadow="never"><div class="card-label">开发进度 <el-tag type="info">3 / 9 阶段</el-tag></div><h2>原材料库存</h2><p>下一阶段：成品入库、出库与库存流水</p></el-card>
  </div>
  <el-alert v-for="message in failures" :key="message" :title="message" type="error" show-icon :closable="false" class="error-alert" />
  <div class="section-heading"><h3>业务概览</h3><span>库存与生产统计尚未开放</span></div>
  <div class="metrics"><el-card v-for="label in metrics" :key="label" shadow="never"><div class="metric-label">{{ label }}</div><div class="metric-value">{{ counts[label] ?? '—' }}</div><small>{{ counts[label] !== undefined ? '当前档案数量' : '待接入' }}</small></el-card></div>
  <div class="recent-grid">
    <el-card shadow="never"><template #header><strong>库存流水</strong></template><p>原材料流水已可查询，首页最近流水卡片将在完整看板阶段接入。</p><el-button @click="$router.push('/material-transactions')">查看原材料流水</el-button></el-card>
    <el-card shadow="never"><template #header><strong>最近生产单</strong></template><el-empty :image-size="70" description="生产单将在第六阶段开放" /></el-card>
  </div>
  <div class="page-footer"><span>最后检查：{{ checkedAt }}（北京时间）</span><a href="http://127.0.0.1:8000/docs" target="_blank" rel="noopener">打开 API 文档 ↗</a></div>
</template>
