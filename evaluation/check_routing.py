"""Compare local routing against explicitly labeled development fixtures."""
import json
from collections import Counter
from pathlib import Path
from vincent.router import Router


def evaluate(cases):
    router=Router('9');counts=Counter();errors=[]
    for case in cases:
        route=router.route(case['message'],referenced=case.get('referenced'),recent_bot=case.get('recent_bot'),bot_ancestor=case.get('bot_ancestor'))
        counts[f"expected_{case['expected']}"]+=1
        if route.action==case['expected']:counts['matched']+=1
        else:errors.append({'id':case['id'],'expected':case['expected'],'actual':route.action,'reason':route.reason})
    return {'total':len(cases),'matched':counts['matched'],'counts':dict(counts),'errors':errors,'caveat':'Hand-written development fixtures, not a representative server accuracy measure.'}

if __name__=='__main__':
    cases=json.loads((Path(__file__).parent/'routing_cases.json').read_text())
    print(json.dumps(evaluate(cases),indent=2))
