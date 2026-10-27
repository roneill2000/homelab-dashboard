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

// M3 fade-through between pages via the View Transitions API (Chrome, Edge, Safari 18+). The DOM update runs inside the
// transition (flushSync) so the old and new page are captured; falls back to a plain update elsewhere or with reduced motion.
// from: the element that was activated. Its snapshot morphs into the new page's title (shared element); the old title
// steps aside for the duration so only one element carries the name in each snapshot.
// Resolves once the new page is on screen (after the transition, or two frames when there is none).
export function withPageTransition(update,from){
  if(!document.startViewTransition||reducedMotion()){update();return new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))}
  const title=document.querySelector('main h1');
  if(from&&title&&from!==title){from.style.viewTransitionName='page-title';title.style.viewTransitionName='none'}
  return document.startViewTransition(()=>{ReactDOM.flushSync(update);if(from)from.style.viewTransitionName='';if(title)title.style.viewTransitionName=''}).finished.catch(()=>{});
}
// In-page shared element: the activated element morphs into the element matching toSelector after the update (a picked
// row into its detail card). The page itself holds still (html.vt-local).
export function morph(update,from,toSelector,after){
  if(!document.startViewTransition||reducedMotion()||!from){update();after?.();return}
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
  let wave='';if(wavy&&end>cap){for(let x=-L;x<=actEnd+L;x+=2)wave+=(wave?'L':'M')+x.toFixed(1)+' '+(mid+A*Math.sin(2*Math.PI*x/L)).toFixed(2)}
  const trackStart=pct>0?end+GAP+cap:cap,trackEnd=w-cap-(pct<100?STOP+2:0);
  const id=useRef('lp'+Math.random().toString(36).slice(2)).current;
  return <div ref={ref} className={cx('m3-xprogress',wavy&&'m3-xprogress--wavy',className)} style={{height:H}} role="progressbar" aria-label={label}
    aria-valuemin={0} aria-valuemax={max} aria-valuenow={ok?num:undefined} {...props}>
    {w>0&&<svg width={w} height={H} aria-hidden="true">
      {trackStart<trackEnd&&<line x1={trackStart} y1={mid} x2={trackEnd} y2={mid} className="m3-xprogress__track" strokeWidth={T} strokeLinecap="round"/>}
      {pct<100&&<circle cx={w-STOP/2} cy={mid} r={STOP/2} style={{fill:ink}}/>}
      {pct>0&&(wavy?<><clipPath id={id}><rect x="0" y="0" width={end} height={H}/></clipPath>
        <path d={wave} clipPath={`url(#${id})`} className="m3-xprogress__wave" style={{stroke:ink}} strokeWidth={T} strokeLinecap="round" fill="none"/></>
        :<line x1={cap} y1={mid} x2={actEnd} y2={mid} style={{stroke:ink}} strokeWidth={T} strokeLinecap="round"/>)}
    </svg>}</div>;
}
// M3 Expressive loading indicator: a shape that morphs through Material's loading sequence while it turns (m3-shapes.mjs).
// contained = on a circular primary-container. Use it for waits under a few seconds where no progress is known.
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
  useEffect(()=>{const previous=document.activeElement;(initialFocusRef?.current||box.current?.querySelector('button'))?.focus();
    const others=[...document.body.children].filter(el=>el!==box.current?.parentElement&&!el.contains(box.current));const was=others.map(el=>el.inert);others.forEach(el=>{el.inert=true});const unlock=lockScroll();
    return()=>{unlock();others.forEach((el,i)=>{el.inert=was[i]});if(previous?.isConnected)previous.focus()}},[]);
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
