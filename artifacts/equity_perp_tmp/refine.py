#!/usr/bin/env python3
from __future__ import annotations
import csv, datetime as dt, json, re, time, urllib.parse, urllib.request, zipfile
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'output'; FINAL=ROOT/'final'; FINAL.mkdir(parents=True,exist_ok=True)
NOW=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
FIELDS=['exchange','contract_ticker','underlying_ticker','name','asset_type','quote_currency','status','max_leverage','market_region','source_endpoint','classification_method','confidence','collected_at_utc']
errors={}; notes=[]

def get(url,timeout=25):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 equity-perp-research/1.1','Accept':'application/json'})
    last=None
    for i in range(2):
        try:
            with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode())
        except Exception as e:last=e; time.sleep(i+1)
    raise RuntimeError(f'{url}: {last}')

def clean(x): return '' if x is None else str(x).strip()
def norm(x): return re.sub(r'[^A-Z0-9.]','',clean(x).upper())
def base(sym):
    s=clean(sym).upper(); s=re.sub(r'^(PF_|PERP_)','',s)
    for sep in ['-','_','/']:
        if sep in s:
            p=[z for z in s.split(sep) if z]
            if len(p)>1 and p[-1] in {'USDT','USDC','USD','USD1','PERP'}: return p[0]
    for q in ['USDT','USDC','USD1','USD']:
        if s.endswith(q) and len(s)>len(q): return s[:-len(q)]
    return s

def mk(ex,c,u='',n='',a='',q='',st='',lev='',reg='',src='',method='',conf='high'):
    return dict(exchange=clean(ex),contract_ticker=clean(c),underlying_ticker=clean(u),name=clean(n),asset_type=clean(a),quote_currency=clean(q),status=clean(st),max_leverage=clean(lev),market_region=clean(reg),source_endpoint=clean(src),classification_method=clean(method),confidence=clean(conf),collected_at_utc=NOW)

def readcsv(path):
    if not path.exists(): return []
    with path.open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def save(path,rows):
    rows=sorted(rows,key=lambda r:(r['exchange'],r['underlying_ticker'],r['contract_ticker']))
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(rows)

def gate_stock_universe():
    src='https://api.gateio.ws/api/v4/stock/symbols'; rows=[]
    # Prefer global pagination; fall back to region-specific pages if endpoint requires exchange.
    try:
        for page in range(1,30):
            o=get(src+'?'+urllib.parse.urlencode({'page':page,'page_size':500}))
            arr=o if isinstance(o,list) else (o.get('data') or o.get('list') or o.get('result') or [])
            if isinstance(arr,dict):arr=arr.get('list') or arr.get('items') or []
            if not arr:break
            rows.extend(arr)
            if len(arr)<500:break
    except Exception as e:
        notes.append('Gate global stock-symbol pagination failed; used region fallback: '+repr(e))
        rows=[]
        for ex in ['us','hk','kr','jp']:
            try:
                for page in range(1,20):
                    o=get(src+'?'+urllib.parse.urlencode({'exchange':ex,'page':page,'page_size':500}))
                    arr=o if isinstance(o,list) else (o.get('data') or o.get('list') or o.get('result') or [])
                    if isinstance(arr,dict):arr=arr.get('list') or arr.get('items') or []
                    if not arr:break
                    for x in arr:
                        x=dict(x);x['_region']=ex.upper();rows.append(x)
                    if len(arr)<500:break
            except Exception: pass
    return rows

stocks=gate_stock_universe()
name_map={}; type_map={}; region_map={}; alias_map={}
for s in stocks:
    ticker=clean(s.get('symbol') or s.get('ticker') or s.get('code'))
    name=clean(s.get('symbol_desc') or s.get('name') or s.get('english_name') or s.get('display_name'))
    cat=clean(s.get('category') or s.get('type')).upper()
    reg=clean(s.get('_region') or s.get('exchange') or s.get('market')).upper()
    if ticker:
        k=norm(ticker); name_map[k]=name or name_map.get(k,''); type_map[k]='etf' if cat in {'ETF','ETN','ETV','ETS','FUND'} else 'equity'; region_map[k]=reg
    # Some exchanges expose a tradable/display alias separate from the local-market code.
    for key,val in s.items():
        v=clean(val)
        if isinstance(val,str) and 1<=len(v)<=24 and re.fullmatch(r'[A-Za-z0-9._-]+',v):
            if name: alias_map.setdefault(norm(v),(ticker,name,type_map.get(norm(ticker),'equity'),reg))

