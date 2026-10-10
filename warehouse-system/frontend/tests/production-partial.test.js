import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { computed, reactive, ref } from 'vue'
import { quantityUnits, quantityText, formatQuantity } from '../src/quantity.js'
import { postingRequest } from '../src/postingRequest.js'
import { canOperate } from '../src/permissions.js'

const source = parse(readFileSync(new URL('../src/views/ProductionOrders.vue', import.meta.url), 'utf8')).descriptor.scriptSetup.content
const body = source.replace(/^import .*$/gm, '') + '\nreturn {openAction,applySuggestion,submitAction,clearAllocationQuantities,cumulativeIssued,projectedIssued,action,current,error,actionTotal}'
function component(options = {}) {
  const sent = [], requested = []
  let count = 0
  const material = {id:1,material_id:7,base_unit:'kg',material_name:'测试原料',planned_required_quantity:'200',issued_quantity:'0',supplemented_quantity:'0',net_issued_quantity:'0'}
  const order = {id:9,materials:[material],material_records:[]}
  const api = {
    async get(url, config) {
      if(url.endsWith('fefo-suggestion')) {
        requested.push(config.params)
        if (options.suggestionFailure) throw new Error('批次读取失败')
        const target = config.params.quantity || '200'
        const quantity = quantityText(quantityUnits(target) < 250000000n ? quantityUnits(target) : 250000000n)
        return {data:{remaining_to_issue:'200',suggestions:[{batch_id:11,quantity}],batches:[{id:11,remaining_quantity:'250'},{id:12,remaining_quantity:'0'}]}}
      }
      return {data:url === '/production-orders/9' ? order : {items:[],total:0}}
    },
    async post(url,payload) {
      sent.push({url,payload})
      const total = payload.allocations.reduce((sum,row)=>sum+quantityUnits(row.quantity),0n)
      material.issued_quantity = quantityText(quantityUnits(material.issued_quantity)+total)
      material.net_issued_quantity = material.issued_quantity
    },
  }
  const factory = new Function('computed','reactive','ref','watch','onMounted','useRoute','auth','canOperate','api','ElMessage','ElMessageBox','quantityUnits','quantityText','formatQuantity','postingRequest','crypto',body)
  const state = factory(computed,reactive,ref,()=>{},()=>{},()=>({meta:{resource:'production-orders'},query:{}}),{user:{role:'workshop'}},canOperate,api,{success(){}},{},quantityUnits,quantityText,formatQuantity,postingRequest,{randomUUID:()=>`key-${++count}`})
  state.current.value=order
  return {state,sent,requested,material}
}

test('partial allocation leaves unused batches blank and sends only current quantity', async () => {
  const {state,sent,material}=component()
  await state.openAction('issue',material)
  assert.equal(state.action.desired,'')
  assert.deepEqual(state.action.allocations.map(row=>row.quantity),['',''])
  state.action.desired='40'
  await state.applySuggestion()
  assert.deepEqual(state.action.allocations.map(row=>row.quantity),['40',''])
  await state.submitAction()
  assert.deepEqual(sent[0].payload.allocations,[{batch_id:11,quantity:'40'}])
})

test('edited current quantity cannot accidentally post previous full allocation', async () => {
  const {state,sent,material}=component()
  await state.openAction('issue',material)
  state.action.desired='200'; await state.applySuggestion()
  state.action.desired='40'; await state.submitAction()
  assert.equal(sent.length,0); assert.match(state.error.value,/本次总量/)
  await state.applySuggestion(); await state.submitAction()
  assert.equal(sent[0].payload.allocations[0].quantity,'40')
})

test('manual per-batch partial issues can be repeated in fresh visits', async () => {
  const {state,sent,material}=component()
  for(const quantity of ['40','100','60']) {
    await state.openAction('issue',material)
    state.action.allocations[0].quantity=quantity
    await state.submitAction()
  }
  assert.deepEqual(sent.map(row=>row.payload.allocations[0].quantity),['40','100','60'])
  assert.equal(new Set(sent.map(row=>row.payload.request_id)).size,3)
})

test('total alone submits directly and repeated issues may exceed theoretical demand', async () => {
  const {state,sent,requested,material}=component()
  for (const [quantity,cumulative] of [['100','100'],['110','210'],['5.000001','215.000001']]) {
    await state.openAction('issue',material)
    state.action.desired=quantity
    assert.equal(state.projectedIssued.value,cumulative)
    await state.submitAction()
    assert.equal(state.error.value,'')
    assert.equal(material.issued_quantity,cumulative)
  }
  assert.deepEqual(sent.map(row=>row.payload.allocations),[
    [{batch_id:11,quantity:'100'}],[{batch_id:11,quantity:'110'}],[{batch_id:11,quantity:'5.000001'}],
  ])
  assert.equal(requested.at(-1).quantity,'5.000001')
  assert.equal(new Set(sent.map(row=>row.payload.request_id)).size,3)
})

test('editing total clears previous allocation and confirming allocates the new quantity', async () => {
  const {state,sent,material}=component()
  await state.openAction('issue',material)
  state.action.desired='200'; await state.applySuggestion()
  state.action.desired='100'; state.clearAllocationQuantities()
  await Promise.all([state.submitAction(),state.submitAction()])
  assert.equal(sent.length,1)
  assert.equal(sent[0].payload.allocations[0].quantity,'100')
})

test('unavailable total is refused without posting a smaller quantity', async () => {
  const {state,sent,material}=component()
  await state.openAction('issue',material)
  state.action.desired='300'; await state.submitAction()
  assert.equal(sent.length,0)
  assert.match(state.error.value,/库存不足/)
})

test('failed batch read never posts and cumulative issued includes supplements before returns', async () => {
  const {state,sent,material}=component({suggestionFailure:true})
  material.issued_quantity='100'; material.supplemented_quantity='20'; material.net_issued_quantity='90'
  assert.equal(state.cumulativeIssued(material),'120')
  await state.openAction('issue',material)
  state.action.desired='10'; await state.submitAction()
  assert.equal(sent.length,0)
  assert.match(state.error.value,/批次读取失败/)
})
