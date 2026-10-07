<script setup>
import { useRoute } from 'vue-router'
const route = useRoute()
</script>

<template>
  <section class="mobile-card mobile-pending"><span class="mobile-pill">尚未开发</span><h2>{{ route.meta.pendingLabel || route.meta.title }}</h2><p class="mobile-muted">手机{{ route.meta.title }}将在 {{ route.meta.stage }} 阶段实现。当前仅提供手机基础框架。</p><p class="mobile-muted">请使用既有电脑版办理业务。</p><router-link to="/m" class="mobile-button primary">返回工作台</router-link></section>
</template>
