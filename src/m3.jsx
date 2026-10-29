// Material 3 React components for the dashboard. Styling lives in src/m3-components.css (class names below).
// Every component is a thin wrapper: pass extra props (aria-*, onClick, data-*) and they reach the root element.
const {useState,useEffect,useRef}=React;

// Material Symbols Rounded names for the dashboard's icon keys. Any other string is used as a Material Symbols name
// directly (add it to m3-icons.mjs so the font subset includes it).
export const SYMBOLS={home:'home',grid:'apps',server:'dns',disk:'hard_drive',network:'lan',play:'play_circle',layers:'stacks',search:'search',arrow:'arrow_forward',external:'open_in_new',down:'expand_more',up:'expand_less',refresh:'refresh',star:'star',close:'close',check:'check',shield:'shield',activity:'monitoring',sun:'light_mode',moon:'dark_mode',copy:'content_copy',bell:'notifications',folder:'folder',book:'menu_book',cloud:'cloud',terminal:'terminal',menu:'menu',clock:'schedule',wifi:'wifi',game:'sports_esports'};
export function Icon({name='grid',size=20,filled=false,className='',style,...props}){
  return <span className={`material-symbols-rounded${filled?' is-filled':''}${className?' '+className:''}`} style={{fontSize:size,...style}} aria-hidden="true" {...props}>{SYMBOLS[name]||name}</span>;
}

// Homelab logo: M3 app-icon style. Tonal tile (primary-container) with a house whose body is a three-unit server rack.
// brand-icon.svg is the same mark in the dark-theme colours for the favicon and app manifest.
export function LogoMark({size=32}){
  const bars=[252,302,352];
  return <svg className="m3-logo" width={size} height={size} viewBox="0 0 512 512" aria-hidden="true">
    <rect width="512" height="512" rx="128" fill="var(--md-sys-color-primary-container)"/>
    <path d="M256 112 L404 230 Q412 236 412 246 V372 Q412 400 384 400 H128 Q100 400 100 372 V246 Q100 236 108 230 Z" fill="var(--md-sys-color-on-primary-container)" stroke="var(--md-sys-color-on-primary-container)" strokeWidth="16" strokeLinejoin="round"/>
    {bars.map(y=><g key={y}><rect x="152" y={y} width="208" height="34" rx="12" fill="var(--md-sys-color-primary-container)"/><circle cx="330" cy={y+17} r="8" fill="var(--md-sys-color-on-primary-container)"/></g>)}</svg>;
}

const reducedMotion=()=>window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
// Android System WebView (the Capacitor app) mis-composites the View Transitions API: the old and new page snapshots
// overlap with a vertical offset and the nav bar is drawn twice. Skip view transitions there. CSS entrance animations
// still run, so pages still arrive with a stagger; only the cross-page snapshot morph is dropped.
const inWebView=()=>{try{return /\bwv\)/.test(navigator.userAgent)||!!window.Capacitor?.isNativePlatform?.()}catch{return false}};
const skipViewTransition=()=>reducedMotion()||inWebView();

