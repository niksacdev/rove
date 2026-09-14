"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const source = name => fs.readFileSync(path.resolve(__dirname, "../frontend", name), "utf8");
async function composer(options = {}) {
  const dom = new JSDOM(source("index.html"), {url: "http://localhost/?view=quick&case=case-r1", runScripts: "outside-only", pretendToBeVisual: true});
  const w = dom.window, calls = [];
  w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute("open","");this.querySelector("[autofocus]")?.focus();};
  w.HTMLDialogElement.prototype.close=function(){this.removeAttribute("open");this.dispatchEvent(new w.Event("close"));};
  w.lucide = {createIcons(){}}; w.HTMLElement.prototype.scrollIntoView = () => {};
  w.URL.createObjectURL = () => "blob:observation";w.URL.revokeObjectURL=()=>{};
  const streams = [];
  w.EventSource = class { constructor(){this.listeners = {}; streams.push(this);} addEventListener(type, listener){this.listeners[type] = listener;} close(){} emit(type, data){this.listeners[type]?.({data: JSON.stringify(data)});} };
  const item = {id: "case-r1", case_id: "case", name: "Block on tray", task: "Place the block in the tray", revision: 1, image_asset: {sha256: "a".repeat(64)}, candidate_context: {robot: "panda", proprioception: [0, 1], constraints: ["Avoid cup"]}, reference_data: {eval_qa: {answer: "private answer"}, expected_subtasks: ["private plan"]}};
  w.fetch = async (url, request = {}) => {
    const route = new URL(url, w.location.href).pathname; calls.push({route, method: request.method || "GET", body: request.body});
    if (route === "/api/history") return {ok:true,json:async()=>options.serverHistory || []};
    if (route === "/api/cases/case-r1") return {ok: !options.missing, json: async () => item};
    if (route.startsWith("/api/trial-assets/")) return {ok: true, blob: async () => new w.Blob(["image"], {type: "image/png"})};
    if (route.endsWith("panda.urdf")) return {ok: !options.noRobot, blob: async () => new w.Blob(['<robot name="panda"/>'], {type: "application/xml"})};
    if (route === "/api/examples") return {ok: true, json: async () => ({examples: [{task: "Place the block", filename: "block.png", import_status: "imported", case_revision_id: "case-r1", eval_category: "atomic"}]})};
    if (route === "/data/legacy.png") return {ok:true,blob:async()=>{await options.legacyWait;return new w.Blob(["legacy"],{type:"image/png"});}};
    if (route === "/api/evaluate" && options.submissionFails) throw Error("Submission unavailable");
    if (route === "/api/evaluate") return {ok: true, json: async () => ({eval_id: "evaluation", trial_ids: options.trialIds || {mock: "trial"}, strategies: options.strategies?.map(item=>item.id) || ["mock"]})};
    return {ok: true, json: async () => route.includes("history") ? [] : route === "/api/strategies" ? {strategies: options.strategies || [{id: "mock", display_name: "Mock", perceive: "mock-vlm", verify: "mock-vlm"}]} : route === "/api/models" ? {models: []} : {defaults: {}, endpoints: {}, strategies: {}}};
  };
  w.eval(source("navigation.js")); w.eval(source("sample-cases.js") + ";window.createSampleCaseCard = createSampleCaseCard;"); w.eval(source("stage-renderers.js"));
  w.eval(source("strategy-table.js")); w.eval(source("app.js") + ";window.caseRunnerTest={get savedCase(){return selectedSavedCase;},get robot(){return selectedUrdfFile;},setRunning,showHistoryEntry,loadExample,saveHistory,get history(){return runHistory;},get activeEntry(){return runHistory[activeHistoryIndex];},seedHistory(entries,activeIndex=-1,runningId=null){runHistory=entries;activeHistoryIndex=activeIndex;runningEvaluationId=runningId;renderHistory();}};");
  const pause = () => new Promise(resolve => setTimeout(resolve, 40)); await pause();
  return {w, calls, streams, el: id => w.document.getElementById(id), pause};
}

test("saved case opens original composer with observation, instruction and supported robot, without executing", async () => {
  const {w, calls, el, pause} = await composer();
  try {
    assert.equal(el("quickComposer").hidden, false);
    assert.equal(el("taskInput").value, "Place the block in the tray");
    assert.equal(el("imagePreview").classList.contains("hidden"), false);
    assert.equal(el("urdfPreview").classList.contains("hidden"), false);
    assert.equal(w.caseRunnerTest.robot.name, "panda.urdf");
    assert.match(el("trialCaseContext").textContent, /Block on tray.*revision 1/);
    assert.equal(w._selectedGroundTruth, null); assert.equal(w._selectedExpectedSubtasks, null);
    assert.equal(calls.some(call => call.method === "POST"), false);
    el("strategyGrid").querySelector('[data-strategy-id="mock"]').click(); w.document.querySelector('#quickJourney [data-quick-step="run"]').click();el("evalBtn").click(); await pause();
    const call = calls.find(call => call.route === "/api/evaluate"); assert.ok(call);
    assert.equal(call.body.get("case_revision_id"), "case-r1");
    assert.equal(call.body.get("task"), "Place the block in the tray");
    assert.equal(call.body.get("urdf").name, "panda.urdf");
    assert.equal(call.body.get("example_filename"), null);
  } finally { w.close(); }
});

