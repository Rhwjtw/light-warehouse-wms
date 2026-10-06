import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { computed, reactive, ref } from 'vue'
import { canOperate } from '../src/permissions.js'

const descriptor = parse(readFileSync(new URL('../src/views/MaterialInventory.vue', import.meta.url), 'utf8')).descriptor
const body = descriptor.scriptSetup.content.replace(/^import .*$/gm, '') + '\nreturn {form,submit,purposeChanged,candidates,error,saving}'
function component({cancel=false,fail=false}={}) {
  const sent=[],queries=[],confirmations=[]
  let sequence=0
  const api={
    async get(url,config) {
      queries.push({url,params:config?.params})
      return {data:{items:[{id:11,batch_no:'B-11',supplier_name:'原供应商',remaining_quantity:'30',unit:'kg'}],total:1}}
    },
    async post(url,payload) {sent.push({url,payload}); if(fail) throw new Error('network'); return {data:{document_no:'TEST-11'}}},
  }
  const state = new Function('computed','reactive','ref','watch','useRoute','auth','canOperate','api','ElMessage','ElMessageBox','crypto',body)(
    computed,reactive,ref,()=>{},()=>({meta:{resource:'material-outbounds'}}),{user:{role:'warehouse'}},canOperate,api,{success(){}},
    {async confirm(text){confirmations.push(text); if(cancel) throw new Error('cancel')}},{randomUUID:()=>`key-${++sequence}`})
  Object.assign(state.form,{material_id:7,batch_id:11,quantity:'2',notes:'破损',purpose:'scrap'})
  state.candidates.value=[{id:11,batch_no:'B-11',supplier_name:'原供应商'}]
  return {state,sent,queries,confirmations}
}

test('return and scrap require a reason and confirm quantity before posting',async()=>{
  const {state,sent,confirmations}=component()
  state.form.notes=' '; await state.submit()
  assert.equal(sent.length,0); assert.match(state.error.value,/原因/)
  state.form.notes='破损报废'; await state.submit()
  assert.equal(sent[0].payload.purpose,'scrap'); assert.equal(sent[0].payload.notes,'破损报废')
  assert.match(confirmations[0],/报废.*B-11.*2 kg/)
  assert.equal(state.form.notes,''); assert.equal(state.form.quantity,'')
})

test('canceling supplier return confirmation never posts or clears input',async()=>{
  const {state,sent,confirmations}=component({cancel:true})
  state.form.purpose='supplier_return'; await state.submit()
  assert.equal(sent.length,0); assert.equal(state.form.quantity,'2'); assert.equal(state.saving.value,false)
  assert.match(confirmations[0],/原供应商/)
})

test('purpose switch reloads candidate rules and failed requests reuse keys',async()=>{
  const {state,sent,queries}=component({fail:true})
  await state.purposeChanged()
  assert.equal(queries[0].params.purpose,'scrap')
  await state.submit(); await state.submit()
  assert.equal(sent[0].payload.request_id,sent[1].payload.request_id)
  state.form.purpose='supplier_return'; await state.purposeChanged(); await state.submit()
  assert.notEqual(sent[1].payload.request_id,sent[2].payload.request_id)
  assert.match(descriptor.template.content,/writePage && canWrite/)
})