// M3 fade-through between pages via the View Transitions API (Chrome, Edge, Safari 18+). The DOM update runs inside the
// transition (flushSync) so the old and new page are captured; falls back to a plain update elsewhere or with reduced motion.
// from: the element that was activated. Its snapshot morphs into the new page's title (shared element); the old title
// steps aside for the duration so only one element carries the name in each snapshot.
// Resolves once the new page is on screen (after the transition, or two frames when there is none).
// Page switches render the new page at once and slide its sections in with compositor-only animations. The title
// morphs from the old title (or the tapped element) into the new one: a copy of the old one glides and resizes onto the
// new title while it fades in. A view transition did this before, but it captured the whole page first, which dropped
// one or two frames per switch at 120 Hz, and Android WebView mis-composited it, so this also runs in the app.
export function withPageTransition(update,from,origin){
  const settle=()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
  if(reducedMotion()){update();return settle()}
  const src=from?.isConnected?from:null;let ghost=null,r0=null;
  if(src){r0=src.getBoundingClientRect();const cs=getComputedStyle(src);ghost=src.cloneNode(true);ghost.removeAttribute('id');ghost.setAttribute('aria-hidden','true');
    Object.assign(ghost.style,{position:'fixed',left:r0.left+'px',top:r0.top+'px',width:r0.width+'px',height:r0.height+'px',margin:'0',boxSizing:'border-box',
      font:cs.font,color:cs.color,letterSpacing:cs.letterSpacing,whiteSpace:cs.whiteSpace,padding:cs.padding,background:cs.backgroundColor,borderRadius:cs.borderRadius,
      overflow:'hidden',transformOrigin:'0 0',pointerEvents:'none',zIndex:'30',viewTransitionName:'none',animation:'none'})}
  ReactDOM.flushSync(update);
  const content=document.querySelector('.m3-app__content');if(!content?.animate)return settle();
  const css=getComputedStyle(document.documentElement),easing=css.getPropertyValue('--md-sys-motion-spring-expressive-slow-spatial').trim()||'cubic-bezier(0.2,0,0,1)';
  const duration=parseFloat(css.getPropertyValue('--md-sys-motion-spring-expressive-slow-spatial-duration'))||600;
  const title=content.querySelector('.m3-page-header__title'),header=title?.closest('.m3-page-header');
  // The header itself holds still so the title's own motion isn't added to the slide; its other parts slide with the page.
  const parts=[...content.children].flatMap(c=>c.classList.contains('m3-top-app-bar')?[]:c===header?[...header.querySelectorAll('.m3-page-header__subtitle'),...[...header.children].slice(1)]:[c]);
  const rise=[{opacity:0,transform:'translateY(40px)'},{opacity:1,transform:'none'}];
  // From a navigation button: every part scales up out of that button's centre (bottom bar: up from that tab, side rail:
  // out from the left), so the page visibly comes from what was pressed. Otherwise the page rises in place.
  const grow=el=>{const r=el.getBoundingClientRect();return [{opacity:0,transformOrigin:`${origin.x-r.left}px ${origin.y-r.top}px`,transform:'scale(.86)'},{opacity:1,transformOrigin:`${origin.x-r.left}px ${origin.y-r.top}px`,transform:'none'}]};
  const runs=parts.map(c=>c.animate(origin?grow(c):rise,{duration,easing}));
  // Nav switches: the old title leaves with the old page and the new one rises in (a fading copy of the old word read
  // as the wrong title). A tapped element (from) still morphs into the title.
  if(title&&!from)runs.push(title.animate(origin?grow(title):[{opacity:0,transform:'translateY(10px)'},{opacity:1,transform:'none'}],{duration:Math.min(duration,origin?duration:420),easing}));
  else if(title&&ghost&&r0){const r1=title.getBoundingClientRect();
    if(r1.width&&r1.height&&r0.width&&r0.height){document.body.appendChild(ghost);
      // One uniform scale (never separate x/y, which squashed and stretched the text), kept near 1 so a big tile doesn't balloon the title.
      const dx=r0.left-r1.left,dy=r0.top-r1.top,k=Math.max(.6,Math.min(1.4,Math.min(r0.width/r1.width,r0.height/r1.height))),sx=k,sy=k;
      runs.push(title.animate([{transformOrigin:'0 0',transform:`translate(${dx}px,${dy}px) scale(${sx},${sy})`},{transformOrigin:'0 0',transform:'none'}],{duration,easing}));
      title.animate([{opacity:0},{opacity:1}],{duration:duration*.5,delay:duration*.08,easing:'cubic-bezier(0.2,0,0,1)',fill:'backwards'});
      ghost.animate([{transform:'none'},{transform:`translate(${-dx}px,${-dy}px) scale(${1/sx},${1/sy})`}],{duration,easing,fill:'forwards'});
      const fade=ghost.animate([{opacity:1},{opacity:0}],{duration:duration*.35,easing:'cubic-bezier(0.3,0,1,1)',fill:'forwards'});
      Promise.all([fade.finished]).finally(()=>setTimeout(()=>ghost.remove(),duration*.7))}}
  return Promise.all(runs.map(a=>a.finished)).catch(()=>{});
}
// In-page shared element: the activated element morphs into the element matching toSelector after the update (a picked
// row into its detail card). The page itself holds still (html.vt-local).
export function morph(update,from,toSelector,after){
  if(!document.startViewTransition||skipViewTransition()||!from){update();after?.();return}
  document.documentElement.classList.add('vt-local');from.style.viewTransitionName='focus-morph';let target=null;
  const t=document.startViewTransition(()=>{ReactDOM.flushSync(update);from.style.viewTransitionName='';target=document.querySelector(toSelector);if(target)target.style.viewTransitionName='focus-morph'});
  t.finished.finally(()=>{if(target)target.style.viewTransitionName='';document.documentElement.classList.remove('vt-local');after?.()});
}