test("manual task changes clear case identity and inherited robot context before execution", async () => {
  const {w, calls, el, pause} = await composer();
  try {
    el("taskInput").value = "A different task"; el("taskInput").dispatchEvent(new w.Event("input", {bubbles: true}));
    assert.equal(w.caseRunnerTest.savedCase, null);
    assert.equal(w.caseRunnerTest.robot, null); assert.equal(w._selectedProprioception, null);
    assert.equal(new URL(w.location.href).searchParams.has("case"), false);
    el("strategyGrid").querySelector('[data-strategy-id="mock"]').click(); w.document.querySelector('#quickJourney [data-quick-step="run"]').click();el("evalBtn").click(); await pause();
    const call = calls.find(call => call.route === "/api/evaluate"); assert.ok(call);
    assert.equal(call.body.get("case_revision_id"), null); assert.equal(call.body.get("urdf"), null);
    assert.equal(call.body.get("task"), "A different task");
  } finally { w.close(); }
});

test("removing or replacing the observation clears the original case binding", async () => {
  const {w, el} = await composer();
  try { el("removeImage").click(); assert.equal(w.caseRunnerTest.savedCase, null); assert.equal(new URL(w.location.href).searchParams.has("case"), false); }
  finally { w.close(); }
});

test("unavailable robot remains unattached, while unavailable case reports error without execution", async () => {
  const first = await composer({noRobot: true});
  try { assert.equal(first.w.caseRunnerTest.robot, null); assert.equal(first.w.caseRunnerTest.savedCase.id, "case-r1"); assert.match(first.el("trialCaseContext").textContent, /Add a robot description/); }
  finally { first.w.close(); }
  const second = await composer({missing: true});
  try { assert.equal(second.w.caseRunnerTest.savedCase, null); assert.match(second.el("trialCaseContext").textContent, /No case was loaded or executed/); assert.equal(second.calls.some(call => call.method === "POST"), false); }
  finally { second.w.close(); }
});

test("completed saved trial offers Add to campaign with exact durable ID; followup survives reopening without rerun", async () => {
  const {w, calls, streams, el, pause} = await composer();
  try {
    el("strategyGrid").querySelector('[data-strategy-id="mock"]').click(); w.document.querySelector('#quickJourney [data-quick-step="run"]').click();el("evalBtn").click(); await pause();
    assert.equal(w.document.querySelector(".trial-campaign-link"), null, "a running trial is not offered for reuse");
    streams[0].emit("strategy_complete", {strategy_id: "mock", total_latency_ms: 123, success: false});
    assert.equal(w.document.querySelector(".trial-campaign-link"), null, "strategy events precede durable completion");
    streams[0].emit("complete", {trial_ids: {mock: "trial/a?revision=1"}, results: [{strategy_id: "mock", success: false, stages: [{stage: "verify", status: "completed"}]}]});
    const followup = el("tab-content-mock").querySelector(".trial-campaign-link");
    assert.equal(followup.textContent, "Add to campaign");
    assert.equal(followup.getAttribute("href"), "/static/datasets.html?trial=trial%2Fa%3Frevision%3D1");
    assert.equal(new URL(el("tab-content-mock").querySelector('.trial-next-step-actions a:last-child').href).searchParams.get("trial"), "trial/a?revision=1");
    assert.equal(calls.filter(call => call.method === "POST").length, 1, "followup rendering neither saves a campaign nor reruns");
    w.caseRunnerTest.showHistoryEntry(0);
    assert.equal(el("tab-content-mock").querySelector(".trial-campaign-link").getAttribute("href"), followup.getAttribute("href"));
    assert.equal(calls.filter(call => call.method === "POST").length, 1);
  } finally { w.close(); }
});

