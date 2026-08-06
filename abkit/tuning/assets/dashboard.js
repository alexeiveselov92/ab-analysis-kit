"use strict";(()=>{var He={"--abk-page":"#f5f1e8","--abk-card":"#fbf9f3","--abk-ink":"#1b1916","--abk-ink-2":"#6e675b","--abk-muted":"#9a9384","--abk-border":"#e6e0d4","--abk-chart-bg":"#211e1a","--abk-chart-border":"#332f29","--abk-chart-ink":"#c9c2b4","--abk-chart-grid":"#9a9384","--abk-series-1":"#c9a6f0","--abk-series-2":"#8e76e0","--abk-st-good":"#1e9e6a","--abk-st-warn":"#e0a23b","--abk-st-serious":"#d6453d","--abk-st-critical":"#b23a6b","--abk-good-text":"#15774e","--abk-explore-accent":"#6a45c4"};function Ie(e){return getComputedStyle(document.documentElement).getPropertyValue(e).trim()||He[e]||"#888"}function _n(e){let l=e.replace("#","").trim();l.length===3&&(l=l[0]+l[0]+l[1]+l[1]+l[2]+l[2]);let d=parseInt(l,16);return l.length!==6||Number.isNaN(d)?[137,135,129]:[d>>16&255,d>>8&255,d&255]}function dn(e,l){let[d,c,v]=_n(e);return`rgba(${d},${c},${v},${l})`}function cn(e){let l=Math.max(1,window.devicePixelRatio||1),d=e.clientWidth||e.offsetWidth||0,c=e.clientHeight||e.offsetHeight||0;return e.width=Math.round(d*l),e.height=Math.round(c*l),l}function bn(e,l,d,c){let v=()=>e.width-(l.l+l.r)*c,U=()=>e.height-(l.t+l.b)*c,R=()=>d.xmax-d.xmin||1,y=()=>d.vmax-d.vmin||1;return{px:S=>l.l*c+(S-d.xmin)/R()*v(),py:S=>e.height-l.b*c-(S-d.vmin)/y()*U(),xAt:S=>d.xmin+(S-l.l*c)/(v()||1)*R(),plotW:v,plotH:U}}function Fe(e,l,d){return{left:l.l*d,top:l.t*d,right:e.width-l.r*d,bottom:e.height-l.b*d}}var Ne=Number.isFinite;function pn(e,l,d,c,v,U,R,y,B,L,H,S,le){let Z=l.length,Y=Math.max(1,Math.round(R)),V=v-c||1,re=0;for(let x=0;x<Z;x++){let I=d[x];!Ne(I)||l[x]<c||l[x]>v||re++}e.strokeStyle=L,e.lineWidth=H*S,e.lineJoin="round",le&&e.setLineDash(le.map(x=>x*S)),e.beginPath();let ie=[];if(re<=Y){let x=j=>j>=0&&j<Z&&Ne(d[j])&&l[j]>=c&&l[j]<=v,I=!1;for(let j=0;j<Z;j++){if(!x(j)){I=!1;continue}let P=y(l[j]),D=B(d[j]);if(!x(j-1)&&!x(j+1)){ie.push([P,D]),I=!1;continue}I?e.lineTo(P,D):(e.moveTo(P,D),I=!0)}}else{let x=new Array(Y).fill(null),I=new Array(Y).fill(null);for(let P=0;P<Z;P++){let D=d[P],te=l[P];if(!Ne(D)||te<c||te>v)continue;let _=Math.floor((te-c)/V*(Y-1));_=_<0?0:_>Y-1?Y-1:_,(x[_]===null||D<x[_])&&(x[_]=D),(I[_]===null||D>I[_])&&(I[_]=D)}let j=!1;for(let P=0;P<Y;P++){if(I[P]===null){j=!1;continue}let D=U+P,te=B(I[P]),_=B(x[P]);j?e.lineTo(D,te):(e.moveTo(D,te),j=!0),e.lineTo(D,_)}}if(e.stroke(),le&&e.setLineDash([]),ie.length>0){e.fillStyle=L,e.beginPath();for(let[x,I]of ie)e.moveTo(x+Math.max(1.5,H)*S,I),e.arc(x,I,Math.max(1.5,H)*S,0,Math.PI*2);e.fill()}}function un(e,l,d,c,v,U,R,y,B){let L=v(U),H=Fe(l,d,c);L<H.top-.5||L>H.bottom+.5||(e.strokeStyle=R,e.lineWidth=1.25*c,B&&e.setLineDash(B.map(S=>S*c)),e.beginPath(),e.moveTo(H.left,L),e.lineTo(H.right,L),e.stroke(),B&&e.setLineDash([]),y&&(e.fillStyle=R,e.textAlign="right",e.textBaseline="middle",e.font=`${10*c}px ui-monospace, Menlo, Consolas, monospace`,e.fillText(y,(d.l-8)*c,L)))}function Ce(e){let l=Math.abs(e);return l>=1e3?e.toFixed(0):l>=10?e.toFixed(1):l>=1?e.toFixed(2):l>=.001||l===0?e.toFixed(3):e.toExponential(1)}function Oe(e){return(e>0?"+":"")+Ce(e)}function Me(e){return e<.001?"<0.001":e.toFixed(3)}function Se(e){return new Date(e).toISOString().slice(0,16).replace("T"," ")}function ze(e){return new Date(e).toISOString().slice(0,10)}var Mn="<svg class='abk-logomark' viewBox='0 0 100 100' width='22' height='22' role='img' aria-label='abkit' focusable='false'><rect x='3' y='3' width='94' height='94' rx='26' fill='#6a45c4'/><g fill='none' stroke='#fbf9f3' stroke-width='9' stroke-linecap='round' stroke-linejoin='round'><polyline points='13 50 34 50'/><polyline points='34 50 86 27'/><polyline points='34 50 86 61'/></g><circle cx='86' cy='27' r='7' fill='#fbf9f3'/></svg>";function kn(){let e=document.createElement("div");e.className="abk-brand",e.innerHTML=Mn;let l=document.createElement("span");return l.className="abk-wordmark",l.textContent="abkit",e.appendChild(l),e}var n="abk-dashboard",Sn=3,Ue={l:3,r:3,t:5,b:5},jn=864e5,An=1200,Bn=8e3,Pn=600,Dn=2e3,Nn=`name: my_experiment
start_ts: 2026-01-01
horizon_ts: 2026-01-15
unit_key: user_id
assignment:
  query: SELECT user_id, variant, exposure_ts FROM assignments
  variants: [control, treatment]
  expected_split: {control: 0.5, treatment: 0.5}
comparisons:
  - metric: my_metric
    is_main_metric: true
    method: {name: t-test}
`,Hn={separated:"",co_leaders:" (co-leaders)",untested:" (untested)",no_leader:""};function o(e,l,d){let c=document.createElement(e);return l&&(c.className=l),d!==void 0&&(c.textContent=d),c}function h(e,l,d){let c=document.createElement("button");return c.className=e,c.textContent=l,c.type="button",d&&(c.title=d),c}var pe=(e,l=Ce)=>e===null?"\u2014":l(e);function oe(e){return e instanceof Error?e.message||e.name:String(e)}function fn(e){return e instanceof Error&&e.name==="AbortError"}function In(e,l){return e===null||l===null?null:(l-e)/jn}var Je=null;function Fn(e,l){var on;zn(),Je&&Je();let d=[];Je=()=>{for(let t of d)t();d.length=0},l.classList.add(n),l.innerHTML="";let c=o("div","abk-root");l.appendChild(c);let v=(on=new URLSearchParams(window.location.search).get("token"))!=null?on:"",U=e.initial_window,R=0,y=null,B=0,L=new Map,H=new Map,S=new Map,le=!1;function Z(t,s={}){let a=new URLSearchParams({...s,token:v});return`${t}?${a.toString()}`}async function Y(t){let s="";try{s=(await t.text()).trim()}catch{s=""}let a=new Error(s||`HTTP ${t.status}`);return a.name=`HTTP${t.status}`,a}async function V(t,s={},a){let i=await fetch(Z(t,s),a?{signal:a}:void 0);if(!i.ok)throw await Y(i);return await i.json()}async function re(t,s){let a=await fetch(Z(t),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(s)});if(!a.ok)throw await Y(a);return await a.json()}let ie=o("div","abk-header");c.appendChild(ie),ie.appendChild(kn());let x=o("div","abk-h-top");ie.appendChild(x);let I=o("h1","abk-title",`dashboard \xB7 ${e.project}`);x.appendChild(I),x.appendChild(o("span","abk-badge-page",`v${e.version}`)),e.profile!==null&&x.appendChild(o("span","abk-badge-page",`profile: ${e.profile}`));let j=o("div","abk-meta");ie.appendChild(j);function P(){let t=fe.length;j.textContent=[`${t} experiment${t===1?"":"s"}`,`booted ${Se(e.generated_at)} UTC`].join(" \xB7 ")}if(v===""){let t=o("div","abk-warning","\u26A0 this page was opened without its ?token= \u2014 every request will be refused. Reopen the URL `abk dashboard` printed.");ie.appendChild(t)}let D=o("div","abk-warning abk-banner");D.style.display="none",ie.appendChild(D);function te(t){D.textContent=t,D.style.display=t===""?"none":""}let _=o("div","abk-controls");c.appendChild(_);let We=o("div","abk-seg");_.appendChild(o("span","abk-ctl-label","sparkline window")),_.appendChild(We);let Ye=new Map;for(let t of e.window_presets){let s=h("abk-seg-btn",t,`bound the sparkline to the last ${t}`);t===U&&s.classList.add("on"),s.addEventListener("click",()=>{if(t!==U){U=t;for(let[a,i]of Ye)i.classList.toggle("on",a===t);Te(Le())}}),Ye.set(t,s),We.appendChild(s)}let Ve=h("abk-btn abk-btn-ghost","Refresh","re-read every row");Ve.addEventListener("click",()=>Te(Le())),_.appendChild(Ve);let ve=h("abk-btn abk-btn-ghost","Reload configs","re-read the project\u2019s experiment YAML from disk");_.appendChild(ve);let Ke=h("abk-btn","New experiment","write a new experiment YAML");_.appendChild(Ke);let je=o("span","abk-chip abk-chip-fill","idle");_.appendChild(je);let $e=h("abk-chip abk-chip-job","jobs: idle","open the job drawer");$e.addEventListener("click",()=>se.toggleList()),_.appendChild($e);let ke=o("div","abk-list");c.appendChild(ke);let fe=e.experiments;function Xe(){if(ke.innerHTML="",L.clear(),fe.length===0){ke.appendChild(o("div","abk-empty","No experiments in this dashboard\u2019s selection \u2014 restart with `abk dashboard --select \u2026`."));return}let t=new Map;for(let a of fe){let i=t.get(a.dir);i===void 0?t.set(a.dir,[a]):i.push(a)}let s=t.size>1;for(let[a,i]of t){s&&ke.appendChild(o("div","abk-group",a===""?"(top level)":a));let b=o("div","abk-table");b.appendChild(wn());for(let g of i){let M=yn(g);L.set(g.name,M),b.appendChild(M.root)}ke.appendChild(b)}}P(),Xe();let C=nn("");C.root.classList.add("abk-create"),C.root.style.display="none",c.insertBefore(C.root,ke);let me=document.createElement("input");me.className="abk-text",me.type="text",me.placeholder="subfolder under experiments/ (optional)",C.root.insertBefore(me,C.root.firstChild);let xe=h("abk-btn abk-btn-run","Create","validate, then write a new file"),Ge=h("abk-btn abk-btn-ghost","Cancel");C.buttons.appendChild(xe),C.buttons.appendChild(Ge);function qe(t){C.clearForce(),xe.disabled=!0,C.say(t?"creating (forced)\u2026":"creating\u2026"),re("/api/experiment/create",{text:C.area.value,folder:me.value.trim()===""?null:me.value.trim(),force:t}).then(s=>{xe.disabled=!1,C.say([`created ${s.path}`,...s.warnings].join(`
`),"ok"),Ee(s.experiments),s.in_selection&&(C.root.style.display="none")}).catch(s=>{xe.disabled=!1;let a=oe(s);C.say(a,"err"),tn(a)&&C.offerForce(()=>qe(!0))})}xe.addEventListener("click",()=>qe(!1)),Ge.addEventListener("click",()=>{C.root.style.display="none"}),Ke.addEventListener("click",()=>{if(C.root.style.display===""){C.root.style.display="none";return}C.root.style.display="",C.clearForce(),C.area.value.trim()===""&&(C.area.value=Nn),C.say("a new experiments/<name>.yml \u2014 the file is named after `name:`"),C.area.focus()}),ve.addEventListener("click",()=>{ve.disabled=!0,re("/api/reload",{}).then(t=>{ve.disabled=!1,Ee(t.experiments),t.warnings.length>0?te(t.warnings.join(`
`)):te("")}).catch(t=>{ve.disabled=!1,te(`reload failed: ${oe(t)}`)})});let se=xn();c.appendChild(se.root);function Le(){return fe.map(t=>t.name)}function Ee(t){let s=Ze(fe);fe=t,P(),Ze(t)!==s&&(Xe(),Te(Le()))}function Ze(t){return JSON.stringify(t)}function Qe(){je.textContent=B>0?`loading ${B}\u2026`:"idle",je.classList.toggle("abk-chip-busy",B>0)}function Te(t){var M;R+=1;let s=R;y==null||y.abort();let a=new AbortController;y=a;for(let f of t)(M=L.get(f))==null||M.pending();let i=t.slice();B=i.length,Qe();let b=async()=>{var f,p;for(;;){if(s!==R)return;let m=i.shift();if(m===void 0)return;try{let E=await V(`/api/stats/${encodeURIComponent(m)}`,{window:U},a.signal);if(s!==R)return;H.set(m,E),(f=L.get(m))==null||f.paint(E)}catch(E){if(fn(E)||s!==R)return;(p=L.get(m))==null||p.paintError(oe(E))}finally{s===R&&(B=Math.max(0,B-1),Qe())}}},g=[];for(let f=0;f<Math.min(Sn,i.length);f++)g.push(b());Promise.all(g)}function en(t){if(!L.has(t))return;let s=R,a=L.get(t);a==null||a.pending(),V(`/api/stats/${encodeURIComponent(t)}`,{window:U},y==null?void 0:y.signal).then(i=>{s===R&&(H.set(t,i),a==null||a.paint(i))}).catch(i=>{fn(i)||s!==R||a==null||a.paintError(oe(i))})}let ge=0,Ae=!1;function Re(t){Ae||(ge&&window.clearTimeout(ge),ge=window.setTimeout(()=>{hn()},t))}async function hn(){try{let t=await V("/api/jobs");if(Ae)return;vn(t)}catch{}Re(le||se.following()?An:Bn)}function vn(t){le=t.pipeline_active;let s=t.jobs.filter(i=>i.status==="running");$e.textContent=s.length===0?"jobs: idle":`jobs: ${s.map(i=>{var b;return`${i.kind} ${(b=i.experiment)!=null?b:i.label}`}).join(", ")}`,$e.classList.toggle("abk-chip-busy",s.length>0);for(let i of L.values())i.root.classList.toggle("abk-busy",le);for(let i of t.jobs){let b=S.get(i.id);S.set(i.id,i.status),b==="running"&&i.status!=="running"&&i.experiment!==null&&en(i.experiment)}let a=new Set(t.jobs.map(i=>i.id));for(let i of[...S.keys()])a.has(i)||S.delete(i);se.adoptList(t.jobs)}function xn(){let t=o("div","abk-drawer");t.style.display="none";let s=o("div","abk-drawer-head"),a=o("span","abk-drawer-label","jobs"),i=o("span","abk-drawer-status",""),b=h("abk-btn abk-btn-danger","Stop","SIGTERM, then SIGKILL after 5s"),g=h("abk-btn abk-btn-ghost","Close");s.appendChild(a),s.appendChild(i),s.appendChild(b),s.appendChild(g);let M=o("div","abk-drawer-list"),f=o("pre","abk-drawer-log");t.appendChild(s),t.appendChild(M),t.appendChild(f);let p=null,m=0,E=0,Q=!1;function G(){E&&(window.clearTimeout(E),E=0)}function de(){t.style.display=""}function A(){G(),p=null,Q=!1,t.style.display="none"}g.addEventListener("click",A),b.addEventListener("click",()=>{let u=p;u!==null&&(b.disabled=!0,re(`/api/job/${encodeURIComponent(u)}/stop`,{}).then(()=>{K()}).catch($=>{i.textContent=`stop failed: ${oe($)}`,b.disabled=!1}))});function z(u){if(u.length!==0){for(let $ of u)f.appendChild(o("div","abk-log-line",$));for(;f.childElementCount>Dn&&f.firstElementChild!==null;)f.removeChild(f.firstElementChild);f.scrollTop=f.scrollHeight}}function N(u){a.textContent=u.label;let $=[u.status];if(u.returncode!==null&&$.push(`exit ${u.returncode}`),u.truncated&&$.push(`${u.dropped} earlier line(s) discarded \u2014 the buffer is capped`),i.textContent=$.join(" \xB7 "),i.className=`abk-drawer-status abk-job-${u.status}`,b.style.display=u.status==="running"?"":"none",b.disabled=u.status!=="running",z(u.lines),m=u.next_offset,u.url!==null&&u.status==="running"){let J=h("abk-btn abk-btn-ghost","Open cockpit"),ee=u.url;J.addEventListener("click",()=>Be(ee)),i.appendChild(J)}}async function K(){let u=p;if(u!==null){G();try{let $=await V(`/api/job/${encodeURIComponent(u)}`,{offset:String(m)});if(p!==u)return;N($),$.status==="running"&&(E=window.setTimeout(()=>{K()},Pn))}catch($){if(p!==u)return;i.textContent=`poll failed: ${oe($)}`}}}return{root:t,follow(u){G(),p=u,m=0,Q=!1,M.style.display="none",f.style.display="",f.textContent="",i.textContent="starting\u2026",de(),K(),Re(0)},toggleList(){if(Q){A();return}G(),p=null,Q=!0,a.textContent="jobs",i.textContent="",b.style.display="none",M.style.display="",f.style.display="none",de(),Re(0)},adoptList(u){if(Q){if(M.textContent="",u.length===0){M.appendChild(o("div","abk-log-line","no jobs spawned yet"));return}for(let $ of u){let J=o("div","abk-job-row");J.appendChild(o("span",`abk-job-${$.status}`,$.status)),J.appendChild(o("span","abk-job-label",$.label));let ee=h("abk-btn abk-btn-ghost","log");ee.addEventListener("click",()=>se.follow($.id)),J.appendChild(ee),M.appendChild(J)}}},following(){return p!==null},dispose(){G(),p=null}}}function Be(t,s){let a=null;try{a=window.open(t,"_blank")}catch{a=null}if(a!==null||s===void 0)return;let i=o("a","abk-link",t);i.href=t,i.target="_blank",i.rel="noopener",s(i)}function nn(t){let s=o("div","abk-editor"),a=document.createElement("textarea");a.className="abk-yaml",a.spellcheck=!1,a.rows=18,a.placeholder=t,s.appendChild(a);let i=o("div","abk-editor-msg");i.style.display="none",s.appendChild(i);let b=o("div","abk-btn-row");b.style.display="none",s.appendChild(b);let g=o("div","abk-btn-row");s.appendChild(g);function M(m,E="info"){i.textContent=m,i.className=`abk-editor-msg abk-editor-msg-${E}`,i.style.display=m===""?"none":""}function f(){b.innerHTML="",b.style.display="none"}function p(m){f();let E=h("abk-btn abk-btn-danger","Save anyway","write the file even though `abk run` will refuse it");E.addEventListener("click",()=>{f(),m()}),b.appendChild(E),b.style.display=""}return{root:s,area:a,buttons:g,say:M,offerForce:p,clearForce:f}}function tn(t){return t.includes("not valid for this project")}function gn(t,s){let a=nn("");a.root.style.display="none";let i=null,b=t.name,g=h("abk-btn abk-btn-run","Save","validate, archive, then write"),M=h("abk-btn abk-btn-ghost","Revert","discard the edits in this box"),f=h("abk-btn abk-btn-danger","Delete\u2026","archive the YAML, then remove it");a.buttons.appendChild(g),a.buttons.appendChild(M),a.buttons.appendChild(f);let p=o("div","abk-confirm");p.style.display="none",p.appendChild(o("div","abk-confirm-text","Delete archives the YAML under .history/ and removes the file. The experiment\u2019s persisted rows are NOT deleted \u2014 `abk clean --orphaned-experiments` prunes those."));let m=o("div","abk-btn-row"),E=h("abk-btn abk-btn-danger","Delete anyway"),Q=h("abk-btn abk-btn-ghost","Cancel");m.appendChild(E),m.appendChild(Q),p.appendChild(m),a.root.appendChild(p);function G(){a.clearForce(),a.say("loading\u2026"),g.disabled=!0,V(`/api/experiment-source/${encodeURIComponent(b)}`).then(A=>{i=A,a.area.value=A.yaml_text,A.editable?(g.disabled=!1,a.say(A.path)):a.say(`${A.path} is too large to edit here \u2014 it was truncated for display; open it in your editor`,"err")}).catch(A=>{i=null,a.area.value="",a.say(`could not read the YAML: ${oe(A)}`,"err")})}function de(A){if(i===null)return;a.clearForce(),g.disabled=!0,a.say(A?"saving (forced)\u2026":"saving\u2026");let z=a.area.value;re("/api/experiment/save",{select:b,text:z,digest:i.digest,force:A}).then(N=>{var K;g.disabled=!1,b=N.name,i={name:N.name,path:N.path,yaml_text:z,truncated:!1,digest:N.digest,editable:!0},a.say([`saved ${N.path} \xB7 previous archived at ${(K=N.archived)!=null?K:"\u2014"}`,...N.warnings].join(`
`),N.warnings.length>0?"err":"ok"),Ee(N.experiments),N.renamed_from===null&&en(N.name)}).catch(N=>{g.disabled=!1;let K=oe(N);a.say(K,"err"),tn(K)&&a.offerForce(()=>de(!0))})}return g.addEventListener("click",()=>de(!1)),M.addEventListener("click",()=>G()),f.addEventListener("click",()=>{p.style.display=""}),Q.addEventListener("click",()=>{p.style.display="none"}),E.addEventListener("click",()=>{var A;p.style.display="none",a.say("deleting\u2026"),re("/api/experiment/delete",{select:b,digest:(A=i==null?void 0:i.digest)!=null?A:null}).then(z=>{te([`deleted ${z.path} \u2014 archived at ${z.archived}`,...z.warnings].join(`
`)),Ee(z.experiments)}).catch(z=>{a.say(`delete refused: ${oe(z)}`,"err")})}),s.addEventListener("click",()=>{if(a.root.style.display===""){a.root.style.display="none";return}a.root.style.display="",G()}),a.root}function wn(){let t=o("div","abk-row abk-head");for(let[s,a]of[["abk-cell-disclose",""],["abk-cell-name","experiment"],["abk-cell-verdict","verdict"],["abk-cell-effect","effect (CI)"],["abk-cell-p","p / \u03B1"],["abk-cell-time","elapsed"],["abk-cell-spark","effect over the window"],["abk-cell-actions",""]])t.appendChild(o("div",`abk-cell ${s}`,a));return t}function yn(t){let s=o("div","abk-row");s.setAttribute("data-abk-experiment",t.name);let a=o("div","abk-row-main");s.appendChild(a);let i=o("div","abk-cell abk-cell-disclose"),b=h("abk-disclose","\u25B8","show verdicts, warnings and actions");b.setAttribute("aria-expanded","false"),i.appendChild(b),a.appendChild(i);let g=o("div","abk-cell abk-cell-name");g.appendChild(o("div","abk-name",t.name));let M=[t.file];if(t.status!==null&&M.push(t.status),t.main_metric!==null&&M.push(t.main_metric),g.appendChild(o("div","abk-sub",M.join(" \xB7 "))),t.tags.length>0){let r=o("div","abk-tags");for(let T of t.tags)r.appendChild(o("span","abk-tag",T));g.appendChild(r)}a.appendChild(g);let f=o("div","abk-cell abk-cell-verdict"),p=o("span","abk-chip abk-v-pending","pending");f.appendChild(p);let m=o("span","abk-badges");f.appendChild(m),a.appendChild(f);let E=o("div","abk-cell abk-cell-effect","\u2014");a.appendChild(E);let Q=o("div","abk-cell abk-cell-p","\u2014");a.appendChild(Q);let G=o("div","abk-cell abk-cell-time","\u2014");a.appendChild(G);let de=o("div","abk-cell abk-cell-spark"),A=document.createElement("canvas");A.className="abk-spark",de.appendChild(A),a.appendChild(de);let z=o("div","abk-cell abk-cell-actions"),N=h("abk-btn","Open","the full report for this experiment"),K=h("abk-btn","Explore","launch the tuning cockpit (may take a while)"),u=h("abk-btn abk-btn-run","Run","spawn `abk run` for this experiment");z.appendChild(N),z.appendChild(K),z.appendChild(u),a.appendChild(z);let $=o("div","abk-row-note");$.style.display="none",s.appendChild($);let J=o("div","abk-row-msg");J.style.display="none",s.appendChild(J);let ee=o("div","abk-detail");ee.style.display="none",s.appendChild(ee);let ue=!1,Pe=[];function ce(r,T="info"){J.textContent=r,J.className=`abk-row-msg${T==="err"?" abk-row-msg-err":""}`,J.style.display=r===""?"none":""}function ye(r,T){$.textContent=r,$.className=`abk-row-note ${T}`.trim(),$.style.display=r===""?"none":""}N.addEventListener("click",()=>{let r=Z(`/experiment/${encodeURIComponent(t.name)}`);Be(r,T=>{ce("the browser blocked the tab \u2014 ","info"),J.appendChild(T)})}),K.addEventListener("click",()=>{K.disabled=!0;let r=()=>{K.disabled=!1};ce("starting the explore cockpit \u2014 this can take up to 90 s\u2026"),re("/api/explore",{select:t.name}).then(T=>{r(),ce(""),se.follow(T.job_id),Be(T.url,W=>{ce("cockpit ready \u2014 ","info"),J.appendChild(W)})}).catch(T=>{r(),ce(`explore failed: ${oe(T)}`,"err")})});function _e(r,T,W){ce(`starting ${W}\u2026`),re(r,T).then(X=>{ce(""),se.follow(X.job_id)}).catch(X=>{ce(`${W} refused: ${oe(X)}`,"err")})}u.addEventListener("click",()=>_e("/api/run",{select:t.name},"run"));let be=o("div","abk-readout"),rn=!1;function ln(){var W,X;be.textContent="";let r=H.get(t.name);if(r===void 0)return;r.error!==null&&be.appendChild(o("div","abk-warning",`\u26A0 ${r.error}`));for(let F of r.warnings)be.appendChild(o("div","abk-warning",`\u26A0 ${F}`));for(let F of r.caveats)be.appendChild(o("div","abk-caveat",`! ${F}`));if(r.rationale.length>0){let F=o("div","abk-block");F.appendChild(o("div","abk-block-title","why this verdict"));for(let O of r.rationale)F.appendChild(o("div","abk-rationale",O));be.appendChild(F)}if(r.verdicts.length>0){let F=o("div","abk-block");F.appendChild(o("div","abk-block-title","per arm pair"));for(let O of r.verdicts){let q=o("div","abk-pair");q.appendChild(o("span",`abk-v-word abk-v-${O.verdict.toLowerCase()}`,O.verdict)),q.appendChild(o("span","abk-pair-name",`${O.metric}: ${O.pair.c} vs ${O.pair.t}`)),((W=O.role)!=null?W:"vs_control")!=="vs_control"&&q.appendChild(o("span","abk-pair-role","arm vs arm")),q.appendChild(o("span","abk-pair-effect",pe(O.effect,Oe))),O.guardrail_regressed&&q.appendChild(o("span","abk-badge-guardrail","guardrail regressed"));for(let ne of O.caveats)q.appendChild(o("div","abk-caveat",`! ${ne}`));F.appendChild(q)}be.appendChild(F)}let T=[`SRM p ${pe(r.srm_pvalue,Me)}`,`last look ${r.last_end_ts===null?"\u2014":`${Se(r.last_end_ts)} UTC`}`,`timezone ${(X=r.timezone)!=null?X:"\u2014"}`,r.locked?"the pipeline lock is HELD":"unlocked"];be.appendChild(o("div","abk-facts",T.join(" \xB7 ")))}function $n(){ee.appendChild(be);let r=o("div","abk-block");r.appendChild(o("div","abk-block-title","run one comparison"));let T=o("div","abk-btn-row");for(let ae of t.comparisons){let Rn=ae.is_main_metric?ae.metric:`${ae.metric} (secondary)`,sn=h("abk-btn",`Run ${Rn}`,`abk run --metric ${ae.metric}`);sn.addEventListener("click",()=>_e("/api/run",{select:t.name,metric:ae.metric},`run ${ae.metric}`)),T.appendChild(sn)}r.appendChild(T),ee.appendChild(r);let W=o("div","abk-block");W.appendChild(o("div","abk-block-title","maintenance"));let X=o("div","abk-btn-row"),F=h("abk-btn","Unlock","release a stale pipeline lock (`abk unlock`)");F.addEventListener("click",()=>_e("/api/unlock",{select:t.name},"unlock")),X.appendChild(F);let O=h("abk-btn abk-btn-danger","Clean\u2026","delete orphaned rows (`abk clean --execute`)");X.appendChild(O);let q=h("abk-btn abk-btn-ghost","Edit YAML",t.file);X.appendChild(q),W.appendChild(X);let ne=o("div","abk-confirm");ne.style.display="none",ne.appendChild(o("div","abk-confirm-text","Clean runs `abk clean --select \u2026 --execute`: it DELETES orphaned _ab_results / _ab_unit_state rows for this experiment. There is no undo."));let he=o("div","abk-btn-row"),k=h("abk-btn abk-btn-danger","Clean anyway"),w=h("abk-btn abk-btn-ghost","Cancel");k.addEventListener("click",()=>{ne.style.display="none",_e("/api/clean",{select:t.name},"clean")}),w.addEventListener("click",()=>{ne.style.display="none"}),he.appendChild(k),he.appendChild(w),ne.appendChild(he),O.addEventListener("click",()=>{ne.style.display=""}),W.appendChild(ne),ee.appendChild(W),ee.appendChild(gn(t,q))}b.addEventListener("click",()=>{ue=!ue,b.textContent=ue?"\u25BE":"\u25B8",b.setAttribute("aria-expanded",ue?"true":"false"),ee.style.display=ue?"":"none",ue&&(rn||(rn=!0,$n()),ln())});function Ln(){p.className="abk-chip abk-v-pending",p.textContent="pending",m.textContent="",E.textContent="\u2014",Q.textContent="\u2014",G.textContent="\u2014",ye("",""),Pe=[],De()}function En(r){var O,q,ne,he;if(m.textContent="",r.error!==null)p.className="abk-chip abk-v-error",p.textContent="error",ye(r.error,"abk-v-error");else if(r.verdict===null)p.className="abk-chip abk-v-none",p.textContent="no data",ye("no computed results yet \u2014 press Run","");else{let k=["abk-chip",`abk-v-${r.verdict.toLowerCase()}`],w="";r.srm_flag?(k.push("abk-srm-fail"),w=`SRM FAILED (p ${pe(r.srm_pvalue,Me)}) \u2014 effects untrustworthy`):r.insufficient?(k.push("abk-insufficient"),w="insufficient data at the latest look \u2014 inference withheld"):!r.is_horizon&&r.verdict==="INCONCLUSIVE"&&(k.push("abk-prehorizon"),w="pre-horizon: fixed CIs are not peeking-valid, so a verdict is withheld"),p.className=k.join(" "),p.textContent=r.verdict,ye(w,w===""?"":k.slice(1).join(" "))}let T=new Set(r.verdicts.filter(k=>{var w;return((w=k.role)!=null?w:"vs_control")==="vs_control"}).map(k=>k.pair.t)).size>1;if(T&&r.leader!==null){let k=(q=Hn[(O=r.separation)!=null?O:"separated"])!=null?q:"",w=o("span","abk-badge-leader",`\u2192 ${r.leader}${k}`);w.title=(he=(ne=r.rollups.find(ae=>ae.leader===r.leader))==null?void 0:ne.rationale.join(`
`))!=null?he:"",m.appendChild(w)}let W=r.rollups.reduce((k,w)=>k+w.losers.length,0);if(T&&W>0){let k=o("span","abk-badge-guardrail",`${W} lost`);k.title=r.rollups.filter(w=>w.losers.length>0).map(w=>`${w.metric}: ${w.losers.join(", ")}`).join(`
`),m.appendChild(k)}if(r.leaders_agree===!1){let k=o("span","abk-badge-caveat","leaders split");k.title=r.rollups.map(w=>{var ae;return`${w.metric}: ${(ae=w.leader)!=null?ae:"no leader"}`}).join(`
`),m.appendChild(k)}if(r.guardrail_regressed&&m.appendChild(o("span","abk-badge-guardrail","guardrail")),r.caveats.length>0){let k=o("span","abk-badge-caveat",`\u26A0 ${r.caveats.length}`);k.title=r.caveats.join(`
`),m.appendChild(k)}if(r.weekly_cycle_pct!==null){let k=o("span","abk-badge-caveat",`${Math.round(r.weekly_cycle_pct*100)}% wk`);k.title="decided before one full weekly cycle",m.appendChild(k)}if(r.locked){let k=o("span","abk-badge-lock","locked");k.title="the pipeline lock is held \u2014 Run would refuse",m.appendChild(k)}E.textContent=`${pe(r.effect,Oe)} [${pe(r.ci[0])}, ${pe(r.ci[1])}]`,Q.textContent=`${pe(r.pvalue,Me)} / ${pe(r.alpha)}`;let X=In(r.start_ts,r.horizon_ts),F=r.elapsed_days===null?"\u2014":`${Ce(r.elapsed_days)}d`;G.textContent=X===null?F:`${F} / ${Ce(X)}d${r.is_horizon?" \u2713":""}`,G.title=r.last_end_ts===null?"no computed look yet":`latest look ${Se(r.last_end_ts)} UTC`,Pe=r.spark,De(),ue&&ln()}function Tn(r){p.className="abk-chip abk-v-error",p.textContent="error",m.textContent="",ye(r,"abk-v-error")}function De(){On(A,Pe)}return{root:s,pending:Ln,paint:En,paintError:Tn,redraw:De}}let Cn=window.setTimeout(()=>{e.experiments.length>0&&Te(Le()),Re(0)},0);d.push(()=>window.clearTimeout(Cn)),d.push(()=>{Ae=!0,ge&&window.clearTimeout(ge),se.dispose(),y==null||y.abort(),R+=1});let we=0,an=()=>{we&&window.clearTimeout(we),we=window.setTimeout(()=>{for(let t of L.values())t.redraw()},120)};window.addEventListener("resize",an),d.push(()=>{window.removeEventListener("resize",an),we&&window.clearTimeout(we)})}function On(e,l){let d=null;try{d=e.getContext("2d")}catch{d=null}if(d===null){e.classList.add("abk-spark-blank");return}let c=cn(e);if(d.clearRect(0,0,e.width,e.height),e.width===0||e.height===0||l.length===0){e.title="";return}let v=l.map(([V])=>V),U=l.map(([,V])=>V===null?NaN:V),R=U.filter(V=>Number.isFinite(V)),y=v[0],B=v[v.length-1]===y?y+1:v[v.length-1],L=Math.min(0,...R),H=Math.max(0,...R),S=(H-L||Math.abs(H)||1)*.15,le={xmin:y,xmax:B,vmin:L-S,vmax:H+S},Z=bn(e,Ue,le,c),Y=Fe(e,Ue,c);un(d,e,Ue,c,Z.py,0,dn(Ie("--abk-chart-grid"),.45),"",[3,3]),pn(d,v,U,y,B,Y.left,Y.right-Y.left,Z.px,Z.py,Ie("--abk-series-1"),1.4,c),e.title=`${l.length} bucket(s), ${ze(y)} \u2192 ${ze(B)} UTC`}var mn=!1;function zn(){if(mn)return;mn=!0;let l=`
:where(:root){${Object.entries(He).map(([c,v])=>`${c}:${v}`).join(";")};
  --abk-sans:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  --abk-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
.${n}{font-family:var(--abk-sans);color:var(--abk-ink);background:var(--abk-page);}
.${n} *{box-sizing:border-box;}
.${n} .abk-root{min-height:100vh;padding:16px 18px 96px;}
/* header ------------------------------------------------------------------- */
.${n} .abk-header{padding-left:12px;border-left:3px solid var(--abk-explore-accent);
  margin-bottom:10px;}
.${n} .abk-brand{display:flex;align-items:center;gap:8px;margin-bottom:6px;}
.${n} .abk-logomark{width:22px;height:22px;border-radius:6px;display:block;}
.${n} .abk-wordmark{font:700 14px var(--abk-sans);color:var(--abk-explore-accent);
  letter-spacing:-0.01em;}
.${n} .abk-h-top{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px;}
.${n} .abk-title{font-size:19px;font-weight:700;margin:0;letter-spacing:-0.01em;}
.${n} .abk-badge-page{font-size:10px;font-family:var(--abk-mono);text-transform:uppercase;
  letter-spacing:0.08em;padding:2px 8px;border-radius:8px;border:1px solid var(--abk-border);
  color:var(--abk-ink-2);}
.${n} .abk-meta{font-size:11.5px;color:var(--abk-ink-2);font-family:var(--abk-mono);
  margin-top:3px;}
.${n} .abk-warning{font-size:12px;color:var(--abk-ink);
  background:color-mix(in srgb, var(--abk-st-warn) 14%, transparent);
  border:1px solid var(--abk-st-warn);border-radius:8px;padding:4px 10px;margin:4px 0;}
.${n} .abk-caveat{font-size:11.5px;color:var(--abk-ink-2);font-family:var(--abk-mono);
  margin:3px 0;}
/* controls ----------------------------------------------------------------- */
.${n} .abk-controls{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;
  margin:10px 0;}
.${n} .abk-ctl-label{font:600 10.5px var(--abk-mono);color:var(--abk-muted);
  text-transform:uppercase;letter-spacing:0.06em;}
.${n} .abk-seg{display:flex;gap:4px;}
.${n} .abk-seg-btn{font:600 11px var(--abk-mono);padding:4px 9px;border-radius:8px;
  border:1px solid var(--abk-border);background:var(--abk-card);color:var(--abk-ink-2);
  cursor:pointer;}
.${n} .abk-seg-btn.on{border-color:var(--abk-explore-accent);
  color:var(--abk-explore-accent);background:color-mix(in srgb, var(--abk-explore-accent) 8%, transparent);}
.${n} .abk-chip{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;
  background:var(--abk-card);border:1px solid var(--abk-border);border-radius:10px;
  font:11.5px var(--abk-mono);color:var(--abk-ink-2);}
.${n} .abk-chip-job{cursor:pointer;}
.${n} .abk-chip-busy{border-color:var(--abk-explore-accent);
  color:var(--abk-explore-accent);}
/* the list ----------------------------------------------------------------- */
.${n} .abk-group{font:700 11px var(--abk-mono);text-transform:uppercase;
  letter-spacing:0.06em;color:var(--abk-muted);margin:14px 0 4px;}
.${n} .abk-table{border:1px solid var(--abk-border);border-radius:12px;
  background:var(--abk-card);overflow:hidden;}
.${n} .abk-row{border-top:1px solid var(--abk-border);}
.${n} .abk-table > .abk-row:first-child{border-top:none;}
.${n} .abk-row-main{display:grid;align-items:center;gap:10px;padding:8px 12px;
  grid-template-columns:22px minmax(180px,2fr) 140px 150px 110px 96px minmax(90px,1fr) auto;}
.${n} .abk-head .abk-row-main,.${n} .abk-head{background:transparent;}
.${n} .abk-head{display:grid;align-items:center;gap:10px;padding:6px 12px;
  grid-template-columns:22px minmax(180px,2fr) 140px 150px 110px 96px minmax(90px,1fr) auto;
  font:600 9.5px var(--abk-mono);text-transform:uppercase;letter-spacing:0.07em;
  color:var(--abk-muted);border-bottom:1px solid var(--abk-border);}
.${n} .abk-cell{min-width:0;font:11.5px var(--abk-mono);color:var(--abk-ink-2);}
.${n} .abk-cell-name{font-family:var(--abk-sans);}
.${n} .abk-name{font:600 13px var(--abk-sans);color:var(--abk-ink);
  overflow-wrap:anywhere;}
.${n} .abk-sub{font:10.5px var(--abk-mono);color:var(--abk-muted);overflow-wrap:anywhere;}
.${n} .abk-tags{display:flex;flex-wrap:wrap;gap:4px;margin-top:3px;}
.${n} .abk-tag{font:9.5px var(--abk-mono);border:1px solid var(--abk-border);
  border-radius:6px;padding:0 5px;color:var(--abk-muted);}
.${n} .abk-badges{display:inline-flex;flex-wrap:wrap;gap:4px;margin-left:6px;}
.${n} .abk-disclose{background:none;border:none;cursor:pointer;color:var(--abk-muted);
  font-size:12px;padding:0;}
.${n} .abk-spark{width:100%;height:26px;display:block;}
.${n} .abk-spark-blank{opacity:0.35;}
.${n} .abk-cell-actions{display:flex;gap:6px;justify-content:flex-end;}
/* A dashed Run while a pipeline job runs: a HINT, not a disabled button \u2014 the
   chip is advisory (it can lag by one finished job) and the route's 400 is the
   authority, so the button must stay clickable. */
.${n} .abk-busy .abk-btn-run{border-style:dashed;}
/* verdict chips + the \xA74 markers -------------------------------------------- */
.${n} .abk-v-pending{color:var(--abk-muted);border-style:dashed;}
.${n} .abk-v-none{color:var(--abk-muted);border-style:dashed;}
.${n} .abk-v-win{border-color:var(--abk-st-good);color:var(--abk-good-text);
  font-weight:700;}
.${n} .abk-v-lose{border-color:var(--abk-st-serious);color:var(--abk-st-serious);
  font-weight:700;}
.${n} .abk-v-flat{border-color:var(--abk-border);color:var(--abk-ink-2);}
.${n} .abk-v-inconclusive{border-color:var(--abk-st-warn);color:var(--abk-ink);}
.${n} .abk-v-error{border-color:var(--abk-st-critical);color:var(--abk-st-critical);}
.${n} .abk-prehorizon{border-style:dashed;}
.${n} .abk-insufficient{background:color-mix(in srgb, var(--abk-muted) 14%, transparent);}
.${n} .abk-srm-fail{background:var(--abk-st-critical);border-color:var(--abk-st-critical);
  color:var(--abk-card);font-weight:700;}
.${n} .abk-badge-guardrail{font:600 9.5px var(--abk-mono);padding:1px 6px;
  border-radius:7px;border:1px solid var(--abk-st-serious);color:var(--abk-st-serious);}
.${n} .abk-badge-caveat{font:600 9.5px var(--abk-mono);padding:1px 6px;border-radius:7px;
  border:1px solid var(--abk-st-warn);color:var(--abk-ink-2);cursor:help;}
.${n} .abk-badge-lock{font:600 9.5px var(--abk-mono);padding:1px 6px;border-radius:7px;
  border:1px solid var(--abk-border);color:var(--abk-muted);cursor:help;}
.${n} .abk-badge-leader{font:600 9.5px var(--abk-mono);padding:1px 6px;border-radius:7px;
  border:1px solid var(--abk-explore-accent);color:var(--abk-explore-accent);cursor:help;}
.${n} .abk-pair-role{margin-left:6px;font:500 9px var(--abk-mono);padding:0 4px;
  border:1px solid var(--abk-border);border-radius:3px;color:var(--abk-muted);}
/* row note / message / detail ----------------------------------------------- */
.${n} .abk-row-note{font:11px var(--abk-mono);padding:0 12px 7px 44px;
  color:var(--abk-ink-2);}
.${n} .abk-row-note.abk-srm-fail{color:var(--abk-st-critical);background:none;
  font-weight:700;}
.${n} .abk-row-note.abk-v-error{color:var(--abk-st-critical);overflow-wrap:anywhere;}
.${n} .abk-row-msg{font:11px var(--abk-mono);padding:0 12px 7px 44px;
  color:var(--abk-ink-2);overflow-wrap:anywhere;}
.${n} .abk-row-msg-err{color:var(--abk-st-critical);}
.${n} .abk-detail{padding:2px 12px 12px 44px;border-top:1px dashed var(--abk-border);}
.${n} .abk-block{margin:8px 0;}
.${n} .abk-block-title{font:600 9.5px var(--abk-mono);text-transform:uppercase;
  letter-spacing:0.07em;color:var(--abk-muted);margin-bottom:4px;}
.${n} .abk-rationale{font:11px var(--abk-mono);color:var(--abk-ink-2);margin:2px 0;
  line-height:1.5;}
.${n} .abk-pair{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px;margin:3px 0;
  font:11px var(--abk-mono);}
.${n} .abk-v-word{font-weight:700;}
.${n} .abk-pair-effect{color:var(--abk-ink);}
.${n} .abk-facts{font:10.5px var(--abk-mono);color:var(--abk-muted);margin-top:6px;}
.${n} .abk-btn-row{display:flex;flex-wrap:wrap;gap:6px;}
/* the YAML editor (UI-1) ---------------------------------------------------- */
.${n} .abk-editor{margin-top:8px;display:flex;flex-direction:column;gap:8px;}
.${n} .abk-create{margin:0 0 12px;padding:10px 12px;background:var(--abk-card);
  border:1px solid var(--abk-border);border-radius:11px;}
.${n} .abk-yaml{width:100%;box-sizing:border-box;font:11px var(--abk-mono);
  line-height:1.55;padding:9px 10px;border:1px solid var(--abk-border);border-radius:9px;
  background:var(--abk-page);color:var(--abk-ink);resize:vertical;min-height:180px;
  white-space:pre;overflow-wrap:normal;overflow:auto;}
.${n} .abk-yaml:focus{outline:2px solid var(--abk-explore-accent);outline-offset:1px;}
.${n} .abk-text{width:100%;box-sizing:border-box;font:11px var(--abk-mono);
  padding:5px 8px;border:1px solid var(--abk-border);border-radius:8px;
  background:var(--abk-page);color:var(--abk-ink);}
.${n} .abk-editor-msg{font:11px var(--abk-mono);line-height:1.5;white-space:pre-wrap;
  overflow-wrap:anywhere;color:var(--abk-ink-2);}
.${n} .abk-editor-msg-ok{color:var(--abk-good-text);}
.${n} .abk-editor-msg-err{color:var(--abk-st-critical);}
.${n} .abk-confirm{margin-top:8px;border:1px solid var(--abk-st-warn);border-radius:9px;
  padding:8px 10px;background:color-mix(in srgb, var(--abk-st-warn) 10%, transparent);}
.${n} .abk-confirm-text{font-size:11.5px;line-height:1.5;margin-bottom:8px;}
/* buttons ------------------------------------------------------------------- */
.${n} .abk-btn{font:600 11px var(--abk-sans);padding:5px 10px;border-radius:8px;
  cursor:pointer;border:1px solid var(--abk-border);background:var(--abk-page);
  color:var(--abk-ink);}
.${n} .abk-btn:disabled{opacity:0.5;cursor:progress;}
.${n} .abk-btn-run{border-color:var(--abk-explore-accent);
  color:var(--abk-explore-accent);}
.${n} .abk-btn-danger{border-color:var(--abk-st-critical);color:var(--abk-st-critical);}
.${n} .abk-btn-ghost{background:transparent;color:var(--abk-ink-2);}
.${n} .abk-link{color:var(--abk-explore-accent);overflow-wrap:anywhere;}
/* the job drawer ------------------------------------------------------------ */
.${n} .abk-drawer{position:fixed;left:0;right:0;bottom:0;max-height:46vh;
  display:flex;flex-direction:column;background:var(--abk-card);
  border-top:1px solid var(--abk-border);padding:8px 14px 10px;}
.${n} .abk-drawer-head{display:flex;flex-wrap:wrap;align-items:center;gap:8px;
  margin-bottom:6px;}
.${n} .abk-drawer-label{font:700 11.5px var(--abk-mono);overflow-wrap:anywhere;}
.${n} .abk-drawer-status{font:11px var(--abk-mono);color:var(--abk-ink-2);
  display:inline-flex;align-items:center;gap:8px;}
.${n} .abk-drawer-list{overflow:auto;max-height:32vh;}
.${n} .abk-drawer-log{margin:0;overflow:auto;max-height:34vh;background:var(--abk-page);
  border:1px solid var(--abk-border);border-radius:8px;padding:8px;
  font:10.5px var(--abk-mono);white-space:pre-wrap;}
.${n} .abk-log-line{overflow-wrap:anywhere;}
.${n} .abk-job-row{display:flex;align-items:baseline;gap:8px;font:10.5px var(--abk-mono);
  padding:2px 0;}
.${n} .abk-job-label{flex:1;overflow-wrap:anywhere;}
.${n} .abk-job-running{color:var(--abk-explore-accent);font-weight:700;}
.${n} .abk-job-done{color:var(--abk-good-text);}
.${n} .abk-job-failed{color:var(--abk-st-serious);font-weight:700;}
.${n} .abk-job-stopped{color:var(--abk-muted);}
/* empty state + narrow screens --------------------------------------------- */
.${n} .abk-empty{font-size:13px;color:var(--abk-ink-2);background:var(--abk-card);
  border:1px dashed var(--abk-border);border-radius:10px;padding:14px;}
@media (max-width: 1100px){
  .${n} .abk-head{display:none;}
  .${n} .abk-row-main{grid-template-columns:22px 1fr;grid-auto-rows:min-content;}
  .${n} .abk-cell-actions{justify-content:flex-start;grid-column:2;}
  .${n} .abk-cell-verdict,.${n} .abk-cell-effect,
  .${n} .abk-cell-p,.${n} .abk-cell-time,
  .${n} .abk-cell-spark{grid-column:2;}
  .${n} .abk-detail,.${n} .abk-row-note,
  .${n} .abk-row-msg{padding-left:12px;}
}
`,d=document.createElement("style");d.setAttribute("data-abk-dashboard",""),d.textContent=l,document.head.appendChild(d)}window.__ABK_DASHBOARD__={render:Fn};})();
