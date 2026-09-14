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
  w.URL.createObjectURL = () => "blob:observation";
  const streams = [];
  w.EventSource = class { constructor(){this.listeners = {}; streams.push(this);} addEventListener(type, listener){this.listeners[type] = listener;} close(){} emit(type, data){this.listeners[type]?.({data: JSON.stringify(data)});} };
  const item = {id: "case-r1", case_id: "case", name: "Block on tray", task: "Place the block in the tray", revision: 1, image_asset: {sha256: "a".repeat(64)}, candidate_context: {robot: "panda", proprioception: [0, 1], constraints: ["Avoid cup"]}, reference_data: {eval_qa: {answer: "private answer"}, expected_subtasks: ["private plan"]}};
  w.fetch = async (url, request = {}) => {
    const route = new URL(url, w.location.href).pathname; calls.push({route, method: request.method || "GET", body: request.body});
    if (route === "/api/cases/case-r1") return {ok: !options.missing, json: async () => item};
    if (route.startsWith("/api/trial-assets/")) return {ok: true, blob: async () => new w.Blob(["image"], {type: "image/png"})};
    if (route.endsWith("panda.urdf")) return {ok: !options.noRobot, blob: async () => new w.Blob(['<robot name="panda"/>'], {type: "application/xml"})};
    if (route === "/api/examples") return {ok: true, json: async () => ({examples: [{task: "Place the block", filename: "block.png", import_status: "imported", case_revision_id: "case-r1", eval_category: "atomic"}]})};
    if (route === "/data/legacy.png") return {ok:true,blob:async()=>{await options.legacyWait;return new w.Blob(["legacy"],{type:"image/png"});}};
    if (route === "/api/evaluate" && options.submissionFails) throw Error("Submission unavailable");
    if (route === "/api/evaluate") return {ok: true, json: async () => ({eval_id: "evaluation", trial_ids: {mock: "trial"}, strategies: ["mock"]})};
    return {ok: true, json: async () => route.includes("history") ? [] : route === "/api/strategies" ? {strategies: options.strategies || [{id: "mock", display_name: "Mock", perceive: "mock-vlm", verify: "mock-vlm"}]} : route === "/api/models" ? {models: []} : {defaults: {}, endpoints: {}, strategies: {}}};
  };
  w.eval(source("navigation.js")); w.eval(source("sample-cases.js") + ";window.createSampleCaseCard = createSampleCaseCard;"); w.eval(source("stage-renderers.js"));
  w.eval(source("strategy-table.js")); w.eval(source("app.js") + ";window.caseRunnerTest={get savedCase(){return selectedSavedCase;},get robot(){return selectedUrdfFile;},setRunning,showHistoryEntry,loadExample,seedHistory(entries){runHistory=entries;renderHistory();}};");
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
