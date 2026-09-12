"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const {JSDOM} = require("jsdom");
const root = path.resolve(__dirname, ".."), read = name => fs.readFileSync(path.join(root, "frontend", name), "utf8");
const pause = () => new Promise(resolve => setTimeout(resolve, 25));

for (const [file, section] of [["index.html","start"],["datasets.html","evaluate"],["benchmarks.html","results"],["history.html","results"]]) {
  test(`${file} shares exactly the same primary links and accessible theme control`, () => {
    const dom = new JSDOM(read(file), {url:"http://localhost/",runScripts:"outside-only"}), w = dom.window;
    try {
      w.document.documentElement.dataset.theme = "dark";
      w.eval(read("navigation.js"));
      const links = [...w.document.querySelectorAll('#roveNav nav a')];
      assert.deepEqual(links.map(a=>[a.textContent,a.getAttribute('href')]), [["Evaluate","/static/datasets.html"],["Results","/static/benchmarks.html"]]);
      const active = w.document.querySelectorAll("#roveNav [aria-current=page]");
      assert.equal(active.length,1);
      assert.equal(active[0].dataset.navSection,section);
      const settings = w.document.querySelector(".rove-utilities .rove-settings");
      assert.equal(settings.getAttribute("href"), "/?view=strategies");
      assert.equal(settings.textContent,"Settings");
      assert.equal(settings.closest("nav"),null);
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
    w.eval(read("navigation.js"));w.eval(read("app.js") + ";window.testRoot={setupTabs,setRunning,restoreEvaluation,switchView};"); await pause();
    const el=id=>w.document.getElementById(id);
    assert.equal(el("quickComposer").hidden,true);
    assert.equal(el("quickSidebar").hidden,true);
    // The legacy runner remains deep-linkable, without competing with campaigns on the landing page.
    assert.equal(w.document.querySelector('.start-actions [data-root-view="quick"]'),null);
    assert.equal(w.document.querySelector('.start-actions .start-secondary').getAttribute("href"),"/static/datasets.html?step=cases&pick=existing");
    w.testRoot.switchView("quick");
    assert.equal(w.location.search,"?view=quick");
    assert.equal(el("quickComposer").hidden,false);
    el("taskInput").value="Keep this task while checking endpoints";
    w.document.querySelector('#roveNav [data-nav-section="configure"]').click();
    assert.equal(w.location.search,"?view=strategies");
    assert.equal(el("quickComposer").hidden,true);
    assert.equal(el("configureHeading").hidden,false);
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
