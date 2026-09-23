<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Box, Expand, Fold, Goods, House, Setting, SetUp, Warning } from '@element-plus/icons-vue'
import { menuGroups } from './router'
import { auth } from './auth'
import { canAccess } from './permissions'
import AccountBar from './components/AccountBar.vue'

const route = useRoute()
const groups = computed(() => menuGroups.map(group => ({ ...group, items: group.items.filter(([path]) => canAccess(auth.user?.role, path)) })).filter(group => group.items.length))
const groupIcons = { 原材料管理: Box, 成品管理: Goods, 生产管理: SetUp, 预警中心: Warning, 系统管理: Setting }
const manualCollapsed = ref(false)
const narrowViewport = ref(false)
const collapsed = computed(() => manualCollapsed.value || narrowViewport.value)

// 仅在明显窄于常见仓库电脑分辨率时自动收起；1366px 及以上默认保留完整导航效率。
function syncViewport() { narrowViewport.value = window.innerWidth < 1080 }
onMounted(() => { syncViewport(); window.addEventListener('resize', syncViewport, { passive: true }) })
onBeforeUnmount(() => window.removeEventListener('resize', syncViewport))
</script>

<template>
  <router-view v-if="route.meta.public" v-slot="{ Component }"><transition name="page"><component :is="Component" /></transition></router-view>
  <el-container v-else class="app-shell">
    <el-aside :width="collapsed ? '76px' : '232px'" :class="['sidebar', { 'is-collapsed': collapsed }]">
      <div class="brand"><span class="brand-icon">仓</span><div class="brand-copy">轻仓 WMS<small>仓库 · 生产 · 领料</small></div></div>
      <el-menu router :default-active="route.path" :collapse="collapsed" :collapse-transition="false" class="navigation">
        <el-menu-item index="/"><el-icon><House /></el-icon><template #title>首页</template></el-menu-item>
        <el-sub-menu v-for="group in groups" :key="group.label" :index="group.label">
          <template #title><el-icon><component :is="groupIcons[group.label]" /></el-icon><span>{{ group.label }}</span></template>
          <el-menu-item v-for="[path, label] in group.items" :key="path" :index="`/${path}`">{{ label }}</el-menu-item>
        </el-sub-menu>
      </el-menu>
      <div class="sidebar-footer">v1.0.0 · 轻仓工作台</div>
    </el-aside>
    <el-container class="workspace">
      <el-header class="topbar">
        <div class="topbar-start">
          <el-tooltip :content="collapsed ? '展开导航' : '收起导航'" placement="bottom">
            <el-button text circle class="sidebar-toggle" :aria-label="collapsed ? '展开导航' : '收起导航'" @click="manualCollapsed = !manualCollapsed"><el-icon><Expand v-if="collapsed" /><Fold v-else /></el-icon></el-button>
          </el-tooltip>
          <div class="topbar-context"><div class="topbar-eyebrow">{{ route.meta.group || '工作台' }}</div><div class="topbar-title">{{ route.meta.title }}</div></div>
        </div>
        <AccountBar />
      </el-header>
      <el-main class="main-content">
        <router-view v-slot="{ Component, route: viewRoute }"><transition name="page" mode="out-in"><div :key="viewRoute.path" class="page-content"><component :is="Component" /></div></transition></router-view>
      </el-main>
    </el-container>
  </el-container>
</template>
