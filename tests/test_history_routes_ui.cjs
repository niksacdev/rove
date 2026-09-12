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