// Animated number for hero stats: counts up from zero when it first appears, then glides to new values on the
// Expressive default spatial spring (stiffness 380, damping 0.8) when it changes. Screen readers get the final value.
export function CountUp({value,decimals=0}){
  const n=Number(value),first=Number.isFinite(n)&&!reducedMotion()?0:n,[shown,setShown]=useState(first),from=useRef(first),raf=useRef();
  useEffect(()=>{if(!Number.isFinite(n)){setShown(n);return}const start=from.current;if(start===n||!Number.isFinite(start)||reducedMotion()){from.current=n;setShown(n);return}
    const k=380,z=0.8,w=Math.sqrt(k);let x=0,v=0,last=null;cancelAnimationFrame(raf.current);
    // Time comes only from frame timestamps (first frame sets the origin), never mixed with performance.now().
    const step=t=>{if(last===null)last=t;const dt=Math.max(0,Math.min(0.032,(t-last)/1000));last=t;const a=-k*(x-1)-2*z*w*v;v+=a*dt;x+=v*dt;
      if(Math.abs(1-x)<0.002&&Math.abs(v)<0.02){from.current=n;setShown(n);return}from.current=start+(n-start)*x;setShown(from.current);raf.current=requestAnimationFrame(step)};
    raf.current=requestAnimationFrame(step);return()=>cancelAnimationFrame(raf.current)},[n]);
  if(!Number.isFinite(n))return value??'—';
  const text=Number(shown).toLocaleString(undefined,{minimumFractionDigits:decimals,maximumFractionDigits:decimals});
  return <><span aria-hidden="true">{text}</span><span className="m3-visually-hidden">{n.toLocaleString(undefined,{maximumFractionDigits:decimals})}</span></>;
}

const cx=(...a)=>a.filter(Boolean).join(' ');

// Everything Tab can reach inside root, in order: links, enabled controls and explicit tabindex >= 0, visible and not inert.
export function tabbables(root){
  return [...(root?.querySelectorAll('a[href],button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])')||[])]
    .filter(el=>el.getClientRects().length&&!el.closest('[inert]')&&getComputedStyle(el).visibility!=='hidden');
}
// Expressive press feedback: a tap is ~80ms of :active, too short for the shape morph to read. Pointer presses on buttons
// get .is-pressing for at least 240ms (the fast spatial spring's settle point), then release springs back.
const PRESSABLE='.m3-btn,.primary-button,.secondary-button,.text-button,.m3-icon-btn,.issue-chip,.segments button,.m3-segment,.section-jump,.service-card,.net-row,.disk-row,.summary-link';
if(typeof document!=='undefined')document.addEventListener('pointerdown',ev=>{const el=ev.target.closest?.(PRESSABLE);if(!el||el.disabled)return;
  const t=performance.now();el.classList.add('is-pressing');
  const up=()=>{removeEventListener('pointerup',up,true);removeEventListener('pointercancel',up,true);setTimeout(()=>el.classList.remove('is-pressing'),Math.max(0,240-(performance.now()-t)))};
  addEventListener('pointerup',up,true);addEventListener('pointercancel',up,true)},true);
// Background scroll lock shared by every overlay (dialogs, search, modal navigation). Counted, so nested overlays release
// only when the last one closes. html keeps a stable scrollbar gutter, so locking does not shift the layout.
let scrollLocks=0;
export function lockScroll(){
  if(scrollLocks++===0)document.documentElement.classList.add('m3-scroll-locked');
  return()=>{if(--scrollLocks===0)document.documentElement.classList.remove('m3-scroll-locked')};
}

// Buttons. variant: 'filled' (main action), 'tonal' (secondary), 'outlined' (tertiary), 'text' (low emphasis), 'error'.
// Pass href to render a link styled as a button.
export function Button({variant='filled',icon,children,className,href,...props}){
  const cls=cx('m3-btn','m3-btn--'+variant,'m3-state',icon&&'has-icon',className);
  const inner=<>{icon&&<Icon name={icon} size={20}/>}{children}</>;
  return href?<a className={cls} href={href} {...props}>{inner}</a>:<button type="button" className={cls} {...props}>{inner}</button>;
}

