<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Search } from '@element-plus/icons-vue'
import { auth } from '../auth'
import { canOperate } from '../permissions'
import { api } from '../api'
import { mobileStore } from './session'
import { returnToParent } from './navigation'
import { detailLocation, inventoryViews, recordViews, readCriteria, queryRequest, readGate, receiptStatuses, rowKey, rowTitle, quantityText, scopeLabel, safeReadOrigin, kindLabels } from './readQueries'
const materialWrite = computed(() => canOperate(auth.user?.role, 'material.inventory.write'))
const finishedWrite = computed(() => canOperate(auth.user?.role, 'finished.inventory.write'))
const route = useRoute(), router = useRouter()
const directoryViews = { directory: { label: '库位', endpoint: '/locations', search: '库位名称' } }
const views = computed(() => route.path === '/m/records' ? recordViews : route.path === '/m/locations' ? directoryViews : route.path === '/m/pending' ? { receipts: recordViews.receipts } : inventoryViews)
const criteria = computed(() => readCriteria(route.query, views.value, Object.keys(views.value)[0]))
const config = computed(() => views.value[criteria.value.view])
const search = ref(''), status = ref('open'), items = ref([]), total = ref(0), loading = ref(false), error = ref(''), refreshed = ref('')
const filtering = computed(() => route.query.panel === 'filter'), pages = computed(() => Math.max(1, Math.ceil(total.value / 20))), gate = readGate()
const activeCriteria = computed(() => ({ ...criteria.value, status: route.path === '/m/pending' ? criteria.value.status || 'open' : criteria.value.status }))
const requestKey = computed(() => JSON.stringify({ path: route.path, ...queryRequest(activeCriteria.value, views.value) }))
const location = ref(null), locationError = ref('')
async function restorePosition(path, ticket) {
  await nextTick(); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  if (!gate.current(ticket) || route.fullPath !== path) return
  const position = mobileStore()?.list(path), anchor = position?.anchor && document.getElementById(position.anchor)
  window.scrollTo({ top: anchor ? window.scrollY + anchor.getBoundingClientRect().top - 80 : position?.top || 0 })
}
async function load(restore = false) {
  const ticket = gate.begin(), path = route.fullPath, request = queryRequest(activeCriteria.value, views.value)
  loading.value = true; error.value = ''; items.value = []; total.value = 0; location.value = null; locationError.value = ''; refreshed.value = ''
  try {
    const result = (await api.get(request.endpoint, { params: request.params })).data
    if (!gate.current(ticket)) return
    items.value = result.items; total.value = result.total
    if (criteria.value.page > pages.value) { change({ page: String(pages.value) }); return }
    refreshed.value = new Date().toLocaleTimeString('zh-CN')
    if (request.params.location_id) {
      try { const found = (await api.get(`/locations/${request.params.location_id}`)).data; if (gate.current(ticket)) location.value = found }
      catch (e) { if (gate.current(ticket)) locationError.value = e.message }
    }
    loading.value = false
    if (restore) await restorePosition(path, ticket)
  } catch (e) { if (gate.current(ticket)) error.value = e.message }
  finally { if (gate.current(ticket)) loading.value = false }
}
function change(values) {
  const query = { ...route.query, ...values }; delete query.panel
  router.replace({ path: route.path, query })
}
function switchView(view) { router.replace({ path: route.path, query: { view } }) }
function apply() { change({ q: search.value || undefined, status: status.value || undefined, page: '1' }) }
function openFilter() { status.value = activeCriteria.value.status || ''; router.push({ path: route.path, query: { ...route.query, panel: 'filter' } }) }
function closeFilter() { const query = { ...route.query }; delete query.panel; returnToParent(router, router.resolve({ path: route.path, query }).fullPath) }
function open(row) {
  if (route.path === '/m/locations') {
    router.push({ path: safeReadOrigin(route.query.from).split('?')[0], query: { ...router.resolve(safeReadOrigin(route.query.from)).query, view: 'locations', location_id: String(row.id), page: '1' } }); return
  }
  if (criteria.value.view === 'counts') router.push({ path: `/m/read/count/${row.document_id}`, query: { from: route.fullPath, batch_id: String(row.batch_id) } })
  else if (route.path === '/m/records' && criteria.value.view === 'material') router.push({ path: `/m/read/document/${row.document_id}`, query: { from: route.fullPath } })
  else router.push(detailLocation(criteria.value.view, row, route.fullPath))
}
function refresh() {
  if (!loading.value && !error.value) {
    const anchor = [...document.querySelectorAll('[data-mobile-anchor]')].find(element => element.getBoundingClientRect().bottom > 80)
    mobileStore()?.saveList(route.fullPath, { top: window.scrollY, anchor: anchor?.id })
  }
  return load(true)
}
function resume() { refresh() }
watch(requestKey, () => { search.value = criteria.value.q; status.value = activeCriteria.value.status || ''; load(true) }, { immediate: true })
onMounted(() => window.addEventListener('wms-mobile-resume', resume))
onBeforeUnmount(() => { gate.cancel(); window.removeEventListener('wms-mobile-resume', resume) })
</script>