test("execution error or missing persisted trial identity never fabricates a campaign reuse link", async () => {
  for (const result of [{trial_ids: {}, results: [{strategy_id: "mock", stages: []}]}, {trial_ids: {mock: "failed-trial"}, results: [{strategy_id: "mock", stages: [{stage: "act", status: "error"}]}]}]) {
    const {w, streams, el, pause} = await composer();
    try {
      el("strategyGrid").querySelector('[data-strategy-id="mock"]').click(); w.document.querySelector('#quickJourney [data-quick-step="run"]').click();el("evalBtn").click(); await pause();
      streams[0].emit("complete", result);
      assert.equal(w.document.querySelector(".trial-campaign-link"), null);
    } finally { w.close(); }
  }
});

test("trial sample gallery opens the original composer with a versioned case rather than skipping into campaign setup", async () => {
  const {w, el, calls, pause} = await composer();
  try {
    w.document.querySelector('#quickWelcome [data-root-view="examples"]').click(); await pause();
    const sample = el("examplesView").querySelector('article a');
    assert.equal(sample.getAttribute("href"), "/?view=quick&case=case-r1");
    assert.match(sample.textContent, /Use in a trial/);
    assert.equal(calls.some(call => call.method === "POST"), false);
  } finally { w.close(); }
});


test("shared quick journey runs one case against two strategies through existing SSE and preserves inputs for refinement",async()=>{
  const {w,calls,streams,el,pause}=await composer({strategies:[{id:"mock",display_name:"Planner",perceive:"vlm",plan:"agent",verify:"grader",pipeline_mode:"sequential"},{id:"vla",display_name:"VLA",act:"policy",verify:"grader",pipeline_mode:"parallel",verification_checks:[{endpoint:"collision",role:"constraint",required:true}]}]});
  const step=name=>w.document.querySelector(`#quickJourney [data-quick-step="${name}"]`).click();
  try{
    assert.equal(el("quickCasePanel").hidden,false);assert.equal(el("quickComposer").parentElement.id,"quickCaseComposer");
    const skipTarget=w.document.querySelector(w.document.querySelector(".skip-link").getAttribute("href"));assert.equal(skipTarget.tagName,"MAIN");assert.equal(skipTarget.hidden,false);assert.equal(skipTarget.contains(el("quickWorkspace")),true);
    step("configure");assert.equal(el("quickConfigurePanel").hidden,false);assert.equal(el("quickComposer").hidden,true);
    assert.match(el("strategyGrid").querySelector('[data-strategy-id="vla"]').textContent,/policy.*grader.*collision.*Required constraint/);
    for(const id of ["mock","vla"])el("strategyGrid").querySelector(`input[value="${id}"]`).click();
    assert.match(el("quickTrialCount").textContent,/1 case × 2 strategies × 1 attempt = 2 trials/);
    step("case");assert.equal(el("taskInput").value,"Place the block in the tray");
    assert.equal(calls.some(call=>call.method==="POST"),false);w.document.querySelector('#quickJourney [data-quick-step="run"]').click();el("evalBtn").click();await pause();
    const submissions=calls.filter(call=>call.route==="/api/evaluate");assert.equal(submissions.length,1);
    assert.equal(submissions[0].body.get("strategy_ids"),"mock,vla");assert.equal(submissions[0].body.get("urdf").name,"panda.urdf");assert.equal(submissions[0].body.get("case_revision_id"),"case-r1");
    assert.equal(el("quickRunPanel").hidden,false);assert.equal(el("chatArea").hidden,false);assert.equal(el("taskInput").disabled,true);
    assert.equal(el("taskInput").value,"Place the block in the tray");assert.ok(el("quickCaseComposer"));
    streams[0].emit("strategy_started",{strategy_id:"vla",pipeline_mode:"parallel"});
    streams[0].emit("stage",{strategy_id:"mock",stage:"perceive",status:"completed",latency_ms:14,model_id:"vlm",output:{scene_description:"Block observed on tray"}});
    el("tabBar").querySelector('[data-tab-id="mock"]').click();assert.match(el("tab-content-mock").textContent,/Block observed on tray/);
    for(const sid of ["mock","vla"])streams[0].emit("strategy_complete",{strategy_id:sid,success:true,total_latency_ms:100});
    streams[0].emit("complete",{trial_ids:{mock:"trial-a",vla:"trial-b"},results:[{strategy_id:"mock",stages:[]},{strategy_id:"vla",stages:[]}]});
    assert.equal(el("quickResultsPanel").hidden,false);assert.equal(el("quickRunAction").hidden,true);assert.equal(el("taskInput").disabled,false);
    assert.equal(el("tab-content-__summary__").querySelectorAll(".trial-campaign-link").length,2);
    step("case");el("taskInput").value="Refined placement instruction";el("taskInput").dispatchEvent(new w.Event("input",{bubbles:true}));
    assert.equal(el("strategyGrid").querySelectorAll("input:checked").length,2);assert.equal(calls.filter(call=>call.route==="/api/evaluate").length,1);
    step("results");assert.match(el("quickTrialCount").textContent,/2 trials/);assert.ok(el("tab-content-mock"));
  }finally{w.close();}
});

