<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import MobileReadFields from './MobileReadFields.vue'
import { readGate, safeReadOrigin, scopeLabel } from './readQueries'
const route = useRoute(), detail = ref(null), batches = ref([]), archive = ref(null), archiveError = ref(''), loading = ref(false), error = ref(''), search = ref(''), gate = readGate()
const relatedArchives = ref([])
const origin = computed(() => safeReadOrigin(route.query.from, '/m/records'))
const matchingBatches = computed(() => batches.value.filter(row => (row.batch_no || '历史无批次归属库存').includes(search.value.trim())))
async function load() {
  const ticket = gate.begin(), { kind, id } = route.params
  loading.value = true; error.value = ''; detail.value = null; batches.value = []; archive.value = null; archiveError.value = ''; relatedArchives.value = []
  try {
    const endpoint = kind === 'batch' ? `/material-location-stock/${id}` : kind === 'finished' ? `/finished-stock/${id}` : kind === 'receipt' ? `/material-receipts/${id}` : `/stock-documents/${id}`
    const row = (await api.get(endpoint)).data
    if (!gate.current(ticket)) return
    detail.value = row
    if (kind === 'finished') {
      const result = (await api.get(`/finished-stock/${id}/batches`)).data
      if (gate.current(ticket)) batches.value = result
    }
    if (kind === 'batch' || kind === 'receipt') {
      // 批次接口无档案状态字段，独立读取原档案；失败明确显示，不推测为启用。
      try { const record = (await api.get(`/materials/${row.material_id}`)).data; if (gate.current(ticket)) archive.value = record }
      catch (e) { if (gate.current(ticket)) archiveError.value = e.message }
      for (const [resource, id, label] of [['suppliers', row.supplier_id, '供应商'], ['locations', row.location_id, '库位']]) {
        if (!id || !gate.current(ticket)) continue
        try { const record = (await api.get(`/${resource}/${id}`)).data; if (gate.current(ticket)) relatedArchives.value.push({ label, record }) }
        catch (e) { if (gate.current(ticket)) relatedArchives.value.push({ label, error: e.message }) }
      }
    }
    if (kind === 'count') {
      let page = 1, found = false
      do {
        const result = (await api.get('/material-counts', { params: { batch_id: route.query.batch_id, page, page_size: 100 } })).data
        if (!gate.current(ticket)) return
        const count = result.items.find(item => String(item.document_id) === id)
        if (count) { detail.value = { ...row, ...count, id: row.id }; found = true; break }
        if (page * 100 >= result.total) break
        page++
      } while (true)
      if (!found) throw new Error('盘点明细未查到，请重新查询原记录')
    }
  } catch (e) { if (gate.current(ticket)) { detail.value = null; batches.value = []; error.value = e.message } }
  finally { if (gate.current(ticket)) loading.value = false }
}
watch(() => route.fullPath, load, { immediate: true })
onMounted(() => window.addEventListener('wms-mobile-resume', load))
onBeforeUnmount(() => { gate.cancel(); window.removeEventListener('wms-mobile-resume', load) })
</script>
<template>
  <section class="mobile-card"><span class="mobile-pill">只读详情</span><p class="mobile-muted mobile-small">本页不记账。查询记录只能辅助核对，不能确定某个请求键的提交结果。</p><button class="mobile-button" :disabled="loading" @click="load">刷新详情</button></section>
  <p v-if="loading" class="mobile-status" role="status">正在读取详情…</p><section v-else-if="error" class="mobile-card"><p class="mobile-error" role="alert">读取失败：{{ error }}</p><button class="mobile-button primary" @click="load">重试详情</button></section>
  <template v-else-if="detail">
    <section class="mobile-card"><h2>{{ detail.material_name || detail.name || detail.document_no }}</h2><MobileReadFields :record="detail" /><p v-if="archive?.is_active === false" class="mobile-warning">原料档案已停用</p><p v-if="archiveError" class="mobile-warning">原料档案状态未核实：{{ archiveError }}</p><p v-for="related in relatedArchives" :key="related.label" :class="related.error || related.record?.is_active === false ? 'mobile-warning' : 'mobile-muted mobile-small'">{{ related.label }}{{ related.error ? '状态未核实：' + related.error : related.record.name + ' · ' + (related.record.is_active ? '启用' : '已停用') }}</p></section>
    <template v-if="route.params.kind === 'finished'">
      <section class="mobile-card"><h2>该成品全部批次</h2><label>在本成品内查批号<input v-model="search" class="mobile-batch-search" type="search" maxlength="80" /></label><p class="mobile-muted mobile-small">真实批次与历史无批次库存分别显示；历史余额不推测生产归属。</p><p v-if="!matchingBatches.length" class="mobile-empty">没有匹配的库存批次</p></section>
      <section v-for="batch in matchingBatches" :key="batch.id" class="mobile-card"><h2>{{ scopeLabel(batch.stock_scope, batch.batch_no) }}</h2><MobileReadFields :record="batch" /></section>
      <router-link :to="{ path: '/m/records', query: { view: 'finished', product_id: String(detail.id) } }" class="mobile-button">查看本成品全部流水</router-link>
    </template>
    <template v-if="route.params.kind === 'receipt'">
      <p class="mobile-warning">{{ detail.status === 'reversed' ? '本入库已冲销，不能继续上架。' : detail.status === 'shelved' ? '本记录已全部上架。' : '待上架量为当前查询值；进入作业后重新核实。' }}</p>
      <router-link v-if="!['reversed', 'shelved'].includes(detail.status)" :to="{ path: '/m/jobs/putaway', query: { receipt_id: String(detail.id), movement_id: String(detail.receipt_movement_id), from: origin } }" class="mobile-button">手动上架此明细</router-link><section v-for="allocation in detail.allocations" :key="allocation.id" class="mobile-card"><h2>上架分配 {{ allocation.id }} {{ allocation.reversed_by_document_id ? '（已冲销）' : '' }}</h2><MobileReadFields :record="{ ...allocation, unit: detail.unit }" /><router-link :to="{ path: '/m/read/document/' + allocation.document_id, query: { from: origin } }" class="mobile-button">查看上架单据</router-link></section>
      <router-link :to="{ path: '/m/read/document/' + detail.id, query: { from: origin } }" class="mobile-button">查看原入库单据与流水</router-link>
    </template>
    <template v-if="route.params.kind === 'batch'"><router-link :to="{ path: '/m/jobs/material-transfer', query: { batch_id: String(detail.id), from: origin } }" class="mobile-button">移库此库位批次</router-link><router-link :to="{ path: '/m/jobs/material-count', query: { batch_id: String(detail.id), from: origin } }" class="mobile-button">盘点此库位批次</router-link><router-link :to="{ path: '/m/records', query: { view: 'material', batch_id: String(detail.id) } }" class="mobile-button">查看本批次全部流水</router-link><p class="mobile-muted mobile-small">批次 ID 区分库位及供应商；累计入库保持历史口径，不能当作本次入库量。</p></template>
    <template v-if="['document', 'count'].includes(route.params.kind)">
      <section v-if="detail.original || detail.reversal" class="mobile-card"><h2>关联单据</h2><router-link v-for="linked in [detail.original, detail.reversal].filter(Boolean)" :key="linked.id" :to="{ path: '/m/read/document/' + linked.id, query: { from: origin } }" class="mobile-button">{{ linked.document_no }}</router-link><p v-if="detail.reversal?.reason">冲销原因 {{ detail.reversal.reason }}</p></section>
      <section v-for="transaction in detail.transactions" :key="transaction.id" class="mobile-card"><h2>流水 {{ transaction.id }}</h2><MobileReadFields :record="transaction" /></section><p v-if="!detail.transactions?.length" class="mobile-muted">本单无库存变动流水；零差盘点仍保留盘点事实。</p>
    </template>
  </template>
</template>