<template>
  <section class="mobile-card">
    <div class="mobile-section-heading"><h2>{{ route.path === '/m/pending' ? '待上架明细' : config.label }}</h2><button class="mobile-link-button" :disabled="loading" @click="refresh">刷新</button></div>
    <div v-if="Object.keys(views).length > 1" :class="['mobile-view-tabs', { 'mobile-record-tabs': route.path === '/m/records' }]" aria-label="查询范围"><button v-for="(view, key) in views" :key="key" :class="{ active: criteria.view === key }" :aria-pressed="criteria.view === key" @click="switchView(key)">{{ view.label }}</button></div>
    <form v-if="config.search" class="mobile-search" @submit.prevent="apply"><Search /><input v-model="search" type="search" :placeholder="config.search" :aria-label="config.search" :maxlength="criteria.view === 'locations' ? 80 : 120" /><button class="mobile-button" :disabled="loading">搜索</button></form>
    <button v-if="criteria.view === 'receipts'" class="mobile-button" @click="openFilter">状态：{{ receiptStatuses[activeCriteria.status] || '全部' }}</button>
    <div v-if="filtering && criteria.view === 'receipts'" role="dialog" aria-label="入库状态筛选" class="mobile-filter-panel"><label>入库状态<select v-model="status" aria-label="入库状态"><option v-if="route.path !== '/m/pending'" value="">全部</option><option v-for="(label, key) in receiptStatuses" :key="key" :value="key">{{ label }}</option></select></label><button class="mobile-button primary" @click="apply">应用筛选</button><button class="mobile-button" @click="closeFilter">取消筛选</button></div>
    <template v-if="criteria.view === 'locations'"><router-link :to="{ path: '/m/locations', query: { from: route.fullPath } }" class="mobile-button">按名称查找库位</router-link><p v-if="criteria.location_id" class="mobile-muted">库位：{{ location?.name || 'ID ' + criteria.location_id }} {{ location?.is_active === false ? '（已停用）' : '' }}</p><p v-if="locationError" class="mobile-error">库位信息读取失败：{{ locationError }}</p><button v-if="criteria.location_id" class="mobile-button" @click="change({ location_id: undefined, page: '1' })">清除库位筛选</button></template>
    <button v-if="criteria.material_id" class="mobile-button" @click="change({ material_id: undefined, page: '1' })">清除原料 ID {{ criteria.material_id }} 筛选</button>
    <p v-if="criteria.batch_id || criteria.product_id" class="mobile-muted mobile-small">关联{{ criteria.batch_id ? '原料批次 ID ' + criteria.batch_id : '成品 ID ' + criteria.product_id }}</p>
    <div class="mobile-row"><router-link v-if="route.path === '/m/inventory'" to="/m/pending" class="mobile-text-link">待上架记录</router-link><router-link v-if="materialWrite && (route.path === '/m/pending' || (route.path === '/m/records' && criteria.view === 'receipts'))" :to="{ path: '/m/jobs/receipt', query: { from: route.fullPath } }" class="mobile-text-link">新建原料入库</router-link><router-link v-if="route.path === '/m/inventory' && (criteria.view === 'finished' ? finishedWrite : materialWrite)" :to="{ path: criteria.view === 'finished' ? '/m/jobs/finished-outbound' : '/m/jobs/material-outbound', query: { from: route.fullPath } }" class="mobile-text-link">办理出库</router-link><router-link v-if="finishedWrite && route.path === '/m/records' && ['finishedReceipts', 'finishedOutbounds'].includes(criteria.view)" :to="{ path: criteria.view === 'finishedReceipts' ? '/m/jobs/finished-receipt' : '/m/jobs/finished-outbound', query: { from: route.fullPath } }" class="mobile-text-link">办理成品{{ criteria.view === 'finishedReceipts' ? '入库' : '出库' }}</router-link></div>
    <p v-if="refreshed" class="mobile-muted mobile-small">读取时间 {{ refreshed }}</p>
  </section>
  <p v-if="loading" class="mobile-status" role="status">正在读取…</p><section v-else-if="error" class="mobile-card"><p class="mobile-error" role="alert">查询失败：{{ error }}</p><button class="mobile-button primary" @click="load(true)">重试查询</button></section>
  <template v-else>
    <p v-if="!items.length" class="mobile-empty">没有匹配的记录</p>
    <section v-for="row in items" :id="rowKey(criteria.view, row)" :key="rowKey(criteria.view, row)" class="mobile-card" data-mobile-anchor>
      <h2>{{ rowTitle(row) }}</h2><p class="mobile-muted">{{ row.material_code || row.product_code || row.code }} <span v-if="row.document_no"> · {{ row.document_no }}</span></p>
      <p v-if="config.quantity" class="mobile-quantity">{{ quantityText(row[config.quantity], row.unit) }}</p>
      <p v-if="row.stock_scope !== undefined">{{ scopeLabel(row.stock_scope, row.batch_no) }}</p><p v-else-if="row.batch_no">批号 {{ row.batch_no }}</p>
      <p v-if="row.location_name">库位 {{ row.location_name }}</p><p v-if="row.supplier_name">供应商 {{ row.supplier_name }}</p>
      <p v-if="row.production_date || row.expiry_date" class="mobile-small">生产 {{ row.production_date || '—' }} / 到期 {{ row.expiry_date || '—' }}</p>
      <p v-if="row.expired" class="mobile-warning">已过期；查询可见不代表可正常出库</p><p v-if="row.is_active === false" class="mobile-warning">档案已停用</p>
      <p v-if="row.receipt_movement_id">入库单 ID {{ row.id }} · 明细 ID {{ row.receipt_movement_id }}</p><p v-if="row.pending_quantity !== undefined">待上架 {{ quantityText(row.pending_quantity, row.unit) }} · {{ receiptStatuses[row.status] }}</p>
      <router-link v-if="materialWrite && criteria.view === 'receipts' && ['pending', 'partial'].includes(row.status)" :to="{ path: '/m/jobs/putaway', query: { receipt_id: String(row.id), movement_id: String(row.receipt_movement_id), from: route.fullPath } }" class="mobile-button">上架此明细</router-link><p v-if="row.kind">{{ kindLabels[row.kind] || row.kind }} <span v-if="row.reversed_by_document_id">· 已冲销</span></p>
      <p v-if="row.material_outbound_purpose">用途 {{ { normal: '普通出库', supplier_return: '供应商退货', scrap: '报废' }[row.material_outbound_purpose] }}</p>
      <p v-if="row.source_location_name || row.target_location_name">{{ row.source_location_name || '无来源库位' }} → {{ row.target_location_name || '无目标库位' }}</p>
      <p v-if="row.before_quantity !== undefined" class="mobile-small">变动前 {{ quantityText(row.before_quantity, row.unit) }} → 变动后 {{ quantityText(row.after_quantity, row.unit) }}</p>
      <p v-if="row.book_quantity !== undefined" class="mobile-small">账面 {{ quantityText(row.book_quantity, row.unit) }} / 实盘 {{ quantityText(row.actual_quantity, row.unit) }}</p>
      <p class="mobile-muted mobile-small">{{ row.created_at || row.counted_at }} {{ row.operator_name || row.counted_by_name }}</p>
      <button class="mobile-button" @click="open(row)">{{ route.path === '/m/locations' ? '查看此库位库存' : criteria.view === 'material' && route.path !== '/m/records' ? '查看原料批次' : '查看详情' }}</button>
    </section>
    <section class="mobile-card" data-mobile-list-ready><p>共 {{ total }} 条 · 第 {{ criteria.page }} / {{ pages }} 页</p><div class="mobile-row"><button class="mobile-button" :disabled="criteria.page <= 1" @click="change({ page: String(criteria.page - 1) })">上一页</button><button class="mobile-button" :disabled="criteria.page >= pages" @click="change({ page: String(criteria.page + 1) })">下一页</button></div></section>
  </template>
</template>