test("quick steps gate missing configuration and never execute from Case",async()=>{
  const {w,calls,el,pause}=await composer();
  const step=name=>w.document.querySelector(`#quickJourney [data-quick-step="${name}"]`);
  try {
    assert.equal(step("run").disabled,true);assert.equal(step("results").disabled,true);
    el("evalBtn").click();await pause();assert.equal(el("quickCasePanel").hidden,false);
    assert.equal(calls.some(call=>call.method==="POST"),false);
    step("configure").click();assert.equal(el("quickConfigurePanel").hidden,false);
    el("strategyGrid").querySelector("input").click();assert.equal(step("run").disabled,false);
    step("case").click();el("removeImage").click();assert.equal(step("configure").disabled,true);assert.equal(step("run").disabled,true);
    el("evalBtn").click();await pause();assert.equal(calls.some(call=>call.method==="POST"),false);
  }finally{w.close();}
});


test("reopening an older recent run cannot replace the active stream's output or history target",async()=>{
  const {w,el,calls,streams,pause}=await composer();
  try {
    w.caseRunnerTest.seedHistory([{id:"older",task:"Old task",strategyIds:["old-strategy"],timestamp:"2026-01-01T00:00:00Z",status:"completed",results:{"old-strategy":{stages:[],success:true}},summaryResults:{}}]);
    el("strategyGrid").querySelector("input").click();w.document.querySelector('#quickJourney [data-quick-step="run"]').click();el("evalBtn").click();await pause();
    const live=el("tab-content-mock");assert.ok(live);
    w.caseRunnerTest.showHistoryEntry(0);await pause();
    assert.equal(el("tab-content-mock"),live);assert.equal(el("tab-content-old-strategy"),null);
    assert.match(el("liveAnnouncer").textContent,/comparison is running/);
    streams[0].emit("stage",{strategy_id:"mock",stage:"perceive",status:"completed",latency_ms:12,model_id:"mock-vlm",output:{scene_description:"Live evidence remains attached"}});
    assert.match(live.textContent,/Live evidence remains attached/);
    assert.equal(calls.filter(call=>call.route==="/api/evaluate").length,1);
    streams[0].emit("complete",{trial_ids:{mock:"trial-current"},results:[{strategy_id:"mock",stages:[]}]});
    w.caseRunnerTest.showHistoryEntry(0);assert.ok(el("tab-content-old-strategy"));
  }finally{w.close();}
});


test("legacy sample loading cannot replace the active comparison's locked case",async()=>{
  const {w,el,calls,pause}=await composer();
  try {
    el("strategyGrid").querySelector("input").click();w.document.querySelector('#quickJourney [data-quick-step="run"]').click();el("evalBtn").click();await pause();
    const input=el("taskInput").value,image=el("previewImg").src,robot=w.caseRunnerTest.robot,source=w.caseRunnerTest.savedCase;
    await w.caseRunnerTest.loadExample({filename:"legacy.png",task:"Different sample task"});await pause();
    assert.equal(el("taskInput").value,input);assert.equal(el("previewImg").src,image);assert.equal(w.caseRunnerTest.robot,robot);assert.equal(w.caseRunnerTest.savedCase,source);
    assert.equal(calls.some(call=>call.route==="/data/legacy.png"),false);assert.equal(el("taskInput").disabled,true);assert.match(el("liveAnnouncer").textContent,/Finish the running comparison/);
  }finally{w.close();}
});


test("Results opens at the outcome summary and queued stream scrolling cannot push it back to the bottom",async()=>{
  const {w,el,streams,pause}=await composer();
  try {
    const frames=[];w.requestAnimationFrame=callback=>{frames.push(callback);return frames.length;};
    Object.defineProperty(el("chatArea"),"scrollHeight",{value:1800,configurable:true});
    el("strategyGrid").querySelector("input").click();w.document.querySelector('#quickJourney [data-quick-step="run"]').click();el("evalBtn").click();await pause();
    streams[0].emit("stage",{strategy_id:"mock",stage:"perceive",status:"completed",model_id:"mock-vlm",output:{scene_description:"Observed"}});
    el("chatArea").scrollTop=1200;
    streams[0].emit("complete",{trial_ids:{mock:"trial"},results:[{strategy_id:"mock",stages:[]}]});
    assert.equal(el("chatArea").scrollTop,0);for(const frame of frames.splice(0))frame();assert.equal(el("chatArea").scrollTop,0);
    w.document.querySelector('#quickJourney [data-quick-step="case"]').click();el("chatArea").scrollTop=900;
    w.document.querySelector('#quickJourney [data-quick-step="results"]').click();for(const frame of frames.splice(0))frame();assert.equal(el("chatArea").scrollTop,0);
    el("chatArea").scrollTop=600;w.caseRunnerTest.showHistoryEntry(0);for(const frame of frames.splice(0))frame();assert.equal(el("chatArea").scrollTop,0);
  }finally{w.close();}
});


