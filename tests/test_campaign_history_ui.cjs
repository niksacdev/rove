"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), {JSDOM} = require("jsdom");
const frontend = path.resolve(__dirname, "../frontend");
const flush = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };
function payload(id = "selected") {
  const node = identity => ({id:identity,name:identity === id ? "Tray comparison" : "Earlier baseline",created_at:"2026-09-13T00:00:00Z",status:"completed",case_count:2,repetitions:3,outcomes:[],strategy_versions:[]});
  return {campaign_id:id,as_of:"2026-09-13T01:00:00Z",nodes:[node("earlier"),node(id)],edges:[{parent_id:"earlier",campaign_id:id,changes:["strategies"],comparable:false,reasons:["Assessment conditions differ"],comparisons:[]}]};
}
function setup(t, {search="?campaign=selected", respond} = {}) {
  const dom = new JSDOM(fs.readFileSync(path.join(frontend,"campaign-history.html"),"utf8"),{url:`http://localhost/static/campaign-history.html${search}`,runScripts:"outside-only"});
  t.after(()=>dom.window.close()); const calls=[];
  dom.window.fetch = async (url,options) => { calls.push({url,options}); return respond ? respond(url) : {ok:true,json:async()=>payload()}; };
  for (const file of ["navigation.js","campaign-trajectory.js","campaign-history.js"]) dom.window.eval(fs.readFileSync(path.join(frontend,file),"utf8"));
  return {window:dom.window,document:dom.window.document,calls};
}
test("campaign history uses the exact selected lineage and shared navigation without executing",async t=>{
  const {document,calls}=setup(t); await flush();
  assert.equal(document.querySelector("h1").textContent,"Campaign history");
  assert.equal(document.getElementById("campaignHistoryName").textContent,"Tray comparison");
  assert.equal(document.querySelector("#roveNav [aria-current]").textContent,"Campaigns");
  assert.equal(document.querySelectorAll('#roveNav a[aria-label="Settings"]').length,1);
  assert.equal(document.querySelector('link[rel="stylesheet"]:last-of-type').getAttribute("href"),"/static/fluent.css?v=workspace-8");
  assert.equal(document.getElementById("historyViewResults").getAttribute("href"),"/static/datasets.html?step=review&campaign=selected");
  assert.equal(document.querySelector("main > a").getAttribute("href"),"/static/benchmarks.html");
  assert.equal(document.querySelectorAll(".trajectory-iteration").length,2);
  assert.equal(document.querySelector(".trajectory-current").dataset.campaignId,"selected");
  assert.match(document.getElementById("campaignHistoryStatus").textContent,/2 saved iterations/);
  assert.deepEqual(calls,[{url:"/api/campaigns/selected/timeline",options:undefined}]);
});
test("missing or ambiguous campaign IDs never select another campaign or fetch data",async t=>{
  for (const search of ["","?campaign=","?campaign=one&campaign=two"]) {
    const page=setup(t,{search}); await flush();
    assert.equal(page.calls.length,0);
    assert.match(page.document.getElementById("campaignHistoryStatus").textContent,/Choose a campaign/);
    assert.equal(page.document.getElementById("historyViewResults").hidden,true);
    assert.equal(page.document.getElementById("refreshCampaignHistory").disabled,true);
  }
});
test("not-found and mismatched histories have explicit errors and retry the same campaign",async t=>{
  let attempt=0;
  const page=setup(t,{respond:()=>++attempt===1 ? {ok:false,status:404} : attempt===2 ? {ok:true,json:async()=>payload("wrong")} : {ok:true,json:async()=>payload()}});
  await flush(); const el=id=>page.document.getElementById(id);
  assert.match(el("campaignHistoryStatus").textContent,/not found/);
  assert.equal(el("historyViewResults").hidden,true);
  el("refreshCampaignHistory").click(); await flush();
  assert.match(el("campaignHistoryStatus").textContent,/does not match/);
  assert.equal(page.document.querySelectorAll(".trajectory-iteration").length,0);
  el("refreshCampaignHistory").click(); await flush();
  assert.equal(el("historyViewResults").hidden,false);
  assert.equal(page.calls.length,3);
  assert.ok(page.calls.every(call=>call.url==="/api/campaigns/selected/timeline" && !call.options));
});
test("campaign identity links are encoded and names remain text",async t=>{
  const identity="part/a?b",data=payload(identity); data.nodes[1].name="<img src=x onerror=alert(1)>";
  const page=setup(t,{search:"?campaign=part%2Fa%3Fb",respond:()=>({ok:true,json:async()=>data})}); await flush();
  assert.equal(page.calls[0].url,"/api/campaigns/part%2Fa%3Fb/timeline");
  assert.equal(page.document.getElementById("historyViewResults").getAttribute("href"),"/static/datasets.html?step=review&campaign=part%2Fa%3Fb");
  assert.equal(page.document.getElementById("campaignHistoryName").textContent,data.nodes[1].name);
  assert.equal(page.document.querySelector("img"),null);
});
