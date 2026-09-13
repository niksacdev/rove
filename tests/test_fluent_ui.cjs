"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs");
const {JSDOM}=require("jsdom");
const css=fs.readFileSync("frontend/fluent.css","utf8");
function tokens(block){return Object.fromEntries([...block.matchAll(/(--f-[\w-]+):\s*(#[a-f0-9]{6})\s*;/g)].map(m=>[m[1],m[2]]));}
const dark=tokens(css.match(/:root\s*\{([^}]+)\}/)[1]);
const light={...dark,...tokens(css.match(/\[data-theme="light"\]\s*\{([^}]+)\}/)[1])};
function luminance(hex){return hex.slice(1).match(/../g).map(v=>parseInt(v,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);}
function contrast(a,b){const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}
for(const [theme,t] of Object.entries({dark,light}))test(`${theme} workspace text and controls meet Fluent contrast guidance`,()=>{
  for(const fg of ["--f-text","--f-text-secondary","--f-text-muted","--f-accent-text"]){
    for(const bg of ["--f-bg","--f-surface","--f-elevated"]){assert.ok(contrast(t[fg],t[bg])>=4.5,`${fg} on ${bg}: ${contrast(t[fg],t[bg])}`);}
  }
  for(const bg of ["--f-accent","--f-accent-hover"])assert.ok(contrast("#ffffff",t[bg])>=4.5,`primary action ${bg}`);
  assert.ok(contrast(t["--f-border-strong"],t["--f-surface"])>=3,"field boundary");
});
test("every workspace loads the shared Foundry component layer after page styles",()=>{
  for(const page of ["index","datasets","benchmarks","history"]){
    const dom=new JSDOM(fs.readFileSync(`frontend/${page}.html`,"utf8"));
    const links=[...dom.window.document.querySelectorAll('link[rel="stylesheet"]')];
    assert.match(links.at(-1).getAttribute("href"),/^\/static\/fluent.css\?/);
    assert.equal(links.filter(l=>l.getAttribute("href").includes("/fluent.css")).length,1);
    assert.equal(links.some(l=>l.href.includes("fonts.googleapis.com")),false);
    dom.window.close();
  }
});
