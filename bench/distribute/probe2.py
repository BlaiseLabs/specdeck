import atomic, run as base, json
from pathlib import Path
from openai import OpenAI
t=Path('/tmp/why-uvl313hg/proofdeck')
out=open('/tmp/why_out.txt').read()
test='tests/test_fork_mode.py::test_switched_code_behaves_like_the_mutant_and_keeps_line_numbers'
ttext=f'--- {test} ---\n{base.test_source(t,test)}\n\nfailure output:\n{base.failure_of(out,test)}'
n,s,e=[x for x in atomic.functions(t,'proofdeck/schemata.py') if x[0]=='switch_line'][0]
func=atomic.numbered('\n'.join((t/'proofdeck/schemata.py').read_text().splitlines()[s-1:e]),s)
plain=atomic.WORKER_FN.format(tests=ttext,path='proofdeck/schemata.py',func=func)
Q='Is the cause of these failures in this function?'
trace=('What this function did during the failing test, for the line `if qty > 100:`:\n'
       '  switch_line(mutant: "    if qty > 100:" -> "    if qty >= 100:") returned None\n'
       '  switch_line(mutant: "    if qty > 100:" -> "    if qty > 101:") returned "    if qty > (101 if _proofdeck_m(6) else 100):"\n\n')
expect=('What the test needs: bulk_discount(100) == 15 holds only if the `qty >= 100` mutant is the one compiled in '
        'for that line, so switch_line must return a switched line for it, not None.\n\n')
walk=('Work it through: call switch_line with orig = "    if qty > 100:" and mut = "    if qty >= 100:" '
      '(operator "boundary"). Compute a0, b0, delta, then for the node `qty > 100` compute a, b, orig_expr and '
      'mut_expr, and check line 66. Does it return a switched line, as the test needs, or None?\n\n')
variants={'trace+expect': plain.replace(Q, trace+expect+Q), 'walkthrough': plain.replace(Q, walk+Q)}
client=OpenAI(); ledger=[]; res={}
for arm,prompt in variants.items():
    assert prompt!=plain
    res[arm]=[]
    for i in range(3):
        r=base.parse(base.call(client,'gpt-4o-mini',[{'role':'user','content':prompt}],ledger,'worker'))
        res[arm].append(r); print(arm,i,r.get('verdict'),'|',str(r.get('why'))[:220])
prev=json.load(open('probe_trace.json'))
prev['res'].update(res); prev['tokens']+=sum(c['total'] for c in ledger); prev['ledger']+=ledger
json.dump(prev,open('probe_trace.json','w'),indent=1)
print('tokens this run',sum(c['total'] for c in ledger),'probe total',prev['tokens'])
