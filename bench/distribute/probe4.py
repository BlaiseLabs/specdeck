import atomic, run as base, json
from openai import OpenAI
exec(open('probe2.py').read().split("variants=")[0])
values=('Values inside this function during the failing test, for orig = "    if qty > 100:" and mut = "    if qty >= 100:":\n'
        '  line 54: a0 = 12, b0 = 12      line 55: delta = 1      line 60: a = 7, b = 16 (the node `qty > 100`)\n'
        '  line 65: orig_expr = "qty > 100", mut_expr = "qty >= 1"\n'
        '  line 66: orig[:a] + mut_expr + orig[b:] = "    if qty >= 1:" != mut, so the node is skipped and line 70 returns None\n\n')
d=json.load(open('probe_trace.json'))
client=OpenAI(); ledger=[]; res={'values:verdict':[], 'values:fix':[]}
v=plain.replace(Q, expect+values+Q)
for i in range(3):
    r=base.parse(base.call(client,'gpt-4o-mini',[{'role':'user','content':v}],ledger,'worker'))
    res['values:verdict'].append(r); print('verdict',i,r.get('verdict'),'|',str(r.get('why'))[:160])
    if r.get('verdict')=='bug':
        fp=atomic.WORKER_FIX.format(tests=ttext,path='proofdeck/schemata.py',func=func,why=r.get('why'),first=s,last=e)
        fp=fp.replace('Replace the wrong line or lines.', expect+values+'Replace the wrong line or lines.')
        f=base.parse(base.call(client,'gpt-4o-mini',[{'role':'user','content':fp}],ledger,'worker'))
        ok=f.get('start')==65 and 'b + delta' in str(f.get('new',''))
        res['values:fix'].append(f); print('   fix lines',f.get('start'),f.get('end'),'CORRECT' if ok else '','|',repr(f.get('new'))[:100])
d['res'].update(res); d['tokens']+=sum(c['total'] for c in ledger); d['ledger']+=ledger
json.dump(d,open('probe_trace.json','w'),indent=1); print('probe total tokens',d['tokens'])