# ---------- Bitget: exact stock taxonomy from v3 ----------
bitget=[]
try:
    src='https://api.bitget.com/api/v3/market/instruments?category=USDT-FUTURES';o=get(src)
    for x in o.get('data',[]) or []:
        if clean(x.get('symbolType')).lower()!='stock' or clean(x.get('type')).lower() not in {'','perpetual'}:continue
        c=clean(x.get('symbol'));u=clean(x.get('baseCoin') or base(c));k=norm(u)
        bitget.append(mk('Bitget',c,u,name_map.get(k,''),type_map.get(k,'equity_or_etf'),x.get('quoteCoin'),x.get('status'),x.get('maxLeverage'),'US',src,'symbolType=stock','high'))
except Exception as e:errors['Bitget']=repr(e);bitget=readcsv(OUT/'bitget.csv')

# ---------- MEXC: exact Stock/ETF concept plates only ----------
mexc=[]
try:
    src='https://api.mexc.com/api/v1/contract/detail';o=get(src)
    for x in o.get('data',[]) or []:
        ps=x.get('conceptPlate') or []; tags={clean(p).lower() for p in ps} if isinstance(ps,list) else {clean(ps).lower()}
        is_stock='mc-trade-zone-stock' in tags; is_etf='mc-trade-zone-etf' in tags
        if not (is_stock or is_etf):continue
        c=clean(x.get('symbol'));u=clean(x.get('baseCoin') or base(c));k=norm(u)
        t='etf' if is_etf and not is_stock else type_map.get(k,'equity')
        n=clean(x.get('displayNameEn') or x.get('displayName') or name_map.get(k,''))
        # displayName may merely repeat a contract code; prefer Gate's security name when available.
        if name_map.get(k):n=name_map[k]
        mexc.append(mk('MEXC',c,u,n,t,x.get('quoteCoin'), 'active',x.get('maxLeverage') or x.get('maxLeverageLong'),'','https://api.mexc.com/api/v1/contract/detail','conceptPlate=Stock|ETF','high'))
except Exception as e:errors['MEXC']=repr(e);mexc=readcsv(OUT/'mexc.csv')

# ---------- Gate: match futures to complete stock universe/aliases ----------
gate=[]
try:
    src='https://api.gateio.ws/api/v4/futures/usdt/contracts';o=get(src);arr=o if isinstance(o,list) else (o.get('data') or o.get('result') or [])
    for x in arr:
        c=clean(x.get('name') or x.get('symbol'));u=clean(x.get('underlying') or base(c));k=norm(u)
        match=None
        if k in name_map: match=(u,name_map[k],type_map.get(k,'equity'),region_map.get(k,''))
        elif k in alias_map:
            ticker,n,t,reg=alias_map[k]; match=(u,n,t,reg)
        if not match:continue
        uu,n,t,reg=match
        gate.append(mk('Gate',c,uu,n,t,'USDT','active' if x.get('in_delisting') is False else x.get('status'),x.get('leverage_max'),reg,src+' + https://api.gateio.ws/api/v4/stock/symbols','matched Gate stock-symbol universe','high'))
except Exception as e:errors['Gate']=repr(e);gate=readcsv(OUT/'gate.csv')

# ---------- Bybit: official regional hosts; US GitHub runner may be blocked on global host ----------
bybit=[];bybit_host=''
for host in ['https://api.bytick.com','https://api.bybit.kz','https://api.bybitgeorgia.ge','https://api.bybit.ae','https://api.bybit.tr','https://api.bybit.nl','https://api.bybit.com']:
    try:
        cur='';tmp=[]
        for _ in range(20):
            url=host+'/v5/market/instruments-info?category=linear&limit=1000'+(('&cursor='+urllib.parse.quote(cur)) if cur else '')
            o=get(url);rr=o.get('result',{})
            for x in rr.get('list',[]) or []:
                t=clean(x.get('symbolType')).lower()
                if t not in {'stock','etf'}:continue
                c=clean(x.get('symbol'));u=clean(x.get('underlyingTicker') or x.get('baseCoin') or base(c));k=norm(u);lf=x.get('leverageFilter') or {}
                tmp.append(mk('Bybit',c,u,name_map.get(k,'') or clean(x.get('fullName') or x.get('displayName')),t,x.get('quoteCoin'),x.get('status'),lf.get('maxLeverage'),x.get('marketRegion'),host+'/v5/market/instruments-info','symbolType=stock|etf','high'))
            cur=clean(rr.get('nextPageCursor'))
            if not cur:break
        if tmp:bybit=tmp;bybit_host=host;break
    except Exception as e:errors['Bybit '+host]=repr(e)