test("late legacy sample responses cannot overwrite newer inputs or a comparison snapshot",async()=>{
  for(const scenario of ["inputs","running","completed"]){
    let release;const legacyWait=new Promise(resolve=>{release=resolve;});const {w,el,calls,streams,pause}=await composer({legacyWait});
    try {
      const original=el("taskInput").value,image=el("previewImg").src;
      const loading=w.caseRunnerTest.loadExample({filename:"legacy.png",task:"Late task replacement"});await pause();
      assert.equal(w.caseRunnerTest.savedCase.id,"case-r1","pending sample preserves the current bound case");
      if(scenario==="inputs"){el("taskInput").value="More recent instruction";el("taskInput").dispatchEvent(new w.Event("input",{bubbles:true}));}
      else {el("strategyGrid").querySelector("input").click();w.document.querySelector('#quickJourney [data-quick-step="run"]').click();el("evalBtn").click();await pause();const submitted=calls.find(call=>call.route==="/api/evaluate");assert.equal(submitted.body.get("case_revision_id"),"case-r1");assert.equal(submitted.body.get("urdf").name,"panda.urdf");if(scenario==="completed")streams[0].emit("complete",{trial_ids:{mock:"trial"},results:[{strategy_id:"mock",stages:[]}]});}
      release();await loading;await pause();
      assert.equal(el("taskInput").value,scenario==="inputs"?"More recent instruction":original);assert.equal(el("previewImg").src,image);assert.equal(w._selectedExampleFilename,null);
      assert.equal(calls.filter(call=>call.route==="/api/evaluate").length,scenario==="inputs"?0:1);
      assert.match(el("liveAnnouncer").textContent,/Sample loading cancelled/);
    }finally{release();w.close();}
  }
});

test("review shows submitted inputs together and is the only launch boundary",async()=>{
  const {w,el,calls,streams,pause}=await composer();
  const step=name=>w.document.querySelector(`#quickJourney [data-quick-step="${name}"]`);
  try {
    const enter=new w.KeyboardEvent("keydown",{key:"Enter",bubbles:true,cancelable:true});el("taskInput").dispatchEvent(enter);
    assert.equal(enter.defaultPrevented,false);assert.equal(calls.some(c=>c.method==="POST"),false);
    assert.equal(el("quickRunAction").closest('[data-quick-panel]').dataset.quickPanel,"run");
    el("quickContinueConfigure").click();assert.equal(el("quickConfigurePanel").hidden,false);assert.equal(el("quickContinueReview").disabled,true);
    el("strategyGrid").querySelector("input").click();assert.equal(el("quickContinueReview").disabled,false);
    el("evalBtn").dispatchEvent(new w.MouseEvent("click",{bubbles:true}));await pause();assert.equal(calls.some(c=>c.method==="POST"),false);
    el("quickContinueReview").click();assert.equal(el("quickRunPanel").hidden,false);
    assert.match(el("quickReviewSummary").textContent,/Place the block in the tray/);assert.match(el("quickReviewSummary").textContent,/panda.urdf/);assert.match(el("quickReviewSummary").textContent,/Mock/);assert.ok(el("quickReviewSummary").querySelector("img"));
    assert.equal(el("evalBtn").disabled,false);assert.equal(calls.some(c=>c.method==="POST"),false);
    // An edit arriving after review must not silently authorize the new input.
    el("taskInput").value="Place it gently";el("taskInput").dispatchEvent(new w.Event("input",{bubbles:true}));
    assert.equal(el("evalBtn").disabled,true);el("evalBtn").dispatchEvent(new w.MouseEvent("click",{bubbles:true}));await pause();assert.equal(calls.some(c=>c.method==="POST"),false);
    step("configure").click();el("quickContinueReview").click();assert.match(el("quickReviewSummary").textContent,/Place it gently/);
    el("evalBtn").click();el("evalBtn").dispatchEvent(new w.MouseEvent("click",{bubbles:true}));await pause();assert.equal(calls.filter(c=>c.route==="/api/evaluate").length,1);
    for(const control of w.document.querySelectorAll('#quickJourney button'))assert.equal(control.disabled,true);
    streams[0].emit("complete",{trial_ids:{mock:"saved"},results:[{strategy_id:"mock",stages:[]}]});
    assert.equal(el("quickResultsPanel").hidden,false);el("quickStartAnother").click();assert.equal(el("quickCasePanel").hidden,false);assert.equal(el("taskInput").value,"Place it gently");assert.equal(calls.filter(c=>c.route==="/api/evaluate").length,1);
  }finally{w.close();}
});

