<script setup>
import { SYSTEM_INFO } from '../config/system'
import DataPanel from '../components/ui/DataPanel.vue'
import PageHeader from '../components/ui/PageHeader.vue'

const details = [
  ['系统名称', SYSTEM_INFO.name],
  ['英文名称', SYSTEM_INFO.nameEn],
  ['当前版本', SYSTEM_INFO.version],
  ['系统设计与开发', SYSTEM_INFO.developer],
  ['首次开发', SYSTEM_INFO.year],
  ['技术架构', SYSTEM_INFO.architecture],
  ['系统类型', SYSTEM_INFO.type],
]
const features = [
  '原材料管理', '成品管理', '批次库存管理', '库位管理',
  '入库、上架、出库、移库', '车间领料、补料、退料', 'BOM 配方', '生产单管理',
  '成品完工', '理论用量与实际用量差异', '库存流水', '批次追溯',
  '单据冲销', '权限管理', '操作日志',
]
</script>

<template>
  <PageHeader eyebrow="系统管理" title="关于系统" description="系统基本信息与主要功能。" />
  <div class="about-layout">
    <DataPanel title="系统信息">
      <dl class="system-details">
        <div v-for="[label, value] in details" :key="label" class="system-detail">
          <dt>{{ label }}</dt><dd>{{ value }}</dd>
        </div>
      </dl>
    </DataPanel>
    <DataPanel title="主要功能">
      <ul class="system-features"><li v-for="feature in features" :key="feature">{{ feature }}</li></ul>
    </DataPanel>
    <footer class="about-footer">
      <p>© {{ SYSTEM_INFO.year }}{{ SYSTEM_INFO.copyrightOwner }}</p>
      <p>Designed &amp; Developed by {{ SYSTEM_INFO.developer }}。</p>
    </footer>
  </div>
</template>

<style scoped>
.about-layout { max-width: 1040px; }
.system-details { margin: 0; }
.system-detail { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 18px; padding: 13px 0; font-size: 13px; line-height: 1.7; }
.system-detail + .system-detail { border-top: 1px solid var(--wms-border); }
.system-detail:first-child { padding-top: 0; }
.system-detail:last-child { padding-bottom: 0; }
.system-detail dt { color: var(--wms-text-secondary); }
.system-detail dd { margin: 0; overflow-wrap: anywhere; }
.system-features { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px 28px; margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.7; }
.system-features li::marker { color: var(--wms-text-tertiary); }
.about-footer { margin-top: 24px; text-align: center; }
.about-footer p { color: var(--wms-text-tertiary); font-size: 11px; line-height: 1.8; }
@media (max-width: 1180px) { .system-features { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
