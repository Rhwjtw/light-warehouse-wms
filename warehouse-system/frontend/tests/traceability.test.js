import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { ref } from 'vue'

const descriptor = parse(readFileSync(new URL('../src/views/Traceability.vue', import.meta.url), 'utf8')).descriptor
const body = descriptor.scriptSetup.content.replace(/^import .*$/gm, '') + '\nreturn {query,candidates,result,error,search,open}'
function component(api) {
  return new Function('ref', 'useRouter', 'api', body)(ref, () => ({push(){}}), api)
}
test('trace search keeps same-number supplier candidates separate and displays server quantities unchanged', async () => {
  const candidates = [{entry_type:'material',id:1,batch_no:'RM001',supplier_name:'A'}, {entry_type:'material',id:2,batch_no:'RM001',supplier_name:'B'}]
  const traced = {warnings:['无法准确分摊'],production_orders:[{materials:[{net_used_quantity:'5.500001'}]}]}
  const calls = []
  const state = component({async get(url) {calls.push(url); return {data:url.endsWith('/search') ? {items:candidates,truncated:false} : traced}}})
  state.query.value='RM001'; await state.search()
  assert.deepEqual(state.candidates.value,candidates)
  await state.open(candidates[1]); assert.equal(calls[1],'/traceability/material-batches/2')
  assert.equal(state.result.value.production_orders[0].materials[0].net_used_quantity,'5.500001')
  assert.equal(state.result.value.warnings[0],'无法准确分摊')
  assert.match(descriptor.template.content, /read-only/)
  assert.doesNotMatch(descriptor.scriptSetup.content, /api\.(post|put|delete)/)
})
test('older trace response cannot overwrite the newest entrance', async () => {
  let release
  const state = component({get(url) {return url.endsWith('/1') ? new Promise(resolve => {release=resolve}) : Promise.resolve({data:{id:2}})}})
  const first = state.open({entry_type:'finished',id:1})
  await state.open({entry_type:'production',id:2})
  release({data:{id:1}}); await first
  assert.equal(state.result.value.id,2)
})
