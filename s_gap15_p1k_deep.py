"""
s_gap15_p1k_deep.py — Deep analysis of the champion LARGE+MEGA strategy
=========================================================================
Strategy: GAP > 1.5% + Price < Rs 1000 → SELL at b1 close
Config: TP=3.0% SL=0.5% EXIT=b45 | Top 15/day | 50k capital 5x margin

Includes:
  - Full 39 months (202301-202603)
  - UNSEEN data: 202201-202212 (never touched in any analysis)
  - Monthly P&L table with Rs amounts
  - Equity curve chart
  - Monthly bar chart
  - Drawdown chart
  - Daily distribution chart
  - All 13 quant ratios for FULL, TRAIN, TEST, UNSEEN
  - Per-trade detail

ZERO LOOKAHEAD.

LIVE-MATCHING FIXES (matches paper_trader.py behavior):
  FIX 1 — SL checked BEFORE TP: when both TP and SL are hit in the
          same 1-minute candle, SL wins (conservative). In live trading,
          the broker's stop-loss triggers first. Previously TP won ties,
          inflating returns by ~2%/month.
  FIX 2 — Gap slippage on SL: when a candle OPENS above the SL price
          (gap-through), the actual exit is at the OPEN price, not the
          SL price. This makes losses worse than flat -0.5% (can be
          -1% to -5%). ~14% of SL trades have gap-through slippage.
          Previously all SL trades showed exactly -0.5%, hiding real losses.
  FIX 3 — Transaction cost subtracted from every trade (COST=0.15%).
          Brokerage + STT + slippage as per parquets/prompt_to_use_cache.md.
          Applied once to every return (win or loss). Without this, every
          metric is inflated and the true edge is overstated.
"""

import sys,io,time,json,warnings,signal,os,gc
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from pathlib import Path

warnings.filterwarnings("ignore")
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',line_buffering=True)
signal.signal(signal.SIGINT,lambda*_:(print("\nInterrupted!"),os._exit(1)))

DATA_DIR=Path("C:/Users/BT-25/Desktop/project/dhan-trader/data")
OUT_DIR=DATA_DIR/"analysis_gap15_p1k"
OUT_DIR.mkdir(exist_ok=True)
CAPITAL=50000;LEV=5;RF_ANNUAL=0.065
TP=3.0;SL=0.5;EXIT_BKT=45;TOP_N=15;ENTRY_BKT=1
COST=0.15  # % per trade (brokerage + STT + slippage). Subtracted from every return.

# Capital Allocation
# actual_pos = min(CAPITAL * LEV / n_trades_today, base_pos * CAP_MULT)
# CAP_MULT=2 → best Sharpe for LARGE+MEGA strategy (from capital_allocation_test.py)
CAP_MULT=2