test("unsaved trial navigation can be cancelled and completed unchanged inputs no longer warn",async()=>{
  const {w,el,calls,streams,pause}=await composer();
  const unload=()=>{const event=new w.Event("beforeunload",{cancelable:true});w.dispatchEvent(event);return event.defaultPrevented;};
  const click=element=>{const event=new w.MouseEvent("click",{bubbles:true,cancelable:true,button:0});element.dispatchEvent(event);return event;};
  try {
    assert.equal(unload(),true);
    const back=w.document.querySelector('#quickHeading a'),location=w.location.href,task=el("taskInput").value;
    assert.equal(click(back).defaultPrevented,true);assert.equal(el("quickLeaveDialog").open,true);assert.match(el("quickLeaveMessage").textContent,/unsaved/i);el("quickLeaveStay").click();assert.equal(w.location.href,location);assert.equal(el("taskInput").value,task);
    click(w.document.querySelector('#roveNav a[data-nav-section="configure"]'));el("quickLeaveStay").click();assert.equal(w.location.href,location);assert.equal(el("quickCasePanel").hidden,false);
    el("quickContinueConfigure").click();el("strategyGrid").querySelector("input").click();el("quickContinueReview").click();assert.equal(el("quickLeaveDialog").open,false);
    el("evalBtn").click();await pause();click(back);assert.match(el("quickLeaveTitle").textContent,/running/i);el("quickLeaveStay").click();assert.equal(unload(),true);
    streams[0].emit("complete",{trial_ids:{mock:"saved"},results:[{strategy_id:"mock",stages:[]}]});assert.equal(unload(),false);
    el("quickStartAnother").click();assert.equal(unload(),false);
    el("taskInput").value="A different task";el("taskInput").dispatchEvent(new w.Event("input",{bubbles:true}));assert.equal(unload(),true);
    assert.equal(calls.filter(c=>c.route==="/api/evaluate").length,1);
  }finally{w.close();}
});

test("cancelling browser back preserves the unfinished trial URL and inputs",async()=>{
  const {w,el,pause}=await composer();
  try {
    const original=w.location.href;
    w.history.replaceState({},"","/");w.dispatchEvent(new w.PopStateEvent("popstate"));await pause();
    assert.equal(w.location.href,original);assert.equal(el("quickCasePanel").hidden,false);assert.match(el("taskInput").value,/Place the block/);assert.equal(el("quickLeaveDialog").open,true);assert.match(el("quickLeaveMessage").textContent,/unsaved/i);el("quickLeaveStay").click();
  }finally{w.close();}
});

test("failed submission retains unsaved protection",async()=>{
  const {w,el,pause}=await composer({submissionFails:true});
  try {
    el("quickContinueConfigure").click();el("strategyGrid").querySelector("input").click();el("quickContinueReview").click();el("evalBtn").click();await pause();
    const event=new w.Event("beforeunload",{cancelable:true});w.dispatchEvent(event);assert.equal(event.defaultPrevented,true);
    assert.equal(w.document.querySelector(".trial-campaign-link"),null);
  }finally{w.close();}
});

test("leave dialog defaults to staying and Escape restores the draft",async()=>{
  const {w,el}=await composer();
  try {
    const link=w.document.querySelector('#quickHeading a'),original=w.location.href;
    link.focus();link.click();
    assert.equal(el("quickLeaveDialog").open,true);assert.equal(w.document.activeElement,el("quickLeaveStay"));
    el("quickLeaveDialog").dispatchEvent(new w.Event("cancel",{cancelable:true}));
    assert.equal(el("quickLeaveDialog").open,false);assert.equal(w.location.href,original);assert.equal(w.document.activeElement,link);
    const event=new w.MouseEvent("click",{bubbles:true,cancelable:true,button:0,ctrlKey:true});link.dispatchEvent(event);
    assert.equal(event.defaultPrevented,false);assert.equal(el("quickLeaveDialog").open,false);
  }finally{w.close();}
});

test("keyboard task completion updates step readiness without launching",async()=>{
  const {w,el,calls}=await composer();
  try {
    el("taskInput").value="";el("taskInput").dispatchEvent(new w.Event("input",{bubbles:true}));assert.equal(el("quickContinueConfigure").disabled,true);
    el("taskInput").dispatchEvent(new w.KeyboardEvent("keydown",{key:"Tab",bubbles:true,cancelable:true}));
    assert.ok(el("taskInput").value.trim());assert.equal(el("quickContinueConfigure").disabled,false);assert.equal(calls.some(c=>c.method==="POST"),false);
  }finally{w.close();}
});

