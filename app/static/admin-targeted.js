"use strict";
async function loadTargetedResearch(){
  const status=$("targeted-status"),container=$("targeted-results");
  container.replaceChildren();status.textContent="Nachrecherche-Aufträge werden geladen…";
  try{
    const params=new URLSearchParams({language:$("language").value,limit:"100"});
    const data=await request("targeted-research?"+params.toString());
    status.textContent=data.total_uncovered+" Mitglieder ohne bestätigte Profile"+
      (data.truncated?" · erste 100 angezeigt":"")+
      " · Snapshot: "+(data.updated_at||"nicht vorhanden");
    const labels={review_existing:"Vorhandene Kandidaten prüfen",
      inspect_homepage:"Offizielle Homepage untersuchen",
      find_official_source:"Offizielle Personenseite recherchieren"};
    for(const item of data.worklist||[]){
      const p=document.createElement("p");
      p.textContent=item.name+" (Nr. "+item.person_number+") · "+
        labels[item.priority]+" · "+item.candidate_count+" Prüfkandidaten · ";
      const query=document.createElement("span");
      query.textContent="Suchbegriff: "+item.research_query;
      p.append(query);
      if(item.homepage_url?.startsWith("https://")){
        const a=document.createElement("a");a.href=item.homepage_url;
        a.target="_blank";a.rel="noopener noreferrer";
        a.textContent=" · Homepage öffnen";p.append(a);
      }
      container.append(p);
    }
    if(!data.worklist?.length)container.textContent="Keine offenen Nachrecherche-Aufträge.";
  }catch(err){
    status.textContent="Nachrecherche-Aufträge konnten nicht geladen werden.";
    $("message").textContent=err.message;
  }
}
$("targeted-load").addEventListener("click",loadTargetedResearch);
$("language").addEventListener("change",()=>{
  $("targeted-status").textContent="Nachrecherche für diese Sprache laden.";
  $("targeted-results").replaceChildren();
});
