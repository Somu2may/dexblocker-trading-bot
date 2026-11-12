const tg = window.Telegram?.WebApp; tg?.ready(); tg?.expand();
try{ tg?.setHeaderColor('#05070d'); tg?.setBackgroundColor('#05070d'); }catch(e){}
const hap = (t='light') => tg?.HapticFeedback?.impactOccurred(t);
const api = async (p, body) => {
  const r = await fetch(p, {method: body?'POST':'GET', headers:{'Content-Type':'application/json','X-Telegram-Init-Data': tg?.initData||''}, body: body?JSON.stringify(body):undefined});
  if(!r.ok){const e=await r.json().catch(()=>({})); const m=e.error||e.detail; throw Object.assign(new Error(typeof m==='string'?m:('HTTP '+r.status)), {status:r.status});}
  const j=await r.json(); if('demo' in j) document.getElementById('demo').hidden=!j.demo; return j;
};
const $ = s => document.querySelector(s), $$ = s => document.querySelectorAll(s), V = $('#view');
const esc = s => String(s??'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const px = n => n==null?'—':(n>=1000?n.toLocaleString('en-US',{maximumFractionDigits:1}):n>=100?n.toFixed(2):n>=1?n.toFixed(3):n.toPrecision(4));
const usd = (n,sign) => n==null?'—':((sign&&n>=0?'+':'')+(n<0?'-':'')+'$'+Math.abs(n).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}));
const pc = n => n==null?'—':`${n>=0?'+':''}${n.toFixed(2)}%`, cls = n => n>=0?'g':'r';
const toast = m => { const t=$('#toast'); t.textContent=m; t.classList.add('show'); setTimeout(()=>t.classList.remove('show'),2200); };
const ago = s => s==null?'—':s<60?`${s}s ago`:`${Math.floor(s/60)}m ago`;
const hm = iso => new Date(iso).toLocaleString('en-IN',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Asia/Kolkata'})+' IST';
const sideB = s => `<span class="side ${s==='LONG'||s>0?'L':'S'}">${s==='LONG'||s>0?'LONG':'SHORT'}</span>`;
const key = () => (crypto.randomUUID?crypto.randomUUID():Math.random().toString(36).slice(2)+Date.now()).replace(/[^A-Za-z0-9_-]/g,'').slice(0,40);
let tab = new URLSearchParams(location.search).get('tab') || 'dash', SYMS=['BTC','ETH','SOL'], timer=null;

function spark(curve){ if(!curve||curve.length<2) return '<div class="sub">Equity curve appears after the first hour.</div>';
  const W=340,H=120,ys=curve.map(p=>p[1]),mn=Math.min(...ys),mx=Math.max(...ys),r=(mx-mn)||1,t0=curve[0][0],t1=curve[curve.length-1][0]||t0+1;
  const pts=curve.map(([t,y])=>[((t-t0)/(t1-t0||1))*W, H-8-((y-mn)/r)*(H-16)]);
  const d=pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' '); const up=ys[ys.length-1]>=ys[0]; const c=up?'#00ff9d':'#ff3b5c';
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><defs><linearGradient id="eg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${c}" stop-opacity=".35"/><stop offset="1" stop-color="${c}" stop-opacity="0"/></linearGradient></defs>
   <path d="${d} L ${W} ${H} L 0 ${H} Z" fill="url(#eg)"/><path d="${d}" fill="none" stroke="${c}" stroke-width="2" style="filter:drop-shadow(0 0 4px ${c})"/></svg>
   <div class="row sub"><span>${usd(mn)}</span><span>${usd(mx)}</span></div>`; }
function dbars(d){ if(!d||!d.length) return '<div class="sub">No closed trades yet.</div>'; const mx=Math.max(...d.map(x=>Math.abs(x[1])),1);
  return `<div class="bars mid">${d.map(([k,v])=>`<div class="${v>=0?'p':'n'}" title="${k}: ${usd(v,1)}"><div style="height:50%;display:flex;flex-direction:column;justify-content:flex-end">${v>=0?`<b style="height:${Math.max(3,v/mx*100)}%"></b>`:''}</div><div style="height:50%">${v<0?`<b style="height:${Math.max(3,-v/mx*100)}%"></b>`:''}</div><span>${k.slice(3)}</span></div>`).join('')}</div><div style="height:14px"></div>`; }

let ST={name:'donchian_15m',tf:'15m',trend_tf:'1h',channel:20,long_only:true}, MEME=false;
function noteStrat(st){ if(!st) return; stratBadge(st); MEME=st.name==='gmgn_meme';
  const b=document.querySelector('.brand .m'); if(b) b.textContent=MEME?'Memes':'Futures';
  const feed=$('#feed'); if(feed&&MEME) feed.textContent='SOL';
  const sig=document.querySelector('.tabs button[data-tab=sig]'); if(sig) sig.innerHTML=MEME?'<i>◎</i>Scanner':'<i>◎</i>Signals';
  document.title=MEME?'DexBlocker Memes':'DexBlocker Futures'; }
function memeDash(s){ const st=s.strategy||{};
  V.innerHTML=`<div class="card exp"><b>PAPER · Solana memecoins</b><div class="sub">${esc(st.summary||'Missing data rejects. A price quote does not prove a token can be sold.')}</div></div>
  <div class="card hero"><div class="lbl">Paper equity · memecoins</div><div class="big mono">${usd(s.equity)}</div>
   <div class="mono ${cls(s.today_pnl)} glow">${usd(s.today_pnl,1)} (${pc(s.today_pnl_pct)}) today</div>
   <div class="sub" style="margin-top:4px">All-time ${usd(s.total_pnl,1)} (${pc(s.total_pnl_pct)}) from ${usd(s.start_balance)}</div>${spark(s.equity_curve)}</div>
  <div class="acts"><button class="btn ghost" data-go="sig">Scanner</button><button class="btn ghost" data-go="pos">Open · ${s.open_positions}/${s.max_open}</button></div>
  <div class="card"><div class="row"><div><div class="tk">Auto entries ${s.running?'<span class="g">ON</span>':'<span class="r">PAUSED</span>'}</div>
   <div class="sub">${s.online===false?'Bot process offline':'Size '+(st.risk_pct||'')+'% · stop -'+(st.stop_loss_pct||'')+'%'}</div></div>
   <button id="tgl" class="sw ${s.running?'on':''}" ${s.demo||s.live_control?'':'disabled'}></button></div></div>
  <div class="grid">
   <div class="card"><div class="lbl">Win rate</div><div class="val">${(s.all.win_rate||0).toFixed(0)}%</div><div class="sub">${s.all.wins}W / ${s.all.losses}L</div></div>
   <div class="card"><div class="lbl">Profit factor</div><div class="val">${s.all.profit_factor==null?'—':s.all.profit_factor.toFixed(2)}</div><div class="sub">fees ${usd(s.all.fees)}</div></div>
   <div class="card"><div class="lbl">Today</div><div class="val ${cls(s.today.pnl)}">${usd(s.today.pnl,1)}</div></div>
   <div class="card"><div class="lbl">Daily loss cap</div><div class="val y">-${st.daily_loss_cap_pct||s.risk.daily_loss_cap_pct}%</div><div class="sub">streak ${s.breaker.consec_losses}</div></div></div>
  <h2>Data sources</h2><div class="card"><div class="sub">${esc(JSON.stringify(s.sources||{}))}</div>
   <div class="sub" style="margin-top:6px">${esc(s.breaker.reason||'no breaker')}</div></div>
  <div class="note">PAPER only. No wallet. GMGN is read-only when a key is set. DexScreener prices do not prove a sell. Fills add a fee and slippage from size versus liquidity.</div>`;
  bindCommon();
  const ctl=s.demo||s.live_control; if(ctl&&$('#tgl')) $('#tgl').onclick=async()=>{ hap('medium'); try{ const r=await api('/api/toggle',{running:!s.running}); toast(r.running?'Resuming…':'Pausing…'); }catch(e){toast(e.message)} setTimeout(()=>R.dash().catch(()=>{}),3000); };
}
function memePos(r){ const t=r.open||[];
  V.innerHTML=`<h2>Open memecoins · ${t.length}</h2>`+(t.length?'':`<div class="card m">No open paper positions.</div>`)+
  t.map(x=>`<div class="card"><div class="row"><div><div class="tk">${esc(x.symbol)} <span class="tag">PAPER</span></div>
    <div class="sub">${esc(x.mint||'')} · ${x.held_min}m · liq ${usd(x.liquidity)}</div></div>
    <div style="text-align:right"><span class="pill ${cls(x.upnl)}">${usd(x.upnl,1)}</span><div class="sub">${pc(x.upnl_pct)}</div></div></div>
    <div class="kv4"><div><span>Entry</span>${px(x.entry)}</div><div><span>Now</span>${px(x.mark)}</div><div><span>Stop</span><b class="r">${px(x.sl)}</b></div>
    <div><span>Mcap</span>${usd(x.mcap)}</div></div>
    <div class="sub" style="margin-top:6px">TP stages hit ${(x.tp_done||[]).length} · fees ${usd(x.fees)}</div></div>`).join('');
}
function memeHist(h){ const t=h.closed||[], rej=h.rejected||[]; const tot=t.reduce((a,b)=>a+b.pnl,0);
  V.innerHTML=`<div class="card hero"><div class="lbl">Realized P&L</div><div class="val ${cls(tot)}">${usd(tot,1)}</div></div><h2>Closed</h2>`+
  (t.map(x=>`<div class="card"><div class="row"><div><div class="tk">${esc(x.symbol)}</div><div class="sub">${px(x.entry)} → ${px(x.exit)} · ${esc(x.reason)}</div><div class="sub">${hm(x.closed_iso)}</div></div>
    <div class="mono ${cls(x.pnl)}"><b>${usd(x.pnl,1)}</b></div></div></div>`).join('')||'<div class="card m">No closed trades yet.</div>')+
  `<h2>Rejected</h2>`+(rej.map(x=>`<div class="card"><div class="tk">${esc(x.symbol)} <span class="chip rj">REJECT</span></div><div class="sub" style="margin-top:4px">${esc(x.reason)}</div><div class="sub">${hm(x.time)}</div></div>`).join('')||'<div class="card m">No rejects logged yet.</div>');
}
function memeScan(s){ const rows=s.scanner||s.signals||[];
  V.innerHTML=`<div class="card exp"><b>Scanner</b><div class="sub">Fail-closed. Rejected tokens stay on this list with the reason. Honeypot passes only when a security source explicitly says no.</div></div>`+
  rows.map(x=>{ const rej=x.decision!=='enter';
    return `<div class="card"><div class="row"><div><div class="tk">${esc(x.symbol)} ${rej?'':'<span class="chip ps">ENTER</span>'}</div>
      <div class="sub">${esc(x.mint||'')} · ${ago(x.checked?Math.round(Date.now()/1000-x.checked):x.checked_ago_s)}</div></div>
      <span class="chip ${rej?'rj':'ps'}">${rej?'REJECT':'PASS'}</span></div>
      <div class="sub" style="margin-top:6px;color:var(--tx)">${esc((x.reasons||[]).join(' · ')||x.reason||'')}</div>
      <div class="sgl"><span>px ${px(x.price)}</span><span>mcap ${usd(x.mcap)}</span><span>liq ${usd(x.liquidity)}</span><span>1h ${x.chg_1h==null?'—':x.chg_1h+'%'}</span><span>smart ${x.smart_n??'—'}</span><span>score ${x.score??'—'}</span></div>
      ${x.dev_analyzed?`<div class="sub">dev alive ${x.dev_alive}/${x.dev_analyzed} · rug ${x.dev_rug_rate==null?'—':Math.round(x.dev_rug_rate*100)+'%'}</div>`:''}</div>`}).join('')+
  (rows.length?'':`<div class="card m">No scan yet.</div>`);
}
const chl=(n,tf)=>tf==='1h'?n+'h':n+'×'+tf;
function hfWarn(st){ if(!st||st.name!=='hf_maker') return ''; const e=st.expect||{}, b=e.bulk_60d||{};
  return `<div class="card exp"><b>⚠️ EXPERIMENT · ${esc(st.label)}</b><div class="sub">${esc(st.summary||'')}. Target ≥ ${st.target_trades_per_day||50} trades/day across BTC/ETH/SOL. PAPER only.</div>
   <div class="sub y" style="margin-top:6px">Backtest LOST money: Binance 1m ${e.days||245} d → ${e.trades_per_day} trades/day, win ${e.win_pct}%, PF ${e.pf}, net ${e.net_pct}%, max DD ${e.max_dd_pct}%.
   BULK own 60 d → ${b.trades_per_day} trades/day, PF ${b.pf}, net ${b.net_pct}%. Even with zero fees there was no edge. 3% daily loss cap stops it for the day.</div></div>`; }
function stratBadge(st){ if(st) ST={...ST,...st}; const b=$('#strat'); if(!b||!st) return; b.hidden=false; b.textContent=st.experimental?'EXP':(st.name||'').toUpperCase(); b.title=st.label||''; }
const R = {
 async dash(){ const s=await api('/api/status'); noteStrat(s.strategy); if(MEME) return memeDash(s);
  SYMS=(s.symbols||SYMS).filter(x=>!(s.suspended||{})[x]); $('#feed').textContent='BULK'+(s.proxy?' · PROXY':''); stratBadge(s.strategy); const ctl=s.demo||s.live_control;
  V.innerHTML=hfWarn(s.strategy)+`<div class="card hero"><div class="lbl">Paper equity · BULK perps</div>
   <div class="big mono">${usd(s.equity)}</div>
   <div class="mono ${cls(s.today_pnl)} glow">${usd(s.today_pnl,1)} (${pc(s.today_pnl_pct)}) today</div>
   <div class="sub" style="margin-top:4px">All-time ${usd(s.total_pnl,1)} (${pc(s.total_pnl_pct)}) from ${usd(s.start_balance)} · margin used ${usd(s.used_margin)}</div>
   ${spark(s.equity_curve)}</div>
  <div class="acts"><button class="btn buy" data-trade="1">▲ Long</button><button class="btn short" data-trade="-1">▼ Short</button><button class="btn ghost" data-go="pos">Open · ${s.open_positions}/${s.max_open}</button></div>
  <div class="card"><div class="row"><div><div class="tk">Auto-trading ${s.running?'<span class="g">ON</span>':'<span class="r">PAUSED</span>'}</div>
   <div class="sub">${s.online===false?'Bot process offline':s.running?`${esc((s.strategy||{}).label||'')} · ${(s.enabled_symbols||[]).join(' · ')} · ${(s.strategy||{}).risk_pct??s.risk.risk_pct}% risk · max ${s.risk.leverage}x`:'No new entries · open positions still managed'}</div></div>
   <button id="tgl" class="sw ${s.running?'on':''}" ${ctl?'':'disabled style="opacity:.4"'}></button></div></div>
  <div class="grid">
   <div class="card"><div class="lbl">Win rate</div><div class="val ${s.all.win_rate>=50?'g':'y'}">${s.all.win_rate.toFixed(0)}%</div><div class="sub">${s.all.wins}W / ${s.all.losses}L · ${s.all.trades} trades</div></div>
   <div class="card"><div class="lbl">Profit factor</div><div class="val ${s.all.profit_factor>=1?'g':'r'}">${s.all.profit_factor==null?'—':s.all.profit_factor.toFixed(2)}</div><div class="sub">avg ${s.all.avg_r>=0?'+':''}${s.all.avg_r}R · fees ${usd(s.all.fees)}</div></div>
   <div class="card"><div class="lbl">Today</div><div class="val ${cls(s.today.pnl)}">${usd(s.today.pnl,1)}</div><div class="sub">${s.today.wins}W / ${s.today.losses}L realized</div></div>
   <div class="card"><div class="lbl">Daily loss cap</div><div class="val y">-${s.risk.daily_loss_cap_pct}%</div><div class="sub">streak breaker ${s.breaker.consec_losses}/${s.risk.max_consecutive_losses}</div></div></div>
  <h2>Daily P&L (USD)</h2><div class="card">${dbars(s.daily_pnl)}</div>
  <h2>Circuit breaker & data</h2><div class="card"><div class="row"><div><span class="dot ${s.breaker.tripped?'bad':''}"></span><b>${s.breaker.tripped?'TRIPPED':'All clear'}</b>
   <div class="sub">${esc(s.breaker.reason||(s.proxy?'proxy prices: '+s.proxy:'BULK data fresh · within daily cap'))}</div>${Object.keys(s.suspended||{}).length?`<div class="sub y">Skipped (BULK status): ${Object.entries(s.suspended).map(([k,v])=>k+'-USD '+v).join(', ')}</div>`:''}</div>
   <div class="mono m" style="text-align:right;font-size:11px">${esc(s.source||'no source')} ${s.ws?'· ws':'· rest'}${s.source&&s.source!=='bulk'?' (proxy)':''}<br><span class="g">${s.heartbeat_age_s!=null?'beat '+s.heartbeat_age_s+'s ago':''}</span></div></div>
   <div class="tick" style="margin-top:10px">${Object.entries(s.prices||{}).map(([k,v])=>`<div><span>${k}</span><b>${px(v.last)}</b></div>`).join('')}</div></div>
  <div class="note">PAPER mode is locked. Venue BULK (bulk.trade); no wallet, keys or agent wallet connected. Fills are simulated: taker 3.5 bps + slippage from the live BULK order book, TP1 as maker (0 bps), hourly funding.</div>`;
  bindCommon();
  if(ctl) $('#tgl').onclick=async()=>{ hap('medium'); try{ const r=await api('/api/toggle',{running:!s.running}); toast(r.running?'Resuming…':'Pausing…'); }catch(e){toast(e.message)} setTimeout(()=>R.dash().catch(()=>{}),3000); };
 },
 async pos(){ const r=await api('/api/positions'); if(r.meme||MEME) return memePos(r); const t=r.open;
  V.innerHTML=`<div class="acts"><button class="btn buy" data-trade="1">▲ Long</button><button class="btn short" data-trade="-1">▼ Short</button><button class="btn danger" data-closeall ${t.length?'':'disabled'}>Close all</button></div>
   <h2>Open positions · ${t.length}</h2>`+(t.length?'':`<div class="card m">No open paper positions. The bot enters on a ${ST.tf} close above the ${chl(ST.channel||20,ST.tf)} high while the ${ST.trend_tf} trend is up${ST.long_only?'':' (and shorts the mirror)'}, or tap Long / Short.</div>`)+
   t.map((x,i)=>`<div class="card"><div class="row"><div><div class="tk">${x.symbol}-USD${sideB(x.side)}<span class="lev">${x.lev}x</span>${x.manual?' <span class="tag">MANUAL</span>':''}</div>
    <div class="sub">${x.setup||''} · ${x.held_min>=120?(x.held_min/60).toFixed(1)+'h':x.held_min+'m'} · ${x.left_pct<100?x.left_pct+'% left · ':''}${usd(x.notional)} notional</div></div>
    <div style="text-align:right"><span class="pill ${cls(x.upnl)}">${usd(x.upnl,1)}</span><div class="sub ${cls(x.upnl_pct)}" style="margin-top:4px">${pc(x.upnl_pct)} on margin</div></div></div>
    <div class="kv4"><div><span>Entry</span>${px(x.entry)}</div><div><span>Mark</span><b class="${cls(x.upnl)}">${px(x.mark)}</b></div><div><span>Liq.</span><b class="y">${px(x.liq)}</b></div>
     <div><span>Stop</span><b class="r">${px(x.sl)}</b></div>${x.mode==='hf'?`<div><span>Target</span><b class="g">${px(x.tp1)}</b></div>`:x.mode==='trend'?`<div><span>Initial stop</span><b class="m">${px(x.sl0)}</b></div>`:`<div><span>TP1 ${x.tp1_done?'✓':''}</span><b class="g">${px(x.tp1)}</b></div>`}<div><span>Margin</span>${usd(x.margin)}</div></div>
    ${x.mode==='hf'?`<div class="stages" style="margin-top:8px"><div class="st hit">Maker fill</div><div class="st">Target ${px(x.tp1)} (maker)</div><div class="st">Stop · max 120m</div></div>`:x.mode==='trend'?`<div class="stages" style="margin-top:8px"><div class="st hit">3 ATR stop</div><div class="st ${(x.sl-x.sl0)*(x.side==='LONG'?1:-1)>0?'hit':''}">Trail 3 ATR (${ST.tf})</div><div class="st">No TP</div></div>`
     :`<div class="stages" style="margin-top:8px"><div class="st ${x.tp1_done?'hit':''}">TP1 50%</div><div class="st ${x.tp1_done?'hit':''}">Stop → BE</div><div class="st">Trail rest</div></div>`}
    <div class="sells"><span class="lbl">Close</span>${[25,50,100].map(p=>`<button class="sb${p===100?' all':''}" data-close="${i}" data-pct="${p}">${p}%</button>`).join('')}</div></div>`).join('');
  bindCommon();
  $$('[data-close]').forEach(b=>b.onclick=()=>{ hap(); const x=t[+b.dataset.close], p=+b.dataset.pct;
    confirmDlg(`Close ${p}% · ${x.symbol}`, `<div class="kv2"><span>Position</span><b>${x.side} ${x.lev}x</b><span>Mark</span><b>${px(x.mark)}</b><span>uPnL now</span><b class="${cls(x.upnl)}">${usd(x.upnl,1)}</b><span>Closing</span><b>${p}% of what is left</b></div>`,
      `Close ${p}% (paper)`, async()=>{ const r=await api('/api/manual/close',{symbol:x.symbol,pct:p,idempotency_key:key()}); toast(`Closed ${p}% · P&L ${usd(r.pnl,1)}`); R.pos(); }, true); });
  const ca=$('[data-closeall]'); if(ca) ca.onclick=()=>confirmDlg('Close ALL positions', `<div class="mb">Market-close all ${t.length} paper position(s).</div>`, 'Close all (paper)', async()=>{ await api('/api/manual/close',{all:true,idempotency_key:key()}); toast('All closed'); R.pos(); }, true);
 },
 async hist(){ const h=await api('/api/history'); if(MEME) return memeHist(h);
  const t=h.closed; const tot=t.reduce((a,b)=>a+b.pnl,0), w=t.filter(x=>x.pnl>0).length;
  V.innerHTML=`<div class="card hero row"><div><div class="lbl">Realized P&L</div><div class="val ${cls(tot)}">${usd(tot,1)}</div></div>
   <div style="text-align:right"><div class="lbl">Trades · win</div><div class="val">${t.length} · ${t.length?Math.round(w/t.length*100):0}%</div></div></div><h2>Closed trades</h2>`+
   t.map(x=>`<div class="card"><div class="row"><div><div class="tk">${x.symbol}${sideB(x.side)}<span class="lev">${x.lev}x</span>${x.manual?' <span class="tag">MANUAL</span>':''}</div>
    <div class="sub">${px(x.entry)} → ${px(x.exit)} · ${esc(x.reason)} · ${x.held_min}m</div><div class="sub">${hm(x.closed_iso)} · ${esc(x.setup||'')} · fees ${usd(x.fees)}</div></div>
    <div style="text-align:right"><div class="mono ${cls(x.pnl)}"><b>${usd(x.pnl,1)}</b></div><div class="sub ${cls(x.r_mult)}">${x.r_mult>=0?'+':''}${(+x.r_mult).toFixed(2)}R</div></div></div></div>`).join('')+(t.length?'':'<div class="card m">No closed trades yet.</div>');
 },
 async sig(){ const s=await api('/api/signals'); if(s.meme||(s.strategy||{}).name==='gmgn_meme'){ noteStrat(s.strategy); return memeScan(s); }
  const g=s.strategy||{}, DC=(g.name||'').startsWith('donchian'); stratBadge(g); const tf=g.tf||'1h', ttf=g.trend_tf||'4h', ch=chl(g.channel||20,tf);
  const HF=g.name==='hf_maker';
  if(HF){ V.innerHTML=hfWarn(g)+s.signals.map(x=>{const q=x.action&&x.action.startsWith('quotes');
    return `<div class="card"><div class="row"><div><div class="tk">${x.symbol}-USD${x.enabled?'':' <span class="tag">OFF</span>'}</div><div class="sub">${px(x.price)} · ${ago(x.checked_ago_s)}</div></div>
     <span class="chip ${q?'ps':'wt'}" style="margin:0">${q?'QUOTING':'IDLE'}</span></div>
     <div class="sub" style="margin-top:6px;color:var(--tx)">${esc(x.reason)}${x.action?' → '+esc(x.action):''}</div>
     <div class="sgl"><span class="g">bid ${px(x.bid)}</span><span class="y">ask ${px(x.ask)}</span><span>EMA60 ${px(x.mean)}</span><span>ATR ${x.atr_pct??'—'}%</span><span>fund ${x.funding_pct==null?'—':x.funding_pct+'%/'+(x.funding_hours||1)+'h'}</span></div></div>`}).join('')+
     `<h2>Recent fills</h2>`+(s.log.length?s.log.map(l=>`<div class="card"><div class="row"><div class="tk">${l.symbol}${sideB(l.side)}</div><span class="sub">${hm(l.time)}</span></div><div class="sub" style="margin-top:4px">${esc(l.reason)}</div></div>`).join(''):'<div class="card m">No fills yet.</div>'); return; }
  V.innerHTML=(DC?`<div class="card exp"><b>🧪 ${esc(g.label)}</b><div class="sub">Long when a ${tf} close breaks the prior ${ch} high and the last ${ttf} close is above ${ttf} EMA50${g.long_only===false?'; short on the mirror (close below the '+ch+' low, '+ttf+' below EMA50)':''}. Stop 3 ATR, trails 3 ATR (moves on each ${tf} close). PAPER only.${tf==='15m'?' <span class="y">Backtest of this 15m version lost money (PF 0.74) - test only.</span>':''}</div></div>`
   :`<div class="sub" style="margin:0 2px 8px">BULK prices · checked on every closed ${s.entry_tf} bar · trend 15m EMA50/200 · volume from reference venue</div>`)+s.signals.map(x=>{const st=x.status&&x.status!=='TRADING'?[x.status,'rj']:x.side&&x.action==='opened'?['OPENED','ps']:x.side?['SIGNAL','wt']:x.trend? ['WATCH','wt']:['NO TRADE','rj'];
   return `<div class="card"><div class="row"><div><div class="tk">${x.symbol}-USD ${x.side?sideB(x.side):''}${x.enabled?'':' <span class="tag">OFF</span>'}</div><div class="sub">${px(x.price)} · ${ago(x.checked_ago_s)}</div></div>
    <span class="chip ${st[1]}" style="margin:0">${st[0]}</span></div>
    <div class="sub" style="margin-top:6px;color:var(--tx)">${esc(x.reason)}${x.action&&x.side?' → '+esc(x.action):''}</div>
    ${DC?`<div class="sgl"><span class="${x.trend>0?'g':x.trend<0?'r':''}">${ttf} ${x.trend>0?'▲ UP':x.trend<0?'▼ DOWN':'— n/a'}${x.ema4h?' · EMA50 '+px(x.ema4h):''}</span><span class="${x.dist_pct!=null&&x.dist_pct<=0?'g':x.dist_pct!=null&&x.dist_pct<1?'y':''}">${chl(x.channel||20,tf)} high ${px(x.dch_high)}</span>
     <span>${x.dist_pct==null?'—':x.dist_pct<=0?'above high':x.dist_pct.toFixed(2)+'% to break'}</span><span>ATR ${x.atr_pct??'—'}%</span>${x.trail_stop?`<span class="y">trail ${px(x.trail_stop)}</span>`:''}<span>fund ${x.funding_pct==null?'—':x.funding_pct+'%/'+(x.funding_hours||1)+'h'}</span></div></div>`:''}
    ${DC?'':`<div class="sgl"><span class="${x.trend>0?'g':x.trend<0?'r':''}">15m ${x.trend>0?'▲ up':x.trend<0?'▼ down':'— flat'}</span><span class="${x.adx>=20?'g':'y'}">ADX ${x.adx??'—'}</span><span>RSI ${x.rsi??'—'}</span>
     <span>ATR ${x.atr_pct??'—'}%</span><span class="${x.vol_x>=1.2?'g':''}">vol ${x.vol_x??'—'}x</span><span>fund ${x.funding_pct==null?'—':x.funding_pct+'%/'+(x.funding_hours||1)+'h'}</span></div></div>`}`}).join('')+
   `<h2>Recent signals</h2>`+(s.log.length?s.log.map(l=>`<div class="card"><div class="row"><div class="tk">${l.symbol}${sideB(l.side)}</div><span class="sub">${hm(l.time)}</span></div><div class="sub" style="margin-top:4px">${esc(l.reason)}</div><div class="sub ${l.action==='opened'?'g':'y'}">${esc(l.action)}</div></div>`).join(''):'<div class="card m">No signals yet.</div>');
 },
 async chart(){ const s=await api('/api/status'); noteStrat(s.strategy);
  if(MEME){ V.innerHTML='<div class="card">Charts are for the futures strategies. Memecoin prices, rejects and open positions are on Scanner, Positions and History.</div>'; return; }
  CH.sym=CH.sym||SYMS[0]||'BTC';
  V.innerHTML=`<div class="chartbar"><div class="symchips">${SYMS.map(x=>`<button class="chip2 ${x===CH.sym?'on':''}" data-cs="${x}">${x}</button>`).join('')}</div>
   <div class="symchips">${['1m','5m','15m'].map(x=>`<button class="chip2 ${x===CH.tf?'on':''}" data-ctf="${x}">${x}</button>`).join('')}</div></div>
   <div class="row" style="margin:0 2px 8px"><div class="tk mono" id="cpx">—</div><div class="sub" id="csrc"></div></div>
   <div id="lc" class="chartbox"></div>
   <div class="legend"><span><span class="g">▲</span> long entry</span><span><span class="r">▼</span> short entry</span><span>● exit (P&L $)</span><span><i style="border-color:#ff3b5c"></i>stop</span><span><i style="border-color:#00ff9d"></i>target</span><span><i style="border-color:#3fa9ff"></i>maker bid</span><span><i style="border-color:#ffb020"></i>maker ask</span></div>
   <div class="note">Candles: BULK ${CH.tf} klines; the forming bar moves with the bot's live BULK mark (refresh 3 s). Times in IST. Charts by TradingView Lightweight Charts.</div>`;
  $$('[data-cs]').forEach(b=>b.onclick=()=>{hap(); CH.sym=b.dataset.cs; CH.first=null; R.chart();});
  $$('[data-ctf]').forEach(b=>b.onclick=()=>{hap(); CH.tf=b.dataset.ctf; CH.first=null; R.chart();});
  if(CH.chart){ try{CH.chart.remove()}catch(e){} CH.chart=null; }
  const el=$('#lc'), LC=window.LightweightCharts; if(!LC){ el.innerHTML='<div class="card r">chart library failed to load</div>'; return; }
  CH.chart=LC.createChart(el,{width:el.clientWidth,height:380,layout:{background:{type:'solid',color:'#070b16'},textColor:'#8b95a7',fontFamily:'ui-monospace,Menlo,monospace'},
    grid:{vertLines:{color:'rgba(255,255,255,.04)'},horzLines:{color:'rgba(255,255,255,.05)'}},rightPriceScale:{borderColor:'rgba(255,255,255,.08)'},
    timeScale:{borderColor:'rgba(255,255,255,.08)',timeVisible:true,secondsVisible:false},crosshair:{mode:0}});
  CH.series=CH.chart.addCandlestickSeries({upColor:'#00ff9d',downColor:'#ff3b5c',borderVisible:false,wickUpColor:'#00ff9d',wickDownColor:'#ff3b5c'});
  CH.lines=[]; new ResizeObserver(()=>CH.chart&&CH.chart.applyOptions({width:el.clientWidth})).observe(el);
  await chartTick(true);
 },
 async set(){ const s=await api('/api/settings'), ed=s.editable; const groups={}; s.fields.forEach(f=>(groups[f.group]=groups[f.group]||[]).push(f));
  const inp=f=>f.type==='symbols'?`<div class="symchips">${f.options.map(o=>`<button class="chip2 ${f.value.includes(o)?'on':''}" data-sym="${o}" ${ed?'':'disabled'}>${o}</button>`).join('')}</div>`
   : `<input type="number" step="${f.step}" min="${f.min}" max="${f.max}" data-k="${f.key}" data-t="${f.type}" value="${f.value}" ${ed?'':'disabled'}>`;
  V.innerHTML=(ed?'':`<div class="card y" style="font-size:12px">Read-only: ${s.demo?'demo data':'bot is offline'}. Use /set in Telegram.</div>`)+
   `<div class="card"><div class="row"><div><b>Auto-trading</b><div class="sub">pause = no new entries, positions still managed</div></div><button class="sw ${s.running?'on':''}" id="run" ${ed?'':'disabled'}></button></div></div>`+
   Object.entries(groups).map(([g,fs])=>`<h2>${g}</h2><div class="card">`+fs.map(f=>f.type==='symbols'?`<div class="field"><b>${f.label}</b><div class="sub" style="margin-bottom:8px">${f.unit}</div>${inp(f)}</div>`:`<div class="field row"><div><b>${f.label}</b>${f.unit?`<div class="sub">${f.unit}</div>`:''}</div>${inp(f)}</div>`).join('')+`</div>`).join('')+
   `<div class="note">Mode: PAPER (locked). Fees, start balance, data sources and keys cannot be changed from the app. Leverage is capped at 3x in code.</div>
   ${ed?'<button class="save" id="sv">Save settings</button>':''}`;
  if(!ed) return;
  $$('[data-sym]').forEach(b=>b.onclick=()=>{hap();b.classList.toggle('on');});
  $('#run').onclick=async()=>{ hap('medium'); const on=!$('#run').classList.contains('on'); try{ await api('/api/toggle',{running:on}); $('#run').classList.toggle('on',on); toast(on?'Auto-trading ON':'Paused'); }catch(e){toast(e.message)} };
  $('#sv').onclick=async()=>{ const ch={}; s.fields.forEach(f=>{ if(f.type==='symbols'){ const v=[...$$('[data-sym].on')].map(b=>b.dataset.sym); if(JSON.stringify(v)!==JSON.stringify(f.value)) ch[f.key]=v; }
     else { const el=$(`[data-k="${f.key}"]`); const v=f.type==='int'?parseInt(el.value):parseFloat(el.value); if(v!==f.value) ch[f.key]=v; } });
    if(!Object.keys(ch).length) return toast('Nothing changed'); hap('medium');
    try{ await api('/api/settings',{changes:ch}); toast('Saved ✓'); }catch(e){ toast(e.message); } };
 },
};

