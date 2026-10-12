<script setup>
import { ArrowRight, Search } from '@element-plus/icons-vue'
defineProps({ title: String, search: String, placeholder: { type: String, default: '名称 / 编码 / 批号' }, maxlength: { type: Number, default: 120 }, items: Array, total: Number, page: Number, pages: Number, loading: Boolean, error: String, busy: Boolean, prefix: String })
defineEmits(['update:search', 'search', 'select', 'page', 'cancel'])
</script>
<template>
  <div class="mobile-page-intro"><h2>{{ title }}</h2></div>
  <form class="mobile-search" @submit.prevent="$emit('search')"><Search /><input :value="search" :placeholder="placeholder" :aria-label="'搜索' + title" :maxlength="maxlength" @input="$emit('update:search', $event.target.value)" /><button class="mobile-button" :disabled="busy">搜索</button></form>
  <p v-if="loading" class="mobile-status" role="status">正在查询…</p>
  <template v-else-if="error"><p class="mobile-error" role="alert">{{ error }}</p><button class="mobile-button" @click="$emit('page', page)">重试查询</button></template>
  <div v-else class="mobile-choice-list" data-mobile-list-ready>
    <p class="mobile-list-meta">共 {{ total }} 条 · 选择后返回表单</p>
    <article v-for="row in items" :id="prefix + row.id" :key="row.id" class="mobile-card" data-mobile-anchor>
      <slot :row="row" />
      <button class="mobile-choice-action" :disabled="busy" @click="$emit('select', row)">选择此记录<ArrowRight /></button>
    </article>
    <p v-if="!items.length" class="mobile-empty">没有匹配的可用记录</p>
    <div class="mobile-pagination"><button class="mobile-button" :disabled="page <= 1 || busy" @click="$emit('page', page - 1)">上一页</button><span class="mobile-small mobile-muted">{{ page }} / {{ pages }}</span><button class="mobile-button" :disabled="page >= pages || busy" @click="$emit('page', page + 1)">下一页</button></div>
  </div>
  <button class="mobile-button" @click="$emit('cancel')">取消选择，返回表单</button>
</template>
