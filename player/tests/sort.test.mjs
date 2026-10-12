import { test } from "node:test";
import assert from "node:assert/strict";
import { createSorter, sortVisible } from "../src/sort.js";
import { lookAt } from "../src/camera.js";

const identity = new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]);
function model(count = 2048, timelineMode = 0) {
  const value = {
    positionTime: new Float32Array(count*4), velocityDuration: new Float32Array(count*4),
    alpha: new Float32Array(count), opacityFloor:0, timelineMode, useVelocity:true,
  };
  let seed = 90210;
  const random = () => ((seed = Math.imul(seed,1664525)+1013904223 >>> 0) / 2**32);
  for (let i=0; i<count; i++) {
    value.positionTime.set([random()*4-2, random()*4-2, -4+random()*6,
      timelineMode === 1 ? i%6 : random()*1.6-.3], i*4);
    value.velocityDuration.set([random()-.5, random()-.5, random()-.5, 10**(-5+random()*5.3)], i*4);
    value.alpha[i] = i%17===0 ? 0 : i%19===0 ? 1/255 : random();
  }
  return value;
}

test("prepared sorting preserves reference IDs/order across timelines, movement, floors and cameras", () => {
  const cameras = [identity, lookAt([1,-.2,-1],[0,0,-3],[0,1,0])];
  for (const mode of [0,1,2]) for (const motion of [false,true]) for (const floor of [0,.0001,1/255,.01]) {
    const data=model(2048,mode); data.useVelocity=motion; data.opacityFloor=floor;
    const sort=createSorter(data);
    for (const time of [-.3,0,.15,.5,.7,1,2,3,5]) for (const camera of cameras) for (const near of [.001,-Infinity])
      assert.deepEqual(sort(time,camera,near),sortVisible(data,time,camera,near));
  }
});

test("conservative support preserves cutoff boundaries, wide/tiny durations and invalid-parameter fallback", () => {
  const data=model(1);
  data.positionTime.set([0,0,-2,.5]);
  // Float64 also checks callers at the exact threshold, which Float32 cannot represent.
  data.alpha=new Float64Array(1);
  for (const alpha of [0,1/255,Math.fround(1/255),.5,1,NaN]) for (const duration of [1e-20,.01,1,1e9,0,Infinity,NaN]) {
    data.alpha[0]=alpha; data.velocityDuration[3]=duration;
    const width=alpha>=1/255 && duration>0 ? duration*Math.sqrt(2*Math.log(alpha*255)) : 0;
    const sort=createSorter(data);
    for (const time of [.5,.5-1e-12,.5+1e-12,0,1,10,.5+width,.5-width,.5+width*(1+1e-10),NaN])
      assert.deepEqual(sort(time,identity),sortVisible(data,time,identity),`alpha=${alpha}, duration=${duration}, time=${time}`);
  }
});

test("reused scratch preserves stable depth ties, empty frames and ownership across transfer/replacement", () => {
  const data=model(16);
  for(let i=0;i<data.alpha.length;i++) {
    data.positionTime.set([0,0,-2,.5],i*4); data.velocityDuration.set([0,0,0,.01],i*4); data.alpha[i]=.5;
  }
  const sort=createSorter(data), first=sort(.5,identity);
  assert.deepEqual([...first],Array.from({length:16},(_,i)=>i));
  const transferred=structuredClone(first,{transfer:[first.buffer]});
  assert.equal(first.byteLength,0);
  assert.equal(sort(0,identity).length,0);
  assert.deepEqual(sort(.5,identity),transferred);
  const replacement=createSorter(model(3,2));
  assert.deepEqual(replacement(0,identity),sortVisible(model(3,2),0,identity));
  assert.deepEqual(sort(.5,identity),transferred);
});
