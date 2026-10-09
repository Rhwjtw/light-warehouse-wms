<script setup>
import { useRoute } from 'vue-router'
const route = useRoute()
</script>

<template>
  <section class="mobile-card mobile-pending"><span class="mobile-pill">{{ ['/m/jobs', '/m/jobs/outbound'].includes(route.path) ? '原料与成品手动作业已接入' : '本项写入尚未开发' }}</span><h2>{{ route.meta.pendingLabel || route.meta.title }}</h2><router-link v-if="route.path === '/m/jobs'" to="/m/jobs/receipt" class="mobile-button primary">原料入库</router-link><router-link v-if="route.path === '/m/jobs'" to="/m/jobs/putaway" class="mobile-button primary">原料手动上架</router-link><template v-if="['/m/jobs', '/m/jobs/outbound'].includes(route.path)"><router-link to="/m/jobs/material-outbound" class="mobile-button primary">原料普通出库</router-link><router-link to="/m/jobs/finished-outbound" class="mobile-button primary">成品出库</router-link></template><router-link v-if="route.path === '/m/jobs'" to="/m/jobs/finished-receipt" class="mobile-button primary">成品普通入库</router-link><p class="mobile-muted">原料入库/上架/普通出库、成品普通入出库可用；移库与盘点留待S6。当前可只读查询库存与记录。</p><router-link v-if="['/m/jobs', '/m/jobs/putaway'].includes(route.path)" to="/m/pending" class="mobile-button">查询待上架记录（只读）</router-link><router-link v-if="route.path === '/m/jobs'" to="/m/state-lab" class="mobile-button">S2 返回、草稿与状态演练（不记账）</router-link><router-link to="/m" class="mobile-button">返回工作台</router-link></section>
</template>
