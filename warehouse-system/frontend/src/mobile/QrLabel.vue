<script setup>
defineProps({ label: { type: Object, required: true } })
</script>
<template>
  <article class="wms-qr-label">
    <div class="wms-qr-label-body"><div class="wms-qr-code"><img :src="label.image" alt="对象二维码" /><strong>{{ label.type === 'receipt' ? '原料入库明细' : '库位' }}</strong></div><div class="wms-qr-words"><p v-for="(line, index) in label.lines" :key="index">{{ line }}</p></div></div>
    <p class="wms-qr-instance">实例 {{ label.instance }}</p>
  </article>
</template>
<style>
.wms-qr-label { box-sizing: border-box; width: 60mm; height: 40mm; padding: 1.5mm; background: white; color: black; border: 0; overflow: hidden; font-family: Arial, 'Microsoft YaHei', sans-serif; }
.wms-qr-label-body { display: flex; align-items: flex-start; gap: 1mm; }
.wms-qr-code { width: 26mm; flex: 0 0 26mm; text-align: center; font-size: 7pt; }
.wms-qr-code img { display: block; width: 26mm; height: 26mm; }
.wms-qr-words { flex: 1; min-width: 0; font-size: 7pt; line-height: 1.15; }
.wms-qr-label .wms-qr-words p { margin: 0 0 .3mm; font-size: 7pt; line-height: 1.15; overflow-wrap: anywhere; }
.wms-qr-label .wms-qr-instance { margin: 1mm 0 0; font-size: 6pt; line-height: 1.1; overflow-wrap: anywhere; }
.wms-qr-print { position: fixed; left: -10000px; top: 0; }
@media print {
  @page { size: 60mm 40mm; margin: 0; }
  body:has(> .wms-qr-print) > * { display: none !important; }
  body > .wms-qr-print { display: block !important; position: static; width: 60mm; }
  .wms-qr-print .wms-qr-label { break-after: page; page-break-after: always; }
  .wms-qr-print .wms-qr-label:last-child { break-after: auto; page-break-after: auto; }
}
</style>
