(function(root){
  "use strict";
  function render(container, options) {
    const document=container.ownerDocument;
    const node=(tag,text,className)=>{const el=document.createElement(tag);if(text!=null)el.textContent=text;if(className)el.className=className;return el;};
    const selected=new Set(options.selected || []), limit=options.limit ?? 20;
    container.replaceChildren();container.classList.add("rove-strategy-table-host");
    if(!options.strategies?.length){container.append(node("p","No strategies available. Configure a pipeline in Settings.","muted"));return;}
    const table=node("table",null,"rove-strategy-table");table.setAttribute("aria-label","Strategies to compare and their pipeline stages");
    const head=node("thead"),headRow=node("tr"),body=node("tbody");
    for(const title of ["Select","Strategy","Perceive","Plan","Act","Verify"]){const cell=node("th",title);cell.scope="col";headRow.append(cell);}head.append(headRow);table.append(head,body);container.append(table);
    options.strategies.forEach((strategy,index)=>{
      const row=node("tr");row.dataset.strategyId=strategy.id;row.classList.toggle("selected",selected.has(strategy.id));
      const control=node("td"),checkbox=node("input");checkbox.type="checkbox";checkbox.value=strategy.id;checkbox.id=`${container.id}-strategy-${index}`;checkbox.dataset.strategyId=strategy.id;checkbox.checked=selected.has(strategy.id);checkbox.disabled=Boolean(options.disabled)||(!checkbox.checked&&selected.size>=limit);checkbox.setAttribute("aria-label",`Compare ${strategy.display_name || strategy.id}`);
      checkbox.addEventListener("change",()=>{options.onChange?.(strategy.id,checkbox.checked);container.querySelectorAll("input").forEach(input=>{if(input.value===strategy.id)input.focus();});});control.append(checkbox);
      const identity=node("td",null,"strategy-identity"),label=node("label",strategy.display_name || strategy.id);label.htmlFor=checkbox.id;identity.append(label);
      if(strategy.description){const description=node("p",strategy.description);description.title=strategy.description;identity.append(description);}
      const metadata=[];if(strategy.pipeline_mode)metadata.push(`${strategy.pipeline_mode==="parallel"?"Parallel":"Sequential"} pipeline`);if(strategy.sim)metadata.push(`Simulation: ${strategy.sim}`);if(metadata.length)identity.append(node("small",metadata.join(" · ")));
      row.append(control,identity);
      for(const stage of ["perceive","plan","act","verify"]){
        const cell=node("td",null,"strategy-stage"),configured=strategy[stage],endpoint=typeof configured==="string"?configured:configured?.endpoint;cell.dataset.label=stage[0].toUpperCase()+stage.slice(1);cell.append(node("span",endpoint || "Skipped"));
        if(stage==="verify"){
          const checks=Array.isArray(strategy.verification_checks)?strategy.verification_checks:(configured?.checks || []);
          for(const check of checks)if(typeof check.endpoint==="string"){const detail=node("span",check.endpoint,"strategy-check");detail.append(node("small",check.role==="constraint"?(check.required?"Required constraint":"Optional constraint"):"Diagnostic check"));cell.append(detail);}
          if(strategy.compute_dynamics)cell.append(node("small","Dynamics check enabled"));
        }
        row.append(cell);
      }
      row.addEventListener("click",event=>{if(!event.target.closest("input,label,a,button")&&!checkbox.disabled)checkbox.click();});
      body.append(row);
    });
  }
  root.RoveStrategyTable={render};
})(window);
