"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const root = path.resolve(__dirname, ".."), read = name => fs.readFileSync(path.join(root, "frontend", name), "utf8");
const pause = () => new Promise(resolve => setTimeout(resolve, 25));

for (const [file, section] of [["index.html","start"],["datasets.html","results"],["benchmarks.html","results"],["history.html","evaluate"],["campaign-history.html","results"]]) {
  test(`${file} shares exactly the same primary links and accessible theme control`, () => {
    const dom = new JSDOM(read(file), {url:`http://localhost${file === "index.html" ? "/" : `/static/${file}`}`,runScripts:"outside-only"}), w = dom.window;
    try {
      w.document.documentElement.dataset.theme = "dark";
      w.eval(read("navigation.js"));
      const links = [...w.document.querySelectorAll('#roveNav nav a')];
      assert.deepEqual(links.map(a=>[a.textContent,a.getAttribute('href')]), [["Trials","/static/history.html"],["Campaigns","/static/benchmarks.html"]]);
      const active = w.document.querySelectorAll("#roveNav [aria-current=page]");
      assert.equal(active.length,1);
      assert.equal(active[0].dataset.navSection,section);
      if (file !== "index.html") {
        w.RoveNavigation.setActive(section === "evaluate" ? "results" : "evaluate");
        assert.equal(w.document.querySelector("#roveNav [aria-current=page]").dataset.navSection,section,"legacy step names cannot switch the selected resource");
      }
      const settings = w.document.querySelector("#roveNav .rove-utilities .rove-settings");
      assert.equal(settings.getAttribute("href"), "/?view=strategies");
      assert.equal(settings.getAttribute("aria-label"),"Settings");
      assert.equal(settings.title,"Settings");
      assert.ok(settings.querySelector("svg"));
      assert.equal(settings.closest("nav"),null);
      assert.equal(w.document.querySelectorAll(".rove-settings").length,1);
      assert.equal(w.document.querySelector("#roveFooter"),null);
      assert.equal(w.document.querySelector("#quickSidebar .rove-settings"),null);
      Object.defineProperty(w, "localStorage", {get(){throw Error("Unavailable storage");}});
      w.document.querySelector('#themeToggle').click();
      assert.equal(w.document.documentElement.dataset.theme,"light");
      assert.equal(w.document.querySelector('#themeToggle').getAttribute('aria-label'),"Switch to dark mode");
    } finally {w.close();}
  });
}