t0=time.perf_counter()
def elapsed():
    s=time.perf_counter()-t0;m=int(s//60)
    return f"[{m:02d}:{s%60:05.2f}]" if m else f"[{s:05.2f}s]"
def log(msg):print(f"{elapsed()} {msg}",flush=True)

# ============================================================================
#  LOAD — include 2022 unseen data
# ============================================================================
log("Loading (including 2022 unseen)...")
vg=json.load(open(DATA_DIR/"volume_groups.json"))["volume_groups"]
MEGA=set(vg.get("MEGA (>100cr/day)",[]))
LARGE=set(vg.get("LARGE (10-100cr/day)",[]))
TARGET=MEGA|LARGE

# 2022 + 2023-2026
ALL_M=list(range(202201,202213))+list(range(202301,202313))+list(range(202401,202413))+list(range(202501,202513))+[202601,202602,202603]
COLS=["symbol","date","gap_pct","day_open","bucket","open","high","low","close","vwap","buy_ratio","volume"]
MAX_BKT=EXIT_BKT+1

dfs=[]
for ym in ALL_M:
    p=DATA_DIR/f"candles_{ym}.parquet"
    if not p.exists():continue
    d=pd.read_parquet(p,columns=COLS)
    d=d[(d["bucket"]<=MAX_BKT)&(d["symbol"].isin(TARGET))]
    for c in ["open","high","low","close","gap_pct","day_open","vwap","buy_ratio"]:
        d[c]=d[c].astype(np.float32)
    d["volume"]=d["volume"].astype(np.int32)
    dfs.append(d)
    log(f"  {ym}: {len(d):,} rows")
df=pd.concat(dfs,ignore_index=True);del dfs;gc.collect()
log(f"Total: {len(df):,} rows | {df['symbol'].nunique()} symbols | {df['date'].nunique()} days")

# Pivot
log("Pivoting...")
sd=df.groupby(["symbol","date"]).agg(gap_pct=("gap_pct","first"),day_open=("day_open","first")).reset_index()
piv=sd.copy()
for val in ["close","open","high","low"]:
    sub=df[["symbol","date","bucket",val]].copy()
    p=sub.pivot_table(index=["symbol","date"],columns="bucket",values=val,aggfunc="first")
    p.columns=[f"{val}_b{int(c)}" for c in p.columns]
    for col in p.columns:
        if p[col].dtype==np.float64:p[col]=p[col].astype(np.float32)
    piv=piv.merge(p,on=["symbol","date"],how="left")
    del sub,p;gc.collect()
del df,sd;gc.collect()

DATES=piv["date"].values.astype(str)
MONTHS=np.array([d[:7] for d in DATES])
SYMS=piv["symbol"].values
N=len(piv)
BKTS=list(range(1,MAX_BKT+1));NB=len(BKTS)
b2i={b:i for i,b in enumerate(BKTS)}
def bi(b):return b2i[b]
def _a(p,b):
    c=f"{p}_b{b}";return piv[c].values.astype(np.float32) if c in piv.columns else np.full(N,np.nan,np.float32)

O=np.stack([_a("open",b) for b in BKTS],1)
H=np.stack([_a("high",b) for b in BKTS],1)
L=np.stack([_a("low",b) for b in BKTS],1)
C=np.stack([_a("close",b) for b in BKTS],1)
GAP=piv["gap_pct"].values.astype(np.float32)
del piv;gc.collect()

udates=np.unique(DATES);nd=len(udates)
d2i={d:i for i,d in enumerate(udates)}
DIDX=np.array([d2i[d] for d in DATES],np.int32)
umonths=sorted(np.unique(MONTHS));nm=len(umonths)
m2i={m:i for i,m in enumerate(umonths)}
d2m=np.zeros(nd,np.int32)
for i in range(N):d2m[DIDX[i]]=m2i[MONTHS[i]]

PRICE=C[:,bi(1)].copy()
VALID=(O[:,bi(1)]>0)&~np.isnan(C[:,bi(1)])
NC=np.where(O[:,bi(1)]>0,(H[:,bi(1)]-L[:,bi(1)])/O[:,bi(1)]*100,0)>=0.01

log(f"Pivoted: {N:,} sd | {nd} days | {nm} months ({umonths[0]}..{umonths[-1]})")

# ============================================================================
#  MASK + SIMULATE
# ============================================================================
log("Mask + Simulate...")

mask = (GAP > 1.5) & (PRICE < 1000) & (PRICE > 0) & NC & VALID
log(f"  Pool: {mask.sum():,}")

ei=bi(ENTRY_BKT);hi=bi(EXIT_BKT)
ep=C[:,ei].copy();valid=mask&(ep>0)&~np.isnan(ep)
n_valid=int(valid.sum())
ep_v=ep[valid];s=ei+1;e=min(hi+1,NB)
fH=H[valid,s:e];fL=L[valid,s:e];fC=C[valid,s:e];nf=fH.shape[1]

fO=O[valid,s:e]  # future opens for gap slippage
tph=fL<=ep_v[:,None]*(1-TP/100)
slh=fH>=ep_v[:,None]*(1+SL/100)
def first_true(a):
    any_=a.any(1);ix=np.argmax(a,1);ix[~any_]=nf;return ix
ti=first_true(tph);si=first_true(slh)

# FIX 1: SL checked BEFORE TP (matches paper_trader.py line 379-393)
# When both hit in same candle (si==ti), SL wins (conservative)
sl_hit=(si<ti)&(si<nf)            # SL strictly first
both_same=(si==ti)&(si<nf)        # same candle — SL wins (checked first in live)
sl_hit=sl_hit|both_same
tp_win=(ti<si)&(ti<nf)            # TP only when strictly before SL
time_exit=~tp_win&~sl_hit

ret=np.full(n_valid,np.nan,np.float32)
ret[tp_win]=TP

# FIX 2: Gap slippage on SL (matches paper_trader.py line 382-388)
# If candle opens ABOVE SL price, actual exit is at OPEN (worse than SL)
sl_price_arr=ep_v*(1+SL/100)
sl_idx=np.where(sl_hit)[0]
for j in sl_idx:
    si_j=si[j]
    if si_j<nf:
        open_at_sl=fO[j,si_j]
        if not np.isnan(open_at_sl) and open_at_sl>=sl_price_arr[j]:
            # Gap through SL — exit at open (worse loss)
            ret[j]=-(open_at_sl-ep_v[j])/ep_v[j]*100
        else:
            ret[j]=-SL
    else:
        ret[j]=-SL

if time_exit.any():
    rev=fC[time_exit][:,::-1];vm=~np.isnan(rev);fv=np.argmax(vm,1);has=vm.any(1)
    lc=np.full(time_exit.sum(),np.nan,np.float32);lc[has]=rev[has,fv[has]]
    epe=ep_v[time_exit];ret[time_exit]=np.where(epe>0,(epe-lc)/epe*100,np.nan).astype(np.float32)

# FIX 3: Subtract transaction cost from every trade (win or loss)
ret=ret-COST

exit_bkt_arr=np.full(n_valid,EXIT_BKT,np.int32)
exit_bkt_arr[tp_win]=s+ti[tp_win];exit_bkt_arr[sl_hit]=s+si[sl_hit]
full_ret=np.full(N,np.nan,np.float32);full_ret[valid]=ret

log(f"  Simulated {n_valid:,} trades")

# ============================================================================
#  TOP-N SELECTION (scored by GAP — higher gap = pick first)
# ============================================================================
has_r=~np.isnan(full_ret)&mask
idx=np.where(has_r)[0];vr=full_ret[idx];vd=DIDX[idx]
vs=GAP[idx]  # score = GAP (higher gap picked first)
sk=vd.astype(np.float64)*1e6-vs.astype(np.float64)
order=np.argsort(sk);sr=vr[order];sd_=vd[order];si=idx[order]
dc=np.concatenate([[1],(np.diff(sd_)!=0).astype(np.int32)])
gs=np.where(dc)[0]
gc_=np.arange(len(sr))-np.repeat(gs,np.diff(np.concatenate([gs,[len(sr)]])))
sel=gc_<TOP_N
sel_ret=sr[sel];sel_day=sd_[sel];sel_idx=si[sel]

# Daily P&L (capped capital allocation)
total_margin=CAPITAL*LEV
base_pos=total_margin/TOP_N
max_pos=base_pos*CAP_MULT

d_count=np.bincount(sel_day,minlength=nd).astype(np.float32)
active=d_count>0
d_pos=np.zeros(nd,np.float32)
d_pos[active]=np.minimum(total_margin/d_count[active],max_pos)

daily_rs=np.zeros(nd,np.float32)
for j in range(len(sel_ret)):
    di=sel_day[j]
    daily_rs[di]+=sel_ret[j]/100*d_pos[di]

dpnl=daily_rs/CAPITAL*100
cum_eq=CAPITAL+np.cumsum(daily_rs)

log(f"  Selected {len(sel_ret):,} trades | {int(active.sum())} active days")

# ============================================================================
#  TRADE + MONTHLY STATS
# ============================================================================
wins=sel_ret[sel_ret>0];losses=sel_ret[sel_ret<0]
total_t=len(sel_ret);n_w=len(wins);n_l=len(losses)
wr=n_w/total_t if total_t>0 else 0

# Monthly
m_roc=[];m_trades=[];m_wins=[];m_pnl_rs=[]
for mi,m in enumerate(umonths):
    dim=np.where((d2m==mi)&active)[0]
    mroc=dpnl[dim].sum() if len(dim)>0 else 0.0
    mm=np.isin(sel_day,dim);mr=sel_ret[mm]
    m_roc.append(mroc)
    m_trades.append(len(mr))
    m_wins.append(int((mr>0).sum()))
    m_pnl_rs.append(mroc/100*CAPITAL)
m_roc=np.array(m_roc);m_pnl_rs=np.array(m_pnl_rs)

# Periods
is_unseen=np.array([m<'2023-01' for m in umonths])  # 2022
is_train=np.array([(m>='2023-01')&(m<='2025-01') for m in umonths])
is_test=np.array([m>'2025-01' for m in umonths])

# ============================================================================
#  PRINT FULL REPORT
# ============================================================================
print(f"\n{'='*100}")
print(f"  S_gap15_p1k — CHAMPION STRATEGY DEEP ANALYSIS")
print(f"  GAP > 1.5% + Price < Rs 1000 → SELL at b1 close (9:16 AM)")
print(f"  TP=3.0% SL=0.5% EXIT=b45 (10:00 AM) | Top 15/day scored by GAP")
print(f"  Capital: Rs {CAPITAL:,} | Leverage: {LEV}x | Cost: {COST}% per trade")
print(f"  Data: {umonths[0]} to {umonths[-1]} ({nm} months)")
print(f"  UNSEEN: 2022 data (NEVER used in any prior analysis)")
print(f"{'='*100}")

print(f"\n  TRADE SUMMARY")
print(f"  {'─'*50}")
print(f"  Pool:           {n_valid:,}")
print(f"  Selected:       {total_t:,}")
print(f"  Active days:    {int(active.sum())}")
print(f"  Avg/day:        {total_t/max(int(active.sum()),1):.1f}")
print(f"  Wins:           {n_w:,} ({wr*100:.1f}%)")
print(f"  Losses:         {n_l:,}")
print(f"  Avg win:        {wins.mean():+.3f}%")
print(f"  Avg loss:       {losses.mean():+.3f}%")
print(f"  TP hits:        {int(tp_win.sum()):,} ({tp_win.sum()/n_valid*100:.1f}%)")
print(f"  SL hits:        {int(sl_hit.sum()):,} ({sl_hit.sum()/n_valid*100:.1f}%)")
print(f"  Time exits:     {int(time_exit.sum()):,} ({time_exit.sum()/n_valid*100:.1f}%)")

# Monthly table
print(f"\n  MONTHLY P&L")
print(f"  {'─'*130}")
print(f"  {'Month':<10} {'ROC%':>8} {'Trades':>7} {'Wins':>6} {'WR%':>6} {'Rs P&L':>10} {'Cum Rs':>12} {'Equity':>12} {'':>8} {'Period':>8}")
print(f"  {'─'*130}")

cum_rs=0
for mi,m in enumerate(umonths):
    roc=m_roc[mi];t=m_trades[mi];w=m_wins[mi]
    ww=w/t*100 if t>0 else 0
    rs=m_pnl_rs[mi];cum_rs+=rs
    st="GREEN" if roc>0 else "RED" if roc<0 else "---"
    period="UNSEEN" if is_unseen[mi] else "TRAIN" if is_train[mi] else "TEST"
    eq=CAPITAL+cum_rs
    if t>0:
        print(f"  {m:<10} {roc:>+7.2f}% {t:>7} {w:>6} {ww:>5.1f}% {rs:>+9.0f} {cum_rs:>+11.0f} {eq:>11.0f} {st:<8} [{period}]")
print(f"  {'─'*130}")

# Period summaries
for period_name, period_mask in [("UNSEEN (2022)",is_unseen),("TRAIN (2023-01..2025-01)",is_train),("TEST (2025-02..2026-03)",is_test),("FULL",np.ones(nm,bool))]:
    pm=period_mask
    rocs=m_roc[pm];trades_p=np.array(m_trades)[pm]
    if rocs.sum()==0 and trades_p.sum()==0:continue
    active_m=rocs!=0
    g=int((rocs>0).sum());r=int((rocs[active_m]<=0).sum())
    print(f"\n  {period_name}:")
    print(f"    Months: {int(active_m.sum())} | Green: {g} Red: {r} | Avg: {rocs[active_m].mean():+.2f}%/m | Total: {rocs.sum():+.1f}% | Worst: {rocs[active_m].min():+.2f}%")
    print(f"    Trades: {int(trades_p.sum()):,} | Rs P&L: {m_pnl_rs[pm].sum():+,.0f}")

# Equity stats
pk=np.maximum.accumulate(cum_eq)
dd=(pk-cum_eq)/pk*100
mdd_pct=dd.max();mdd_rs=(pk-cum_eq).max()
print(f"\n  EQUITY CURVE")
print(f"  Start: Rs {CAPITAL:,} → End: Rs {cum_eq[-1]:,.0f}")
print(f"  Peak: Rs {pk.max():,.0f} | Max DD: {mdd_pct:.2f}% (Rs {mdd_rs:,.0f})")

# ============================================================================
#  13 QUANT RATIOS
# ============================================================================
def compute_ratios(dpnl_arr, label):
    dr=dpnl_arr[dpnl_arr!=0]
    if len(dr)<10:print(f"  {label}: insufficient data");return None
    dr_d=dr/100;rf_d=RF_ANNUAL/252
    mean_d=dr_d.mean();std_d=dr_d.std(ddof=1)
    ann_ret=mean_d*252
    cum=CAPITAL*np.cumprod(1+dr_d)
    years=len(dr)/252
    pk=np.maximum.accumulate(cum);dd=(pk-cum)/pk
    mdd=dd.max();avg_dd=dd[dd>0].mean() if (dd>0).any() else 0.001
    exc=dr_d-rf_d
    sharpe=(exc.mean()/exc.std(ddof=1))*np.sqrt(252) if exc.std(ddof=1)>0 else 0
    ds=dr_d[dr_d<rf_d]-rf_d
    ds_std=np.sqrt((ds**2).mean()) if len(ds)>0 else 0.001
    sortino=(mean_d-rf_d)/ds_std*np.sqrt(252)
    calmar=ann_ret/mdd if mdd>0 else 999
    bench=0.12/252;trk=dr_d-bench
    ir=(trk.mean()/trk.std(ddof=1))*np.sqrt(252) if trk.std(ddof=1)>0 else 0
    g=dr_d[dr_d>0].sum();la=np.abs(dr_d[dr_d<0].sum())
    pf=g/la if la>0 else 999
    aw=dr_d[dr_d>0].mean() if (dr_d>0).any() else 0
    al=np.abs(dr_d[dr_d<0].mean()) if (dr_d<0).any() else 0.001
    payoff=aw/al
    wr_=(dr_d>0).sum()/len(dr_d)
    expectancy=(wr_*aw-(1-wr_)*al)*100
    total_net=(cum[-1]-CAPITAL)/CAPITAL
    recovery=total_net/mdd if mdd>0 else 999
    sterling=ann_ret/(avg_dd+0.10)
    omega=g/la if la>0 else 999
    ulcer=np.sqrt((dd**2).mean())
    upi=ann_ret/ulcer if ulcer>0 else 999
    cagr=(cum[-1]/CAPITAL)**(1/years)-1 if years>0 else 0
    max_dd=mdd*100

    print(f"\n  {'='*60}")
    print(f"  {label}")
    print(f"  {'='*60}")
    print(f"  {len(dr)} days ({years:.1f} yrs) | Rs {CAPITAL:,} → Rs {cum[-1]:,.0f}")
    print(f"  {'─'*60}")
    print(f"   1. Sharpe          {sharpe:>10.3f}")
    print(f"   2. Sortino         {sortino:>10.3f}")
    print(f"   3. Calmar          {calmar:>10.3f}")
    print(f"   4. Info Ratio      {ir:>10.3f}")
    print(f"   5. Profit Factor   {pf:>10.3f}")
    print(f"   6. Payoff Ratio    {payoff:>10.3f}")
    print(f"   7. Expectancy %    {expectancy:>9.3f}%")
    print(f"   8. Recovery Factor {recovery:>10.3f}")
    print(f"   9. Sterling        {sterling:>10.3f}")
    print(f"  10. Omega           {omega:>10.3f}")
    print(f"  11. UPI             {upi:>10.3f}")
    print(f"  12. CAGR            {cagr*100:>9.2f}%")
    print(f"  13. Max Drawdown    {max_dd:>9.2f}%")
    print(f"  {'─'*60}")
    print(f"  Daily: Avg={mean_d*100:+.4f}% Std={std_d*100:.4f}%")
    print(f"  Win days: {(dr_d>0).sum()}/{len(dr_d)} ({wr_*100:.1f}%)")
    print(f"  Best: {dr_d.max()*100:+.3f}% | Worst: {dr_d.min()*100:+.3f}%")
    return {'sharpe':sharpe,'sortino':sortino,'calmar':calmar,'ir':ir,'pf':pf,
            'payoff':payoff,'expectancy':expectancy,'recovery':recovery,
            'sterling':sterling,'omega':omega,'upi':upi,'cagr':cagr*100,'mdd':max_dd}

# Build day-level period masks
unseen_days=np.zeros(nd,bool)
train_days=np.zeros(nd,bool)
test_days=np.zeros(nd,bool)
for di in range(nd):
    mi=d2m[di];m=umonths[mi]
    if m<'2023-01':unseen_days[di]=True
    elif m<='2025-01':train_days[di]=True
    else:test_days[di]=True

ratios_full=compute_ratios(dpnl,"FULL PERIOD")
ratios_unseen=compute_ratios(dpnl[unseen_days],"UNSEEN 2022 (never touched)")
ratios_train=compute_ratios(dpnl[train_days],"TRAIN (2023-01..2025-01)")
ratios_test=compute_ratios(dpnl[test_days],"TEST (2025-02..2026-03)")

# Comparison table
if ratios_unseen and ratios_train and ratios_test:
    print(f"\n  {'='*70}")
    print(f"  PERIOD COMPARISON")
    print(f"  {'='*70}")
    print(f"  {'Metric':<25} {'UNSEEN':>10} {'TRAIN':>10} {'TEST':>10} {'FULL':>10}")
    print(f"  {'─'*70}")
    for key,label in [('sharpe','Sharpe'),('sortino','Sortino'),('calmar','Calmar'),
                       ('pf','Profit Factor'),('payoff','Payoff Ratio'),
                       ('expectancy','Expectancy %'),('omega','Omega'),
                       ('cagr','CAGR %'),('mdd','Max DD %')]:
        u=ratios_unseen[key];tr=ratios_train[key];te=ratios_test[key];f=ratios_full[key]
        print(f"  {label:<25} {u:>9.2f} {tr:>9.2f} {te:>9.2f} {f:>9.2f}")

# ============================================================================
#  CHARTS
# ============================================================================
log("Generating charts...")

# Date labels for x-axis
date_labels=[udates[i] for i in range(nd)]

# 1. Equity Curve
fig,ax=plt.subplots(figsize=(16,6))
ax.plot(range(nd),cum_eq,color='#2196F3',linewidth=1.5,label='Equity')
ax.fill_between(range(nd),CAPITAL,cum_eq,alpha=0.15,color='#2196F3')
# Mark period boundaries
for mi,m in enumerate(umonths):
    if m in ['2023-01','2025-02']:
        di_first=None
        for di in range(nd):
            if d2m[di]==mi:di_first=di;break
        if di_first:
            ax.axvline(di_first,color='red',linestyle='--',alpha=0.5)
            ax.text(di_first,cum_eq.max()*0.95,m,rotation=90,fontsize=8,color='red')
ax.set_title('S_gap15_p1k Equity Curve (Rs 50k start, 5x leverage)',fontsize=14,fontweight='bold')
ax.set_ylabel('Equity (Rs)')
ax.set_xlabel('Trading Days')
ax.legend()
ax.grid(True,alpha=0.3)
# Sparse x-ticks
tick_pos=[];tick_lab=[]
for mi,m in enumerate(umonths):
    if mi%3==0:
        for di in range(nd):
            if d2m[di]==mi:tick_pos.append(di);tick_lab.append(m);break
ax.set_xticks(tick_pos);ax.set_xticklabels(tick_lab,rotation=45,fontsize=7)
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x,p:f"Rs {x:,.0f}"))
plt.tight_layout()
plt.savefig(OUT_DIR/"equity_curve.png",dpi=150)
plt.close()
log(f"  Saved equity_curve.png")

