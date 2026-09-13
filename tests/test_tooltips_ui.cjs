"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs");
const {JSDOM} = require("jsdom");
function setup() {
  const dom = new JSDOM('<div style="overflow:auto"><div class="strategy-chip" tabindex="0"><span>π0.5</span><div class="chip-popover" aria-hidden="true">Full strategy detail</div></div></div>', {runScripts:"outside-only", pretendToBeVisual:true});
  const w = dom.window, chip = w.document.querySelector(".strategy-chip");
  w.HTMLElement.prototype.getBoundingClientRect = function() { return this === chip ? {left:0,top:1,bottom:41,width:80,height:40} : {width:320,height:160}; };
  w.eval(fs.readFileSync("frontend/tooltips.js","utf8"));
  return {dom,w,chip};
}
test("strategy details escape clipping, fit viewport, open on focus and dismiss with Escape", () => {
  const {dom,w,chip} = setup();
  try {
    chip.focus();
    const tip = w.document.querySelector('[role="tooltip"]');
    assert.equal(tip.parentElement,w.document.body);
    assert.equal(tip.textContent,"Full strategy detail");
    assert.equal(tip.style.left,"12px");
    assert.equal(tip.style.top,"49px"); // flips below near the top edge
    assert.equal(chip.getAttribute("aria-describedby"),tip.id);
    assert.equal(tip.hasAttribute("aria-hidden"),false);
    w.document.dispatchEvent(new w.KeyboardEvent("keydown", {key:"Escape"}));
    assert.equal(w.document.querySelector('[role="tooltip"]'),null);
    assert.equal(chip.hasAttribute("aria-describedby"),false);
  } finally {dom.window.close();}
});
test("hover tooltip remains readable when pointer crosses onto it and closes on scroll", async () => {
  const {dom,w,chip} = setup();
  try {
    chip.dispatchEvent(new w.Event("pointerover",{bubbles:true}));
    const tip = w.document.querySelector('[role="tooltip"]');
    chip.dispatchEvent(new w.MouseEvent("pointerout",{bubbles:true,relatedTarget:tip}));
    tip.dispatchEvent(new w.Event("pointerenter"));
    await new Promise(resolve=>setTimeout(resolve,150));
    assert.equal(w.document.querySelector('[role="tooltip"]'),tip);
    chip.parentElement.dispatchEvent(new w.Event("scroll"));
    assert.equal(w.document.querySelector('[role="tooltip"]'),null);
  } finally {dom.window.close();}
});
