"use strict";(()=>{var De={"--abk-page":"#f5f1e8","--abk-card":"#fbf9f3","--abk-ink":"#1b1916","--abk-ink-2":"#6e675b","--abk-muted":"#9a9384","--abk-border":"#e6e0d4","--abk-chart-bg":"#211e1a","--abk-chart-border":"#332f29","--abk-chart-ink":"#c9c2b4","--abk-chart-grid":"#9a9384","--abk-series-1":"#c9a6f0","--abk-series-2":"#8e76e0","--abk-st-good":"#1e9e6a","--abk-st-warn":"#e0a23b","--abk-st-serious":"#d6453d","--abk-st-critical":"#b23a6b","--abk-good-text":"#15774e","--abk-explore-accent":"#6a45c4"};function Ne(e){return getComputedStyle(document.documentElement).getPropertyValue(e).trim()||De[e]||"#888"}function Mn(e){let l=e.replace("#","").trim();l.length===3&&(l=l[0]+l[0]+l[1]+l[1]+l[2]+l[2]);let d=parseInt(l,16);return l.length!==6||Number.isNaN(d)?[137,135,129]:[d>>16&255,d>>8&255,d&255]}function dn(e,l){let[d,c,v]=Mn(e);return`rgba(${d},${c},${v},${l})`}function cn(e){let l=Math.max(1,window.devicePixelRatio||1),d=e.clientWidth||e.offsetWidth||0,c=e.clientHeight||e.offsetHeight||0;return e.width=Math.round(d*l),e.height=Math.round(c*l),l}function bn(e,l,d,c){let v=()=>e.width-(l.l+l.r)*c,J=()=>e.height-(l.t+l.b)*c,E=()=>d.xmax-d.xmin||1,w=()=>d.vmax-d.vmin||1;return{px:M=>l.l*c+(M-d.xmin)/E()*v(),py:M=>e.height-l.b*c-(M-d.vmin)/w()*J(),xAt:M=>d.xmin+(M-l.l*c)/(v()||1)*E(),plotW:v,plotH:J}}function He(e,l,d){return{left:l.l*d,top:l.t*d,right:e.width-l.r*d,bottom:e.height-l.b*d}}var Pe=Number.isFinite;function pn(e,l,d,c,v,J,E,w,B,$,H,M,oe){let q=l.length,Y=Math.max(1,Math.round(E)),V=v-c||1,te=0;for(let x=0;x<q;x++){let I=d[x];!Pe(I)||l[x]<c||l[x]>v||te++}e.strokeStyle=$,e.lineWidth=H*M,e.lineJoin="round",oe&&e.setLineDash(oe.map(x=>x*M)),e.beginPath();let ae=[];if(te<=Y){let x=_=>_>=0&&_<q&&Pe(d[_])&&l[_]>=c&&l[_]<=v,I=!1;for(let _=0;_<q;_++){if(!x(_)){I=!1;continue}let P=w(l[_]),D=B(d[_]);if(!x(_-1)&&!x(_+1)){ae.push([P,D]),I=!1;continue}I?e.lineTo(P,D):(e.moveTo(P,D),I=!0)}}else{let x=new Array(Y).fill(null),I=new Array(Y).fill(null);for(let P=0;P<q;P++){let D=d[P],ee=l[P];if(!Pe(D)||ee<c||ee>v)continue;let T=Math.floor((ee-c)/V*(Y-1));T=T<0?0:T>Y-1?Y-1:T,(x[T]===null||D<x[T])&&(x[T]=D),(I[T]===null||D>I[T])&&(I[T]=D)}let _=!1;for(let P=0;P<Y;P++){if(I[P]===null){_=!1;continue}let D=J+P,ee=B(I[P]),T=B(x[P]);_?e.lineTo(D,ee):(e.moveTo(D,ee),_=!0),e.lineTo(D,T)}}if(e.stroke(),oe&&e.setLineDash([]),ae.length>0){e.fillStyle=$,e.beginPath();for(let[x,I]of ae)e.moveTo(x+Math.max(1.5,H)*M,I),e.arc(x,I,Math.max(1.5,H)*M,0,Math.PI*2);e.fill()}}function un(e,l,d,c,v,J,E,w,B){let $=v(J),H=He(l,d,c);$<H.top-.5||$>H.bottom+.5||(e.strokeStyle=E,e.lineWidth=1.25*c,B&&e.setLineDash(B.map(M=>M*c)),e.beginPath(),e.moveTo(H.left,$),e.lineTo(H.right,$),e.stroke(),B&&e.setLineDash([]),w&&(e.fillStyle=E,e.textAlign="right",e.textBaseline="middle",e.font=`${10*c}px ui-monospace, Menlo, Consolas, monospace`,e.fillText(w,(d.l-8)*c,$)))}function we(e){let l=Math.abs(e);return l>=1e3?e.toFixed(0):l>=10?e.toFixed(1):l>=1?e.toFixed(2):l>=.001||l===0?e.toFixed(3):e.toExponential(1)}function Ie(e){return(e>0?"+":"")+we(e)}function Re(e){return e<.001?"<0.001":e.toFixed(3)}function Me(e){return new Date(e).toISOString().slice(0,16).replace("T"," ")}function Fe(e){return new Date(e).toISOString().slice(0,10)}var _n="<svg class='abk-logomark' viewBox='0 0 100 100' width='22' height='22' role='img' aria-label='abkit' focusable='false'><rect x='3' y='3' width='94' height='94' rx='26' fill='#6a45c4'/><g fill='none' stroke='#fbf9f3' stroke-width='9' stroke-linecap='round' stroke-linejoin='round'><polyline points='13 50 34 50'/><polyline points='34 50 86 27'/><polyline points='34 50 86 61'/></g><circle cx='86' cy='27' r='7' fill='#fbf9f3'/></svg>";function kn(){let e=document.createElement("div");e.className="abk-brand",e.innerHTML=_n;let l=document.createElement("span");return l.className="abk-wordmark",l.textContent="abkit",e.appendChild(l),e}var n="abk-dashboard",Sn=3,Oe={l:3,r:3,t:5,b:5},jn=864e5,An=1200,Bn=8e3,Pn=600,Dn=2e3,Nn=`name: my_experiment
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
`;function o(e,l,d){let c=document.createElement(e);return l&&(c.className=l),d!==void 0&&(c.textContent=d),c}function h(e,l,d){let c=document.createElement("button");return c.className=e,c.textContent=l,c.type="button",d&&(c.title=d),c}var de=(e,l=we)=>e===null?"\u2014":l(e);function ne(e){return e instanceof Error?e.message||e.name:String(e)}function fn(e){return e instanceof Error&&e.name==="AbortError"}function Hn(e,l){return e===null||l===null?null:(l-e)/jn}var ze=null;function In(e,l){var tn;On(),ze&&ze();let d=[];ze=()=>{for(let t of d)t();d.length=0},l.classList.add(n),l.innerHTML="";let c=o("div","abk-root");l.appendChild(c);let v=(tn=new URLSearchParams(window.location.search).get("token"))!=null?tn:"",J=e.initial_window,E=0,w=null,B=0,$=new Map,H=new Map,M=new Map,oe=!1;function q(t,s={}){let a=new URLSearchParams({...s,token:v});return`${t}?${a.toString()}`}async function Y(t){let s="";try{s=(await t.text()).trim()}catch{s=""}let a=new Error(s||`HTTP ${t.status}`);return a.name=`HTTP${t.status}`,a}async function V(t,s={},a){let i=await fetch(q(t,s),a?{signal:a}:void 0);if(!i.ok)throw await Y(i);return await i.json()}async function te(t,s){let a=await fetch(q(t),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(s)});if(!a.ok)throw await Y(a);return await a.json()}let ae=o("div","abk-header");c.appendChild(ae),ae.appendChild(kn());let x=o("div","abk-h-top");ae.appendChild(x);let I=o("h1","abk-title",`dashboard \xB7 ${e.project}`);x.appendChild(I),x.appendChild(o("span","abk-badge-page",`v${e.version}`)),e.profile!==null&&x.appendChild(o("span","abk-badge-page",`profile: ${e.profile}`));let _=o("div","abk-meta");ae.appendChild(_);function P(){let t=pe.length;_.textContent=[`${t} experiment${t===1?"":"s"}`,`booted ${Me(e.generated_at)} UTC`].join(" \xB7 ")}if(v===""){let t=o("div","abk-warning","\u26A0 this page was opened without its ?token= \u2014 every request will be refused. Reopen the URL `abk dashboard` printed.");ae.appendChild(t)}let D=o("div","abk-warning abk-banner");D.style.display="none",ae.appendChild(D);function ee(t){D.textContent=t,D.style.display=t===""?"none":""}let T=o("div","abk-controls");c.appendChild(T);let Je=o("div","abk-seg");T.appendChild(o("span","abk-ctl-label","sparkline window")),T.appendChild(Je);let Ue=new Map;for(let t of e.window_presets){let s=h("abk-seg-btn",t,`bound the sparkline to the last ${t}`);t===J&&s.classList.add("on"),s.addEventListener("click",()=>{if(t!==J){J=t;for(let[a,i]of Ue)i.classList.toggle("on",a===t);Le(Ce())}}),Ue.set(t,s),Je.appendChild(s)}let We=h("abk-btn abk-btn-ghost","Refresh","re-read every row");We.addEventListener("click",()=>Le(Ce())),T.appendChild(We);let me=h("abk-btn abk-btn-ghost","Reload configs","re-read the project\u2019s experiment YAML from disk");T.appendChild(me);let Ye=h("abk-btn","New experiment","write a new experiment YAML");T.appendChild(Ye);let _e=o("span","abk-chip abk-chip-fill","idle");T.appendChild(_e);let ye=h("abk-chip abk-chip-job","jobs: idle","open the job drawer");ye.addEventListener("click",()=>re.toggleList()),T.appendChild(ye);let be=o("div","abk-list");c.appendChild(be);let pe=e.experiments;function Ve(){if(be.innerHTML="",$.clear(),pe.length===0){be.appendChild(o("div","abk-empty","No experiments in this dashboard\u2019s selection \u2014 restart with `abk dashboard --select \u2026`."));return}let t=new Map;for(let a of pe){let i=t.get(a.dir);i===void 0?t.set(a.dir,[a]):i.push(a)}let s=t.size>1;for(let[a,i]of t){s&&be.appendChild(o("div","abk-group",a===""?"(top level)":a));let b=o("div","abk-table");b.appendChild(wn());for(let g of i){let R=yn(g);$.set(g.name,R),b.appendChild(R.root)}be.appendChild(b)}}P(),Ve();let y=Qe("");y.root.classList.add("abk-create"),y.root.style.display="none",c.insertBefore(y.root,be);let ue=document.createElement("input");ue.className="abk-text",ue.type="text",ue.placeholder="subfolder under experiments/ (optional)",y.root.insertBefore(ue,y.root.firstChild);let he=h("abk-btn abk-btn-run","Create","validate, then write a new file"),Ke=h("abk-btn abk-btn-ghost","Cancel");y.buttons.appendChild(he),y.buttons.appendChild(Ke);function Xe(t){y.clearForce(),he.disabled=!0,y.say(t?"creating (forced)\u2026":"creating\u2026"),te("/api/experiment/create",{text:y.area.value,folder:ue.value.trim()===""?null:ue.value.trim(),force:t}).then(s=>{he.disabled=!1,y.say([`created ${s.path}`,...s.warnings].join(`
`),"ok"),$e(s.experiments),s.in_selection&&(y.root.style.display="none")}).catch(s=>{he.disabled=!1;let a=ne(s);y.say(a,"err"),en(a)&&y.offerForce(()=>Xe(!0))})}he.addEventListener("click",()=>Xe(!1)),Ke.addEventListener("click",()=>{y.root.style.display="none"}),Ye.addEventListener("click",()=>{if(y.root.style.display===""){y.root.style.display="none";return}y.root.style.display="",y.clearForce(),y.area.value.trim()===""&&(y.area.value=Nn),y.say("a new experiments/<name>.yml \u2014 the file is named after `name:`"),y.area.focus()}),me.addEventListener("click",()=>{me.disabled=!0,te("/api/reload",{}).then(t=>{me.disabled=!1,$e(t.experiments),t.warnings.length>0?ee(t.warnings.join(`
`)):ee("")}).catch(t=>{me.disabled=!1,ee(`reload failed: ${ne(t)}`)})});let re=xn();c.appendChild(re.root);function Ce(){return pe.map(t=>t.name)}function $e(t){let s=Ge(pe);pe=t,P(),Ge(t)!==s&&(Ve(),Le(Ce()))}function Ge(t){return JSON.stringify(t)}function qe(){_e.textContent=B>0?`loading ${B}\u2026`:"idle",_e.classList.toggle("abk-chip-busy",B>0)}function Le(t){var R;E+=1;let s=E;w==null||w.abort();let a=new AbortController;w=a;for(let f of t)(R=$.get(f))==null||R.pending();let i=t.slice();B=i.length,qe();let b=async()=>{var f,p;for(;;){if(s!==E)return;let m=i.shift();if(m===void 0)return;try{let L=await V(`/api/stats/${encodeURIComponent(m)}`,{window:J},a.signal);if(s!==E)return;H.set(m,L),(f=$.get(m))==null||f.paint(L)}catch(L){if(fn(L)||s!==E)return;(p=$.get(m))==null||p.paintError(ne(L))}finally{s===E&&(B=Math.max(0,B-1),qe())}}},g=[];for(let f=0;f<Math.min(Sn,i.length);f++)g.push(b());Promise.all(g)}function Ze(t){if(!$.has(t))return;let s=E,a=$.get(t);a==null||a.pending(),V(`/api/stats/${encodeURIComponent(t)}`,{window:J},w==null?void 0:w.signal).then(i=>{s===E&&(H.set(t,i),a==null||a.paint(i))}).catch(i=>{fn(i)||s!==E||a==null||a.paintError(ne(i))})}let ve=0,Se=!1;function Ee(t){Se||(ve&&window.clearTimeout(ve),ve=window.setTimeout(()=>{hn()},t))}async function hn(){try{let t=await V("/api/jobs");if(Se)return;vn(t)}catch{}Ee(oe||re.following()?An:Bn)}function vn(t){oe=t.pipeline_active;let s=t.jobs.filter(i=>i.status==="running");ye.textContent=s.length===0?"jobs: idle":`jobs: ${s.map(i=>{var b;return`${i.kind} ${(b=i.experiment)!=null?b:i.label}`}).join(", ")}`,ye.classList.toggle("abk-chip-busy",s.length>0);for(let i of $.values())i.root.classList.toggle("abk-busy",oe);for(let i of t.jobs){let b=M.get(i.id);M.set(i.id,i.status),b==="running"&&i.status!=="running"&&i.experiment!==null&&Ze(i.experiment)}let a=new Set(t.jobs.map(i=>i.id));for(let i of[...M.keys()])a.has(i)||M.delete(i);re.adoptList(t.jobs)}function xn(){let t=o("div","abk-drawer");t.style.display="none";let s=o("div","abk-drawer-head"),a=o("span","abk-drawer-label","jobs"),i=o("span","abk-drawer-status",""),b=h("abk-btn abk-btn-danger","Stop","SIGTERM, then SIGKILL after 5s"),g=h("abk-btn abk-btn-ghost","Close");s.appendChild(a),s.appendChild(i),s.appendChild(b),s.appendChild(g);let R=o("div","abk-drawer-list"),f=o("pre","abk-drawer-log");t.appendChild(s),t.appendChild(R),t.appendChild(f);let p=null,m=0,L=0,Z=!1;function G(){L&&(window.clearTimeout(L),L=0)}function ie(){t.style.display=""}function S(){G(),p=null,Z=!1,t.style.display="none"}g.addEventListener("click",S),b.addEventListener("click",()=>{let u=p;u!==null&&(b.disabled=!0,te(`/api/job/${encodeURIComponent(u)}/stop`,{}).then(()=>{K()}).catch(C=>{i.textContent=`stop failed: ${ne(C)}`,b.disabled=!1}))});function z(u){if(u.length!==0){for(let C of u)f.appendChild(o("div","abk-log-line",C));for(;f.childElementCount>Dn&&f.firstElementChild!==null;)f.removeChild(f.firstElementChild);f.scrollTop=f.scrollHeight}}function N(u){a.textContent=u.label;let C=[u.status];if(u.returncode!==null&&C.push(`exit ${u.returncode}`),u.truncated&&C.push(`${u.dropped} earlier line(s) discarded \u2014 the buffer is capped`),i.textContent=C.join(" \xB7 "),i.className=`abk-drawer-status abk-job-${u.status}`,b.style.display=u.status==="running"?"":"none",b.disabled=u.status!=="running",z(u.lines),m=u.next_offset,u.url!==null&&u.status==="running"){let U=h("abk-btn abk-btn-ghost","Open cockpit"),Q=u.url;U.addEventListener("click",()=>je(Q)),i.appendChild(U)}}async function K(){let u=p;if(u!==null){G();try{let C=await V(`/api/job/${encodeURIComponent(u)}`,{offset:String(m)});if(p!==u)return;N(C),C.status==="running"&&(L=window.setTimeout(()=>{K()},Pn))}catch(C){if(p!==u)return;i.textContent=`poll failed: ${ne(C)}`}}}return{root:t,follow(u){G(),p=u,m=0,Z=!1,R.style.display="none",f.style.display="",f.textContent="",i.textContent="starting\u2026",ie(),K(),Ee(0)},toggleList(){if(Z){S();return}G(),p=null,Z=!0,a.textContent="jobs",i.textContent="",b.style.display="none",R.style.display="",f.style.display="none",ie(),Ee(0)},adoptList(u){if(Z){if(R.textContent="",u.length===0){R.appendChild(o("div","abk-log-line","no jobs spawned yet"));return}for(let C of u){let U=o("div","abk-job-row");U.appendChild(o("span",`abk-job-${C.status}`,C.status)),U.appendChild(o("span","abk-job-label",C.label));let Q=h("abk-btn abk-btn-ghost","log");Q.addEventListener("click",()=>re.follow(C.id)),U.appendChild(Q),R.appendChild(U)}}},following(){return p!==null},dispose(){G(),p=null}}}function je(t,s){let a=null;try{a=window.open(t,"_blank")}catch{a=null}if(a!==null||s===void 0)return;let i=o("a","abk-link",t);i.href=t,i.target="_blank",i.rel="noopener",s(i)}function Qe(t){let s=o("div","abk-editor"),a=document.createElement("textarea");a.className="abk-yaml",a.spellcheck=!1,a.rows=18,a.placeholder=t,s.appendChild(a);let i=o("div","abk-editor-msg");i.style.display="none",s.appendChild(i);let b=o("div","abk-btn-row");b.style.display="none",s.appendChild(b);let g=o("div","abk-btn-row");s.appendChild(g);function R(m,L="info"){i.textContent=m,i.className=`abk-editor-msg abk-editor-msg-${L}`,i.style.display=m===""?"none":""}function f(){b.innerHTML="",b.style.display="none"}function p(m){f();let L=h("abk-btn abk-btn-danger","Save anyway","write the file even though `abk run` will refuse it");L.addEventListener("click",()=>{f(),m()}),b.appendChild(L),b.style.display=""}return{root:s,area:a,buttons:g,say:R,offerForce:p,clearForce:f}}function en(t){return t.includes("not valid for this project")}function gn(t,s){let a=Qe("");a.root.style.display="none";let i=null,b=t.name,g=h("abk-btn abk-btn-run","Save","validate, archive, then write"),R=h("abk-btn abk-btn-ghost","Revert","discard the edits in this box"),f=h("abk-btn abk-btn-danger","Delete\u2026","archive the YAML, then remove it");a.buttons.appendChild(g),a.buttons.appendChild(R),a.buttons.appendChild(f);let p=o("div","abk-confirm");p.style.display="none",p.appendChild(o("div","abk-confirm-text","Delete archives the YAML under .history/ and removes the file. The experiment\u2019s persisted rows are NOT deleted \u2014 `abk clean --orphaned-experiments` prunes those."));let m=o("div","abk-btn-row"),L=h("abk-btn abk-btn-danger","Delete anyway"),Z=h("abk-btn abk-btn-ghost","Cancel");m.appendChild(L),m.appendChild(Z),p.appendChild(m),a.root.appendChild(p);function G(){a.clearForce(),a.say("loading\u2026"),g.disabled=!0,V(`/api/experiment-source/${encodeURIComponent(b)}`).then(S=>{i=S,a.area.value=S.yaml_text,S.editable?(g.disabled=!1,a.say(S.path)):a.say(`${S.path} is too large to edit here \u2014 it was truncated for display; open it in your editor`,"err")}).catch(S=>{i=null,a.area.value="",a.say(`could not read the YAML: ${ne(S)}`,"err")})}function ie(S){if(i===null)return;a.clearForce(),g.disabled=!0,a.say(S?"saving (forced)\u2026":"saving\u2026");let z=a.area.value;te("/api/experiment/save",{select:b,text:z,digest:i.digest,force:S}).then(N=>{var K;g.disabled=!1,b=N.name,i={name:N.name,path:N.path,yaml_text:z,truncated:!1,digest:N.digest,editable:!0},a.say([`saved ${N.path} \xB7 previous archived at ${(K=N.archived)!=null?K:"\u2014"}`,...N.warnings].join(`
`),N.warnings.length>0?"err":"ok"),$e(N.experiments),N.renamed_from===null&&Ze(N.name)}).catch(N=>{g.disabled=!1;let K=ne(N);a.say(K,"err"),en(K)&&a.offerForce(()=>ie(!0))})}return g.addEventListener("click",()=>ie(!1)),R.addEventListener("click",()=>G()),f.addEventListener("click",()=>{p.style.display=""}),Z.addEventListener("click",()=>{p.style.display="none"}),L.addEventListener("click",()=>{var S;p.style.display="none",a.say("deleting\u2026"),te("/api/experiment/delete",{select:b,digest:(S=i==null?void 0:i.digest)!=null?S:null}).then(z=>{ee([`deleted ${z.path} \u2014 archived at ${z.archived}`,...z.warnings].join(`
`)),$e(z.experiments)}).catch(z=>{a.say(`delete refused: ${ne(z)}`,"err")})}),s.addEventListener("click",()=>{if(a.root.style.display===""){a.root.style.display="none";return}a.root.style.display="",G()}),a.root}function wn(){let t=o("div","abk-row abk-head");for(let[s,a]of[["abk-cell-disclose",""],["abk-cell-name","experiment"],["abk-cell-verdict","verdict"],["abk-cell-effect","effect (CI)"],["abk-cell-p","p / \u03B1"],["abk-cell-time","elapsed"],["abk-cell-spark","effect over the window"],["abk-cell-actions",""]])t.appendChild(o("div",`abk-cell ${s}`,a));return t}function yn(t){let s=o("div","abk-row");s.setAttribute("data-abk-experiment",t.name);let a=o("div","abk-row-main");s.appendChild(a);let i=o("div","abk-cell abk-cell-disclose"),b=h("abk-disclose","\u25B8","show verdicts, warnings and actions");b.setAttribute("aria-expanded","false"),i.appendChild(b),a.appendChild(i);let g=o("div","abk-cell abk-cell-name");g.appendChild(o("div","abk-name",t.name));let R=[t.file];if(t.status!==null&&R.push(t.status),t.main_metric!==null&&R.push(t.main_metric),g.appendChild(o("div","abk-sub",R.join(" \xB7 "))),t.tags.length>0){let r=o("div","abk-tags");for(let j of t.tags)r.appendChild(o("span","abk-tag",j));g.appendChild(r)}a.appendChild(g);let f=o("div","abk-cell abk-cell-verdict"),p=o("span","abk-chip abk-v-pending","pending");f.appendChild(p);let m=o("span","abk-badges");f.appendChild(m),a.appendChild(f);let L=o("div","abk-cell abk-cell-effect","\u2014");a.appendChild(L);let Z=o("div","abk-cell abk-cell-p","\u2014");a.appendChild(Z);let G=o("div","abk-cell abk-cell-time","\u2014");a.appendChild(G);let ie=o("div","abk-cell abk-cell-spark"),S=document.createElement("canvas");S.className="abk-spark",ie.appendChild(S),a.appendChild(ie);let z=o("div","abk-cell abk-cell-actions"),N=h("abk-btn","Open","the full report for this experiment"),K=h("abk-btn","Explore","launch the tuning cockpit (may take a while)"),u=h("abk-btn abk-btn-run","Run","spawn `abk run` for this experiment");z.appendChild(N),z.appendChild(K),z.appendChild(u),a.appendChild(z);let C=o("div","abk-row-note");C.style.display="none",s.appendChild(C);let U=o("div","abk-row-msg");U.style.display="none",s.appendChild(U);let Q=o("div","abk-detail");Q.style.display="none",s.appendChild(Q);let ce=!1,Ae=[];function le(r,j="info"){U.textContent=r,U.className=`abk-row-msg${j==="err"?" abk-row-msg-err":""}`,U.style.display=r===""?"none":""}function ge(r,j){C.textContent=r,C.className=`abk-row-note ${j}`.trim(),C.style.display=r===""?"none":""}N.addEventListener("click",()=>{let r=q(`/experiment/${encodeURIComponent(t.name)}`);je(r,j=>{le("the browser blocked the tab \u2014 ","info"),U.appendChild(j)})}),K.addEventListener("click",()=>{K.disabled=!0;let r=()=>{K.disabled=!1};le("starting the explore cockpit \u2014 this can take up to 90 s\u2026"),te("/api/explore",{select:t.name}).then(j=>{r(),le(""),re.follow(j.job_id),je(j.url,W=>{le("cockpit ready \u2014 ","info"),U.appendChild(W)})}).catch(j=>{r(),le(`explore failed: ${ne(j)}`,"err")})});function Te(r,j,W){le(`starting ${W}\u2026`),te(r,j).then(X=>{le(""),re.follow(X.job_id)}).catch(X=>{le(`${W} refused: ${ne(X)}`,"err")})}u.addEventListener("click",()=>Te("/api/run",{select:t.name},"run"));let se=o("div","abk-readout"),an=!1;function on(){var W,X;se.textContent="";let r=H.get(t.name);if(r===void 0)return;r.error!==null&&se.appendChild(o("div","abk-warning",`\u26A0 ${r.error}`));for(let F of r.warnings)se.appendChild(o("div","abk-warning",`\u26A0 ${F}`));for(let F of r.caveats)se.appendChild(o("div","abk-caveat",`! ${F}`));if(r.rationale.length>0){let F=o("div","abk-block");F.appendChild(o("div","abk-block-title","why this verdict"));for(let O of r.rationale)F.appendChild(o("div","abk-rationale",O));se.appendChild(F)}if(r.verdicts.length>0){let F=o("div","abk-block");F.appendChild(o("div","abk-block-title","per arm pair"));for(let O of r.verdicts){let k=o("div","abk-pair");k.appendChild(o("span",`abk-v-word abk-v-${O.verdict.toLowerCase()}`,O.verdict)),k.appendChild(o("span","abk-pair-name",`${O.metric}: ${O.pair.c} vs ${O.pair.t}`)),((W=O.role)!=null?W:"vs_control")!=="vs_control"&&k.appendChild(o("span","abk-pair-role","arm vs arm")),k.appendChild(o("span","abk-pair-effect",de(O.effect,Ie))),O.guardrail_regressed&&k.appendChild(o("span","abk-badge-guardrail","guardrail regressed"));for(let A of O.caveats)k.appendChild(o("div","abk-caveat",`! ${A}`));F.appendChild(k)}se.appendChild(F)}let j=[`SRM p ${de(r.srm_pvalue,Re)}`,`last look ${r.last_end_ts===null?"\u2014":`${Me(r.last_end_ts)} UTC`}`,`timezone ${(X=r.timezone)!=null?X:"\u2014"}`,r.locked?"the pipeline lock is HELD":"unlocked"];se.appendChild(o("div","abk-facts",j.join(" \xB7 ")))}function $n(){Q.appendChild(se);let r=o("div","abk-block");r.appendChild(o("div","abk-block-title","run one comparison"));let j=o("div","abk-btn-row");for(let fe of t.comparisons){let Rn=fe.is_main_metric?fe.metric:`${fe.metric} (secondary)`,sn=h("abk-btn",`Run ${Rn}`,`abk run --metric ${fe.metric}`);sn.addEventListener("click",()=>Te("/api/run",{select:t.name,metric:fe.metric},`run ${fe.metric}`)),j.appendChild(sn)}r.appendChild(j),Q.appendChild(r);let W=o("div","abk-block");W.appendChild(o("div","abk-block-title","maintenance"));let X=o("div","abk-btn-row"),F=h("abk-btn","Unlock","release a stale pipeline lock (`abk unlock`)");F.addEventListener("click",()=>Te("/api/unlock",{select:t.name},"unlock")),X.appendChild(F);let O=h("abk-btn abk-btn-danger","Clean\u2026","delete orphaned rows (`abk clean --execute`)");X.appendChild(O);let k=h("abk-btn abk-btn-ghost","Edit YAML",t.file);X.appendChild(k),W.appendChild(X);let A=o("div","abk-confirm");A.style.display="none",A.appendChild(o("div","abk-confirm-text","Clean runs `abk clean --select \u2026 --execute`: it DELETES orphaned _ab_results / _ab_unit_state rows for this experiment. There is no undo."));let ke=o("div","abk-btn-row"),rn=h("abk-btn abk-btn-danger","Clean anyway"),ln=h("abk-btn abk-btn-ghost","Cancel");rn.addEventListener("click",()=>{A.style.display="none",Te("/api/clean",{select:t.name},"clean")}),ln.addEventListener("click",()=>{A.style.display="none"}),ke.appendChild(rn),ke.appendChild(ln),A.appendChild(ke),O.addEventListener("click",()=>{A.style.display=""}),W.appendChild(A),Q.appendChild(W),Q.appendChild(gn(t,k))}b.addEventListener("click",()=>{ce=!ce,b.textContent=ce?"\u25BE":"\u25B8",b.setAttribute("aria-expanded",ce?"true":"false"),Q.style.display=ce?"":"none",ce&&(an||(an=!0,$n()),on())});function Ln(){p.className="abk-chip abk-v-pending",p.textContent="pending",m.textContent="",L.textContent="\u2014",Z.textContent="\u2014",G.textContent="\u2014",ge("",""),Ae=[],Be()}function En(r){var F,O;if(m.textContent="",r.error!==null)p.className="abk-chip abk-v-error",p.textContent="error",ge(r.error,"abk-v-error");else if(r.verdict===null)p.className="abk-chip abk-v-none",p.textContent="no data",ge("no computed results yet \u2014 press Run","");else{let k=["abk-chip",`abk-v-${r.verdict.toLowerCase()}`],A="";r.srm_flag?(k.push("abk-srm-fail"),A=`SRM FAILED (p ${de(r.srm_pvalue,Re)}) \u2014 effects untrustworthy`):r.insufficient?(k.push("abk-insufficient"),A="insufficient data at the latest look \u2014 inference withheld"):!r.is_horizon&&r.verdict==="INCONCLUSIVE"&&(k.push("abk-prehorizon"),A="pre-horizon: fixed CIs are not peeking-valid, so a verdict is withheld"),p.className=k.join(" "),p.textContent=r.verdict,ge(A,A===""?"":k.slice(1).join(" "))}if(r.verdicts.some(k=>k.role==="treatment_pair")&&r.leader!==null){let k=o("span","abk-badge-leader",`\u2192 ${r.leader}`);k.title=(O=(F=r.rollups.find(A=>A.leader===r.leader))==null?void 0:F.rationale.join(`
`))!=null?O:"",m.appendChild(k)}if(r.leaders_agree===!1){let k=o("span","abk-badge-caveat","leaders split");k.title=r.rollups.map(A=>{var ke;return`${A.metric}: ${(ke=A.leader)!=null?ke:"no leader"}`}).join(`
`),m.appendChild(k)}if(r.guardrail_regressed&&m.appendChild(o("span","abk-badge-guardrail","guardrail")),r.caveats.length>0){let k=o("span","abk-badge-caveat",`\u26A0 ${r.caveats.length}`);k.title=r.caveats.join(`
`),m.appendChild(k)}if(r.weekly_cycle_pct!==null){let k=o("span","abk-badge-caveat",`${Math.round(r.weekly_cycle_pct*100)}% wk`);k.title="decided before one full weekly cycle",m.appendChild(k)}if(r.locked){let k=o("span","abk-badge-lock","locked");k.title="the pipeline lock is held \u2014 Run would refuse",m.appendChild(k)}L.textContent=`${de(r.effect,Ie)} [${de(r.ci[0])}, ${de(r.ci[1])}]`,Z.textContent=`${de(r.pvalue,Re)} / ${de(r.alpha)}`;let W=Hn(r.start_ts,r.horizon_ts),X=r.elapsed_days===null?"\u2014":`${we(r.elapsed_days)}d`;G.textContent=W===null?X:`${X} / ${we(W)}d${r.is_horizon?" \u2713":""}`,G.title=r.last_end_ts===null?"no computed look yet":`latest look ${Me(r.last_end_ts)} UTC`,Ae=r.spark,Be(),ce&&on()}function Tn(r){p.className="abk-chip abk-v-error",p.textContent="error",m.textContent="",ge(r,"abk-v-error")}function Be(){Fn(S,Ae)}return{root:s,pending:Ln,paint:En,paintError:Tn,redraw:Be}}let Cn=window.setTimeout(()=>{e.experiments.length>0&&Le(Ce()),Ee(0)},0);d.push(()=>window.clearTimeout(Cn)),d.push(()=>{Se=!0,ve&&window.clearTimeout(ve),re.dispose(),w==null||w.abort(),E+=1});let xe=0,nn=()=>{xe&&window.clearTimeout(xe),xe=window.setTimeout(()=>{for(let t of $.values())t.redraw()},120)};window.addEventListener("resize",nn),d.push(()=>{window.removeEventListener("resize",nn),xe&&window.clearTimeout(xe)})}function Fn(e,l){let d=null;try{d=e.getContext("2d")}catch{d=null}if(d===null){e.classList.add("abk-spark-blank");return}let c=cn(e);if(d.clearRect(0,0,e.width,e.height),e.width===0||e.height===0||l.length===0){e.title="";return}let v=l.map(([V])=>V),J=l.map(([,V])=>V===null?NaN:V),E=J.filter(V=>Number.isFinite(V)),w=v[0],B=v[v.length-1]===w?w+1:v[v.length-1],$=Math.min(0,...E),H=Math.max(0,...E),M=(H-$||Math.abs(H)||1)*.15,oe={xmin:w,xmax:B,vmin:$-M,vmax:H+M},q=bn(e,Oe,oe,c),Y=He(e,Oe,c);un(d,e,Oe,c,q.py,0,dn(Ne("--abk-chart-grid"),.45),"",[3,3]),pn(d,v,J,w,B,Y.left,Y.right-Y.left,q.px,q.py,Ne("--abk-series-1"),1.4,c),e.title=`${l.length} bucket(s), ${Fe(w)} \u2192 ${Fe(B)} UTC`}var mn=!1;function On(){if(mn)return;mn=!0;let l=`
:where(:root){${Object.entries(De).map(([c,v])=>`${c}:${v}`).join(";")};
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
`,d=document.createElement("style");d.setAttribute("data-abk-dashboard",""),d.textContent=l,document.head.appendChild(d)}window.__ABK_DASHBOARD__={render:In};})();