# 2. Monthly Bar Chart
fig,ax=plt.subplots(figsize=(18,6))
colors=[]
for mi in range(nm):
    if is_unseen[mi]:colors.append('#9C27B0' if m_roc[mi]>0 else '#E91E63')
    elif is_train[mi]:colors.append('#4CAF50' if m_roc[mi]>0 else '#F44336')
    else:colors.append('#2196F3' if m_roc[mi]>0 else '#FF9800')
ax.bar(range(nm),m_roc,color=colors,edgecolor='white',linewidth=0.5)
ax.axhline(0,color='black',linewidth=0.5)
ax.set_title('Monthly ROC % — S_gap15_p1k',fontsize=14,fontweight='bold')
ax.set_ylabel('Monthly ROC %')
ax.set_xticks(range(nm))
ax.set_xticklabels([m[2:] for m in umonths],rotation=90,fontsize=7)
ax.grid(True,alpha=0.3,axis='y')
# Legend
from matplotlib.patches import Patch
legend_elements=[Patch(facecolor='#9C27B0',label='UNSEEN 2022'),
                 Patch(facecolor='#4CAF50',label='TRAIN 2023-2025'),
                 Patch(facecolor='#2196F3',label='TEST 2025-2026'),
                 Patch(facecolor='#F44336',label='Red month')]
