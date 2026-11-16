import tempfile
import unittest
from pathlib import Path

from vincent.queue import WorkQueue
from vincent.runtime_lock import RuntimeLock
from vincent.store import Store


class RestartRecoveryTests(unittest.TestCase):
    def test_observe_send_reconciliation_records_cooldown_atomically(self):
        store=Store(':memory:');queue=WorkQueue(store,clock=lambda:100)
        first=queue.enqueue(guild_id=1,channel_id=2,author_id=1,branch_id=1,message_id=1,priority=1,reason='observe')
        claim=queue.claim();queue.begin_send(first,'reply',lease_token=claim['lease_token'])
        self.assertTrue(queue.reconcile_sent(first,42,sent_at=95))
        self.assertEqual(store.db.execute('SELECT last_auto_sent FROM channel_activity WHERE channel_id=?',('2',)).fetchone()[0],95)
        second=queue.enqueue(guild_id=1,channel_id=2,author_id=2,branch_id=2,message_id=2,priority=1,reason='observe')
        claim=queue.claim();queue.begin_send(second,'reply',lease_token=claim['lease_token'])
        self.assertTrue(queue.reconcile_sent(second,43,sent_at=90))
        self.assertEqual(store.db.execute('SELECT last_auto_sent FROM channel_activity WHERE channel_id=?',('2',)).fetchone()[0],95)
        store.close()
    def test_uncertain_send_holds_branch_until_reconciled(self):
        store=Store(':memory:');queue=WorkQueue(store,clock=lambda:100)
        first=queue.enqueue(guild_id=1,channel_id=2,author_id=1,branch_id=7,message_id=1)
        second=queue.enqueue(guild_id=1,channel_id=2,author_id=2,branch_id=7,message_id=2)
        claim=queue.claim();queue.begin_send(first,'reply',lease_token=claim['lease_token'])
        self.assertIsNone(queue.claim())
        self.assertTrue(queue.reconcile_sent(first,42))
        self.assertEqual(queue.claim()['id'],second)
        store.close()

    def test_expired_uncertain_send_does_not_hold_branch_forever(self):
        store=Store(':memory:');clock=[100];queue=WorkQueue(store,clock=lambda:clock[0])
        first=queue.enqueue(guild_id=1,channel_id=2,author_id=1,branch_id=7,message_id=1,ttl=5)
        second=queue.enqueue(guild_id=1,channel_id=2,author_id=2,branch_id=7,message_id=2,ttl=100)
        claim=queue.claim();queue.begin_send(first,'reply',lease_token=claim['lease_token'])
        self.assertIsNone(queue.claim());clock[0]=106
        self.assertEqual(queue.claim()['id'],second)
        self.assertEqual(store.db.execute('SELECT status FROM queue WHERE id=?',(first,)).fetchone()[0],'uncertain')
        store.close()
    def test_backlog_prioritizes_direct_work_and_records_rejections(self):
        store=Store(':memory:');queue=WorkQueue(store,clock=lambda:100,max_backlog=2)
        def enqueue(mid,priority):
            return queue.enqueue(guild_id=1,channel_id=2,author_id=mid,branch_id=mid,message_id=mid,priority=priority)
        low=enqueue(1,1);direct=enqueue(2,20);replacement=enqueue(3,20);rejected=enqueue(4,20)
        rows={r['id']:r['status'] for r in store.db.execute('SELECT id,status FROM queue')}
        self.assertEqual(rows,{low:'expired',direct:'pending',replacement:'pending',rejected:'expired'})
        self.assertEqual(enqueue(4,20),rejected)
        self.assertEqual(store.db.execute("SELECT count(*) FROM queue_events WHERE action='backlog_displaced'").fetchone()[0],1)
        self.assertEqual(store.db.execute("SELECT count(*) FROM queue_events WHERE action='backlog_rejected'").fetchone()[0],1)
        store.close()

    def test_backlog_never_displaces_a_claimed_request(self):
        store=Store(':memory:');queue=WorkQueue(store,clock=lambda:100,max_backlog=1)
        low=queue.enqueue(guild_id=1,channel_id=2,author_id=1,branch_id=1,message_id=1,priority=1)
        queue.claim()
        direct=queue.enqueue(guild_id=1,channel_id=2,author_id=2,branch_id=2,message_id=2,priority=20)
        rows={r['id']:r['status'] for r in store.db.execute('SELECT id,status FROM queue')}
        self.assertEqual(rows,{low:'claimed',direct:'expired'})
        store.close()

    def test_only_one_runtime_may_recover_a_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db'
            first,second=RuntimeLock(path),RuntimeLock(path)
            first.acquire()
            with self.assertRaises(RuntimeError):second.acquire()
            first.release()
            second.acquire()
            second.release()

    def test_restart_reclaims_only_unsent_live_claims(self):
        store=Store(':memory:');now=[100.0];queue=WorkQueue(store,clock=lambda:now[0])
        def enqueue(mid):
            return queue.enqueue(guild_id=1,channel_id=2,author_id=mid,branch_id=mid,message_id=mid)
        live=enqueue(1);uncertain=enqueue(2);expired=enqueue(3)
        live_claim=queue.claim();send_claim=queue.claim();queue.begin_send(uncertain,'reply',lease_token=send_claim['lease_token'])
        queue.claim()
        with store.transaction() as db:db.execute('UPDATE queue SET expires_at=105 WHERE id=?',(expired,))
        now[0]=110
        self.assertEqual(queue.recover_after_restart(),2)
        rows={r['id']:r['status'] for r in store.db.execute('SELECT id,status FROM queue')}
        self.assertEqual(rows,{live:'pending',uncertain:'uncertain',expired:'expired'})
        self.assertFalse(queue.finish(live,'sent',lease_token=live_claim['lease_token']))
        self.assertEqual(queue.claim()['id'],live)
        store.close()


if __name__=='__main__':unittest.main()
