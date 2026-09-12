"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
const {JSDOM}=require("jsdom");

test("assistant writes require explicit confirmation and named baseline selects immutable revision",async()=>{
 const root=path.resolve(__dirname,".."),dom=new JSDOM(fs.readFileSync(path.join(root,"frontend/datasets.html"),"utf8"),{url:"http://localhost/static/datasets.html",runScripts:"outside-only"}),w=dom.window,calls=[];
 const summary={completed_trials:1,planned_trials:1,tasks:[{passed:0,failed:0,unknown:1}],strategies:[]};
 const campaign={id:"campaign",status:"completed",contract_id:"contract",spec:{name:"Completed baseline",strategies:["strategy"],tasks:[{id:"case"}],seeds:[1]}};
 let named=[];const frozen={id:"baseline",revision_id:"revision-1",revision:1,name:"Release",campaign_id:"campaign",strategy_id:"strategy",pinned:true,note:"Unknown remains unknown",trial_outcomes:[],summary};
 w.HTMLElement.prototype.scrollIntoView=()=>{};w.HTMLElement.prototype.focus=()=>{};
 w.fetch=async(url,opts={})=>{
  const method=opts.method||"GET",body=typeof opts.body==="string"?JSON.parse(opts.body):opts.body||null;calls.push({url,method,body});let value;
  if(url==="/api/evidence-assets")value={sha256:"a".repeat(64),size_bytes:14,media_type:"application/json"};
  else if(url==="/api/assistant/status")value={available:true};
  else if(url==="/api/assistant/ask")value={answer:"<img src=x> Prepared a contract",evidence:[{label:"unsafe",url:"javascript:alert(1)"}],proposals:[{kind:"create_contract",operation_id:"operation",confirmation_token:"host-token",request:{contract:{name:"Quality"}},preview:{ready:true,note:"Creates an immutable contract"}}]};
  else if(url==="/api/assistant/confirm")value={kind:"create_contract",id:"contract",name:"Quality"};
  else if(url.startsWith("/api/cases?"))value={cases:[]};
  else if(url==="/api/success-contracts")value={contracts:[{id:"contract",name:"Quality",criteria:[]}]};
  else if(url.startsWith("/api/datasets?"))value={datasets:[]};
  else if(url==="/api/strategies")value={strategies:[{id:"strategy",display_name:"Strategy"}]};
  else if(url==="/api/campaigns")value=[{id:"campaign",name:"Completed baseline",status:"completed"}];
  else if(url==="/api/campaigns/campaign")value={campaign,summary};
  else if(url==="/api/campaigns/campaign/assessments")value={summary,assessments:{scope:"success_contract"},trials:[]};
  else if(url==="/api/baselines"&&method==="POST"){named=[frozen];value=frozen;}
  else if(url==="/api/baselines")value={baselines:named};
  else if(url==="/api/baselines/baseline")value=frozen;
  else if(url==="/api/baselines/baseline/revisions")value={revisions:[frozen]};
  else if(url==="/api/campaigns/campaign/ablation-preview")value={ready:true,planned_trials:1,comparison:{comparable:true,reasons:[],differences:[]}};
  else throw Error("Unexpected request "+method+" "+url);
  return {ok:true,status:200,json:async()=>value};
 };
 const pause=()=>new Promise(resolve=>setTimeout(resolve,15)),el=id=>w.document.getElementById(id),set=(id,value,type="input")=>{el(id).value=value;el(id).dispatchEvent(new w.Event(type,{bubbles:true}));},submit=id=>el(id).dispatchEvent(new w.Event("submit",{bubbles:true,cancelable:true}));
 try{
  w.eval(fs.readFileSync(path.join(root,"frontend/navigation.js"),"utf8"));w.eval(fs.readFileSync(path.join(root,"frontend/datasets.js"),"utf8"));await pause();
  Object.defineProperty(el("recordingFile"),"files",{value:[new w.File(['{"records":[]}'],"recording.json",{type:"application/json"})]});
  el("recordingFile").dispatchEvent(new w.Event("change",{bubbles:true}));await pause();
  assert.equal(el("episodeEvidence").value,"");assert.equal(el("attachRecording").disabled,false);
  el("attachRecording").click();const recording=JSON.parse(el("episodeEvidence").value);
  assert.equal(recording.evidence_refs[0].asset_sha256,"a".repeat(64));assert.equal(recording.task_completed,undefined);assert.equal(recording.quality,undefined);assert.equal(recording.evidence_refs[0].clock_id,undefined);
  assert.equal(calls.filter(c=>c.url==="/api/cases"&&c.method==="POST").length,0);
  set("assistantQuestion","Prepare a contract");submit("assistantForm");await pause();
  assert.equal(calls.filter(c=>c.url==="/api/assistant/confirm").length,0);assert.equal(el("assistantResponse").querySelectorAll("img,a").length,0);
  const confirm=[...el("assistantResponse").querySelectorAll("button")].find(b=>b.textContent==="Confirm success contract");assert.ok(confirm);confirm.click();await pause();
  assert.deepEqual(calls.find(c=>c.url==="/api/assistant/confirm").body,{operation_id:"operation",confirmation_token:"host-token",confirmed:true});
  assert.match(el("assistantResponse").textContent,/Confirmed change saved/);
  set("resultCampaign","campaign","change");await pause();set("namedBaselineName","Release");submit("namedBaselineForm");await pause();
  const save=calls.find(c=>c.url==="/api/baselines"&&c.method==="POST");assert.ok(save.body.operation_id);assert.equal(save.body.campaign_id,"campaign");assert.equal(el("baselineRevisionHistory").value,"revision-1");
  set("candidateStrategy","strategy");el("previewAblation").click();await pause();
  assert.equal(calls.find(c=>c.url.endsWith("ablation-preview")).body.baseline_revision_id,"revision-1");
 }finally{w.close();}
});