ax.legend(handles=legend_elements,loc='upper left',fontsize=8)
plt.tight_layout()
plt.savefig(OUT_DIR/"monthly_roc.png",dpi=150)
plt.close()
log(f"  Saved monthly_roc.png")

# 3. Drawdown Chart
fig,ax=plt.subplots(figsize=(16,4))
ax.fill_between(range(nd),-dd,0,color='#F44336',alpha=0.4)
ax.plot(range(nd),-dd,color='#F44336',linewidth=0.5)
ax.set_title('Drawdown %',fontsize=14,fontweight='bold')
ax.set_ylabel('Drawdown %')
ax.set_xticks(tick_pos);ax.set_xticklabels(tick_lab,rotation=45,fontsize=7)
ax.grid(True,alpha=0.3)
plt.tight_layout()
plt.savefig(OUT_DIR/"drawdown.png",dpi=150)
plt.close()
log(f"  Saved drawdown.png")

# 4. Daily Return Distribution
fig,ax=plt.subplots(figsize=(12,5))
daily_active=dpnl[active]
ax.hist(daily_active,bins=100,color='#2196F3',edgecolor='white',alpha=0.8)
ax.axvline(daily_active.mean(),color='red',linestyle='--',label=f'Mean: {daily_active.mean():+.3f}%')
ax.axvline(0,color='black',linewidth=1)
ax.set_title('Daily ROC Distribution',fontsize=14,fontweight='bold')
ax.set_xlabel('Daily ROC %')
ax.set_ylabel('Frequency')
ax.legend()
ax.grid(True,alpha=0.3)
plt.tight_layout()
plt.savefig(OUT_DIR/"daily_distribution.png",dpi=150)
plt.close()
log(f"  Saved daily_distribution.png")

