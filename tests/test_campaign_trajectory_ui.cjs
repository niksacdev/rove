"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), {JSDOM} = require("jsdom");
const {render,mount} = require("../frontend/campaign-trajectory.js");
function fixture() {
  const item=id=>({id,name:id,created_at:"2026-09-13T00:00:00Z",status:"completed",revision:"commit-a",evidence_kind:"synthetic_or_mixed",case_count:1,repetitions:2,strategy_versions:[{id:"base",name:"Current pipeline",fingerprint:"abc"}],outcomes:[{strategy_id:"base",passed:1,failed:0,unknown:1,latency_p95_ms:20}]});
  return {campaign_id:"b",as_of:"2026-09-13T01:00:00Z",nodes:[item("a"),item("b")],edges:[{parent_id:"a",campaign_id:"b",changes:["cases","success_metrics"],comparable:false,reasons:["Case revisions changed"],comparisons:[]}]};
}
test("timeline distinguishes changed cases/scoring and current outcomes from frozen baseline evidence", t=>{
  const dom=new JSDOM('<main id="view"></main>');t.after(()=>dom.window.close());const view=dom.window.document.getElementById("view"),data=fixture();
  data.nodes[0].name="<img src=x onerror=alert(1)>";render(view,data);
  assert.equal(view.querySelector("img"),null);assert.equal(view.querySelectorAll(".trajectory-iteration").length,2);
  assert.match(view.textContent,/Cases changedScoring changed/);assert.match(view.textContent,/No improvement claim/);assert.match(view.textContent,/current assessments as of/);assert.match(view.textContent,/1 unassessed/);
  assert.equal(view.querySelectorAll(".trajectory-pairs").length,0);
  assert.equal(view.querySelectorAll(".trajectory-evidence").length,2);assert.match(view.textContent,/Synthetic \/ mock evidence/);
  assert.equal(view.querySelectorAll(".trajectory-outcome-bar").length,2);assert.equal(view.querySelector(".trajectory-details").open,false);
  assert.equal(view.querySelectorAll(".trajectory-actions .trajectory-action").length,4);
  data.edges[0]={parent_id:"a",campaign_id:"b",changes:["strategies"],comparable:true,baseline_revision_id:"frozen-r1",comparisons:[{comparable:true,baseline_strategy_id:"old",candidate_strategy_id:"new",paired_counts:{fail_to_pass:1,pass_to_fail:0,unknown:1},baseline_outcomes:[{passed:0,failed:1,unknown:1}]}]};
  render(view,data);assert.match(view.textContent,/Frozen baseline revision frozen-r1/);assert.match(view.textContent,/Frozen reference: 0 accepted · 1 rejected · 1 unassessed/);assert.match(view.textContent,/1 unresolved pairs/);
  assert.equal(view.querySelectorAll(".trajectory-iteration").length,2);
});
test("late timeline responses cannot replace another campaign and requests are read-only", async t=>{
  const dom=new JSDOM('<main id="view"></main>');t.after(()=>dom.window.close());const view=dom.window.document.getElementById("view");
  let resolveOld;const old=new Promise(resolve=>{resolveOld=resolve;});const calls=[];
  const fetcher=url=>{calls.push(url);return url.includes("/old/")?old:Promise.resolve({ok:true,json:async()=>({...fixture(),nodes:[{...fixture().nodes[0],name:"Latest campaign"}],edges:[]})});};
  const first=mount(view,"old",fetcher);await mount(view,"new",fetcher);resolveOld({ok:true,json:async()=>fixture()});await first;
  assert.match(view.textContent,/Latest campaign/);assert.doesNotMatch(view.textContent,/Cases changed/);assert.deepEqual(calls,["/api/campaigns/old/timeline","/api/campaigns/new/timeline"]);
});
