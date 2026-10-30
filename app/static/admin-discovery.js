"use strict";
async function loadCoverage(){
  const status=$("coverage-status"),cards=$("coverage-cards"),
    sources=$("coverage-sources"),candidates=$("coverage-candidates");
  status.textContent="Gespeicherte Recherche wird geladen…";
  cards.replaceChildren();sources.replaceChildren();candidates.replaceChildren();
  try{
    const params=new URLSearchParams({language:$("language").value,limit:"100"});
    const data=await request("discovery-report?"+params.toString());
    status.textContent="Snapshot: "+(data.updated_at||"noch nicht vorhanden")+
      (data.truncated?" · Erste 100 Kandidaten angezeigt":"")+
      " · Alle Profile erfordern manuelle Identitätsprüfung.";
    const values=[
      ["Mitglieder mit bestätigten Profilen",data.confirmed_member_count],
      ["Mitglieder mit Prüfkandidaten",data.members_with_review_candidates],
      ["Davon ohne bestätigte Profile",data.members_without_confirmed_profiles_with_candidates],
      ["Unterschiedliche Kandidaten",data.total_distinct_candidates],
      ["Identitätskonflikte",data.identity_conflict_candidates],
    ];
    for(const [label,value] of values){
      const card=document.createElement("div"),heading=document.createElement("span"),
        count=document.createElement("strong");
      heading.textContent=label;count.textContent=String(value??0);
      card.append(heading,count);cards.append(card);
    }
    const entries=Object.entries(data.candidates_by_source||{})
      .sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]));
    if(!entries.length)sources.textContent="Keine Kandidaten im gespeicherten Snapshot.";
    const max=Math.max(1,...entries.map(([,count])=>count));
    for(const [source,count] of entries){
      const line=document.createElement("div"),label=document.createElement("p"),
        bar=document.createElement("div");
      label.textContent=source+": "+count;
      bar.style.height="12px";bar.style.background="#2563eb";
      bar.style.width=Math.max(1,100*count/max)+"%";
      bar.setAttribute("role","img");
      bar.setAttribute("aria-label",source+": "+count+" Kandidaten");
      line.append(label,bar);sources.append(line);
    }
    const rows=Array.isArray(data.candidates)?data.candidates:[];
    if(!rows.length)candidates.textContent="Keine Kandidaten zur Prüfung vorhanden.";
    for(const item of rows){
      const p=document.createElement("p");
      const flags=[];
      if(item.identity_conflicts?.length)flags.push("Identitätskonflikt");
      if(!item.has_confirmed_profiles)flags.push("Noch kein bestätigtes Profil");
      p.append(document.createTextNode("Mitglied "+item.person_number+" · "+
        (item.platform||"Plattform unbekannt")+" · Priorität "+(item.priority??0)+
        " · "+(flags.join(", ")||"Manuell prüfen")+" · "));
      if(typeof item.url==="string"&&item.url.startsWith("https://")){
        const a=document.createElement("a");a.href=item.url;a.target="_blank";
        a.rel="noopener noreferrer";a.textContent="Kandidat";p.append(a);
      }
      if(typeof item.source_url==="string"&&item.source_url.startsWith("https://")){
        const a=document.createElement("a");a.href=item.source_url;a.target="_blank";
        a.rel="noopener noreferrer";a.textContent="Fundstelle";
        p.append(document.createTextNode(" · "),a);
      }
      candidates.append(p);
    }
  }catch(err){
    status.textContent="Recherche-Abdeckung konnte nicht geladen werden.";
    $("message").textContent=err.message;
  }
}
$("coverage-load").addEventListener("click",loadCoverage);
$("language").addEventListener("change",()=>{
  $("coverage-status").textContent="Für diese Sprache Recherche-Abdeckung laden.";
  for(const id of ["coverage-cards","coverage-sources","coverage-candidates"])
    $(id).replaceChildren();
});
