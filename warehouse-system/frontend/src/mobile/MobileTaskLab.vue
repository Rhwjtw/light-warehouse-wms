<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { auth } from '../auth'
import { canOperate } from '../permissions'
import { formatQuantity, quantityUnits } from '../quantity'
import { mobileRevision, mobileStore, touchMobile } from './session'
import { prototypeListLocation } from './navigation'
import { lockedTask, taskLabels } from './taskState'
const route = useRoute(), router = useRouter(), ready = ref(false), checking = ref(false), error = ref(''), outcome = ref('success'), timer = ref(null)
const form = reactive({ quantity: '', location: '', notes: '' })
const task = computed(() => { mobileRevision.value; return mobileStore()?.get(route.params.jobId) })
const allowed = computed(() => canOperate(auth.user?.role, 'material.inventory.write'))
const locked = computed(() => lockedTask(task.value))
const step = computed(() => route.params.step)
const warning = computed(() => { mobileRevision.value; return mobileStore()?.warning || '' })
let restoring = false
function loadFields() { restoring = true; Object.assign(form, task.value?.fields || { quantity: '', location: '', notes: '' }); restoring = false }
watch(() => route.params.jobId, () => { ready.value = false; loadFields(); check() })
watch(form, () => {
  if (!restoring && task.value && !locked.value) { mobileStore().edit(task.value.id, form); touchMobile() }
}, { flush: 'sync' })
function validate() {
  try {
    const value = quantityUnits(form.quantity)
    if (value <= 0n || value > 99999999999999999999n) throw new Error('数量必须大于零且不超过十四位整数、六位小数')
    if (!form.location.trim()) throw new Error('请填写演练库位')
    return true
  } catch (e) { error.value = e.message; return false }
}
async function check() {
  if (checking.value) return false
  checking.value = true; ready.value = false; error.value = ''
  try {
    const result = await api.get('/auth/me')
    if (result.data.id !== auth.user?.id) throw new Error('账户已变化，请重新登录')
    auth.user = result.data
    // 原型记录仅作本地确认。后续业务须在此接入实时记录/余额/库位读取。
    ready.value = Boolean(task.value && /^S2-([1-9]|[12]\d|3[0-6])$/.test(task.value.record))
    if (!ready.value) throw new Error('演练记录已失效，请返回列表')
    return true
  } catch (e) { error.value = e.message; return false }
  finally { checking.value = false }
}
async function confirm() {
  if (!task.value || locked.value || !validate() || !await check()) return
  router.push(`/m/state-lab/${task.value.id}/confirm`)
}
async function submit() {
  if (!task.value || locked.value || checking.value || !await check()) return
  try {
    const started = mobileStore().begin(task.value.id, { ready: ready.value, allowed: allowed.value })
    touchMobile()
    if (outcome.value === 'hold') return
    const selected = outcome.value
    timer.value = window.setTimeout(() => {
      const state = mobileStore()?.get(started.id)
      if (state?.status !== 'submitting') return
      const status = selected === 'unknown' ? 'uncertain' : selected === 'reject' ? 'rejected' : 'succeeded'
      mobileStore().settle(started.id, status, status === 'uncertain' ? '演练响应丢失，不能推定失败或重新提交。' : status === 'rejected' ? '演练明确校验拒绝：请返回修改。' : '')
      touchMobile(); router.replace(`/m/state-lab/${started.id}/result`)
    }, 1000)
  } catch (e) { error.value = e.message; touchMobile() }
}
function resolvePending() {
  if (task.value?.status !== 'submitting') return
  mobileStore().settle(task.value.id, 'uncertain', '已停止等待演练响应；原任务保留待核实。')
  touchMobile(); router.replace(`/m/state-lab/${task.value.id}/result`)
}
function returnList() { router.replace(prototypeListLocation(task.value?.origin)) }
onMounted(() => { loadFields(); check(); window.addEventListener('wms-mobile-resume', check) })
onBeforeUnmount(() => { if (timer.value) clearTimeout(timer.value); window.removeEventListener('wms-mobile-resume', check) })
</script>

