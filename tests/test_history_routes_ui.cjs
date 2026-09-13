"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
test("trial source links and Back restore the displayed filter without rerunning evaluations", async () => {
  const read = file => fs.readFileSync(path.join(__dirname,"../frontend",file),"utf8");
  const dom = new JSDOM(read("history.html"),{url:"http://localhost/static/history.html?source=quick",runScripts:"outside-only"}), w=dom.window,calls=[];
  const pause=()=>new Promise(r=>setTimeout(r,25));
  w.fetch=async(url,options={})=>{assert.equal(options.method||"GET","GET");calls.push(url);return {ok:true,json:async()=>({trials:[],total:0})};};
  try {
    w.eval(read("navigation.js"));w.eval(read("history.js"));await pause();
    const filter=w.document.getElementById("sourceFilter");
    assert.equal(filter.value,"quick");assert.ok(calls.some(url=>url.includes("source=quick")));
    filter.value="legacy";filter.dispatchEvent(new w.Event("change"));await pause();
    assert.equal(new URL(w.location.href).searchParams.get("source"),"legacy");
    w.history.back();await pause();assert.equal(filter.value,"quick");
    assert.ok(calls.at(-1).includes("source=quick"));
  } finally {w.close();}
});

async function savedHistory(search="", pendingDetail=null) {
  const read=file=>fs.readFileSync(path.join(__dirname,"../frontend",file),"utf8");
  const dom=new JSDOM(read("history.html"),{url:`http://localhost/static/history.html${search}`,runScripts:"outside-only",pretendToBeVisual:true});
  const w=dom.window,calls=[],pause=()=>new Promise(resolve=>setTimeout(resolve,20)),el=id=>w.document.getElementById(id);
  const trial={id:"trial/a",source:"campaign",campaign_id:"campaign-a",task:{task:"Place the red part"},strategy:{name:"Vision pipeline"},status:"completed",created_at:"2026-09-13T08:00:00Z",result:{outcome:"unknown"}};
  w.fetch=async(url,options={})=>{
    assert.equal(options.method||"GET","GET");calls.push(url);let result;
    if(url.startsWith("/api/trials?"))result={trials:[trial],total:60};
    else if(url==="/api/trials/trial%2Fa") {if(pendingDetail)await pendingDetail;result=trial;}
    else if(url.startsWith("/api/trials/trial%2Fa/events?"))result={events:[],total:0};
    else throw new Error(`Unexpected request ${url}`);
    return {ok:true,json:async()=>result};
  };
  w.eval(read("history.js"));await pause();return {dom,w,calls,pause,el};
}

test("saved trials browse full-width then open details and return to the same source and page", async()=>{
  const {dom,w,calls,pause,el}=await savedHistory("?source=campaign&offset=25");
  try {
    assert.equal(el("trialBrowser").hidden,false);assert.equal(el("trialDetailView").hidden,true);
    assert.equal(calls.length,1);assert.match(calls[0],/offset=25.*source=campaign/);
    const row=w.document.querySelector(".trial-row"),action=row.querySelector(".trial-link");
    assert.deepEqual([...row.children].map(child=>child.className),["trial-task","trial-strategy","trial-outcome","trial-created","trial-link trial-action secondary"]);
    assert.match(row.textContent,/Place the red part.*Vision pipeline.*Not scored.*Execution: completed/);
    assert.match(row.querySelector(".trial-source").textContent,/trial\/a/);
    assert.ok(row.querySelector(".trial-time").textContent);
    assert.equal(new URL(action.href).searchParams.get("offset"),"25");
    action.click();await pause();assert.equal(el("trialBrowser").hidden,true);assert.equal(el("trialDetailView").hidden,false);
    assert.equal(el("inspectorHeading").textContent,"Place the red part");
    assert.ok(el("inspector").textContent.includes("Frozen configuration"));
    el("backToTrials").click();assert.equal(el("trialBrowser").hidden,false);assert.equal(el("trialDetailView").hidden,true);
    assert.equal(new URL(w.location.href).searchParams.get("trial"),null);assert.equal(new URL(w.location.href).searchParams.get("offset"),"25");assert.equal(el("sourceFilter").value,"campaign");
    assert.equal(w.document.activeElement,action);
    el("nextPage").click();await pause();assert.match(calls.at(-1),/offset=50/);
    w.history.back();await pause();assert.match(calls.at(-1),/offset=25/);
  } finally {dom.window.close();}
});

test("trial deep links open the inspector directly and Back works without an earlier history entry", async()=>{
  const {dom,w,el,pause}=await savedHistory("?trial=trial%2Fa&source=campaign&compare=another");
  try {
    await pause();assert.equal(el("trialDetailView").hidden,false);assert.equal(el("trialBrowser").hidden,true);
    assert.equal(el("inspectorHeading").textContent,"Place the red part");
    assert.equal(w.document.querySelector('[aria-label="Compare with another saved trial"]').value,"another");
    el("backToTrials").click();assert.equal(el("trialDetailView").hidden,true);assert.equal(el("trialBrowser").hidden,false);assert.equal(new URL(w.location.href).searchParams.get("compare"),null);
  } finally {dom.window.close();}
});

test("returning to trial browser cancels late detail rendering", async()=>{
  let finish;const pending=new Promise(resolve=>{finish=resolve;});
  const {dom,w,el,pause,calls}=await savedHistory("?trial=trial%2Fa",pending);
  try {
    assert.equal(el("trialDetailView").hidden,false);el("backToTrials").click();finish();await pause();
    assert.equal(el("trialDetailView").hidden,true);assert.equal(el("trialBrowser").hidden,false);assert.equal(calls.some(url=>url.includes("/events?")),false);
  } finally {finish();dom.window.close();}
});