# 5. Monthly P&L in Rs (bar)
fig,ax=plt.subplots(figsize=(18,5))
ax.bar(range(nm),m_pnl_rs,color=colors,edgecolor='white',linewidth=0.5)
ax.axhline(0,color='black',linewidth=0.5)
ax.set_title('Monthly P&L in Rs',fontsize=14,fontweight='bold')
ax.set_ylabel('Rs')
ax.set_xticks(range(nm))
ax.set_xticklabels([m[2:] for m in umonths],rotation=90,fontsize=7)
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x,p:f"Rs {x:,.0f}"))
ax.grid(True,alpha=0.3,axis='y')
plt.tight_layout()
plt.savefig(OUT_DIR/"monthly_pnl_rs.png",dpi=150)
plt.close()
log(f"  Saved monthly_pnl_rs.png")

# 6. Cumulative Monthly Rs
fig,ax=plt.subplots(figsize=(16,5))
cum_m_rs=np.cumsum(m_pnl_rs)
ax.plot(range(nm),CAPITAL+cum_m_rs,color='#4CAF50',linewidth=2,marker='o',markersize=4)
ax.fill_between(range(nm),CAPITAL,CAPITAL+cum_m_rs,alpha=0.15,color='#4CAF50')
for mi,m in enumerate(umonths):
    if m in ['2023-01','2025-02']:
        ax.axvline(mi,color='red',linestyle='--',alpha=0.5)
        ax.text(mi,CAPITAL+cum_m_rs.max()*0.95,m,rotation=90,fontsize=8,color='red')
