<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { listQuery, returnToParent } from './navigation'
import { mobileRevision, mobileStore, touchMobile } from './session'
import { taskLabels } from './taskState'
const route = useRoute(), router = useRouter()
const criteria = computed(() => listQuery(route.query)), search = ref(criteria.value.q), filtering = computed(() => route.query.panel === 'filter'), group = ref(criteria.value.group)
watch(() => route.fullPath, () => { search.value = criteria.value.q; group.value = criteria.value.group })
const records = Array.from({ length: 36 }, (_, index) => ({ id: `S2-${index + 1}`, name: `演练记录 ${String(index + 1).padStart(2, '0')}`, group: index % 2 ? 'B' : 'A', unit: 'kg' }))
const matches = computed(() => records.filter(record => record.name.includes(criteria.value.q) && (criteria.value.group === 'all' || record.group === criteria.value.group)))
const pages = computed(() => Math.max(1, Math.ceil(matches.value.length / 12)))
const page = computed(() => Math.min(criteria.value.page, pages.value))
const rows = computed(() => matches.value.slice((page.value - 1) * 12, page.value * 12))
const tasks = computed(() => { mobileRevision.value; return mobileStore()?.tasks() || [] })
const warning = computed(() => { mobileRevision.value; return mobileStore()?.warning || '' })
function apply(nextPage = 1) {
  router.replace({ path: '/m/state-lab', query: { q: search.value || undefined, group: group.value === 'all' ? undefined : group.value, page: String(nextPage) } })
}
function openFilter() { router.push({ path: route.path, query: { ...route.query, panel: 'filter' } }) }
function closeFilter() { const query = { ...route.query }; delete query.panel; returnToParent(router, router.resolve({ path: route.path, query }).fullPath) }
function open(record) {
  const task = mobileStore().create('s2-prototype', record.id, route.fullPath)
  touchMobile(); router.push(`/m/state-lab/${task.id}/${['draft', 'rejected'].includes(task.status) ? 'form' : 'result'}`)
}
function resume(task) { router.push(`/m/state-lab/${task.id}/${['draft', 'rejected'].includes(task.status) ? 'form' : 'result'}`) }
</script>

<template>
  <section class="mobile-card"><span class="mobile-pill">S2 隔离原型 · 示例数据</span><h2>返回与状态演练</h2><p class="mobile-muted">仅演练页面状态，不读取业务库存，不记账。筛选与页码保存在网址，草稿保留在本标签页会话中。</p><p v-if="warning" class="mobile-error" role="alert">{{ warning }}</p></section>
  <section class="mobile-card">
    <form class="mobile-form" @submit.prevent="apply()"><label>搜索演练记录<input v-model="search" type="search" maxlength="80" /></label><button class="mobile-button primary">搜索</button></form>
    <button class="mobile-button" @click="openFilter">筛选：{{ criteria.group === 'all' ? '全部' : criteria.group + '组' }}</button>
    <div v-if="filtering" role="dialog" aria-label="演练筛选" class="mobile-filter-panel"><label>分组<select v-model="group" aria-label="分组"><option value="all">全部</option><option value="A">A组</option><option value="B">B组</option></select></label><button class="mobile-button primary" @click="apply()">应用筛选</button><button class="mobile-button" @click="closeFilter">取消筛选</button></div>
  </section>
  <section v-if="tasks.length" class="mobile-card"><h2>本账号作业状态</h2><p class="mobile-muted mobile-small">演练待核实不能由相似记录或空列表判定失败。</p><button v-for="task in tasks" :key="task.id" class="mobile-button" @click="resume(task)">{{ task.record }} · {{ taskLabels[task.status] }}</button></section>
  <p v-if="!rows.length" class="mobile-empty">没有匹配的演练记录</p>
  <section v-for="row in rows" :id="row.id" :key="row.id" class="mobile-card" data-mobile-anchor><h2>{{ row.name }}</h2><p>{{ row.id }} · {{ row.group }}组 · 单位 {{ row.unit }}</p><p class="mobile-muted">这是状态演练记录，没有库存余额。</p><button class="mobile-button" @click="open(row)">打开演练表单</button></section>
  <section class="mobile-card"><p>第 {{ page }} / {{ pages }} 页</p><div class="mobile-row"><button class="mobile-button" :disabled="page <= 1" @click="search = criteria.q; group = criteria.group; apply(page - 1)">上一页</button><button class="mobile-button" :disabled="page >= pages" @click="search = criteria.q; group = criteria.group; apply(page + 1)">下一页</button></div></section>
</template>
