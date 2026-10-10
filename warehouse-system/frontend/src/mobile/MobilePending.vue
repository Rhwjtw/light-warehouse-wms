<script setup>
import { useRoute } from 'vue-router'
const route = useRoute()
</script>

<template>
  <section class="mobile-card mobile-pending"><span class="mobile-pill">原料与成品手动作业已接入</span><h2>{{ route.meta.pendingLabel || route.meta.title }}</h2><template v-if="route.path === '/m/jobs'"><router-link to="/m/jobs/receipt" class="mobile-button primary">原料入库</router-link><router-link to="/m/jobs/putaway" class="mobile-button primary">原料手动上架</router-link><router-link to="/m/jobs/finished-receipt" class="mobile-button primary">成品普通入库</router-link><router-link to="/m/jobs/material-transfer" class="mobile-button primary">原料移库</router-link><router-link to="/m/jobs/material-count" class="mobile-button primary">库位批次盘点</router-link></template><template v-if="['/m/jobs', '/m/jobs/outbound'].includes(route.path)"><router-link to="/m/jobs/material-outbound" class="mobile-button primary">原料普通出库</router-link><router-link to="/m/jobs/finished-outbound" class="mobile-button primary">成品出库</router-link></template><p class="mobile-muted">原料入库、上架、普通出库、移库、库位批次盘点及成品普通入出库可用。库存与记录可只读查询。</p><router-link v-if="route.path === '/m/jobs'" to="/m/pending" class="mobile-button">查询待上架记录（只读）</router-link><router-link v-if="route.path === '/m/jobs'" to="/m/state-lab" class="mobile-button">S2 返回、草稿与状态演练（不记账）</router-link><router-link to="/m" class="mobile-button">返回工作台</router-link></section>
</template>