ax.set_title('Cumulative Equity (Monthly)',fontsize=14,fontweight='bold')
ax.set_ylabel('Rs')
ax.set_xticks(range(nm))
ax.set_xticklabels([m[2:] for m in umonths],rotation=90,fontsize=7)
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x,p:f"Rs {x:,.0f}"))
ax.grid(True,alpha=0.3)
plt.tight_layout()
plt.savefig(OUT_DIR/"cumulative_equity.png",dpi=150)
plt.close()
log(f"  Saved cumulative_equity.png")

# ============================================================================
#  STREAKS + DAY OF WEEK
# ============================================================================
dw=dpnl[active]>0;dl=dpnl[active]<0
def max_streak(arr):
    ms=0;c=0
    for v in arr:
        if v:c+=1;ms=max(ms,c)
        else:c=0
    return ms

print(f"\n  STREAKS")
print(f"  Max winning days: {max_streak(dw)} | Max losing: {max_streak(dl)}")
print(f"  Max green months: {max_streak(m_roc>0)} | Max red: {max_streak(m_roc<=0)}")

dow=np.array([pd.Timestamp(d).dayofweek for d in udates])
dn=['Mon','Tue','Wed','Thu','Fri']
print(f"\n  DAY OF WEEK")
for d in range(5):
    dm=(dow==d)&active
    if dm.sum()==0:continue
    dr=dpnl[dm]
    print(f"    {dn[d]}: Avg={dr.mean():+.3f}% Win={((dr>0).sum()/len(dr)*100):.1f}% Days={len(dr)}")

np.save(OUT_DIR/"daily_pnl_rs.npy",daily_rs)
np.save(OUT_DIR/"udates.npy",udates)
log(f"Saved daily_pnl_rs.npy (for combined-portfolio / walk-forward analysis)")

print(f"\n  Charts saved to: {OUT_DIR}")
log(f"Done in {time.perf_counter()-t0:.1f}s")
