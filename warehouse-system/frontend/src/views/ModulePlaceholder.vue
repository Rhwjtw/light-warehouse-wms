<script setup>
import { useRoute } from 'vue-router'
const route = useRoute()
</script>
<template>
  <div class="page-heading"><div><h1>{{ route.meta.title }}</h1><p>{{ route.meta.group }}</p></div></div>
  <el-card shadow="never"><el-empty description="该业务模块尚未开放，将按开发阶段逐步实现。"><el-button type="primary" @click="$router.push('/')">返回工作台</el-button></el-empty></el-card>
</template>
