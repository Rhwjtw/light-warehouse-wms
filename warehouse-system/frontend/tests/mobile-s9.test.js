import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { computed, ref, reactive } from 'vue'
import { createQrCamera } from '../src/mobile/camera.js'
import { createTaskStore, lockedTask, taskLabels } from '../src/mobile/taskState.js'
import * as putaway from '../src/mobile/putaway.js'
import * as qr from '../src/mobile/qr.js'
import { quantityUnits } from '../src/quantity.js'
import { readGate, positiveId } from '../src/mobile/readQueries.js'
import { returnToParent, mobileParentLocation } from '../src/mobile/navigation.js'
import { mobileLoginTarget } from '../src/mobile/routes.js'
const instance='a'.repeat(32)
const row={id:501,receipt_movement_id:901,batch_id:701,unit:'kg',pending_quantity:'12.300001',status:'partial',material_id:107,supplier_id:111,document_no:'MR501'}
const fields={receiptId:'501',movementId:'901',sourceBatchId:'701',locationId:'31',unit:'kg',quantity:'2.300001',notes:'保留备注'}
function camera(options={}) {
  let stops=0,posts=0,callback,texts=[],errors=[],constraints
  const video={srcObject:null,pause(){},async play(){},readyState:2,videoWidth:640,videoHeight:480}
  const track={stop(){stops++}},stream={getTracks:()=>[track]}
  const controller=createQrCamera({video,canvas:{getContext:()=>({drawImage(){}})},mediaDevices:{async getUserMedia(c){posts++;constraints=c;return stream}},secure:true,reader:async()=>({decodeFromCanvas(){throw {name:'NotFoundException'}}}),schedule:fn=>{callback=fn;return 1},cancel(){callback=null},onText:text=>texts.push(text),onError:e=>errors.push(e),...options})
  return {controller,video,stream,errors,texts,stops:()=>stops,requests:()=>posts,constraints:()=>constraints,tick:()=>callback?.()}
}
test('camera requests back-facing video only; double start is blocked and stop releases all tracks',async()=>{
 const c=camera();await c.controller.start();await c.controller.start();assert.equal(c.requests(),1);assert.equal(c.constraints().audio,false);assert.equal(c.constraints().video.facingMode.ideal,'environment');assert.equal(c.controller.state(),'scanning');c.controller.stop();assert.equal(c.stops(),1);assert.equal(c.video.srcObject,null);c.tick();assert.equal(c.texts.length,0)
})
test('late permission grant after cancel is disposed without decoding or attaching video',async()=>{
 let release,requests=0
 const c=camera({mediaDevices:{getUserMedia(){requests++;return new Promise(r=>release=r)}}})
 const pending=c.controller.start();await new Promise(r=>setTimeout(r,0));c.controller.stop();release(c.stream);await pending
 assert.equal(requests,1);assert.equal(c.stops(),1);assert.equal(c.controller.state(),'stopped');assert.equal(c.video.srcObject,null);assert.equal(c.texts.length,0)
})
test('first decoded result stops before callback and duplicate frames never emit a second result',async()=>{
 let stoppedBeforeResult=false
 const c=camera({reader:async()=>({decodeFromCanvas:()=>({getText:()=>`wmsqr:v1:${instance}:location:31`})}),onText(){stoppedBeforeResult=c.stops()===1}})
 await c.controller.start();c.tick();assert.ok(stoppedBeforeResult);assert.equal(c.stops(),1);assert.equal(c.controller.state(),'stopped')
})
test('permission denial/insecure browser never selects; background cannot restart automatically',async()=>{
 const denied=camera({mediaDevices:{getUserMedia:async()=>{throw {name:'NotAllowedError'}}}});await denied.controller.start();assert.match(denied.errors[0],/权限/);assert.equal(denied.controller.state(),'stopped')
 const insecure=camera({secure:false});await insecure.controller.start();assert.equal(insecure.requests(),0);assert.match(insecure.errors[0],/HTTPS/)
 let visible=true;const c=camera({visible:()=>visible});await c.controller.start();visible=false;c.tick();assert.equal(c.stops(),1);visible=true;assert.equal(c.requests(),1);await c.controller.start();assert.equal(c.requests(),2);c.controller.stop()
})
test('explicit camera selection and fatal decoder errors release stream',async()=>{
 const c=camera({reader:async()=>({decodeFromCanvas(){throw new Error('fatal')}})});await c.controller.start('camera-two');assert.equal(c.constraints().video.deviceId.exact,'camera-two');assert.equal(c.stops(),1);assert.equal(c.errors.length,1)
})
test('no-code ZXing errors remain recoverable after constructor names are minified',async()=>{
 const c=camera({reader:async()=>({decodeFromCanvas(){throw {name:'minified',getKind:()=> 'NotFoundException'}}})});await c.controller.start();c.tick();assert.equal(c.controller.state(),'scanning');assert.equal(c.errors.length,0);c.controller.stop()
})
function view({read=async()=>row,status='draft'}={}) {
 const map=new Map(),storage={getItem:k=>map.get(k),setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)};let sequence=0
 const store=createTaskStore({storage,instance:'unit-test',user:9,newId:()=>`id-${++sequence}`})
 let task=store.create(putaway.putawayKind,'new','/m/pending');task=store.selectPutaway(task.id,row);store.edit(task.id,fields)
 if(status==='uncertain'){store.begin(task.id,{ready:true,allowed:true});store.settle(task.id,'uncertain')}
 const route={params:{jobId:task.id,step:'scan'},query:{kind:'receipt'},path:putaway.putawayTaskPath(task,'scan'),fullPath:putaway.putawayTaskPath(task,'scan')}
 const calls=[],router={options:{history:{state:{}}},replace:x=>calls.push(x),push:x=>calls.push(x)},auth={user:{id:9,role:'admin'}}
 const code=parse(readFileSync(new URL('../src/mobile/MobilePutaway.vue',import.meta.url),'utf8')).descriptor.scriptSetup.content.replace(/^import .*$/gm,'')
 const deps={...putaway,...qr,qrInstance:()=>instance,readQrObject:read,computed,ref,reactive,watch(){},nextTick:async()=>{},onMounted(){},onBeforeUnmount(){},useRoute:()=>route,useRouter:()=>router,api:{get:async()=>({data:{id:9,role:'admin'}})},auth,canOperate:()=>true,formatQuantity:String,quantityUnits,mobileRevision:ref(0),mobileStore:()=>store,touchMobile(){deps.mobileRevision.value++},lockedTask,taskLabels,readGate,positiveId,returnToParent}
 const result=new Function(...Object.keys(deps),code+'\nloadFields(); return { scanned,scan,cancelScan,form,scanError,busy }')(...Object.values(deps))
 return {...result,store,task:store.get(task.id),route,calls,touch:deps.touchMobile}
}
test('scan/manual share original task and same cargo preserves valid quantity/location/request context',async()=>{
 const state=view();const before=state.store.get(state.task.id);await state.scanned('label')
 const after=state.store.get(state.task.id);assert.deepEqual(after,before);assert.equal(state.calls[0],putaway.putawayTaskPath(before));assert.equal(state.form.quantity,'2.300001');assert.equal(state.form.locationId,'31')
 assert.equal(mobileParentLocation({meta:{mobilePutawayTask:true},params:{step:'scan'},query:{}},before),putaway.putawayTaskPath(before))
 assert.equal(mobileLoginTarget(putaway.putawayTaskPath(before,'scan')),putaway.putawayTaskPath(before,'scan'))
})
test('invalid label or failed GET cannot overwrite choices; uncertain task cannot scan or resubmit',async()=>{
 const failed=view({read:async()=>{throw new Error('读取失败')}});await failed.scanned('label');assert.equal(failed.form.quantity,'2.300001');assert.equal(failed.calls.length,0);assert.match(failed.scanError.value,/读取失败/)
 let reads=0;const locked=view({status:'uncertain',read:async()=>{reads++;return row}});await locked.scanned('label');locked.scan('location');assert.equal(reads,0);assert.equal(locked.calls.length,0);assert.equal(locked.store.get(locked.task.id).status,'uncertain')
})
test('cancel while object GET pending discards late selection; double recognition reads only once',async()=>{
 let release,reads=0;const state=view({read:()=>{reads++;return new Promise(r=>release=r)}})
 const pending=state.scanned('label');await state.scanned('label');await new Promise(r=>setTimeout(r,0));assert.equal(reads,1)
 state.route.fullPath='/m/pending';release({...row,id:502,receipt_movement_id:902});await pending;assert.equal(state.store.get(state.task.id).fields.receiptId,'501');assert.equal(state.calls.length,0)
})
