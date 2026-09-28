import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { computed, ref } from 'vue'
import { canOperate } from '../src/permissions.js'

// Execute the real SFC setup against transport/dialog doubles. Browser checks cover rendering.
const source = parse(readFileSync(new URL('../src/components/DocumentReversal.vue', import.meta.url), 'utf8')).descriptor.scriptSetup.content
const body = source.replace(/^import .*$/gm, '') + '\nreturn {load, execute, detail, preview, reason, error, canReverse}'
function component({ kind = 'putaway', role = 'warehouse', eligible = true, cancel = false, lostResponse = false } = {}) {
  const sent = [], emitted = [], confirmations = []
  let reversed = false, sequence = 0
  const api = {
    async get(url) {
      if (url.endsWith('reversal-preview')) return {data:{eligible, changes:[]}}
      return {data:{id:7, document_no:'ISOLATED-7', kind, status: reversed ? 'reversed' : 'normal'}}
    },
    async post(url, payload) {
      sent.push({url, payload})
      if (lostResponse) throw new Error('network lost')
      reversed = true
      return {data:{document_no:'ISOLATED-RV'}}
    },
  }
  const factory = new Function('computed','ref','watch','ElMessage','ElMessageBox','api','auth','canOperate','defineProps','defineEmits','crypto',body)
  const state = factory(computed,ref,()=>{}, {success(){}}, {async confirm(text) {
    confirmations.push(text); if(cancel) throw new Error('cancel')
  }}, api,{user:{role}},canOperate,()=>({modelValue:true,documentId:7}),()=>name=>emitted.push(name),{randomUUID:()=>`key-${++sequence}`})
  return {state,sent,emitted,confirmations}
}

test('empty reason never opens confirmation or sends a reversal', async () => {
  const {state,sent,confirmations}=component()
  await state.load(7); state.reason.value='  '; await state.execute()
  assert.equal(sent.length,0); assert.equal(confirmations.length,0)
  assert.match(state.error.value,/原因/)
})
test('second confirmation cancellation preserves stock and successful confirmation reloads status', async () => {
  const canceled=component({cancel:true})
  await canceled.state.load(7); canceled.state.reason.value='wrong quantity'; await canceled.state.execute()
  assert.equal(canceled.sent.length,0); assert.equal(canceled.emitted.length,0)
  const completed=component()
  await completed.state.load(7); completed.state.reason.value=' wrong quantity '; await completed.state.execute()
  assert.equal(completed.sent[0].payload.reason,'wrong quantity')
  assert.equal(completed.sent[0].url,'/stock-documents/7/reverse')
  assert.equal(completed.state.detail.value.status,'reversed')
  assert.deepEqual(completed.emitted,['changed'])
})
test('network retry keeps the same request key; edited reason creates a new key', async () => {
  const {state,sent}=component({lostResponse:true})
  await state.load(7); state.reason.value='wrong quantity'
  await state.execute(); await state.execute()
  assert.equal(sent[0].payload.request_id,sent[1].payload.request_id)
  state.reason.value='wrong location'; await state.execute()
  assert.notEqual(sent[1].payload.request_id,sent[2].payload.request_id)
})
test('all seven supported kinds preview; blocked previews cannot submit; production/count excluded', async () => {
  for (const kind of ['material_receipt','putaway','transfer','finished_receipt','adjustment','material_outbound','finished_outbound']) {
    const {state,sent}=component({kind,eligible:false})
    await state.load(7); assert.equal(state.preview.value.eligible,false)
    state.reason.value='blocked'; await state.execute(); assert.equal(sent.length,0)
  }
  for (const kind of ['issue','supplement','return','count_match']) {
    const {state}=component({kind}); await state.load(7); assert.equal(state.preview.value,null)
  }
  for (const role of ['workshop_director','workshop','accountant']) assert.equal(component({role}).state.canReverse.value,false)
})
