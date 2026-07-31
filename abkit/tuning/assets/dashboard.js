"use strict";(()=>{var _e={"--abk-page":"#f5f1e8","--abk-card":"#fbf9f3","--abk-ink":"#1b1916","--abk-ink-2":"#6e675b","--abk-muted":"#9a9384","--abk-border":"#e6e0d4","--abk-chart-bg":"#211e1a","--abk-chart-border":"#332f29","--abk-chart-ink":"#c9c2b4","--abk-chart-grid":"#9a9384","--abk-series-1":"#c9a6f0","--abk-series-2":"#8e76e0","--abk-st-good":"#1e9e6a","--abk-st-warn":"#e0a23b","--abk-st-serious":"#d6453d","--abk-st-critical":"#b23a6b","--abk-good-text":"#15774e","--abk-explore-accent":"#6a45c4"};function je(e){return getComputedStyle(document.documentElement).getPropertyValue(e).trim()||_e[e]||"#888"}function un(e){let o=e.replace("#","").trim();o.length===3&&(o=o[0]+o[0]+o[1]+o[1]+o[2]+o[2]);let s=parseInt(o,16);return o.length!==6||Number.isNaN(s)?[137,135,129]:[s>>16&255,s>>8&255,s&255]}function Ve(e,o){let[s,c,u]=un(e);return`rgba(${s},${c},${u},${o})`}function Ke(e){let o=Math.max(1,window.devicePixelRatio||1),s=e.clientWidth||e.offsetWidth||0,c=e.clientHeight||e.offsetHeight||0;return e.width=Math.round(s*o),e.height=Math.round(c*o),o}function Ye(e,o,s,c){let u=()=>e.width-(o.l+o.r)*c,B=()=>e.height-(o.t+o.b)*c,C=()=>s.xmax-s.xmin||1,h=()=>s.vmax-s.vmin||1;return{px:y=>o.l*c+(y-s.xmin)/C()*u(),py:y=>e.height-o.b*c-(y-s.vmin)/h()*B(),xAt:y=>s.xmin+(y-o.l*c)/(u()||1)*C(),plotW:u,plotH:B}}function Ae(e,o,s){return{left:o.l*s,top:o.t*s,right:e.width-o.r*s,bottom:e.height-o.b*s}}var Me=Number.isFinite;function Xe(e,o,s,c,u,B,C,h,L,$,S,y,V){let U=o.length,N=Math.max(1,Math.round(C)),I=u-c||1,ie=0;for(let k=0;k<U;k++){let M=s[k];!Me(M)||o[k]<c||o[k]>u||ie++}e.strokeStyle=$,e.lineWidth=S*y,e.lineJoin="round",V&&e.setLineDash(V.map(k=>k*y)),e.beginPath();let K=[];if(ie<=N){let k=T=>T>=0&&T<U&&Me(s[T])&&o[T]>=c&&o[T]<=u,M=!1;for(let T=0;T<U;T++){if(!k(T)){M=!1;continue}let v=h(o[T]),j=L(s[T]);if(!k(T-1)&&!k(T+1)){K.push([v,j]),M=!1;continue}M?e.lineTo(v,j):(e.moveTo(v,j),M=!0)}}else{let k=new Array(N).fill(null),M=new Array(N).fill(null);for(let v=0;v<U;v++){let j=s[v],Y=o[v];if(!Me(j)||Y<c||Y>u)continue;let D=Math.floor((Y-c)/I*(N-1));D=D<0?0:D>N-1?N-1:D,(k[D]===null||j<k[D])&&(k[D]=j),(M[D]===null||j>M[D])&&(M[D]=j)}let T=!1;for(let v=0;v<N;v++){if(M[v]===null){T=!1;continue}let j=B+v,Y=L(M[v]),D=L(k[v]);T?e.lineTo(j,Y):(e.moveTo(j,Y),T=!0),e.lineTo(j,D)}}if(e.stroke(),V&&e.setLineDash([]),K.length>0){e.fillStyle=$,e.beginPath();for(let[k,M]of K)e.moveTo(k+Math.max(1.5,S)*y,M),e.arc(k,M,Math.max(1.5,S)*y,0,Math.PI*2);e.fill()}}function Ge(e,o,s,c,u,B,C,h,L){let $=u(B),S=Ae(o,s,c);$<S.top-.5||$>S.bottom+.5||(e.strokeStyle=C,e.lineWidth=1.25*c,L&&e.setLineDash(L.map(y=>y*c)),e.beginPath(),e.moveTo(S.left,$),e.lineTo(S.right,$),e.stroke(),L&&e.setLineDash([]),h&&(e.fillStyle=C,e.textAlign="right",e.textBaseline="middle",e.font=`${10*c}px ui-monospace, Menlo, Consolas, monospace`,e.fillText(h,(s.l-8)*c,$)))}function ue(e){let o=Math.abs(e);return o>=1e3?e.toFixed(0):o>=10?e.toFixed(1):o>=1?e.toFixed(2):o>=.001||o===0?e.toFixed(3):e.toExponential(1)}function Be(e){return(e>0?"+":"")+ue(e)}function ve(e){return e<.001?"<0.001":e.toFixed(3)}function we(e){return new Date(e).toISOString().slice(0,16).replace("T"," ")}function De(e){return new Date(e).toISOString().slice(0,10)}var kn="<svg class='abk-logomark' viewBox='0 0 100 100' width='22' height='22' role='img' aria-label='abkit' focusable='false'><rect x='3' y='3' width='94' height='94' rx='26' fill='#6a45c4'/><g fill='none' stroke='#fbf9f3' stroke-width='9' stroke-linecap='round' stroke-linejoin='round'><polyline points='13 50 34 50'/><polyline points='34 50 86 27'/><polyline points='34 50 86 61'/></g><circle cx='86' cy='27' r='7' fill='#fbf9f3'/></svg>";function qe(){let e=document.createElement("div");e.className="abk-brand",e.innerHTML=kn;let o=document.createElement("span");return o.className="abk-wordmark",o.textContent="abkit",e.appendChild(o),e}var n="abk-dashboard",fn=3,Pe={l:3,r:3,t:5,b:5},mn=864e5,hn=1200,xn=8e3,gn=600,vn=2e3;function a(e,o,s){let c=document.createElement(e);return o&&(c.className=o),s!==void 0&&(c.textContent=s),c}function _(e,o,s){let c=document.createElement("button");return c.className=e,c.textContent=o,c.type="button",s&&(c.title=s),c}var te=(e,o=ue)=>e===null?"\u2014":o(e);function re(e){return e instanceof Error?e.message||e.name:String(e)}function Ze(e){return e instanceof Error&&e.name==="AbortError"}function wn(e,o){return e===null||o===null?null:(o-e)/mn}var He=null;function Cn(e,o){var Oe;yn(),He&&He();let s=[];He=()=>{for(let t of s)t();s.length=0},o.classList.add(n),o.innerHTML="";let c=a("div","abk-root");o.appendChild(c);let u=(Oe=new URLSearchParams(window.location.search).get("token"))!=null?Oe:"",B=e.initial_window,C=0,h=null,L=0,$=new Map,S=new Map,y=new Map,V=!1;function U(t,d={}){let l=new URLSearchParams({...d,token:u});return`${t}?${l.toString()}`}async function N(t){let d="";try{d=(await t.text()).trim()}catch{d=""}let l=new Error(d||`HTTP ${t.status}`);return l.name=`HTTP${t.status}`,l}async function I(t,d={},l){let i=await fetch(U(t,d),l?{signal:l}:void 0);if(!i.ok)throw await N(i);return await i.json()}async function ie(t,d){let l=await fetch(U(t),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)});if(!l.ok)throw await N(l);return await l.json()}let K=a("div","abk-header");c.appendChild(K),K.appendChild(qe());let k=a("div","abk-h-top");K.appendChild(k);let M=a("h1","abk-title",`dashboard \xB7 ${e.project}`);k.appendChild(M),k.appendChild(a("span","abk-badge-page",`v${e.version}`)),e.profile!==null&&k.appendChild(a("span","abk-badge-page",`profile: ${e.profile}`));let T=[`${e.experiments.length} experiment${e.experiments.length===1?"":"s"}`,`booted ${we(e.generated_at)} UTC`];if(K.appendChild(a("div","abk-meta",T.join(" \xB7 "))),u===""){let t=a("div","abk-warning","\u26A0 this page was opened without its ?token= \u2014 every request will be refused. Reopen the URL `abk dashboard` printed.");K.appendChild(t)}let v=a("div","abk-controls");c.appendChild(v);let j=a("div","abk-seg");v.appendChild(a("span","abk-ctl-label","sparkline window")),v.appendChild(j);let Y=new Map;for(let t of e.window_presets){let d=_("abk-seg-btn",t,`bound the sparkline to the last ${t}`);t===B&&d.classList.add("on"),d.addEventListener("click",()=>{if(t!==B){B=t;for(let[l,i]of Y)i.classList.toggle("on",l===t);ye($e())}}),Y.set(t,d),j.appendChild(d)}let D=_("abk-btn abk-btn-ghost","Refresh","re-read every row");D.addEventListener("click",()=>ye($e())),v.appendChild(D);let Ce=a("span","abk-chip abk-chip-fill","idle");v.appendChild(Ce);let ke=_("abk-chip abk-chip-job","jobs: idle","open the job drawer");ke.addEventListener("click",()=>q.toggleList()),v.appendChild(ke);let fe=a("div","abk-list");if(c.appendChild(fe),e.experiments.length===0)fe.appendChild(a("div","abk-empty","No experiments in this dashboard\u2019s selection \u2014 restart with `abk dashboard --select \u2026`."));else{let t=new Map;for(let l of e.experiments){let i=t.get(l.dir);i===void 0?t.set(l.dir,[l]):i.push(l)}let d=t.size>1;for(let[l,i]of t){d&&fe.appendChild(a("div","abk-group",l===""?"(top level)":l));let f=a("div","abk-table");f.appendChild(on());for(let J of i){let A=rn(J);$.set(J.name,A),f.appendChild(A.root)}fe.appendChild(f)}}let q=an();c.appendChild(q.root);function $e(){return e.experiments.map(t=>t.name)}function Ne(){Ce.textContent=L>0?`loading ${L}\u2026`:"idle",Ce.classList.toggle("abk-chip-busy",L>0)}function ye(t){var A;C+=1;let d=C;h==null||h.abort();let l=new AbortController;h=l;for(let x of t)(A=$.get(x))==null||A.pending();let i=t.slice();L=i.length,Ne();let f=async()=>{var x,m;for(;;){if(d!==C)return;let R=i.shift();if(R===void 0)return;try{let O=await I(`/api/stats/${encodeURIComponent(R)}`,{window:B},l.signal);if(d!==C)return;S.set(R,O),(x=$.get(R))==null||x.paint(O)}catch(O){if(Ze(O)||d!==C)return;(m=$.get(R))==null||m.paintError(re(O))}finally{d===C&&(L=Math.max(0,L-1),Ne())}}},J=[];for(let x=0;x<Math.min(fn,i.length);x++)J.push(f());Promise.all(J)}function en(t){if(!$.has(t))return;let d=C,l=$.get(t);l==null||l.pending(),I(`/api/stats/${encodeURIComponent(t)}`,{window:B},h==null?void 0:h.signal).then(i=>{d===C&&(S.set(t,i),l==null||l.paint(i))}).catch(i=>{Ze(i)||d!==C||l==null||l.paintError(re(i))})}let de=0,Le=!1;function me(t){Le||(de&&window.clearTimeout(de),de=window.setTimeout(()=>{nn()},t))}async function nn(){try{let t=await I("/api/jobs");if(Le)return;tn(t)}catch{}me(V||q.following()?hn:xn)}function tn(t){V=t.pipeline_active;let d=t.jobs.filter(i=>i.status==="running");ke.textContent=d.length===0?"jobs: idle":`jobs: ${d.map(i=>{var f;return`${i.kind} ${(f=i.experiment)!=null?f:i.label}`}).join(", ")}`,ke.classList.toggle("abk-chip-busy",d.length>0);for(let i of $.values())i.root.classList.toggle("abk-busy",V);for(let i of t.jobs){let f=y.get(i.id);y.set(i.id,i.status),f==="running"&&i.status!=="running"&&i.experiment!==null&&en(i.experiment)}let l=new Set(t.jobs.map(i=>i.id));for(let i of[...y.keys()])l.has(i)||y.delete(i);q.adoptList(t.jobs)}function an(){let t=a("div","abk-drawer");t.style.display="none";let d=a("div","abk-drawer-head"),l=a("span","abk-drawer-label","jobs"),i=a("span","abk-drawer-status",""),f=_("abk-btn abk-btn-danger","Stop","SIGTERM, then SIGKILL after 5s"),J=_("abk-btn abk-btn-ghost","Close");d.appendChild(l),d.appendChild(i),d.appendChild(f),d.appendChild(J);let A=a("div","abk-drawer-list"),x=a("pre","abk-drawer-log");t.appendChild(d),t.appendChild(A),t.appendChild(x);let m=null,R=0,O=0,X=!1;function W(){O&&(window.clearTimeout(O),O=0)}function be(){t.style.display=""}function le(){W(),m=null,X=!1,t.style.display="none"}J.addEventListener("click",le),f.addEventListener("click",()=>{let p=m;p!==null&&(f.disabled=!0,ie(`/api/job/${encodeURIComponent(p)}/stop`,{}).then(()=>{Z()}).catch(g=>{i.textContent=`stop failed: ${re(g)}`,f.disabled=!1}))});function se(p){if(p.length!==0){for(let g of p)x.appendChild(a("div","abk-log-line",g));for(;x.childElementCount>vn&&x.firstElementChild!==null;)x.removeChild(x.firstElementChild);x.scrollTop=x.scrollHeight}}function he(p){l.textContent=p.label;let g=[p.status];if(p.returncode!==null&&g.push(`exit ${p.returncode}`),p.truncated&&g.push(`${p.dropped} earlier line(s) discarded \u2014 the buffer is capped`),i.textContent=g.join(" \xB7 "),i.className=`abk-drawer-status abk-job-${p.status}`,f.style.display=p.status==="running"?"":"none",f.disabled=p.status!=="running",se(p.lines),R=p.next_offset,p.url!==null&&p.status==="running"){let P=_("abk-btn abk-btn-ghost","Open cockpit"),F=p.url;P.addEventListener("click",()=>Te(F)),i.appendChild(P)}}async function Z(){let p=m;if(p!==null){W();try{let g=await I(`/api/job/${encodeURIComponent(p)}`,{offset:String(R)});if(m!==p)return;he(g),g.status==="running"&&(O=window.setTimeout(()=>{Z()},gn))}catch(g){if(m!==p)return;i.textContent=`poll failed: ${re(g)}`}}}return{root:t,follow(p){W(),m=p,R=0,X=!1,A.style.display="none",x.style.display="",x.textContent="",i.textContent="starting\u2026",be(),Z(),me(0)},toggleList(){if(X){le();return}W(),m=null,X=!0,l.textContent="jobs",i.textContent="",f.style.display="none",A.style.display="",x.style.display="none",be(),me(0)},adoptList(p){if(X){if(A.textContent="",p.length===0){A.appendChild(a("div","abk-log-line","no jobs spawned yet"));return}for(let g of p){let P=a("div","abk-job-row");P.appendChild(a("span",`abk-job-${g.status}`,g.status)),P.appendChild(a("span","abk-job-label",g.label));let F=_("abk-btn abk-btn-ghost","log");F.addEventListener("click",()=>q.follow(g.id)),P.appendChild(F),A.appendChild(P)}}},following(){return m!==null},dispose(){W(),m=null}}}function Te(t,d){let l=null;try{l=window.open(t,"_blank")}catch{l=null}if(l!==null||d===void 0)return;let i=a("a","abk-link",t);i.href=t,i.target="_blank",i.rel="noopener",d(i)}function on(){let t=a("div","abk-row abk-head");for(let[d,l]of[["abk-cell-disclose",""],["abk-cell-name","experiment"],["abk-cell-verdict","verdict"],["abk-cell-effect","effect (CI)"],["abk-cell-p","p / \u03B1"],["abk-cell-time","elapsed"],["abk-cell-spark","effect over the window"],["abk-cell-actions",""]])t.appendChild(a("div",`abk-cell ${d}`,l));return t}function rn(t){let d=a("div","abk-row");d.setAttribute("data-abk-experiment",t.name);let l=a("div","abk-row-main");d.appendChild(l);let i=a("div","abk-cell abk-cell-disclose"),f=_("abk-disclose","\u25B8","show verdicts, warnings and actions");f.setAttribute("aria-expanded","false"),i.appendChild(f),l.appendChild(i);let J=a("div","abk-cell abk-cell-name");J.appendChild(a("div","abk-name",t.name));let A=[t.file];if(t.status!==null&&A.push(t.status),t.main_metric!==null&&A.push(t.main_metric),J.appendChild(a("div","abk-sub",A.join(" \xB7 "))),t.tags.length>0){let r=a("div","abk-tags");for(let w of t.tags)r.appendChild(a("span","abk-tag",w));J.appendChild(r)}l.appendChild(J);let x=a("div","abk-cell abk-cell-verdict"),m=a("span","abk-chip abk-v-pending","pending");x.appendChild(m);let R=a("span","abk-badges");x.appendChild(R),l.appendChild(x);let O=a("div","abk-cell abk-cell-effect","\u2014");l.appendChild(O);let X=a("div","abk-cell abk-cell-p","\u2014");l.appendChild(X);let W=a("div","abk-cell abk-cell-time","\u2014");l.appendChild(W);let be=a("div","abk-cell abk-cell-spark"),le=document.createElement("canvas");le.className="abk-spark",be.appendChild(le),l.appendChild(be);let se=a("div","abk-cell abk-cell-actions"),he=_("abk-btn","Open","the full report for this experiment"),Z=_("abk-btn","Explore","launch the tuning cockpit (may take a while)"),p=_("abk-btn abk-btn-run","Run","spawn `abk run` for this experiment");se.appendChild(he),se.appendChild(Z),se.appendChild(p),l.appendChild(se);let g=a("div","abk-row-note");g.style.display="none",d.appendChild(g);let P=a("div","abk-row-msg");P.style.display="none",d.appendChild(P);let F=a("div","abk-detail");F.style.display="none",d.appendChild(F);let ae=!1,Ee=[];function Q(r,w="info"){P.textContent=r,P.className=`abk-row-msg${w==="err"?" abk-row-msg-err":""}`,P.style.display=r===""?"none":""}function pe(r,w){g.textContent=r,g.className=`abk-row-note ${w}`.trim(),g.style.display=r===""?"none":""}he.addEventListener("click",()=>{let r=U(`/experiment/${encodeURIComponent(t.name)}`);Te(r,w=>{Q("the browser blocked the tab \u2014 ","info"),P.appendChild(w)})}),Z.addEventListener("click",()=>{Z.disabled=!0;let r=()=>{Z.disabled=!1};Q("starting the explore cockpit \u2014 this can take up to 90 s\u2026"),ie("/api/explore",{select:t.name}).then(w=>{r(),Q(""),q.follow(w.job_id),Te(w.url,H=>{Q("cockpit ready \u2014 ","info"),P.appendChild(H)})}).catch(w=>{r(),Q(`explore failed: ${re(w)}`,"err")})});function xe(r,w,H){Q(`starting ${H}\u2026`),ie(r,w).then(b=>{Q(""),q.follow(b.job_id)}).catch(b=>{Q(`${H} refused: ${re(b)}`,"err")})}p.addEventListener("click",()=>xe("/api/run",{select:t.name},"run"));let ee=a("div","abk-readout"),ze=!1;function Je(){var H;ee.textContent="";let r=S.get(t.name);if(r===void 0)return;r.error!==null&&ee.appendChild(a("div","abk-warning",`\u26A0 ${r.error}`));for(let b of r.warnings)ee.appendChild(a("div","abk-warning",`\u26A0 ${b}`));for(let b of r.caveats)ee.appendChild(a("div","abk-caveat",`! ${b}`));if(r.rationale.length>0){let b=a("div","abk-block");b.appendChild(a("div","abk-block-title","why this verdict"));for(let E of r.rationale)b.appendChild(a("div","abk-rationale",E));ee.appendChild(b)}if(r.verdicts.length>0){let b=a("div","abk-block");b.appendChild(a("div","abk-block-title","per arm pair"));for(let E of r.verdicts){let G=a("div","abk-pair");G.appendChild(a("span",`abk-v-word abk-v-${E.verdict.toLowerCase()}`,E.verdict)),G.appendChild(a("span","abk-pair-name",`${E.metric}: ${E.pair.c} vs ${E.pair.t}`)),G.appendChild(a("span","abk-pair-effect",te(E.effect,Be))),E.guardrail_regressed&&G.appendChild(a("span","abk-badge-guardrail","guardrail regressed"));for(let ge of E.caveats)G.appendChild(a("div","abk-caveat",`! ${ge}`));b.appendChild(G)}ee.appendChild(b)}let w=[`SRM p ${te(r.srm_pvalue,ve)}`,`last look ${r.last_end_ts===null?"\u2014":`${we(r.last_end_ts)} UTC`}`,`timezone ${(H=r.timezone)!=null?H:"\u2014"}`,r.locked?"the pipeline lock is HELD":"unlocked"];ee.appendChild(a("div","abk-facts",w.join(" \xB7 ")))}function sn(){F.appendChild(ee);let r=a("div","abk-block");r.appendChild(a("div","abk-block-title","run one comparison"));let w=a("div","abk-btn-row");for(let z of t.comparisons){let pn=z.is_main_metric?z.metric:`${z.metric} (secondary)`,We=_("abk-btn",`Run ${pn}`,`abk run --metric ${z.metric}`);We.addEventListener("click",()=>xe("/api/run",{select:t.name,metric:z.metric},`run ${z.metric}`)),w.appendChild(We)}r.appendChild(w),F.appendChild(r);let H=a("div","abk-block");H.appendChild(a("div","abk-block-title","maintenance"));let b=a("div","abk-btn-row"),E=_("abk-btn","Unlock","release a stale pipeline lock (`abk unlock`)");E.addEventListener("click",()=>xe("/api/unlock",{select:t.name},"unlock")),b.appendChild(E);let G=_("abk-btn abk-btn-danger","Clean\u2026","delete orphaned rows (`abk clean --execute`)");b.appendChild(G);let ge=_("abk-btn abk-btn-ghost","Show YAML",t.file);b.appendChild(ge),H.appendChild(b);let oe=a("div","abk-confirm");oe.style.display="none",oe.appendChild(a("div","abk-confirm-text","Clean runs `abk clean --select \u2026 --execute`: it DELETES orphaned _ab_results / _ab_unit_state rows for this experiment. There is no undo."));let Se=a("div","abk-btn-row"),Ue=_("abk-btn abk-btn-danger","Clean anyway"),Fe=_("abk-btn abk-btn-ghost","Cancel");Ue.addEventListener("click",()=>{oe.style.display="none",xe("/api/clean",{select:t.name},"clean")}),Fe.addEventListener("click",()=>{oe.style.display="none"}),Se.appendChild(Ue),Se.appendChild(Fe),oe.appendChild(Se),G.addEventListener("click",()=>{oe.style.display=""}),H.appendChild(oe),F.appendChild(H);let ne=a("pre","abk-source");ne.style.display="none",F.appendChild(ne),ge.addEventListener("click",()=>{if(ne.style.display===""){ne.style.display="none";return}ne.style.display="",ne.textContent="loading\u2026",I(`/api/experiment-source/${encodeURIComponent(t.name)}`).then(z=>{ne.textContent=z.truncated?`${z.yaml_text}
\u2026 truncated \u2014 open ${z.path} in your editor`:`${z.path}

${z.yaml_text}`}).catch(z=>{ne.textContent=`could not read the YAML: ${re(z)}`})})}f.addEventListener("click",()=>{ae=!ae,f.textContent=ae?"\u25BE":"\u25B8",f.setAttribute("aria-expanded",ae?"true":"false"),F.style.display=ae?"":"none",ae&&(ze||(ze=!0,sn()),Je())});function dn(){m.className="abk-chip abk-v-pending",m.textContent="pending",R.textContent="",O.textContent="\u2014",X.textContent="\u2014",W.textContent="\u2014",pe("",""),Ee=[],Re()}function cn(r){if(R.textContent="",r.error!==null)m.className="abk-chip abk-v-error",m.textContent="error",pe(r.error,"abk-v-error");else if(r.verdict===null)m.className="abk-chip abk-v-none",m.textContent="no data",pe("no computed results yet \u2014 press Run","");else{let b=["abk-chip",`abk-v-${r.verdict.toLowerCase()}`],E="";r.srm_flag?(b.push("abk-srm-fail"),E=`SRM FAILED (p ${te(r.srm_pvalue,ve)}) \u2014 effects untrustworthy`):r.insufficient?(b.push("abk-insufficient"),E="insufficient data at the latest look \u2014 inference withheld"):!r.is_horizon&&r.verdict==="INCONCLUSIVE"&&(b.push("abk-prehorizon"),E="pre-horizon: fixed CIs are not peeking-valid, so a verdict is withheld"),m.className=b.join(" "),m.textContent=r.verdict,pe(E,E===""?"":b.slice(1).join(" "))}if(r.guardrail_regressed&&R.appendChild(a("span","abk-badge-guardrail","guardrail")),r.caveats.length>0){let b=a("span","abk-badge-caveat",`\u26A0 ${r.caveats.length}`);b.title=r.caveats.join(`
`),R.appendChild(b)}if(r.weekly_cycle_pct!==null){let b=a("span","abk-badge-caveat",`${Math.round(r.weekly_cycle_pct*100)}% wk`);b.title="decided before one full weekly cycle",R.appendChild(b)}if(r.locked){let b=a("span","abk-badge-lock","locked");b.title="the pipeline lock is held \u2014 Run would refuse",R.appendChild(b)}O.textContent=`${te(r.effect,Be)} [${te(r.ci[0])}, ${te(r.ci[1])}]`,X.textContent=`${te(r.pvalue,ve)} / ${te(r.alpha)}`;let w=wn(r.start_ts,r.horizon_ts),H=r.elapsed_days===null?"\u2014":`${ue(r.elapsed_days)}d`;W.textContent=w===null?H:`${H} / ${ue(w)}d${r.is_horizon?" \u2713":""}`,W.title=r.last_end_ts===null?"no computed look yet":`latest look ${we(r.last_end_ts)} UTC`,Ee=r.spark,Re(),ae&&Je()}function bn(r){m.className="abk-chip abk-v-error",m.textContent="error",R.textContent="",pe(r,"abk-v-error")}function Re(){$n(le,Ee)}return{root:d,pending:dn,paint:cn,paintError:bn,redraw:Re}}let ln=window.setTimeout(()=>{e.experiments.length>0&&ye($e()),me(0)},0);s.push(()=>window.clearTimeout(ln)),s.push(()=>{Le=!0,de&&window.clearTimeout(de),q.dispose(),h==null||h.abort(),C+=1});let ce=0,Ie=()=>{ce&&window.clearTimeout(ce),ce=window.setTimeout(()=>{for(let t of $.values())t.redraw()},120)};window.addEventListener("resize",Ie),s.push(()=>{window.removeEventListener("resize",Ie),ce&&window.clearTimeout(ce)})}function $n(e,o){let s=null;try{s=e.getContext("2d")}catch{s=null}if(s===null){e.classList.add("abk-spark-blank");return}let c=Ke(e);if(s.clearRect(0,0,e.width,e.height),e.width===0||e.height===0||o.length===0){e.title="";return}let u=o.map(([I])=>I),B=o.map(([,I])=>I===null?NaN:I),C=B.filter(I=>Number.isFinite(I)),h=u[0],L=u[u.length-1]===h?h+1:u[u.length-1],$=Math.min(0,...C),S=Math.max(0,...C),y=(S-$||Math.abs(S)||1)*.15,V={xmin:h,xmax:L,vmin:$-y,vmax:S+y},U=Ye(e,Pe,V,c),N=Ae(e,Pe,c);Ge(s,e,Pe,c,U.py,0,Ve(je("--abk-chart-grid"),.45),"",[3,3]),Xe(s,u,B,h,L,N.left,N.right-N.left,U.px,U.py,je("--abk-series-1"),1.4,c),e.title=`${o.length} bucket(s), ${De(h)} \u2192 ${De(L)} UTC`}var Qe=!1;function yn(){if(Qe)return;Qe=!0;let o=`
:where(:root){${Object.entries(_e).map(([c,u])=>`${c}:${u}`).join(";")};
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
.${n} .abk-source{font:10.5px var(--abk-mono);background:var(--abk-page);
  border:1px solid var(--abk-border);border-radius:8px;padding:8px;margin-top:8px;
  max-height:320px;overflow:auto;white-space:pre-wrap;}
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
`,s=document.createElement("style");s.setAttribute("data-abk-dashboard",""),s.textContent=o,document.head.appendChild(s)}window.__ABK_DASHBOARD__={render:Cn};})();
