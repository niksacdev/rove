/* Graphs use recorded assessment aggregates. AI commentary never supplies scores. */
(function () {
  "use strict";
  const palette = ["#438f86", "#b18443", "#6689b2", "#aa7378", "#879650", "#897ca7"];
  const label = (campaign, id) => campaign.strategy_definitions?.[id]?.display_name || campaign.config?.strategies?.[id]?.display_name || id;
  function outcomeRows(summary, campaign) {
    return (summary.strategies || []).map(strategy => {
      const tasks = (summary.tasks || []).filter(task => task.strategy_id === strategy.strategy_id);
      return {id: strategy.strategy_id, name: label(campaign, strategy.strategy_id), passed: tasks.reduce((s,t) => s+(t.passed || 0),0), failed: tasks.reduce((s,t) => s+(t.failed || 0),0), unknown: tasks.reduce((s,t) => s+(t.unknown || 0),0), ...strategy};
    });
  }
  function render(container, summary, campaign) {
    const doc = container.ownerDocument;
    const node = (tag,text,cls) => { const el=doc.createElement(tag); if(text!=null) el.textContent=String(text); if(cls)el.className=cls; return el; };
    const svg = (tag,attrs) => { const el=doc.createElementNS("http://www.w3.org/2000/svg",tag); for(const [key,value] of Object.entries(attrs)) el.setAttribute(key,String(value)); return el; };
    const rows=outcomeRows(summary,campaign); container.replaceChildren();
    const totals=rows.reduce((s,r)=>({passed:s.passed+r.passed,failed:s.failed+r.failed,unknown:s.unknown+r.unknown}),{passed:0,failed:0,unknown:0});
    const stats=node("div",null,"outcome-statistics");
    for(const [title,value,kind] of [["Accepted",totals.passed,"pass"],["Rejected",totals.failed,"fail"],["Unassessed",totals.unknown,"unknown"],["Trials recorded",`${summary.completed_trials || 0} / ${summary.planned_trials || 0}`,"recorded"]]) {
      const card=node("div",null,`outcome-stat ${kind}`); card.append(node("span",title),node("strong",value)); stats.append(card);
    }
    container.append(stats);
    const grid=node("div",null,"outcome-charts");
    const outcomes=node("section",null,"outcome-chart"); outcomes.append(node("h3","Outcomes by strategy"));
    const legend=node("div",null,"outcome-legend"); for(const [name,kind] of [["Accepted","pass"],["Rejected","fail"],["Unassessed","unknown"]]) legend.append(node("span",name,kind)); outcomes.append(legend);
    for(const row of rows) {
      const n=row.passed+row.failed+row.unknown, item=node("div",null,"outcome-bar-row");
      const bar=node("div",null,"outcome-bar"); bar.setAttribute("role","img"); bar.setAttribute("aria-label",`${row.name}: ${row.passed} accepted, ${row.failed} rejected, ${row.unknown} unassessed`);
      for(const kind of ["passed","failed","unknown"]) { const part=node("span",null,{passed:"pass",failed:"fail",unknown:"unknown"}[kind]);part.style.width=`${n ? row[kind]/n*100 : 0}%`;bar.append(part); }
      item.append(node("strong",row.name),bar,node("small",`${row.passed} accepted · ${row.failed} rejected · ${row.unknown} unassessed`)); outcomes.append(item);
    }
    if(!rows.length) outcomes.append(node("p","No strategy outcomes recorded yet.","muted"));
    grid.append(outcomes);
    const reliability=node("section",null,"outcome-chart"); reliability.append(node("h3","Reliability across repeated trials"));
    const controls=node("div",null,"chart-switch"), select=node("select");select.setAttribute("aria-label","Reliability measure");
    for(const [value,text]of [["pass_at_k","At least one success · pass@k"],["pass_pow_k","All attempts succeed · pass^k"]]) { const option=node("option",text);option.value=value;select.append(option); }controls.append(select);reliability.append(controls);
    const plot=node("div"), values=node("details");values.append(node("summary","Exact values and uncertainty"));reliability.append(plot,values);
    function draw() {
      plot.replaceChildren();while(values.children.length>1)values.lastChild.remove();
      const measure=select.value, ks=[...new Set(rows.flatMap(row=>(row[measure]||[]).map(p=>p.k)))].sort((a,b)=>a-b), maxK=Math.max(1,...ks);
      const chart=svg("svg",{viewBox:"0 0 440 220",role:"img","aria-label":`${select.selectedOptions[0].textContent}. Unresolved bounds are shown separately from measured values.`});
      const x=k=>48+(maxK===1?160:(k-1)/(maxK-1)*360), y=p=>174-p*140;
      for(const value of [0,.5,1]) { chart.append(svg("line",{x1:48,x2:408,y1:y(value),y2:y(value),class:"chart-gridline"})); const text=svg("text",{x:38,y:y(value)+4,"text-anchor":"end"});text.textContent=`${value*100}%`;chart.append(text); }
      for(const k of ks) { const text=svg("text",{x:x(k),y:197,"text-anchor":"middle"});text.textContent=String(k);chart.append(text); }
      const axis=svg("text",{x:226,y:217,"text-anchor":"middle"});axis.textContent="Attempts (k)";chart.append(axis);
      const names=node("div",null,"strategy-legend");let hasValues=false,hasBounds=false;
      rows.forEach((row,index)=>{
        const color=palette[index%palette.length], points=row[measure]||[];
        const name=node("span",row.name);name.style.setProperty("--series",color);names.append(name);
        const table=node("table"), caption=node("caption",row.name);table.append(caption);
        const head=node("tr");for(const text of ["k","Estimate","Unresolved bounds"])head.append(node("th",text));table.append(head);
        let previous=null;
        for(const p of points) {
          const valid=Number.isFinite(p.value), bounded=Number.isFinite(p.lower)&&Number.isFinite(p.upper);
          if(valid) {
            hasValues=true;
            if(previous)chart.append(svg("line",{x1:x(previous.k),y1:y(previous.value),x2:x(p.k),y2:y(p.value),stroke:color,"stroke-width":2}));
            chart.append(svg("circle",{cx:x(p.k),cy:y(p.value),r:4,fill:color}));previous=p;
          } else { previous=null; if(bounded){hasBounds=true;chart.append(svg("line",{x1:x(p.k),x2:x(p.k),y1:y(p.upper),y2:y(p.lower),stroke:color,"stroke-width":5,"stroke-dasharray":"3 4",opacity:.5}));} }
          const tr=node("tr");tr.append(node("td",p.k),node("td",valid?`${(p.value*100).toFixed(1)}%`:"Not yet known"),node("td",!valid&&bounded?`${(p.lower*100).toFixed(1)}–${(p.upper*100).toFixed(1)}%`:"—"));table.append(tr);
        }
        values.append(table);
      });
      if(ks.length)plot.append(chart,names);else plot.append(node("p","Reliability becomes available when repeated trial measurements are recorded.","muted"));
      if(!hasValues&&hasBounds)plot.append(node("p","Awaiting assessments. Dashed bounds show what is still unknown; they are not confidence intervals.","chart-note"));
      else if(hasBounds)plot.append(node("p","Dots show known estimates. Dashed bounds show unresolved outcomes.","chart-note"));
    }
    select.addEventListener("change",draw);draw();grid.append(reliability);
    const latency=node("section",null,"outcome-chart latency-chart");latency.append(node("h3","Pipeline latency"),node("p","95th percentile of recorded timings · lower is faster","chart-note"));
    const max=Math.max(1,...rows.map(r=>Number.isFinite(r.latency_p95_ms)?r.latency_p95_ms:0));
    for(const row of rows) { const item=node("div",null,"latency-row"), bar=node("span",null,"latency-bar");bar.style.width=`${Number.isFinite(row.latency_p95_ms)?row.latency_p95_ms/max*100:0}%`;item.append(node("strong",row.name),node("span",Number.isFinite(row.latency_p95_ms)?`${Math.round(row.latency_p95_ms)} ms`:"Not recorded"));const track=node("div",null,"latency-track");track.append(bar);item.append(track);latency.append(item); }
    grid.append(latency);
    if (summary.campaign_targets?.length) {
      const targets=node("section",null,"outcome-chart target-chart"); targets.append(node("h3","Success targets"));
      for (const target of summary.campaign_targets) {
        const row=node("div",null,"target-outcome"), status={met:"Met",not_met:"Not met",unknown:"Unassessed"}[target.status] || "Unassessed";
        row.append(node("strong",label(campaign,target.strategy_id)),node("span",`${target.metric.replaceAll("_"," ")} · ${status}`));
        row.dataset.state=target.status; targets.append(row);
      }
      grid.append(targets);
    }
    container.append(grid);
    const note=node("p","These outcomes use the campaign’s scoring rules. Pipeline timing is not robot task completion time.","chart-note");container.append(note);
  }
  if(typeof module!=="undefined"&&module.exports)module.exports={outcomeRows,render};
  if(typeof window!=="undefined")window.RoveCampaignResults={outcomeRows,render};
})();