test("approved Settings navigation keeps a running comparison stream and draft intact",async()=>{
  const {w,el,streams,pause}=await composer();
  try {
    el("quickContinueConfigure").click();el("strategyGrid").querySelector("input").click();el("quickContinueReview").click();el("evalBtn").click();await pause();
    w.document.querySelector('#roveNav [data-nav-section="configure"]').click();assert.equal(el("quickLeaveDialog").open,true);
    el("quickLeaveConfirm").click();assert.equal(w.location.search,"?view=strategies");assert.equal(el("quickLeaveDialog").open,false);
    w.document.querySelector('#configureHeading [data-root-view="models"]').click();assert.equal(el("quickLeaveDialog").open,false);assert.equal(w.location.search,"?view=models");
    streams[0].emit("complete",{trial_ids:{mock:"saved"},results:[{strategy_id:"mock",stages:[]}]});
    assert.match(el("taskInput").value,/Place the block/);
    w.history.pushState({},"","/?view=quick");w.dispatchEvent(new w.PopStateEvent("popstate"));
    assert.equal(el("quickResultsPanel").hidden,false);assert.equal(streams.length,1);
  }finally{w.close();}
});

test("three-strategy inspection keeps overview navigation and exact trace destinations during execution and after completion",async()=>{
  const strategies=["a","b","c"].map(id=>({id,display_name:`Pipeline ${id}`,perceive:"vlm",verify:"grader"}));
  const {w,el,streams,calls,pause}=await composer({strategies,trialIds:{a:"trial/a",b:"trial-b",c:"trial-c"}});
  try {
    for(const {id} of strategies)el("strategyGrid").querySelector(`input[value="${id}"]`).click();
    w.document.querySelector('#quickJourney [data-quick-step="run"]').click();el("evalBtn").click();await pause();
    const nav=el("trialOutputNavigation"),overview=el("tabBar").querySelector('[data-tab-id="__summary__"]');
    const main=w.document.querySelector(".root-main");
    Object.defineProperty(main,"clientHeight",{value:640});Object.defineProperty(nav,"offsetHeight",{value:100});
    main.getBoundingClientRect=()=>({top:60});nav.getBoundingClientRect=()=>({top:490});
    assert.equal(nav.hidden,false);assert.equal(overview.textContent,"All strategies");
    assert.equal(el("quickTrialInspect").hidden,true,"overview cannot claim one selected trial");
    Object.defineProperty(el("chatArea"),"scrollHeight",{configurable:true,value:4000});
    for(const {id} of strategies){
      streams[0].emit("stage",{strategy_id:id,stage:"perceive",status:"completed",latency_ms:10,output:{scene_description:`Evidence ${id}`}});
      el("chatArea").scrollTop=2500;
      el("tabBar").querySelector(`[data-tab-id="${id}"]`).click();await pause();
      assert.equal(nav.hidden,false);assert.equal(el("chatArea").scrollTop,0,"selecting output cancels pending bottom scroll");
      assert.equal(el("chatArea").style.minHeight,"540px","output reserves space so the root can scroll the setup header away");
      assert.ok(main.scrollTop>=430,"the user selection moves the strategy navigation to the root viewport top");
      assert.match(el(`tab-content-${id}`).textContent,new RegExp(`Evidence ${id}`));
      assert.equal(el(`tab-content-${id}`).classList.contains("hidden"),false);
      assert.equal(el("quickTrialInspect").hash,"#traces");assert.equal(new URL(el("quickTrialInspect").href).searchParams.get("trial"),id==="a"?"trial/a":`trial-${id}`);
      assert.equal(el("quickTrialInspect").target,"_blank","trace inspection keeps the running comparison open");
      overview.click();assert.equal(el("tab-content-__summary__").classList.contains("hidden"),false);assert.equal(el("quickTrialInspect").hidden,true);
    }
    overview.dispatchEvent(new w.KeyboardEvent("keydown",{key:"End",bubbles:true}));
    assert.equal(el("tabBar").querySelector('[aria-selected="true"]').dataset.tabId,"c");
    streams[0].emit("complete",{trial_ids:{a:"trial/a",b:"trial-b",c:"trial-c"},results:strategies.map(({id})=>({strategy_id:id,stages:[]}))});
    assert.equal(el("quickResultsPanel").hidden,false);assert.equal(nav.hidden,false);
    overview.click();assert.equal(el("tab-content-__summary__").classList.contains("hidden"),false);
    assert.equal(calls.filter(call=>call.route==="/api/evaluate").length,1,"inspection never reruns a strategy");
    el("quickStartAnother").click();assert.equal(el("chatArea").style.minHeight,"","setup returns to its normal layout");
    assert.match(source("styles.css"),/#trialOutputNavigation\s*\{[^}]*position:sticky/);
  }finally{w.close();}
});

test("newest-first server history retains the latest 50 comparisons and their exact durable trial IDs",async()=>{
  const serverHistory=Array.from({length:107},(_,index)=>({eval_id:`evaluation-${107-index}`,task:`Task ${107-index}`,timestamp:new Date(Date.UTC(2026,0,1,0,107-index)).toISOString(),status:"completed",strategy_ids:["a","b","c"],trial_ids:{a:`trial-${107-index}-a`,b:`trial-${107-index}-b`,c:`trial-${107-index}-c`},results:["a","b","c"].map(strategy_id=>({strategy_id,success:true,stages:[]}))}));
  const {w,el}=await composer({serverHistory,strategies:["a","b","c"].map(id=>({id,display_name:id,perceive:"model"}))});
  try{
    const restored=JSON.parse(w.localStorage.getItem("rove-history"));
    assert.equal(restored.length,50);assert.equal(restored.some(item=>item.id==="evaluation-107"),true);assert.equal(restored.some(item=>item.id==="evaluation-58"),true);assert.equal(restored.some(item=>item.id==="evaluation-57"),false);
    assert.deepEqual(restored.find(item=>item.id==="evaluation-107").trialIds,{a:"trial-107-a",b:"trial-107-b",c:"trial-107-c"});
    assert.match(el("historyItems").querySelector(".history-open").textContent,/Task 107/);
    el("historyItems").querySelector(".history-open").click();
    el("tabBar").querySelector('[data-tab-id="b"]').click();
    assert.equal(new URL(el("quickTrialInspect").href).searchParams.get("trial"),"trial-107-b");
  }finally{w.close();}
});

test("chronological history trimming preserves the selected entry and a distinct live stream target",async()=>{
  const {w}=await composer();
  try{
    const entries=Array.from({length:107},(_,index)=>({id:`entry-${index}`,task:`Task ${index}`,strategyIds:["agent"],timestamp:new Date(Date.UTC(2026,0,1,0,index)).toISOString(),status:index===1?"running":"completed",trialIds:{agent:`trial-${index}`},results:{},summaryResults:{}}));
    w.caseRunnerTest.seedHistory(entries,0,"entry-1");w.caseRunnerTest.saveHistory();
    assert.equal(w.caseRunnerTest.history.length,50);assert.equal(w.caseRunnerTest.activeEntry,entries[0]);
    assert.ok(w.caseRunnerTest.history.includes(entries[1]));assert.ok(w.caseRunnerTest.history.includes(entries[106]));
    assert.equal(w.caseRunnerTest.history.includes(entries[2]),false);
    w.caseRunnerTest.saveHistory();assert.equal(w.caseRunnerTest.activeEntry,entries[0],"repeat saves do not move selection to another comparison");
    assert.equal(w.caseRunnerTest.history.find(entry=>entry.id==="entry-1").trialIds.agent,"trial-1");
  }finally{w.close();}
});

test("restored summary uses recorded stage outcomes and does not invent pending stages for completed strategies",async()=>{
  const serverHistory=[{eval_id:"saved",task:"Inspect the scene",timestamp:"2026-09-14T10:00:00Z",status:"completed",strategy_ids:["scene","unrecorded"],trial_ids:{scene:"scene-trial",unrecorded:"legacy-trial"},results:[{strategy_id:"scene",success:null,stages:[{stage:"perceive",status:"completed",output:{}},{stage:"plan",status:"skipped"}]},{strategy_id:"unrecorded",success:null,stages:[]}]}];
  const {w,el}=await composer({serverHistory});
  try{
    el("historyItems").querySelector(".history-open").click();
    const overview=el("tab-content-__summary__");
    assert.ok(overview.querySelector('[aria-label="perceive completed"].done'));
    assert.ok(overview.querySelector('[aria-label="plan not run"]'));
    assert.equal(overview.querySelector('[aria-label="act pending"]'),null);assert.equal(overview.querySelector('[aria-label="verify pending"]'),null);
    assert.match(overview.textContent,/No stages recorded/);
    const saved=w.caseRunnerTest.history[0];assert.equal(saved.summaryResults.scene.status,"completed","missing verdict is not execution failure");
    saved.summaryResults.scene.stageStatuses={};
    w.caseRunnerTest.showHistoryEntry(0);
    assert.ok(el("tab-content-__summary__").querySelector('[aria-label="perceive completed"].done'),"old browser caches are repaired from preserved stages too");
  }finally{w.close();}
});
