<script setup>
import { useRoute } from 'vue-router'
import { ArrowLeft, Box, Document, House, SetUp, User } from '@element-plus/icons-vue'
import { mobileNavigation } from './routes'
import './mobile.css'
const route = useRoute()
const icons = { Box, Document, House, SetUp, User }
</script>

<template>
  <div :class="['mobile-root', { 'mobile-task': route.meta.mobileTask }]">
    <header class="mobile-header">
      <router-link v-if="route.meta.mobileParent" :to="route.meta.mobileParent" class="mobile-back" aria-label="返回"><ArrowLeft /><span>返回</span></router-link>
      <span v-else class="mobile-brand" aria-hidden="true">仓</span>
      <h1>{{ route.meta.title }}</h1><span class="mobile-header-tag">轻仓 WMS</span>
    </header>
    <main id="mobile-content" class="mobile-content"><router-view /></main>
    <nav v-if="!route.meta.mobileTask" class="mobile-nav" aria-label="手机主导航">
      <router-link v-for="item in mobileNavigation" :key="item.path" :to="item.path" :class="{ active: route.meta.mobileTab === item.path }" :aria-current="route.meta.mobileTab === item.path ? 'page' : undefined">
        <component :is="icons[item.icon]" /><span>{{ item.label }}</span>
      </router-link>
    </nav>
  </div>
</template>