// Icon button: always give a label (it becomes aria-label and the tooltip). size 'touch' = 48px (top app bar).
export function IconButton({icon,label,selected,tonal,touch,className,href,iconSize=24,children,...props}){
  const cls=cx('m3-icon-btn','m3-state',selected&&'is-selected',tonal&&'m3-icon-btn--tonal',touch&&'m3-icon-btn--touch',className);
  const inner=<><Icon name={icon} size={iconSize} filled={!!selected}/>{children}</>;
  return href?<a className={cls} href={href} aria-label={label} title={label} {...props}>{inner}</a>
    :<button type="button" className={cls} aria-label={label} title={label} {...props}>{inner}</button>;
}

// Segmented buttons (single select). options: [[value,label],...]. Selected segment shows a check.
export function Segmented({label,options,value,onChange,className}){
  return <div className={cx('m3-segmented',className)} role="group" aria-label={label}>
    {options.map(([v,text])=><button type="button" key={v} className="m3-segment m3-state" aria-pressed={value===v} onClick={()=>onChange(v)}>
      <Icon name="check" size={18} className="m3-segment__check"/>{text}</button>)}
  </div>;
}

// Status chip (read-only assist chip). tone: success | warning | error | info | neutral | undefined (outlined).
export function Chip({tone,icon,compact,children,className,...props}){
  return <span className={cx('m3-chip',tone&&'m3-chip--'+tone,compact&&'m3-chip--compact',icon&&'has-icon',className)} {...props}>{icon&&<Icon name={icon} size={compact?16:18}/>}{children}</span>;
}

// Badge on an icon (dot when count is empty, number otherwise). Wrap the icon: <BadgeHost count={3}><Icon .../></BadgeHost>
export function BadgeHost({count,label,children}){
  return <span className="m3-badge-host">{children}{count!==undefined&&count!==null&&count!==false&&
    <span className={cx('m3-badge',count!==''&&'m3-badge--large')} aria-label={label}>{count===''?null:count}</span>}</span>;
}

// Card. variant: 'outlined' (default), 'filled' (grouped summaries), 'elevated' (Overview headline cards).
// accent: one of teal green cyan blue indigo purple pink orange amber -> sets --accent* vars for children.
export function Card({variant='outlined',accent,as:Tag='section',className,children,interactive,...props}){
  return <Tag className={cx('m3-card','m3-card--'+variant,interactive&&'m3-state',className)} data-accent={accent} {...props}>{children}</Tag>;
}
export function CardHeader({icon,title,subtitle,trailing,headingLevel=3}){
  const H='h'+headingLevel;
  return <div className="m3-card__header">{icon&&<span className="m3-accent-icon"><Icon name={icon} size={24}/></span>}
    <div style={{flex:1,minWidth:0}}><H className="m3-card__title">{title}</H>{subtitle&&<p className="m3-card__subtitle">{subtitle}</p>}</div>{trailing}</div>;
}

