"use strict";(()=>{var De={"--abk-page":"#f5f1e8","--abk-card":"#fbf9f3","--abk-ink":"#1b1916","--abk-ink-2":"#6e675b","--abk-muted":"#9a9384","--abk-border":"#e6e0d4","--abk-chart-bg":"#211e1a","--abk-chart-border":"#332f29","--abk-chart-ink":"#c9c2b4","--abk-chart-grid":"#9a9384","--abk-series-1":"#c9a6f0","--abk-series-2":"#8e76e0","--abk-st-good":"#1e9e6a","--abk-st-warn":"#e0a23b","--abk-st-serious":"#d6453d","--abk-st-critical":"#b23a6b","--abk-good-text":"#15774e","--abk-explore-accent":"#6a45c4"};function Ne(e){return getComputedStyle(document.documentElement).getPropertyValue(e).trim()||De[e]||"#888"}function Mn(e){let i=e.replace("#","").trim();i.length===3&&(i=i[0]+i[0]+i[1]+i[1]+i[2]+i[2]);let d=parseInt(i,16);return i.length!==6||Number.isNaN(d)?[137,135,129]:[d>>16&255,d>>8&255,d&255]}function dn(e,i){let[d,c,v]=Mn(e);return`rgba(${d},${c},${v},${i})`}function cn(e){let i=Math.max(1,window.devicePixelRatio||1),d=e.clientWidth||e.offsetWidth||0,c=e.clientHeight||e.offsetHeight||0;return e.width=Math.round(d*i),e.height=Math.round(c*i),i}function bn(e,i,d,c){let v=()=>e.width-(i.l+i.r)*c,O=()=>e.height-(i.t+i.b)*c,T=()=>d.xmax-d.xmin||1,w=()=>d.vmax-d.vmin||1;return{px:_=>i.l*c+(_-d.xmin)/T()*v(),py:_=>e.height-i.b*c-(_-d.vmin)/w()*O(),xAt:_=>d.xmin+(_-i.l*c)/(v()||1)*T(),plotW:v,plotH:O}}function He(e,i,d){return{left:i.l*d,top:i.t*d,right:e.width-i.r*d,bottom:e.height-i.b*d}}var Pe=Number.isFinite;function pn(e,i,d,c,v,O,T,w,A,$,H,_,ne){let K=i.length,U=Math.max(1,Math.round(T)),W=v-c||1,Q=0;for(let x=0;x<K;x++){let I=d[x];!Pe(I)||i[x]<c||i[x]>v||Q++}e.strokeStyle=$,e.lineWidth=H*_,e.lineJoin="round",ne&&e.setLineDash(ne.map(x=>x*_)),e.beginPath();let ee=[];if(Q<=U){let x=S=>S>=0&&S<K&&Pe(d[S])&&i[S]>=c&&i[S]<=v,I=!1;for(let S=0;S<K;S++){if(!x(S)){I=!1;continue}let P=w(i[S]),D=A(d[S]);if(!x(S-1)&&!x(S+1)){ee.push([P,D]),I=!1;continue}I?e.lineTo(P,D):(e.moveTo(P,D),I=!0)}}else{let x=new Array(U).fill(null),I=new Array(U).fill(null);for(let P=0;P<K;P++){let D=d[P],q=i[P];if(!Pe(D)||q<c||q>v)continue;let R=Math.floor((q-c)/W*(U-1));R=R<0?0:R>U-1?U-1:R,(x[R]===null||D<x[R])&&(x[R]=D),(I[R]===null||D>I[R])&&(I[R]=D)}let S=!1;for(let P=0;P<U;P++){if(I[P]===null){S=!1;continue}let D=O+P,q=A(I[P]),R=A(x[P]);S?e.lineTo(D,q):(e.moveTo(D,q),S=!0),e.lineTo(D,R)}}if(e.stroke(),ne&&e.setLineDash([]),ee.length>0){e.fillStyle=$,e.beginPath();for(let[x,I]of ee)e.moveTo(x+Math.max(1.5,H)*_,I),e.arc(x,I,Math.max(1.5,H)*_,0,Math.PI*2);e.fill()}}function un(e,i,d,c,v,O,T,w,A){let $=v(O),H=He(i,d,c);$<H.top-.5||$>H.bottom+.5||(e.strokeStyle=T,e.lineWidth=1.25*c,A&&e.setLineDash(A.map(_=>_*c)),e.beginPath(),e.moveTo(H.left,$),e.lineTo(H.right,$),e.stroke(),A&&e.setLineDash([]),w&&(e.fillStyle=T,e.textAlign="right",e.textBaseline="middle",e.font=`${10*c}px ui-monospace, Menlo, Consolas, monospace`,e.fillText(w,(d.l-8)*c,$)))}function xe(e){let i=Math.abs(e);return i>=1e3?e.toFixed(0):i>=10?e.toFixed(1):i>=1?e.toFixed(2):i>=.001||i===0?e.toFixed(3):e.toExponential(1)}function Ie(e){return(e>0?"+":"")+xe(e)}function Te(e){return e<.001?"<0.001":e.toFixed(3)}function Re(e){return new Date(e).toISOString().slice(0,16).replace("T"," ")}function Fe(e){return new Date(e).toISOString().slice(0,10)}var _n="<svg class='abk-logomark' viewBox='0 0 100 100' width='22' height='22' role='img' aria-label='abkit' focusable='false'><rect x='3' y='3' width='94' height='94' rx='26' fill='#6a45c4'/><g fill='none' stroke='#fbf9f3' stroke-width='9' stroke-linecap='round' stroke-linejoin='round'><polyline points='13 50 34 50'/><polyline points='34 50 86 27'/><polyline points='34 50 86 61'/></g><circle cx='86' cy='27' r='7' fill='#fbf9f3'/></svg>";function kn(){let e=document.createElement("div");e.className="abk-brand",e.innerHTML=_n;let i=document.createElement("span");return i.className="abk-wordmark",i.textContent="abkit",e.appendChild(i),e}var n="abk-dashboard",Sn=3,Oe={l:3,r:3,t:5,b:5},jn=864e5,An=1200,Bn=8e3,Pn=600,Dn=2e3,Nn=`name: my_experiment
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
`;function o(e,i,d){let c=document.createElement(e);return i&&(c.className=i),d!==void 0&&(c.textContent=d),c}function h(e,i,d){let c=document.createElement("button");return c.className=e,c.textContent=i,c.type="button",d&&(c.title=d),c}var le=(e,i=xe)=>e===null?"\u2014":i(e);function Z(e){return e instanceof Error?e.message||e.name:String(e)}function fn(e){return e instanceof Error&&e.name==="AbortError"}function Hn(e,i){return e===null||i===null?null:(i-e)/jn}var ze=null;function In(e,i){var tn;On(),ze&&ze();let d=[];ze=()=>{for(let t of d)t();d.length=0},i.classList.add(n),i.innerHTML="";let c=o("div","abk-root");i.appendChild(c);let v=(tn=new URLSearchParams(window.location.search).get("token"))!=null?tn:"",O=e.initial_window,T=0,w=null,A=0,$=new Map,H=new Map,_=new Map,ne=!1;function K(t,s={}){let a=new URLSearchParams({...s,token:v});return`${t}?${a.toString()}`}async function U(t){let s="";try{s=(await t.text()).trim()}catch{s=""}let a=new Error(s||`HTTP ${t.status}`);return a.name=`HTTP${t.status}`,a}async function W(t,s={},a){let r=await fetch(K(t,s),a?{signal:a}:void 0);if(!r.ok)throw await U(r);return await r.json()}async function Q(t,s){let a=await fetch(K(t),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(s)});if(!a.ok)throw await U(a);return await a.json()}let ee=o("div","abk-header");c.appendChild(ee),ee.appendChild(kn());let x=o("div","abk-h-top");ee.appendChild(x);let I=o("h1","abk-title",`dashboard \xB7 ${e.project}`);x.appendChild(I),x.appendChild(o("span","abk-badge-page",`v${e.version}`)),e.profile!==null&&x.appendChild(o("span","abk-badge-page",`profile: ${e.profile}`));let S=o("div","abk-meta");ee.appendChild(S);function P(){let t=be.length;S.textContent=[`${t} experiment${t===1?"":"s"}`,`booted ${Re(e.generated_at)} UTC`].join(" \xB7 ")}if(v===""){let t=o("div","abk-warning","\u26A0 this page was opened without its ?token= \u2014 every request will be refused. Reopen the URL `abk dashboard` printed.");ee.appendChild(t)}let D=o("div","abk-warning abk-banner");D.style.display="none",ee.appendChild(D);function q(t){D.textContent=t,D.style.display=t===""?"none":""}let R=o("div","abk-controls");c.appendChild(R);let Je=o("div","abk-seg");R.appendChild(o("span","abk-ctl-label","sparkline window")),R.appendChild(Je);let Ue=new Map;for(let t of e.window_presets){let s=h("abk-seg-btn",t,`bound the sparkline to the last ${t}`);t===O&&s.classList.add("on"),s.addEventListener("click",()=>{if(t!==O){O=t;for(let[a,r]of Ue)r.classList.toggle("on",a===t);Ce(we())}}),Ue.set(t,s),Je.appendChild(s)}let We=h("abk-btn abk-btn-ghost","Refresh","re-read every row");We.addEventListener("click",()=>Ce(we())),R.appendChild(We);let ke=h("abk-btn abk-btn-ghost","Reload configs","re-read the project\u2019s experiment YAML from disk");R.appendChild(ke);let Ye=h("abk-btn","New experiment","write a new experiment YAML");R.appendChild(Ye);let Me=o("span","abk-chip abk-chip-fill","idle");R.appendChild(Me);let ge=h("abk-chip abk-chip-job","jobs: idle","open the job drawer");ge.addEventListener("click",()=>ae.toggleList()),R.appendChild(ge);let ce=o("div","abk-list");c.appendChild(ce);let be=e.experiments;function Ve(){if(ce.innerHTML="",$.clear(),be.length===0){ce.appendChild(o("div","abk-empty","No experiments in this dashboard\u2019s selection \u2014 restart with `abk dashboard --select \u2026`."));return}let t=new Map;for(let a of be){let r=t.get(a.dir);r===void 0?t.set(a.dir,[a]):r.push(a)}let s=t.size>1;for(let[a,r]of t){s&&ce.appendChild(o("div","abk-group",a===""?"(top level)":a));let b=o("div","abk-table");b.appendChild(wn());for(let g of r){let M=yn(g);$.set(g.name,M),b.appendChild(M.root)}ce.appendChild(b)}}P(),Ve();let y=Qe("");y.root.classList.add("abk-create"),y.root.style.display="none",c.insertBefore(y.root,ce);let pe=document.createElement("input");pe.className="abk-text",pe.type="text",pe.placeholder="subfolder under experiments/ (optional)",y.root.insertBefore(pe,y.root.firstChild);let fe=h("abk-btn abk-btn-run","Create","validate, then write a new file"),Ke=h("abk-btn abk-btn-ghost","Cancel");y.buttons.appendChild(fe),y.buttons.appendChild(Ke);function Xe(t){y.clearForce(),fe.disabled=!0,y.say(t?"creating (forced)\u2026":"creating\u2026"),Q("/api/experiment/create",{text:y.area.value,folder:pe.value.trim()===""?null:pe.value.trim(),force:t}).then(s=>{fe.disabled=!1,y.say([`created ${s.path}`,...s.warnings].join(`
`),"ok"),ye(s.experiments),s.in_selection&&(y.root.style.display="none")}).catch(s=>{fe.disabled=!1;let a=Z(s);y.say(a,"err"),en(a)&&y.offerForce(()=>Xe(!0))})}fe.addEventListener("click",()=>Xe(!1)),Ke.addEventListener("click",()=>{y.root.style.display="none"}),Ye.addEventListener("click",()=>{if(y.root.style.display===""){y.root.style.display="none";return}y.root.style.display="",y.clearForce(),y.area.value.trim()===""&&(y.area.value=Nn),y.say("a new experiments/<name>.yml \u2014 the file is named after `name:`"),y.area.focus()}),ke.addEventListener("click",()=>{ke.disabled=!0,Q("/api/reload",{}).then(t=>{ke.disabled=!1,ye(t.experiments),t.warnings.length>0?q(t.warnings.join(`
`)):q("")}).catch(t=>{ke.disabled=!1,q(`reload failed: ${Z(t)}`)})});let ae=xn();c.appendChild(ae.root);function we(){return be.map(t=>t.name)}function ye(t){let s=Ge(be);be=t,P(),Ge(t)!==s&&(Ve(),Ce(we()))}function Ge(t){return JSON.stringify(t)}function qe(){Me.textContent=A>0?`loading ${A}\u2026`:"idle",Me.classList.toggle("abk-chip-busy",A>0)}function Ce(t){var M;T+=1;let s=T;w==null||w.abort();let a=new AbortController;w=a;for(let f of t)(M=$.get(f))==null||M.pending();let r=t.slice();A=r.length,qe();let b=async()=>{var f,u;for(;;){if(s!==T)return;let m=r.shift();if(m===void 0)return;try{let L=await W(`/api/stats/${encodeURIComponent(m)}`,{window:O},a.signal);if(s!==T)return;H.set(m,L),(f=$.get(m))==null||f.paint(L)}catch(L){if(fn(L)||s!==T)return;(u=$.get(m))==null||u.paintError(Z(L))}finally{s===T&&(A=Math.max(0,A-1),qe())}}},g=[];for(let f=0;f<Math.min(Sn,r.length);f++)g.push(b());Promise.all(g)}function Ze(t){if(!$.has(t))return;let s=T,a=$.get(t);a==null||a.pending(),W(`/api/stats/${encodeURIComponent(t)}`,{window:O},w==null?void 0:w.signal).then(r=>{s===T&&(H.set(t,r),a==null||a.paint(r))}).catch(r=>{fn(r)||s!==T||a==null||a.paintError(Z(r))})}let me=0,_e=!1;function $e(t){_e||(me&&window.clearTimeout(me),me=window.setTimeout(()=>{hn()},t))}async function hn(){try{let t=await W("/api/jobs");if(_e)return;vn(t)}catch{}$e(ne||ae.following()?An:Bn)}function vn(t){ne=t.pipeline_active;let s=t.jobs.filter(r=>r.status==="running");ge.textContent=s.length===0?"jobs: idle":`jobs: ${s.map(r=>{var b;return`${r.kind} ${(b=r.experiment)!=null?b:r.label}`}).join(", ")}`,ge.classList.toggle("abk-chip-busy",s.length>0);for(let r of $.values())r.root.classList.toggle("abk-busy",ne);for(let r of t.jobs){let b=_.get(r.id);_.set(r.id,r.status),b==="running"&&r.status!=="running"&&r.experiment!==null&&Ze(r.experiment)}let a=new Set(t.jobs.map(r=>r.id));for(let r of[..._.keys()])a.has(r)||_.delete(r);ae.adoptList(t.jobs)}function xn(){let t=o("div","abk-drawer");t.style.display="none";let s=o("div","abk-drawer-head"),a=o("span","abk-drawer-label","jobs"),r=o("span","abk-drawer-status",""),b=h("abk-btn abk-btn-danger","Stop","SIGTERM, then SIGKILL after 5s"),g=h("abk-btn abk-btn-ghost","Close");s.appendChild(a),s.appendChild(r),s.appendChild(b),s.appendChild(g);let M=o("div","abk-drawer-list"),f=o("pre","abk-drawer-log");t.appendChild(s),t.appendChild(M),t.appendChild(f);let u=null,m=0,L=0,X=!1;function V(){L&&(window.clearTimeout(L),L=0)}function oe(){t.style.display=""}function j(){V(),u=null,X=!1,t.style.display="none"}g.addEventListener("click",j),b.addEventListener("click",()=>{let k=u;k!==null&&(b.disabled=!0,Q(`/api/job/${encodeURIComponent(k)}/stop`,{}).then(()=>{Y()}).catch(C=>{r.textContent=`stop failed: ${Z(C)}`,b.disabled=!1}))});function F(k){if(k.length!==0){for(let C of k)f.appendChild(o("div","abk-log-line",C));for(;f.childElementCount>Dn&&f.firstElementChild!==null;)f.removeChild(f.firstElementChild);f.scrollTop=f.scrollHeight}}function N(k){a.textContent=k.label;let C=[k.status];if(k.returncode!==null&&C.push(`exit ${k.returncode}`),k.truncated&&C.push(`${k.dropped} earlier line(s) discarded \u2014 the buffer is capped`),r.textContent=C.join(" \xB7 "),r.className=`abk-drawer-status abk-job-${k.status}`,b.style.display=k.status==="running"?"":"none",b.disabled=k.status!=="running",F(k.lines),m=k.next_offset,k.url!==null&&k.status==="running"){let z=h("abk-btn abk-btn-ghost","Open cockpit"),G=k.url;z.addEventListener("click",()=>Se(G)),r.appendChild(z)}}async function Y(){let k=u;if(k!==null){V();try{let C=await W(`/api/job/${encodeURIComponent(k)}`,{offset:String(m)});if(u!==k)return;N(C),C.status==="running"&&(L=window.setTimeout(()=>{Y()},Pn))}catch(C){if(u!==k)return;r.textContent=`poll failed: ${Z(C)}`}}}return{root:t,follow(k){V(),u=k,m=0,X=!1,M.style.display="none",f.style.display="",f.textContent="",r.textContent="starting\u2026",oe(),Y(),$e(0)},toggleList(){if(X){j();return}V(),u=null,X=!0,a.textContent="jobs",r.textContent="",b.style.display="none",M.style.display="",f.style.display="none",oe(),$e(0)},adoptList(k){if(X){if(M.textContent="",k.length===0){M.appendChild(o("div","abk-log-line","no jobs spawned yet"));return}for(let C of k){let z=o("div","abk-job-row");z.appendChild(o("span",`abk-job-${C.status}`,C.status)),z.appendChild(o("span","abk-job-label",C.label));let G=h("abk-btn abk-btn-ghost","log");G.addEventListener("click",()=>ae.follow(C.id)),z.appendChild(G),M.appendChild(z)}}},following(){return u!==null},dispose(){V(),u=null}}}function Se(t,s){let a=null;try{a=window.open(t,"_blank")}catch{a=null}if(a!==null||s===void 0)return;let r=o("a","abk-link",t);r.href=t,r.target="_blank",r.rel="noopener",s(r)}function Qe(t){let s=o("div","abk-editor"),a=document.createElement("textarea");a.className="abk-yaml",a.spellcheck=!1,a.rows=18,a.placeholder=t,s.appendChild(a);let r=o("div","abk-editor-msg");r.style.display="none",s.appendChild(r);let b=o("div","abk-btn-row");b.style.display="none",s.appendChild(b);let g=o("div","abk-btn-row");s.appendChild(g);function M(m,L="info"){r.textContent=m,r.className=`abk-editor-msg abk-editor-msg-${L}`,r.style.display=m===""?"none":""}function f(){b.innerHTML="",b.style.display="none"}function u(m){f();let L=h("abk-btn abk-btn-danger","Save anyway","write the file even though `abk run` will refuse it");L.addEventListener("click",()=>{f(),m()}),b.appendChild(L),b.style.display=""}return{root:s,area:a,buttons:g,say:M,offerForce:u,clearForce:f}}function en(t){return t.includes("not valid for this project")}function gn(t,s){let a=Qe("");a.root.style.display="none";let r=null,b=t.name,g=h("abk-btn abk-btn-run","Save","validate, archive, then write"),M=h("abk-btn abk-btn-ghost","Revert","discard the edits in this box"),f=h("abk-btn abk-btn-danger","Delete\u2026","archive the YAML, then remove it");a.buttons.appendChild(g),a.buttons.appendChild(M),a.buttons.appendChild(f);let u=o("div","abk-confirm");u.style.display="none",u.appendChild(o("div","abk-confirm-text","Delete archives the YAML under .history/ and removes the file. The experiment\u2019s persisted rows are NOT deleted \u2014 `abk clean --orphaned-experiments` prunes those."));let m=o("div","abk-btn-row"),L=h("abk-btn abk-btn-danger","Delete anyway"),X=h("abk-btn abk-btn-ghost","Cancel");m.appendChild(L),m.appendChild(X),u.appendChild(m),a.root.appendChild(u);function V(){a.clearForce(),a.say("loading\u2026"),g.disabled=!0,W(`/api/experiment-source/${encodeURIComponent(b)}`).then(j=>{r=j,a.area.value=j.yaml_text,j.editable?(g.disabled=!1,a.say(j.path)):a.say(`${j.path} is too large to edit here \u2014 it was truncated for display; open it in your editor`,"err")}).catch(j=>{r=null,a.area.value="",a.say(`could not read the YAML: ${Z(j)}`,"err")})}function oe(j){if(r===null)return;a.clearForce(),g.disabled=!0,a.say(j?"saving (forced)\u2026":"saving\u2026");let F=a.area.value;Q("/api/experiment/save",{select:b,text:F,digest:r.digest,force:j}).then(N=>{var Y;g.disabled=!1,b=N.name,r={name:N.name,path:N.path,yaml_text:F,truncated:!1,digest:N.digest,editable:!0},a.say([`saved ${N.path} \xB7 previous archived at ${(Y=N.archived)!=null?Y:"\u2014"}`,...N.warnings].join(`
`),N.warnings.length>0?"err":"ok"),ye(N.experiments),N.renamed_from===null&&Ze(N.name)}).catch(N=>{g.disabled=!1;let Y=Z(N);a.say(Y,"err"),en(Y)&&a.offerForce(()=>oe(!0))})}return g.addEventListener("click",()=>oe(!1)),M.addEventListener("click",()=>V()),f.addEventListener("click",()=>{u.style.display=""}),X.addEventListener("click",()=>{u.style.display="none"}),L.addEventListener("click",()=>{var j;u.style.display="none",a.say("deleting\u2026"),Q("/api/experiment/delete",{select:b,digest:(j=r==null?void 0:r.digest)!=null?j:null}).then(F=>{q([`deleted ${F.path} \u2014 archived at ${F.archived}`,...F.warnings].join(`
`)),ye(F.experiments)}).catch(F=>{a.say(`delete refused: ${Z(F)}`,"err")})}),s.addEventListener("click",()=>{if(a.root.style.display===""){a.root.style.display="none";return}a.root.style.display="",V()}),a.root}function wn(){let t=o("div","abk-row abk-head");for(let[s,a]of[["abk-cell-disclose",""],["abk-cell-name","experiment"],["abk-cell-verdict","verdict"],["abk-cell-effect","effect (CI)"],["abk-cell-p","p / \u03B1"],["abk-cell-time","elapsed"],["abk-cell-spark","effect over the window"],["abk-cell-actions",""]])t.appendChild(o("div",`abk-cell ${s}`,a));return t}function yn(t){let s=o("div","abk-row");s.setAttribute("data-abk-experiment",t.name);let a=o("div","abk-row-main");s.appendChild(a);let r=o("div","abk-cell abk-cell-disclose"),b=h("abk-disclose","\u25B8","show verdicts, warnings and actions");b.setAttribute("aria-expanded","false"),r.appendChild(b),a.appendChild(r);let g=o("div","abk-cell abk-cell-name");g.appendChild(o("div","abk-name",t.name));let M=[t.file];if(t.status!==null&&M.push(t.status),t.main_metric!==null&&M.push(t.main_metric),g.appendChild(o("div","abk-sub",M.join(" \xB7 "))),t.tags.length>0){let l=o("div","abk-tags");for(let E of t.tags)l.appendChild(o("span","abk-tag",E));g.appendChild(l)}a.appendChild(g);let f=o("div","abk-cell abk-cell-verdict"),u=o("span","abk-chip abk-v-pending","pending");f.appendChild(u);let m=o("span","abk-badges");f.appendChild(m),a.appendChild(f);let L=o("div","abk-cell abk-cell-effect","\u2014");a.appendChild(L);let X=o("div","abk-cell abk-cell-p","\u2014");a.appendChild(X);let V=o("div","abk-cell abk-cell-time","\u2014");a.appendChild(V);let oe=o("div","abk-cell abk-cell-spark"),j=document.createElement("canvas");j.className="abk-spark",oe.appendChild(j),a.appendChild(oe);let F=o("div","abk-cell abk-cell-actions"),N=h("abk-btn","Open","the full report for this experiment"),Y=h("abk-btn","Explore","launch the tuning cockpit (may take a while)"),k=h("abk-btn abk-btn-run","Run","spawn `abk run` for this experiment");F.appendChild(N),F.appendChild(Y),F.appendChild(k),a.appendChild(F);let C=o("div","abk-row-note");C.style.display="none",s.appendChild(C);let z=o("div","abk-row-msg");z.style.display="none",s.appendChild(z);let G=o("div","abk-detail");G.style.display="none",s.appendChild(G);let se=!1,je=[];function re(l,E="info"){z.textContent=l,z.className=`abk-row-msg${E==="err"?" abk-row-msg-err":""}`,z.style.display=l===""?"none":""}function ve(l,E){C.textContent=l,C.className=`abk-row-note ${E}`.trim(),C.style.display=l===""?"none":""}N.addEventListener("click",()=>{let l=K(`/experiment/${encodeURIComponent(t.name)}`);Se(l,E=>{re("the browser blocked the tab \u2014 ","info"),z.appendChild(E)})}),Y.addEventListener("click",()=>{Y.disabled=!0;let l=()=>{Y.disabled=!1};re("starting the explore cockpit \u2014 this can take up to 90 s\u2026"),Q("/api/explore",{select:t.name}).then(E=>{l(),re(""),ae.follow(E.job_id),Se(E.url,J=>{re("cockpit ready \u2014 ","info"),z.appendChild(J)})}).catch(E=>{l(),re(`explore failed: ${Z(E)}`,"err")})});function Le(l,E,J){re(`starting ${J}\u2026`),Q(l,E).then(p=>{re(""),ae.follow(p.job_id)}).catch(p=>{re(`${J} refused: ${Z(p)}`,"err")})}k.addEventListener("click",()=>Le("/api/run",{select:t.name},"run"));let ie=o("div","abk-readout"),an=!1;function on(){var J;ie.textContent="";let l=H.get(t.name);if(l===void 0)return;l.error!==null&&ie.appendChild(o("div","abk-warning",`\u26A0 ${l.error}`));for(let p of l.warnings)ie.appendChild(o("div","abk-warning",`\u26A0 ${p}`));for(let p of l.caveats)ie.appendChild(o("div","abk-caveat",`! ${p}`));if(l.rationale.length>0){let p=o("div","abk-block");p.appendChild(o("div","abk-block-title","why this verdict"));for(let B of l.rationale)p.appendChild(o("div","abk-rationale",B));ie.appendChild(p)}if(l.verdicts.length>0){let p=o("div","abk-block");p.appendChild(o("div","abk-block-title","per arm pair"));for(let B of l.verdicts){let te=o("div","abk-pair");te.appendChild(o("span",`abk-v-word abk-v-${B.verdict.toLowerCase()}`,B.verdict)),te.appendChild(o("span","abk-pair-name",`${B.metric}: ${B.pair.c} vs ${B.pair.t}`)),te.appendChild(o("span","abk-pair-effect",le(B.effect,Ie))),B.guardrail_regressed&&te.appendChild(o("span","abk-badge-guardrail","guardrail regressed"));for(let Ee of B.caveats)te.appendChild(o("div","abk-caveat",`! ${Ee}`));p.appendChild(te)}ie.appendChild(p)}let E=[`SRM p ${le(l.srm_pvalue,Te)}`,`last look ${l.last_end_ts===null?"\u2014":`${Re(l.last_end_ts)} UTC`}`,`timezone ${(J=l.timezone)!=null?J:"\u2014"}`,l.locked?"the pipeline lock is HELD":"unlocked"];ie.appendChild(o("div","abk-facts",E.join(" \xB7 ")))}function $n(){G.appendChild(ie);let l=o("div","abk-block");l.appendChild(o("div","abk-block-title","run one comparison"));let E=o("div","abk-btn-row");for(let ue of t.comparisons){let Rn=ue.is_main_metric?ue.metric:`${ue.metric} (secondary)`,sn=h("abk-btn",`Run ${Rn}`,`abk run --metric ${ue.metric}`);sn.addEventListener("click",()=>Le("/api/run",{select:t.name,metric:ue.metric},`run ${ue.metric}`)),E.appendChild(sn)}l.appendChild(E),G.appendChild(l);let J=o("div","abk-block");J.appendChild(o("div","abk-block-title","maintenance"));let p=o("div","abk-btn-row"),B=h("abk-btn","Unlock","release a stale pipeline lock (`abk unlock`)");B.addEventListener("click",()=>Le("/api/unlock",{select:t.name},"unlock")),p.appendChild(B);let te=h("abk-btn abk-btn-danger","Clean\u2026","delete orphaned rows (`abk clean --execute`)");p.appendChild(te);let Ee=h("abk-btn abk-btn-ghost","Edit YAML",t.file);p.appendChild(Ee),J.appendChild(p);let de=o("div","abk-confirm");de.style.display="none",de.appendChild(o("div","abk-confirm-text","Clean runs `abk clean --select \u2026 --execute`: it DELETES orphaned _ab_results / _ab_unit_state rows for this experiment. There is no undo."));let Be=o("div","abk-btn-row"),rn=h("abk-btn abk-btn-danger","Clean anyway"),ln=h("abk-btn abk-btn-ghost","Cancel");rn.addEventListener("click",()=>{de.style.display="none",Le("/api/clean",{select:t.name},"clean")}),ln.addEventListener("click",()=>{de.style.display="none"}),Be.appendChild(rn),Be.appendChild(ln),de.appendChild(Be),te.addEventListener("click",()=>{de.style.display=""}),J.appendChild(de),G.appendChild(J),G.appendChild(gn(t,Ee))}b.addEventListener("click",()=>{se=!se,b.textContent=se?"\u25BE":"\u25B8",b.setAttribute("aria-expanded",se?"true":"false"),G.style.display=se?"":"none",se&&(an||(an=!0,$n()),on())});function Ln(){u.className="abk-chip abk-v-pending",u.textContent="pending",m.textContent="",L.textContent="\u2014",X.textContent="\u2014",V.textContent="\u2014",ve("",""),je=[],Ae()}function En(l){if(m.textContent="",l.error!==null)u.className="abk-chip abk-v-error",u.textContent="error",ve(l.error,"abk-v-error");else if(l.verdict===null)u.className="abk-chip abk-v-none",u.textContent="no data",ve("no computed results yet \u2014 press Run","");else{let p=["abk-chip",`abk-v-${l.verdict.toLowerCase()}`],B="";l.srm_flag?(p.push("abk-srm-fail"),B=`SRM FAILED (p ${le(l.srm_pvalue,Te)}) \u2014 effects untrustworthy`):l.insufficient?(p.push("abk-insufficient"),B="insufficient data at the latest look \u2014 inference withheld"):!l.is_horizon&&l.verdict==="INCONCLUSIVE"&&(p.push("abk-prehorizon"),B="pre-horizon: fixed CIs are not peeking-valid, so a verdict is withheld"),u.className=p.join(" "),u.textContent=l.verdict,ve(B,B===""?"":p.slice(1).join(" "))}if(l.guardrail_regressed&&m.appendChild(o("span","abk-badge-guardrail","guardrail")),l.caveats.length>0){let p=o("span","abk-badge-caveat",`\u26A0 ${l.caveats.length}`);p.title=l.caveats.join(`
`),m.appendChild(p)}if(l.weekly_cycle_pct!==null){let p=o("span","abk-badge-caveat",`${Math.round(l.weekly_cycle_pct*100)}% wk`);p.title="decided before one full weekly cycle",m.appendChild(p)}if(l.locked){let p=o("span","abk-badge-lock","locked");p.title="the pipeline lock is held \u2014 Run would refuse",m.appendChild(p)}L.textContent=`${le(l.effect,Ie)} [${le(l.ci[0])}, ${le(l.ci[1])}]`,X.textContent=`${le(l.pvalue,Te)} / ${le(l.alpha)}`;let E=Hn(l.start_ts,l.horizon_ts),J=l.elapsed_days===null?"\u2014":`${xe(l.elapsed_days)}d`;V.textContent=E===null?J:`${J} / ${xe(E)}d${l.is_horizon?" \u2713":""}`,V.title=l.last_end_ts===null?"no computed look yet":`latest look ${Re(l.last_end_ts)} UTC`,je=l.spark,Ae(),se&&on()}function Tn(l){u.className="abk-chip abk-v-error",u.textContent="error",m.textContent="",ve(l,"abk-v-error")}function Ae(){Fn(j,je)}return{root:s,pending:Ln,paint:En,paintError:Tn,redraw:Ae}}let Cn=window.setTimeout(()=>{e.experiments.length>0&&Ce(we()),$e(0)},0);d.push(()=>window.clearTimeout(Cn)),d.push(()=>{_e=!0,me&&window.clearTimeout(me),ae.dispose(),w==null||w.abort(),T+=1});let he=0,nn=()=>{he&&window.clearTimeout(he),he=window.setTimeout(()=>{for(let t of $.values())t.redraw()},120)};window.addEventListener("resize",nn),d.push(()=>{window.removeEventListener("resize",nn),he&&window.clearTimeout(he)})}function Fn(e,i){let d=null;try{d=e.getContext("2d")}catch{d=null}if(d===null){e.classList.add("abk-spark-blank");return}let c=cn(e);if(d.clearRect(0,0,e.width,e.height),e.width===0||e.height===0||i.length===0){e.title="";return}let v=i.map(([W])=>W),O=i.map(([,W])=>W===null?NaN:W),T=O.filter(W=>Number.isFinite(W)),w=v[0],A=v[v.length-1]===w?w+1:v[v.length-1],$=Math.min(0,...T),H=Math.max(0,...T),_=(H-$||Math.abs(H)||1)*.15,ne={xmin:w,xmax:A,vmin:$-_,vmax:H+_},K=bn(e,Oe,ne,c),U=He(e,Oe,c);un(d,e,Oe,c,K.py,0,dn(Ne("--abk-chart-grid"),.45),"",[3,3]),pn(d,v,O,w,A,U.left,U.right-U.left,K.px,K.py,Ne("--abk-series-1"),1.4,c),e.title=`${i.length} bucket(s), ${Fe(w)} \u2192 ${Fe(A)} UTC`}var mn=!1;function On(){if(mn)return;mn=!0;let i=`
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
`,d=document.createElement("style");d.setAttribute("data-abk-dashboard",""),d.textContent=i,document.head.appendChild(d)}window.__ABK_DASHBOARD__={render:In};})();
