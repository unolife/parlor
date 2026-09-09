#!/usr/bin/env python3
from __future__ import annotations
import csv, datetime as dt, json, re, time, urllib.parse, urllib.request, zipfile
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'output'; OUT.mkdir(parents=True,exist_ok=True)
NOW=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
FIELDS=['exchange','contract_ticker','underlying_ticker','name','asset_type','quote_currency','status','max_leverage','market_region','source_endpoint','classification_method','confidence','collected_at_utc']
COM={'XAU','XAG','XPT','XPD','GOLD','SILVER','CL','BZ','WTI','BRENT','COPPER','NATGAS','NG','HG','XCU','USOIL','UKOIL'}
PRIVATE={'OPENAI','ANTHROPIC','SPACEX','SPCX','UNITREE','MINIMAX','ZHIPU','XAI'}
errors={}; meta={}

def get(url,timeout=30):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 equity-perp-research/1.0','Accept':'application/json'})
    last=None
    for i in range(3):
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

def mk(ex,c,u='',n='',a='',q='',st='',lev='',reg='',src='',method='',conf=''):
    return dict(exchange=ex,contract_ticker=clean(c),underlying_ticker=clean(u),name=clean(n),asset_type=clean(a),quote_currency=clean(q),status=clean(st),max_leverage=clean(lev),market_region=clean(reg),source_endpoint=clean(src),classification_method=clean(method),confidence=clean(conf),collected_at_utc=NOW)