function confirmDlg(title, body, ok, fn, danger){ const o=document.createElement('div'); o.className='ov dlg';
  o.innerHTML=`<div class="modal ${danger?'warn':''}"><div class="mt">${title}</div><div class="mb">${body}</div><div class="mbtn"><button class="btn ghost" data-x>Cancel</button><button class="btn ${danger?'danger solid':'buy'}" data-ok>${ok}</button></div></div>`;
  document.body.appendChild(o); requestAnimationFrame(()=>o.classList.add('in'));
  const close=()=>{o.classList.remove('in'); setTimeout(()=>o.remove(),180);};
  o.querySelector('[data-x]').onclick=close; o.onclick=e=>{if(e.target===o) close();};
  o.querySelector('[data-ok]').onclick=async e=>{ e.target.disabled=true; hap('heavy'); try{ await fn(); close(); }catch(err){ toast(err.message); e.target.disabled=false; } };
}

function tradeSheet(side, sym){ let S={side:+side||1, sym:sym||'BTC', risk:1.0, pv:null, err:null, load:false}; const o=document.createElement('div'); o.className='ov';
  o.innerHTML='<div class="sheet"></div>'; document.body.appendChild(o); requestAnimationFrame(()=>o.classList.add('in'));
  const sh=o.querySelector('.sheet'); const close=()=>{o.classList.remove('in'); setTimeout(()=>o.remove(),220);}; o.onclick=e=>{if(e.target===o) close();};
  const load=async()=>{ S.load=true; S.err=null; draw(); try{ S.pv=await api('/api/manual/preview',{symbol:S.sym,side:S.side>0?'long':'short',risk_pct:S.risk}); }catch(e){ S.pv=null; S.err=e.message; } S.load=false; draw(); };
  const draw=()=>{ const p=S.pv, L=S.side>0;
   sh.innerHTML=`<div class="grab"></div><div class="row"><div class="mt">Manual ${L?'LONG':'SHORT'} · paper</div><button class="x" data-x>✕</button></div>
    <div class="ls"><button class="L ${L?'on':''}" data-sd="1">▲ LONG</button><button class="S ${!L?'on':''}" data-sd="-1">▼ SHORT</button></div>
    <label class="lbl">Market</label><div class="uni">${SYMS.map(s=>`<button class="chip2 ${S.sym===s?'on':''}" data-s="${s}">${s}-USD</button>`).join('')}</div>
    <label class="lbl">Risk if stop is hit</label><div class="amts">${[0.25,0.5,1,2].map(r=>`<button class="amt ${S.risk===r?'on':''}" data-r="${r}"><b>${r}%</b><span>of equity</span></button>`).join('')}</div>
    ${S.load?'<div class="pv load"><div class="spin"></div><span class="m">Live price + ATR stop…</span></div>':''}
    ${S.err?`<div class="errb">${esc(S.err)}</div>`:''}
    ${p&&!S.load?`<div class="card" style="margin-top:12px"><div class="kv2"><span>Price</span><b>${px(p.price)}</b><span>${p.mode==='trend'?'Stop (3 ATR '+ST.tf+')':'Stop (ATR)'}</span><b class="r">${px(p.stop)}</b>${p.tp1==null?'<span>Exit</span><b class="g">trail 3 ATR · no TP</b>':`<span>TP1 (50%)</span><b class="g">${px(p.tp1)}</b>`}
      <span>Size</span><b>${(+p.qty).toPrecision(4)} ${p.symbol} · ${usd(p.notional)}</b><span>Leverage · margin</span><b>${p.lev}x · ${usd(p.margin)}</b><span>Liq. price</span><b class="y">${px(p.liq)}</b>
      <span>Risk</span><b>${usd(p.risk_usd)} (${p.risk_pct}%)</b><span>Est. fees (3.5 bps)</span><b>${usd(p.est_fees)}</b><span>Book slippage</span><b>${p.est_slippage_pct!=null?p.est_slippage_pct.toFixed(3)+'%':'—'}</b><span>BULK min order</span><b>${usd(p.min_notional)}</b></div>
      ${p.capped_by_leverage?'<div class="wn">Size capped by the 3x leverage budget, real risk is below the % picked.</div>':''}
      ${p.signal?`<div class="sub" style="margin-top:6px">Bot signal now: ${esc(p.signal)}</div>`:''}
      ${p.allowed?'':`<div class="errb">Not allowed: ${esc(p.why)}</div>`}</div>`:''}
    <button class="save ${L?'':'warnbtn'}" data-go2 ${p&&p.allowed&&!S.load?'':'disabled'} style="margin-top:14px">Review ${L?'LONG':'SHORT'} ${S.sym}</button>`;
   sh.querySelector('[data-x]').onclick=close;
   sh.querySelectorAll('[data-sd]').forEach(b=>b.onclick=()=>{hap(); S.side=+b.dataset.sd; load();});
   sh.querySelectorAll('[data-s]').forEach(b=>b.onclick=()=>{hap(); S.sym=b.dataset.s; load();});
   sh.querySelectorAll('[data-r]').forEach(b=>b.onclick=()=>{hap(); S.risk=+b.dataset.r; load();});
   const g=sh.querySelector('[data-go2]'); if(g) g.onclick=()=>confirmDlg(`Confirm ${L?'LONG':'SHORT'} ${S.sym}-USD`,
     `<div class="kv2"><span>Side</span><b class="${L?'g':'r'}">${L?'LONG':'SHORT'} ${p.lev}x</b><span>Entry ≈</span><b>${px(p.price)}</b><span>Stop</span><b>${px(p.stop)}</b><span>Notional</span><b>${usd(p.notional)}</b><span>Max loss ≈</span><b class="r">${usd(p.risk_usd)}</b></div><div class="sub">Paper trade, simulated fill at market.</div>`,
     `Open ${L?'LONG':'SHORT'} (paper)`, async()=>{ await api('/api/manual/open',{symbol:S.sym,side:L?'long':'short',risk_pct:S.risk,idempotency_key:key()}); toast('Position opened ✓'); close(); tab='pos'; go(); }, !L);
  };
  draw(); load();
}

