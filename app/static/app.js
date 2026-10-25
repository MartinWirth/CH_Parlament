const councilNames={de:["Nationalrat","Ständerat","Bundesrat","Vereinigte Bundesversammlung"],fr:["Conseil national","Conseil des États","Conseil fédéral","Assemblée fédérale (Chambres réunies)"],it:["Consiglio nazionale","Consiglio degli Stati","Consiglio federale","Assemblea federale plenaria"],rm:["Cussegl naziunal","Cussegl dals chantuns","Cussegl federal","Assamblea federala reunida"]};
const councilCodes={"1":0,"2":1,"3":3,"99":2,"NR":0,"SR":1,"BR":2,"VB":3};
const language=document.querySelector("#ui-language");
function councilIndex(m){const code=String(m.rat_code??"").toUpperCase();if(code in councilCodes)return councilCodes[code];const name=String(m.rat||"").toLowerCase();for(const values of Object.values(councilNames)){const idx=values.findIndex(v=>v.toLowerCase()===name);if(idx>=0)return idx}return -1}
function councilLabel(m){const i=councilIndex(m);return i<0?m.rat:councilNames[language.value][i]}
const fields={rat:"Rat",partei:"Partei",kanton:"Kanton",fraktion:"Fraktion",geschlecht:"Geschlecht"};
const cols={name:"Name",...fields,socialmedia:"Socialmedia"};let all=[],selected={},sortKey="name",descending=false,loadVersion=0;
const filters=document.querySelector("#filters"),tbody=document.querySelector("#results"),status=document.querySelector("#status");
const search=document.querySelector("#search");
function buildFilters(){filters.replaceChildren();for(const [key,label] of Object.entries(fields)){const div=document.createElement("div");div.className="filter";const title=document.createElement("label");title.textContent=label;const select=document.createElement("select");select.multiple=true;select.setAttribute("aria-label",label+" mehrfach auswählen");select.title="Strg/Ctrl oder Cmd für Mehrfachauswahl";const values=key==="rat"?councilNames[language.value]:[...new Set(all.map(m=>m[key]).filter(Boolean))].sort((a,b)=>a.localeCompare(b,"de"));for(const value of values){const option=document.createElement("option");option.value=key==="rat"?String(values.indexOf(value)):value;option.textContent=value;select.append(option)}select.addEventListener("change",()=>{selected[key]=[...select.selectedOptions].map(o=>o.value);render()});const hint=document.createElement("small");hint.textContent="Strg/Cmd für Mehrfachauswahl";div.append(title,select,hint);filters.append(div)}}
function render(){const query=search.value.trim().toLocaleLowerCase("de");const rows=all.filter(m=>m.name.toLocaleLowerCase("de").includes(query)&&Object.keys(fields).every(k=>!selected[k]?.length||(k==="rat"?(selected[k].includes(String(councilIndex(m)))||(selected[k].includes("3")&&[0,1].includes(councilIndex(m)))):selected[k].includes(m[k]))));rows.sort((a,b)=>(descending?-1:1)*String(sortKey==="rat"?councilLabel(a):a[sortKey]||"").localeCompare(String(sortKey==="rat"?councilLabel(b):b[sortKey]||""),language.value));tbody.replaceChildren();for(const m of rows){const tr=document.createElement("tr");for(const key of Object.keys(cols)){const td=document.createElement("td");if(key==="socialmedia"){const links=m.socialmedia||{};for(const [platform,url] of Object.entries(links)){if(!url.startsWith("https://"))continue;const a=document.createElement("a");a.href=url;a.target="_blank";a.rel="noopener noreferrer";a.textContent=platform;if(td.childNodes.length)td.append(document.createTextNode(" · "));td.append(a)}if(!td.childNodes.length)td.textContent="–"}else{td.textContent=(key==="rat"?councilLabel(m):m[key])||"–"}tr.append(td)}tbody.append(tr)}document.querySelector("#count").textContent="("+rows.length+")";status.textContent=rows.length?"": "Keine passenden Parlamentarier gefunden."}
const headers=document.querySelector("#headers");for(const [key,label] of Object.entries(cols)){const th=document.createElement("th");th.textContent=label;th.tabIndex=0;th.addEventListener("click",()=>{descending=sortKey===key?!descending:false;sortKey=key;render()});th.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();th.click()}});headers.append(th)}
language.addEventListener("change",()=>{debug("Anzeigesprache geändert",{language:language.value});loadMembers()});
search.addEventListener("input",render);document.querySelector("#reset").addEventListener("click",()=>{selected={};search.value="";filters.querySelectorAll("select").forEach(s=>s.selectedIndex=-1);render()});
const logBox=document.querySelector("#debug-log");
function debug(message,data){const line=new Date().toISOString()+" "+message+(data===undefined?"":" "+JSON.stringify(data));console.info("[CH_Parlament]",line);logBox.textContent+=line+"\n";logBox.scrollTop=logBox.scrollHeight}
document.querySelector("#debug-toggle").addEventListener("click",async e=>{logBox.hidden=!logBox.hidden;e.target.textContent=logBox.hidden?"Debug-Log anzeigen":"Debug-Log ausblenden";if(!logBox.hidden){try{await navigator.clipboard.writeText(logBox.textContent);debug("Debug-Log in Zwischenablage kopiert")}catch(err){debug("Zwischenablage konnte nicht beschrieben werden",{message:err.message})}}});
document.querySelector("#debug-clear").addEventListener("click",()=>{logBox.textContent=""});
debug("Frontend initialisiert");
async function loadMembers(){
  const requested=language.value;
  const version=++loadVersion;
  status.textContent="Lade Parlamentsdaten…";
  debug("GET /api/members gestartet",{language:requested});
  const started=performance.now();
  try{
    const r=await fetch("/api/members?language="+encodeURIComponent(requested.toUpperCase()));
    debug("HTTP Antwort",{status:r.status,ok:r.ok,duration_ms:Math.round(performance.now()-started)});
    if(!r.ok){const error=await r.json().catch(()=>({detail:r.statusText}));throw Error(error.detail||r.statusText)}
    const data=await r.json();
    if(language.value!==requested||version!==loadVersion)return;
    all=data.members||[];
    selected={};
    debug("API Daten empfangen",{members:all.length,backend:data.debug,data_language:data.data_language,first:all[0]||null});
    document.querySelector("#source").textContent="Quelle: "+data.source+" · Datensprache: "+data.data_language+" · Datenabruf: "+new Date(data.retrieved_at).toLocaleString("de-CH");
    buildFilters();render();
    debug("Tabelle gerendert",{rows:tbody.rows.length});
    loadSocialProfiles(requested,version);
  }catch(err){if(language.value!==requested||version!==loadVersion)return;debug("FEHLER",{message:err.message});status.textContent="Daten konnten nicht geladen werden: "+err.message}
}
async function loadSocialProfiles(requested,version){
  debug("Social-Media-Abfrage gestartet",{language:requested});
  const started=performance.now();
  try{
    const response=await fetch("/api/social-profiles?language="+encodeURIComponent(requested.toUpperCase()));
    if(!response.ok)throw Error("HTTP "+response.status);
    const data=await response.json();
    if(version!==loadVersion||language.value!==requested)return;
    let updated=0;
    for(const member of all){
      const links=data.profiles?.[member.id];
      if(links&&Object.keys(links).length){member.socialmedia={...member.socialmedia,...links};updated++}
    }
    render();
    debug("Social-Media-Abfrage abgeschlossen",{duration_ms:Math.round(performance.now()-started),updated_members:updated,updated_at:data.updated_at,backend:data.debug});
  }catch(err){if(version===loadVersion)debug("Social-Media-Abfrage fehlgeschlagen",{message:err.message})}
}
loadMembers();