test("root journey routes configuration and quick runs without losing an in-progress draft", async () => {
  const dom = new JSDOM(read("index.html"), {url:"http://localhost/",runScripts:"outside-only",pretendToBeVisual:true}), w = dom.window, calls=[];
  w.lucide={createIcons(){}};
  w.HTMLElement.prototype.scrollIntoView=()=>{};
  w.fetch=async(url,options={})=>{calls.push({url,method:options.method||"GET"});return {ok:true,json:async()=>url.includes("history")?[]:url.includes("strategies")?{strategies:[]}:url.includes("endpoints")?{endpoints:[]}:url.includes("models")?{models:[]}:{defaults:{},endpoints:{},strategies:{}}};};
  try {
    w.eval(read("navigation.js"));w.eval(read("stage-renderers.js"));w.eval(read("strategy-table.js")); w.eval(read("app.js") + ";window.testRoot={setupTabs,setRunning,restoreEvaluation,switchView};"); await pause();
    const el=id=>w.document.getElementById(id);
    assert.equal(el("quickComposer").hidden,true);
    assert.equal(el("quickSidebar").hidden,true);
    // The original runner stays discoverable alongside repeatable campaigns.
    assert.match(w.document.querySelector('.start-actions [data-root-view="quick"]').textContent, /New trial/);
    assert.equal(w.document.querySelector('.start-actions .start-primary').getAttribute("href"),"/?view=quick");
    assert.equal(w.document.querySelector('.start-actions .start-secondary').getAttribute("href"),"/static/datasets.html");
    w.testRoot.switchView("quick");
    assert.equal(w.location.search,"?view=quick");
    assert.equal(el("quickComposer").hidden,false);
    assert.equal(el("roveFooter"),null);
    assert.equal(w.document.querySelectorAll(".rove-settings").length,1);
    assert.ok(w.document.querySelector("#roveNav .rove-utilities .rove-settings"));
    w.testRoot.switchView("examples");
    assert.equal(el("quickSidebar").hidden,false);
    assert.equal(w.document.querySelector("#quickSidebar .rove-settings"),null);
    w.testRoot.switchView("quick");
    el("taskInput").value="Keep this task while checking endpoints";
    w.document.querySelector('#roveNav [data-nav-section="configure"]').click();
    assert.equal(w.location.search,"?view=strategies");
    assert.equal(el("quickComposer").hidden,true);
    assert.equal(el("configureHeading").hidden,false);
    assert.equal(w.document.querySelector("#roveNav .rove-settings").getAttribute("aria-current"),"page");
    w.document.querySelector('#configureHeading [data-root-view="models"]').click();
    assert.equal(w.location.search,"?view=models");
    assert.equal(w.document.querySelector('#configureHeading [aria-current]').textContent,"Endpoints");
    w.history.back(); await pause();
    assert.equal(w.location.search,"?view=strategies");
    w.history.back(); await pause();
    assert.equal(w.location.search,"?view=quick");
    assert.equal(el("quickComposer").hidden,false);
    assert.equal(el("taskInput").value,"Keep this task while checking endpoints");
    // Real tab initialization replaces the chat DOM: persistent routes must survive it.
    w.testRoot.setupTabs(["mock"]); w.testRoot.setRunning(true); w.testRoot.restoreEvaluation();
    const output=el("tab-content-mock"); assert.ok(output);
    w.testRoot.switchView("strategies");
    assert.equal(el("quickComposer").hidden,true);
    output.append(w.document.createTextNode("Streamed result while inspecting configuration"));
    w.testRoot.restoreEvaluation();
    assert.equal(el("quickWelcome").hidden,true);
    assert.equal(el("tab-content-mock"),output);
    assert.match(output.textContent,/Streamed result/);
    w.testRoot.setRunning(false);el("chatArea").scrollTop=900;w.testRoot.switchView("home");
    assert.equal(el("chatArea").scrollTop,0);
    w.testRoot.setupTabs(["next"]);w.testRoot.restoreEvaluation();
    assert.ok(el("quickWelcome"));assert.ok(el("tab-content-next"));
    assert.equal(calls.some(c=>c.method!=="GET"),false);
  } finally {w.close();}
});


test("each resource browser has one matching New action and no duplicate resource navigation", () => {
  for (const [page,title,label,href] of [["history.html","Trials","New trial","/?view=quick"],["benchmarks.html","Campaigns","New campaign","/static/datasets.html"]]) {
    const dom = new JSDOM(fs.readFileSync(path.join(root, "frontend", page), "utf8"));
    const doc = dom.window.document;
    assert.equal(doc.querySelector("h1").textContent,title);
    assert.equal(doc.querySelector(".page-heading > a").textContent,label);
    assert.equal(doc.querySelector(".page-heading > a").getAttribute("href"),href);
    assert.equal(doc.querySelector(".workspace-tabs"),null);
    dom.window.close();
  }
});

test("direct trial drafts and Settings highlight their own destination before the runner loads", () => {
  for (const [view,section] of [["quick","evaluate"],["examples","evaluate"],["strategies","configure"],["models","configure"]]) {
    const dom=new JSDOM(read("index.html"),{url:`http://localhost/?view=${view}`,runScripts:"outside-only"});
    try {dom.window.eval(read("navigation.js"));assert.equal(dom.window.document.querySelector("#roveNav [aria-current=page]").dataset.navSection,section);}
    finally {dom.window.close();}
  }
});

test("trial sidebar exposes full button-style library destinations with icons and readable labels", () => {
  const dom = new JSDOM(read("index.html"));
  try {
    const library = dom.window.document.querySelector('#quickSidebar nav[aria-label="Trial library"]');
    const actions = [...library.querySelectorAll("a.sidebar-action")];
    assert.deepEqual(actions.map(action => [action.textContent.trim(), action.getAttribute("href")]), [["All saved trials", "/static/history.html?source=quick"], ["Sample cases", "/?view=examples"]]);
    for(const action of actions) assert.equal(action.querySelector("svg").getAttribute("aria-hidden"),"true");
    assert.equal(actions[1].dataset.rootView,"examples");
    assert.equal(library.querySelector("button"),null,"destinations keep native link semantics");
  } finally {dom.window.close();}
});
