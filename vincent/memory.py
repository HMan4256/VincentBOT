"""Scoped, evidence-linked memory proposals and retrieval."""
import json
import logging
import re
import time
import unicodedata
from difflib import SequenceMatcher
from .store import json_text


_STOP = set('the and for with this that you your what how are vin vincent can not but was have they from like likes about know remember tell me my our their just really been were will would could should does did has had its its a an to of is it i im on in at do so we he she them be was again'.split())
_TOPICS = (
    {'game','games','gaming','gamer','videogame','videogames','steam','xbox','playstation','minecraft','roblox','chess'},
    {'music','song','songs','sing','singing','singer','band','album','artist','listen','listening'},
    {'cat','cats','kitten','kittens','feline'},
    {'dog','dogs','puppy','puppies','canine'},
    {'movie','movies','film','films','cinema'},
    {'age','old','birthday','born'},
)

def _terms(text):
    words = re.findall(r"[^\W_]+", unicodedata.normalize('NFKC', text or '').casefold())
    result=set()
    for word in words:
        if len(word)<3 or word in _STOP or word.isdigit():continue
        # A small stemmer handles common inflections without a model or dependency.
        for suffix in ('ing','ies','ed','es','s'):
            if word.endswith(suffix) and len(word)-len(suffix)>=4:
                word=word[:-len(suffix)]+('y' if suffix=='ies' else '')
                break
        result.add(word)
    return result

def _relevance(query_words, content):
    words=_terms(content)
    exact=query_words & words
    score=3*len(exact)
    # Tolerate typos only for longer words, avoiding broad short-word matches.
    for q in query_words-exact:
        if len(q)>=5 and any(len(w)>=5 and SequenceMatcher(None,q,w).ratio()>=.86 for w in words-exact):score+=1
    for topic in _TOPICS:
        stems=_terms(' '.join(topic))
        if query_words & stems and words & stems and not exact & stems:score+=1
    return score


