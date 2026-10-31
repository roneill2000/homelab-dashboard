import {catalogRows} from './catalog.mjs';
import {tokens as m3Tokens,SEED as PINE} from '../m3-tokens.mjs';
import {LogoMark,withPageTransition,morph,CountUp,Icon,Button,IconButton,Segmented,Chip,BadgeHost,Card,CardHeader,LinearProgress,IndeterminateProgress,LoadingIndicator,Spinner,tabbables,lockScroll,TextField,SelectField,Dialog,Snackbar,ListItem,Divider,PageHeader,Section,useWindowClass,Skeleton,MenuSelect} from './m3.jsx';
const {useState,useEffect,useLayoutEffect,useRef,useMemo}=React;
const valid=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
const num=(v,dp=0)=>valid(v)?Number(v).toLocaleString(undefined,{maximumFractionDigits:dp}):'—';
const rate=v=>valid(v)?num(Number(v)/1e6,1):'—';
const size=v=>!valid(v)?'—':v>=1e12?`${num(v/1e12,2)} TB`:`${num(v/1e9,1)} GB`;
const bounded=v=>Math.max(0,Math.min(100,Number(v)||0));
function useStored(key,initial){const [v,set]=useState(()=>{try{return JSON.parse(localStorage.getItem(key))??initial}catch{return initial}});useEffect(()=>{try{localStorage.setItem(key,JSON.stringify(v))}catch{}},[v]);return [v,set]}
const catalog=catalogRows.map(([id,name,desc,url,group,icon,accent])=>({id,name,desc,url,group,icon,accent,color:`var(--md-custom-${accent})`,ip:new URL(url).host}));
function sourceFresh(s){return s?.online===true&&(!('last_success_at' in s)||(valid(s.last_success_at)&&Date.now()/1000-s.last_success_at<45))&&!s.error}
// Speedtest Tracker returns local time (APP_TIMEZONE America/New_York) as 'YYYY-MM-DD HH:MM:SS', parsed as browser-local. Older than 12 h gets flagged.
function testedLabel(value){if(!value)return '';const t=Date.parse(String(value).replace(' ','T'));if(!Number.isFinite(t))return '';const h=(Date.now()-t)/3600000;const when=h<1?`${Math.max(1,Math.round(h*60))} min ago`:h<48?`${Math.round(h)} h ago`:`${Math.round(h/24)} days ago`;return ` · tested ${when}${h>12?' (old)':''}`}
function serviceState(s,d){if(!d||!d.updated_at)return 'unknown';const direct={openwebui:d.ai?.webui,pve:d.pve,unraid:d.unraid,scrutiny:d.scrutiny,syslog:d.syslog,netalertx:d.netalertx,sabnzbd:d.media?.sabnzbd,sonarr:d.media?.sonarr,readarr:d.media?.readarr,prowlarr:d.media?.prowlarr,jellyfin:d.media?.jellyfin};const source=direct[s.id];const value=source?.online??d.services?.[s.id]??null;if(value===null)return null;if(d.updated_at&&Date.now()/1000-d.updated_at>45)return 'stale';if(source?.last_success_at&&Date.now()/1000-source.last_success_at>45)return 'stale';if(!source&&d.services?.last_success_at&&Date.now()/1000-d.services.last_success_at>45)return 'stale';return value}
function SourceBadge({source,onlineLabel}){const stale=source?.last_success_at&&Date.now()/1000-source.last_success_at>45;return <Badge state={stale?'warn':source?.online} label={stale?'Stale':source?.error?'Unavailable':source?.online?onlineLabel:undefined}/>}
function SourceNote({source}){if(!source?.error&&(!source?.last_success_at||Date.now()/1000-source.last_success_at<=45))return null;return <p className="source-note error">{source.error?'Source unavailable. ':''}Last successful read: {source.last_success_at?new Date(source.last_success_at*1000).toLocaleTimeString():'none'}.</p>}
function Badge({state,label}){return <span className={'badge '+(state===true?'good':state===false?'bad':state==='warn'?'warn':'neutral')}><i/>{label||(state===true?'Online':state===false?'Unreachable':state==='warn'?'Check':'Unknown')}</span>}
function Chart({labels={},points:allPoints=[],keys=['read_bps','write_bps'],colors=['var(--md-chart-1)','var(--md-chart-2)'],unit='MB/s',divisor=1e6,height=150,gapSeconds=90,maxValue=null,inDialog=false,legend=false}){
  const [big,setBig]=useState(null);const props={labels,points:allPoints,keys,colors,unit,divisor,gapSeconds,maxValue};
  // Enlarged chart only: zoom the time axis (drag across it, scroll, pinch, or the buttons) and fit the y axis to what's
  // visible. view is [from,to] in seconds; null is the whole loaded range. The small charts keep the honest full scale.
  const [view,setView]=useState(null),[fit,setFit]=useState(false),[brush,setBrush]=useState(null);
  const fullStart=allPoints[0]?.ts,fullEnd=allPoints.at(-1)?.ts;
  const points=view?allPoints.filter((p,i,a)=>p.ts>=view[0]&&p.ts<=view[1]||(a[i+1]&&a[i+1].ts>=view[0]&&p.ts<view[0])||(a[i-1]&&a[i-1].ts<=view[1]&&p.ts>view[1])):allPoints;
  const clampView=(a,b)=>{if(!fullStart||!fullEnd)return null;const span=Math.max(60,Math.min(fullEnd-fullStart,b-a));let s0=Math.max(fullStart,Math.min(a,fullEnd-span));const e0=s0+span;return e0-s0>=fullEnd-fullStart-1?null:[s0,e0]};
  const openBig=()=>{const card=chartRef.current?.closest('article,section');const h=card?.querySelector('h3,h2')?.textContent?.trim();setBig(h||'Chart')};
 const [hover,setHover]=useState(null),[chartWidth,setChartWidth]=useState(600);const chartRef=useRef(null);
  // Lines are drawn a frame after the chart mounts, so opening a page full of charts fits in one 120 Hz frame.
  const [drawn,setDrawn]=useState(false);useEffect(()=>{const id=requestAnimationFrame(()=>setDrawn(true));return()=>cancelAnimationFrame(id)},[]);
  // Measure before the first paint: the SVG scales by its aspect ratio, so a frame drawn at the 600px guess is too tall
  // and the enlarged-chart dialog visibly jumps when it shrinks back.
  useLayoutEffect(()=>{const el=chartRef.current;if(!el)return;setChartWidth(Math.max(240,el.clientWidth));const ro=new ResizeObserver(([entry])=>setChartWidth(Math.max(240,entry.contentRect.width)));ro.observe(el);return()=>ro.disconnect()},[]);const clean=points.filter(p=>keys.some(k=>valid(p[k])));const niceMax=v=>{const e=10**Math.floor(Math.log10(v)),f=v/e;return (f<=1?1:f<=2?2:f<=2.5?2.5:f<=5?5:10)*e};// Each unit has a minimum scale so a quiet line stays near the floor instead of being stretched into a spike.
  const floor={'MB/s':10,'Mbps':10,'ms':20,'I/O':4,'°C':100,'W':100}[unit]||1;const peak=Math.max(0,...clean.flatMap(p=>keys.map(k=>(Number(p[k])||0)/divisor)));
  const max=fit?niceMax(Math.max(peak*1.1,1e-3)):valid(maxValue)&&maxValue>0?maxValue:unit==='%'?100:Math.max(floor,niceMax(Math.max(1,peak)*1.1));
 const start=view?view[0]:points[0]?.ts,end=view?view[1]:points.at(-1)?.ts;const W=chartWidth,H=height;const x=p=>42+((p.ts-start)/Math.max(1,end-start))*(W-52);const y=v=>H-25-(Number(v)/divisor/max)*(H-50);
 function segments(key){let out=[],part=[];for(let i=0;i<points.length;i++){const p=points[i];if(!valid(p[key])||(i&&p.ts-points[i-1].ts>gapSeconds)){if(part.length)out.push(part);part=[];}if(valid(p[key]))part.push(p)}if(part.length)out.push(part);return out}
 const trackPointer=ev=>{const r=ev.currentTarget.getBoundingClientRect();setHover(Math.max(0,Math.min(1,((ev.clientX-r.left)/r.width*W-42)/(W-52))))};const inspectKey=ev=>{if(ev.key==='Escape'&&hover!==null){ev.stopPropagation();setHover(null);return}if(!['ArrowLeft','ArrowRight','Home','End'].includes(ev.key)||!points.length)return;ev.preventDefault();const at=points.findIndex(p=>p===selected);const next=ev.key==='Home'?0:ev.key==='End'?points.length-1:Math.max(0,Math.min(points.length-1,(at<0?points.length-1:at)+(ev.key==='ArrowLeft'?-1:1)));setHover((points[next].ts-start)/Math.max(1,end-start))};const targetTs=start+hover*(end-start);const selected=hover===null?null:points.reduce((best,p)=>!best||Math.abs(p.ts-targetTs)<Math.abs(best.ts-targetTs)?p:best,null);
 // Zoom gestures (enlarged chart): pinch and two-finger pan on touch, drag-to-select and wheel with a mouse.
 const tsAt=cx=>{const r=chartRef.current?.querySelector('svg')?.getBoundingClientRect();if(!r)return start;const f=Math.max(0,Math.min(1,((cx-r.left)/r.width*W-42)/(W-52)));return start+f*(end-start)};
 const fracAt=cx=>{const r=chartRef.current?.querySelector('svg')?.getBoundingClientRect();return r?Math.max(0,Math.min(1,((cx-r.left)/r.width*W-42)/(W-52))):.5};
 const zoomAt=(center,factor)=>{if(!start||!end)return;const span=(end-start)*factor,f=(center-start)/Math.max(1,end-start);setView(clampView(center-f*span,center-f*span+span))};
 const touches=useRef(new Map()),pinch=useRef(null),drag=useRef(null);
 useEffect(()=>{if(!inDialog)return;const svg=chartRef.current?.querySelector('svg');if(!svg)return;const on=e=>{e.preventDefault();zoomAt(tsAt(e.clientX),e.deltaY>0?1.25:.8)};svg.addEventListener('wheel',on,{passive:false});return()=>svg.removeEventListener('wheel',on)});
 const zDown=ev=>{if(!inDialog)return;try{ev.currentTarget.setPointerCapture(ev.pointerId)}catch{}touches.current.set(ev.pointerId,ev.clientX);
  if(touches.current.size===2){const [a,b]=[...touches.current.values()];pinch.current={d0:Math.max(20,Math.abs(a-b)),a0:start,b0:end,c0:tsAt((a+b)/2)};drag.current=null;setBrush(null)}
  else if(ev.pointerType==='mouse'&&ev.button===0)drag.current={x0:ev.clientX,t0:tsAt(ev.clientX)}};
 const zMove=ev=>{if(!inDialog)return;if(touches.current.has(ev.pointerId))touches.current.set(ev.pointerId,ev.clientX);
  const pc=pinch.current;if(pc&&touches.current.size===2){const [a,b]=[...touches.current.values()];const span=(pc.b0-pc.a0)*pc.d0/Math.max(20,Math.abs(a-b));const s0=pc.c0-fracAt((a+b)/2)*span;setView(clampView(s0,s0+span));return true}
  if(drag.current&&ev.buttons&&Math.abs(ev.clientX-drag.current.x0)>6){setBrush([drag.current.t0,tsAt(ev.clientX)]);return true}};
 const zUp=ev=>{if(!inDialog)return;touches.current.delete(ev.pointerId);if(touches.current.size<2)pinch.current=null;
  if(drag.current&&brush&&Math.abs(brush[1]-brush[0])>=30)setView(clampView(Math.min(...brush),Math.max(...brush)));drag.current=null;setBrush(null)};
 const clock=t=>new Date(t*1000).toLocaleTimeString([],end-start<900?{hour:'2-digit',minute:'2-digit',second:'2-digit'}:{hour:'2-digit',minute:'2-digit'});
 const coarseInput=typeof matchMedia==='function'&&matchMedia('(pointer: coarse)').matches;
 return <div ref={chartRef} className={'chart'+(inDialog?' chart--dialog':'')} onMouseLeave={()=>{if(!chartRef.current?.contains(document.activeElement))setHover(null)}} onDoubleClick={inDialog?undefined:openBig}>{legend&&!inDialog&&<div className="chart-legend" aria-hidden="true">{keys.map((k,i)=><span key={k}><i style={{background:colors[i]}}/>{labels[k]||k.split('_')[0]}</span>)}</div>}{!inDialog&&clean.length>=2&&<IconButton icon="open_in_full" label="Open larger chart" className="chart__expand" onClick={openBig}/>}{big&&<Dialog headline={big} labelId={'chart-dlg-'+(big||'').replace(/\W+/g,'-')} onClose={()=>setBig(null)} actions={<Button variant="text" onClick={()=>setBig(null)}>Close</Button>}><div className="chart-legend-big">{keys.map((k,i)=><span key={k}><i style={{background:colors[i]}}/>{labels[k]||k.split('_')[0]}</span>)}<span className="chart-legend-big__unit">{unit}</span></div><Chart {...props} height={Math.min(520,Math.round(window.innerHeight*.55))} inDialog/></Dialog>}
 {inDialog&&<div className="chart-zoom"><Segmented label="Y axis" value={fit?'fit':'full'} onChange={v=>setFit(v==='fit')} options={[['full','Full scale'],['fit','Fit to data']]}/><span className="chart-zoom__btns"><IconButton icon="zoom_out" label="Zoom out" disabled={!view} onClick={()=>zoomAt((start+end)/2,2)}/><IconButton icon="zoom_in" label="Zoom in" onClick={()=>zoomAt((start+end)/2,.5)}/><IconButton icon="restart_alt" label="Reset zoom" disabled={!view&&!fit} onClick={()=>{setView(null);setFit(false)}}/></span><small className="chart-zoom__hint">{coarseInput?'Pinch to zoom · two fingers to pan':'Drag across to zoom · scroll to zoom in or out'}</small></div>}
 <svg style={inDialog?{touchAction:'none'}:undefined} viewBox={`0 0 ${W} ${H}`} role="img" tabIndex="0" aria-label={`${keys.map(k=>(labels[k]||k.split('_')[0])).join(' and ')} history in ${unit}. Latest: ${keys.map(k=>`${(labels[k]||k.split('_')[0])} ${valid(points.at(-1)?.[k])?num(points.at(-1)[k]/divisor,1):'unavailable'}`).join(', ')}. Tap or use arrow keys to inspect samples.`} onPointerMove={ev=>{if(zMove(ev))return;if(ev.pointerType!=='touch'||ev.buttons)trackPointer(ev)}} onPointerDown={ev=>{zDown(ev);if(ev.pointerType!=='touch')trackPointer(ev)}} onPointerUp={zUp} onPointerCancel={zUp} onClick={trackPointer} onKeyDown={inspectKey} onFocus={()=>setHover(1)} onBlur={()=>setHover(null)}>
 {!inDialog&&<text x="42" y="11" className="chart-label chart-unit">{unit}</text>}{[0,.5,1].map(f=><g key={f}><line x1="42" x2={W-10} y1={H-25-f*(H-50)} y2={H-25-f*(H-50)} className="chart-grid"/><text x="1" y={H-22-f*(H-50)} className="chart-label">{num(f*max,max<10?2:0)}</text></g>)}
 {drawn&&clean.length>=2&&keys.map((key,i)=>segments(key).map((part,j)=><path key={key+j} className="chart-line" d={part.map((p,n)=>`${n?'L':'M'}${x(p)},${y(p[key])}`).join(' ')} style={{stroke:colors[i]}} strokeWidth="2" strokeLinecap="round" fill="none" strokeLinejoin="round" vectorEffect="non-scaling-stroke"/>))}
 {selected&&<line x1={x(selected)} x2={x(selected)} y1="10" y2={H-25} className="chart-cursor"/>}{selected&&keys.map((k,i)=>valid(selected[k])&&<circle key={k} cx={x(selected)} cy={y(selected[k])} r="4.5" className="chart-point" style={{fill:colors[i]}}/>)}
 {brush&&<rect className="chart-brush" x={x({ts:Math.min(...brush)})} y="10" width={Math.abs(x({ts:brush[1]})-x({ts:brush[0]}))} height={H-35}/>}
 {start&&<text x="42" y={H-3} className="chart-label">{clock(start)}</text>}{inDialog&&start&&end&&<text x={42+(W-52)/2} textAnchor="middle" y={H-3} className="chart-label">{clock((start+end)/2)}</text>}{end&&<text x={W-10} textAnchor="end" y={H-3} className="chart-label">{clock(end)}</text>}
 </svg>{clean.length<2&&<div className="chart-empty"><LoadingIndicator small label="Collecting live samples"/>Collecting live samples…</div>}
 {<div className={'chart-tooltip'+(selected?'':' is-empty')} role="status" aria-live="polite">{selected&&<>{new Date(selected.ts*1000).toLocaleTimeString()} · {keys.map((k,i)=><span key={k} className="chart-tooltip__item"><i style={{background:colors[i]}}/>{(labels[k]||k.split('_')[0])} {valid(selected[k])?num(selected[k]/divisor,1):'—'} {unit} </span>)}</>}</div>}
 </div>
}
function Meter({value,label,detail,color,active}){return <div className="meter"><div><span>{label}</span><span className="mono">{detail??`${num(value)}%`}</span></div><LinearProgress value={bounded(value)} color={color} wavy={active} label={label}/></div>}
function CapacityForecast({disk,fresh}){
 const e=disk.exhaustion||{},s=fresh?e.state:'unavailable';
 const duration=t=>t<60?'under 1 minute':t<3600?`${num(Math.ceil(t/60))} minutes`:t<86400?`${num(t/3600,1)} hours`:`${num(t/86400,1)} days`;
 const title=s==='full'?'Cache full':s==='filling'?`~${duration(e.seconds)} to full`:s==='draining'?'Free space increasing':s==='flat'?'No recent space growth':s==='unavailable'?'Fill estimate unavailable':'Collecting fill-rate samples';
 return <div className={'capacity-forecast '+(s==='full'||s==='filling'&&e.seconds<1800?'urgent':'')}><strong>{title}</strong><span>{['filling','draining','flat'].includes(s)?`${s==='filling'?`${rate(e.growth_bps)} MB/s net growth · `:''}last ${num(e.window_seconds/60,1)} min`:s==='full'?'Immediate capacity limit':'Needs at least 2 minutes of continuous capacity readings'}</span>{s==='filling'&&<small>At the recent fill rate; bursts and open files can fill it sooner.</small>}{s==='full'&&<small>Less than 16 MiB available. Writes may fail.</small>}</div>
}
// Page summaries: the current condition first, detail below. Unknown stays "—" with a reason, never zero.
const B=({children})=><strong className="ov-num">{children}</strong>;
function DiskSummary({disks,scrutiny,onPick}){
 const drives=scrutiny?.drives||[],healthKnown=sourceFresh(scrutiny||{})&&drives.length>0,bad=drives.filter(d=>d.healthy===false);
 return <section className="page-summary" aria-labelledby="disk-summary-h"><div className="page-summary__head"><h2 id="disk-summary-h">Drives</h2>
  <Chip compact tone={!healthKnown?'neutral':bad.length?'error':'success'} icon={!healthKnown?'help':bad.length?'error':'check_circle'}>{!healthKnown?'Health not reported':bad.length?`${bad.length} drive ${bad.length===1?'warning':'warnings'}`:`All ${drives.length} drives healthy`}</Chip></div>
  <ul className="disk-rows">{disks.map(d=>{const fresh=d.online&&d.sampled_at&&Date.now()/1000-d.sampled_at<45;return <li key={d.id}><button type="button" className="disk-row m3-state" onClick={ev=>onPick?.(d.id,ev.currentTarget)} aria-label={`${d.name}: show history`}><span className="disk-row__icon"><Icon name="disk" size={22}/></span>
   <div className="disk-row__body"><div className="disk-row__line"><strong>{d.name}</strong><span>{valid(d.used_pct)?<><B>{num(d.used_pct,1)}%</B> used · <B>{size(d.free_bytes)}</B> free</>:d.id==='parity'?(drives.find(x=>x.name===d.device)?.cap?<><B>{drives.find(x=>x.name===d.device).cap}</B> · protects the array</>:'Protects the array'):'Capacity not reported'}</span></div>
   {valid(d.used_pct)&&<LinearProgress value={d.used_pct} label={`${d.name} used`}/>}
   <span className="disk-row__io">{fresh?<><span className="nowrap">Read <B>{rate(d.read_bps)} MB/s</B></span> · <span className="nowrap">Write <B>{rate(d.write_bps)} MB/s</B></span> · <span className="nowrap">Busy <B>{num(d.busy_pct,0)}%</B></span></>:'No live I/O reading'}</span></div><span className="disk-row__go" aria-hidden="true">History<Icon name="arrow_forward" size={18}/></span></button></li>})}</ul></section>;
}
function MediaSummary({data,activity}){
 const m=data.media||{},jf=m.jellyfin,sab=m.sabnzbd,son=m.sonarr,rd=m.readarr,a=activity?.latest||{},aFresh=a.ts&&Date.now()/1000-a.ts<45;
 const tiles=[['play','Streaming',sourceFresh(jf||{})?num(jf.active_streams):'—',sourceFresh(jf||{})?(Number(jf.active_streams)?'Jellyfin, right now':'Nothing playing'):'Jellyfin not reporting'],
  ['download','Usenet',sourceFresh(sab||{})?`${num(parseFloat(sab.speed)*1024/1e6,1)} MB/s`:'—',sourceFresh(sab||{})?`${num(sab.queue)} in the SABnzbd queue`:'SABnzbd not reporting'],
  ['cloud','Debrid',aFresh&&valid(a.rdt_bps)?`${num(a.rdt_bps/1e6,1)} MB/s`:'—',aFresh?'RdtClient download rate':'No live reading'],
  ['layers','Library queue',sourceFresh(son||{})||sourceFresh(rd||{})?num((sourceFresh(son||{})?Number(son.queue)||0:0)+(sourceFresh(rd||{})?Number(rd.queue)||0:0)):'—',sourceFresh(son||{})||sourceFresh(rd||{})?'Sonarr and Readarr, waiting':'Not reporting',catalog.find(c=>c.id==='sonarr')?.url]];
 return <section className="page-summary" aria-labelledby="media-summary-h"><div className="page-summary__head"><h2 id="media-summary-h">Right now</h2>
  <div className="page-summary__actions"><Button variant="tonal" icon="play" href="https://jellyfin.lan" target="_blank" rel="noreferrer">Jellyfin</Button><Button variant="tonal" icon="download" href={catalog.find(c=>c.id==='sabnzbd')?.url} target="_blank" rel="noreferrer">SABnzbd</Button></div></div>
  <div className="summary-tiles">{tiles.map(([ic,l,v,sub,url])=>{const inner=<><span className="summary-tile__label"><Icon name={ic} size={18}/>{l}{url&&<Icon name="external" size={14} className="summary-tile__go"/>}</span><strong>{v}</strong><small>{sub}</small></>;return url?<a key={l} className="summary-tile summary-tile--link m3-state" href={url} target="_blank" rel="noreferrer">{inner}</a>:<div key={l} className="summary-tile">{inner}</div>})}</div></section>;
}
function StorageCard({disk,drive,compact=false,capacity=false}){
 const d=disk||{};const fresh=d.online&&d.sampled_at&&Date.now()/1000-d.sampled_at<45;const color=d.id==='scratch'?'var(--md-chart-1)':d.id==='cache'?'var(--md-chart-5)':'var(--md-chart-3)';
 return <article className={'m3-card m3-card--outlined panel storage-card '+(compact?'compact':'')} style={{'--card-accent':color}}>
 <header className="panel-header"><div className="panel-title"><span className="icon-tile"><Icon name="disk"/></span><div><h3>{d.name||'Storage'}</h3><p>{d.description||'Waiting for collector'}</p></div></div><Badge state={fresh?true:null} label={fresh?'Live':'Waiting'}/></header>
 {capacity&&d.id!=='parity'?<div className="storage-values"><div><small><span className="legend" style={{background:color}}/>Used</small><strong>{valid(d.used_pct)?num(d.used_pct,1):'—'}<em>%</em></strong></div><div><small>Free</small><strong>{valid(d.free_bytes)?size(d.free_bytes):'—'}</strong></div><span className="device-label">{d.device||'—'}<br/>{d.host||''}</span></div>
 :<div className="storage-values"><div><small><span className="legend read"/>Read</small><strong>{fresh?rate(d.read_bps):'—'}<em>MB/s</em></strong></div><div><small><span className="legend write"/>Write</small><strong>{fresh?rate(d.write_bps):'—'}<em>MB/s</em></strong></div><span className="device-label">{d.device||'—'}<br/>{d.host||''}</span></div>}
 {capacity&&d.id!=='parity'&&<div className="disk-saturation"><span>Now: read <b>{fresh?rate(d.read_bps):'—'} MB/s</b></span><span>write <b>{fresh?rate(d.write_bps):'—'} MB/s</b></span></div>}
 {!compact&&!capacity&&<div className="disk-saturation"><span>Busy <b>{fresh?num(d.busy_pct,1):'—'}%</b></span><span>Latency <b>{fresh?num(d.latency_ms,1):'—'} ms</b></span><span>Queue <b>{fresh?num(d.queue_depth,1):'—'}</b></span></div>}
 {capacity&&d.id==='parity'?<p className="storage-note"><Icon name="info" size={18}/>Parity has no filesystem capacity to chart. It stores no files, only parity data that can rebuild a failed data disk{drive?.cap?`. This ${drive.cap} drive protects the array and must be at least as large as its biggest data disk.`:'.'}</p>:<Chart gapSeconds={Math.max(90,(d.bucket_seconds||10)*2.5)} points={d.history||[]} keys={capacity?['used_pct']:['read_bps','write_bps']} labels={capacity?{used_pct:'Used'}:{}} colors={capacity?[color]:undefined} unit={capacity?'%':'MB/s'} divisor={capacity?1:1e6} height={compact?160:180}/>}
 {valid(d.used_pct)?<><Meter value={d.used_pct} label={`${size(d.used_bytes)} used`} detail={`${num(d.used_pct,1)}%`} color={color}/><footer className="storage-footer"><span>{size(d.free_bytes)} free of {size(d.size_bytes)}</span><span>{fresh&&valid(d.latency_ms)?`${num(d.latency_ms,1)} ms I/O`:'10s samples'}</span></footer></>:<footer className="storage-footer"><span>{d.id==='parity'?(drive?.cap?`${drive.cap} parity drive · ${drive.model}`:'Parity drive · no files, so no used space'):'Capacity not reported'}</span><span>{drive?`${drive.healthy===false?'Health warning':'Healthy'}${valid(drive.temp)?` · ${drive.temp} °C`:''}`:'Physical device I/O'}</span></footer>}
 {d.id==='cache'&&<CapacityForecast disk={d} fresh={fresh}/>}
 </article>
}
function HostCard({name,ip,host,history,icon='server'}){const raw=host||{};const available=sourceFresh(raw);const h=available?raw:{online:raw.online};return <article className="m3-card m3-card--outlined panel host-card"><header className="panel-header"><div className="panel-title"><span className="icon-tile"><Icon name={icon}/></span><div><h3>{name}</h3><p className="mono">{ip} · {h.uptime?`up ${h.uptime}`:'No uptime reading'}</p></div></div><SourceBadge source={raw}/></header><div className="host-stats"><div><small>CPU</small><strong>{num(h.cpu,1)}<em>%</em></strong></div><div><small>Memory</small><strong>{num(h.ram_pct,1)}<em>%</em></strong></div><div><small>Temperature</small><strong>{num(h.cpu_temp)}<em>°C</em></strong></div></div><Meter value={h.ram_pct} label="Memory allocated" detail={`${num(h.ram_used_gb,1)} / ${num(h.ram_total_gb,1)} GiB`} color="var(--md-custom-indigo)"/><div className="host-capacity"><Meter value={ip.endsWith('.3')?h.rootfs_pct:h.cache_pct} label={ip.endsWith('.3')?'Root filesystem':'Cache pool'} detail={ip.endsWith('.3')?`${num(h.rootfs_used_gb,1)} / ${num(h.rootfs_total_gb,1)} GiB`:`${num(h.cache_used_gb,1)} / ${num(h.cache_total_gb,1)} GB`} color="var(--md-custom-blue)"/></div><SourceNote source={raw}/><div className="host-footer"><span><i className="tiny-dot"/>{valid(h.guests_running)&&valid(h.guests_total)?`${h.guests_running} of ${h.guests_total} guests running`:`${num(h.containers_running)} containers running`}</span><a href={ip.endsWith('.3')?'https://192.168.0.3:8006':'http://192.168.0.4'} target="_blank" rel="noreferrer">Open host <Icon name="external" size={13}/></a></div></article>}
// Task Manager-style throughput per machine: live up/down, history chart, and data moved in the last hour / 24 h.
function NetworkPanel({network,ranges,compact=false}){
 const hosts=network?.hosts||[];const bytes=b=>b==null?'—':b>=1e12?`${num(b/1e12,2)} TB`:b>=1e9?`${num(b/1e9,1)} GB`:`${num(b/1e6)} MB`;
 const mbps=v=>v==null?'—':v>=1e9?`${num(v/1e9,2)} Gbps`:`${num(v/1e6,v<1e7?1:0)} Mbps`;
 // One host's history at a time: the rows carry every host's live rate and totals, the chart follows the selected row.
 const [pick,setPick]=useState(null);const sel=hosts.find(h=>h.id===pick)||hosts[0];
 const isLive=h=>h.online&&h.sampled_at&&Date.now()/1000-h.sampled_at<30;
 return <section className="net-section"><div className="section-heading"><h2>Host traffic</h2>{!compact&&ranges}</div>
  <div className="net-rows" role="group" aria-label="Choose a host to chart">{hosts.map(h=>{const live=isLive(h),t=h.totals||{};
   return <button type="button" key={h.id} className={'net-row m3-state'+(sel?.id===h.id?' is-selected':'')} aria-pressed={sel?.id===h.id} onClick={ev=>{const el=ev.currentTarget;morph(()=>setPick(h.id),el,'.net-history')}}>
    <span className="net-row__icon"><Icon name={h.id==='unraid'?'layers':'server'} size={22}/></span>
    <span className="net-row__body"><strong>{h.name}</strong><small>{h.link_mbps?`${num(h.link_mbps)} Mbps link`:'Link speed unknown'} · 24 h ↑ {bytes(t['24h']?.tx_bytes)} ↓ {bytes(t['24h']?.rx_bytes)}</small></span>
    <span className="net-row__now">{live?<><span>↑ {mbps(h.tx_bps)}</span><span>↓ {mbps(h.rx_bps)}</span></>:<span>{h.online===false?'Unreachable':'Waiting'}</span>}</span></button>})}</div>
  {sel&&!compact&&<article className="m3-card panel net-card net-history"><header className="panel-header"><div className="panel-title"><div><h3>{sel.name}</h3><p>Send and receive · {sel.link_mbps?`${num(sel.link_mbps)} Mbps link`:'link speed unknown'}</p></div></div><Badge state={isLive(sel)?true:sel.online===false?false:null} label={isLive(sel)?'Live':sel.online===false?'Unreachable':'Waiting'}/></header>
   <Chart points={sel.history||[]} keys={['tx_bps','rx_bps']} labels={{tx_bps:'Send',rx_bps:'Receive'}} colors={['var(--md-chart-2)','var(--md-chart-1)']} unit="Mbps" divisor={1e6} height={180} legend gapSeconds={Math.max(30,(network?.bucket_seconds||5)*3)}/>
   <div className="net-totals">{[['1h','Last hour'],['24h','Last 24 h']].map(([k,l])=>{const t=sel.totals||{};return <div key={k}><small>{l}</small><span>↑ {bytes(t[k]?.tx_bytes)} · ↓ {bytes(t[k]?.rx_bytes)}</span>{t[k]&&t[k].coverage<0.95&&<em>{Math.round(t[k].coverage*100)}% sampled</em>}</div>})}</div></article>}
 </section>}