// Determinate linear progress, M3 Expressive: active indicator, 4px gap, track, 4px stop dot at the end.
// wavy: the active part is a moving sine wave (amplitude 3px, wavelength 40px, height 10px) - use it while something is
// actually in progress (uploads, updates). Flat for static values such as disk usage. Values: m3-expressive.css.
export function LinearProgress({value,max=100,tone,label,wavy,color,className,...props}){
  const ref=useRef();const [w,setW]=useState(0);
  useEffect(()=>{const el=ref.current;if(!el)return;setW(el.clientWidth);const ro=new ResizeObserver(([e])=>setW(e.contentRect.width));ro.observe(el);return()=>ro.disconnect()},[]);
  const num=Number(value),ok=Number.isFinite(num)&&max>0,pct=ok?Math.max(0,Math.min(100,num/max*100)):0;
  const T=4,A=3,L=40,GAP=4,STOP=4,H=wavy?10:T,mid=H/2,cap=T/2;
  const ink=color||(tone==='warning'?'var(--md-custom-amber)':tone==='error'?'var(--md-sys-color-error)':'var(--accent,var(--md-sys-color-primary))');
  const end=Math.max(0,w*pct/100);const actEnd=Math.max(cap,end-cap);
  // Wavy: the wave is redrawn each frame between fixed ends (flat, round-capped) instead of sliding a long path under a
  // clip, which cut the ends square and made them bob.
  const waveRef=useRef(null);
  const wavePath=phase=>{let d='';const ramp=L/2;for(let x=cap;x<=actEnd;x+=2){const env=Math.min(1,(x-cap)/ramp,(actEnd-x)/ramp);d+=(d?'L':'M')+x.toFixed(1)+' '+(mid+A*Math.max(0,env)*Math.sin(2*Math.PI*(x-phase)/L)).toFixed(2)}return d};
  useEffect(()=>{if(!wavy||!(end>cap))return;const el=waveRef.current;if(!el)return;
    if(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches){el.setAttribute('d',wavePath(0));return}
    let raf;const t0=performance.now();const tick=t=>{el.setAttribute('d',wavePath(((t-t0)/1000*L)%L));raf=requestAnimationFrame(tick)};raf=requestAnimationFrame(tick);return()=>cancelAnimationFrame(raf)},[wavy,end,actEnd,mid]);
  const trackStart=pct>0?end+GAP+cap:cap,trackEnd=w-cap-(pct<100?STOP+2:0);
  return <div ref={ref} className={cx('m3-xprogress',wavy&&'m3-xprogress--wavy',className)} style={{height:H}} role="progressbar" aria-label={label}
    aria-valuemin={0} aria-valuemax={max} aria-valuenow={ok?num:undefined} {...props}>
    {w>0&&<svg width={w} height={H} aria-hidden="true">
      {trackStart<trackEnd&&<line x1={trackStart} y1={mid} x2={trackEnd} y2={mid} className="m3-xprogress__track" strokeWidth={T} strokeLinecap="round"/>}
      {pct<100&&<circle cx={w-STOP/2} cy={mid} r={STOP/2} style={{fill:ink}}/>}
      {pct>0&&(wavy?<path ref={waveRef} d={wavePath(0)} className="m3-xprogress__wave" style={{stroke:ink}} strokeWidth={T} strokeLinecap="round" strokeLinejoin="round" fill="none"/>
        :<line x1={cap} y1={mid} x2={actEnd} y2={mid} style={{stroke:ink}} strokeWidth={T} strokeLinecap="round"/>)}
    </svg>}</div>;
}
// M3 Expressive loading indicator: a shape that morphs through Material's loading sequence while it turns (m3-shapes.mjs).
// contained = on a circular primary-container. Use it for waits under a few seconds where no progress is known.
// Placeholder shaped like the content it stands in for. Shimmer is off under Reduce motion; hidden from assistive tech
// (the region that holds it carries aria-busy).
export function Skeleton({w='100%',h=16,radius,className=''}){return <span className={'m3-skeleton '+className} aria-hidden="true" style={{width:w,height:h,borderRadius:radius}}/>}
export function LoadingIndicator({label='Loading',small,contained,className}){
  return <span className={cx('m3-loading',small&&'m3-loading--small',contained&&'m3-loading--contained',className)} role="progressbar" aria-label={label}><span className="m3-loading__shape"/></span>;
}
export function IndeterminateProgress({label='Loading'}){
  return <div className="m3-linear-progress m3-linear-progress--indeterminate" role="progressbar" aria-label={label}><div className="m3-linear-progress__bar"/></div>;
}
export function Spinner({small,label='Loading'}){
  return <svg className={cx('m3-circular-progress',small&&'m3-circular-progress--small')} viewBox="0 0 48 48" role="progressbar" aria-label={label}><circle cx="24" cy="24" r="20"/></svg>;
}

// Outlined text field. Extra props go to the <input>. onClear shows a clear button when there is a value.
export function TextField({label,icon,value,onChange,onClear,clearLabel='Clear search',dense,className,inputRef,...props}){
  return <div className={cx('m3-field',dense&&'m3-field--dense',className)}>
    {icon&&<Icon name={icon} size={24}/>}
    <input className="m3-field__input" ref={inputRef} value={value} onChange={onChange} aria-label={props['aria-label']||label} {...props}/>
    {label&&<span className="m3-field__label">{label}</span>}
    {onClear&&value&&<IconButton icon="close" label={clearLabel} className="m3-field__clear" onClick={onClear}/>}
  </div>;
}

