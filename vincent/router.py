"""Local conversation routing; never calls a model per message."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    action: str
    score: float
    reason: str
    branch_id: str


class Router:
    def __init__(self,bot_id,names=('vincent','vin','vinnie'),followup_seconds=60):
        self.bot_id=str(bot_id);self.names=names;self.followup_seconds=followup_seconds

    def route(self,message,*,referenced=None,recent_bot=None,bot_ancestor=None,recent_channel_bot=None):
        text=message.get('content',''); mid=str(message['message_id']); parent=str(message.get('reply_to') or '')
        explicit=self.bot_id in {str(x) for x in message.get('mentions',[])} or text.lower().startswith('!vin ') or text.lower()=='!vin'
        if referenced and str(referenced.get('author_id'))==self.bot_id:
            return Route('direct',1.0,'reply_to_bot',parent)
        if explicit:return Route('direct',1.0,'mention_or_command',parent or mid)
        named=any(re.search(r'(?<!\w)'+re.escape(x)+r'(?!\w)',text,re.I) for x in self.names)
        if named:return Route('candidate',.72,'name_mentioned',parent or mid)
        # An explicit reply to another person is not a general follow-up to Vincent.
        if referenced and str(referenced.get('author_id'))!=self.bot_id:
            if bot_ancestor:return Route('candidate',.58,'active_bot_branch',str(bot_ancestor))
            return Route('ignore',0.0,'reply_to_other',parent)
        # Someone else can answer Vincent's latest prompt without a reply or
        # mention. Ambiguous answers require an uninterrupted exchange.
        if not parent and not message.get('mentions') and recent_channel_bot:
            gap=message['created_at']-recent_channel_bot['created_at']
            if 0<=gap<=30 and str(message['author_id'])!=str(recent_channel_bot.get('target_user_id')):
                previous=(recent_channel_bot.get('content') or '').casefold()
                current=text.casefold()
                actions={'admit','confess','apologize','apologise','explain','answer','prove','promise'}
                old_words=set(re.findall(r"[\w']+",previous));new_words=set(re.findall(r"[\w']+",current))
                addressed=bool(old_words & {'you','u','your','ur'})
                response=bool(new_words & {'i','im',"i'm",'me','ok','okay','yes','yeah','no','nah','fine','alright'})
                branch=str(recent_channel_bot.get('branch_id') or recent_channel_bot['message_id'])
                if addressed and response and old_words & new_words & actions:
                    return Route('direct',.9,'channel_prompt_answer',branch)
                short_answer=bool(re.match(r"^\s*(?:ok|okay|fine|yes|yeah|yep|no|nah|nope|sure|alright)\b",current)) and len(new_words)<=8
                if addressed and not recent_channel_bot.get('interrupted') and short_answer and (('?' in previous) or bool(old_words & actions)):
                    return Route('candidate',.65,'channel_question_answer',branch)
        if recent_bot:
            gap=message['created_at']-recent_bot['created_at']
            same_person=str(message['author_id'])==str(recent_bot.get('target_user_id'))
            if 0<=gap<=self.followup_seconds and same_person:
                words=re.findall(r"[\w']+",text.casefold())
                previous=recent_bot.get('content') or ''
                prev_words=set(re.findall(r"[\w']+",previous.casefold()))
                common={'the','and','you','your','that','this','what','with','have','just','like','bro','gng','vin','for','are','was','but','not','yes','no','im','i'}
                overlap={w for w in words if len(w)>3 and w not in common} & {w for w in prev_words if len(w)>3 and w not in common}
                reaction=bool(words) and len(words)<=3 and words[0] in {'yes','yeah','yep','nah','no','fair','unfortunately','fortunately','mhm','bruh','lol','lmao','lmfao','exactly','sure','what','why','how','true','false','correct','wrong','stop'}
                addressed=bool(re.search(r"\b(?:you|your|ur|u|you're|youre|yours|yourself)\b",text,re.I))
                continuing=bool(words) and words[0] in {'and','but','so','then','also','wait'}
                answer_to_question='?' in previous and len(words)<=12
                branch=str(recent_bot.get('branch_id') or recent_bot['message_id'])
                if not message.get('mentions') and (reaction or addressed or continuing or overlap or answer_to_question or '?' in text):
                    return Route('candidate',.78,'recent_participant_followup',branch)
                # A missing cue is not proof of a topic change. Let the model
                # decide rather than losing an implicit response.
                return Route('candidate',.45,'recent_participant_ambiguous',branch)
        return Route('ignore',0.0,'no_evidence',parent or mid)