<template>
  <section class="mobile-card"><span class="mobile-pill">S2 状态演练 · 不记账</span><h2>{{ task?.record || '作业上下文不存在' }}</h2><p v-if="task">状态：{{ taskLabels[task.status] }}</p><p class="mobile-muted mobile-small">只核实当前登录和本地演练记录。真实余额/库位/业务结果尚未接入。</p><p v-if="checking" role="status">正在核实登录与权限…</p><p v-if="error" class="mobile-error" role="alert">{{ error }}</p><p v-if="warning" class="mobile-error" role="alert">{{ warning }}</p><p v-if="!allowed" class="mobile-error">当前角色可查看演练，原库存写权限不足，不能演练提交。</p><button v-if="!ready" class="mobile-button" @click="check">重新核实</button></section>
  <section v-if="!task" class="mobile-card"><p>草稿可能已清理或属于其他账号，不能重建原请求。</p><button class="mobile-button primary" @click="returnList">返回演练列表</button></section>
  <section v-else-if="locked" class="mobile-card">
    <h2>{{ taskLabels[task.status] }}</h2><p>{{ task.reason || task.result?.label || '正在等待演练响应，请勿重复提交。' }}</p>
    <dl class="mobile-details"><div><dt>本次数量</dt><dd>{{ formatQuantity(task.snapshot?.quantity) }} kg</dd></div><div><dt>库位</dt><dd>{{ task.snapshot?.location }}</dd></div><div><dt>请求时间</dt><dd>{{ task.submittedAt }}</dd></div><div><dt>备注</dt><dd>{{ task.snapshot?.notes || '—' }}</dd></div></dl>
    <p v-if="task.status === 'succeeded'" class="mobile-success">演练成功，旧表单已失效。实际业务没有记账，未查询任何余额。</p>
    <template v-if="task.status === 'uncertain'"><p class="mobile-warning">必须先核对相关单据/流水。空列表、相似数量/人员/时间不能唯一确定原请求。本原型没有实际业务结果。</p><router-link to="/m/records" class="mobile-button">查看单据与流水（仅辅助核对）</router-link></template>
    <button v-if="task.status === 'submitting'" class="mobile-button" @click="resolvePending">演练响应丢失，保留待核实</button>
    <button class="mobile-button primary" @click="returnList">返回原列表</button>
  </section>
  <section v-else-if="step === 'context'" class="mobile-card"><h2>返回上下文预留</h2><p>后续扫码覆盖层从此返回原表单，保留当前字段。本阶段没有相机或扫码按钮。</p><router-link :to="`/m/state-lab/${task.id}/form`" class="mobile-button primary">取消并返回原表单</router-link></section>
  <section v-else-if="step === 'confirm'" class="mobile-card"><h2>核对演练内容</h2><dl class="mobile-details"><div><dt>记录</dt><dd>{{ task.record }}</dd></div><div><dt>库位</dt><dd>{{ form.location }}</dd></div><div><dt>数量</dt><dd>{{ formatQuantity(form.quantity) }} kg</dd></div><div><dt>备注</dt><dd>{{ form.notes || '—' }}</dd></div></dl><label class="mobile-select-label">演练响应<select v-model="outcome" aria-label="演练响应"><option value="success">明确成功</option><option value="reject">明确拒绝</option><option value="unknown">超时/响应丢失</option><option value="hold">持续提交中</option></select></label><button class="mobile-button primary" :disabled="!ready || checking || !allowed || Boolean(warning)" @click="submit">演练提交（不记账）</button><router-link :to="`/m/state-lab/${task.id}/form`" class="mobile-button">返回修改</router-link></section>
  <section v-else class="mobile-card"><p v-if="task.status === 'rejected'" class="mobile-error">{{ task.reason }}</p><form class="mobile-form" @submit.prevent="confirm"><label>演练库位<input v-model="form.location" maxlength="80" autocomplete="off" /></label><label>本次数量 · kg<input v-model="form.quantity" type="text" inputmode="decimal" maxlength="80" autocomplete="off" aria-describedby="lab-quantity-help" /></label><p id="lab-quantity-help" class="mobile-muted mobile-small">最多六位小数；键盘缺少小数点时可粘贴完整数字，不自动舍入。</p><label>备注（选填）<textarea v-model="form.notes" maxlength="300" rows="3"></textarea></label><p v-if="error" class="mobile-error" role="alert">{{ error }}</p><button type="submit" class="mobile-button primary" :disabled="checking || !allowed">下一步：核对</button></form><router-link :to="`/m/state-lab/${task.id}/context`" class="mobile-button">演练覆盖层返回</router-link><p class="mobile-muted mobile-small">编辑时自动保存。正常返回、刷新保留输入；退出、换账号会清理本账号草稿。</p></section>
</template>