// Outlined select (native <select>, so keyboard and screen readers work unchanged). options: [[value,label],...]
// M3 exposed dropdown menu: a listbox popover instead of the native <select> (which opens Android's grey system picker).
// options: [{value,label,disabled?,group?}]. onChange(value). Keyboard: arrows, Home/End, Enter/Space, Escape, typeahead.
// While open it counts as an overlay (m3:dialog-open/close), so Back and the predictive-back gesture close it.
export function MenuSelect({value,onChange,options,disabled,className='','aria-label':ariaLabel}){
  const [open,setOpen]=useState(false),[active,setActive]=useState(-1),[pos,setPos]=useState(null);
  const trigger=useRef(),list=useRef(),typed=useRef({text:'',t:0}),id=useRef('m3-menu-'+Math.random().toString(36).slice(2,8)).current;
  const items=options.map((o,i)=>({...o,i}));const enabled=items.filter(o=>!o.disabled);
  const current=items.find(o=>o.value===value);
  const place=()=>{const r=trigger.current.getBoundingClientRect(),vh=window.innerHeight,gap=4,need=Math.min(items.length*48+24,360);
    const below=vh-r.bottom-gap-8,above=r.top-gap-8,up=below<need&&above>below;
    setPos({left:Math.max(8,Math.min(r.left,window.innerWidth-Math.max(r.width,200)-8)),width:Math.max(r.width,200),
      top:up?undefined:r.bottom+gap,bottom:up?vh-r.top+gap:undefined,maxHeight:Math.max(160,up?above:below),up})};
  const openMenu=()=>{if(disabled)return;place();setActive(Math.max(0,items.findIndex(o=>o.value===value&&!o.disabled)));setOpen(true)};
  const close=(focus=true)=>{setOpen(false);if(focus)trigger.current?.focus()};
  const choose=o=>{if(!o||o.disabled)return;onChange?.(o.value);close()};
  useEffect(()=>{if(!open)return;window.dispatchEvent(new CustomEvent('m3:dialog-open'));requestAnimationFrame(()=>list.current?.focus());
    const outside=ev=>{if(!list.current?.contains(ev.target)&&!trigger.current?.contains(ev.target))close(false)};
    const shut=()=>close(false);const resize=()=>close(false);
    document.addEventListener('pointerdown',outside,true);window.addEventListener('m3:close-all-overlays',shut);window.addEventListener('resize',resize);
    return()=>{document.removeEventListener('pointerdown',outside,true);window.removeEventListener('m3:close-all-overlays',shut);window.removeEventListener('resize',resize);window.dispatchEvent(new CustomEvent('m3:dialog-close'))}},[open]);
  useEffect(()=>{if(open&&active>=0)document.getElementById(`${id}-${active}`)?.scrollIntoView({block:'nearest'})},[open,active]);
  const step=d=>{const idx=enabled.findIndex(o=>o.i===active);const n=enabled[(idx+d+enabled.length)%enabled.length];if(n)setActive(n.i)};
  const listKeys=ev=>{const k=ev.key;
    if(k==='ArrowDown'){ev.preventDefault();step(1)}else if(k==='ArrowUp'){ev.preventDefault();step(-1)}
    else if(k==='Home'){ev.preventDefault();enabled[0]&&setActive(enabled[0].i)}else if(k==='End'){ev.preventDefault();enabled.at(-1)&&setActive(enabled.at(-1).i)}
    else if(k==='Enter'||k===' '){ev.preventDefault();choose(items[active])}
    else if(k==='Escape'){ev.preventDefault();ev.stopPropagation();close()}else if(k==='Tab'){close(false)}
    else if(k.length===1){const now=performance.now();typed.current={text:(now-typed.current.t<600?typed.current.text:'')+k.toLowerCase(),t:now};
      const hit=enabled.find(o=>String(o.label).toLowerCase().startsWith(typed.current.text));if(hit)setActive(hit.i)}};
  const triggerKeys=ev=>{if(open){listKeys(ev);return}if(['ArrowDown','ArrowUp','Enter',' '].includes(ev.key)){ev.preventDefault();openMenu()}};
  let lastGroup;
  return <span className={cx('m3-select',className)}>
    <button type="button" ref={trigger} className={cx('m3-menu-select__trigger',open&&'is-open')} role="combobox" aria-haspopup="listbox" aria-expanded={open} aria-controls={id} aria-label={ariaLabel} disabled={disabled}
      onClick={()=>open?close():openMenu()} onKeyDown={triggerKeys}><span className="m3-menu-select__value">{current?.label??'—'}</span></button>
    <Icon name="arrow_drop_down" size={24} className={cx('m3-select__icon',open&&'is-open')}/>
    {open&&pos&&ReactDOM.createPortal(<ul ref={list} id={id} role="listbox" tabIndex={-1} aria-label={ariaLabel} aria-activedescendant={active>=0?`${id}-${active}`:undefined}
      className={cx('m3-menu-select__menu',pos.up&&'is-up')} style={{left:pos.left,minWidth:pos.width,top:pos.top,bottom:pos.bottom,maxHeight:pos.maxHeight}} onKeyDown={listKeys}>
      {items.map(o=>{const head=o.group&&o.group!==lastGroup?(lastGroup=o.group,<li key={'g-'+o.group} role="presentation" className="m3-menu-select__group">{o.group}</li>):null;if(!o.group)lastGroup=undefined;
        return <React.Fragment key={o.value}>{head}<li id={`${id}-${o.i}`} role="option" aria-selected={o.value===value} aria-disabled={o.disabled||undefined}
          className={cx('m3-menu-select__option',o.i===active&&'is-active',o.value===value&&'is-selected')} onPointerMove={()=>!o.disabled&&setActive(o.i)} onClick={()=>choose(o)}>
          <span>{o.label}</span>{o.value===value&&<Icon name="check" size={20}/>}</li></React.Fragment>})}
    </ul>,document.body)}
  </span>;
}

