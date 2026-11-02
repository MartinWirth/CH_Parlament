"use strict";
async function loadDirectoryHealth(){
  const summary=$("directory-health-summary"),results=$("directory-health-results");
  summary.textContent="Quellenstatus wird geladen…";results.replaceChildren();
  try{
    const data=await request("directory-source-health?language="+encodeURIComponent($("language").value));
    const sources=Array.isArray(data.sources)?data.sources:[];
    summary.textContent="Snapshot: "+(data.updated_at||"nicht vorhanden")+" · Konfiguriert: "+data.configured+
      " · Erfolgreich geparst: "+data.parsed_directories+" · Dauerhaft fehlerhaft: "+(data.persistent_failure_count??0)+" · Im Bericht: "+sources.length;
    if(!sources.length){results.textContent="Noch keine Quelldiagnose gespeichert. Bitte zuerst einen Refresh ausführen.";return;}
    const table=document.createElement("table");table.style.width="100%";
    const header=document.createElement("tr");
    for(const label of ["Quelle","Status","Fehler in Folge","Mitglieder","Hinweis"]){
      const th=document.createElement("th");th.textContent=label;th.scope="col";header.append(th);
    }
    table.append(header);
    for(const item of sources){
      const tr=document.createElement("tr"),cells=[
        item.url||"–",item.persistent_failure?"Dauerhaft fehlerhaft":(item.status||"unbekannt"),
        String(item.failure_streak??0),String(item.matched_members??0),
        item.reason||((item.usable)?"Erfolgreich geparst":"Nicht nutzbar")];
      cells.forEach((value,index)=>{
        const td=document.createElement("td");td.style.padding=".4rem";td.style.borderTop="1px solid #cbd5e1";
        if(index===0&&typeof item.url==="string"&&item.url.startsWith("https://")){
          const a=document.createElement("a");a.href=item.url;a.textContent=item.url;
          a.target="_blank";a.rel="noopener noreferrer";td.append(a);
        }else td.textContent=value;
        tr.append(td);
      });
      table.append(tr);
    }
    results.append(table);
    const queue=Array.isArray(data.review_queue)?data.review_queue:[];
    const heading=document.createElement("h3");heading.textContent="Priorisierte Prüfliste ("+queue.length+")";
    results.append(heading);
    if(!queue.length){const p=document.createElement("p");p.textContent="Keine Quellen zur Prüfung vorgemerkt.";results.append(p);}
    for(const item of queue){
      const section=document.createElement("details");
      const summaryLine=document.createElement("summary");
      summaryLine.textContent=(item.priority==="high"?"HOCH":item.priority==="medium"?"MITTEL":"NIEDRIG")+
        " · "+(item.url||"–")+" · Fehler in Folge: "+(item.failure_streak??0);
      section.append(summaryLine);
      const advice=document.createElement("p");advice.textContent="Empfehlung: "+(item.recommendation||"–");section.append(advice);
      const last=document.createElement("p");last.textContent="Letzter erfolgreicher Scan: "+(item.last_success_at||"nicht bekannt");section.append(last);
      const history=document.createElement("ul");
      for(const event of (Array.isArray(item.failure_history)?item.failure_history:[])){
        const li=document.createElement("li");
        li.textContent=(event.checked_at||"–")+" · "+(event.status||"–")+" · "+(event.reason||"–");
        history.append(li);
      }
      section.append(history);results.append(section);
    }
  }catch(err){summary.textContent="Quellenstatus konnte nicht geladen werden.";results.replaceChildren();$("message").textContent=err.message;}
}
$("directory-health-load").addEventListener("click",loadDirectoryHealth);
$("language").addEventListener("change",()=>{
  $("directory-health-summary").textContent="Für diese Sprache Quellenstatus laden.";
  $("directory-health-results").replaceChildren();
});
