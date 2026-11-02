"use strict";
async function loadDirectoryHealth(){
  const summary=$("directory-health-summary"),results=$("directory-health-results");
  summary.textContent="Quellenstatus wird geladen…";results.replaceChildren();
  try{
    const data=await request("directory-source-health?language="+encodeURIComponent($("language").value));
    const sources=Array.isArray(data.sources)?data.sources:[];
    const health=await request("directory-storage-health");
    const healthLine=document.createElement("p");
    healthLine.textContent="Speicherzustand: "+health.status+
      " · Backup: "+(health.backup_valid?"gültig":"nicht gültig")+
      " · Letzte Wiederherstellung: "+(health.last_recovery?.at||"keine");
    results.append(healthLine);
    const migrationLine=document.createElement("p");
    const migration=health.migration;
    migrationLine.textContent="SQLite-Migration: "+(migration?.state||"nicht verfügbar")+
      (migration?.completed_at?" · Abgeschlossen: "+migration.completed_at:"")+
      (migration?.reviews!==undefined?" · Prüfungen: "+migration.reviews+" · Audit: "+migration.events:"");
    results.append(migrationLine);
    if(health.maintenance){
      const maintenanceLine=document.createElement("p");
      const m=health.maintenance;
      maintenanceLine.textContent="SQLite-Wartung: "+(m.enabled?"aktiv (alle "+m.interval_hours+" h)":"deaktiviert")+
        " · Letzte Prüfung: "+(m.last_check_at||"keine")+
        " · Prüfergebnis: "+(m.last_check_status||"ausstehend")+
        " · Letztes erfolgreiches Backup: "+(m.last_backup_at||"keines")+
        (m.last_error?" · Fehler: "+m.last_error:"");
      results.append(maintenanceLine);
    }
    if(health.maintenance&&health.migration?.state!=="legacy_json"){
      const versions=await request("directory-backup-versions");
      const section=document.createElement("section");
      const title=document.createElement("h3");title.textContent="SQLite-Backup-Versionen";
      const select=document.createElement("select");
      const choices=Array.isArray(versions.versions)?versions.versions:[];
      for(const entry of choices){
        const option=document.createElement("option");
        option.value=entry.id;
        option.textContent=entry.id+(entry.valid?"":" (ungültig)");
        option.disabled=!entry.valid;
        select.append(option);
      }
      const restore=document.createElement("button");
      restore.type="button";restore.textContent="Ausgewählte Version wiederherstellen";
      restore.disabled=!choices.some(entry=>entry.valid);
      const feedback=document.createElement("p");
      restore.addEventListener("click",async()=>{
        if(!select.value||!confirm("Datenbank auf Version "+select.value+" zurücksetzen? Aktuelle Daten werden ersetzt."))return;
        restore.disabled=true;
        try{
          const response=await request("directory-backup-version-restore",{
            method:"POST",headers:{"Content-Type":"application/json","X-Admin-Action":"1"},
            body:JSON.stringify({version:select.value})
          });
          feedback.textContent="Wiederhergestellt: "+response.reviews+" Prüfungen, "+response.events+" Ereignisse.";
        }catch(err){feedback.textContent="Wiederherstellung fehlgeschlagen: "+err.message;}
        finally{restore.disabled=false;}
      });
      section.append(title,select,restore,feedback);results.append(section);
    }
    summary.textContent="Snapshot: "+(data.updated_at||"nicht vorhanden")+" · Konfiguriert: "+data.configured+
      " · Erfolgreich geparst: "+data.parsed_directories+" · Dauerhaft fehlerhaft: "+(data.persistent_failure_count??0)+" · Im Bericht: "+sources.length;
    const filters=document.createElement("section");
    const heading=document.createElement("h3");heading.textContent="Audit-Verlauf filtern und exportieren";filters.append(heading);
    const from=document.createElement("input");from.type="date";from.setAttribute("aria-label","Von Datum");
    const to=document.createElement("input");to.type="date";to.setAttribute("aria-label","Bis Datum");
    const status=document.createElement("select");status.setAttribute("aria-label","Prüfstatus");
    for(const [value,label] of [["","Alle Status"],["in_review","In Prüfung"],["resolved","Behoben"],["deferred","Zurückgestellt"]]){
      const option=document.createElement("option");option.value=value;option.textContent=label;status.append(option);
    }
    const source=document.createElement("select");source.setAttribute("aria-label","Quelle");
    const all=document.createElement("option");all.value="";all.textContent="Alle Quellen";source.append(all);
    for(const url of [...new Set(sources.map(item=>item.url).filter(Boolean))].sort()){
      const option=document.createElement("option");option.value=url;option.textContent=url;source.append(option);
    }
    filters.append(from,to,status,source);
    const output=document.createElement("div");output.setAttribute("aria-live","polite");
    async function loadFilteredAudit(){
      const params=new URLSearchParams({language:$("language").value,from_date:from.value,to_date:to.value,
                                        status:status.value,source:source.value});
      const data=await request("directory-review-audit?"+params);
      output.replaceChildren();
      const p=document.createElement("p");p.textContent="Gefundene Änderungen: "+data.count;output.append(p);
      const list=document.createElement("ul");
      for(const event of data.events){
        const li=document.createElement("li");
        li.textContent=(event.changed_at||"–")+" · "+(event.url||"–")+" · "+
          (event.previous_status||"Neu")+" → "+(event.new_status||"–")+" · "+(event.comment||"");
        list.append(li);
      }
      output.append(list);
      return params;
    }
    const apply=document.createElement("button");apply.type="button";apply.textContent="Filter anwenden";
    apply.addEventListener("click",()=>loadFilteredAudit().catch(err=>{output.textContent=err.message;}));
    filters.append(apply);
    for(const format of ["csv","json"]){
      const button=document.createElement("button");button.type="button";button.textContent=format.toUpperCase()+" exportieren";
      button.addEventListener("click",async()=>{
        button.disabled=true;
        try{
          const params=new URLSearchParams({language:$("language").value,from_date:from.value,to_date:to.value,
                                            status:status.value,source:source.value,format});
          const response=await fetch("/admin/directory-review-audit?"+params,{credentials:"same-origin",cache:"no-store"});
          if(!response.ok)throw Error("Export fehlgeschlagen: HTTP "+response.status);
          const blob=await response.blob(),url=URL.createObjectURL(blob);
          const link=document.createElement("a");link.href=url;link.download="directory-review-audit."+format;
          document.body.append(link);link.click();link.remove();URL.revokeObjectURL(url);
        }catch(err){output.textContent=err.message;}
        finally{button.disabled=false;}
      });
      filters.append(button);
    }
    const backupButton=document.createElement("button");backupButton.type="button";
    backupButton.textContent="Sicherung erstellen";
    const restoreButton=document.createElement("button");restoreButton.type="button";
    restoreButton.textContent="Letzte Sicherung wiederherstellen";
    async function storageAction(endpoint){
      const result=await request(endpoint,{method:"POST",headers:{"X-Admin-Action":"1"}});
      output.textContent="Erfolgreich: "+result.events+" Audit-Ereignisse gesichert/wiederhergestellt.";
    }
    backupButton.addEventListener("click",async()=>{
      backupButton.disabled=true;
      try{await storageAction("directory-review-backup");}
      catch(err){output.textContent=err.message;}
      finally{backupButton.disabled=false;}
    });
    restoreButton.addEventListener("click",async()=>{
      if(!window.confirm("Letzte Sicherung wiederherstellen? Nicht gesicherte Änderungen können verloren gehen."))return;
      restoreButton.disabled=true;
      try{await storageAction("directory-review-restore");await loadDirectoryHealth();}
      catch(err){output.textContent=err.message;}
      finally{restoreButton.disabled=false;}
    });
    filters.append(backupButton,restoreButton);
    filters.append(output);results.append(filters);
    if(!sources.length){results.append(document.createTextNode("Noch keine Quelldiagnose gespeichert. Bitte zuerst einen Refresh ausführen."));return;}
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
    const manual=data.manual_reviews||{};
    const audit=Array.isArray(data.review_audit)?data.review_audit:[];
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
      section.append(history);
      const existing=manual[item.url]||{};
      const state=document.createElement("select");
      for(const [value,label] of [["in_review","In Prüfung"],["resolved","Behoben"],["deferred","Zurückgestellt"]]){
        const option=document.createElement("option");option.value=value;option.textContent=label;state.append(option);
      }
      state.value=existing.status||"in_review";
      const comment=document.createElement("textarea");comment.maxLength=1000;
      comment.placeholder="Kommentar (max. 1000 Zeichen)";comment.value=existing.comment||"";
      const saved=document.createElement("p");
      saved.textContent=existing.reviewed_at?"Letzte Bearbeitung: "+existing.reviewed_at:"Noch nicht bearbeitet";
      const save=document.createElement("button");save.type="button";save.textContent="Prüfstatus speichern";
      save.addEventListener("click",async()=>{
        save.disabled=true;
        try{
          const result=await request("directory-source-review",{
            method:"POST",headers:{"Content-Type":"application/json","X-Admin-Action":"1"},
            body:JSON.stringify({language:$("language").value,url:item.url,status:state.value,comment:comment.value})
          });
          saved.textContent="Gespeichert: "+result.reviewed_at;
        }catch(err){saved.textContent="Speichern fehlgeschlagen: "+err.message;}
        finally{save.disabled=false;}
      });
      const events=audit.filter(event=>event.url===item.url);
      const auditDetails=document.createElement("details");
      const auditTitle=document.createElement("summary");
      auditTitle.textContent="Änderungsverlauf ("+events.length+")";
      auditDetails.append(auditTitle);
      const auditList=document.createElement("ul");
      for(const event of events.slice().reverse()){
        const li=document.createElement("li");
        li.textContent=(event.changed_at||"–")+" · "+(event.previous_status||"Neu")+
          " → "+(event.new_status||"–")+" · Kommentar: "+(event.comment||"–");
        auditList.append(li);
      }
      auditDetails.append(auditList);
      section.append(state,comment,save,saved,auditDetails);results.append(section);
    }
  }catch(err){summary.textContent="Quellenstatus konnte nicht geladen werden.";results.replaceChildren();$("message").textContent=err.message;}
}
$("directory-health-load").addEventListener("click",loadDirectoryHealth);
$("language").addEventListener("change",()=>{
  $("directory-health-summary").textContent="Für diese Sprache Quellenstatus laden.";
  $("directory-health-results").replaceChildren();
});
