import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { computed, reactive, ref } from 'vue'
import { canOperate } from '../src/permissions.js'

const source = parse(readFileSync(new URL('../src/views/FinishedInventory.vue', import.meta.url), 'utf8')).descriptor.scriptSetup.content
const body = source.replace(/^import .*$/gm, '') + '\nreturn {form,selected,batchRows,error,submit}'
function component(rows) {
  const sent=[]
  const api={async post(url,payload) {sent.push(payload); return {data:{document_no:'FG-test'}}}, async get(url) {
    return {data:url.endsWith('/batches') ? [] : url.startsWith('/finished-stock/') ? {unit:'盒',quantity:'100'} : {items:[],total:0}}
  }}
  const state = new Function('computed','reactive','ref','watch','onBeforeRouteLeave','onBeforeRouteUpdate','useRoute','ElMessage','api','auth','canOperate','crypto',body)(
    computed,reactive,ref,()=>{},()=>{},()=>{},()=>({meta:{resource:'finished-outbounds'}}),{success(){}},api,{user:{role:'warehouse'}},canOperate,{randomUUID:()=> 'key-fg-test'})
  Object.assign(state.form,{product_id:1,quantity:'100',business_date:'2026-10-01'})
  state.selected.value={unit:'盒',quantity:'1000'}
  state.batchRows.value=rows
  return {state,sent}
}
test('finished outbound keeps explicit aggregate and real batch allocations separate', async () => {
  const {state,sent}=component([{id:1,stock_scope:'aggregate',outbound_quantity:'40'}, {id:2,stock_scope:'batch',outbound_quantity:'60'}])
  await state.submit()
  assert.deepEqual(sent[0].allocations,[{batch_id:1,quantity:'40'},{batch_id:2,quantity:'60'}])
  assert.equal(sent[0].quantity,'100')
})
test('invalid batch quantity is not silently skipped when another row is valid', async () => {
  for (const invalid of ['abc','0','1.0000001','-1']) {
    const {state,sent}=component([{id:1,outbound_quantity:'100'}, {id:2,outbound_quantity:invalid}])
    await state.submit(); assert.equal(sent.length,0); assert.match(state.error.value,/不能跳过/)
  }
})
