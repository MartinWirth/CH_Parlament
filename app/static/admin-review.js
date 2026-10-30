"use strict";
let reviewEntries=[];
function reviewState(item){
  return item.review_status==="approved"?"approved":
    item.review_status==="rejected"?"rejected":"pending";
}
function reviewStatistics(entries){
  const counts={all:entries.length,pending:0,approved:0,rejected:0};
  for(const entry of entries)counts[reviewState(entry.item)]++;
  return counts;
}
function reviewFiltered(entries,search,status,platform,sort,minPriority=0){
  const needle=search.trim().toLocaleLowerCase("de");
  return entries.filter(({number,item})=>{
    const haystack=[number,item.name,item.platform,item.url,item.source_url]
      .filter(v=>typeof v==="string").join(" ").toLocaleLowerCase("de");
    return (!needle||haystack.includes(needle))&&
      (status==="all"||reviewState(item)===status)&&
      (platform==="all"||item.platform===platform)&&
      (Number(item.review_priority_score||0)>=minPriority);
  }).sort((a,b)=>{
    if(sort==="priority")return (Number(b.item.review_priority_score||0)-Number(a.item.review_priority_score||0))||String(a.number).localeCompare(String(b.number),"de",{numeric:true});
    const value=e=>sort==="platform"?String(e.item.platform||""):
      sort==="status"?reviewState(e.item):String(e.item.name||e.number);
    return value(a).localeCompare(value(b),"de",{numeric:true})||
      String(a.number).localeCompare(String(b.number),"de",{numeric:true});
  });
}
function renderReview(){
  const container=$("review-list");
  container.replaceChildren();
  const stats=reviewStatistics(reviewEntries);
  $("review-stats").textContent="Gesamt: "+stats.all+" · Ungeprüft: "+stats.pending+
    " · Bestätigt: "+stats.approved+" · Verworfen: "+stats.rejected;
  const entries=reviewFiltered(reviewEntries,$("review-search").value,
    $("review-status").value,$("review-platform").value,$("review-sort").value,Number($("review-priority").value));
  $("review-count").textContent=entries.length+" von "+reviewEntries.length+" Kandidaten angezeigt";
  if(!entries.length){container.textContent="Keine passenden Kandidaten.";return;}
  for(const {number,item} of entries){
    const panel=document.createElement("section");
    const title=document.createElement("p");
    title.textContent=(item.name||"Mitglied "+number)+" · "+item.platform+" · "+
      ({pending:"Ungeprüft",approved:"Bestätigt",rejected:"Verworfen"}[reviewState(item)]);
    panel.append(title);
    const priority=document.createElement("p");
    priority.textContent="Prüfpriorität: "+Number(item.review_priority_score||0)+" · "+
      (Array.isArray(item.review_priority_reasons)?item.review_priority_reasons.join(", "):"Keine Bewertung");
    panel.append(priority);
    for(const [label,url] of [["Profil",item.url],["Quelle",item.source_url]]){
      if(typeof url!=="string"||!url.startsWith("https://"))continue;
      const p=document.createElement("p"),a=document.createElement("a");
      a.href=url;a.target="_blank";a.rel="noopener noreferrer";a.textContent=label+": "+url;
      p.append(a);panel.append(p);
    }
    for(const [decision,label] of [["approved","Bestätigen"],["rejected","Verwerfen"]]){
      const button=document.createElement("button");
      button.type="button";button.textContent=label;
      button.disabled=reviewState(item)===decision;
      button.addEventListener("click",async()=>{
        button.disabled=true;
        try{
          await request("review",{method:"POST",headers,
            body:JSON.stringify({language:$("language").value,person_number:number,
              url:item.url,decision})});
          await loadReview();
        }catch(err){$("message").textContent=err.message;button.disabled=false;}
      });
      panel.append(button);
    }
    container.append(panel);
  }
}
for(const id of ["review-search","review-status","review-platform","review-sort","review-priority"]){
  $(id).addEventListener(id==="review-search"?"input":"change",renderReview);
}
$("language").addEventListener("change",()=>{
  reviewEntries=[];
  $("review-platform").replaceChildren(new Option("Alle","all"));
  $("review-list").replaceChildren();
  $("review-stats").textContent="Kandidaten für diese Sprache laden.";
  $("review-count").textContent="";
});
$("review-load").addEventListener("click",loadReview);
async function loadReview(){
  try{
    const data=await request("review?language="+encodeURIComponent($("language").value));
    reviewEntries=Object.entries(data.candidates||{}).flatMap(([number,items])=>
      Array.isArray(items)?items.filter(item=>item&&typeof item==="object")
        .map(item=>({number,item})):[]);
    const select=$("review-platform"),selected=select.value;
    const platforms=[...new Set(reviewEntries.map(entry=>entry.item.platform)
      .filter(value=>typeof value==="string"))].sort((a,b)=>a.localeCompare(b,"de"));
    select.replaceChildren(new Option("Alle","all"),...platforms.map(p=>new Option(p,p)));
    select.value=platforms.includes(selected)?selected:"all";
    renderReview();
  }catch(err){$("message").textContent=err.message;}
}
