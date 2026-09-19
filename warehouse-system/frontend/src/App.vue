<script setup>
import { useRoute } from 'vue-router'
import { menuGroups } from './router'
import { computed } from 'vue'
import { auth } from './auth'
import AccountBar from './components/AccountBar.vue'
const route = useRoute()
const groups = computed(() => menuGroups.map(group => ({ ...group, items: group.items.filter(([path]) => auth.user?.role === 'admin' || !['users', 'settings', 'logs'].includes(path)) })).filter(group => group.items.length))
</script>

<template>
  <router-view v-if="route.meta.public" />
  <el-container v-else class="app-shell">
    <el-aside width="224px" class="sidebar">
      <div class="brand"><span class="brand-icon">仓</span><div>轻仓 WMS<small>仓库 · 生产 · 领料</small></div></div>
      <el-menu router :default-active="route.path" class="navigation">
        <el-menu-item index="/">首页</el-menu-item>
        <el-sub-menu v-for="group in groups" :key="group.label" :index="group.label">
          <template #title>{{ group.label }}</template>
          <el-menu-item v-for="[path, label] in group.items" :key="path" :index="`/${path}`">{{ label }}</el-menu-item>
        </el-sub-menu>
      </el-menu>
      <div class="sidebar-footer">v0.7.0 · 本地开发版</div>
    </el-aside>
    <el-container>
      <el-header class="topbar">
        <el-breadcrumb separator="/"><el-breadcrumb-item>工作台</el-breadcrumb-item><el-breadcrumb-item>{{ route.meta.title }}</el-breadcrumb-item></el-breadcrumb>
        <AccountBar />
      </el-header>
      <el-main><router-view /></el-main>
    </el-container>
  </el-container>
</template>
