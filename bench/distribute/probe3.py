import atomic, run as base, json
from pathlib import Path
from openai import OpenAI
exec(open('probe2.py').read().split("variants=")[0])   # same inputs as probe2
d=json.load(open('probe_trace.json'))
client=OpenAI(); ledger=[]; out={}
for arm,nudge in (('trace+expect',trace+expect),('walkthrough',walk)):
    why=next(r['why'] for r in d['res'][arm] if r.get('verdict')=='bug')
    prompt=atomic.WORKER_FIX.format(tests=ttext,path='proofdeck/schemata.py',func=func,why=why,first=s,last=e)
    prompt=prompt.replace('Replace the wrong line or lines.', nudge+'Replace the wrong line or lines.')
    out[arm]=[]
    for i in range(3):
        r=base.parse(base.call(client,'gpt-4o-mini',[{'role':'user','content':prompt}],ledger,'worker'))
        right = r.get('start')==65 and 'b + delta' in str(r.get('new',''))
        out[arm].append(r); print(arm,i,'lines',r.get('start'),r.get('end'),'correct' if right else '', '|',repr(r.get('new'))[:110])
d['fix']=out; d['tokens']+=sum(c['total'] for c in ledger); d['ledger']+=ledger
json.dump(d,open('probe_trace.json','w'),indent=1)
print('probe total tokens',d['tokens'])