const CH={sym:null,tf:'1m',chart:null,series:null,lines:[],first:null}, IST_OFF=19800;
async function chartTick(fit){ if(!CH.series) return; const d=await api(`/api/candles?symbol=${CH.sym}&tf=${CH.tf}`);
  const bars=d.candles.map(b=>({...b,time:b.time+IST_OFF}));                       // lightweight-charts shows UTC: shift to IST
  if(fit||CH.first!==bars[0]?.time){ CH.series.setData(bars); CH.first=bars[0]?.time; if(fit) CH.chart.timeScale().fitContent(); }
  else if(bars.length){ CH.series.update(bars[bars.length-1]); }
  CH.series.setMarkers(d.markers.map(m=>({...m,time:m.time+IST_OFF})));
  CH.lines.forEach(l=>CH.series.removePriceLine(l));
  CH.lines=d.lines.map(l=>CH.series.createPriceLine({price:l.price,color:l.color,lineWidth:1,lineStyle:l.style,axisLabelVisible:true,title:l.title}));
  const c=bars[bars.length-1], o=bars[0]; if(c&&$('#cpx')) $('#cpx').innerHTML=`${CH.sym}-USD ${px(d.mark||c.close)} <span class="${cls(c.close-o.open)}" style="font-size:12px">${pc((c.close/o.open-1)*100)}</span>`;
  if($('#csrc')) $('#csrc').textContent=d.source; }