class Memory:
    def __init__(self,store,clock=time.time):self.store,self.clock,self.semantic=store,clock,None

    def enable_semantic(self):
        from .semantic_memory import SemanticMemory
        self.semantic=SemanticMemory(self.store)

    def _semantic_scores(self,rows,query):
        if self.semantic is None:return {}
        try:return self.semantic.scores(rows,query)
        except Exception:
            logging.getLogger('vincent').exception('Semantic memory retrieval failed; using lexical fallback')
            return {}

    @staticmethod
    def _context_sources(db,sources,dependencies):
        all_sources=list(dict.fromkeys(str(s) for s in sources))
        for source in sources:
            for mid,version in (dependencies or {}).get(str(source),{}).items():
                if not db.execute('SELECT 1 FROM messages WHERE message_id=? AND changed_at=? AND deleted=0',(mid,version)).fetchone():return None
                if mid not in all_sources:all_sources.append(mid)
        return all_sources

    def _audit(self,db,action,*,memory_id=None,guild=None,user=None,details=None):
        db.execute('INSERT INTO memory_events(memory_id,guild_id,user_id,action,occurred_at,details) VALUES(?,?,?,?,?,?)',
                   (memory_id,str(guild) if guild is not None else None,str(user) if user is not None else None,action,self.clock(),json_text(details or {})))

    def generation(self,guild,user):
        with self.store.lock:
            row=self.store.db.execute('SELECT enabled,generation FROM memory_preferences WHERE guild_id=? AND user_id=?',(str(guild),str(user))).fetchone()
        return (bool(row['enabled']),row['generation']) if row else (True,0)

    def set_enabled(self,guild,user,enabled):
        with self.store.transaction() as db:
            db.execute('''INSERT INTO memory_preferences(guild_id,user_id,enabled,generation) VALUES(?,?,?,1)
                ON CONFLICT(guild_id,user_id) DO UPDATE SET enabled=excluded.enabled,generation=memory_preferences.generation+1''',(str(guild),str(user),int(enabled)))
            db.execute('DELETE FROM memory_candidates WHERE guild_id=? AND user_id=?',(str(guild),str(user)))
            self._audit(db,'collection_enabled' if enabled else 'collection_disabled',guild=guild,user=user)

    def set_auto(self,guild,user,enabled):
        with self.store.transaction() as db:
            db.execute("""INSERT INTO memory_preferences(guild_id,user_id,enabled,generation,auto_enabled) VALUES(?,?,1,1,?)
                ON CONFLICT(guild_id,user_id) DO UPDATE SET auto_enabled=excluded.auto_enabled,generation=memory_preferences.generation+1""",(str(guild),str(user),int(enabled)))
            db.execute('DELETE FROM memory_candidates WHERE guild_id=? AND user_id=?',(str(guild),str(user)))
            self._audit(db,'auto_enabled' if enabled else 'auto_disabled',guild=guild,user=user)

    def forget(self,guild,user):
        with self.store.transaction() as db:
            sources={r['message_id'] for r in db.execute('SELECT message_id FROM messages WHERE guild_id=? AND author_id=?',(str(guild),str(user))).fetchall()}
            for record in db.execute("SELECT id,source_ids FROM memories WHERE guild_id=? AND scope='server'",(str(guild),)).fetchall():
                if sources.intersection(json.loads(record['source_ids'])):db.execute('DELETE FROM memories WHERE id=?',(record['id'],))
            db.execute('DELETE FROM memories WHERE guild_id=? AND user_id=?',(str(guild),str(user)))
            db.execute('DELETE FROM user_profiles WHERE guild_id=? AND user_id=?',(str(guild),str(user)))
            db.execute('DELETE FROM profile_interactions WHERE guild_id=? AND user_id=?',(str(guild),str(user)))
            db.execute('DELETE FROM profile_outgoing WHERE guild_id=? AND user_id=?',(str(guild),str(user)))
            self._audit(db,'profile_forgotten',guild=guild,user=user)
            db.execute('DELETE FROM memory_candidates WHERE guild_id=? AND user_id=?',(str(guild),str(user)))
            db.execute('''INSERT INTO memory_preferences(guild_id,user_id,enabled,generation) VALUES(?,?,1,1)
                ON CONFLICT(guild_id,user_id) DO UPDATE SET generation=memory_preferences.generation+1''',(str(guild),str(user)))

    def change_record(self,guild,user,memory_id,*,replacement=None,source_id=None):
        guild,user=str(guild),str(user)
        if replacement is not None and (not isinstance(replacement,str) or not 3<=len(replacement.strip())<=300):raise ValueError('Memory must contain 3–300 characters')
        with self.store.transaction() as db:
            row=db.execute("SELECT id FROM memories WHERE id=? AND guild_id=? AND user_id=? AND scope='user' AND status='active'",(memory_id,guild,user)).fetchone()
            if not row:return False
            if replacement is None:
                db.execute('DELETE FROM memories WHERE id=?',(memory_id,))
            else:
                source=db.execute('SELECT author_id,guild_id,deleted FROM messages WHERE message_id=?',(str(source_id),)).fetchone()
                if not source or source['author_id']!=user or source['guild_id']!=guild or source['deleted']:raise ValueError('Correction source unavailable')
                db.execute("UPDATE memories SET content=?,kind='fact',source_ids=?,source_type='self_report',confidence=1.0,updated_at=? WHERE id=?",(replacement.strip(),json_text([str(source_id)]),self.clock(),memory_id))
            self._audit(db,'corrected' if replacement is not None else 'deleted',memory_id=memory_id,guild=guild,user=user)
            db.execute('DELETE FROM memory_candidates WHERE guild_id=? AND user_id=?',(guild,user))
            db.execute("INSERT INTO memory_preferences(guild_id,user_id,enabled,generation) VALUES(?,?,1,1) ON CONFLICT(guild_id,user_id) DO UPDATE SET generation=memory_preferences.generation+1",(guild,user))
            return True

    def apply_user_proposals(self,guild,user,generation,proposals,expected_versions=None,context_dependencies=None):
        """Require that all cited source messages are authored by this user in this guild."""
        now=self.clock();guild=str(guild);user=str(user);added=0
        with self.store.transaction() as db:
            pref=db.execute('SELECT enabled,generation FROM memory_preferences WHERE guild_id=? AND user_id=?',(guild,user)).fetchone()
            if pref and (not pref['enabled'] or pref['generation']!=generation):return 0
            if not pref and generation!=0:return 0
            for p in proposals:
                content=p.get('content');kind=p.get('kind');sources=p.get('source_ids');confidence=p.get('confidence',0.5)
                if not isinstance(content,str) or not 3<=len(content.strip())<=300 or kind not in {'fact','interest','preference','relationship','age'} or not isinstance(sources,list) or not sources or not isinstance(confidence,(float,int)) or isinstance(confidence,bool) or not 0<=confidence<=1:continue
                if kind=='age' and p.get('source_type')!='self_report':continue
                if p.get('source_type') not in {'self_report','inference'}:continue
                rows=[db.execute('SELECT author_id,guild_id,deleted,changed_at,created_at,content FROM messages WHERE message_id=?',(str(s),)).fetchone() for s in sources]
                if any(not r or r['author_id']!=user or r['guild_id']!=guild or r['deleted'] for r in rows):continue
                if expected_versions is not None and any(str(source) not in expected_versions or r['changed_at']!=expected_versions[str(source)] for source,r in zip(sources,rows)):continue
                all_sources=self._context_sources(db,sources,context_dependencies)
                if all_sources is None:continue
                # Personal claims require a first-person assertion. Interaction
                # patterns use the separate, lower-confidence relationship kind.
                # This is conservative and does not prove semantic correctness.
                if kind in {'fact','interest','preference','age'}:
                    if p['source_type']!='self_report':continue
                    # Include a small set of first-person forms observed in
                    # Arcadia's English, Hindi/Urdu transliteration and Czech.
                    # This is only a filter, not a semantic or language model.
                    direct=re.compile(r"\b(?:i|i'm|im|i’ve|i've|main|mai|mujhe|mujhko|já|jsem|mám|moje|můj|my\s+(?:favo(?:u)?rite|hobby|age|name|birthday|job|project|pronouns|pet|cat|dog))\b",re.I)
                    if not any(direct.search(r['content'] or '') or (r['content'] or '').lower().startswith('!vin memory add ') for r in rows):continue
                if kind=='age':
                    asserted=set()
                    for row in rows:
                        asserted.update(re.findall(r"\b(?:i\s*(?:am|'m)|im|my age is)\s+(\d{1,3})\b",row['content'] or '',re.I))
                    claimed=set(re.findall(r'\b\d{1,3}\b',content))
                    if not asserted.intersection(claimed):continue
                if kind=='relationship' and (p['source_type']!='inference' or len(set(map(str,sources)))<2 or confidence>.6):continue
                observed=max(r['created_at'] for r in rows)
                if kind=='age':
                    latest=db.execute("SELECT max(observed_at) FROM memories WHERE scope='user' AND guild_id=? AND user_id=? AND kind='age' AND status='active'",(guild,user)).fetchone()[0]
                    if latest is not None and observed<latest:continue
                if kind=='age':
                    previous=db.execute("SELECT id FROM memories WHERE scope='user' AND guild_id=? AND user_id=? AND kind='age' AND status='active' AND lower(content)!=lower(?)",(guild,user,content.strip())).fetchall()
                    db.execute("UPDATE memories SET status='superseded',updated_at=? WHERE scope='user' AND guild_id=? AND user_id=? AND kind='age' AND status='active' AND lower(content)!=lower(?)",(now,guild,user,content.strip()))
                    for item in previous:self._audit(db,'superseded',memory_id=item['id'],guild=guild,user=user)
                    old=db.execute("SELECT id,status FROM memories WHERE scope='user' AND guild_id=? AND user_id=? AND kind='age' AND lower(content)=lower(?) LIMIT 1",(guild,user,content.strip())).fetchone()
                    if old and old['status']!='active':
                        db.execute("UPDATE memories SET status='active',source_ids=?,source_type=?,confidence=?,updated_at=?,observed_at=? WHERE id=?",(json_text(all_sources),p['source_type'],confidence,now,observed,old['id']))
                        self._audit(db,'reactivated',memory_id=old['id'],guild=guild,user=user,details={'source_ids':all_sources});added+=1;continue
                exists=db.execute("SELECT 1 FROM memories WHERE scope='user' AND guild_id=? AND user_id=? AND kind=? AND lower(content)=lower(?) AND status='active'",(guild,user,kind,content.strip())).fetchone()
                if exists:continue
                cursor=db.execute('INSERT INTO memories(scope,guild_id,user_id,kind,content,source_ids,source_type,confidence,created_at,updated_at,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',('user',guild,user,kind,content.strip(),json_text(all_sources),p['source_type'],confidence,now,now,observed))
                self._audit(db,'created',memory_id=cursor.lastrowid,guild=guild,user=user,details={'kind':kind,'source_ids':all_sources});added+=1
        return added

    def apply_server_proposals(self,guild,proposals,expected_versions,expected_generations,context_dependencies=None):
        guild=str(guild);now=self.clock();added=0
        with self.store.transaction() as db:
            for p in proposals:
                content=p.get('content');sources=p.get('source_ids');kind=p.get('kind')
                if kind not in {'server_fact','shared_bit','server_event'} or not isinstance(content,str) or not 3<=len(content.strip())<=300 or not isinstance(sources,list) or not sources:continue
                rows=[db.execute('SELECT author_id,guild_id,changed_at,deleted FROM messages WHERE message_id=?',(str(source),)).fetchone() for source in sources]
                valid=True
                for source,row in zip(sources,rows):
                    if not row or row['deleted'] or row['guild_id']!=guild or expected_versions.get(str(source))!=row['changed_at']:
                        valid=False;break
                    pref=db.execute('SELECT enabled,generation FROM memory_preferences WHERE guild_id=? AND user_id=?',(guild,row['author_id'])).fetchone()
                    current=(bool(pref['enabled']),pref['generation']) if pref else (True,0)
                    if current!=(True,expected_generations.get(str(source))):valid=False;break
                if not valid:continue
                all_sources=self._context_sources(db,sources,context_dependencies)
                if all_sources is None:continue
                # An automatically inferred shared fact must be supported by
                # at least two distinct participants, not one person's profile.
                if len({r['author_id'] for r in rows})<2:continue
                duplicate=db.execute("SELECT 1 FROM memories WHERE scope='server' AND guild_id=? AND kind=? AND lower(content)=lower(?) AND status='active'",(guild,kind,content.strip())).fetchone()
                if duplicate:continue
                cursor=db.execute('INSERT INTO memories(scope,guild_id,user_id,kind,content,source_ids,source_type,confidence,created_at,updated_at) VALUES(?,?,NULL,?,?,?,?,?,?,?)',('server',guild,kind,content.strip(),json_text(all_sources),'inference',.5,now,now))
                self._audit(db,'created',memory_id=cursor.lastrowid,guild=guild,details={'kind':kind,'source_ids':all_sources});added+=1
        return added

    def put_shared(self, *, scope, content, kind='fact', guild=None, source_ids=(), source_type='owner', confidence=1.0):
        if scope not in {'global','server'} or (scope == 'server' and guild is None):
            raise ValueError('Invalid shared memory scope')
        if not isinstance(content,str) or not 3<=len(content.strip())<=300:
            raise ValueError('Invalid memory content')
        if source_type not in {'owner','self_report','inference'} or not 0<=confidence<=1:
            raise ValueError('Invalid provenance')
        now=self.clock();guild=str(guild) if guild is not None else None
        with self.store.transaction() as db:
            row=db.execute("SELECT id FROM memories WHERE scope=? AND guild_id IS ? AND user_id IS NULL AND kind=? AND content=? AND status='active'",(scope,guild,kind,content.strip())).fetchone()
            if row:return row['id']
            cursor=db.execute('INSERT INTO memories(scope,guild_id,user_id,kind,content,source_ids,source_type,confidence,created_at,updated_at) VALUES(?,?,NULL,?,?,?,?,?,?,?)',
                (scope,guild,kind,content.strip(),json_text([str(x) for x in source_ids]),source_type,confidence,now,now))
            self._audit(db,'created_shared',memory_id=cursor.lastrowid,guild=guild,details={'scope':scope,'kind':kind,'source_ids':[str(x) for x in source_ids]})
            return cursor.lastrowid

    def remove_shared(self,memory_id,*,guild=None,allow_global=False):
        with self.store.transaction() as db:
            row=db.execute("SELECT scope,guild_id,kind FROM memories WHERE id=? AND user_id IS NULL",(memory_id,)).fetchone()
            if not row or row['kind']=='identity':return False
            if row['scope']=='global' and not allow_global:return False
            if row['scope']=='server' and row['guild_id']!=str(guild):return False
            db.execute('DELETE FROM memories WHERE id=?',(memory_id,))
            self._audit(db,'deleted_shared',memory_id=memory_id,guild=row['guild_id'],details={'scope':row['scope'],'kind':row['kind']})
            return True

    def shared(self,guild,limit=8,query=None):
        with self.store.lock:
            rows=self.store.db.execute("SELECT * FROM memories WHERE user_id IS NULL AND status='active' AND (scope='global' OR (scope='server' AND guild_id=?)) AND (expires_at IS NULL OR expires_at>?)",(str(guild),self.clock())).fetchall()
        if query is None:
            return [dict(row) for row in sorted(rows,key=lambda r:r['updated_at'],reverse=True)[:limit]]
        words=_terms(query)
        similarities=self._semantic_scores(rows,query)
        def score(r):return similarities.get(r['id'],0)+min(_relevance(words,r['content']),4)*.06
        ranked=sorted(rows,key=lambda r:(r['scope']=='global' and r['kind']=='identity',score(r),r['source_type']=='owner',r['confidence'],r['updated_at']),reverse=True)
        # Always carry identity; everything else must be relevant to this exchange.
        return [dict(r) for r in ranked if (r['scope']=='global' and r['kind']=='identity') or similarities.get(r['id'],0)>=.32 or _relevance(words,r['content'])>0][:limit]

    def search(self,guild,user,query,limit=5):
        if not self.generation(guild,user)[0]:return []
        with self.store.lock:
            rows=self.store.db.execute("SELECT * FROM memories WHERE scope='user' AND guild_id=? AND user_id=? AND status='active' AND (expires_at IS NULL OR expires_at>?)",(str(guild),str(user),self.clock())).fetchall()
        words=_terms(query)
        recall=bool(re.search(r"\b(?:remember|recall|know)\b.*\b(?:me|my|mine)\b|\bwhat do you know about me\b",query,re.I))
        similarities=self._semantic_scores(rows,query)
        scored=[(similarities.get(r['id'],0)+min(_relevance(words,r['content']),4)*.06,r) for r in rows]
        relevant=[(score,r) for score,r in scored if similarities.get(r['id'],0)>=.32 or _relevance(words,r['content'])>0 or r['kind']=='relationship' or (recall and r['source_type']=='self_report')]
        ranked=sorted(relevant,key=lambda pair:(pair[0],pair[1]['source_type']=='self_report',pair[1]['confidence'],pair[1]['updated_at']),reverse=True)
        selected=[];relationship=False
        for _,row in ranked:
            if row['kind']=='relationship':
                if relationship:continue
                relationship=True
            selected.append(dict(row))
            if len(selected)>=limit:break
        return selected