function OffsiteCard({cb,failed}){
 const now=Date.now()/1000,age=cb?.last_success?now-cb.last_success:null;
 const ago=s=>s<3600?`${Math.max(1,Math.round(s/60))} min ago`:s<172800?`${Math.round(s/3600)} h ago`:`${Math.round(s/86400)} days ago`;
 const staleSources=(cb?.sources||[]).filter(x=>x.last&&now-x.last>x.stale_h*3600);
 const state=!cb?null:cb.last_failed_start?false:(age!==null&&age>36*3600)||staleSources.length?false:cb.last_success?true:null;
 const label=!cb?(failed?'Unavailable':'Checking'):cb.running?'Uploading':cb.last_failed_start?'Last run failed':age!==null&&age>36*3600?'Stale':staleSources.length?`${staleSources.map(x=>x.label).join(', ')} stale`:cb.last_success?'Up to date':'First upload pending';
 const when=ts=>new Date(ts*1000).toLocaleString([],{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
 const nextDate=cb?.next_run?new Date(String(cb.next_run).replace(/^\w+ /,'')):null;const next=nextDate&&!isNaN(nextDate)?nextDate.toLocaleString([],{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'—';
 return <article className="m3-card m3-card--outlined panel recovery-card"><header className="panel-header"><div className="panel-title"><span className="icon-tile"><Icon name="cloud"/></span><div><h3>Off-site backup</h3><p>{cb?.destination||'Google Drive'}</p></div></div><Badge state={state} label={label}/></header>
  <strong className="recovery-value">{age!==null?ago(age):cb?.running?'In progress':'—'}</strong>
  {cb?.running&&cb.progress?.percent==null&&<div className="offsite-progress"><p className="source-note">Preparing upload: checking which files changed…</p><IndeterminateProgress label="Preparing upload"/></div>}
  {cb?.running&&cb.progress?.percent!=null&&<div className="offsite-progress"><Meter active value={cb.progress.percent} label={`Uploading ${cb.progress.phase==='proxmox'?'Proxmox backups':cb.progress.phase==='photos'?'photos':'backup'} · ${num(cb.progress.percent,1)}%`} detail={`${num(cb.progress.bytes_done/1e9)} of ${num(cb.progress.bytes_total/1e9)} GB${cb.progress.eta_s?` · about ${cb.progress.eta_s>=3600?`${Math.floor(cb.progress.eta_s/3600)} h ${Math.round(cb.progress.eta_s%3600/60)} min`:`${Math.max(1,Math.round(cb.progress.eta_s/60))} min`} left`:''}`} color="var(--md-custom-teal)"/>{cb.progress.source==='estimate'&&<p className="source-note">Estimated from data read so far.</p>}</div>}
  {cb?.sources?<ul className="offsite-sources">{cb.sources.map(x=><li key={x.tag}><span>{x.label}</span><span className={x.last&&now-x.last>x.stale_h*3600?'error':''}>{x.last?when(x.last):'No snapshot yet'}</span></li>)}</ul>:<p>Last successful upload{cb?.last_success?` · ${new Date(cb.last_success*1000).toLocaleString([],{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}`:''}</p>}
  <p>{cb?.scope||'Photos + newest Proxmox backups'} · Proxmox job next runs {next}, Unraid daily at 4 AM</p>
  {cb?.last_failed_start&&<p className="source-note error">The run that started {cb.last_failed_start} did not finish. Check /var/lib/homelab-cloud-backup/run.log on Proxmox.</p>}
  <footer className="host-footer"><span>Encrypted with restic · 7 daily, 4 weekly, 12 monthly</span></footer></article>}
function SmartDetail({s}){
 if(!s)return null;
 const ago=h=>h<48?`${h} h`:h<24*60?`${Math.round(h/24)} days`:`${Math.round(h/24/30.4)} months`;
 const kind=s.rotational?'extended':'short',t=s[`last_${kind}`],a=s.attrs||{};
 const warn=['reallocated','pending','offline_uncorrectable','reported_uncorrectable'].filter(k=>a[k]>0);
 const test=s.running_remaining_pct!=null?`${kind} self-test running, ${s.running_remaining_pct}% left`:t?`last ${kind} test ${/without error/i.test(t.result)?'passed':String(t.result??'unknown').toLowerCase()} ${ago(t.hours_ago)} ago`:`no ${kind} test recorded`;
 return <p className="drive-smart">{s.role} · {num((s.power_on_hours||0)/8766,1)} years powered on · {test}{warn.length?<span className="error"> · {warn.map(k=>`${a[k]} ${k.replace(/_/g,' ')}`).join(', ')}</span>:null}{a.crc_errors?` · ${a.crc_errors} cable (CRC) error${a.crc_errors>1?'s':''}`:''}</p>}
function RecoveryReadiness({cb,activity,failed}){
 const rt=cb?.restore_test,now=Date.now()/1000;
 const when=t=>t?new Date(t*1000).toLocaleString([],{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'—';
 const ago=t=>!t?'':now-t<5400?`${Math.max(1,Math.round((now-t)/60))} min ago`:now-t<172800?`${Math.round((now-t)/3600)} h ago`:`${Math.round((now-t)/86400)} days ago`;
 const vz=activity?.recovery?.backup?.last_success?.finished_at;
 const rows=[
  {key:'photos',label:'Photos',local:'Originals on the parity-protected array'},
  {key:'proxmox',label:'Proxmox guests',local:vz?`vzdump ${ago(vz)}`:'vzdump · unknown',localTs:vz},
  {key:'unraid',label:'Unraid appdata, flash + Private',local:'No local copy (cache SSD)'},
  {key:'configs',label:'Router + host configs',local:cb?.configs_local?`Saved ${ago(cb.configs_local)}`:'Not saved yet',localTs:cb?.configs_local}];
 const src=k=>(cb?.sources||[]).find(x=>x.tag===k),chk=k=>(rt?.checks||[]).find(x=>x.key===k);
 const overall=!rt?null:rt.ok===true;
 const label=!rt?'No restore test yet':rt.ok===true?`Restore test passed ${ago(rt.finished_at)}`:rt.ok===false?'Restore test failed':'Restore test incomplete';
 const nextDate=cb?.restore_next?new Date(String(cb.restore_next).replace(/^\w+ /,'')):null;const next=nextDate&&!isNaN(nextDate)?nextDate.toLocaleDateString([],{month:'short',day:'numeric'}):null;
 const head=<div className="section-heading"><div><h2>Recovery readiness</h2><p>Newest copy of each, and whether a test restore came back intact.</p></div></div>;
 // No backup data yet (loading or failed): say so, never 'No restore test yet' or 'No snapshot yet'.
 if(!cb)return <section>{head}<article className="m3-card m3-card--outlined panel"><p className="source-note" style={{margin:0}}>{failed?'Backup status is unavailable right now. Retrying every minute.':'Checking backup status…'}</p></article></section>;
 return <section>{head}
 <article className="m3-card m3-card--outlined panel"><header className="panel-header"><div className="panel-title"><span className="icon-tile"><Icon name="shield"/></span><div><h3>{label}</h3><p>{rt?`Checked ${when(rt.finished_at)}`:'Runs monthly'}{next?` · next ${next}`:''} · samples pulled from Google Drive and compared with the originals</p></div></div><Badge state={overall} label={!rt?'Not tested':rt.ok===true?'Restorable':rt.ok===false?'Check':'Partial'}/></header>
 <div className="readiness-table" role="table" aria-label="Recovery readiness by source"><div className="readiness-row readiness-head" role="row"><span role="columnheader">Source</span><span role="columnheader">Local copy</span><span role="columnheader">Off-site copy</span><span role="columnheader">Restore test</span></div>
 {rows.map(r=>{const o=src(r.key),c=chk(r.key),stale=o?.last&&now-o.last>o.stale_h*3600;return <div className="readiness-row" role="row" key={r.key}><strong role="cell">{r.label}</strong><span role="cell">{r.local}</span><span role="cell" className={stale?'error':''}>{o?.last?`${when(o.last)}${stale?' · stale':''}`:'No snapshot yet'}</span><span role="cell" className={c?.ok===false?'error':''} title={c?.detail||''}>{!c?'—':c.ok===true?`Passed · ${c.files} file${c.files===1?'':'s'}`:c.ok===false?'Failed':'Could not run'}{c?.detail?<small>{c.detail}</small>:null}</span></div>})}</div>
 <p className="source-note">Full application restore drills: Memos and Open WebUI were restored and checked on Oct 20, 2026 (manual). The monthly test proves files come back intact, not that every app starts.</p>
 <p className="source-note">Not backed up off-site, by choice: the media library, the Private share and the stopped VMs 100, 101, 109 and 110.</p></article></section>}
function Timeline(){
 const [data,setData]=useState(null),[err,setErr]=useState(false),[filter,setFilter]=useState('all'),[more,setMore]=useState(false);
 useEffect(()=>{let alive=true;async function get(){try{const r=await fetch('/api/events',{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error();const d=await r.json();if(alive){setData(d);setErr(false)}}catch{if(alive)setErr(true)}}get();const iv=setInterval(()=>{if(!document.hidden)get()},300000);return()=>{alive=false;clearInterval(iv)}},[]);
 const groups={all:null,updates:['update'],backups:['backup'],repairs:['repair','disk'],alerts:['alert'],changes:['deploy','change','power']};
 const icons={update:'refresh',deploy:'layers',backup:'cloud',repair:'shield',alert:'bell',power:'moon',change:'server',disk:'disk'};
 const list=(data?.events||[]).filter(e=>!groups[filter]||groups[filter].includes(e.kind));
 const merged=[],open={};for(const e of list){const m=open[e.title];if(m&&m.first-e.ts<3600){m.count++;m.first=e.ts}else{const n={...e,count:1,first:e.ts};open[e.title]=n;merged.push(n)}}  // same event within an hour merges even with others in between
 const shown=more?merged:merged.slice(0,25);const byDay=[];for(const e of shown){const d=new Date(e.ts*1000).toLocaleDateString([],{weekday:'long',month:'short',day:'numeric'});if(!byDay.length||byDay[byDay.length-1].day!==d)byDay.push({day:d,items:[]});byDay[byDay.length-1].items.push(e)}
 const time=t=>new Date(t*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
 return <section><div className="section-heading"><div><h2>What changed</h2><p>Updates, deploys, backups, repairs, alerts and power events from the last {data?.days||7} days. Shows what happened, not why; something near a problem may be unrelated.</p></div><div className="segments timeline-filter" role="group" aria-label="Filter changes">{Object.keys(groups).map(k=><button key={k} className={filter===k?'selected':''} aria-pressed={filter===k} onClick={()=>setFilter(k)}>{k[0].toUpperCase()+k.slice(1)}</button>)}</div></div>
 <article className="m3-card m3-card--outlined panel timeline-panel">{!data&&!err&&<p className="muted">Collecting events from Proxmox, Unraid and Homepage…</p>}{err&&!data&&<p className="source-note error">Timeline unavailable. The collector runs on Proxmox; try again in a minute.</p>}
 {data&&!merged.length&&<p className="muted">Nothing in this category in the last {data.days} days.</p>}
 {byDay.map(g=><div key={g.day} className="timeline-day"><h3>{g.day}</h3><ul>{g.items.map((e,i)=><li key={e.ts+e.title+i} className={e.ok===false?'bad':''}><span className="timeline-time mono">{time(e.ts)}</span><span className="timeline-icon"><Icon name={icons[e.kind]||'activity'} size={15}/></span><div><strong>{e.title}{e.count>1?` ×${e.count}`:''}</strong>{e.count>1&&<small>{time(e.first)}–{time(e.ts)}</small>}{e.detail&&<p>{e.detail}</p>}</div><span className="timeline-source">{e.source}</span></li>)}</ul></div>)}
 {merged.length>25&&<button className="m3-btn m3-btn--text m3-state text-button" onClick={()=>setMore(!more)}>{more?'Show fewer':`Show all ${merged.length}`}</button>}</article></section>}
function PiCard({pi,observedAt}){
 const fresh=pi?.online&&observedAt&&Date.now()/1000-observedAt<300,h=fresh?pi:{};
 const up=h.uptime_s?(h.uptime_s>=86400?`${Math.floor(h.uptime_s/86400)}d ${Math.floor(h.uptime_s%86400/3600)}h`:`${Math.floor(h.uptime_s/3600)}h`):null;
 const kuma=(h.containers||[]).find(c=>c.name==='uptime-kuma'),sync=(h.containers||[]).find(c=>c.name==='nebula-sync');
 const state=c=>!c?'—':c.state==='running'?'Running':c.state;
 return <article className="m3-card m3-card--outlined panel host-card pi-card"><header className="panel-header"><div className="panel-title"><span className="icon-tile"><Icon name="server"/></span><div><h3>Orange Pi Zero 2W</h3><p className="mono">192.168.0.10 · {up?`up ${up}`:'No uptime reading'}</p></div></div><Badge state={pi?fresh?true:pi.online===false?false:null:null} label={!pi?'Checking':fresh?'Online':pi.online===false?'Unreachable':'Stale'}/></header>
  <div className="host-stats"><div><small>CPU</small><strong>{num(h.cpu,1)}<em>%</em></strong></div><div><small>Memory</small><strong>{num(h.ram_pct,1)}<em>%</em></strong></div><div><small>Temperature</small><strong>{num(h.temp_c)}<em>°C</em></strong></div></div>
  <Meter value={h.ram_pct} label="Memory used" detail={`${num(h.ram_used_mb)} / ${num(h.ram_total_mb)} MiB`} color="var(--md-custom-indigo)"/>
  <div className="host-capacity"><Meter value={h.disk_pct} label="SD card" detail={`${num(h.disk_pct)}% used`} color="var(--md-custom-blue)"/></div>
  <details className="host-more"><summary>Services and system</summary><ul className="pi-roles"><li><span>Pi-hole replica (DNS)</span><b className={h.pihole==='active'?'ok':'warn'}>{h.pihole==='active'?'Answering':h.pihole||'—'}</b></li><li><span>Uptime Kuma (alerts)</span><b className={kuma?.state==='running'?'ok':'warn'}>{state(kuma)}</b></li><li><span>nebula-sync (copies Pi-hole settings from .11)</span><b className={sync?.state==='running'?'ok':'warn'}>{state(sync)}</b></li>{h.failed_units?.length>0&&<li><span>Failed services</span><b className="warn">{h.failed_units.join(', ')}</b></li>}</ul>
  <p className="source-note mono">{h.os||'—'} · kernel {h.kernel||'—'}</p></details>
  <div className="host-footer"><span><i className="tiny-dot"/>Second DNS server, uptime alerts</span><span className="speed-links"><a href="http://192.168.0.10/admin" target="_blank" rel="noreferrer">Pi-hole <Icon name="external" size={13}/></a><a href="http://192.168.0.10:3001" target="_blank" rel="noreferrer">Uptime Kuma <Icon name="external" size={13}/></a></span></div></article>}
function ServiceCard({service:s,data,favorite,onFavorite,row}){const state=serviceState(s,data);const stateLabel=state===null?'Unmonitored':state==='unknown'?'No reading':state==='stale'?'Stale':state?'Online':'Unreachable';let detail=s.ip;if(s.id==='readarr'&&sourceFresh(data.media?.readarr))detail=`${num(data.media.readarr.books)} books · ${num(data.media.readarr.queue)} queued`;if(s.id==='sonarr'&&sourceFresh(data.media?.sonarr))detail=`${num(data.media.sonarr.series)} series · ${num(data.media.sonarr.queue)} queued`;if(s.id==='jellyfin'&&sourceFresh(data.media?.jellyfin))detail=`${num(data.media.jellyfin.active_streams)} active streams`;if(s.id==='sabnzbd'&&sourceFresh(data.media?.sabnzbd))detail=`${num(parseFloat(data.media.sabnzbd.speed)*1024/1e6,1)} MB/s · ${num(data.media.sabnzbd.queue)} queued`;return <article className={'service-card'+(row?' service-card--row':'')} data-group={s.group} style={{'--service-color':s.color}}><a className="service-link" href={s.url} target="_blank" rel="noreferrer"><span className="service-icon"><Icon name={s.icon} size={23}/><i className={'service-dot '+(state===true?'online':state===false?'offline':'unknown')+(state==='stale'?' stale':'')} role="img" aria-label={stateLabel} title={stateLabel}/></span><div><h3>{s.name}{state!==true&&<span className={'service-state service-state--'+(state===false?'offline':state==='stale'?'stale':'unmonitored')} aria-hidden="true"><Icon name={state===false?'link_off':state==='stale'?'sync_problem':'help'} size={14}/>{stateLabel}</span>}</h3><p>{s.desc}</p><span className={'service-address'+(detail!==s.ip?' is-live':'')}>{detail}</span></div><Icon name="external" size={15} className="launch-arrow"/></a><button className={'pin-button '+(favorite?'pinned':'')} aria-label={`${favorite?'Unpin':'Pin'} ${s.name}`} aria-pressed={favorite} onClick={()=>onFavorite(s.id)} title={`${favorite?'Unpin':'Pin'} ${s.name}`}><Icon name="star" size={15}/></button></article>}
const activityLabels={sab_bps:'SABnzbd',rdt_bps:'RdtClient',read_bps:'Scratch read',write_bps:'Scratch write',busy_pct:'Scratch busy',pressure_pct:'Host I/O pressure',await_ms:'I/O latency',queue_depth:'Queue depth'};
function ActivityPanel({activity,ranges}){
 const [metric,setMetric]=useState('throughput');const a=activity||{},r=a.latest||{},fresh=r.ts&&Date.now()/1000-r.ts<45;
 const modes={throughput:{keys:['sab_bps','rdt_bps','read_bps','write_bps'],colors:['var(--md-chart-4)','var(--md-chart-5)','var(--md-chart-1)','var(--md-chart-2)'],unit:'MB/s',divisor:1e6},busy:{keys:['busy_pct','pressure_pct'],colors:['var(--md-chart-1)','var(--md-chart-4)'],unit:'%',divisor:1},latency:{keys:['await_ms'],colors:['var(--md-chart-3)'],unit:'ms',divisor:1},queue:{keys:['queue_depth'],colors:['var(--md-chart-5)'],unit:'I/O',divisor:1}};const spec=modes[metric];
 return <section className="activity-section"><div className="section-heading"><h2>Downloads & scratch disk</h2>{ranges}</div><article className="m3-card m3-card--outlined panel activity-panel"><header className="activity-toolbar"><div className="segments" role="group" aria-label="Activity metric">{[['throughput','Rates'],['busy','Busy / pressure'],['latency','Latency'],['queue','Queue']].map(([key,label])=><button key={key} className={metric===key?'selected':''} aria-pressed={metric===key} onClick={()=>setMetric(key)}>{label}</button>)}</div><Badge state={fresh?true:null} label={fresh?'Live':'Unavailable'}/></header><div className="activity-readings">{[['sab_bps','SABnzbd','MB/s',1e6],['rdt_bps','RdtClient','MB/s',1e6],['busy_pct','Scratch busy','%',1],['await_ms','I/O latency','ms',1],['queue_depth','Queue depth','I/O',1],['pressure_pct','Host I/O pressure','%',1]].map(([key,label,unit,divisor])=><div key={key}><small>{label}</small><strong>{fresh&&valid(r[key])?num(r[key]/divisor,1):'—'}<em>{unit}</em></strong></div>)}</div><div className="activity-legend">{spec.keys.map((key,i)=><span key={key}><i style={{background:spec.colors[i]}}/>{activityLabels[key]}</span>)}</div><Chart points={a.history||[]} {...spec} labels={activityLabels} height={220} gapSeconds={Math.max(90,(a.bucket_seconds||10)*2.5)}/><details className="storage-explainer"><summary>Metric details</summary><p>~10s samples · 24h server history. Downloads are application rates; scratch is physical disk I/O. Host I/O pressure is the share of time at least one Proxmox task waited for I/O, averaged over 10 seconds. Latency is average completion time; queue depth is average outstanding I/O. Idle latency is unavailable. Gaps mean unavailable readings. Matching activity alone does not prove a bottleneck.</p><p>{a.history_since?`History starts ${new Date(a.history_since*1000).toLocaleString()}.`:'Waiting for samples.'}</p></details></article></section>
}
const gpuLabels={compute_pct:'Compute',render_pct:'Render',video_pct:'Video',copy_pct:'Copy',enhance_pct:'Video enhance',vram_used_mib:'VRAM used',temperature_c:'Temperature'};
const gpuColors=['var(--md-chart-1)','var(--md-chart-3)','var(--md-chart-5)','var(--md-chart-4)','var(--md-chart-2)'];
const STRATA_MODEL='swift-1.5-iq2_xs';
const modelProfiles={
 'gemma-4-26b-a4b':{role:'Chat',speed:168.3}, // measured 22 Oct 2026: 48K context, 100% CUDA0 (186 tok/s prefill)
 'qwen3.6-35b-moe':{role:'General use',speed:82.1}, // measured 22 Oct 2026: 48K context, 100% CUDA0
 'qwen3.8-27b':{role:'Reasoning',speed:54.6}, // measured 21-22 Oct 2026: UD-IQ4_XS 48K ctx + ngram-mod (was 16.3 tok/s)
 'swift-1.5-iq2_xs':{role:'Strata · quick answers, short prompts',speed:67.3}, // 30 Oct 2026: Swift 1.5 IQ2_XS on Strata, strata-bench (54/58 vs the 27B's 55/58); prompt reading ~210 tok/s, so long prompts are slow
 'qwen3.8-27b-uncensored':{role:'Uncensored · abliterated',speed:51.4} // 27 Oct 2026: Heretic ARA IQ4_XS 3.0 (13.99 GB, all on GPU), 51.38 tok/s over 1,024 tokens, ngram-mod 43% acceptance; replaced the 15.57 GB OrcaRouter build that spilled past 16 GB VRAM
};
function inlineMd(text,key){
 const out=[];const re=/\*\*([^*\n]+?)\*\*|`([^`\n]+)`/g;let last=0,m,i=0;
 while((m=re.exec(text))){if(m.index>last)out.push(text.slice(last,m.index));out.push(m[1]!==undefined?<strong key={key+'b'+i++}>{m[1]}</strong>:<code key={key+'c'+i++}>{m[2]}</code>);last=re.lastIndex}
 if(last<text.length)out.push(text.slice(last));return out}
function SafeMarkdown({text}){
 const blocks=[];let list=null;
 const flush=()=>{if(list){blocks.push(list);list=null}};
 String(text).split('\n').forEach((raw,n)=>{const line=raw.trimEnd();
  const bullet=line.match(/^\s*[-*•]\s+(.*)$/),num=line.match(/^\s*\d+[.)]\s+(.*)$/),head=line.match(/^\s*#{1,6}\s+(.*)$/);
  if(bullet||num){const kind=bullet?'ul':'ol';if(!list||list.kind!==kind){flush();list={kind,items:[]}}list.items.push((bullet||num)[1]);return}
  flush();
  if(!line.trim()){blocks.push({kind:'gap'});return}
  if(head){blocks.push({kind:'h',text:head[1].replace(/\*\*/g,'')});return}
  const prev=blocks[blocks.length-1];if(prev&&prev.kind==='p')prev.lines.push(line);else blocks.push({kind:'p',lines:[line]});
 });flush();
 return <>{blocks.filter(b=>b.kind!=='gap').map((b,i)=>b.kind==='h'?<h4 key={i}>{b.text}</h4>
  :b.kind==='p'?<p key={i}>{b.lines.map((l,k)=><React.Fragment key={k}>{k>0&&<>{'\n'}<br/></>}{inlineMd(l,i+'-'+k)}</React.Fragment>)}</p>
  :React.createElement(b.kind,{key:i},b.items.map((it,k)=><li key={k}>{inlineMd(it,i+'-'+k)}</li>)))}</>}
async function postAction(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Action':'1'},body:JSON.stringify(body||{})});let d={};try{d=await r.json()}catch{}return {ok:r.ok&&d.ok!==false,...d}}
// One power control for the gaming PC: shows its state and Wake (one tap) or Sleep (confirm; second confirm if the GPU is busy).
function PcPowerControl({pc,ask,notify,compact=false}){
 const online=sourceFresh(pc),known=online||pc?.online===false;
 const [sending,setSending]=useState(false);
 // Launcher shortcut "Wake PC" calls this through window.__homelabShortcut (it goes through the page, so the
 // same-origin action guard still applies). Ref, not a dependency: the clock re-renders App every second.
 const wakeRef=useRef();wakeRef.current=()=>{if(online){notify('The gaming PC is already on.');return}wake()};
 useEffect(()=>{const f=()=>wakeRef.current();window.__homelabWake=f;return()=>{if(window.__homelabWake===f)window.__homelabWake=null}},[]);
 async function wake(){setSending(true);try{const d=await postAction('/api/action/wake-pc');notify(d.ok?'Wake signal sent. From sleep, the models are back in about 30 s.':'The wake signal could not be sent.')}catch{notify('The wake signal could not be sent.')}finally{setSending(false)}}
 function sleep(force){return async()=>{try{const d=await postAction('/api/action/sleep-pc',{force});
  if(d.ok){ask(null);notify('The PC shows a 60-second warning, then sleeps.')}
  else if(d.busy)ask({type:'pc',icon:'moon',title:`The GPU is busy (${d.gpu}%)`,text:'A game or model may be running. Put the PC to sleep anyway?',confirmLabel:'Sleep anyway',run:sleep(true)});
  else{ask(null);notify(d.error||'The PC did not accept the request.')}}catch{ask(null);notify('Could not reach the gaming PC.')}}}
 const askSleep=()=>ask({type:'pc',icon:'moon',title:'Put the gaming PC to sleep?',text:'It shows a 60-second warning on screen first. Wake PC brings it back.',confirmLabel:'Sleep PC',run:sleep(false)});
 return <div className={'pc-power'+(compact?' compact':'')} role="group" aria-label="Gaming PC power">
  <span className={'pc-dot '+(online?'on':known?'off':'unknown')}/><span className="pc-label"><strong>Gaming PC</strong><small>{online?'Online':known?'Offline or asleep':'Checking…'}</small></span>
  {online?<button className="m3-btn m3-btn--tonal m3-state secondary-button" onClick={askSleep}><Icon name="moon" size={16}/>Sleep PC</button>
   :<button className="m3-btn m3-btn--tonal m3-state secondary-button" disabled={sending||!known} onClick={wake}><Icon name="power_settings_new" size={16}/>{sending?'Sending…':'Wake PC'}</button>}</div>}
function LocalAI({ai,gpu,compact=false,onOpen,power,onNavigate}){
 // Strata is a second engine on the same GPU; the GPU broker loads whichever engine a chat asks for, so it is listed as one more model.
 const strata=ai?.strata||{},strataLive=sourceFresh(strata);
 const web=ai?.webui||{},pc=ai?.desktop||{},live=sourceFresh(pc),webLive=sourceFresh(web);
 const llamaItems=(pc.models||(live?[]:Object.keys(modelProfiles).filter(id=>id!==STRATA_MODEL).map(id=>({id,state:'unknown',context_tokens:null})))).filter(m=>m.id!==STRATA_MODEL);
 const items=[...llamaItems,{id:strata.model||STRATA_MODEL,engine:'strata',state:strataLive?(strata.loaded?'loaded':'unloaded'):'unknown',context_tokens:strata.context_tokens??null}];
 const fresh=m=>m.engine==='strata'?strataLive:live,canChat=m=>fresh(m)&&webLive;
 const pcStatus=pc.online===false?'Gaming PC unavailable':'Waiting for gaming PC status';
 const labels={loaded:'Loaded',unloaded:'Loads on demand',loading:'Loading',unloading:'Unloading',sleeping:'Idle · unloaded',failed:'Failed',unknown:'Unknown'};
 const loaded=items.filter(m=>fresh(m)&&m.state==='loaded'),others=items.filter(m=>!(fresh(m)&&m.state==='loaded'));
 const chat=id=>`http://192.168.0.16:3000/?model=${encodeURIComponent(id)}`;
 const speed=m=>{const profile=modelProfiles[m.id];const isLiveGen=pc?.is_active&&pc?.active_model===m.id&&valid(pc?.active_speed);return isLiveGen?`${Number(pc.active_speed).toFixed(1)} tok/s ${pc?.is_recent?'recent':'live'}`:valid(profile?.speed)?`${profile.speed.toFixed(1)} tok/s recorded`:null};
 const facts=m=>[speed(m),valid(m.context_tokens)?`${num(m.context_tokens)} tokens context${!fresh(m)?' · last seen':''}`:null].filter(Boolean).join(' · ');
 const modelRow=m=><article className="m3-card panel ai-model ai-model--row" key={m.id}><div className="ai-model-title"><div><h3>{PC_MODEL_NAMES[m.id]||m.id}</h3><p className="ai-role">{[modelProfiles[m.id]?.role,facts(m)].filter(Boolean).join(' · ')}</p></div><Badge state={fresh(m)&&m.state==='failed'?false:null} label={fresh(m)?(labels[m.state]||'Unknown'):'Unavailable'}/></div>
  {canChat(m)?<a className="m3-btn m3-btn--text m3-state text-button" href={chat(m.id)} target="_blank" rel="noreferrer">Open chat <Icon name="external" size={16}/></a>:<button className="m3-btn m3-btn--text m3-state text-button" disabled>{fresh(m)?'Web UI unavailable':'PC unavailable'}</button>}</article>;
 if(compact)return <section className="local-ai-section"><div className="section-heading"><h2>Local AI</h2><button className="m3-btn m3-btn--text m3-state text-button" onClick={onOpen}>View models <Icon name="arrow" size={16}/></button></div>{<div className="ai-summary"><span>{live?`${items.length} models · ${items.filter(m=>m.state==='loaded').length} loaded`:'Desktop models currently unavailable'}</span>{live&&items.filter(m=>m.state==='loaded').map(m=>{const profile=modelProfiles[m.id];const isLiveGen=pc?.is_active&&pc?.active_model===m.id&&valid(pc?.active_speed);const isRecent=pc?.is_recent;return <span key={m.id} className="ai-summary-model"><a href={`http://192.168.0.16:3000/?model=${encodeURIComponent(m.id)}`} target="_blank" rel="noreferrer">{m.id} <Icon name="external" size={14}/></a>{isLiveGen?<span className={`ai-badge-live ${isRecent?'recent':'active'}`}><i/>{Number(pc.active_speed).toFixed(1)} tok/s {isRecent?'recent':'live'}</span>:valid(profile?.speed)&&<span className="ai-badge-benchmark">{profile.speed.toFixed(1)} tok/s</span>}</span>})}</div>}</section>;
 return <section className="local-ai-section">
  <div className="ai-tasks">
   <article className="ai-task ai-task--chat" aria-labelledby="ai-chat-heading">
    <div className="ai-task__head"><span className="ai-task__icon"><Icon name="forum" size={24}/></span><div><h2 id="ai-chat-heading">Chat with a model</h2><p>Desktop models on the gaming PC, in Open WebUI</p></div></div>
    {loaded.map(m=><div className="ai-model is-featured" key={m.id}><div className="ai-model-title"><div><span className="ai-featured__badge">Loaded now</span><h3>{PC_MODEL_NAMES[m.id]||m.id}</h3>{modelProfiles[m.id]&&<p className="ai-role">{modelProfiles[m.id].role}</p>}<p className="ai-facts">{facts(m)}</p></div></div>
     {canChat(m)?<a className="m3-btn m3-btn--filled m3-state" href={chat(m.id)} target="_blank" rel="noreferrer">Open chat <Icon name="external" size={18}/></a>:<button className="m3-btn m3-btn--filled m3-state" disabled>Web UI unavailable</button>}</div>)}
    {live&&!loaded.length&&<p className="ai-task__note">No model is loaded. Choosing one in Open WebUI loads it (about 30 s).</p>}
    {!live&&<p className="ai-task__note ai-offline" role="status"><strong>{pcStatus}.</strong> It may be off, asleep, or its model server stopped. Wake it to chat; status checks resume automatically.</p>}
    {live&&others.length>0&&<details className="ai-others"><summary>Other models <span className="count">{others.length}</span><Icon name="down" size={20} className="ai-others__chevron"/></summary><div className="ai-model-list">{others.map(modelRow)}</div><p className="source-note">One desktop model loads at a time. Opening a chat with another model switches to it (Strata takes about 2 minutes).</p></details>}
    <div className="ai-task__actions">{power}<span className="ai-task__links"><a className="m3-btn m3-btn--text m3-state text-button ai-launch" href="http://192.168.0.16:3000/" target="_blank" rel="noreferrer">Open WebUI <Icon name="external" size={16}/></a><a className="m3-btn m3-btn--text m3-state text-button" href="https://opencode.lan" target="_blank" rel="noreferrer">OpenCode <Icon name="external" size={16}/></a><a className="m3-btn m3-btn--text m3-state text-button" href="https://strata.lan" target="_blank" rel="noreferrer">Strata <Icon name="external" size={16}/></a></span></div>
    <SourceNote source={web}/>
   </article>
   <AskQwen gpu={gpu} ai={ai} task/>
  </div>
  {!live&&items.length>0&&<details className="ai-history"><summary><Icon name="schedule" size={20}/>Recorded benchmarks · {items.length} {items.length===1?'model':'models'} <span className="ai-history__hint">from the last time the PC was online</span><Icon name="down" size={20} className="ai-history__chevron"/></summary><div className="ai-model-list">{items.map(modelRow)}</div></details>}
  {!items.length&&<div className="m3-card panel ai-empty">{live?'No models reported.':'Waiting for model status.'}</div>}
  <div className="m3-card panel ai-local-model"><div><h3>Nightly log review</h3><p>Unraid · 3 AM review on Gemma 4 E4B (A310) · scans host warning logs after backups finish</p></div><button className="m3-btn m3-btn--tonal m3-state secondary-button" onClick={()=>onNavigate?.('infra')}>Latest review <Icon name="arrow" size={16}/></button></div>
  <details className="storage-explainer"><summary>Connection details</summary><p>Model status: 192.168.0.12:8080 · llama.cpp · {live?'Online':'Unavailable'}; Strata 192.168.0.12:8088 · {strataLive?'Online':'Unavailable'}. Chats go through the GPU broker (192.168.0.2), which gives the GPU to whichever engine is asked for. {pc.last_success_at?`Last read ${new Date(pc.last_success_at*1000).toLocaleTimeString()}.`:''} Context values show server settings. Live speeds are a rolling 5-second average during generation; recorded speeds are earlier benchmarks. Ask about the homelab runs Gemma 4 E4B on the Unraid A310 (~14–18 tok/s, loads in ~12 s), separately from the gaming PC.</p><SourceNote source={pc}/></details>
 </section>
}

// Gaming PC GPU (RTX 5070 Ti) from pc/gpu_metrics.py, read only while this page is open (2 s ping to /api/ai/live).
const pcGpuColors=['var(--md-chart-1)'],pcGpuMetrics={power:['power_w','Power','W'],temperature:['temperature_c','Temperature','°C'],load:['utilization_pct','GPU load','%'],vram:['vram_used_mib','VRAM','MiB']};
// Request history (30 Oct 2026): one row per generation request through the GPU broker, speeds from the engines' own timings.
const engineName={llama:'llama-server',strata:'Strata'},swapName={'unload-strata':'Unloaded Strata','unload-llama':'Unloaded llama','load-strata':'Loaded Strata','unload-llama+load-strata':'Swapped to Strata',game:'Refused: game running',failed:'Switch failed'};
const secs=v=>valid(v)?(v<10?`${num(v,1)} s`:`${num(v)} s`):'—';
function AiHistoryPanel({history:h}){
 const engines=Object.entries(h?.engines||{}),recent=h?.recent||[];
 return <section className="ai-history"><div className="section-heading"><div><h2>Request history</h2><p>Every request through the GPU broker (Open WebUI, OpenCode, Ask). Speeds are the engines' own figures; prompt reading counts prompts of {num(h?.min_prompt||500)}+ tokens.</p></div></div>
  <article className="m3-card m3-card--outlined panel">
   {!engines.length&&!recent.length?<p className="m3-empty-note"><Icon name="monitoring" size={20}/>No requests recorded yet. They appear here as soon as something uses a local model.</p>:<>
   <h3 className="energy-sub">Last 7 days</h3>
   <div className="table-scroll"><table className="ai-hist"><thead><tr><th>Engine</th><th>Requests</th><th>Writing</th><th>Prompt reading</th><th>First output</th><th>Model swaps</th><th>Draft accepted</th></tr></thead>
    <tbody>{engines.map(([e,x])=><tr key={e}><th scope="row">{engineName[e]||e}</th><td>{num(x.requests)}{x.failed?<span className="error"> · {x.failed} failed</span>:''}</td><td>{valid(x.gen_tps)?`${num(x.gen_tps)} tok/s`:'—'}</td><td>{valid(x.prompt_tps)?`${num(x.prompt_tps)} tok/s`:'—'}</td><td>{secs(x.ttfb_s)}</td><td>{x.swaps?`${x.swaps} · ${secs(x.swap_s)} each`:'None'}</td><td>{valid(x.draft_pct)?`${num(x.draft_pct)}%`:'—'}</td></tr>)}</tbody></table></div>
   <h3 className="energy-sub">Recent requests</h3>
   <div className="table-scroll"><table className="ai-hist"><thead><tr><th>Time</th><th>Model</th><th>Swap</th><th>First output</th><th>Prompt</th><th>Answer</th><th>Total</th></tr></thead>
    <tbody>{recent.map((r,i)=><tr key={r.ts+'-'+i} className={r.ok?'':'is-failed'}><td>{new Date(r.ts*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}</td><td>{r.model||engineName[r.engine]||r.engine}</td><td>{r.swap?`${swapName[r.swap]||r.swap}${valid(r.swap_s)?` · ${secs(r.swap_s)}`:''}`:'—'}</td><td>{r.ok?secs(r.ttfb_s):<span className="error">{r.status?`Error ${r.status}`:'No answer'}</span>}</td><td>{valid(r.prompt_n)?`${num(r.prompt_n)} tok${valid(r.prompt_tps)?` · ${num(r.prompt_tps)}/s`:''}`:'—'}</td><td>{valid(r.gen_n)?`${num(r.gen_n)} tok${valid(r.gen_tps)?` · ${num(r.gen_tps)}/s`:''}`:'—'}</td><td>{secs(r.wall_s)}</td></tr>)}</tbody></table></div></>}
   <details className="storage-explainer"><summary>About these numbers</summary><p>First output is the wait from sending the request (after any model swap) to the first bytes of the answer, which is mostly prompt reading. Prompt reading and writing speeds come from the engine's own timings. A swap is the GPU broker unloading one engine and loading the other. Kept 90 days.{h?.history_since?` Records start ${new Date(h.history_since*1000).toLocaleString()}.`:''}</p></details>
  </article></section>}
function PcGpuPanel({live}){
 const [metric,setMetric]=useState('power');const g=live?.gpu?.latest||null;const age=g&&valid(g.observed_at)?Date.now()/1000-g.observed_at:null;const fresh=age!==null&&age>=-5&&age<=10;
 const v=k=>g&&valid(g[k])?g[k]:null;const name=(g?.name||'').replace(/^NVIDIA (GeForce )?/,'')||'Gaming PC GPU';
 const speed=live?.is_active&&valid(live.active_speed)?live.active_speed:null;const [key,label,unit]=pcGpuMetrics[metric];
 const status=fresh?'Live':g?`Last reading ${new Date(g.observed_at*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}`:'Unavailable';
 const tiles=[['power_w','Power','W',1],['temperature_c','Temperature','°C',0],['utilization_pct','GPU load','%',0],['fan_pct','Fan','%',0],['clock_mhz','Clock','MHz',0]];
 return <section className="gpu-section pc-gpu"><div className="section-heading"><h2>Gaming PC GPU</h2></div><article className="m3-card m3-card--outlined panel gpu-panel">
  <header className="panel-header"><div className="panel-title"><span className="icon-tile"><Icon name="activity"/></span><div><h3>{name}</h3><p>{valid(v('power_limit_w'))?`${num(v('power_limit_w'))} W limit · `:''}updates while this page is open</p></div></div><Badge state={fresh?true:null} label={status}/></header>
  {g?<><div className={'gpu-readings'+(fresh?'':' is-stale')}>{tiles.map(([k,l,u,dp])=><div key={k}><small>{l}</small><strong>{num(v(k),dp)}<em>{u}</em></strong></div>)}<div><small>Generation</small><strong>{speed!==null?num(speed,1):'—'}<em>tok/s</em></strong></div></div>
  <div className="gpu-memory"><Meter label="VRAM" value={valid(v('vram_used_mib'))&&v('vram_total_mib')>0?100*v('vram_used_mib')/v('vram_total_mib'):null} detail={`${num(valid(v('vram_used_mib'))?v('vram_used_mib')/1024:null,1)} / ${num(valid(v('vram_total_mib'))?v('vram_total_mib')/1024:null,1)} GiB`} color="var(--md-custom-purple)"/></div>
  <div className="gpu-toolbar"><div className="segments" role="group" aria-label="Gaming PC GPU metric">{Object.entries(pcGpuMetrics).map(([k,[,l]])=><button key={k} className={metric===k?'selected':''} aria-pressed={metric===k} onClick={()=>setMetric(k)}>{l}</button>)}</div></div>
  {(live?.gpu?.history||[]).length>1?<Chart points={live.gpu.history} keys={[key]} colors={pcGpuColors} labels={{[key]:label}} unit={unit} divisor={1} height={200} maxValue={key==='power_w'?v('power_limit_w'):key==='vram_used_mib'?v('vram_total_mib'):null}/>:<p className="source-note">The chart fills in while this page is open.</p>}</>
  :<p className="source-note">{live?.gpu?.error?'No reading from the gaming PC. It may be off or asleep, or the GPU metrics task isn’t installed (pc/Install-GpuMetrics.ps1).':'Waiting for the first reading…'}</p>}
 </article></section>}
// Energy & cost (30 Oct 2026): watts from host energy counters plus labelled estimates; kWh and cost from hourly totals.
const money=v=>valid(v)?`$${Number(v).toFixed(Number(v)<10?2:0)}`:'—';
function EnergyPanel({energy}){
 const e=energy||{};
 const fresh=valid(e?.observed_at)&&Date.now()/1000-e.observed_at<=60,devs=fresh?e.devices||[]:[];
 const cost=!!e?.rate_per_kwh,days=e?.days||[],peak=Math.max(0,...days.map(d=>d.kwh||0));
 // Always 30 day slots (oldest first), so the first days of history are thin bars, not one full-width block.
 const byDate=Object.fromEntries(days.map(d=>[d.date,d])),slots=Array.from({length:30},(_,i)=>{const t=new Date();t.setDate(t.getDate()-29+i);const k=`${t.getFullYear()}-${String(t.getMonth()+1).padStart(2,'0')}-${String(t.getDate()).padStart(2,'0')}`;return byDate[k]||{date:k,kwh:null}});
 const est=fresh&&valid(e.total_w)&&valid(e.measured_w)?e.total_w-e.measured_w:null;
 const tiles=[['Now',fresh?num(e.total_w):'—','W',fresh&&valid(e.measured_w)?`${num(e.measured_w)} W measured`:'No current reading'],
  ['Per day at this rate',cost?money(e.per_day_at_now?.cost):num(e.per_day_at_now?.kwh,1),cost?'':'kWh',cost?`${num(e.per_day_at_now?.kwh,1)} kWh`:'Set a rate to see cost'],
  ['Today so far',cost?money(e?.today?.cost):num(e?.today?.kwh,2),cost?'':'kWh',cost?`${num(e?.today?.kwh,2)} kWh`:'Since midnight'],
  ['This month',cost?money(e?.month?.cost):num(e?.month?.kwh,1),cost?'':'kWh',valid(e?.month?.projected_kwh)?`On track for ${cost?money(e.month.projected_cost):num(e.month.projected_kwh)+' kWh'}`:'Projection after 6 hours of readings']];
 return <section className="energy-section"><div className="section-heading"><div><h2>Energy & cost</h2><p>Estimated draw at the wall for the always-on gear and the gaming PC.{cost?` Priced at ${(e.rate_per_kwh*100).toFixed(1)}¢/kWh${e.rate_label?` (${e.rate_label})`:''}.`:''}</p></div></div>
  <article className="m3-card m3-card--outlined panel energy-panel"><header className="panel-header"><div className="panel-title"><span className="icon-tile"><Icon name="bolt"/></span><div><h3>Homelab power</h3><p>{fresh?`Updated ${new Date(e.observed_at*1000).toLocaleTimeString()}`:'Waiting for the first reading'}</p></div></div><Badge state={!fresh?null:e.complete?true:'warn'} label={!fresh?'No reading':e.complete?'Live':'Partial'}/></header>
   <div className="gpu-readings energy-readings">{tiles.map(([label,value,unit,sub])=><div key={label}><small>{label}</small><strong>{value}{unit&&<em>{unit}</em>}</strong><small>{sub}</small></div>)}</div>
   <ul className="m3-list energy-devices" aria-label="Power by device">{devs.map(d=><ListItem key={d.id} icon={d.id==='pc'?'sports_esports':d.id==='router'?'wifi':d.id==='orangepi'?'dns':'hard_drive'} headline={d.name} supporting={valid(d.watts)?`${d.detail}${d.measured_w>0?` · ${num(d.measured_w,1)} W measured`:' · estimate'}`:`${d.detail} · no current reading`} trailing={<span className="mono">{valid(d.watts)?`${num(d.watts)} W`:'—'}</span>} className={d.state==='unknown'?'is-unknown':''}/>)}</ul>
   {(e?.hours||[]).length>1?<><h4 className="energy-sub">Last 48 hours</h4><Chart points={e.hours} keys={['total_w']} colors={['var(--md-chart-1)']} labels={{total_w:'Average power'}} unit="W" divisor={1} height={160} gapSeconds={7200}/></>:<p className="m3-empty-note"><Icon name="monitoring" size={20}/>The hourly chart fills in as readings arrive.</p>}
   {days.length>0&&<><h4 className="energy-sub">Daily use, last 30 days</h4><div className="energy-days" role="img" aria-label={`Daily energy use: ${days.map(d=>`${d.date} ${num(d.kwh,1)} kWh`).join(', ')}`}>{slots.map(d=><span key={d.date} className={valid(d.kwh)?'':'is-empty'} title={valid(d.kwh)?`${d.date}: ${num(d.kwh,2)} kWh${cost?` · ${money(d.cost)}`:''}`:`${d.date}: no data`} style={{height:`${valid(d.kwh)&&peak>0?Math.max(4,100*d.kwh/peak):4}%`}}/>)}</div></>}
   {(e?.devices_30d||[]).length>0&&<p className="source-note">Last 30 days: {e.devices_30d.map(d=>`${(devs.find(x=>x.id===d.id)||{}).name||d.id} ${num(d.kwh,1)} kWh${cost?` (${money(d.cost)})`:''}`).join(' · ')}</p>}
   <details className="storage-explainer"><summary>How this is worked out</summary><p>Measured: the CPU energy counters on Proxmox and Unraid (RAPL, including Unraid's RAM), the Arc A310's own counter and the gaming PC's GPU board power. Estimated: motherboards, fans and SSDs, hard drives (by spin state), the rest of the gaming PC when it is awake, its standby draw, the router and the Orange Pi, plus power-supply losses. Nothing here is a wall meter, so treat totals as ±20%.{valid(est)?` Right now about ${num(est)} W of the ${num(e.total_w)} W is estimated.`:''}</p>{devs.filter(d=>d.parts?.length).map(d=><p key={d.id}><strong>{d.name}:</strong> {d.parts.map(p=>`${p.label} ${num(p.w,1)} W${p.measured?'':' (est.)'}`).join(' · ')}</p>)}<p>Hourly totals are kept for 400 days. Gaps longer than 30 seconds are left out, not filled in. {e?.history_since?`Records start ${new Date(e.history_since*1000).toLocaleString()}.`:''}</p></details>
  </article></section>}
function GpuPanel({gpu,ranges}){
 const [metric,setMetric]=useState('engines');const g=gpu?.latest||{},fresh=g.online&&valid(g.observed_at)&&Date.now()/1000-g.observed_at>=0&&Date.now()/1000-g.observed_at<=30;
 const keys=metric==='engines'?['compute_pct','render_pct','video_pct','copy_pct','enhance_pct']:metric==='memory'?['vram_used_mib']:['temperature_c'];
 const value=k=>fresh?g[k]:null;const partial=fresh&&['compute_pct','render_pct','video_pct','vram_used_mib','temperature_c'].some(k=>!valid(g[k]));
 return <section className="gpu-section"><div className="section-heading"><h2>Intel Arc A310</h2>{ranges}</div><article className="m3-card m3-card--outlined panel gpu-panel"><header className="panel-header"><div className="panel-title"><span className="icon-tile"><Icon name="activity"/></span><div><h3>Unraid GPU</h3><p>4 GB · PCI 03:00.0</p></div></div><Badge state={fresh&&!partial?true:fresh?'warn':null} label={!fresh?'Unavailable':partial?'Partial readings':'Live'}/></header><div className="gpu-readings">{[['compute_pct','Compute','%'],['render_pct','Render','%'],['video_pct','Video','%'],['temperature_c','Temperature','°C'],['frequency_mhz','Clock','MHz'],['fan_rpm','Fan','RPM']].map(([k,label,unit])=><div key={k}><small>{label}</small><strong>{num(value(k),k.endsWith('_pct')?1:0)}<em>{unit}</em></strong></div>)}</div><div className="gpu-memory"><Meter label="VRAM" value={valid(value('vram_used_mib'))&&valid(value('vram_total_mib'))&&value('vram_total_mib')>0?100*value('vram_used_mib')/value('vram_total_mib'):null} detail={`${num(value('vram_used_mib'))} / ${num(value('vram_total_mib'))} MiB`} color="var(--md-custom-purple)"/></div><details className="gpu-history"><summary><Icon name="monitoring" size={20}/>History and engines<Icon name="down" size={20} className="gpu-history__chevron"/></summary><div className="gpu-toolbar"><div className="segments" role="group" aria-label="GPU metric">{[['engines','Engines'],['memory','VRAM'],['temperature','Temperature']].map(([k,label])=><button key={k} className={metric===k?'selected':''} aria-pressed={metric===k} onClick={()=>setMetric(k)}>{label}</button>)}</div><span className="source-note">{fresh?`Updated ${new Date(g.observed_at*1000).toLocaleTimeString()}`:'No fresh GPU reading'}</span></div><div className="activity-legend">{keys.map((k,i)=><span key={k}><i style={{background:gpuColors[i]}}/>{gpuLabels[k]}</span>)}</div>{(gpu?.history||[]).length?<Chart points={gpu?.history||[]} keys={keys} colors={gpuColors} labels={gpuLabels} unit={metric==='engines'?'%':metric==='memory'?'MiB':'°C'} divisor={1} height={230} maxValue={metric==='engines'?100:metric==='memory'?g.vram_total_mib:null} gapSeconds={Math.max(30,(gpu?.bucket_seconds||10)*2.5)}/>:<p className="m3-empty-note"><Icon name="monitoring" size={20}/>No GPU history yet. Readings appear here once the Unraid probe reports the A310.</p>}<details className="storage-explainer"><summary>Metric details</summary><p>One-second engine samples about every 10 seconds. Short bursts between samples can be missed. Engine classes are separate and are not added into a single utilization number. Qwen activity appeared in Render during validation. VRAM is device-wide usable local memory, including other applications. Zero clock can mean idle. Missing readings stay unknown.</p><p>24-hour history · longer ranges averaged. {gpu?.history_since?`History starts ${new Date(gpu.history_since*1000).toLocaleString()}.`:'Waiting for samples.'}</p></details></details></article></section>
}

const evidenceNames={hosts:'Hosts & containers',storage:'Disks & history',services:'Services & addresses',recovery:'Backups & parity',health:'Drive health',downloads:'Downloads',network:'Network & DNS',ai:'Models & GPU',logs:'Historical warning logs',live_service_checks:'Live service checks'};
function QwenEvidence({evidence}){
 if(!evidence?.sources)return null;
 const labels=items=>(items||[]).map(k=>evidenceNames[k]||k).join(', ');
 return <details className="qwen-evidence"><summary>Show evidence</summary><p className="source-note">Exact observations supplied to the model. Freshness is measured at capture time; general knowledge is not sourced here.</p>{valid(evidence.observed_at)&&<p className="source-note">Captured {new Date(evidence.observed_at*1000).toLocaleString()}</p>}{Object.entries(evidence.sources).map(([key,value])=><details className="evidence-source" key={key}><summary>{evidenceNames[key]||key}</summary><pre>{JSON.stringify(value,null,2)}</pre></details>)}{evidence.recorded_configuration&&<details className="evidence-source"><summary>Recorded configuration · {evidence.recorded_configuration.as_of}</summary><pre>{JSON.stringify(evidence.recorded_configuration,null,2)}</pre></details>}{evidence.budget_omissions?.length>0&&<p className="source-note warning">Trimmed to fit: {evidence.budget_omissions.join(', ')}</p>}{evidence.not_selected?.length>0&&<p className="source-note">Not selected for this question: {labels(evidence.not_selected)}</p>}</details>
}
const PC_MODEL_NAMES={'qwen3.8-27b':'Qwen3.8 27B','qwen3.8-27b-uncensored':'Qwen3.8 27B Uncensored','gemma-4-26b-a4b':'Gemma 4 26B MoE','qwen3.6-35b-moe':'Qwen3.6 35B MoE','swift-1.5-iq2_xs':'Strata · Qwen3.8-Flash-Next'};
const targetLabel=t=>t==='a310'||t==='a310-12b'?'Gemma 4 E4B · A310':t==='strata'?`${PC_MODEL_NAMES['swift-1.5-iq2_xs']} · gaming PC`:t==='auto'?'Auto · 27B on PC, else A310':`${PC_MODEL_NAMES[t.slice(3)||'qwen3.8-27b']||t.slice(3)} · gaming PC`;
function AskQwen({gpu,ai,slim=false,task=false}){
 const [homelab,setHomelab]=useState(()=>{try{return localStorage.getItem('qwenHomelab')==='1'}catch{return false}});
 const [prompt,setPrompt]=useState(''),[target,setTarget]=useState(()=>{try{const saved=localStorage.getItem('qwenTarget');return (saved&&saved!=='a310-12b')?saved:'a310'}catch{return 'a310'}}),[job,setJob]=useState(null),[posting,setPosting]=useState(false),[error,setError]=useState(''),[expanded,setExpanded]=useState(true),[focused,setFocused]=useState(false);
 const jobRef=useRef(null),version=useRef(0),mounted=useRef(true),requestRef=useRef(null);
 const active=posting||['queued','loading','generating','busy'].includes(job?.status);
 const accept=result=>{if(!['idle','queued','loading','generating','complete','failed','busy'].includes(result?.status))throw Error('Invalid model status');jobRef.current=result;setJob(result)};
 useEffect(()=>{
  mounted.current=true;let timer,controller,checkId=0;
  async function check(){
   // Hidden tab and nothing in flight: skip this poll (it resumes when the tab is shown again).
   if(document.hidden&&!['queued','loading','generating','busy'].includes(jobRef.current?.status)){timer=setTimeout(check,15000);return}
   const current=version.current,readId=++checkId;controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),12000);
   try{const response=await fetch('/api/qwen',{cache:'no-store',signal:controller.signal});if(!response.ok)throw Error();const result=await response.json();if(mounted.current&&current===version.current&&readId===checkId){accept(result);setError('')}}
   catch{if(mounted.current&&current===version.current&&readId===checkId)setError('Model status unavailable. Checking again shortly.')}
   finally{clearTimeout(timeout);if(mounted.current&&readId===checkId)timer=setTimeout(check,['queued','loading','generating','busy'].includes(jobRef.current?.status)?3000:15000)}
  }
  requestRef.current=()=>{clearTimeout(timer);controller?.abort();check()};check();
  const vis=()=>{if(!document.hidden)requestRef.current()};document.addEventListener('visibilitychange',vis);
  return()=>{mounted.current=false;clearTimeout(timer);controller?.abort();document.removeEventListener('visibilitychange',vis)};
 },[]);
 async function submit(event){
  event.preventDefault();const text=prompt.trim();if(!text||text.length>3000||active)return;
  version.current++;setPosting(true);setError('');setExpanded(true);
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{const response=await fetch('/api/qwen',{method:'POST',headers:{'Content-Type':'application/json','X-Qwen-Request':'1'},body:JSON.stringify({prompt:text,target,homelab}),signal:controller.signal});const result=await response.json();if(!mounted.current)return;
   if(response.status===409){accept({...result,status:'busy',error:result.error||'The local model is in use. Waiting for it to become available.'});requestRef.current?.();}
   else if(!response.ok){setError(result.error||'The question could not be accepted.');}
   else{accept(result);requestRef.current?.()}
  }catch{if(mounted.current){setError('Could not confirm the request. Check model status before trying again.');requestRef.current?.()}}
  finally{clearTimeout(timeout);if(mounted.current)setPosting(false)}
 }
 const perf=job?.performance||{};
 const collapsed=slim&&!focused&&!prompt&&!active&&job?.status!=='complete';
  // On touch, the first tap only expands the box; a second tap brings up the keyboard.
 const coarse=useMemo(()=>window.matchMedia('(pointer: coarse)').matches,[]),[typing,setTyping]=useState(false),tapCollapsed=useRef(false);
 const noKbd=coarse&&slim&&!typing;
 useEffect(()=>{if(collapsed)setTyping(false)},[collapsed]);
  const sectionRef=useRef(null),lastH=useRef(0),lastMB=useRef(0),lastCollapsed=useRef(collapsed),anim=useRef(null);
 useLayoutEffect(()=>{const el=sectionRef.current;if(!el)return;
  const flip=lastCollapsed.current!==collapsed,run=anim.current;
  // Interrupted mid-way: start from where the box is on screen now and drop the old animation, so they never stack.
  const from=run?el.getBoundingClientRect().height:lastH.current,fromMB=run?parseFloat(getComputedStyle(el).marginBottom):lastMB.current;
  if(flip&&run){run.cancel();anim.current=null}
  const to=el.offsetHeight,toMB=parseFloat(getComputedStyle(el).marginBottom);
  if(flip&&from&&from!==to&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches&&el.animate){
   // Open: an even ease so the growth is visible. Close: quick ease-out, since the hidden content leaves an empty gap.
   // The bottom margin changes with the state too; animating it with the height keeps the content below from jumping.
   const a=el.animate([{height:from+'px',marginBottom:fromMB+'px',overflow:'hidden'},{height:to+'px',marginBottom:toMB+'px',overflow:'hidden'}],collapsed?{duration:180,easing:'cubic-bezier(0.2,0,0,1)'}:{duration:420,easing:'cubic-bezier(0.4,0,0.2,1)'});
   anim.current=a;a.onfinish=a.oncancel=()=>{if(anim.current===a)anim.current=null};
   if(!collapsed)el.querySelectorAll('.qwen-heading,label[for=qwen-prompt],.qwen-help,.qwen-status,.qwen-gpu').forEach((c,i)=>c.animate([{opacity:0,transform:'translateY(-8px)'},{opacity:1,transform:'none'}],{duration:300,delay:120+i*40,easing:'cubic-bezier(0.2,0,0,1)',fill:'backwards'}));
  }
  lastCollapsed.current=collapsed;if(flip||!anim.current){lastH.current=to;lastMB.current=toMB}});
  const labels={idle:'Ready',queued:'Queued',loading:'Loading model',generating:'Writing answer',complete:'Answer ready',failed:'Request failed',busy:'Model in use'};
 return <section ref={sectionRef} className={'qwen-section'+(collapsed?' qwen-collapsed':'')+(slim?' qwen-slim':'')+(task?' ai-task ai-task--ask':'')} aria-labelledby="qwen-heading"><div className="qwen-heading">{task&&<span className="ai-task__icon"><Icon name="psychology" size={24}/></span>}<h2 id="qwen-heading">{task?'Ask about the homelab':'Ask AI'}</h2><span className="qwen-model">{job?.model&&['queued','loading','generating'].includes(job.status)?job.model:targetLabel(target)}</span></div><div className="m3-card m3-card--outlined panel qwen-panel"><form onSubmit={submit} onFocus={()=>setFocused(true)} onBlur={ev=>{if(!ev.currentTarget.contains(ev.relatedTarget))setFocused(false)}}><label htmlFor="qwen-prompt">Your question</label><div className="qwen-input-row"><textarea id="qwen-prompt" readOnly={noKbd} onPointerDown={()=>{tapCollapsed.current=collapsed}} onClick={e=>{if(noKbd&&!tapCollapsed.current){const t=e.currentTarget;setTyping(true);t.readOnly=false;t.blur();t.focus()}}} rows={collapsed?1:2} maxLength="3000" value={prompt} onChange={event=>setPrompt(event.target.value)} placeholder={slim&&collapsed?'Ask about your homelab…':'How busy is the scratch disk right now?'} aria-describedby="qwen-help qwen-count"/><MenuSelect className="m3-select--qwen qwen-target" aria-label="Which model answers" value={target} disabled={active} onChange={v=>{haptic('tap');setTarget(v);try{localStorage.setItem('qwenTarget',v)}catch{}}}
  options={[{value:'a310',label:'Gemma 4 E4B · fast (~14–18 tok/s)',group:'A310 · Unraid'},
   ...(ai?.desktop?.models?.length?ai.desktop.models.map(m=>m.id):Object.keys(PC_MODEL_NAMES)).map(id=>({value:'pc:'+id,label:PC_MODEL_NAMES[id]||id,disabled:ai?.desktop?.online===false,group:`Gaming PC${ai?.desktop?.online===false?' · off':''}`})),
   {value:'strata',label:PC_MODEL_NAMES['swift-1.5-iq2_xs'],disabled:ai?.strata?.online===false,group:`Gaming PC${ai?.desktop?.online===false?' · off':''}`},
   {value:'auto',label:'Auto · best available'}]}/><button className="m3-btn m3-btn--filled m3-state primary-button" disabled={active||!prompt.trim()||!job} type="submit"><span>{posting?'Submitting…':active?'Please wait':'Ask'}</span><Icon name="arrow" size={18}/></button></div><div className="qwen-help"><button type="button" className="qwen-homelab m3-state" aria-pressed={homelab} disabled={active} onClick={()=>{haptic('tap');const v=!homelab;setHomelab(v);try{localStorage.setItem('qwenHomelab',v?'1':'0')}catch{}}}>{homelab&&<Icon name="check" size={18}/>}Include homelab data</button><p id="qwen-help">{homelab?'Adds current host, disk, service, network and log readings, with instructions to stick to them.':'Asks the model directly, with no system prompt or homelab data.'}{target==='strata'?' Strata may search the web.':''} No chat history or actions.</p><span id="qwen-count">{prompt.length}/3000</span></div></form><div className="qwen-status" role="status" aria-live="polite"><span className={active?'qwen-progress':''}>{posting?'Submitting question':job?labels[job.status]:'Checking model status'}</span>{['queued','loading','generating','busy'].includes(job?.status)&&<p>{job?.model?.includes('gaming PC')?'Answering on the gaming PC. Switching its loaded model can add 10–30 seconds.':'The A310 model loads in about 12 seconds and stops after the answer.'}</p>}</div>{job?.fallback&&<p className="source-note">{job.fallback}</p>}{job?.model?.includes('gaming PC')&&['loading','generating','complete'].includes(job.status)&&<div className="qwen-gpu">Answered on the gaming PC (RTX 5070 Ti)</div>}{active&&valid(job?.started_at)&&<p className="source-note">Elapsed {num(Math.max(0,Date.now()/1000-job.started_at))}s{valid(job?.load_seconds)?` · Model loaded in ${num(job.load_seconds,1)}s`:''}</p>}{job?.status==='complete'&&<><div className="qwen-performance"><span><strong>{num(perf.tokens_per_second,1)}</strong> tokens/s</span><span><strong>{num(perf.input_tokens)}</strong> input tokens</span><span><strong>{num(perf.output_tokens)}</strong> output tokens</span>{valid(job.load_seconds)&&<span>Load <strong>{num(job.load_seconds,1)}s</strong></span>}<span>Model <strong>{job.model||'—'}</strong></span><span>Generation <strong>{num(perf.generation_seconds,1)}s</strong></span><span>Total <strong>{num(job.total_seconds,1)}s</strong></span></div><p className="source-note">Output tokens and generation speed include internal reasoning. Speed appears after completion.</p></>}{error&&<p className="source-note error" role="alert">{error}</p>}{job?.status==='busy'&&job.error&&<p className="source-note">{job.error}</p>}{job?.status==='failed'&&<p className="source-note error">{job.error||'The model could not finish this question. You can try again.'}</p>}{job?.status==='complete'&&<div className="qwen-result"><button className="m3-btn m3-btn--text m3-state text-button" type="button" aria-expanded={expanded} aria-controls="qwen-answer" onClick={()=>setExpanded(value=>!value)}>{expanded?'Hide answer':'Read answer'}<Icon name={expanded?'up':'down'} size={16}/></button>{expanded&&<div id="qwen-answer">{job.prompt&&<p className="qwen-question"><strong>Question</strong>{job.prompt}</p>}<div className="qwen-answer"><SafeMarkdown text={job.answer||'No answer was returned.'}/></div>{valid(job.context_at)&&<p className="source-note">Readings captured {new Date(job.context_at*1000).toLocaleString()}</p>}{job.context_note&&<p className="source-note">{job.context_note}</p>}<p className="source-note">Model interpretation · verify before making changes.</p><QwenEvidence evidence={job.evidence}/></div>}</div>}</div></section>
}
function NightlyReview({activity}){
 const r=activity?.nightly,[expanded,setExpanded]=useState(false);
 const date=t=>t?new Date(t*1000).toLocaleString([],{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'—';
 const age=r?.completed_at?Date.now()/1000-r.completed_at:null,old=age!==null&&age>36*3600;
 const interrupted=r&&['running','waiting_backup','waiting_resources'].includes(r.status)&&Date.now()/1000-r.updated_at>2*3600;
 const labels={complete:old?'Older report':'Reviewed',waiting_backup:'Waiting for backup',waiting_resources:'Waiting for resources',running:'Reviewing',failed:'Run failed'};
 return <section className="nightly-section"><div className="section-heading"><h2>Nightly log review</h2><span className="muted nightly-schedule">3 AM · New York time</span></div><article className="m3-card m3-card--outlined panel nightly-panel"><header className="panel-header"><div className="panel-title"><span className="icon-tile"><Icon name="book"/></span><div><h3>{r?.status==='complete'?`${num(r.evidence?.distinct_groups)} log ${r.evidence?.distinct_groups===1?'group':'groups'} found`:r?'Nightly review':'First run pending'}</h3><p>{r?.completed_at?`Finished ${date(r.completed_at)}`:'Runs after the 3 AM backup finishes'}{r?.model_label?` · ${r.model_label}`:''}{r?.load_seconds?` · loaded in ${num(r.load_seconds,0)} s`:''}</p></div></div><Badge state={r?.status==='failed'?false:interrupted?'warn':null} label={r?interrupted?'Run overdue':labels[r.status]||'Unknown':'Scheduled'}/></header>
 {activity?.nightly_source_available===false&&<p className="source-note error">Report source unavailable. Showing the last received report.</p>}
 {interrupted&&<p className="source-note error">No progress reported for over two hours. The run may have stopped.</p>}{r?.fallback_reason&&<p className="source-note">Model note: {r.fallback_reason}.</p>}{r?.skipped_groups?.length>0&&<p className="source-note">{r.skipped_groups.length} log group{r.skipped_groups.length>1?'s':''} skipped ({[...new Set(r.skipped_groups.map(g=>g.reason))].join(', ')}). The rest were reviewed.</p>}
 {r?.status!=='complete'&&<p className="muted">{r?.notes||'The local model loads for the review and stops when it finishes.'}</p>}
 {r?.status==='complete'&&<><div className="nightly-facts"><span><strong>{num(r.evidence?.matched_entries)}</strong> matching log entries</span><span><strong>{num(r.evidence?.files_scanned)}</strong> host logs scanned</span><span>{date(r.evidence?.window_start)} – {date(r.evidence?.window_end)}</span></div>
 <p className="nightly-scope">{r.evidence?.scope}. {r.evidence?.coverage_limited?'Some log coverage is missing; this report cannot establish a clean day.':'Review covers retained, timestamped entries within the scan limits.'}{r.evidence?.groups_omitted_from_model>0?` ${r.evidence.groups_omitted_from_model} groups were not sent to the model.`:''}{r.findings?.some(f=>f.withheld_reason)?` Withheld model explanations: ${r.findings.filter(f=>f.withheld_reason).length}.`:''}</p>
 <button className="m3-btn m3-btn--text m3-state text-button" aria-expanded={expanded} onClick={()=>setExpanded(v=>!v)}>{expanded?'Hide report':'Read report'} <Icon name={expanded?'up':'down'} size={15}/></button>
 {expanded&&<div className="nightly-findings">{(r.evidence?.groups||[]).map(g=>{const finding=(r.findings||[]).find(f=>f.group_id===g.id);return <article key={g.id}><h4>{g.host} <span>{g.level} · {num(g.count)} matching entries</span></h4><p className="nightly-pattern">{g.message}</p><p className="source-note">First: {date(g.first_at)} · Last: {date(g.last_at)}</p><p className="nightly-verified">Service state at review: {{running_again:'running again',running:'running',failed:'failed',restarting:'restarting',stopped:'stopped',unverified:'unverified'}[g.current_service_state?.state]||'unverified'}</p>{finding&&<div className="nightly-model-note"><small>{finding.withheld_reason?'Explanation withheld':'Model interpretation'}</small><p>{finding.interpretation}</p></div>}</article>})}{!r.evidence?.groups?.length&&<p>No entries matched the monitor’s warning/critical patterns. This is not a full system-health check.</p>}<p className="source-note">{r.model} · A310 · {num(r.inference_seconds)}s including model load · suggestions only{r.model_output_truncated?' · output limit reached':''}</p></div>}</>}
 </article></section>
}

function LogGroups({source}){
 const [showAll,setShowAll]=useState(false);const window=source.log_window,groups=source.log_groups;
 if(!Array.isArray(groups))return source.last_alert?<><SourceNote source={source}/><LogNotice source={source}/></>:null;
 const fresh=sourceFresh(source)&&window?.observed_at&&Date.now()/1000-window.observed_at<45;
 const date=ts=>ts?new Date(ts*1000).toLocaleString([],{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit'}):'Unknown';
 const labels={running_again:'Running again',running:'Running now',failed:'Failed now',restarting:'Restarting',stopped:'Stopped now',unverified:'Status unverified'};
 return <section className="log-groups" aria-label="Recent log groups"><header className="section-heading"><div><h2>Recent log groups <span className="count">{groups.length}</span></h2><p className="log-window-note">{window?.returned} latest entries · repeats grouped · counts cover this returned window</p></div><a className="m3-btn m3-btn--text m3-state text-button" href="http://192.168.0.3:8999" target="_blank" rel="noreferrer">Open logs <Icon name="external" size={15}/></a></header>{!fresh&&<p className="source-note error">Log readings are stale. Current service status is unverified.</p>}
 {!groups.length&&<p className="muted">No entries returned by the log source.</p>}
 <div className="log-group-list">{(showAll?groups:groups.slice(0,3)).map(g=>{
 const status=g.service_status||{},checked=fresh&&status.checked_at&&Date.now()/1000-status.checked_at<=90,state=checked?status.state:'unverified';
 const known=g.host==='utilities'&&g.unit==='filebrowser.service';
 return <article className={'m3-card m3-card--outlined panel log-group '+(['running','running_again'].includes(state)?'log-running':'')} key={g.id}>
 <header className="log-group-heading"><div><h3>{known?'FileBrowser':g.unit||g.host}<span className="log-group-host">{g.host} · {g.level}</span></h3></div><Badge state={['running','running_again'].includes(state)?true:['failed','restarting'].includes(state)?false:state==='unverified'||!labels[state]?'warn':null} label={labels[state]||labels.unverified}/></header>
 <p className="log-group-summary">{g.summary}</p><div className="log-group-times"><strong>{num(g.count)} matching {g.count===1?'entry':'entries'}</strong><span>First {date(g.first_at)}</span><span>Last {date(g.last_at)}</span></div>
 <div className="log-current-state">{checked&&state!=='unverified'?<><span>{['running','running_again'].includes(state)?`Service running since ${date(status.active_since)}`:`Service ${status.active||'unknown'} · ${status.sub||'unknown'}`}</span><span>Checked {date(status.checked_at)}</span></>:<span>Current service state has not been verified.</span>}</div>
 <details className="log-entry-details"><summary>Latest full entry</summary><p className="log-message">{g.message}</p></details>
 {known&&<a className="m3-btn m3-btn--text m3-state text-button" href="http://192.168.0.13:6501" target="_blank" rel="noreferrer">Open FileBrowser <Icon name="external" size={15}/></a>}
 </article>})}</div>{groups.length>3&&<button className="secondary-button log-more" aria-expanded={showAll} onClick={()=>setShowAll(v=>!v)}>{showAll?'Show fewer groups':`Show all ${groups.length} groups`}</button>}</section>
}

function LogNotice({source}){
 const event=source.latest_event,message=event?.msg||source.last_alert||'',long=message.length>300;
 const [expanded,setExpanded]=useState(false);useEffect(()=>setExpanded(false),[message]);
 return <section className="notice log-notice" aria-label="Latest log entry"><Icon name="bell" size={20}/><div className="log-notice-content"><div className="log-notice-heading"><strong>Latest log entry</strong>{event&&<span className="log-event-meta">{event.level} · {event.host} · {Number.isFinite(+event.ts)&&<time dateTime={new Date(event.ts*1000).toISOString()}>{new Date(event.ts*1000).toLocaleString()}</time>}</span>}</div><p className="log-message">{long&&!expanded?message.slice(0,300)+'…':message}</p>{long&&<button className="text-button log-expand" aria-expanded={expanded} onClick={()=>setExpanded(v=>!v)}>{expanded?'Show less':'Show full entry'}</button>}{!source.last_alert_full&&<p className="log-detail-note">Full entry unavailable. The log source supplied this shortened summary.</p>}<a className="text-button log-open" href="http://192.168.0.3:8999" target="_blank" rel="noreferrer">Open logs <Icon name="external" size={15}/></a></div></section>
}
// Overview side column: compact backup status (cloud, Proxmox jobs, parity) linking to Infrastructure.
function BackupSummary({activity,cb,onOpen,failed=false}){
  const r=activity?.recovery||{},now=Date.now()/1000,ago=t=>!t?'never':now-t<3600?`${Math.max(1,Math.round((now-t)/60))} min ago`:now-t<172800?`${Math.round((now-t)/3600)} h ago`:`${Math.round((now-t)/86400)} days ago`;
  // Readings older than 3 min are shown as no current reading, never as a tick (the Infra cards already treat them so).
  const live=x=>x?.observed_at&&now-x.observed_at<180;
  const cloudOk=cb?.last_success&&!cb.last_failed_start&&now-cb.last_success<36*3600,jobOk=r.backup?.latest?.status==='OK',parity=live(r.parity)?r.parity:null,backupLive=live(r.backup);
  const rows=[['cloud','Cloud backup',!cb?(failed?'No current reading':'Checking…'):cb.running?'Uploading now':cb.last_failed_start?'Last run failed':cb.last_success?`Last success ${ago(cb.last_success)}`:'First upload pending',!cb?null:cb.running?null:!!cloudOk],
    ['dns','Proxmox backup jobs',!backupLive?(r.backup?'No current reading':'Checking…'):r.backup.latest?`${r.backup.latest.status==='OK'?'Succeeded':'Failed'} ${ago(r.backup.latest.finished_at)}`:'No job reported',backupLive&&r.backup.latest?jobOk:null],
    ['shield','Unraid parity',!parity?(r.parity?'No current reading':'Checking…'):parity.operation==='running'?'Check running':(parity.assigned_disks||[]).some(d=>d.status!=='DISK_OK')?'Disk fault':'Disks OK',!parity?null:parity.operation==='running'?null:!(parity.assigned_disks||[]).some(d=>d.status!=='DISK_OK')]];
  const bad=rows.some(x=>x[3]===false),missing=rows.some(x=>x[2]==='No current reading');
  return <article className={'ov-status'+(bad?' ov-status--bad':'')}><header className="ov-status__head"><h3>Backups</h3><Chip compact tone={bad?'error':rows.every(x=>x[3])?'success':'neutral'} icon={bad?'error':rows.every(x=>x[3])?'check_circle':'schedule'}>{bad?'Needs a look':rows.every(x=>x[3])?'Protected':missing?'No reading':'Checking'}</Chip></header>
    <ul className="m3-list">{rows.map(([ic,h,sup,ok])=><ListItem key={h} icon={ok===false?'error':ok?'check_circle':ic} headline={h} supporting={sup} className={ok===false?'is-bad':ok?'is-ok':''}/>)}</ul>
    <Button variant="text" icon="arrow_forward" onClick={onOpen}>Backups & recovery</Button></article>;
}
// Overview side column: last nightly log review in one card.
function NightlySummary({activity,onOpen}){
  const r=activity?.nightly,old=r?.completed_at&&Date.now()/1000-r.completed_at>36*3600;
  const groups=r?.evidence?.distinct_groups,findings=(r?.findings||[]).length;
  return <article className="ov-status"><header className="ov-status__head"><h3>Nightly log review</h3><Chip compact tone={r?.status==='failed'?'error':r?.status==='complete'&&!old&&!r?.evidence?.coverage_limited?'success':'neutral'}>{!r?'Scheduled':r.status==='complete'?old?'Older report':'Reviewed':r.status==='failed'?'Run failed':'Running'}</Chip></header>
    <p className="ov-status__body">{r?.status==='complete'?`${num(groups)} log ${groups===1?'group':'groups'} found · ${findings} ${findings===1?'finding':'findings'}`:'Runs at 3 AM after the backup finishes'}</p>
    {r?.status==='complete'&&r.evidence?.coverage_limited&&<p className="ov-status__note"><Icon name="info" size={16}/><span><b>Incomplete coverage.</b> Some logs were not collected, so this is not a confirmed clean day.</span></p>}
    <Button variant="text" icon="arrow_forward" onClick={onOpen}>Read the report</Button></article>;
}

function RecoveryPanel({activity,children}){
 const r=activity?.recovery||{},stamp=activity?.latest?.ts,live=stamp&&Date.now()/1000-stamp<45;
 const fresh=s=>live&&s?.observed_at&&Date.now()/1000-s.observed_at>=0&&Date.now()/1000-s.observed_at<180;
 const b=fresh(r.backup)?r.backup:null,p=fresh(r.parity)?r.parity:null;
 const coverage=b?.coverage,missing=coverage?.status==='checked'?coverage.uncovered||[]:null;
 const skipped=g=>g.status==='stopped';
 const gaps=(missing||[]).filter(g=>!skipped(g)),off=(missing||[]).filter(skipped);
 const guestName=g=>g.name&&!g.name.includes(String(g.vmid))?`${g.name} · ${g.vmid}`:g.name||`Guest ${g.vmid}`;
 const date=t=>t>0?new Date(t*1000).toLocaleString([],{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'Unknown';
 // Same wording as every other age on the page: minutes, then whole hours, then days.
 const age=t=>{if(!(t>0))return 'Unknown';const s=Date.now()/1000-t;return s<3600?`${Math.max(1,Math.round(s/60))} min ago`:s<172800?`${Math.round(s/3600)} h ago`:`${Math.round(s/86400)} days ago`};
 const complete=p?.operation==='idle'&&p?.last_exit===0&&p?.last_started_at>0&&p?.last_finished_at>=p?.last_started_at;
 const assigned=p?.assigned_disks||[],faults=assigned.filter(d=>d.status!=='DISK_OK');
 const result=p?p.operation==='running'?'Running':p.operation==='paused'?'Paused':p.operation==='unknown'?'Unknown':complete?'Completed':p.last_exit!=null&&p.last_exit!==0?'Incomplete':'Unknown':'Unknown';
 return <section className="recovery-section"><div className="section-heading"><h2>Backups, off-site & parity</h2></div><div className="two-grid"><article className="m3-card m3-card--outlined panel recovery-card"><header className="panel-header"><div className="panel-title"><span className="icon-tile"><Icon name="clock"/></span><div><h3>Proxmox backup jobs</h3><p>Latest recorded backup job</p></div></div><Badge state={b?.latest?.status?b.latest.status==='OK':null} label={b?.running?'Running':b?.latest?.status==='OK'?'Succeeded':b?.latest?.status?'Check job':'Unknown'}/></header><strong className="recovery-value">{b?.last_success?age(b.last_success.finished_at):'—'}</strong><p>Last success · {date(b?.last_success?.finished_at)}</p><p>Latest job · {date(b?.latest?.finished_at)}{b?.latest?.status&&b.latest.status!=='OK'?` · ${b.latest.status}`:''}</p><div className="backup-coverage" role={gaps.length?'alert':undefined}>{missing===null?<p className="source-note">Backup coverage unknown</p>:<><p className={gaps.length?'source-note error':'source-note'}><strong>{coverage.covered} / {coverage.total-off.length} guests in enabled backup jobs</strong></p>{gaps.length>0&&<><p className="source-note error">Not covered:</p><ul>{gaps.map(g=><li key={g.vmid}>{guestName(g)} · {g.status}</li>)}</ul></>}{off.length>0&&<p className="source-note">Not backed up by choice: {off.map(guestName).join(', ')}</p>}<p className="source-note">Schedule membership; each guest still needs a successful archive.</p></>}</div><footer className="host-footer"><span>Memos & Open WebUI restore-tested Oct 20, 2026</span><a href="https://192.168.0.3:8006" target="_blank" rel="noreferrer">Proxmox <Icon name="external" size={14}/></a></footer></article><article className="m3-card m3-card--outlined panel recovery-card"><header className="panel-header"><div className="panel-title"><span className="icon-tile"><Icon name="shield"/></span><div><h3>Unraid parity</h3><p>Array {p?.array_state?.toLowerCase()||'unknown'}</p></div></div><Badge state={!p?null:faults.length?false:assigned.length?true:null} label={!p?'Unknown':faults.length?'Check disks':assigned.length?'Assigned disks OK':'Unknown'}/></header><strong className="recovery-value">{result}</strong><p>{p?.operation==='idle'?'Last check':'Current operation'} · {p?.action?.startsWith('check')?'Parity check':p?.action?.startsWith('recon')?'Disk rebuild':p?.action?.startsWith('clear')?'Disk clearing':'Operation'}{p?.operation==='running'||p?.operation==='paused'?` · ${num(p.position/p.size*100,1)}%`:''}</p><p>{complete?`${date(p.last_finished_at)} · ${num(p.last_errors)} sync errors`:p?.operation==='running'||p?.operation==='paused'?'Completion pending':p?.last_exit?`Last operation exit code ${p.last_exit}`:'No completed check reported'}</p>{faults.length>0&&<p className="source-note error">{faults.map(d=>`${d.name}: ${d.status}`).join(' · ')}</p>}<footer className="host-footer"><span>{p?.operation==='idle'?'No check running':p?.operation==='running'?'Operation in progress':p?.operation==='paused'?'Operation paused':'Status unavailable'}</span><a href="http://192.168.0.4/Main" target="_blank" rel="noreferrer">Unraid <Icon name="external" size={14}/></a></footer></article>{children}</div></section>
}

const navigation=[['overview','Overview','home'],['services','All services','grid'],['ai','Local AI','terminal'],['media','Media & downloads','play'],['network','Network & DNS','network'],['fleet','Hosts & containers','layers'],['storage','Storage','disk'],['infra','Infrastructure','server'],['updates','Updates','refresh']];
const VIEWS=['overview','services','ai','media','network','fleet','storage','infra','updates'];
export const haptic=(type='tap')=>{
 if(typeof window!=='undefined'&&window.AndroidHaptics){
  if(type==='tick')window.AndroidHaptics.tick();
  else if(type==='snap')window.AndroidHaptics.snap();
  else window.AndroidHaptics.tap();
 }else if(typeof navigator!=='undefined'&&navigator.vibrate){
  try{
   if(type==='tick')navigator.vibrate(8);
   else if(type==='snap')navigator.vibrate(22);
   else navigator.vibrate(12);
  }catch{}
 }
};
function App(){
 const [data,setData]=useState(window.__INITIAL_TELEMETRY__?.updated_at?window.__INITIAL_TELEMETRY__:{}),[storage,setStorage]=useState(null),[error,setError]=useState(false),[storageError,setStorageError]=useState(false),[view,setView]=useState(()=>{const h=location.hash.replace(/^#\/?/,'');if(h&&!VIEWS.includes(h))history.replaceState(null,'','#/overview');return VIEWS.includes(h)?h:'overview'}),[search,setSearch]=useState(''),[command,setCommand]=useState(false),[range,setRange]=useState(3600),[capacity,setCapacity]=useState(false),[now,setNow]=useState(Date.now()),[refreshing,setRefreshing]=useState(false),[toast,setToast]=useState(''),[dialog,setDialog]=useState(null),[busy,setBusy]=useState(false),[menu,setMenu]=useState(false),[filter,setFilter]=useState('all');
 const [compare,setCompare]=useState(()=>window.innerWidth>=840),[diskPick,setDiskPick]=useState('cache');const [category,setCategory]=useState('all');useEffect(()=>setCategory('all'),[view]);const [favorites,setFavorites]=useStored('homelab.favorites.v2',['pve','jellyfin','sabnzbd','files','guacamole','router']);const [theme,setTheme]=useStored('homelab.theme.v2',window.matchMedia?.('(prefers-color-scheme: light)').matches?'light':'dark');const [seedPref,setSeedPref]=useStored('homelab.seed.v1','auto');const [wallSeed,setWallSeed]=useState(readWallpaperSeed);const [sysColors,setSysColors]=useState(readSystemColors);const [palette,setPalette]=useState(false);const win=useWindowClass();const [railPref,setRailPref]=useStored('homelab.rail.v1',true);const [railModal,setRailModal]=useState(false);const navOpener=useRef(null);const [sheet,setSheet]=useState(false);
 const [dialogActive,setDialogActive]=useState(false);
 const [cmdClosing,setCmdClosing]=useState(false);
 const closeCommand=()=>{
  if(window.matchMedia('(prefers-reduced-motion: reduce)').matches){
   setCommand(false);setCmdClosing(false);return;
  }
  setCmdClosing(true);
 };
 useEffect(()=>{
  if(cmdClosing){
   const t=setTimeout(()=>{setCmdClosing(false);setCommand(false)},220);
   return()=>clearTimeout(t);
  }
 },[cmdClosing]);
 useEffect(()=>{
  const onOpen=()=>setDialogActive(true),onClose=()=>setDialogActive(false);
  window.addEventListener('m3:dialog-open',onOpen);window.addEventListener('m3:dialog-close',onClose);
  return()=>{window.removeEventListener('m3:dialog-open',onOpen);window.removeEventListener('m3:dialog-close',onClose)}
 },[]);
 const anyOverlay=Boolean(menu||command||cmdClosing||dialog||dialogActive||(win==='expanded'&&railModal));
 const overlayPushedRef=useRef(false),ignoreNextPopRef=useRef(false),progBackAtRef=useRef(0),viewRef=useRef(view);
 viewRef.current=view;
 const closeAllOverlays=()=>{
  setMenu(false);setSheet(false);setCommand(false);setCmdClosing(false);setDialog(null);setRailModal(false);
  window.dispatchEvent(new CustomEvent('m3:close-all-overlays'));
 };
 useEffect(()=>{
  if(anyOverlay){
   if(!overlayPushedRef.current){
    overlayPushedRef.current=true;
    history.pushState({overlay:true},'');
   }
  }else if(overlayPushedRef.current){
   overlayPushedRef.current=false;
   ignoreNextPopRef.current=true;
   progBackAtRef.current=performance.now();
   history.back();
  }
 },[anyOverlay]);
 // A modal navigation (phone/tablet drawer, or the expanded rail on 840-1199px) makes the rest of the page inert, moves focus in, and returns it on close.
 const navModal=(menu&&(win==='compact'||win==='medium'))||(railModal&&win==='expanded');
 // Close modal navigation that can't exist in the new window class (drawer/sheet above medium, modal rail outside
 // expanded); it otherwise stayed open but invisible and ate the next Back. Compact <-> medium keeps the drawer open.
 useEffect(()=>{if(win!=='compact'&&win!=='medium'){setMenu(false);setSheet(false)}if(win!=='expanded')setRailModal(false)},[win]);
 useEffect(()=>{
  const on=ev=>{
   if(ignoreNextPopRef.current){ignoreNextPopRef.current=false;return}
   // A Back within 400 ms of our own history.back() (overlay closed by X, scrim or Escape) belongs to that close: undo it.
   if(progBackAtRef.current&&performance.now()-progBackAtRef.current<400){progBackAtRef.current=0;ignoreNextPopRef.current=true;history.forward();return}
   // Forward into an old overlay entry: skip it rather than leave a dead Back press.
   if(ev.state?.overlay){if(!overlayPushedRef.current){ignoreNextPopRef.current=true;history.back()}return}
   if(overlayPushedRef.current){
    overlayPushedRef.current=false;
    closeAllOverlays();
    return;
   }
   const h=location.hash.replace(/^#\/?/,'');const v=VIEWS.includes(h)?h:'overview';
   if(v!==viewRef.current){
    navigatedRef.current=true;
    withPageTransition(()=>{setView(v);setSearch('');setFilter('all')});
   }
  };
  window.addEventListener('popstate',on);return()=>window.removeEventListener('popstate',on);
 },[]);
 useEffect(()=>{
  window.__hasOpenOverlay=()=>anyOverlay;
  window.__closeAllOverlays=closeAllOverlays;
  window.__navigateOverview=()=>navigate('overview');
  // Android deep links and launcher shortcuts (MainActivity passes only allowlisted page names and actions).
  window.__homelabOpen=page=>{if(VIEWS.includes(page))navigate(page)};
  window.__homelabShortcut=action=>{if(action==='wake-pc'){navigate('overview');setTimeout(()=>window.__homelabWake?.(),400)}};
  return()=>{
   window.__hasOpenOverlay=null;
   window.__closeAllOverlays=null;
   window.__navigateOverview=null;
   window.__homelabOpen=null;
   window.__homelabShortcut=null;
  };
 },[anyOverlay,closeAllOverlays]);
 useEffect(()=>{
  const shouldIntercept=Boolean(anyOverlay||view!=='overview');
  if(typeof window!=='undefined'&&window.AndroidBridge?.setBackEnabled){
   window.AndroidBridge.setBackEnabled(shouldIntercept);
  }
 },[anyOverlay,view]);
 useEffect(()=>{
  let pbWatchdog;
  const onPb=ev=>{
   const {phase,progress=0,edge=0}=ev.detail||{};
   const m=document.querySelector('.m3-menu-select__menu,.m3-search-view,.m3-dialog,.m3-nav-drawer.is-modal.open,.m3-xrail.is-modal');
   const s=document.querySelectorAll('.m3-scrim');
   const main=document.querySelector('main');
   clearTimeout(pbWatchdog);
   if(phase==='start'||phase==='progress'){
    // If neither cancel nor commit arrives (focus lost mid-gesture), put everything back after 2 s without events.
    pbWatchdog=setTimeout(()=>onPb({detail:{phase:'cancel'}}),2000);
    if(m){
     const isDialog=m.classList.contains('m3-dialog');
     const isSheet=m.classList.contains('is-sheet');
     const isDockedSearch=m.classList.contains('m3-search-view')&&window.innerWidth>=840;
     m.style.transition='none';
     if(isDialog){
      m.style.transform=`translate(-50%, -50%) scale(${1-progress*0.08})`;
     }else if(isSheet){
      m.style.transformOrigin='bottom center';
      m.style.transform=`translateY(${progress*80}px) scale(${1-progress*0.04})`;
     }else if(isDockedSearch){
      m.style.transformOrigin='top center';
      m.style.transform=`translateX(-50%) scale(${1-progress*0.06})`;
     }else{
      m.style.transformOrigin='center center';
      m.style.transform=`scale(${1-progress*0.06})`;
      if(m.classList.contains('m3-search-view')){
       m.style.borderRadius=`${progress*28}px`;
      }
     }
     s.forEach(el=>{el.style.opacity=String(Math.max(0.05,1-progress*0.5));el.style.transition='none'});
    }else if(main&&viewRef.current!=='overview'){
     main.style.transition='none';
     main.style.transformOrigin=edge===0?'right center':'left center';
     main.style.transform=`scale(${1-progress*0.03}) translateX(${edge===0?progress*14:-progress*14}px)`;
    }
   }else if(phase==='cancel'){
    if(m){
     const isDialog=m.classList.contains('m3-dialog');
     const isDockedSearch=m.classList.contains('m3-search-view')&&window.innerWidth>=840;
     m.style.transition='transform 0.22s var(--md-sys-motion-spring-expressive-default-spatial, cubic-bezier(0.2, 0.8, 0.2, 1)), border-radius 0.22s ease';
     if(isDialog){
      m.style.transform='translate(-50%, -50%)';
     }else if(isDockedSearch){
      m.style.transform='translateX(-50%)';
     }else{
      m.style.transform='';
      m.style.borderRadius='';
     }
     m.style.transformOrigin='';
     setTimeout(()=>{if(m){m.style.transition='';if(isDialog)m.style.transform='';if(isDockedSearch)m.style.transform=''}},250);
    }
    s.forEach(el=>{el.style.transition='opacity 0.2s ease';el.style.opacity='';setTimeout(()=>{if(el)el.style.transition=''},220)});
    if(main){
     main.style.transition='transform 0.22s cubic-bezier(0.2, 0.8, 0.2, 1)';
     main.style.transform='';
     setTimeout(()=>{if(main)main.style.transition=''},250);
    }
   }else if(phase==='commit'){
    if(m){
     const isDialog=m.classList.contains('m3-dialog');
     const isSheet=m.classList.contains('is-sheet');
     const isDockedSearch=m.classList.contains('m3-search-view')&&window.innerWidth>=840;
     m.style.transition='transform 0.18s cubic-bezier(0.4, 0, 1, 1), opacity 0.18s ease, border-radius 0.18s ease';
     // Tells Dialog's unmount that the exit already played, so it does not replay it.
     if(isDialog)m.dataset.pbClosed='1';
     if(isDialog&&window.innerWidth<600){
      // Phones: the dialog nearly fills the screen; fading it showed it and the page through each other. Slide it down opaque.
      m.style.transform='translate(-50%, 60vh) scale(0.9)';
     }else{
      if(isDialog){
       m.style.transform='translate(-50%, -46%) scale(0.9)';
      }else if(isSheet){
       m.style.transform='translateY(100%)';
      }else if(isDockedSearch){
       m.style.transform='translateX(-50%) scale(0.9)';
      }else{
       m.style.transform='scale(0.9)';
      }
      m.style.opacity='0';
     }
    }
    s.forEach(el=>{
     el.style.transition='opacity 0.18s ease';
     el.style.opacity='0';
    });
    if(main){
     main.style.transition='transform 0.18s cubic-bezier(0.2, 0.8, 0.2, 1)';
     main.style.transform='';
    }
    setTimeout(()=>{
     // Close only if an overlay was open when the gesture committed; one opened in the meantime stays.
     if(m)closeAllOverlays();
     if(main){main.style.transform='';main.style.transition=''}
     // Keep the overlay and scrims at their faded, off-screen values until React has actually closed them. Clearing
     // them first let the WebView paint the still-open sheet at full opacity for a frame, after which its normal close
     // transition slid it down across the nav bar and gesture line (27 Oct recording).
     // A dialog that has already unmounted keeps its data-pb-closed mark: React may run Dialog's cleanup (which reads it to
     // skip the exit replay) after this, and clearing it first replayed the exit on the phone (30 Oct 16:08 recording).
     const reset=el=>{if(!el.isConnected)return;delete el.dataset.pbClosed;el.style.transition='none';el.style.transform='';el.style.opacity='';el.style.borderRadius='';
      void el.offsetHeight;el.style.transition=''};
     // Sheets and the drawer stay mounted and lose .open; dialogs and search unmount. Give up after 800 ms.
     const until=performance.now()+800;
     const stillOpen=()=>m&&m.isConnected&&(!m.classList.contains('m3-nav-drawer')||m.classList.contains('open'));
     const settle=()=>{
      if(stillOpen()&&performance.now()<until){requestAnimationFrame(settle);return}
      if(m)reset(m);
      s.forEach(el=>{el.style.transition='none';el.style.opacity='';void el.offsetHeight;el.style.transition=''});
     };
     requestAnimationFrame(settle);
    },180);
   }
  };
  window.addEventListener('predictiveback',onPb);
  return()=>window.removeEventListener('predictiveback',onPb);
 },[]);
 useEffect(()=>{const label=view==='overview'?'Overview':navigation.find(n=>n[0]===view)?.[1];document.title=`${label} · Homelab`;
  if(navigatedRef.current&&!navModal){navigatedRef.current=false;requestAnimationFrame(()=>document.querySelector('main h1')?.focus({preventScroll:true}))}},[view,navModal]);
 const trapTab=ev=>{if(ev.key!=='Tab')return;const n=tabbables(ev.currentTarget);if(!n.length)return;
  if(ev.shiftKey&&document.activeElement===n[0]){ev.preventDefault();n.at(-1).focus()}else if(!ev.shiftKey&&document.activeElement===n.at(-1)){ev.preventDefault();n[0].focus()}};
 const searchOpener=useRef(null);useEffect(()=>{if(command)searchOpener.current=document.activeElement;else if(searchOpener.current){const o=searchOpener.current;searchOpener.current=null;requestAnimationFrame(()=>{const t=o.isConnected?o:document.querySelector('[aria-label="Find a service"]');t?.focus()})}},[command]);
 useEffect(()=>{for(const q of ['#main','.skip','.m3-nav-bar','.m3-nav-rail','.m3-xrail','.m3-nav-drawer:not(.is-modal)']){const el=document.querySelector(q);if(el&&!(navModal&&q==='.m3-xrail'))el.inert=command||(navModal&&['#main','.skip','.m3-nav-bar'].includes(q))||(navModal&&win==='medium'&&q==='.m3-nav-rail')}},[command,navModal,win]);
 useEffect(()=>command||navModal?lockScroll():undefined,[command,navModal]);
 // Phone gestures. Swipe the More sheet down to dismiss (past 100px or a quick flick); pull down at the top of the page to
 // refresh, with the Expressive loading shape following the finger (refresh past 64px of travel).
 const sheetDrag=useRef(null);
 const sheetDown=ev=>{if(win==='compact'&&menu)sheetDrag.current={y:ev.clientY,t:performance.now(),dy:0,el:ev.currentTarget,id:ev.pointerId}};
 const sheetMove=ev=>{const d=sheetDrag.current;if(!d)return;d.dy=Math.max(0,ev.clientY-d.y);if(d.dy>8&&!d.captured){d.captured=true;d.el.setPointerCapture?.(d.id);d.el.style.transition='none';d.el.style.animation='none'}if(d.captured)d.el.style.transform=`translateY(${d.dy}px)`};
 const sheetUp=()=>{const d=sheetDrag.current;sheetDrag.current=null;if(!d?.captured)return;const flick=d.dy/Math.max(1,performance.now()-d.t)>0.6;const reset=()=>{d.el.style.transition='';d.el.style.transform='';d.el.style.animation=''};if(d.dy>100||flick){haptic('snap');setMenu(false);requestAnimationFrame(reset)}else reset()};
 const [pull,setPull]=useState(0),[pullHold,setPullHold]=useState(false),[pullShown,setPullShown]=useState(false),[pullLeaving,setPullLeaving]=useState(false);
 const pullActive=pull>0||refreshing||pullHold;
 // Leaving: the indicator stays mounted for 360 ms so it can shrink, rise and fade (the reverse of its entrance).
 useEffect(()=>{if(win!=='compact')return;if(pullActive){setPullShown(true);setPullLeaving(false);return}if(!pullShown)return;setPullLeaving(true);const t=setTimeout(()=>{setPullShown(false);setPullLeaving(false)},360);return()=>clearTimeout(t)},[pullActive,win]);
 useEffect(()=>{if(win!=='compact')return;let y0=null,dist=0;
  const start=ev=>{if(window.scrollY>0||command||menu||document.querySelector('.m3-dialog'))return;y0=ev.touches[0].clientY;dist=0};
  const move=ev=>{if(y0===null)return;if(window.scrollY>0){y0=null;setPull(0);return}dist=Math.max(0,ev.touches[0].clientY-y0)*0.5;setPull(Math.min(110,dist))};
  const end=()=>{if(y0===null)return;y0=null;if(dist>=64){haptic('snap');setRefreshing(true);setPullHold(true);setTimeout(()=>setPullHold(false),1200);loadRef.current?.();storageRef.current?.()}setPull(0)};
  window.addEventListener('touchstart',start,{passive:true});window.addEventListener('touchmove',move,{passive:true});window.addEventListener('touchend',end);window.addEventListener('touchcancel',end);
  return()=>{window.removeEventListener('touchstart',start);window.removeEventListener('touchmove',move);window.removeEventListener('touchend',end);window.removeEventListener('touchcancel',end)}},[win,command,menu]);
 useEffect(()=>{const sel=['#main','.skip','.m3-nav-bar',win==='medium'?'.m3-nav-rail':null].filter(Boolean);for(const q of sel){const el=document.querySelector(q);if(el)el.inert=navModal}
  if(navModal){navOpener.current=document.activeElement;const box=document.querySelector(railModal?'.m3-xrail':'.m3-nav-drawer.is-modal');{let n=0;const tryFocus=()=>{const t=box?.querySelector('[aria-current=page]')||box?.querySelector('button,a');t?.focus();if(!box?.contains(document.activeElement)&&n++<20)requestAnimationFrame(tryFocus)};requestAnimationFrame(tryFocus)}}
  else if(navOpener.current){const o=navOpener.current;navOpener.current=null;if(navigatedRef.current){navigatedRef.current=false;requestAnimationFrame(()=>document.querySelector('main h1')?.focus({preventScroll:true}))}else if(o.isConnected&&o.getClientRects().length&&!o.closest('.m3-nav-drawer'))o.focus();else tabbables(document.querySelector('.m3-top-app-bar,.m3-nav-rail,.m3-xrail'))[0]?.focus()}},[navModal]);const [scrolled,setScrolled]=useState(false);const [scrimGone,setScrimGone]=useState(true);useEffect(()=>{if(menu){setScrimGone(false);return}const t=setTimeout(()=>setScrimGone(true),260);return()=>clearTimeout(t)},[menu]);useEffect(()=>{const on=()=>setScrolled(window.scrollY>4);on();window.addEventListener('scroll',on,{passive:true});return()=>window.removeEventListener('scroll',on)},[]);const cmdRef=useRef(null),toastTimer=useRef(),loadRef=useRef(),storageRef=useRef();
 const notify=msg=>{setToast(msg);clearTimeout(toastTimer.current);toastTimer.current=setTimeout(()=>setToast(''),3600)};
 // lastSource: a tapped tile/row in the page (morphs into the title). lastNav: the centre of the tapped navigation item,
 // so the new page grows out of the button that was pressed.
 const lastSource=useRef(null),lastNav=useRef(null);useEffect(()=>{const on=ev=>{const el=ev.target.closest?.('button,a,[role=button]');lastSource.current=el&&el.closest('main')&&!el.closest('.m3-nav-bar,.m3-nav-rail,.m3-xrail,.m3-nav-drawer')?{el,t:performance.now()}:null;
  const nav=el?.closest('.m3-nav-bar,.m3-nav-rail,.m3-xrail,.m3-nav-drawer')&&el;if(nav){const r=nav.getBoundingClientRect();lastNav.current={x:r.left+r.width/2,y:r.top+r.height/2,t:performance.now()}}};document.addEventListener('pointerdown',on,true);document.addEventListener('keydown',on,true);return()=>{document.removeEventListener('pointerdown',on,true);document.removeEventListener('keydown',on,true)}},[]);
 const navigatedRef=useRef(false);// section: element id to scroll to and focus once the new page is showing (Overview shortcuts into a long page).
 const navigate=(v,section,opts={})=>{
  haptic('tap');
  if(overlayPushedRef.current){
   overlayPushedRef.current=false;
   history.replaceState(null,'','#/'+v);
  }else if(location.hash!=='#/'+v) history.pushState(null,'','#/'+v);
  if(v!==view){
   navigatedRef.current=true;
   const src=lastSource.current&&performance.now()-lastSource.current.t<1500&&lastSource.current.el.isConnected?lastSource.current.el:null;
   const origin=!src&&lastNav.current&&performance.now()-lastNav.current.t<1500?lastNav.current:null;
   lastSource.current=null;lastNav.current=null;
   withPageTransition(()=>{window.scrollTo({top:0,behavior:'instant'});setScrolled(false);setView(v);setSearch('');setFilter(opts.filter||'all')},src,origin).then(()=>section&&jump(section));
  }else{
   setSearch('');setFilter(opts.filter||'all');if(section)jump(section);
  }
  setMenu(false);setRailModal(false);window.scrollTo({top:0,behavior:'instant'});
 };
 useEffect(()=>{document.documentElement.dataset.theme=theme},[theme]);
 // Material You: the page's colours come from a seed. 'auto' is the phone wallpaper in the Android app (Android 12+) and
 // the built-in pine elsewhere; any other choice is a preset or a picked colour. Tokens are generated here with the same
 // code the build uses and set on <html>, so every component follows; clearing them falls back to the built-in pine.
 const seed=resolveSeed(seedPref,wallSeed);
 const applied=useRef([]);
 useLayoutEffect(()=>{const el=document.documentElement;applied.current.forEach(k=>el.style.removeProperty(k));applied.current=[];
  if(!seed)return;const t=m3Tokens(theme!=='light',seed);
  // Wallpaper on Android 14+: the system's own roles replace the generated ones, so colours match other apps exactly.
  if(seedPref==='auto'&&sysColors)for(const [k,v] of Object.entries(sysColors[theme==='light'?'light':'dark']||{}))if(/^#[0-9a-f]{6}$/i.test(v))t['--md-sys-color-'+k]=v;
  for(const [k,v] of Object.entries(t))el.style.setProperty(k,v);applied.current=Object.keys(t)},[seed,theme,seedPref,sysColors]);
 useEffect(()=>{const on=()=>{if(!document.hidden){setWallSeed(readWallpaperSeed());setSysColors(readSystemColors())}};document.addEventListener('visibilitychange',on);return()=>document.removeEventListener('visibilitychange',on)},[]);
 // Android status and navigation bars follow the theme: the page surface behind the status bar, the nav bar container
 // (or the surface when there's no bottom bar) behind the gesture bar. The native side validates and remembers them.
 useEffect(()=>{const id=requestAnimationFrame(()=>{const toHex=c=>{const m=String(c).match(/\d+(\.\d+)?/g);if(!m||m.length<3)return null;return '#'+m.slice(0,3).map(x=>Math.round(+x).toString(16).padStart(2,'0')).join('')};
   const root=getComputedStyle(document.documentElement);const surface=root.getPropertyValue('--md-sys-color-surface').trim();const status=/^#[0-9a-f]{6}$/i.test(surface)?surface:toHex(getComputedStyle(document.body).backgroundColor);
   const bar=document.querySelector('.m3-nav-bar');const nav=bar&&getComputedStyle(bar).display!=='none'?toHex(getComputedStyle(bar).backgroundColor)||status:status;
   document.querySelector('meta[name="theme-color"]')?.setAttribute('content',status);try{window.AndroidBridge?.setSystemBars?.(status,nav,theme==='light')}catch{}});return()=>cancelAnimationFrame(id)},[theme,win,seedPref,wallSeed]);
 // While the Local AI page is open, ask the server to poll the gaming PC fast (live tokens/s). Otherwise it stays quiet
 // so the model can reach llama.cpp's idle sleep (fast /slots polling kept it loaded for hours).
 useEffect(()=>{if(view!=='ai')return;let alive=true;const ping=()=>{if(!document.hidden)fetch('/api/ai/live',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(d=>{if(alive&&d)setAiLive(d)}).catch(()=>{})};ping();const iv=setInterval(ping,2000);
  document.addEventListener('visibilitychange',ping);return()=>{alive=false;clearInterval(iv);document.removeEventListener('visibilitychange',ping)}},[view]);
 // Tells the Android splash screen the dashboard has painted.
 useEffect(()=>{requestAnimationFrame(()=>requestAnimationFrame(()=>{try{window.AndroidBridge?.ready?.()}catch{}}))},[]);
 const [aiLive,setAiLive]=useState(null),[upd,setUpd]=useState(null),[fleet,setFleet]=useState(()=>{try{return JSON.parse(localStorage.getItem('homelab.fleet.v1'))}catch{return null}}),[fleetError,setFleetError]=useState(false);
 // Fleet and backup state: the last copy shows at once (this browser's, then the server's cache), so the Overview
 // never reads green just because the ~8 s Proxmox probe hasn't answered. An old server copy triggers a quick re-ask.
 useEffect(()=>{let alive=true,again;async function get(){try{const r=await fetch('/api/fleet',{cache:'no-store',signal:AbortSignal.timeout(20000)});if(!r.ok)throw Error();const d=await r.json();if(alive){setFleet(d);setFleetError(false);try{localStorage.setItem('homelab.fleet.v1',JSON.stringify(d))}catch{}
  clearTimeout(again);if(d.cloud_backup?.running)again=setTimeout(get,15000);else if(d.checked_at&&Date.now()/1000-d.checked_at>90)again=setTimeout(get,12000)}}catch{if(alive)setFleetError(true)}}get();const iv=setInterval(()=>{if(!document.hidden)get()},60000);const vis=()=>{if(!document.hidden)get()};document.addEventListener('visibilitychange',vis);return()=>{alive=false;clearInterval(iv);clearTimeout(again);document.removeEventListener('visibilitychange',vis)}},[]);
 useEffect(()=>{let alive=true;async function get(){try{const r=await fetch('/api/updates',{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error();const d=await r.json();lastUpdates=d;const items=d.items||[];if(alive)setUpd({pending:items.filter(i=>i.state==='pending').length,failed:d.job?.status==='failed'&&d.job?.kind==='update'})}catch{}}get();const iv=setInterval(()=>{if(!document.hidden)get()},60000);return()=>{alive=false;clearInterval(iv)}},[]);
 useEffect(()=>{let alive=true;let controller;let running=false;async function load(){if(running)return;running=true;controller=new AbortController();const timer=setTimeout(()=>controller.abort(),12000);try{const r=await fetch('/api/telemetry',{signal:controller.signal,cache:'no-store'});if(!r.ok)throw Error();const d=await r.json();if(!alive)return;setData(d.updated_at?d:{});setError(false);}catch{if(alive)setError(true)}finally{clearTimeout(timer);running=false;if(alive)setRefreshing(false)}}loadRef.current=load;load();const iv=setInterval(()=>{if(!document.hidden)load()},5000);const clock=setInterval(()=>{if(!document.hidden)setNow(Date.now())},15000);
  // No polling or clock ticks while the app is in the background; refresh as soon as it is visible again.
  const onVisible=()=>{if(!document.hidden)load()};document.addEventListener('visibilitychange',onVisible);
  return()=>{alive=false;controller?.abort();clearInterval(iv);clearInterval(clock);document.removeEventListener('visibilitychange',onVisible)}},[]);
 useEffect(()=>{let alive=true,controller,running=false;async function load(){if(running)return;running=true;controller=new AbortController();const timer=setTimeout(()=>controller.abort(),12000);try{const r=await fetch(`/api/storage?window=${range}`,{signal:controller.signal,cache:'no-store'});if(!r.ok)throw Error();const d=await r.json();if(!Array.isArray(d.disks))throw Error();if(alive){setStorage(d);setStorageError(false)}}catch{if(alive)setStorageError(true)}finally{clearTimeout(timer);running=false}}storageRef.current=load;load();const iv=setInterval(()=>{if(!document.hidden)load()},10000);const vis=()=>{if(!document.hidden)load()};document.addEventListener('visibilitychange',vis);return()=>{alive=false;controller?.abort();clearInterval(iv);document.removeEventListener('visibilitychange',vis)}},[range]);
 useEffect(()=>{const onKey=ev=>{const editing=['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName);const modal=document.querySelector('.m3-dialog,.m3-nav-drawer.is-modal.open,.m3-xrail.is-modal');if(((ev.key==='k'&&(ev.metaKey||ev.ctrlKey))||(ev.key==='/'&&!editing))&&!modal){ev.preventDefault();if(command&&!cmdClosing)closeCommand();else{setCmdClosing(false);setCommand(true)}}if(ev.key==='Escape'){if(command)closeCommand();if(!busy)setDialog(null);setMenu(false);setRailModal(false)}};window.addEventListener('keydown',onKey);return()=>window.removeEventListener('keydown',onKey)},[busy,command,cmdClosing]);
 useEffect(()=>{if(!command){setSearch('');return}const previous=document.activeElement;requestAnimationFrame(()=>cmdRef.current?.focus());return()=>{if(previous?.isConnected)previous.focus()}},[command]);
 useEffect(()=>{if(!menu)return;const previous=document.activeElement;document.querySelector('.sidebar .nav-item')?.focus();return()=>{if(previous?.isConnected)previous.focus()}},[menu]);
 const fresh=!!data.updated_at&&!error&&now/1000-data.updated_at<45;
 const dashboardData={...data,ai:storage?.ai};
 // First load only: a page whose data hasn't arrived yet shows a skeleton; after a failure the normal states take over.
 const pageLoading=(['storage','media','ai','infra','network'].includes(view)&&!storage&&!storageError)||(view==='fleet'&&!fleet&&!fleetError);
 const known=catalog.filter(s=>{const st=serviceState(s,dashboardData);return st!==null&&st!=='unknown'}),online=known.filter(s=>serviceState(s,dashboardData)===true).length;
 const disks=storage?.disks||[];const driveFor=d=>(data.scrutiny?.drives||[]).find(x=>x.name===d?.device);const getDisk=id=>disks.find(d=>d.id===id)||{id,name:{scratch:'Proxmox scratch',array:'Unraid array',cache:'Unraid cache',parity:'Unraid parity'}[id]};
 const p=data.pve||{},u=data.unraid||{},dns=data.pihole||{},speed=data.speedtest||{},sys=data.syslog||{},sab=data.media?.sabnzbd||{};const dnsFresh=fresh&&sourceFresh(dns.primary)&&sourceFresh(dns.secondary),dnsLabel=!dnsFresh?'Unknown':dns.status==='mixed'?'Mixed':dns.blocking===true?'Protected':dns.blocking===false?'Paused':'Unknown';
 const alerts=Number(sys.warnings_24h||0)+Number(sys.errors_24h||0);const speedTiles=['speedtest','openspeedtest'];const displayed=catalog.filter(s=>(view!=='services'||category==='all'||s.group===category)&&(['overview','services'].includes(view)||(s.group===view&&!(view==='network'&&speedTiles.includes(s.id))))&&(filter==='all'||(filter==='pinned'?favorites.includes(s.id):filter==='online'?serviceState(s,dashboardData)===true:filter==='unmonitored'?serviceState(s,dashboardData)===null:filter==='stale'?serviceState(s,dashboardData)==='stale':serviceState(s,dashboardData)===false))&&`${s.name} ${s.desc} ${s.ip}`.toLowerCase().includes(search.toLowerCase()));
 // Alert snooze (30 Oct 2026): homelab-alerts, the Proxmox watchdog and the Unraid agent hold new alerts until it ends.
 const [snooze,setSnooze]=useState(null);
 const loadSnooze=async()=>{try{const r=await fetch('/api/maintenance',{cache:'no-store'});if(r.ok)setSnooze(await r.json())}catch{}};
 useEffect(()=>{loadSnooze();const iv=setInterval(()=>{if(!document.hidden)loadSnooze()},30000);return()=>clearInterval(iv)},[]);
 const snoozeFor=async minutes=>{const d=await postAction('/api/action/maintenance',{minutes}).catch(()=>({ok:false}));if(d.ok){setSnooze(d);setDialog(null);notify(minutes?`Alerts snoozed until ${new Date(d.until*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}.`:'Alerts are back on.')}else notify(d.error||'The snooze could not be saved.')};
 const snoozeUntil=snooze?.active?new Date(snooze.until*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):null;
 const openSnooze=()=>setDialog({type:'snooze',icon:'notifications_paused',title:'Snooze alerts?',text:'New problems found by homelab-alerts, the Proxmox watchdog and Unraid (except critical alerts) wait until the snooze ends, then alert if they are still there. Uptime Kuma, Scrutiny and Proxmox\'s own notifications still send.',choices:[30,120,240].map(m=>({label:m<60?`${m} min`:`${m/60} h`,run:()=>snoozeFor(m)}))});
 const snoozeButton=snooze?.active?<button className="m3-btn m3-btn--tonal m3-state secondary-button is-snoozed" onClick={()=>snoozeFor(0)} aria-label={`Alerts snoozed until ${snoozeUntil}. Resume alerts`}><Icon name="notifications_paused" size={16}/>Snoozed until {snoozeUntil} · Resume</button>:<button className="m3-btn m3-btn--tonal m3-state secondary-button" disabled={!snooze} onClick={openSnooze}><Icon name="notifications_paused" size={16}/>Snooze alerts</button>;
 const openDnsDialog=()=>setDialog({type:'dns',title:dns.blocking===false?'Resume DNS protection?':'Pause DNS protection for 5 minutes?',text:'This applies to both Pi-hole servers. Other devices on the network will be affected.',confirmLabel:dns.blocking===false?'Resume DNS':'Pause DNS'});
 const power=<PcPowerControl pc={storage?.ai?.desktop} ask={setDialog} notify={notify}/>;
 const issues=[];
 {const down=known.filter(s=>serviceState(s,dashboardData)===false);if(fresh&&down.length)issues.push({label:`${down.length} ${down.length===1?'service':'services'} down`,go:()=>navigate('services',null,{filter:'offline'})})}
 if(upd?.failed)issues.push({label:'Update run failed',go:()=>navigate('updates')});
 if(upd?.pending)issues.push({label:`${upd.pending} ${upd.pending===1?'update':'updates'} pending`,go:()=>navigate('updates'),soft:true});
 {const b=storage?.activity?.recovery?.backup;if(b?.latest?.status&&b.latest.status!=='OK')issues.push({label:'Backup job failed',go:()=>navigate('infra')})}
 if(fresh&&sourceFresh(sys)&&Number(sys.errors_24h)>0)issues.push({label:`${num(sys.errors_24h)} log ${Number(sys.errors_24h)===1?'error':'errors'}`,go:()=>navigate('infra')});
 {const bad=(data.scrutiny?.drives||[]).filter(d=>d.healthy===false);if(sourceFresh(data.scrutiny)&&bad.length)issues.push({label:'Drive health warning',go:()=>navigate('storage')})}
 {const t=Date.parse(String(speed.tested_at||'').replace(' ','T'));if(Number.isFinite(t)&&Date.now()-t>12*3600000)issues.push({label:'Speed tests failing',go:()=>navigate('network'),soft:true})}
 {const cb=fleet?.cloud_backup;if(cb&&!cb.running&&cb.last_failed_start)issues.push({label:'Cloud backup failed',go:()=>navigate('infra')});else if(cb?.last_success&&Date.now()/1000-cb.last_success>36*3600)issues.push({label:'Cloud backup stale',go:()=>navigate('infra')});else if((cb?.sources||[]).some(x=>x.last&&Date.now()/1000-x.last>x.stale_h*3600))issues.push({label:'Cloud backup stale',go:()=>navigate('infra')})}
 async function checkUpdates(){try{const d=await postAction('/api/updates',{action:'check'});notify(d.ok?'Checking for updates. Results appear on the Updates page.':(d.error||'The check could not start.'))}catch{notify('Could not reach the update service.')}}
 const pin=id=>setFavorites(old=>old.includes(id)?old.filter(x=>x!==id):[...old,id]);
 const rows=items=><div className="service-list">{items.map(s=><ServiceCard key={s.id} row service={s} data={dashboardData} favorite={favorites.includes(s.id)} onFavorite={pin}/>)}</div>;
 const SERVICE_GROUPS=[['media','Media'],['ai','AI'],['network','Network'],['infra','Infrastructure'],['cloud','Tools']];
 const grouped=items=>{const pinned=items.filter(s=>favorites.includes(s.id)),rest=items.filter(s=>!favorites.includes(s.id));return <div className="service-groups">{pinned.length>0&&<section className="service-group"><h3 className="service-group__title"><Icon name="star" size={18} filled/>Pinned <span className="count">{pinned.length}</span></h3>{rows(pinned)}</section>}{SERVICE_GROUPS.map(([g,l])=>{const list=rest.filter(s=>s.group===g);return list.length?<section className="service-group" key={g}><h3 className="service-group__title">{l} <span className="count">{list.length}</span></h3>{rows(list)}</section>:null})}</div>};
 const grid=items=><div className="service-grid">{items.map(s=><ServiceCard key={s.id} service={s} data={dashboardData} favorite={favorites.includes(s.id)} onFavorite={pin}/>)}</div>;
 const ranges=<div className="segments" role="group" aria-label="History range">{[[900,'15m'],[3600,'1h'],[21600,'6h'],[86400,'24h']].map(([v,label])=><button key={v} className={range===v?'selected':''} aria-pressed={range===v} onClick={()=>setRange(v)}>{label}</button>)}</div>;
 async function action(choice){if(!dialog)return;const run=choice?.run||dialog.run;if(run){setBusy(true);try{await run()}finally{setBusy(false)}return}setBusy(true);try{const res=await fetch(dialog.type==='dns'?'/api/action/pihole-toggle':'/api/action/syslog-clear',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Action':'1'},body:JSON.stringify(dialog.type==='dns'?{enable:dns.blocking===false,timer:300}:{})});const result=await res.json();if(!res.ok||!result.ok)throw Error();if(result.results&&Object.values(result.results).some(v=>v===false||v?.ok===false||v?.success===false))throw Error();notify('Request completed. Refreshing status.');setDialog(null);loadRef.current?.()}catch{notify('The action could not be confirmed. Check the service directly.')}finally{setBusy(false)}}
 async function copy(text){try{if(navigator.clipboard&&window.isSecureContext)await navigator.clipboard.writeText(text);else{const ta=document.createElement('textarea');ta.value=text;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.select();const ok=document.execCommand('copy');ta.remove();if(!ok)throw Error()}notify('Address copied')}catch{notify('Copy unavailable. Select the address to copy it.')}}
 const hostList=[...(p.guests||[]).map(g=>({...g,online:sourceFresh(p)?g.online:null,id:'p'+g.vmid,kind:g.type,detail:`Proxmox · ${g.vmid}`,ram:g.ram_mb})),...(u.containers||[]).map(g=>({...g,online:sourceFresh(u)?g.online:null,id:'u'+g.name,kind:'Docker',detail:'Unraid',ram:g.ram_mb}))].sort((a,b)=>(a.online===false)-(b.online===false));
 const filteredHosts=hostList.filter(h=>`${h.name} ${h.detail} ${h.kind}`.toLowerCase().includes(search.toLowerCase()));
 // Navigation: [id,label,icon]. SHORT labels are for the rail, bar and More sheet. PRIMARY order = compact bar (first 4).
 const SHORT={overview:'Overview',services:'Services',ai:'Local AI',media:'Media',network:'Network',fleet:'Hosts',storage:'Storage',infra:'Infra',updates:'Updates'};
 const PRIMARY=['overview','services','media','ai','network','storage'];
 const MORE_PAGES=['network','fleet','storage','infra','updates'];
 const navName=(id,label)=>id==='services'?`${label} ${catalog.length}`:label;
 const updBadge=upd?.pending>0?upd.pending:undefined;
 const drawerItems=modal=>navigation
  .filter(([id])=>(modal&&win==='compact')?MORE_PAGES.includes(id):true)
  .map(([id,label,icon])=><button key={id} type="button" className="m3-nav-drawer__item m3-state nav-item" onClick={()=>navigate(id)} aria-current={view===id?'page':undefined}><Icon name={icon} size={24}/><span className="m3-nav-drawer__label">{label}</span>{id==='services'&&<span className="m3-nav-drawer__badge">{catalog.length}</span>}{id==='updates'&&updBadge&&<span className="m3-nav-drawer__badge" aria-label={`${updBadge} pending`}>{updBadge}</span>}</button>);
 // Brand corner = logo + live status (Ryan, 27 Oct): what the homelab is doing right now, and a way back to Overview.
 const hard=issues.filter(i=>!i.soft).length;
 // Backup status needs both sources: fleet (cloud backup) and storage (Proxmox jobs, parity). Until both answer it is
 // 'checking'; if either failed or came back without backup data it is 'unknown'. Neither ever reads as All clear.
 const bkPending=(!fleet&&!fleetError)||(!storage&&!storageError),bkUnknown=!bkPending&&(!fleet?.cloud_backup||!storage?.activity?.recovery);
 const calm=bkPending?'Checking backups…':bkUnknown?'Backup status unknown':'All clear';
 const brandTitle=!fresh?'Waiting for data':hard?`${hard} ${hard===1?'thing needs':'things need'} attention`:calm;
 const brandSub=fresh?`${online} of ${known.length} monitored`:'Telemetry is reconnecting';
 const lastRead=valid(data.updated_at)?new Date(data.updated_at*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):null;
 const brand=cls=><a className={cls+' m3-brand'+(hard?' is-attention':'')+(!fresh?' is-stale':'')} href="#/overview" aria-label={`Overview. ${brandTitle}, ${brandSub}`} onClick={ev=>{ev.preventDefault();navigate('overview')}}><LogoMark size={32}/><span className="m3-brand__text"><strong>{brandTitle}</strong><small>{brandSub}</small></span></a>;
 const drawerBody=modal=><>{modal&&<span className="m3-sheet__handle" aria-hidden="true"/>}{modal&&<IconButton icon="close" label="Close navigation" className="m3-nav-drawer__close" touch onClick={()=>setMenu(false)}/>}{win==='compact'&&<h2 className="m3-sheet__title">More</h2>}{win!=='compact'&&brand('m3-nav-drawer__brand')}{drawerItems(modal)}{win!=='compact'&&<div className="m3-nav-drawer__footer"><Divider/><p className="m3-nav-drawer__meta m3-body-medium"><span className="live-dot"/> Local network 192.168.0.0/24 · {num(sourceFresh(data.netalertx)?data.netalertx.connected:null)} devices</p></div>}</>;
 const railItem=([id,label,icon],extra='')=><button key={id} type="button" className={'m3-nav-rail__item nav-item '+extra} onClick={()=>navigate(id)} aria-current={view===id?'page':undefined} aria-label={navName(id,label)+(id==='updates'&&updBadge?` ${updBadge} pending`:'')}><span className="m3-nav-indicator"><BadgeHost count={id==='updates'?updBadge:undefined}><Icon name={icon} size={24}/></BadgeHost></span><span className="m3-nav-label">{SHORT[id]}</span></button>;
 // Expressive navigation rail: collapsed 96px (icon in a 56x32 indicator, label below) or expanded (icon + label in a 56px pill).
 const railExpanded=win==='large'?railPref:(win==='expanded'&&railModal);
 const xrailItem=([id,label,icon])=><button key={id} type="button" className="m3-xrail__item nav-item" onClick={()=>navigate(id)} aria-current={view===id?'page':undefined} aria-label={navName(id,label)+(id==='updates'&&updBadge?` ${updBadge} pending`:'')}><span className="m3-nav-indicator m3-xrail__indicator"><BadgeHost count={!railExpanded&&id==='updates'?updBadge:undefined}><Icon name={icon} size={24}/></BadgeHost></span><span className="m3-xrail__label">{railExpanded?label:SHORT[id]}</span>{railExpanded&&id==='services'&&<span className="m3-xrail__count">{catalog.length}</span>}{railExpanded&&id==='updates'&&updBadge&&<span className="m3-xrail__count">{updBadge}</span>}</button>;
 const railItems=navigation;
 // The sheet's scrim fades out with the sheet (200 ms), then leaves the layout. A faded but still-displayed scrim made
 // Android's Back think a sheet was open, so Back did nothing on any page reached from More (Ryan, 30 Oct 2026).
 const modalDrawer=(win==='compact'||(win==='medium'&&menu))&&<><div className={'m3-scrim'+(menu?'':' is-leaving')} style={menu||!scrimGone?undefined:{display:'none'}} onClick={()=>setMenu(false)} aria-hidden="true"/><nav className={'m3-nav-drawer is-modal'+(win==='compact'?' sidebar is-sheet':'')+(menu?' open':'')} aria-label={win==='compact'?'More destinations':'Navigation menu'} onPointerDown={sheetDown} onPointerMove={sheetMove} onPointerUp={sheetUp} onPointerCancel={sheetUp} role={menu?'dialog':undefined} aria-modal={menu?'true':undefined} onKeyDown={trapTab} ref={el=>{if(el)el.inert=!menu}}>{drawerBody(true)}</nav></>;
 const serviceDirectory=<><div className="section-heading"><div><h2>Services <span className="count">{displayed.length}</span></h2></div><div className="directory-controls"><label className="inline-search"><Icon name="search" size={16}/><input aria-label="Filter services" placeholder="Filter services or IPs…" value={search} onChange={ev=>setSearch(ev.target.value)}/></label><MenuSelect aria-label="Service status filter" value={filter} onChange={v=>{haptic('tap');setFilter(v)}}
  options={[{value:'all',label:'All statuses'},{value:'pinned',label:'Pinned'},{value:'online',label:'Online'},{value:'offline',label:'Unreachable'},{value:'unmonitored',label:'Unmonitored'},{value:'stale',label:'Stale'}]}/></div></div>{view==='services'&&<div className="segments service-categories" role="group" aria-label="Service category">{[['all','All'],...SERVICE_GROUPS].map(([v,l])=><button key={v} className={category===v?'selected':''} aria-pressed={category===v} onClick={()=>setCategory(v)}>{l}</button>)}</div>}{displayed.length?(view==='services'?grouped(displayed):rows(displayed)):<div className="empty"><Icon name="search" size={30}/><h3>No matching services</h3><p>Try a different name or IP address.</p><button className="m3-btn m3-btn--tonal m3-state secondary-button" onClick={()=>{setSearch('');setFilter('all');setCategory('all')}}>Clear filters</button></div>}</>;
 const jump=id=>{const el=document.getElementById(id);if(!el)return;el.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});el.focus({preventScroll:true})};
 const shell=<>
 {(win==='expanded'||win==='large')&&<><nav className={'m3-xrail sidebar'+(railExpanded?' is-expanded':'')+(win==='expanded'&&railModal?' is-modal':'')} aria-label="Navigation" role={win==='expanded'&&railModal?'dialog':undefined} aria-modal={win==='expanded'&&railModal?'true':undefined} onKeyDown={win==='expanded'&&railModal?trapTab:undefined}>
  <div className="m3-xrail__top"><IconButton icon={railExpanded?'menu_open':'menu'} label={railExpanded?'Collapse navigation':'Expand navigation'} touch aria-expanded={railExpanded} onClick={()=>win==='large'?setRailPref(!railPref):setRailModal(!railModal)}/>{railExpanded&&brand('m3-xrail__brand')}</div>
  {navigation.map(n=>xrailItem(n))}
  {railExpanded&&<p className="m3-xrail__meta m3-body-small"><span className="live-dot"/> Local network · {num(sourceFresh(data.netalertx)?data.netalertx.connected:null)} devices</p>}
 </nav>{win==='expanded'&&railModal&&<div className="m3-scrim" onClick={()=>setRailModal(false)} aria-hidden="true"/>}</>}
 {win==='medium'&&<nav className="m3-nav-rail sidebar" aria-label="Navigation"><IconButton icon="menu" label="Open navigation" className="m3-nav-rail__menu" touch aria-expanded={menu} onClick={()=>setMenu(!menu)}/>{railItems.map(n=>railItem(n))}</nav>}
 </>;
 const topBar=<header className={'m3-top-app-bar topbar'+(scrolled?' is-scrolled':'')}>
  <span key={view} className="m3-top-app-bar__title" aria-hidden={!scrolled}>{view==='overview'?'Overview':navigation.find(n=>n[0]===view)?.[1]}</span>
  {(win==='expanded'||win==='large')&&<div className="m3-search-bar-wrap"><button type="button" className="m3-search-bar m3-state" aria-label="Find a service" onClick={()=>{haptic('tick');setCommand(true)}}><Icon name="search" size={24}/><span className="m3-search-bar__text">Find a service</span><Chip compact tone="neutral">⌘ K</Chip></button></div>}
  <div className="m3-top-app-bar__actions">
   {(win==='compact'||win==='medium')&&<IconButton icon="search" label="Find a service" touch onClick={()=>{haptic('tick');setCommand(true)}}/>}
   <Chip compact tone={fresh?'success':'warning'} icon={fresh?'check_circle':'schedule'} className="live-chip" title={fresh?'Live':'Sync pending'}><span className="live-chip__text">{fresh?'Live':'Sync pending'}</span></Chip>
   <IconButton icon="refresh" label="Refresh telemetry" touch className={refreshing?'spinning':''} onClick={()=>{setRefreshing(true);loadRef.current?.();storageRef.current?.()}}/>
   <IconButton icon="palette" label="Colours" touch onClick={()=>{haptic('tap');setPalette(true)}}/>
   <IconButton icon={theme==='dark'?'sun':'moon'} label={theme==='dark'?'Switch to light':'Switch to dark'} touch className="theme-toggle" onClick={()=>{haptic('tap');setTheme(v=>v==='dark'?'light':'dark')}}/>
  </div></header>;
 return <div className={`m3-app m3-app--${win}${win==='large'&&!railPref?' m3-app--rail-collapsed':''} app`}><a href="#main" className="skip">Skip to content</a>{shell}{modalDrawer}
 {/* --pull: pull is >0 only while actively dragging (live ramp); the instant it resets to 0 on release, hold at 72
    through refreshing/pullHold AND the gap before the leave effect runs. Deriving this from refreshing/pullHold/
    pullLeaving instead (as before) left one render where all three are false but pullShown still is: --pull fell to
    0, opacity snapped to 0 with no transition (only .is-leaving has one), and the later fade had nothing left to
    animate from. That gap's width depends on render timing, which is why the vanish was inconsistent. */}
 {win==='compact'&&pullShown&&<div className={'m3-pull'+(refreshing||pullHold?' is-refreshing':'')+(pullLeaving?' is-leaving':'')} style={{'--pull':pull||72}} aria-hidden="true"><LoadingIndicator contained label="Refreshing"/></div>}
 <main id="main" className="m3-app__main"><div className="m3-app__content">{topBar}<PageHeader large title={view==='overview'?'Overview':navigation.find(n=>n[0]===view)?.[1]} subtitle={[new Date(now).toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric'}),valid(data.weather?.temp_f)?[`${data.weather.temp_f}°F`,data.weather.desc].filter(Boolean).join(' · '):null].filter(Boolean).join(' · ')}/>
 {!fresh&&<div className="notice warning"><Icon name="clock" size={18}/><span>{error?'Connection interrupted. Showing the last received readings.':'Waiting for fresh telemetry. Values may be out of date.'}</span></div>}
 <SectionBoundary key={view}>{pageLoading?<PageSkeleton view={view}/>:<>{view==='overview'&&<>
 {/* 1. Health: the featured surface. Headline = the one thing to know; chips = what needs attention and where to go. */}
 <section className={'ov-hero'+(!fresh?' ov-hero--stale':hard?' ov-hero--attention':bkPending||bkUnknown?' ov-hero--checking':'')} aria-labelledby="ov-health">
  <div className="ov-hero__head"><span className="ov-hero__glyph"><Icon name={!fresh?'schedule':hard?'warning':bkPending?'schedule':bkUnknown?'help':'check_circle'} filled size={32}/></span>
   <div><p className="ov-hero__eyebrow">Homelab health</p><h2 id="ov-health" className="ov-hero__title">{!fresh?(lastRead?`Last reading ${lastRead}`:'Waiting for data'):hard?`${hard} ${hard===1?'thing needs':'things need'} attention`:calm}</h2>
   {fresh&&<p className="ov-hero__sub">{`${online} of ${known.length} monitored services reachable`}{dnsFresh?` · DNS ${dnsLabel.toLowerCase()}`:''}</p>}</div></div>
  {issues.length>0&&<div className={'status-line '+(issues.filter(i=>!i.soft).length?'warn':'ok')} role="status">{issues.map((i,k)=><button key={k} className={'issue-chip'+(i.soft?' soft':'')} onClick={i.go}>{i.label}<Icon name="arrow" size={14}/></button>)}</div>}
  <div className="summary-grid"><button className="summary-stat summary-link" onClick={()=>navigate('services')}><span className="summary-icon mint"><Icon name="grid"/></span><div><small>Services reachable</small><strong>{fresh?<CountUp value={online}/>:'—'}<em>/ {fresh?known.length:'—'}</em></strong><p>{fresh?`${catalog.length-known.length} without a direct monitor`:'Waiting for readings'}</p></div></button><button className="summary-stat summary-link" onClick={()=>navigate('network')}><span className="summary-icon blue"><Icon name="network"/></span><div><small>Devices connected</small><strong>{sourceFresh(data.netalertx)&&valid(data.netalertx.connected)?<CountUp value={data.netalertx.connected}/>:'—'}<em>/ {num(sourceFresh(data.netalertx)?data.netalertx.total:null)}</em></strong><p>Tracked on your local network</p></div></button><button className="summary-stat summary-link" onClick={()=>navigate('network')}><span className="summary-icon violet"><Icon name="shield"/></span><div><small>DNS protection</small><strong className="text-value">{dnsLabel}</strong><p>{num(dnsFresh?dns.blocked:null)} queries blocked</p></div></button><button className="summary-stat summary-link" onClick={()=>navigate('infra')}><span className="summary-icon amber"><Icon name="bell"/></span><div><small>Log events · 24h</small><strong>{sourceFresh(sys)?num(alerts):'—'}</strong><p>{num(sourceFresh(sys)?sys.errors_24h:null)} errors · {num(sourceFresh(sys)?sys.warnings_24h:null)} warnings</p></div></button></div>
 </section>
 {/* 2. Quick actions: the controls used most, straight under the health summary (they sat below six pinned cards on phones). */}
 <div className="ov-actions" role="group" aria-labelledby="ov-actions-h"><h3 id="ov-actions-h" className="ov-actions__title">Quick actions</h3><div className="quick-actions"><PcPowerControl pc={storage?.ai?.desktop} ask={setDialog} notify={notify} compact/><button className="m3-btn m3-btn--tonal m3-state secondary-button" disabled={!dnsFresh||typeof dns.blocking!=='boolean'} onClick={openDnsDialog}><Icon name="shield" size={16}/>{dns.blocking===false?'Resume DNS':'Pause DNS 5 min'}</button>{snoozeButton}<button className="m3-btn m3-btn--tonal m3-state secondary-button" onClick={checkUpdates}><Icon name="refresh" size={16}/>Check updates</button></div></div>
 {/* 3. Activity: one line per area; full telemetry lives on its own page. */}
 <section className="ov-activity" aria-labelledby="ov-activity"><div className="section-heading"><div><h2 id="ov-activity">Activity</h2></div></div>
  {storageError&&<div className="notice warning">Storage metrics are unavailable. The collector will retry.</div>}
  <ul className="m3-list ov-rows">{(()=>{const hs=storage?.network?.hosts||[],live=hs.filter(h=>h.online&&h.sampled_at&&Date.now()/1000-h.sampled_at<30),mb=v=>v>=1e9?`${num(v/1e9,2)} Gbps`:`${num(v/1e6,v<1e7?1:0)} Mbps`;
   const tx=live.reduce((n,h)=>n+(Number(h.tx_bps)||0),0),rx=live.reduce((n,h)=>n+(Number(h.rx_bps)||0),0);
   const ds=['array','cache','scratch'].map(getDisk).filter(d=>valid(d.used_pct)),fullest=ds.reduce((m,d)=>!m||d.used_pct>m.used_pct?d:m,null);
   const diskShort=d=>{const n=String(d.name||'Disk').replace('Unraid ','').replace('Proxmox ','');return n[0].toUpperCase()+n.slice(1)};
   const hostsOk=[['Proxmox',p],['Unraid',u]].filter(([,h])=>sourceFresh(h||{}));
   return <>
    <ListItem icon="network" headline="Network" supporting={live.length?<><strong className="ov-num">{live.length} of {hs.length}</strong> hosts live · ↑ <strong className="ov-num">{mb(tx)}</strong> · ↓ <strong className="ov-num">{mb(rx)}</strong></>:'Waiting for a reading'} trailing={live.length?'Live':''} onClick={()=>navigate('network')}/>
    <ListItem icon="disk" headline="Storage" supporting={ds.length?<>{ds.map((d,i)=><React.Fragment key={d.id}>{i?' · ':''}{diskShort(d)} <strong className="ov-num">{num(d.used_pct,0)}%</strong></React.Fragment>)}{fullest&&<LinearProgress value={fullest.used_pct} label={`${fullest.name} used`}/>}</>:(storage||storageError?'Capacity not reported':<Skeleton w="55%" h={14}/>)} trailing={fullest&&fullest.used_pct>=85?`${diskShort(fullest)} nearly full`:''} onClick={()=>navigate('storage')}/>
    <ListItem icon="server" headline="Hosts" supporting={hostsOk.length?hostsOk.map(([n,h])=><span className="ov-host" key={n}>{n} CPU <strong className="ov-num">{num(h.cpu,0)}%</strong> · RAM <strong className="ov-num">{num(h.ram_pct,0)}%</strong></span>):'No fresh reading'} onClick={()=>navigate('fleet')}/>
   </>})()}
  </ul>
  <div className="ov-links"><button className="m3-btn m3-btn--text m3-state text-button" onClick={()=>navigate('storage')}>All disks <Icon name="arrow" size={16}/></button><button className="m3-btn m3-btn--text m3-state text-button" onClick={()=>navigate('media')}>Downloads <Icon name="arrow" size={16}/></button><button className="m3-btn m3-btn--text m3-state text-button" onClick={()=>navigate('fleet')}>Hosts & containers <Icon name="arrow" size={16}/></button></div>
 </section>
 {/* 4. Favorites (two thirds) beside backup and review status (one third). */}
 <div className="ov-columns">
  <section className="ov-favorites"><div className="section-heading"><div><h2>Pinned services <span className="count">{favorites.length}</span></h2></div><button className="m3-btn m3-btn--text m3-state text-button" onClick={()=>navigate('services')}>All services <Icon name="arrow" size={16}/></button></div>{favorites.length?grid(catalog.filter(s=>favorites.includes(s.id))):<div className="empty">No pinned services. Use the stars in All services.</div>}</section>
  <aside className="ov-side" aria-label="Backups and reviews"><BackupSummary activity={storage?.activity} cb={fleet?.cloud_backup} failed={fleetError} onOpen={()=>navigate('infra','infra-recovery')}/><NightlySummary activity={storage?.activity} onOpen={()=>navigate('infra','infra-logs')}/></aside>
 </div>
 {/* 5. Ask AI: optional entry point, last so a long answer never pushes the controls and activity down. */}
 <AskQwen gpu={storage?.gpu} ai={storage?.ai} slim/>
 </>}
{view==='ai'&&<><LocalAI ai={storage?.ai} gpu={storage?.gpu} onOpen={()=>navigate('overview')} power={power} onNavigate={navigate}/><PcGpuPanel live={aiLive}/><AiHistoryPanel history={storage?.ai_history}/><GpuPanel gpu={storage?.gpu} ranges={ranges}/></>}
  {view==='updates'&&<UpdatesPanel notify={notify}/>}
 {storageError&&['storage','media','ai','network','overview'].includes(view)&&view!=='overview'&&<div className="notice warning" role="alert">Disk and network readings are unavailable right now. Retrying every 10 seconds.</div>}
 {fleetError&&!fleet&&['fleet','infra'].includes(view)&&<div className="notice warning" role="alert">Host and backup status is unavailable right now. Retrying every minute.</div>}
 {view==='storage'&&<><DiskSummary disks={['scratch','array','cache','parity'].map(getDisk)} scrutiny={data.scrutiny} onPick={(id,el)=>morph(()=>{setCompare(false);setDiskPick(id)},el,'.storage-grid .storage-card',()=>document.querySelector('.storage-grid')?.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'}))}/><div className="section-heading"><h2>History</h2></div><p className="source-note">Download speeds and scratch-disk activity are on <button className="text-button inline" onClick={()=>navigate('media')}>Media & downloads</button>.</p><div className="storage-toolbar">{ranges}<div className="segments" role="group" aria-label="Disks shown"><button className={!compare?'selected':''} aria-pressed={!compare} onClick={()=>setCompare(false)}>One disk</button><button className={compare?'selected':''} aria-pressed={compare} onClick={()=>setCompare(true)}>Compare</button></div><div className="segments" role="group" aria-label="Chart content"><button className={!capacity?'selected':''} aria-pressed={!capacity} onClick={()=>setCapacity(false)}>Throughput</button><button className={capacity?'selected':''} aria-pressed={capacity} onClick={()=>setCapacity(true)}>Capacity used</button></div></div>{!compare&&<div className="segments disk-pick" role="group" aria-label="Disk">{['scratch','array','cache','parity'].map(id=><button key={id} className={diskPick===id?'selected':''} aria-pressed={diskPick===id} onClick={()=>setDiskPick(id)}>{getDisk(id).name}</button>)}</div>}<div className={'storage-grid'+(compare?' two-grid':' is-single')}>{(compare?['scratch','array','cache','parity']:[diskPick]).map(id=><StorageCard key={id} disk={getDisk(id)} drive={driveFor(getDisk(id))} capacity={capacity}/>)}</div><details className="storage-explainer"><summary>Metric details</summary><p>~10s samples · 24h retention · longer ranges averaged. Physical disk I/O; parity separate. Busy, latency and queue use kernel counters. SMB transfer speed is not measured separately. {storage?.history_since?`History starts ${new Date(storage.history_since*1000).toLocaleString()}.`:"Waiting for samples."}</p></details><div className="section-heading"><h2>Drive health</h2><a className="m3-btn m3-btn--text m3-state text-button" href="http://192.168.0.4:8080" target="_blank" rel="noreferrer">Open Scrutiny <Icon name="external" size={15}/></a></div><SourceNote source={data.scrutiny}/>{fleet?.smart&&<p className="source-note">Unraid self-tests run automatically at 5:10 AM: extended every {fleet.smart.extended_every_days} days on array disks, short every {fleet.smart.short_every_days} days on the cache SSD, never on Sundays or during a parity check.</p>}<div className="drive-list">{(data.scrutiny?.drives||[]).map((d,i)=><div className="drive-row" key={d.name+i}><Icon name="disk"/><div><strong>{d.model}</strong><p>{d.name}{/\d\s*[GT]B\b/i.test(d.model||'')?'':` · ${d.cap}`} · {num(d.hours)} power-on hours</p><SmartDetail s={(fleet?.smart?.disks||[]).find(x=>x.device===d.name)}/></div><span className="mono">{num(sourceFresh(data.scrutiny)?d.temp:null)}°C</span><Badge state={sourceFresh(data.scrutiny)?d.healthy:null} label={!sourceFresh(data.scrutiny)?'Unknown':d.healthy===true?'SMART OK':d.healthy===false?'Check SMART':'Unknown'}/></div>)}</div></>}
 {view==='network'&&<><div className="two-grid net-status"><article className="m3-card m3-card--outlined panel"><header className="panel-header"><div className="panel-title"><span className="icon-tile"><Icon name="wifi"/></span><div><h3>Internet connection</h3><p>Latest Speedtest Tracker result</p></div></div><SourceBadge source={speed} onlineLabel="API online"/></header><div className="wan-speeds"><div><small><Icon name="down" size={15}/>Download</small><strong>{num(speed.dl/1000,2)}<em>Gbps</em></strong></div><div><small><Icon name="up" size={15}/>Upload</small><strong>{num(speed.ul)}<em>Mbps</em></strong></div></div><div className="host-footer"><span>{num(speed.ping,1)} ms latency · {speed.server||'—'}{testedLabel(speed.tested_at)}</span><span className="speed-links"><a href="http://192.168.0.13:3000" target="_blank" rel="noreferrer">LAN test <Icon name="external" size={13}/></a><a href="http://192.168.0.14" target="_blank" rel="noreferrer">History <Icon name="external" size={13}/></a></span></div><SourceNote source={speed}/><button className="copy-ip" onClick={()=>data.wan?.ip&&copy(data.wan.ip)}><span>WAN <span className="mono">{data.wan?.ip||'—'}</span></span><Icon name="copy" size={14}/></button></article><article className="m3-card m3-card--outlined panel"><header className="panel-header"><div className="panel-title"><span className="icon-tile"><Icon name="shield"/></span><div><h3>Pi-hole cluster</h3><p>VRRP VIP .8 · Primary + replica DNS filtering</p></div></div><Badge state={dnsFresh?dns.blocking:null} label={dnsLabel}/></header><div className="dns-stat"><strong>{num(dnsFresh?dns.pct:null,1)}<em>% blocked</em></strong><p>{num(dnsFresh?dns.queries:null)} total queries · {num(dnsFresh?dns.domains:null)} domains</p></div><div className="dns-nodes"><span><i className={(data.services?.pihole_vip??(sourceFresh(dns.primary)||sourceFresh(dns.secondary)))?'tiny-dot':'tiny-dot unknown'}/>VIP .8</span><span><i className={sourceFresh(dns.primary)?'tiny-dot':'tiny-dot unknown'}/>Primary .11</span><span><i className={sourceFresh(dns.secondary)?'tiny-dot':'tiny-dot unknown'}/>Replica .10</span></div><button className="m3-btn m3-btn--tonal m3-state secondary-button" disabled={!dnsFresh||typeof dns.blocking!=='boolean'} onClick={openDnsDialog}>{dns.blocking===false?'Resume blocking':'Pause DNS 5 min'}</button></article></div><NetworkPanel network={storage?.network} ranges={ranges}/></>}
 {view==='media'&&<><MediaSummary data={data} activity={storage?.activity}/><NowPlaying jf={data.media?.jellyfin}/><DownloadsPanel notify={notify}/><ActivityPanel activity={storage?.activity} ranges={ranges}/><details className="storage-explainer"><summary>Playback setup</summary><p>Jellyfin transcodes on the Intel Arc A310 (QSV): 5 simultaneous 4K HDR remux → 1080p streams in real time, limited by the data disk. Throttled transcoding, seek thumbnails refreshed Wednesdays at 10 AM, library scanned hourly, array disks never spin down.</p></details></>}
 {['services','network','media'].includes(view)&&serviceDirectory}
 {view==='infra'&&<><div className="infra-summary"><BackupSummary activity={storage?.activity} cb={fleet?.cloud_backup} failed={fleetError} onOpen={()=>jump('infra-recovery')}/><NightlySummary activity={storage?.activity} onOpen={()=>jump('infra-logs')}/></div><div className="section-jumps" role="group" aria-label="Jump to a section">{[['infra-recovery','Recovery','backup'],['infra-energy','Energy','bolt'],['infra-logs','Logs & review','receipt_long'],['infra-changes','Changes','history'],['infra-services','Services','apps']].map(([id,l,ic])=><button key={id} type="button" className="section-jump m3-state" onClick={()=>jump(id)}><Icon name={ic} size={18}/>{l}</button>)}</div><section id="infra-recovery" className="infra-section" tabIndex={-1} aria-label="Recovery"><RecoveryPanel activity={storage?.activity}><OffsiteCard cb={fleet?.cloud_backup} failed={fleetError}/></RecoveryPanel><RecoveryReadiness cb={fleet?.cloud_backup} activity={storage?.activity} failed={fleetError}/></section><section id="infra-energy" className="infra-section" tabIndex={-1} aria-label="Energy and cost"><EnergyPanel energy={storage?.energy}/></section><section id="infra-logs" className="infra-section" tabIndex={-1} aria-label="Logs and review"><NightlyReview activity={storage?.activity}/><LogGroups source={sys}/><div className="section-heading"><h2>System log summary</h2><a className="m3-btn m3-btn--text m3-state text-button" href="http://192.168.0.3:8999" target="_blank" rel="noreferrer">Inspect logs <Icon name="external" size={15}/></a></div><article className="m3-card m3-card--outlined panel"><div className="log-summary"><Icon name="bell"/><div><h3>{num(sourceFresh(sys)?sys.warnings_24h:null)} warnings · {num(sourceFresh(sys)?sys.errors_24h:null)} errors</h3><p>{sourceFresh(sys)?'Counts from the log monitor. Repeated entries are included.':'Log source unavailable.'}</p><small>{num(sourceFresh(sys)?sys.hosts_reporting:null)} reporting hosts · past 24 hours</small></div><button className="m3-btn m3-btn--tonal m3-state secondary-button" disabled={!sourceFresh(sys)} onClick={()=>setDialog({type:'logs',title:'Clear the current log alerts?',text:'This clears the current 24-hour alert summary in your syslog monitor.',confirmLabel:'Clear alerts'})}>Clear alerts</button></div></article></section><section id="infra-changes" className="infra-section" tabIndex={-1} aria-label="Changes"><Timeline/></section><section id="infra-services" className="infra-section" tabIndex={-1} aria-label="Infrastructure services">{serviceDirectory}</section></>}
 {view==='fleet'&&<><div className="section-heading"><h2>Hosts</h2></div><div className="host-grid"><HostCard name="Proxmox" ip="192.168.0.3" host={p}/><HostCard name="Unraid" ip="192.168.0.4" host={u} icon="layers"/><PiCard pi={fleet?.pi} observedAt={fleet?.observed_at}/></div><div className="section-heading"><h2>Compute fleet <span className="count">{filteredHosts.length} / {hostList.length}</span></h2><label className="inline-search"><Icon name="search" size={16}/><input aria-label="Filter hosts and containers" placeholder="Find a host…" value={search} onChange={ev=>setSearch(ev.target.value)}/></label></div><div className="fleet-table" role="table" aria-label="Hosts and containers">{!filteredHosts.length&&<div className="empty"><h3>No matching hosts or containers</h3><button className="m3-btn m3-btn--tonal m3-state secondary-button" onClick={()=>setSearch('')}>Clear search</button></div>}<div className="fleet-row table-head" role="row"><span role="columnheader">NAME</span><span role="columnheader">PLATFORM</span><span role="columnheader">CPU</span><span role="columnheader">MEMORY</span><span role="columnheader">STATE</span></div>{filteredHosts.map(h=><div className="fleet-row" role="row" key={h.id}><span className="fleet-name" role="rowheader"><span className="icon-tile"><Icon name={h.kind==='Docker'?'layers':'server'} size={17}/></span><strong>{h.name}<small>{h.detail}</small></strong></span><span role="cell">{h.kind}</span><span role="cell" className="mono">{h.online==null?'—':`${num(h.cpu,1)}%`}</span><span role="cell" className="mono">{h.online==null?'—':`${num(h.ram)} MiB`}</span><span role="cell"><Badge state={h.online===false?null:h.online} label={h.online===true?'Running':h.online===false?'Stopped':'Unknown'}/></span></div>)}</div></>}
 {(view==='network'&&(!search||(data.ip_matrix||[]).some(h=>`${h.name} ${h.ip}`.toLowerCase().includes(search.toLowerCase()))))&&<><div className="section-heading"><h2>Network map</h2><span className="mono muted">192.168.0.0/24</span></div><div className="network-map">{(data.ip_matrix||[]).filter(h=>`${h.name} ${h.ip}`.toLowerCase().includes(search.toLowerCase())).map(h=><a href={h.url} key={h.ip} target="_blank" rel="noreferrer"><span className="ip-square">{h.octet}</span><div><strong>{h.name}</strong><small>{h.role}</small></div><i className={h.online?'tiny-dot':'tiny-dot unknown'}/></a>)}</div></>}
 </>}</SectionBoundary>  <footer className="page-footer"><span>{fresh?'Updated':'Last sample'} {data.updated_at?new Date(data.updated_at*1000).toLocaleTimeString():'—'} <i className={fresh?'tiny-dot':'tiny-dot unknown'}/></span></footer>
 </div></main>
 {win==='compact'&&<nav className="m3-nav-bar" aria-label="Primary" aria-hidden={menu?'true':undefined}>{navigation.filter(n=>PRIMARY.slice(0,4).includes(n[0])).map(([id,label,icon])=><button key={id} type="button" className="m3-nav-bar__item nav-item" onClick={()=>navigate(id)} aria-current={view===id?'page':undefined} aria-label={navName(id,label)}><span className="m3-nav-indicator"><Icon name={icon} size={24}/></span><span className="m3-nav-label">{SHORT[id]}</span>{id==='services'&&<span className="m3-visually-hidden">All services</span>}</button>)}<button type="button" className="m3-nav-bar__item" aria-haspopup="dialog" onClick={()=>{haptic('tap');setSheet(true);setMenu(true)}} aria-current={!PRIMARY.slice(0,4).includes(view)?'page':undefined}><span className="m3-nav-indicator"><Icon name="more_horiz" size={24}/></span><span className="m3-nav-label">More</span></button></nav>}
 {(command||cmdClosing)&&<><div className={'m3-scrim'+(cmdClosing?' is-leaving':'')} onClick={closeCommand} aria-hidden="true"/><div className={'m3-search-view'+(cmdClosing?' is-closing':'')} role="dialog" aria-modal="true" aria-label="Find a service" onAnimationEnd={ev=>{if(ev.target===ev.currentTarget&&cmdClosing){setCmdClosing(false);setCommand(false)}}} onKeyDown={ev=>{if(ev.key==='Tab'){const nodes=tabbables(ev.currentTarget);if(ev.shiftKey&&document.activeElement===nodes[0]){ev.preventDefault();nodes.at(-1)?.focus()}else if(!ev.shiftKey&&document.activeElement===nodes.at(-1)){ev.preventDefault();nodes[0]?.focus()}}}}><div className="m3-search-view__header"><IconButton icon="arrow_back" label="Close search" onClick={closeCommand}/><input className="m3-search-view__input" ref={cmdRef} aria-label="Search service names and IPs" value={search} onChange={ev=>setSearch(ev.target.value)} placeholder="Search services or IPs"/>{search&&<IconButton icon="close" label="Clear search text" onClick={()=>{setSearch('');cmdRef.current?.focus()}}/>}</div>{(()=>{const hits=catalog.filter(s=>`${s.name} ${s.desc} ${s.ip}`.toLowerCase().includes(search.toLowerCase()));return <><p className="m3-visually-hidden" role="status" aria-live="polite">{search?`${hits.length} ${hits.length===1?'result':'results'}`:''}</p>{!hits.length&&<div className="m3-search-view__empty"><p>No services match “{search}”.</p><Button variant="text" icon="close" onClick={()=>{setSearch('');cmdRef.current?.focus()}}>Clear search</Button></div>}</>})()}<ul className="m3-list m3-search-view__results command-results">{catalog.filter(s=>`${s.name} ${s.desc} ${s.ip}`.toLowerCase().includes(search.toLowerCase())).map(s=><li key={s.id}><a className="m3-list-item m3-list-item--two-line m3-state" href={s.url} target="_blank" rel="noreferrer" onClick={closeCommand} data-accent={s.accent}><span className="m3-accent-icon"><Icon name={s.icon}/></span><span className="m3-list-item__body"><span className="m3-list-item__headline">{s.name}</span><span className="m3-list-item__supporting">{s.ip}</span></span><Icon name="external" size={20}/></a></li>)}</ul></div></>}
 {dialog&&<ConfirmDialog dialog={dialog} busy={busy} close={()=>!busy&&setDialog(null)} confirm={action}/>}
 {palette&&<ColourDialog pref={seedPref} wall={wallSeed} onPick={v=>{haptic('tick');setSeedPref(v)}} close={()=>setPalette(false)}/>}
 <div className="m3-snackbar-region" role="status" aria-live="polite">{toast&&<Snackbar>{toast}</Snackbar>}</div>
 </div>
}
// Jellyfin sessions from telemetry (sanitized server-side). Posters come through the dashboard, never with the token.
function NowPlaying({jf}){
 const list=sourceFresh(jf||{})&&Array.isArray(jf.now_playing)?jf.now_playing:[];
 if(!list.length)return null;
 const left=s=>{const m=Math.round(s/60);return m>=60?`${Math.floor(m/60)} h ${m%60} min left`:`${m} min left`};
 return <section className="now-playing" aria-labelledby="np-h"><div className="section-heading"><h2 id="np-h">Now playing <span className="count">{list.length}</span></h2></div>
  <div className="np-list">{list.map((n,i)=>{const ep=n.series?`S${n.season??'?'}E${n.episode??'?'}`:null;const transcoding=n.method==='Transcode';
   return <article className="m3-card m3-card--outlined panel np-card" key={i}>
    {n.poster?<img className="np-poster" src={`/api/media/poster/${n.poster}`} alt="" loading="lazy" onError={e=>{e.currentTarget.style.visibility='hidden'}}/>:<span className="np-poster np-poster--empty"><Icon name="play" size={28}/></span>}
    <div className="np-body"><strong className="np-title">{n.series||n.title}</strong>
     <small>{n.series?`${ep} · ${n.title}`:n.year||''}</small>
     <small>{[n.user,n.device].filter(Boolean).join(' on ')}</small>
     <div className="np-chips"><Chip compact tone={transcoding?'info':'success'} icon={transcoding?'refresh':'check'}>{transcoding?`Transcoding${n.height?` · ${n.height}p`:''}`:n.method==='DirectStream'?'Direct stream':'Direct play'}</Chip>{n.paused&&<Chip compact tone="neutral" icon="schedule">Paused</Chip>}</div>
     {transcoding&&n.reasons?.length>0&&<small className="np-reason">{n.reasons.map(r=>r.replace(/([a-z])([A-Z])/g,'$1 $2').toLowerCase()).join(', ')}</small>}
     {valid(n.progress)&&<><LinearProgress value={n.progress*100} label={`${n.title} progress`}/><small>{n.runtime_s?left(n.runtime_s*(1-n.progress)):`${Math.round(n.progress*100)}%`}</small></>}</div>
   </article>})}</div></section>}

// Material You colour choices. The wallpaper seed comes from the Android app's bridge (system_accent1_500, Android 12+).
const COLOUR_PRESETS=[['pine','Pine',PINE],['#1d5c8a','Ocean','#1d5c8a'],['#3f4fa8','Indigo','#3f4fa8'],['#6b4ca3','Violet','#6b4ca3'],['#9c3d5a','Rose','#9c3d5a'],
 ['#a04a2a','Ember','#a04a2a'],['#8a6a1a','Amber','#8a6a1a'],['#4f6b3a','Sage','#4f6b3a'],['#4a5560','Slate','#4a5560']];
// Android 14+: the exact Material You roles other apps use, light and dark ({light:{primary:'#…',…},dark:{…}}).
function readSystemColors(){try{const v=window.AndroidBridge?.getSystemColors?.();const d=v?JSON.parse(v):null;return d&&d.light&&d.dark?d:null}catch{return null}}
function readWallpaperSeed(){try{const v=window.AndroidBridge?.getWallpaperSeed?.();return /^#[0-9a-f]{6}$/i.test(v||'')?v:null}catch{return null}}
function resolveSeed(pref,wall){if(pref==='auto')return wall;if(pref==='pine')return null;return /^#[0-9a-f]{6}$/i.test(pref||'')?pref:null}
function ColourDialog({pref,wall,onPick,close}){
 const active=pref==='auto'&&!wall?'pine':pref,custom=/^#[0-9a-f]{6}$/i.test(pref)&&!COLOUR_PRESETS.some(p=>p[0]===pref);
 const opts=[...(wall?[['auto','Wallpaper',wall,'wallpaper']]:[]),...COLOUR_PRESETS];
 return <Dialog headline="Colours" icon="palette" labelId="colour-title" onClose={close} actions={<Button variant="text" onClick={close}>Done</Button>}>
  <p className="source-note" style={{margin:'0 0 16px'}}>{wall?'Wallpaper matches your phone, like other Material You apps.':'The whole dashboard is generated from the colour you pick.'} Saved on this device.</p>
  <div className="colour-grid" role="radiogroup" aria-label="Colour">{opts.map(([id,label,hex,icon])=><button key={id} type="button" role="radio" aria-checked={active===id} className={'colour-swatch m3-state'+(active===id?' is-selected':'')} onClick={()=>onPick(id)}>
    <span className="colour-swatch__dot" style={{background:hex}}>{active===id?<Icon name="check" size={20}/>:icon?<Icon name={icon} size={18}/>:null}</span><span>{label}</span></button>)}
   <label className={'colour-swatch m3-state'+(custom?' is-selected':'')}><span className="colour-swatch__dot" style={{background:custom?pref:'conic-gradient(#e57373,#ffd54f,#81c784,#4fc3f7,#9575cd,#e57373)'}}>{custom?<Icon name="check" size={20}/>:<Icon name="colorize" size={18}/>}</span><span>Custom</span>
    <input type="color" className="colour-swatch__input" value={custom?pref:PINE} onChange={e=>onPick(e.target.value)} aria-label="Pick a custom colour"/></label></div>
 </Dialog>}

// Manual downloads: one field that searches Prowlarr's indexers, or adds a pasted magnet straight to RdtClient.
// Grabs and magnets land in the 'prowlarr' category, the same folder Prowlarr's own Search page uses.
const bytes=v=>!valid(v)?'—':v>=1e12?`${num(v/1e12,2)} TB`:v>=1e9?`${num(v/1e9,1)} GB`:`${num(v/1e6,0)} MB`;
const ago=t=>{const d=(Date.now()-Date.parse(t))/864e5;return !Number.isFinite(d)?'':d<1?'today':d<60?`${Math.floor(d)}d ago`:d<730?`${Math.floor(d/30)}mo ago`:`${Math.floor(d/365)}y ago`};
const dlState=t=>t.progress>=1||/UP$|^uploading$/.test(t.state||'')?['Done',true]:t.error||/error|missing/i.test(t.state||'')?['Error',false]:t.stage==='debrid'?[/^meta/.test(t.state||'')?'Finding peers':'At debrid',null]:t.stage==='local'?['Downloading',null]:t.stage==='finishing'?['Finishing',null]:/^meta/.test(t.state||'')?['Fetching info',null]:/^queued|^paused|^stopped/.test(t.state||'')?['Queued',null]:['Downloading',null];
// Last RdtClient list, kept across visits so the Media page doesn't flash a placeholder each time.
let lastRecent=null;
function DownloadsPanel({notify=()=>{}}){
 const [text,setText]=useState(''),[kind,setKind]=useState('torrent'),[results,setResults]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[sent,setSent]=useState({}),[shown,setShown]=useState(15),[recent,setRecent]=useState(()=>lastRecent);
 const isMagnet=/^magnet:\?/i.test(text.trim());
 const poll=useRef(null),seq=useRef(0),ctl=useRef(null);
 useEffect(()=>{let alive=true,timer;async function get(){try{const r=await fetch('/api/downloads',{cache:'no-store',signal:AbortSignal.timeout(15000)});const d=await r.json();const items=r.ok&&Array.isArray(d?.items)?d.items:null;if(items)lastRecent=items;
   // Android app: something is downloading, so keep a progress notification going after the app is left.
   if(items?.some(t=>(t.progress??1)<1&&!/error/i.test(t.state||''))){try{window.AndroidBridge?.watchDownloads?.()}catch{}}if(alive)setRecent(items||{error:d?.error||'RdtClient is not answering.'})}catch{if(alive)setRecent({error:'RdtClient is not answering.'})}finally{if(alive)timer=setTimeout(get,document.hidden?30000:5000)}}
  poll.current=()=>{clearTimeout(timer);get()};get();return()=>{alive=false;clearTimeout(timer)}},[]);
 // Each search cancels the one before it (switching Debrid/Usenet mid-search can't show the other kind's results) and
 // gives up after 45 s, so a hung indexer never locks the panel. Errors are worded for people, not raw browser text.
 async function search(k=kind){const q=text.trim();if(q.length<2)return;const id=++seq.current;ctl.current?.abort();const c=new AbortController();ctl.current=c;const t=setTimeout(()=>c.abort(),45000);
  setBusy(true);setError('');setShown(15);
  try{const r=await fetch('/api/downloads/search?'+new URLSearchParams({q,kind:k}),{cache:'no-store',signal:c.signal});let d;try{d=await r.json()}catch{throw Error(r.ok?'The search came back unreadable. Try again.':'The search service did not answer properly. Try again in a minute.')}
   if(!r.ok)throw Error(d?.error||'Search failed.');if(id===seq.current)setResults({q,kind:k,list:Array.isArray(d?.results)?d.results:[]})}
  catch(e){if(id!==seq.current)return;setError(c.signal.aborted?'The search took too long. Try again.':e instanceof TypeError?'Could not reach the dashboard.':e.message||'Search failed.');setResults(null)}
  finally{clearTimeout(t);if(id===seq.current)setBusy(false)}}
 async function addMagnet(){setBusy(true);setError('');const d=await postAction('/api/action/download',{magnet:text.trim()}).catch(()=>({ok:false,error:'Could not reach the dashboard.'}));setBusy(false);
  if(d.ok){notify('Magnet sent to RdtClient.');setText('');setTimeout(()=>poll.current?.(),1500)}else setError(d.error||'RdtClient did not accept the magnet.')}
 async function grab(r){setSent(m=>({...m,[r.id]:'busy'}));const d=await postAction('/api/action/download',{result:r.id}).catch(()=>({ok:false,error:'Could not reach the dashboard.'}));
  setSent(m=>({...m,[r.id]:d.ok?'done':undefined}));if(d.ok){notify(results?.kind==='usenet'?'Sent to SABnzbd.':'Sent to RdtClient.');setTimeout(()=>poll.current?.(),2500)}else notify(d.error||'Could not send it.')}
 function submit(e){e.preventDefault();if(busy)return;isMagnet?addMagnet():search()}
 const list=results?.list||[];
 return <section className="downloads-section" aria-labelledby="dl-h"><div className="section-heading"><h2 id="dl-h">Add a download</h2></div>
  <article className="m3-card m3-card--outlined panel downloads-panel">
   <form className="dl-form" onSubmit={submit}><TextField icon={isMagnet?'link':'search'} aria-label="Search indexers or paste a magnet link" placeholder="Search, or paste a magnet link" value={text} onChange={e=>setText(e.target.value)} onClear={()=>{setText('');setResults(null);setError('')}} clearLabel="Clear" enterKeyHint={isMagnet?'send':'search'} autoComplete="off" spellCheck="false"/>
    <div className="dl-controls">{!isMagnet&&<Segmented label="Source" value={kind} onChange={k=>{setKind(k);if(results||busy)search(k)}} options={[['torrent','Debrid'],['usenet','Usenet']]}/>}
     <Button type="submit" variant="filled" icon={isMagnet?'download':'search'} disabled={busy||(isMagnet?false:text.trim().length<2)}>{busy?(isMagnet?'Sending…':'Searching…'):isMagnet?'Add magnet':'Search'}</Button></div></form>
   {error&&<div className="notice warning" role="alert">{error}</div>}
   {busy&&!isMagnet&&<IndeterminateProgress label="Searching indexers"/>}
   {results&&<div className="dl-results" aria-live="polite"><h3 className="dl-subhead">{list.length?`${list.length} results for “${results.q}”`:`Nothing found for “${results.q}”`}</h3>
    {list.slice(0,shown).map(r=>{const st=sent[r.id];return <div className="dl-row" key={r.id}><div className="dl-row__text"><strong>{r.title}</strong><small><span className="nowrap"><strong className="ov-num">{bytes(r.size)}</strong></span>{results.kind==='torrent'?<span className="nowrap"><strong className="ov-num">{num(r.seeders)}</strong> seeders</span>:valid(r.grabs)&&<span className="nowrap"><strong className="ov-num">{num(r.grabs)}</strong> grabs</span>}<span className="nowrap">{r.indexer}</span>{r.published&&<span className="nowrap">{ago(r.published)}</span>}</small></div>
     <Button variant={st==='done'?'text':'tonal'} icon={st==='done'?'check':'download'} disabled={!!st} onClick={()=>grab(r)} aria-label={`Download ${r.title}`}>{st==='done'?'Sent':st==='busy'?'Sending…':'Get'}</Button></div>})}
    {list.length>shown&&<button className="m3-btn m3-btn--text m3-state text-button" onClick={()=>setShown(n=>n+15)}>Show {Math.min(15,list.length-shown)} more</button>}</div>}
   <div className="dl-recent"><h3 className="dl-subhead">Recently added to RdtClient</h3>
    {recent===null?<Skeleton h={40}/>:recent.error?<p className="source-note">{recent.error}</p>:!recent.length?<div className="empty">Nothing yet.</div>:
     recent.map((t,i)=>{const [label,tone]=dlState(t),pct=Math.floor((t.progress||0)*1000)/10;return <div className="dl-row dl-row--recent" key={i}><div className="dl-row__text"><strong>{t.name}</strong><small><span className="nowrap"><strong className="ov-num">{bytes(t.size)}</strong></span>{tone===null&&label!=='Done'&&<span className="nowrap">{t.stage==='debrid'?'Debrid ':t.stage==='local'?'To disk ':''}<strong className="ov-num">{pct}%</strong>{t.dlspeed>0&&<> · {rate(t.dlspeed)} MB/s</>}{t.stage==='debrid'&&valid(t.seeders)&&<> · {num(t.seeders)} seeders</>}</span>}{t.error&&<span>{t.error}</span>}{t.category&&<span className="nowrap">{t.category}</span>}</small>{tone===null&&label!=='Queued'&&<LinearProgress value={pct} label={`${t.name} progress`}/>}</div><Badge state={tone} label={label}/></div>})}</div>
  </article></section>}
// Last /api/updates answer, shared by the sidebar badge poll and the Updates page, so returning to the page shows the
// list at once and refreshes it quietly instead of flashing placeholders.
let lastUpdates=null;
function UpdatesPanel({notify=()=>{}}){
 const [state,setState]=useState(()=>lastUpdates),[error,setError]=useState(''),[confirm,setConfirm]=useState(false),[busy,setBusy]=useState(false),[note,setNote]=useState('');
 const running=state?.job?.status==='running';
 const load=useRef(null);
 useEffect(()=>{let alive=true,timer;async function get(){if(document.hidden&&load.current!=='running'){timer=setTimeout(get,20000);return}try{const r=await fetch('/api/updates',{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error();const d=await r.json();lastUpdates=d;if(alive){setState(d);setError('')}}catch{if(alive)setError('Update status unavailable. Retrying.')}finally{if(alive)timer=setTimeout(get,load.current==='running'?3000:20000)}}
  load.get=()=>{clearTimeout(timer);get()};get();return()=>{alive=false;clearTimeout(timer)}},[]);
 useEffect(()=>{load.current=running?'running':'idle'},[running]);
 async function send(action){setBusy(true);setNote('');try{const r=await fetch('/api/updates',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Action':'1'},body:JSON.stringify({action})});const d=await r.json();if(!r.ok||!d.ok){setNote(d.error||'The request was not accepted.');notify(d.error||'The request was not accepted.')}else notify(action==='check'?'Checking for updates.':'Update run started. Guests update one at a time.')}catch{setNote('Could not confirm the request. Check status before retrying.');notify('Could not confirm the request. Check status before retrying.')}finally{setBusy(false);setConfirm(false);setTimeout(()=>load.get?.(),800)}}
 const items=state?.items||[],pending=items.filter(i=>i.state==='pending'),job=state?.job;
 const checkedAge=state?.checked_at?Date.now()/1000-state.checked_at:null;
 const when=t=>t?new Date(t*1000).toLocaleString([],{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'Never';
 const tone={done:true,current:true,failed:false,pending:'warn',queued:null,skipped:null,unknown:null,'not started':null};
 const label={done:'Updated',current:'Up to date',failed:'Failed',pending:'Update available',queued:'Queued',snapshot:'Snapshot',updating:'Updating',verifying:'Checking',checking:'Checking',skipped:'Skipped',unknown:'Unknown','not started':'Not started'};
 const jobText=!job?'No checks yet':job.status==='running'?(job.kind==='check'?'Checking for updates…':'Updating one guest at a time…'):job.status==='complete'?(job.kind==='check'?`Checked ${when(job.finished_at)}`:`Update run finished ${when(job.finished_at)}`):job.status==='refused'?job.error:`${job.kind==='check'?'Check':'Update run'} failed: ${job.error||'unknown error'}`;
 return <section className="updates-section"><div className="section-heading"><div><h2>Pending <span className="count">{state?.checked_at?pending.length:state||error?'Unknown':<Skeleton w={20} h={18} className="m3-skeleton--inline"/>}</span></h2></div></div>
  {!state&&error&&<p className="source-note error">Update status is unknown until the update service answers. Checking runs again automatically every 20 seconds.</p>}
  <article className="m3-card m3-card--outlined panel updates-panel"><header className="panel-header"><div className="panel-title"><span className="icon-tile"><Icon name="refresh"/></span><div><h3>Containers and Docker apps</h3><p role="status" aria-live="polite">{state?<>{jobText}{pending.length>0&&checkedAge>3600&&!running&&' · over an hour old, check again before updating'}</>:error?'Status unknown':<Skeleton w={150} h={14} className="m3-skeleton--inline"/>}</p></div></div>
   <div className="updates-actions"><button className="m3-btn m3-btn--tonal m3-state secondary-button" disabled={running||busy||!state} onClick={()=>send('check')}>Check for updates</button><button className="m3-btn m3-btn--filled m3-state primary-button" disabled={running||busy||!pending.length||checkedAge===null||checkedAge>3600} onClick={()=>setConfirm(true)}>Update {pending.length||''}</button></div></header>
   {error&&<div className="notice warning">{error}</div>}{note&&<div className="notice warning">{note}</div>}
   <p className="source-note">Each Proxmox container gets a snapshot first. Guests update one at a time, and the run stops at the first failure. Nothing reboots on its own. Runs wait while a backup, parity check, mover or Qwen is busy.</p>
   {(()=>{const row=i=><div className={'update-row '+i.state} key={i.id}><span className="fleet-name"><span className="icon-tile"><Icon name={i.kind==='Docker'?'layers':'server'} size={17}/></span><strong>{i.name}<small>{i.kind==='LXC'?`LXC ${i.vmid}`:i.kind==='Pi'?'Orange Pi · DNS replica':'Unraid Docker'}</small></strong></span><span className="update-detail">{i.detail}{i.rollback&&<small>{i.rollback}</small>}{i.kind==='Docker'&&i.state==='unknown'&&/by hand/.test(i.detail||'')&&<a className="update-detail__link" href="http://192.168.0.4/Docker" target="_blank" rel="noreferrer">Open Unraid Docker <Icon name="external" size={14}/></a>}</span><Badge state={tone[i.state]??null} label={label[i.state]||i.state}/></div>;
    // Needs you first (pending, failed, in progress), then checking/unknown; up-to-date and skipped fold away.
    const groups=[['Needs attention',['pending','failed','queued','snapshot','updating','verifying'],true],['Checking or unknown',['checking','unknown','not started'],true],['Up to date',['current','done'],false],['Skipped',['skipped'],false]];
    const known=groups.flatMap(g=>g[1]);const other=items.filter(i=>!known.includes(i.state));
    return <div className="update-list">{groups.map(([title,states,open])=>{const list=items.filter(i=>states.includes(i.state)).concat(title==='Checking or unknown'?other:[]);if(!list.length)return null;
     return open?<div className="update-group" key={title}><h4 className="update-group__title">{title} <span className="count">{list.length}</span></h4>{list.map(row)}</div>
      :<details className="update-group" key={title}><summary className="update-group__title">{title} <span className="count">{list.length}</span><Icon name="down" size={20} className="ai-others__chevron"/></summary>{list.map(row)}</details>})}
    {!state&&!error?<div aria-busy="true" className="update-group"><h4 className="update-group__title"><Skeleton w={130} h={14}/></h4>{[0,1,2,3,4].map(i=><div className="update-row update-row--skeleton" key={i}><Skeleton h={36} radius={12}/></div>)}</div>:!items.length&&<div className="empty">Run a check to list what can be updated.</div>}</div>})()}
   {job?.log&&<footer className="host-footer"><span>Run log on Proxmox: {job.log}</span></footer>}</article>
  {confirm&&<ConfirmDialog dialog={{type:'updates',title:`Update ${pending.length} ${pending.length===1?'guest':'guests'}?`,text:`${pending.map(i=>i.name).join(', ')}. Services restart while they update.`,confirmLabel:`Update ${pending.length} ${pending.length===1?'guest':'guests'}`}} busy={busy} close={()=>setConfirm(false)} confirm={()=>send('start')}/>}</section>}
function ConfirmDialog({dialog,busy,close,confirm}){return <Dialog headline={dialog.title} icon={dialog.icon||(dialog.type==='dns'?'shield':'bell')} labelId="confirm-title" onClose={close} actions={<><Button variant="text" onClick={close} disabled={busy}>Cancel</Button>{dialog.choices?dialog.choices.map(c=><Button key={c.label} variant="text" onClick={()=>confirm(c)} disabled={busy}>{c.label}</Button>):<Button variant="text" onClick={()=>confirm()} disabled={busy}>{busy?'Working…':(dialog.confirmLabel||'Confirm')}</Button>}</>}><p style={{margin:0}}>{dialog.text}</p></Dialog>}
// One malformed telemetry field used to take down the whole app. The page body now has its own boundary, keyed by
// page: a crash replaces only that page's content, the header and navigation keep working, and switching pages resets it.
// Shaped like the page it stands in for, so nothing shifts much when the data arrives.
function PageSkeleton({view}){const card=h=><div className="m3-card skeleton-card" style={{minHeight:h}}><Skeleton w="40%" h={18}/><Skeleton w="70%" h={14}/><Skeleton h={h-90} radius={12}/></div>;
 const rows=view==='storage'?[card(140),card(140),card(140),card(140),card(260)]:view==='fleet'?[card(220),card(220),card(160)]:[card(200),card(200),card(160),card(160)];
 return <div className="skeleton-page" aria-busy="true" aria-label="Loading">{rows.map((r,i)=><React.Fragment key={i}>{r}</React.Fragment>)}</div>}
class SectionBoundary extends React.Component{constructor(p){super(p);this.state={failed:false}}static getDerivedStateFromError(){return {failed:true}}componentDidCatch(e){console.error('Section failed to render',e)}render(){return this.state.failed?<div className="notice warning" role="alert"><Icon name="clock" size={18}/><span>This page couldn’t show its data. The rest of the dashboard still works; try another page or reload.</span></div>:this.props.children}}
class ErrorBoundary extends React.Component{constructor(p){super(p);this.state={failed:false}}static getDerivedStateFromError(){return {failed:true}}render(){return this.state.failed?<main className="fatal"><h1>The dashboard couldn’t render.</h1><p>Your services are still running. Reload to reconnect.</p><button className="m3-btn m3-btn--filled m3-state primary-button" onClick={()=>location.reload()}>Reload dashboard</button></main>:this.props.children}}
ReactDOM.createRoot(document.getElementById('root')).render(<ErrorBoundary><App/></ErrorBoundary>);
