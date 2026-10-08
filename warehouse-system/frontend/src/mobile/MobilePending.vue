<script setup>
import { useRoute } from 'vue-router'
const route = useRoute()
</script>

<template>
  <section class="mobile-card mobile-pending"><span class="mobile-pill">{{ route.path === '/m/jobs' ? '原料入库已接入' : '本项写入尚未开发' }}</span><h2>{{ route.meta.pendingLabel || route.meta.title }}</h2><router-link v-if="route.path === '/m/jobs'" to="/m/jobs/receipt" class="mobile-button primary">原料入库</router-link><p class="mobile-muted">原料入库可用；手动上架、成品入库及其他库存作业仍待后续阶段。当前可只读查询库存与记录。</p><router-link v-if="['/m/jobs', '/m/jobs/putaway'].includes(route.path)" to="/m/pending" class="mobile-button">查询待上架记录（只读）</router-link><router-link v-if="route.path === '/m/jobs'" to="/m/state-lab" class="mobile-button">S2 返回、草稿与状态演练（不记账）</router-link><router-link to="/m" class="mobile-button">返回工作台</router-link></section>
</template>