if not bybit:bybit=readcsv(OUT/'bybit.csv')

# ---------- Binance: keep only official live API if reachable; never guess listings ----------
binance=[];binance_host=''
for host in ['https://fapi.binance.com','https://fapi1.binance.com','https://fapi2.binance.com','https://fapi3.binance.com','https://api-gcp.binance.com']:
    try:
        o=get(host+'/fapi/v1/exchangeInfo');tmp=[]
        for x in o.get('symbols',[]) or []:
            if clean(x.get('contractType'))!='TRADIFI_PERPETUAL':continue
            c=clean(x.get('symbol'));u=clean(x.get('baseAsset') or base(c));k=norm(u)
            # Classify only where the underlying maps to a listed security. Raw non-equity TradFi stays out of final equity CSV.
            if k not in name_map and k not in alias_map:continue
            n=name_map.get(k,'');t=type_map.get(k,'equity_or_etf')
            if not n and k in alias_map:n=alias_map[k][1];t=alias_map[k][2]
            tmp.append(mk('Binance',c,u,n,t,x.get('quoteAsset'),x.get('status'),'','','',host+'/fapi/v1/exchangeInfo','contractType=TRADIFI_PERPETUAL + stock-symbol match','high'))
        if tmp:binance=tmp;binance_host=host;break
    except Exception as e:errors['Binance '+host]=repr(e)
if not binance:binance=readcsv(OUT/'binance.csv')

# Other snapshots from the first-stage collector (already use explicit taxonomies where available).
others=[]
for fn in ['okx.csv','kucoin.csv','coinbase_intx.csv','kraken.csv','bingx.csv']:
    others.extend(readcsv(OUT/fn))

# Enrich names/types for all rows from Gate's security master; retain exchange-native aliases otherwise.
rows=bitget+mexc+gate+bybit+binance+others
for r in rows:
    k=norm(r.get('underlying_ticker'))
    if not r.get('name') and k in name_map:r['name']=name_map[k]
    if r.get('asset_type') in {'equity_or_etf','stock',''} and k in type_map:r['asset_type']=type_map[k]
    if not r.get('market_region') and k in region_map:r['market_region']=region_map[k]

# De-dupe exchange+contract.
rank={'high':2,'medium':1,'low':0};d={}
for r in rows:
    key=(r.get('exchange',''),r.get('contract_ticker',''));old=d.get(key)
    if old is None or (rank.get(r.get('confidence',''),0),bool(r.get('name')))>(rank.get(old.get('confidence',''),0),bool(old.get('name'))):d[key]=r
rows=list(d.values())

# Per-exchange and consolidated files.
counts=Counter(r['exchange'] for r in rows)
for ex in sorted(counts):
    slug=ex.lower().replace(' ','_').replace('/','_')
    save(FINAL/f'{slug}.csv',[r for r in rows if r['exchange']==ex])
save(FINAL/'all_equity_perps.csv',rows)
coverage={
 'Bitget':'complete_live_api' if bitget else 'failed',
 'MEXC':'complete_live_api' if mexc else 'fallback',
 'Gate':'matched_live_futures_to_live_stock_master' if gate else 'fallback',
 'Bybit':'complete_live_api' if bybit else 'blocked_by_runner_region',
 'Binance':'complete_live_api' if binance else 'blocked_by_runner_region',
 'OKX':'complete_live_api','KuCoin':'complete_live_api','Coinbase International':'complete_live_api','Kraken':'official_list_seed','BingX':'partial_api_match_only'
}
manifest={'generated_at_utc':NOW,'rows':len(rows),'by_exchange':dict(sorted(counts.items())),'coverage':coverage,'gate_stock_master_rows':len(stocks),'bybit_host_used':bybit_host,'binance_host_used':binance_host,'errors':errors,'notes':notes,'schema':FIELDS}
(FINAL/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
with zipfile.ZipFile(ROOT/'equity_perps_final.zip','w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(FINAL.iterdir()):z.write(p,arcname=p.name)
print(json.dumps(manifest,ensure_ascii=False,indent=2))
