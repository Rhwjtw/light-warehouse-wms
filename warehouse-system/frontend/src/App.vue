<script setup>
import { useRoute } from 'vue-router'
import { menuGroups } from './router'
const route = useRoute()
</script>

<template>
  <el-container class="app-shell">
    <el-aside width="224px" class="sidebar">
      <div class="brand"><span class="brand-icon">仓</span><div>轻仓 WMS<small>仓库 · 生产 · 领料</small></div></div>
      <el-menu router :default-active="route.path" class="navigation">
        <el-menu-item index="/">首页</el-menu-item>
        <el-sub-menu v-for="group in menuGroups" :key="group.label" :index="group.label">
          <template #title>{{ group.label }}</template>
          <el-menu-item v-for="[path, label] in group.items" :key="path" :index="`/${path}`">{{ label }}</el-menu-item>
        </el-sub-menu>
      </el-menu>
      <div class="sidebar-footer">v0.1.0 · 本地开发版</div>
    </el-aside>
    <el-container>
      <el-header class="topbar">
        <el-breadcrumb separator="/"><el-breadcrumb-item>工作台</el-breadcrumb-item><el-breadcrumb-item>{{ route.meta.title }}</el-breadcrumb-item></el-breadcrumb>
        <el-tag type="info" effect="plain">第一阶段 · 基础环境</el-tag>
      </el-header>
      <el-main><router-view /></el-main>
    </el-container>
  </el-container>
</template>
