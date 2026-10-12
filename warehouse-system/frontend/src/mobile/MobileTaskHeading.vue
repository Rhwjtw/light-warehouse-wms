<script setup>
import { computed } from 'vue'
import { CircleCheck, Warning } from '@element-plus/icons-vue'
const props = defineProps({ title: String, task: Object, step: String, allowed: { type: Boolean, default: true }, warning: String, error: String, checking: Boolean })
const locked = computed(() => ['submitting', 'uncertain', 'succeeded'].includes(props.task?.status))
const phase = computed(() => props.step === 'confirm' ? 1 : locked.value ? 2 : 0)
</script>
<template>
  <div v-if="locked" :class="['mobile-result-heading', { uncertain: task.status !== 'succeeded' }]">
    <span class="mobile-result-icon"><CircleCheck v-if="task.status === 'succeeded'" /><Warning v-else /></span>
    <h2>{{ task.status === 'succeeded' ? title + '成功' : task.status === 'submitting' ? '正在提交' : '结果待核实' }}</h2>
    <p class="mobile-muted">{{ task.status === 'succeeded' ? '本笔已完成，不能重复提交' : '请先核对这笔作业，勿重复提交' }}</p>
  </div>
  <ol v-else-if="task && step !== 'choose' && step !== 'scan'" class="mobile-steps" aria-label="作业进度">
    <li v-for="(name, index) in ['填写', '核对', '完成']" :key="name" :aria-current="phase === index ? 'step' : undefined"><b>{{ index + 1 }}</b>{{ name }}</li>
  </ol>
  <p v-if="!allowed" class="mobile-warning" role="status">当前角色可查询，无此作业的操作权限。</p>
  <p v-if="warning" class="mobile-error" role="alert">{{ warning }}</p>
  <p v-if="error" class="mobile-error" role="alert">{{ error }}</p>
  <p v-if="checking" class="mobile-status-inline" role="status">正在核对最新记录与权限…</p>
</template>