export function SelectField({label,value,onChange,options,dense,className,children,...props}){
  return <div className={cx('m3-field','m3-field--select',dense&&'m3-field--dense',className)}>
    <select className="m3-field__input" value={value} onChange={onChange} aria-label={props['aria-label']||label} {...props}>
      {options?options.map(([v,text])=><option key={v} value={v}>{text}</option>):children}</select>
    {label&&<span className="m3-field__label">{label}</span>}
  </div>;
}

// Basic dialog with scrim, rendered at the top of <body> (one overlay owner). Escape and the scrim call onClose; while it is
// open the rest of the page is inert. Focus moves in (initialFocusRef or the first button) and returns on close.
export function Dialog({headline,icon,children,actions,onClose,labelId='m3-dialog-title',initialFocusRef}){
  const box=useRef();
  // Callers pass inline onClose arrows and App re-renders every second (clock), so the setup below must run once per
  // open, not on every new onClose: re-running it stole focus back to the first button about once a second.
  const closeRef=useRef(onClose);closeRef.current=onClose;
  useEffect(()=>{const previous=document.activeElement;(initialFocusRef?.current||box.current?.querySelector('button'))?.focus();
    const others=[...document.body.children].filter(el=>el!==box.current?.parentElement&&!el.contains(box.current));const was=others.map(el=>el.inert);others.forEach(el=>{el.inert=true});const unlock=lockScroll();
    window.dispatchEvent(new CustomEvent('m3:dialog-open'));
    const handleClose = () => closeRef.current?.();
    window.addEventListener('m3:close-all-overlays', handleClose);
    // Callers unmount the dialog the moment it closes; a short-lived inert copy plays the exit so closing matches opening.
    const layer=box.current?.parentElement;
    return()=>{if(layer&&!reducedMotion()){const g=layer.cloneNode(true);g.inert=true;g.style.pointerEvents='none';g.setAttribute('aria-hidden','true');document.body.appendChild(g);
        const d=g.querySelector('.m3-dialog'),sc=g.querySelector('.m3-scrim'),ease='cubic-bezier(0.3,0,0.8,0.15)';
        [d,sc].forEach(el=>{if(el)el.style.animation='none'});
        d?.animate([{opacity:1,transform:'translate(-50%,-50%)'},{opacity:0,transform:'translate(-50%,-48%) scale(.96)'}],{duration:150,easing:ease,fill:'forwards'});
        const out=sc?.animate([{opacity:1},{opacity:0}],{duration:150,easing:ease,fill:'forwards'});
        (out?out.finished:Promise.resolve()).catch(()=>{}).finally(()=>g.remove())}
      unlock();others.forEach((el,i)=>{el.inert=was[i]});if(previous?.isConnected)previous.focus();window.removeEventListener('m3:close-all-overlays', handleClose);window.dispatchEvent(new CustomEvent('m3:dialog-close'))}},[]);
  const keys=ev=>{if(ev.key==='Escape'){ev.stopPropagation();onClose?.();return}if(ev.key!=='Tab')return;const n=tabbables(box.current);
    if(!n.length)return;if(ev.shiftKey&&document.activeElement===n[0]){ev.preventDefault();n.at(-1).focus()}
    else if(!ev.shiftKey&&document.activeElement===n.at(-1)){ev.preventDefault();n[0].focus()}};
  return ReactDOM.createPortal(<div className="m3-dialog-layer"><div className="m3-scrim" onClick={onClose}/>
    <div className="m3-dialog" role="dialog" aria-modal="true" aria-labelledby={labelId} ref={box} onKeyDown={keys}>
      {icon&&<Icon name={icon} size={24} className="m3-dialog__icon"/>}
      <h2 className="m3-dialog__headline" id={labelId} style={icon?{textAlign:'center'}:undefined}>{headline}</h2>
      <div className="m3-dialog__body">{children}</div>
      {actions&&<div className="m3-dialog__actions">{actions}</div>}
    </div></div>,document.body);
}