function bindCommon(){ $$('[data-trade]').forEach(b=>b.onclick=()=>{hap(); tradeSheet(b.dataset.trade);}); $$('[data-go]').forEach(b=>b.onclick=()=>{tab=b.dataset.go; go();}); }
async function go(){ $$('.tabs button').forEach(x=>x.classList.toggle('on',x.dataset.tab===tab)); clearInterval(timer);
  try{ const s=await api('/api/status'); noteStrat(s.strategy);}catch(e){}
  if(tab!=='chart'&&CH.chart){ try{CH.chart.remove()}catch(e){} CH.chart=null; CH.series=null; CH.first=null; }
  R[tab]().catch(e=>V.innerHTML=`<div class="card r">Error: ${esc(e.message)}</div>`);
  if(tab==='chart') timer=setInterval(()=>{ if(!document.querySelector('.ov')) chartTick().catch(()=>{}); },3000);
  if(tab==='dash'||tab==='pos'||tab==='sig') timer=setInterval(()=>{ if(!document.querySelector('.ov')) R[tab]().catch(()=>{}); }, tab==='pos'?3000:8000); }
$$('.tabs button').forEach(b=>b.onclick=()=>{hap(); tab=b.dataset.tab; go();});
go(); api('/api/status').then(s=>stratBadge(s.strategy)).catch(()=>{});
