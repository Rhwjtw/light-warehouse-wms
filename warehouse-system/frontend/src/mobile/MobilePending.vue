<script setup>
import { useRoute } from 'vue-router'
const route = useRoute()
</script>

<template>
  <section class="mobile-card mobile-pending"><span class="mobile-pill">写入作业尚未开发</span><h2>{{ route.meta.pendingLabel || route.meta.title }}</h2><p class="mobile-muted">手机{{ route.meta.title }}将在 {{ route.meta.stage }} 阶段实现。当前可只读查询库存与记录。</p><p class="mobile-muted">请使用既有电脑版办理业务。</p><router-link v-if="['/m/jobs', '/m/jobs/putaway'].includes(route.path)" to="/m/pending" class="mobile-button">查询待上架记录（只读）</router-link><router-link v-if="route.path === '/m/jobs'" to="/m/state-lab" class="mobile-button">S2 返回、草稿与状态演练（不记账）</router-link><router-link to="/m" class="mobile-button primary">返回工作台</router-link></section>
</template>