// Snackbar. Render it inside an always-mounted role=status region (App does) so screen readers announce it.
export function Snackbar({children,action,onAction,onClose}){
  return <div className="m3-snackbar"><span className="m3-snackbar__text">{children}</span>
    {action&&<Button variant="text" onClick={onAction}>{action}</Button>}
    {onClose&&<IconButton icon="close" label="Dismiss" onClick={onClose}/>}</div>;
}

// List item. Pass href for a link row, onClick for a button row, neither for static.
export function ListItem({icon,headline,supporting,trailing,href,onClick,className,children,...props}){
  const lines=supporting?'m3-list-item--two-line':'';
  const inner=<>{icon&&<Icon name={icon} size={24}/>}<span className="m3-list-item__body"><span className="m3-list-item__headline">{headline}</span>
    {supporting&&<span className="m3-list-item__supporting">{supporting}</span>}{children}</span>{trailing&&<span className="m3-list-item__trailing">{trailing}</span>}</>;
  const cls=cx('m3-list-item',lines,(href||onClick)&&'m3-state',className);
  if(href)return <li><a className={cls} href={href} {...props}>{inner}</a></li>;
  if(onClick)return <li><button type="button" className={cls} onClick={onClick} style={{width:'100%',border:0,background:'transparent',textAlign:'left'}} {...props}>{inner}</button></li>;
  return <li className={cls} {...props}>{inner}</li>;
}
export const Divider=({inset})=><hr className={cx('m3-divider',inset&&'m3-divider--inset')}/>;

// Page and section headers. PageHeader renders the page <h1>; Section renders an <h2>.
// large: the expanded part of an M3 large top app bar (headline-large title); the small title in the top app bar
// takes over when the page scrolls.
export function PageHeader({title,subtitle,eyebrow,trailing,large}){
  return <div className={cx('m3-page-header',large&&'m3-page-header--large')}><div>{eyebrow&&<p className="m3-label-large m3-on-surface-variant" style={{margin:'0 0 4px'}}>{eyebrow}</p>}
    <h1 className="m3-page-header__title" tabIndex={-1}>{title}</h1>{subtitle&&<p className="m3-page-header__subtitle">{subtitle}</p>}</div>{trailing}</div>;
}
export function Section({title,actions,children,className,id,...props}){
  return <section className={cx('m3-section',className)} id={id} {...props}>
    {(title||actions)&&<div className="m3-section__header">{title&&<h2 className="m3-section__title">{title}</h2>}{actions}</div>}{children}</section>;
}

// Window size class from the M3 breakpoints: compact <600, medium <840, expanded <1200, large >=1200.
export function useWindowClass(){
  const get=()=>{const w=window.innerWidth;return w<600?'compact':w<840?'medium':w<1200?'expanded':'large'};
  const [c,setC]=useState(get);
  useEffect(()=>{const on=()=>setC(get());window.addEventListener('resize',on);return()=>window.removeEventListener('resize',on)},[]);
  return c;
}
