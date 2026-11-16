"""Version-isolated canary assignments and cautious vote-based assessment."""
import hashlib
import json
import time
from .store import json_text


class Experiments:
    def __init__(self,store,clock=time.time):self.store,self.clock=store,clock

    @staticmethod
    def _check_proposal_evidence(db,evidence):
        # Older/manual proposals may not have a recorded snapshot; their
        # offline review remains a separate explicit requirement.
        for item in evidence.get('ratings_snapshot',[]):
            votes=db.execute('SELECT emoji FROM feedback WHERE message_id=? AND user_id=? AND active=1',(item['message_id'],item['user_id'])).fetchall()
            if len(votes)!=1 or votes[0]['emoji']!=item['emojis']:raise ValueError('Proposal feedback has changed')
            for mid,version in item.get('context_versions',{}).items():
                source=db.execute('SELECT changed_at,deleted FROM messages WHERE message_id=?',(mid,)).fetchone()
                if not source or source['deleted'] or source['changed_at']!=version:raise ValueError('Proposal context has changed')

    def start(self,candidate_id,evaluation,*,canary_percent=10):
        if not 1<=canary_percent<=25:raise ValueError('Canary must be 1–25 percent')
        if not isinstance(evaluation,dict) or evaluation.get('human_reviewed') is not True or evaluation.get('scenario_count',0)<8:
            raise ValueError('At least eight human-reviewed offline cases required')
        required={'roast','targeting','boundaries','repetition'}
        if not required.issubset(evaluation.get('passed_categories',[])):raise ValueError('Offline regression categories incomplete')
        with self.store.transaction() as db:
            candidate=db.execute("SELECT id,evidence FROM prompt_versions WHERE id=? AND status='proposed'",(candidate_id,)).fetchone()
            if not candidate:raise ValueError('Proposed guidance version required')
            incumbent=db.execute("SELECT id FROM prompt_versions WHERE status='active' LIMIT 1").fetchone()
            evidence=json.loads(candidate['evidence'])
            self._check_proposal_evidence(db,evidence)
            if 'base_version' in evidence and evidence['base_version']!=(incumbent['id'] if incumbent else None):raise ValueError('Proposal was generated from a different active guidance version')
            return db.execute("INSERT INTO prompt_experiments(candidate_id,incumbent_id,state,created_at,canary_percent,evaluation) VALUES(?,?,'running',?,?,?)",(candidate_id,incumbent['id'] if incumbent else None,self.clock(),canary_percent,json_text(evaluation))).lastrowid

    def assign(self,queue_id):
        with self.store.transaction() as db:
            existing=db.execute('SELECT * FROM prompt_assignments WHERE queue_id=?',(queue_id,)).fetchone()
            if existing:return dict(existing)
            experiment=db.execute("SELECT * FROM prompt_experiments WHERE state='running' LIMIT 1").fetchone()
            if not experiment:return None
            roll=int.from_bytes(hashlib.sha256(f"{experiment['id']}:{queue_id}".encode()).digest()[:8],'big')%100
            variant='candidate' if roll<experiment['canary_percent'] else 'control'
            version=experiment['candidate_id'] if variant=='candidate' else experiment['incumbent_id']
            assigned_at=self.clock()
            db.execute('INSERT INTO prompt_assignments(queue_id,experiment_id,variant,prompt_version,assigned_at) VALUES(?,?,?,?,?)',(queue_id,experiment['id'],variant,version,assigned_at))
            return {'queue_id':queue_id,'experiment_id':experiment['id'],'variant':variant,'prompt_version':version,'assigned_at':assigned_at}

    def results(self,experiment_id,positive='👍',negative='👎'):
        with self.store.lock:
            rows=self.store.db.execute('''SELECT a.variant,f.message_id,f.user_id,group_concat(f.emoji) AS emoji,count(*) AS n,max(f.updated_at) AS last_vote
                FROM prompt_assignments a JOIN queue q ON q.id=a.queue_id
                JOIN feedback f ON f.message_id=q.response_message_id AND f.active=1
                WHERE a.experiment_id=? GROUP BY a.variant,f.message_id,f.user_id HAVING n=1 ORDER BY last_vote DESC''',(experiment_id,)).fetchall()
        counts={v:{'positive':0,'negative':0,'raters':set()} for v in ('candidate','control')}
        per_user={};per_message={}
        for r in rows:
            if r['emoji'] not in (positive,negative):continue
            u=(r['variant'],r['user_id']);m=(r['variant'],r['message_id'])
            if per_user.get(u,0)>=4 or per_message.get(m,0)>=3:continue
            per_user[u]=per_user.get(u,0)+1;per_message[m]=per_message.get(m,0)+1
            data=counts[r['variant']];data['positive' if r['emoji']==positive else 'negative']+=1;data['raters'].add(r['user_id'])
        return {v:{'positive':x['positive'],'negative':x['negative'],'distinct_raters':len(x['raters'])} for v,x in counts.items()}

    def finish(self,experiment_id,*,promote=False,positive='👍',negative='👎'):
        # Hold the store lock from evidence collection through state transition,
        # so concurrent vote removal cannot promote based on stale data.
        with self.store.transaction() as db:
            return self._finish_locked(experiment_id,db=db,promote=promote,positive=positive,negative=negative)

    def _finish_locked(self,experiment_id,*,db,promote=False,positive='👍',negative='👎'):
        result=self.results(experiment_id,positive,negative)
        if promote:
            for data in result.values():
                if data['positive']+data['negative']<20 or data['distinct_raters']<5:raise ValueError('Insufficient diverse canary votes')
            candidate=result['candidate'];control=result['control']
            if candidate['positive']/(candidate['positive']+candidate['negative']) < control['positive']/(control['positive']+control['negative']):
                raise ValueError('Candidate vote rate is below control')
        experiment=db.execute("SELECT * FROM prompt_experiments WHERE id=? AND state='running'",(experiment_id,)).fetchone()
        if not experiment:raise ValueError('Experiment not running')
        if promote:
            candidate_row=db.execute('SELECT evidence FROM prompt_versions WHERE id=?',(experiment['candidate_id'],)).fetchone()
            self._check_proposal_evidence(db,json.loads(candidate_row['evidence']))
        # Do not silently overwrite a manually changed incumbent during the experiment.
        current=db.execute("SELECT id FROM prompt_versions WHERE status='active' LIMIT 1").fetchone()
        if (current['id'] if current else None)!=experiment['incumbent_id']:raise ValueError('Incumbent changed during experiment')
        if promote:
            db.execute("UPDATE prompt_versions SET status='archived' WHERE status='active'")
            db.execute("UPDATE prompt_versions SET status='active' WHERE id=?",(experiment['candidate_id'],))
        db.execute('UPDATE prompt_experiments SET state=?,finished_at=?,outcome=? WHERE id=?',('promoted' if promote else 'rejected',self.clock(),json_text(result),experiment_id))
        return result

    def assess(self,experiment_id,*,auto_promote=False,positive='👍',negative='👎'):
        with self.store.lock:
            return self._assess_locked(experiment_id,auto_promote=auto_promote,positive=positive,negative=negative)

    def _assess_locked(self,experiment_id,*,auto_promote=False,positive='👍',negative='👎'):
        row=self.store.db.execute("SELECT p.evidence FROM prompt_experiments e JOIN prompt_versions p ON p.id=e.candidate_id WHERE e.id=? AND e.state='running'",(experiment_id,)).fetchone()
        if row:
            try:self._check_proposal_evidence(self.store.db,json.loads(row['evidence']))
            except ValueError:
                self.finish(experiment_id,promote=False,positive=positive,negative=negative)
                return 'rejected_stale_evidence'
        result=self.results(experiment_id,positive,negative)
        candidate,control=result['candidate'],result['control']
        cn=candidate['positive']+candidate['negative'];bn=control['positive']+control['negative']
        # Early rollback requires multiple raters and a large observed gap. The
        # incumbent remains active throughout a canary, so rejection restores it.
        if cn>=10 and bn>=10 and candidate['distinct_raters']>=3 and control['distinct_raters']>=3:
            if candidate['positive']/cn+0.25 < control['positive']/bn:
                self.finish(experiment_id,promote=False,positive=positive,negative=negative)
                return 'rejected_regression'
        if auto_promote and cn>=20 and bn>=20 and candidate['distinct_raters']>=5 and control['distinct_raters']>=5:
            try:self.finish(experiment_id,promote=True,positive=positive,negative=negative);return 'promoted'
            except ValueError as exc:
                if str(exc)=='Candidate vote rate is below control':
                    self.finish(experiment_id,promote=False,positive=positive,negative=negative)
                    return 'rejected_inferior'
                raise
        return 'insufficient_evidence'
