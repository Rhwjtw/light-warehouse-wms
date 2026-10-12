<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRight, Box, Document, Download, Grid, List, Switch, Upload } from '@element-plus/icons-vue'
import { auth } from '../auth'
import { canOperate } from '../permissions'
const route = useRoute(), router = useRouter()
const overview = computed(() => route.path === '/m/jobs')
const outbound = computed(() => route.path === '/m/jobs/outbound' || (overview.value && route.query.mode === 'outbound'))
const mode = computed(() => outbound.value ? '出库' : '入库')
const choices = computed(() => [
  { label: '原料' + mode.value, hint: outbound.value ? '选择具体库位和批次' : '登记到货，入库后待上架', path: outbound.value ? '/m/jobs/material-outbound' : '/m/jobs/receipt', permission: 'material.inventory.write', icon: outbound.value ? Upload : Download },
  { label: '成品' + mode.value, hint: outbound.value ? '选择成品与库存分配' : '登记普通成品入库', path: outbound.value ? '/m/jobs/finished-outbound' : '/m/jobs/finished-receipt', permission: 'finished.inventory.write', icon: Box },
].filter(item => canOperate(auth.user?.role, item.permission)))
const materialWrite = computed(() => canOperate(auth.user?.role, 'material.inventory.write'))
function switchMode(value) { router.replace({ path: '/m/jobs', query: value === 'outbound' ? { mode: value } : {} }) }
</script>
<template>
  <div class="mobile-page-intro"><h2>{{ overview ? '这次要做什么？' : '选择货物类型' }}</h2><p v-if="!overview" class="mobile-muted">{{ outbound ? '办理本次普通出库' : '登记本次到货' }}</p></div>
  <div v-if="overview" class="mobile-tabs" aria-label="作业类型"><button :aria-pressed="!outbound" @click="switchMode('inbound')">入库</button><button :aria-pressed="outbound" @click="switchMode('outbound')">出库</button></div>
  <router-link v-for="item in choices" :key="item.path" :to="{ path: item.path, query: { from: route.fullPath } }" class="mobile-menu-item"><span class="mobile-menu-icon"><component :is="item.icon" /></span><span><strong>{{ item.label }}</strong><small>{{ item.hint }}</small></span><ArrowRight /></router-link>
  <p v-if="!choices.length" class="mobile-warning">当前角色没有出入库操作权限，可查看库存和记录。</p>
  <template v-if="overview && materialWrite"><h3 class="mobile-section-title">库内作业</h3><div class="mobile-tools"><router-link to="/m/jobs/putaway"><Box />上架</router-link><router-link to="/m/jobs/material-transfer"><Switch />移库</router-link><router-link to="/m/jobs/material-count"><List />盘点</router-link></div></template>
  <h3 class="mobile-section-title">{{ overview ? '辅助工具' : '相关查询' }}</h3>
  <router-link v-if="overview || !outbound" to="/m/pending" class="mobile-menu-item"><span class="mobile-menu-icon"><Document /></span><span><strong>待上架记录</strong><small>查看具体入库明细</small></span><ArrowRight /></router-link>
  <router-link v-if="!overview" :to="{ path: '/m/records', query: { view: outbound ? 'material' : 'receipts' } }" class="mobile-menu-item"><span class="mobile-menu-icon"><Document /></span><span><strong>{{ outbound ? '原料库存流水' : '原料入库记录' }}</strong><small>查询具体原料单据与明细</small></span><ArrowRight /></router-link><router-link v-if="!overview" :to="{ path: '/m/records', query: { view: outbound ? 'finishedOutbounds' : 'finishedReceipts' } }" class="mobile-menu-item"><span class="mobile-menu-icon"><Document /></span><span><strong>成品{{ mode }}记录</strong><small>查询既有成品单据与明细</small></span><ArrowRight /></router-link>
  <router-link v-if="overview" to="/m/labels" class="mobile-menu-item"><span class="mobile-menu-icon"><Grid /></span><span><strong>二维码标签</strong><small>货物与库位标签</small></span><ArrowRight /></router-link>
  <router-link v-if="!choices.length" to="/m/inventory" class="mobile-button primary">查看库存</router-link>
</template>