def save(name,rows):
    rows=sorted(rows,key=lambda r:(r['underlying_ticker'],r['contract_ticker']))
    with (OUT/f'{name}.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader(); w.writerows(rows)

def run(name,fn):
    try:
        rows=fn(); meta[name]={'rows':len(rows),'types':dict(Counter(r['asset_type'] for r in rows))}; save(name,rows); return rows
    except Exception as e:
        errors[name]=repr(e); meta[name]={'rows':0,'error':repr(e)}; save(name,[]); return []

# High-confidence taxonomies first.
def bybit():
    src='https://api.bybit.com/v5/market/instruments-info?category=linear'; out=[]; cur=''
    for _ in range(20):
        url=src+'&limit=1000'+(('&cursor='+urllib.parse.quote(cur)) if cur else '')
        o=get(url); rr=o.get('result',{})
        for x in rr.get('list',[]) or []:
            t=clean(x.get('symbolType')).lower()
            if t not in {'stock','etf'}: continue
            c=clean(x.get('symbol')); u=clean(x.get('underlyingTicker') or x.get('baseCoin') or base(c)); lf=x.get('leverageFilter') or {}
            out.append(mk('Bybit',c,u,x.get('fullName') or x.get('displayName') or '',t,x.get('quoteCoin'),x.get('status'),lf.get('maxLeverage'),x.get('marketRegion'),src,'symbolType=stock|etf','high'))
        cur=clean(rr.get('nextPageCursor'))
        if not cur: break
    return out

def okx():
    src='https://www.okx.com/api/v5/public/instruments?instType=SWAP'; o=get(src); out=[]
    for x in o.get('data',[]) or []:
        if clean(x.get('instCategory'))!='3':continue
        c=clean(x.get('instId')); u=clean(x.get('uly') or x.get('instFamily') or base(c)).split('-')[0]
        out.append(mk('OKX',c,u,'','equity',x.get('settleCcy') or x.get('quoteCcy'),x.get('state'),'','',src,'instCategory=3','high'))
    return out

def kucoin():
    src='https://api-futures.kucoin.com/api/v1/contracts/active'; o=get(src); out=[]
    for x in o.get('data',[]) or []:
        if clean(x.get('marketType')).upper()!='NASDAQ':continue
        c=clean(x.get('symbol')); u=clean(x.get('displayBaseCurrency') or x.get('baseCurrency') or base(c))
        out.append(mk('KuCoin',c,u,'','equity',x.get('quoteCurrency') or x.get('settleCurrency'),'active','','US',src,'marketType=NASDAQ','high'))
    return out

def coinbase():
    src='https://api.international.coinbase.com/api/v1/instruments'; o=get(src)
    arr=o if isinstance(o,list) else (o.get('instruments') or o.get('data') or o.get('results') or []); out=[]
    for x in arr:
        t=clean(x.get('underlying_type') or x.get('underlyingType')).lower()
        if t not in {'equity','etf'}:continue
        c=clean(x.get('instrument_id') or x.get('instrumentId') or x.get('symbol')); u=clean(x.get('underlying') or x.get('base_asset') or x.get('baseAsset') or base(c))
        out.append(mk('Coinbase International',c,u,x.get('display_name') or x.get('name') or '',t,x.get('quote_asset') or x.get('quoteAsset'),x.get('trading_state') or x.get('status'),x.get('max_leverage'),'','https://api.international.coinbase.com/api/v1/instruments','underlying_type=equity|etf','high'))
    return out

BB=run('bybit',bybit); OK=run('okx',okx); KU=run('kucoin',kucoin); CB=run('coinbase_intx',coinbase)
# Kraken official xStocks perp seed.
KS=[('PF_AAPLXUSD','AAPL','Apple Inc.','equity'),('PF_CRCLXUSD','CRCL','Circle Internet Group, Inc.','equity'),('PF_GLDXUSD','GLD','SPDR Gold Shares','commodity_etf'),('PF_GOOGLXUSD','GOOGL','Alphabet Inc.','equity'),('PF_HOODXUSD','HOOD','Robinhood Markets, Inc.','equity'),('PF_MSTRXUSD','MSTR','Strategy Inc.','equity'),('PF_NVDAXUSD','NVDA','NVIDIA Corporation','equity'),('PF_QQQXUSD','QQQ','Invesco QQQ Trust','etf'),('PF_SPYXUSD','SPY','SPDR S&P 500 ETF Trust','etf'),('PF_TSLAXUSD','TSLA','Tesla, Inc.','equity')]
KR=[mk('Kraken',c,u,n,a,'USD','active','20','US','https://support.kraken.com/articles/xstocks-perpetual-futures','official xStocks list seed','high') for c,u,n,a in KS]; save('kraken',KR); meta['kraken']={'rows':len(KR),'types':dict(Counter(r['asset_type'] for r in KR))}

names={}; known=set()
for r in BB+OK+KU+CB+KR:
    if r['asset_type']=='commodity_etf':continue
    for k in [r['underlying_ticker'],base(r['contract_ticker'])]:
        k=norm(k)
        if k: known.add(k); names.setdefault(k,r['name'])

def bitget():
    src='https://api.bitget.com/api/v2/mix/market/contracts?productType=usdt-futures'; o=get(src); out=[]
    for x in o.get('data',[]) or []:
        if clean(x.get('isRwa')).upper()!='YES':continue
        c=clean(x.get('symbol')); u=clean(x.get('baseCoin') or base(c)); nu=norm(u)
        if nu in COM: t,co='commodity','high'
        elif nu in PRIVATE:t,co='private_proxy','medium'
        else:t,co='equity_or_etf','medium'
        out.append(mk('Bitget',c,u,names.get(nu,''),t,x.get('quoteCoin'),x.get('symbolStatus') or x.get('status'),x.get('maxLever') or x.get('maxLeverage'),'','https://api.bitget.com/api/v2/mix/market/contracts?productType=usdt-futures','isRwa=YES; known non-equity exclusions',co))
    return out
BG=run('bitget',bitget)
for r in BG:
    if r['asset_type'] not in {'commodity','private_proxy'}:
        known.add(norm(r['underlying_ticker']))

def binance():
    hosts=['https://fapi.binance.com','https://fapi1.binance.com','https://fapi2.binance.com','https://fapi3.binance.com']; o=None; src=''
    last=None
    for h in hosts:
        try:o=get(h+'/fapi/v1/exchangeInfo');src=h+'/fapi/v1/exchangeInfo';break
        except Exception as e:last=e
    if o is None:raise last or RuntimeError('no Binance endpoint')
    out=[]
    for x in o.get('symbols',[]) or []:
        if clean(x.get('contractType'))!='TRADIFI_PERPETUAL':continue
        c=clean(x.get('symbol'));u=clean(x.get('baseAsset') or base(c));nu=norm(u)
        if nu in COM:t,co='commodity','high'
        elif nu in PRIVATE:t,co='private_proxy','medium'
        else:t,co='equity_or_etf','medium'
        out.append(mk('Binance',c,u,names.get(nu,''),t,x.get('quoteAsset'),x.get('status'),'','',src,'contractType=TRADIFI_PERPETUAL; non-equity exclusions',co))
    return out
BN=run('binance',binance)
for r in BN:
    if r['asset_type'] not in {'commodity','private_proxy'}: known.add(norm(r['underlying_ticker']))

def gate():
    fs='https://api.gateio.ws/api/v4/futures/usdt/contracts'; ss='https://api.gateio.ws/api/v4/stock/symbols'; sm={}
    for ex in ['us','hk','kr']:
        for page in range(1,20):
            u=ss+'?'+urllib.parse.urlencode({'exchange':ex,'page':page,'page_size':500}); o=get(u)
            arr=o if isinstance(o,list) else (o.get('data') or o.get('list') or o.get('result') or [])
            if isinstance(arr,dict):arr=arr.get('list') or arr.get('items') or []
            if not arr:break
            for s in arr:
                k=norm(s.get('symbol'))
                if k:sm[k]=(clean(s.get('symbol_desc') or s.get('name')),clean(s.get('category')),ex.upper())
            if len(arr)<500:break
    o=get(fs); arr=o if isinstance(o,list) else (o.get('data') or o.get('result') or []); out=[]
    for x in arr:
        c=clean(x.get('name') or x.get('symbol')); u=clean(x.get('underlying') or base(c)); nu=norm(u); direct=sm.get(nu)
        if direct:
            n,cat,reg=direct; t='etf' if cat.upper() in {'ETF','ETN','ETV','ETS','FUND'} else 'equity'; method='matched Gate /stock/symbols';co='high'
        elif nu in known and nu not in COM and nu not in PRIVATE:
            n,reg,t,method,co=names.get(nu,''),'','equity_or_etf','cross-exchange underlying match','medium'
        else:continue
        out.append(mk('Gate',c,u,n,t,'USDT','active' if x.get('in_delisting') is False else x.get('status'),x.get('leverage_max'),reg,fs+' + '+ss,method,co))
    meta['gate_stock_symbol_universe']=len(sm)
    return out
GT=run('gate',gate)

def mexc():
    src='https://api.mexc.com/api/v1/contract/detail';o=get(src);out=[];plates=Counter()
    for x in o.get('data',[]) or []:
        ps=x.get('conceptPlate') or []; txt=' '.join(map(str,ps)) if isinstance(ps,list) else clean(ps)
        for p in ps if isinstance(ps,list) else [txt]:plates[clean(p)]+=1
        c=clean(x.get('symbol'));u=clean(x.get('baseCoin') or base(c));nu=norm(u); explicit=bool(re.search(r'stock|equity|tradfi|rwa|nasdaq|nyse|hkex|korea',txt,re.I));matched=nu in known and nu not in COM and nu not in PRIVATE
        if not explicit and not matched:continue
        out.append(mk('MEXC',c,u,x.get('displayNameEn') or x.get('displayName') or names.get(nu,''),'equity_or_etf',x.get('quoteCoin'),'active',x.get('maxLeverage') or x.get('maxLeverageLong'),'','https://api.mexc.com/api/v1/contract/detail','conceptPlate tag' if explicit else 'cross-exchange underlying match','high' if explicit else 'medium'))
    meta['mexc_concept_plates']=plates.most_common(30)
    return out
MX=run('mexc',mexc)

def bingx():
    src='https://open-api.bingx.com/openApi/swap/v2/quote/contracts';o=get(src);d=o.get('data',[]) if isinstance(o,dict) else []
    if isinstance(d,dict):d=d.get('contracts') or d.get('list') or []
    out=[]
    for x in d:
        c=clean(x.get('symbol') or x.get('contractId'));u=clean(x.get('asset') or x.get('baseCoin') or x.get('baseCurrency') or base(c));nu=norm(u);blob=json.dumps(x,ensure_ascii=False);explicit=bool(re.search(r'stock|equity|tradfi|rwa|nasdaq|nyse|hkex',blob,re.I));matched=nu in known and nu not in COM and nu not in PRIVATE
        if not explicit and not matched:continue
        out.append(mk('BingX',c,u,names.get(nu,''),'equity_or_etf',x.get('quoteCoin') or x.get('quoteCurrency') or 'USDT',x.get('status'),x.get('maxLongLeverage') or x.get('maxLeverage'),'','https://open-api.bingx.com/openApi/swap/v2/quote/contracts','API metadata marker' if explicit else 'cross-exchange underlying match','high' if explicit else 'medium'))
    return out
BX=run('bingx',bingx)

allraw=BB+OK+KU+CB+KR+BG+BN+GT+MX+BX
# fill missing names from names union
for r in allraw:
    if not r['name']: r['name']=names.get(norm(r['underlying_ticker']),'')
eq=[r for r in allraw if r['asset_type'] in {'equity','stock','etf','equity_or_etf'}]
d={}
for r in eq:
    k=(r['exchange'],r['contract_ticker']); old=d.get(k)
    if old is None or ({'high':2,'medium':1}.get(r['confidence'],0),bool(r['name']))>({'high':2,'medium':1}.get(old['confidence'],0),bool(old['name'])): d[k]=r
eq=list(d.values()); save('all_equity_perps',eq); save('all_tradfi_raw',allraw)
manifest={'generated_at_utc':NOW,'equity_rows':len(eq),'equity_by_exchange':dict(Counter(r['exchange'] for r in eq)),'exchange_collection':meta,'errors':errors,'methodology':'Official public APIs where explicit taxonomy exists; RWA/TradFi non-equities excluded; Gate/MEXC/BingX may use cross-exchange underlying matching; Kraken uses official xStocks perp list seed.'}
(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
with zipfile.ZipFile(ROOT/'equity_perps_snapshot.zip','w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(OUT.iterdir()): z.write(p,arcname=p.name)
print(json.dumps(manifest,ensure_ascii=False,indent=2))
