"use strict";
const $ = id => document.getElementById(id);
let taskData = [];
let loadedSpec = null;
async function request(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
function message(error) { $("message").textContent = error.message || String(error); }
function choice(container, value, text, checked) {
  const label = document.createElement("label"), input = document.createElement("input");
  input.type = "checkbox"; input.value = value; input.checked = checked;
  input.addEventListener("change", budget);
  label.append(input, document.createTextNode(text)); $(container).append(label);
}
function selected(id) { return [...$(id).querySelectorAll("input:checked")].map(i => i.value); }
function budget() {
  const n = Number($("repeats").value), tasks = selected("tasks").length, configs = selected("strategies").length;
  $("budget").textContent = `${tasks} tasks × ${configs} configurations × ${n} repeats = ${tasks * configs * n} attempts. Configured cloud endpoints may incur charges.`;
}
async function fileBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = reject; reader.readAsDataURL(blob);
  });
}
async function configuration() {
  const ids = selected("tasks"), strategies = selected("strategies");
  if (!ids.length || !strategies.length) throw new Error("Select at least one task and configuration.");
  const n = Number($("repeats").value);
  if (!Number.isInteger(n) || n < 1 || n > 1000) throw new Error("Repeats must be an integer from 1 to 1000.");
  const tasks = [];
  for (const id of ids) {
    const task = taskData.find(t => t.id === id);
    if (task.image_base64) { tasks.push(task); continue; }
    const response = await fetch(`/data/${task.filename.split("/").map(encodeURIComponent).join("/")}`);
    if (!response.ok) throw new Error(`Could not load scene for ${task.task}`);
    const {filename, ...rest} = task;
    tasks.push({...rest, image_base64: await fileBase64(await response.blob())});
  }
  return {name: $("name").value, suite_version: $("suite").value, revision: $("revision").value,
    seeds: loadedSpec && loadedSpec.seeds.length === n ? loadedSpec.seeds : Array.from({length:n}, (_,i) => i),
    ks: loadedSpec ? loadedSpec.ks : [...new Set([1,3,5,n])].sort((a,b)=>a-b),
    timeout_s: Number($("timeout").value), strategies, tasks};
}
async function history() {
  const campaigns = await request("/api/campaigns");
  $("history").replaceChildren();
  if (!campaigns.length) $("history").textContent = "Your first campaign will appear here.";
  for (const c of campaigns) {
    const card = document.createElement("div"); card.className = "campaign";
    const title = document.createElement("h3"); title.textContent = c.name;
    const detail = document.createElement("small"); detail.textContent = `${c.revision} · ${c.status} · ${new Date(c.created_at).toLocaleString()}`;
    card.append(title, detail);
    const {summary} = await request(`/api/campaigns/${c.id}`);
    const progress = document.createElement("p"); progress.className = "progress";
    progress.textContent = `${summary.completed_trials} / ${summary.planned_trials} attempts recorded`;
    card.append(progress);
    for (const [format,label] of [["html","Open report"],["json","JSON"],["csv","CSV"]]) {
      const link = document.createElement("a"); link.href = `/api/campaigns/${c.id}/report?format=${format}`;
      link.textContent = label; if (format === "html") { link.target = "_blank"; link.rel = "noopener"; }
      card.append(link);
    }
    if (c.status !== "completed") {
      const button = document.createElement("button"); button.className = "secondary";
      const action = ["running","pending"].includes(c.status) ? "cancel" : "resume";
      button.textContent = action === "cancel" ? "Cancel" : "Resume remaining trials";
      button.onclick = async () => { try { await request(`/api/campaigns/${c.id}/${action}`, {method:"POST"}); await history(); } catch (e) {message(e);} };
      card.append(document.createElement("br"), button);
    }
    $("history").append(card);
  }
}
$("campaignForm").addEventListener("submit", async event => {
  event.preventDefault(); $("run").disabled = true;
  try {
    const spec = await configuration();
    const result = await request("/api/campaigns", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(spec)});
    message(`Campaign started: ${result.planned_trials} planned attempts.`); await history();
  } catch (error) { message(error); } finally { $("run").disabled = false; }
});
$("save").onclick = async () => {
  try {
    const blob = new Blob([JSON.stringify(await configuration(), null, 2)], {type:"application/json"});
    const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = "rove-campaign.json"; link.click(); URL.revokeObjectURL(link.href);
  } catch (error) { message(error); }
};
$("load").accept = ".json";
$("load").onchange = async () => {
  try {
    const file = $("load").files[0]; if (!file) return;
    const spec = JSON.parse(await file.text());
    if (!Array.isArray(spec.tasks) || !Array.isArray(spec.strategies) || !Array.isArray(spec.seeds)) throw new Error("Invalid campaign configuration");
    const available = [...$("strategies").querySelectorAll("input")].map(i=>i.value);
    if (spec.strategies.some(s=>!available.includes(s))) throw new Error("Saved configuration references an unavailable strategy");
    taskData = spec.tasks; $("tasks").replaceChildren();
    for (const t of taskData) choice("tasks", t.id, t.task, true);
    for (const input of $("strategies").querySelectorAll("input")) input.checked = spec.strategies.includes(input.value);
    $("name").value = spec.name; $("suite").value = spec.suite_version; $("revision").value = spec.revision;
    $("repeats").value = spec.seeds.length; $("timeout").value = spec.timeout_s;
    loadedSpec = spec; budget(); message("Saved configuration loaded, including its seed list and k values.");
  } catch (error) { message(error); }
};
$("addTask").onclick = async () => {
  try {
    const file = $("customImage").files[0], task = $("customInstruction").value.trim();
    if (!file || !task) throw new Error("Provide a task instruction and scene image.");
    if (file.size > 3000000) throw new Error("Choose an image smaller than 3 MB.");
    const item = {id: `custom-${crypto.randomUUID()}`, task, image_base64:await fileBase64(file)};
    taskData.push(item); choice("tasks",item.id,item.task,true); budget();
  } catch (error) { message(error); }
};
for (const id of ["name","suite","revision","repeats","timeout"]) $(id).addEventListener("input",budget);
async function initialize() {
  const [strategies, gallery] = await Promise.all([request("/api/strategies"), request("/api/examples")]);
  for (const s of strategies.strategies) choice("strategies",s.id,s.display_name || s.id,s.id === "mock");
  taskData = gallery.examples.map((e,i)=>({id:`task-${i+1}`,task:e.task,filename:e.filename,
    example:{ground_truth:e.eval_qa || null,extras:Object.fromEntries(Object.entries(e).filter(([k])=>!["filename","source","eval_qa","task"].includes(k)))}}));
  taskData.forEach((t,i)=>choice("tasks",t.id,t.task,i===0));
  budget(); await history();
  async function poll() { try { await history(); } catch(e) {message(e);} setTimeout(poll,4000); }
  setTimeout(poll,4000);
}
initialize().catch(message);
