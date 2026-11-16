"""Generate one validated reply or an explicit decision to stay silent."""
import json
import re
from .adaptation import Adaptation
from .json_output import parse_object

MAX_REPLY_CHARS=200


def shorten_reply(content,limit=MAX_REPLY_CHARS):
    """Keep a complete sentence when practical; otherwise cut at a word boundary."""
    if len(content)<=limit:return content
    prefix=content[:limit]
    boundaries=list(re.finditer(r'[.!?](?=\s|$)',prefix))
    if boundaries and boundaries[-1].end()>=limit//2:
        return prefix[:boundaries[-1].end()].rstrip()
    prefix=content[:limit-1]
    cut=prefix.rfind(' ')
    if cut>=limit//2:prefix=prefix[:cut]
    return prefix.rstrip(' ,;:—-')+'…'


def parse_decision(text):
    value=parse_object(text)
    if not isinstance(value.get('decision'),str) or value['decision'] not in {'reply','silence'} or not isinstance(value.get('text'),str):raise ValueError('Invalid decision schema')
    content=value['text'].strip()
    if value['decision']=='reply' and not content:raise ValueError('Empty reply')
    if value['decision']=='silence' and content:raise ValueError('Silence has text')
    # Never let generated text trigger mass or role mentions.
    content=content.replace('@everyone','@ everyone').replace('@here','@ here')
    content=re.sub(r'<@&\d+>','@role',content)
    return value['decision'],shorten_reply(content)


_CURRENT=object()


class Responder:
    def __init__(self,gateway,adaptation):self.gateway,self.adaptation=gateway,adaptation

    async def decide(self,context,*,kind='direct',queue_id=None,prompt_version=_CURRENT):
        active=self.adaptation.active() if prompt_version is _CURRENT else self.adaptation.by_id(prompt_version);messages=[{'role':'system','content':Adaptation.core()}]
        data={'learned_guidance':active['text'] if active else '', 'conversation':context}
        messages.append({'role':'user','content':json.dumps(data,ensure_ascii=False)})
        for attempt in range(2 if kind=='direct' else 1):
            raw,request=await self.gateway.complete(kind=kind,messages=messages,queue_id=queue_id,prompt_version=active['id'] if active else None)
            try:
                decision,text=parse_decision(raw)
                return {'decision':decision,'text':text,'request_id':request}
            except ValueError:
                if attempt or kind!='direct':raise
                # One budgeted retry without echoing malformed output into context.
                messages.append({'role':'user','content':'Return only a valid JSON decision object as specified.'})
        raise ValueError('Invalid decision')

    async def decide_group(self,contexts):
        if not 2<=len(contexts)<=3:raise ValueError('Expected two or three requests')
        active=self.adaptation.active()
        instruction=(Adaptation.core()+'\nFor this batch, output exactly one JSON object: '
            '{"responses":[{"queue_id":"...","decision":"reply|silence","text":"..."}]}. '
            'Handle each listed request separately, using its queue_id. Do not combine people or responses.')
        data={'learned_guidance':active['text'] if active else '', 'requests':contexts}
        raw,request=await self.gateway.complete(kind='direct',messages=[{'role':'system','content':instruction},{'role':'user','content':json.dumps(data,ensure_ascii=False)}],queue_id=int(contexts[0]['queue_id']),queue_ids=[int(c['queue_id']) for c in contexts],prompt_version=active['id'] if active else None)
        try:return parse_group(raw,[c['queue_id'] for c in contexts]),request
        except ValueError as exc:raise GroupFormatError(str(exc),request) from exc


class GroupFormatError(ValueError):
    def __init__(self,message,request_id):
        super().__init__(message);self.request_id=request_id


def parse_group(text,expected_ids):
    data=parse_object(text);rows=data.get('responses')
    if not isinstance(rows,list):raise ValueError('Missing responses list')
    expected={str(x) for x in expected_ids};result={}
    for row in rows:
        if not isinstance(row,dict) or 'queue_id' not in row:raise ValueError('Invalid group item')
        key=str(row['queue_id'])
        if key not in expected or key in result:raise ValueError('Unexpected or duplicate queue ID')
        decision,content=parse_decision(json.dumps({'decision':row.get('decision'),'text':row.get('text')}))
        result[key]={'decision':decision,'text':content}
    return result
