"""Discord adapter for the new bot. Run only when explicitly configured."""
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
import re
import time
import uuid
import discord
import httpx
from discord.ext import tasks
from .adaptation import Adaptation
from .budget import Budget, BudgetExceeded
from .config import Settings
from .conversations import bot_ancestor
from .context import build_context, clear_channel
from .feedback import Feedback
from .flags import FeatureFlags
from .extraction import Extractor
from .experiments import Experiments
from .llm import Gateway, ModelError, ProviderRateLimited
from .memory import Memory
from .opportunities import Opportunities
from .privacy import erase_user
from .profiles import Profiles
from .provenance import invalidate_source
from .queue import WorkQueue
from .response import Responder, GroupFormatError
from .router import Router
from .runtime_lock import RuntimeLock
from .store import Store

log=logging.getLogger('vincent')


@asynccontextmanager
async def typing_indicator(channel):
    """Show typing while generating; an unavailable indicator must not block a reply."""
    manager=None
    if channel is not None and callable(getattr(channel,'typing',None)):
        try:
            manager=channel.typing()
            await asyncio.wait_for(manager.__aenter__(),timeout=3)
        except (discord.HTTPException,asyncio.TimeoutError,OSError):
            manager=None
            log.warning('Unable to show typing indicator')
    try:
        yield
    finally:
        if manager is not None:
            try:await manager.__aexit__(None,None,None)
            except (discord.HTTPException,OSError):log.warning('Unable to stop typing indicator')


def message_metadata(message):
    """Retain references and types only; never fetch or store media bytes."""
    media=[{'kind':'attachment','type':getattr(a,'content_type',None),'url':getattr(a,'url',None)} for a in getattr(message,'attachments',[])]
    media += [{'kind':'embed','type':getattr(e,'type',None),'url':getattr(e,'url',None)} for e in getattr(message,'embeds',[])]
    media += [{'kind':'sticker','id':str(getattr(x,'id','')),'name':str(getattr(x,'name',''))[:100]} for x in getattr(message,'stickers',[])]
    return {'mentions':[str(u.id) for u in getattr(message,'mentions',[])], 'author_bot':bool(message.author.bot),
            'content_available':True,'display_name':str(getattr(message.author,'display_name',message.author.id))[:100],
            'username':str(getattr(message.author,'name',''))[:100], 'media':media}


def prepare_user_mentions(text,context):
    """Resolve unambiguous names and permit only users represented in this context."""
    allowed=set();names={}
    current=str(context.get('current_user_id',''))
    if current.isdigit():allowed.add(current)
    for message in context.get('recent_messages',[]):
        uid=str(message.get('author_id',''));metadata=message.get('metadata') or {}
        if uid.isdigit():
            allowed.add(uid)
            for key in ('display_name','username'):
                name=metadata.get(key)
                if isinstance(name,str) and name.strip() and len(name)<=100:
                    names.setdefault(name.strip().casefold(),set()).add(uid)
        for mention in metadata.get('mentions',[]):
            if str(mention).isdigit():allowed.add(str(mention))
    # Never turn a shared or ambiguous display name into a ping.
    for name,ids in sorted(names.items(),key=lambda entry:len(entry[0]),reverse=True):
        if len(ids)!=1 or name in {'everyone','here'}:continue
        uid=next(iter(ids))
        text=re.sub(r'(?<![\w@<])@'+re.escape(name)+r'(?!\w)',lambda _:f'<@{uid}>',text,flags=re.IGNORECASE)
    text=re.sub(r'<@!?(\d+)>',lambda match: f"<@{match.group(1)}>" if match.group(1) in allowed else '@unknown',text)
    mentioned={match.group(1) for match in re.finditer(r'<@(\d+)>',text)} & allowed
    return text,discord.AllowedMentions(everyone=False,roles=False,users=[discord.Object(id=int(uid)) for uid in sorted(mentioned)],replied_user=False)


class Vincent(discord.Client):
    def __init__(self,settings):
        # The privileged members intent is only needed for welcome events.
        # Message content remains required for normal chat routing.
        intents=discord.Intents.default();intents.message_content=True;intents.members=bool(settings.welcome_channel_id)
        super().__init__(intents=intents,allowed_mentions=discord.AllowedMentions.none())
        self.settings=settings;self.store=Store(settings.database);self.queue=WorkQueue(self.store,max_backlog=settings.max_backlog);self.memory=Memory(self.store)
        self.runtime_lock=RuntimeLock(settings.database)
        self.budget=Budget(self.store,settings);self.adaptation=Adaptation(self.store,min_votes=settings.adaptation_min_votes,min_raters=settings.adaptation_min_raters);self.experiments=Experiments(self.store);self.model_client=None;self.responder=None
        self.inflight=set()
        self.recent_bot={}
        self.extractor=None
        self.gateway=None
        self.opportunities=Opportunities(self.store)
        self.profiles=Profiles(self.store)
        self.flags=FeatureFlags(self.store,settings)
        self.memory.put_shared(scope='global',content='HMAN is Vincent’s creator and owner.',kind='identity')
        self.adaptation.ensure_seed()

    async def setup_hook(self):
        self.runtime_lock.acquire()
        self.queue.recover_after_restart()
        self.model_client=httpx.AsyncClient(timeout=35)
        gateway=Gateway(self.store,self.budget,self.settings,self.model_client)
        self.gateway=gateway
        self.responder=Responder(gateway,self.adaptation)
        self.extractor=Extractor(self.store,self.memory,gateway)
        if self.settings.memory_enabled:
            try:
                await asyncio.to_thread(self.memory.enable_semantic)
                log.info('Local semantic memory enabled')
            except Exception:
                log.exception('Semantic memory unavailable; using lexical fallback. Run python -m vincent.semantic_memory to download the model')
            self.memory_worker.change_interval(minutes=self.settings.memory_interval_minutes)
            self.memory_worker.start()
        self.worker.start()
        self.quota_worker.start()
        self.recovery_worker.start()
        self.routing_recovery_worker.start()
        self.feedback_worker.start()
        if self.settings.adaptation_enabled:
            self.adaptation_worker.start()
            self.experiment_worker.start()

    async def close(self):
        self.worker.cancel()
        self.quota_worker.cancel()
        self.recovery_worker.cancel()
        self.routing_recovery_worker.cancel()
        self.memory_worker.cancel()
        self.feedback_worker.cancel()
        self.adaptation_worker.cancel()
        self.experiment_worker.cancel()
        for task in tuple(self.inflight):task.cancel()
        if self.inflight:await asyncio.gather(*self.inflight,return_exceptions=True)
        if self.model_client:await self.model_client.aclose()
        await super().close();self.store.close();self.runtime_lock.release()

    async def on_ready(self):log.info('Connected as %s; watching %s channels',self.user,len(self.settings.channels))

    def source_still_pending(self,message_id,changed_at):
        with self.store.lock:
            row=self.store.db.execute('SELECT deleted,changed_at,routing_state FROM messages WHERE message_id=?',(str(message_id),)).fetchone()
        return bool(row and not row['deleted'] and row['changed_at']==changed_at and row['routing_state']=='pending')

    def recent_channel_prompt(self,guild,channel,message_id,created_at):
        """Find the last Vincent message and whether another person has spoken since."""
        with self.store.lock:
            row=self.store.db.execute("""SELECT m.message_id,m.content,m.created_at,q.author_id AS target_user_id,q.branch_id
                FROM queue q JOIN messages m ON m.message_id=q.response_message_id
                WHERE q.guild_id=? AND q.channel_id=? AND q.status='sent' AND m.deleted=0
                AND m.created_at BETWEEN ? AND ? AND m.created_at>=COALESCE((SELECT reset_at FROM channel_context WHERE guild_id=? AND channel_id=?),0)
                ORDER BY m.created_at DESC,q.id DESC LIMIT 1""",(str(guild),str(channel),created_at-30,created_at,str(guild),str(channel))).fetchone()
            if not row:return None
            interrupted=self.store.db.execute("""SELECT 1 FROM messages WHERE guild_id=? AND channel_id=? AND deleted=0
                AND created_at>? AND created_at<=? AND message_id!=? AND json_extract(metadata,'$.author_bot') IS NOT 1 LIMIT 1""",(str(guild),str(channel),row['created_at'],created_at,str(message_id))).fetchone()
            result=dict(row);result['interrupted']=bool(interrupted)
            return result

    async def on_message(self,message):
        if not message.guild or message.channel.id not in self.settings.channels:return
        now=message.created_at.timestamp();ref=message.reference.message_id if message.reference else None
        metadata=message_metadata(message)
        if message.author.bot:
            if self.user and message.author.id==self.user.id and getattr(message,'nonce',None):
                with self.store.lock:
                    pending=self.store.db.execute("SELECT id,status,author_id,guild_id,reason,response_message_id FROM queue WHERE send_nonce=?",(str(message.nonce),)).fetchone()
                if pending and pending['author_id']=='redacted':return
                if pending and pending['status']=='uncertain':self.queue.reconcile_sent(pending['id'],message.id,sent_at=now)
                if pending and pending['status'] in ('uncertain','sent') and (not pending['response_message_id'] or pending['response_message_id']==str(message.id)) and pending['reason'] not in ('observe','welcome') and self.settings.memory_enabled:
                    self.profiles.outgoing(pending['guild_id'],pending['author_id'],now,message_id=message.id)
            self.store.message_event(kind='create',message_id=message.id,guild_id=message.guild.id,channel_id=message.channel.id,author_id=message.author.id,content=message.content,created_at=now,changed_at=now,reply_to=ref,metadata=metadata)
            return
        inserted=self.store.message_event(kind='create',message_id=str(message.id),guild_id=str(message.guild.id),channel_id=str(message.channel.id),author_id=str(message.author.id),content=message.content,created_at=now,changed_at=now,reply_to=ref,metadata=metadata)
        if not inserted:return
        if not self.source_still_pending(message.id,now):
            # An edit was ingested first. Leave the current version for the
            # recovery worker; never execute commands from the older create.
            self.store.log(f'stale_create:{message.id}','stale_create',guild_id=str(message.guild.id),channel_id=str(message.channel.id),message_id=str(message.id),author_id=str(message.author.id))
            return
        # Collect all nonempty ordinary messages, including brief replies and
        # messages not addressed to Vincent, before any asynchronous lookup.
        if self.settings.memory_enabled and message.content.strip() and not message.content.lstrip().startswith(('!','/')):
            self.extractor.collect(message.guild.id,message.author.id,message.id)
        if self.flags.enabled(message.guild.id,message.channel.id,'unsolicited'):self.opportunities.invalidate_channel(message.channel.id)
        if message.content.strip().lower() in ('!vin help','!vin quota'):
            if message.content.strip().lower()=='!vin quota':
                quota=self.budget.status();remaining=quota['provider_remaining']
                answer=f"local requests: {quota['local_used']}/{quota['local_limit']}; provider last reported: {remaining if remaining is not None else 'unknown'} remaining (snapshot)."
                if quota['provider_blocked_until']:answer+=f" provider daily limit: paused until <t:{int(quota['provider_blocked_until'])}:R>."
            else:
                answer='`!vin memory list|add|correct|delete|forget|on|off`, `!vin memory auto on|off`, `!vin data forget`, `!vin quota`. Server managers: `!vin admin status`.'
            await message.reply(answer,mention_author=False,allowed_mentions=discord.AllowedMentions.none())
            self.store.mark_routed(message.id)
            return
        if message.content.lower().startswith('!vin admin '):
            await self.admin_command(message,message.content[11:].strip())
            self.store.mark_routed(message.id)
            return
        if message.content.strip().lower()=='!vin data forget':
            erase_user(self.store,message.guild.id,message.author.id)
            await message.reply('local records were removed and memory collection is off; Discord and provider copies are separate.',mention_author=False,allowed_mentions=discord.AllowedMentions.none())
            self.store.mark_routed(message.id)
            return
        if message.content.lower().startswith('!vin memory '):
            await self.memory_command(message,message.content[12:].strip());self.store.mark_routed(message.id);return
        if not message.content:self.store.mark_routed(message.id);return
        with self.store.lock:
            row=self.store.db.execute('SELECT author_id FROM messages WHERE message_id=?',(str(ref),)).fetchone() if ref else None
        if ref and not row:
            reference=message.reference
            original=getattr(reference,'resolved',None)
            if not hasattr(original,'author') and getattr(reference,'channel_id',message.channel.id)==message.channel.id:
                try:original=await asyncio.wait_for(message.channel.fetch_message(ref),timeout=3)
                except (discord.HTTPException,asyncio.TimeoutError):original=None
            if not self.source_still_pending(message.id,now):return
            if original is not None and hasattr(original,'author'):
                created=original.created_at.timestamp()
                parent=original.reference.message_id if original.reference else None
                self.store.message_event(kind='create',message_id=original.id,guild_id=message.guild.id,channel_id=message.channel.id,author_id=original.author.id,content=original.content,created_at=created,changed_at=created,reply_to=parent,metadata=message_metadata(original))
                row={'author_id':str(original.author.id)}
        remaining=120-max(0,time.time()-now)
        if remaining<=0:
            self.store.log(f'route_expired:{message.id}','route_expired',guild_id=str(message.guild.id),channel_id=str(message.channel.id),message_id=str(message.id),author_id=str(message.author.id))
            self.store.mark_routed(message.id);return
        recent=self.recent_bot.get(message.channel.id)
        if not recent or recent.get('target_user_id')!=str(message.author.id):
            with self.store.lock:
                previous=self.store.db.execute("SELECT q.response_message_id AS message_id,q.author_id AS target_user_id,q.updated_at AS created_at,q.branch_id,m.content FROM queue q LEFT JOIN messages m ON m.message_id=q.response_message_id AND m.deleted=0 WHERE q.channel_id=? AND q.author_id=? AND q.status='sent' AND q.updated_at>=COALESCE((SELECT reset_at FROM channel_context WHERE guild_id=? AND channel_id=?),0) ORDER BY q.updated_at DESC LIMIT 1",(str(message.channel.id),str(message.author.id),str(message.guild.id),str(message.channel.id))).fetchone()
            recent=dict(previous) if previous else None
        ancestor=bot_ancestor(self.store,parent_id=ref,channel_id=message.channel.id,guild_id=message.guild.id,bot_id=self.user.id,now=now) if ref else None
        channel_prompt=self.recent_channel_prompt(message.guild.id,message.channel.id,message.id,now) if not ref else None
        route=Router(self.user.id).route({'message_id':str(message.id),'author_id':str(message.author.id),'created_at':now,'content':message.content,'reply_to':ref,'mentions':[str(x.id) for x in message.mentions]},referenced=dict(row) if row else None,recent_bot=recent,bot_ancestor=ancestor,recent_channel_bot=channel_prompt)
        self.store.log(f'route:{message.id}','route',guild_id=str(message.guild.id),channel_id=str(message.channel.id),message_id=str(message.id),author_id=str(message.author.id),payload={'action':route.action,'score':route.score,'reason':route.reason})
        if self.settings.memory_enabled and not message.content.lstrip().startswith(('!','/')):
            self.extractor.collect(message.guild.id,message.author.id,message.id,interaction=route.action in ('direct','candidate'))
        if route.action in ('direct','candidate'):
            if self.settings.memory_enabled:self.profiles.incoming(message.guild.id,message.author.id,now,message_id=message.id)
            branch=route.branch_id if ref or route.reason in ('channel_prompt_answer','channel_question_answer') else f'user:{message.author.id}'
            self.queue.enqueue(guild_id=message.guild.id,channel_id=message.channel.id,author_id=message.author.id,branch_id=branch,message_id=message.id,priority=20 if route.action=='direct' else 10,ttl=remaining,reason=route.reason)
        elif not (ref and not row) and self.flags.enabled(message.guild.id,message.channel.id,'unsolicited'):
            self.opportunities.observe(guild_id=message.guild.id,channel_id=message.channel.id,author_id=message.author.id,message_id=message.id,content=message.content,branch_id=ref)
        # A missing reply target may be Vincent's message. Retry its lookup
        # instead of permanently classifying it as unrelated.
        if not (ref and not row and route.action not in ('direct','candidate')):
            self.store.mark_routed(message.id)

    async def on_member_join(self,member):
        channel=self.get_channel(self.settings.welcome_channel_id) if self.settings.welcome_channel_id else None
        if not channel or channel.guild.id!=member.guild.id or member.bot:return
        if self.opportunities.quiet(self.settings.quiet_timezone,self.settings.quiet_start,self.settings.quiet_end):return
        now=time.time();event_id=f'join:{member.guild.id}:{member.id}:{int(now)}'
        name=str(getattr(member,'display_name',member.id))[:100]
        self.store.message_event(kind='create',message_id=event_id,guild_id=member.guild.id,channel_id=channel.id,author_id=member.id,content=f'A new member named {name} joined the server.',created_at=now,changed_at=now,metadata={'display_name':name,'author_bot':False})
        self.queue.enqueue(guild_id=member.guild.id,channel_id=channel.id,author_id=member.id,branch_id=event_id,message_id=event_id,priority=1,ttl=120,reason='welcome')
        self.store.mark_routed(event_id)

    async def on_message_edit(self,before,after):
        if not after.guild or after.channel.id not in self.settings.channels:return
        changed=after.edited_at.timestamp() if after.edited_at else time.time()
        with self.store.lock:previous=self.store.db.execute('SELECT changed_at FROM messages WHERE message_id=?',(str(after.id),)).fetchone()
        self.store.message_event(kind='edit',message_id=after.id,guild_id=after.guild.id,channel_id=after.channel.id,author_id=after.author.id,content=after.content,created_at=after.created_at.timestamp(),changed_at=changed,reply_to=after.reference.message_id if after.reference else None,metadata=message_metadata(after))
        if previous and changed>previous['changed_at']:
            self.queue.source_changed(after.id)
            invalidate_source(self.store,after.id,deleted=False)
            if not after.author.bot:
                with self.store.transaction() as db:
                    reroute=db.execute("""UPDATE messages SET routing_state='pending' WHERE message_id=? AND deleted=0 AND changed_at=?
                        AND NOT EXISTS (SELECT 1 FROM queue_sources WHERE message_id=?)""",(str(after.id),changed,str(after.id))).rowcount
                if reroute:self.store.log(f'edit_reroute:{after.id}:{changed}','edit_reroute',guild_id=str(after.guild.id),channel_id=str(after.channel.id),message_id=str(after.id),author_id=str(after.author.id))

    async def on_raw_message_edit(self,payload):
        if payload.channel_id not in self.settings.channels or payload.cached_message is not None:return
        channel=self.get_channel(payload.channel_id)
        if not channel:return
        try:
            after=await asyncio.wait_for(channel.fetch_message(payload.message_id),timeout=3)
            await self.on_message_edit(None,after)
        except (discord.HTTPException,asyncio.TimeoutError) as exc:
            self.store.log(f'edit_unavailable:{payload.message_id}:{time.time()}','edit_unavailable',guild_id=str(payload.guild_id),channel_id=str(payload.channel_id),message_id=str(payload.message_id),payload={'error':type(exc).__name__})

    async def _apply_delete(self,channel_id,guild_id,message_id):
        if channel_id not in self.settings.channels:return
        with self.store.lock:row=self.store.db.execute('SELECT * FROM messages WHERE message_id=?',(str(message_id),)).fetchone()
        if row:
            self.store.message_event(kind='delete',message_id=row['message_id'],guild_id=row['guild_id'],channel_id=row['channel_id'],author_id=row['author_id'],content=None,created_at=row['created_at'],changed_at=time.time())
        else:
            # A deletion can precede a delayed create/edit event. Preserve a
            # tombstone even though the original author and text are unknown.
            observed=time.time()
            self.store.message_event(kind='delete',message_id=message_id,guild_id=guild_id if guild_id is not None else 'unknown',channel_id=channel_id,author_id='unknown',content=None,created_at=discord.utils.snowflake_time(int(message_id)).timestamp(),changed_at=observed,metadata={'content_available':False})
            self.store.log(f'unknown_delete:{message_id}','unknown_delete',guild_id=str(guild_id),channel_id=str(channel_id),message_id=str(message_id))
        self.queue.source_changed(message_id,deleted=True)
        invalidate_source(self.store,message_id,deleted=True)

    async def on_raw_message_delete(self,payload):
        await self._apply_delete(payload.channel_id,payload.guild_id,payload.message_id)

    async def on_raw_bulk_message_delete(self,payload):
        for message_id in payload.message_ids:
            await self._apply_delete(payload.channel_id,payload.guild_id,message_id)

    async def admin_command(self,message,action):
        command=action.lower().strip()
        # Audit the operation without copying free-form memory text into a
        # second event. The original command remains in the message log.
        operation=next((name for name in ('remember server','remember global','memory delete','context clear','feature','activate','status','memories','proposals') if command==name or command.startswith(name+' ')),'unknown')
        def audit(outcome):
            self.store.log(f'admin_action:{uuid.uuid4().hex}','admin_action',guild_id=str(message.guild.id),channel_id=str(message.channel.id),message_id=str(message.id),author_id=str(message.author.id),payload={'operation':operation,'outcome':outcome})
        audit('attempted')
        if not (message.author.guild_permissions.manage_guild or (self.settings.owner_id and message.author.id==self.settings.owner_id)):
            audit('permission_denied')
            await message.reply('Manage Server permission required.',mention_author=False);return
        outcome='completed'
        if command=='status':
            status=self.budget.status()
            with self.store.lock:
                pending=self.store.db.execute("SELECT count(*) FROM queue WHERE status='pending'").fetchone()[0]
                uncertain=self.store.db.execute("SELECT count(*) FROM queue WHERE status='uncertain'").fetchone()[0]
            version=self.adaptation.active()
            answer=f"local calls: {status['local_used']}/{status['local_limit']}; provider last reported remaining: {status['provider_remaining'] if status['provider_remaining'] is not None else 'unknown'}; pending: {pending}; uncertain sends: {uncertain}; guidance: {version['id'] if version else 'core only'}. Provider value is a snapshot."
            if status['provider_blocked_until']:answer+=f" provider daily limit: paused until <t:{int(status['provider_blocked_until'])}:R>."
        elif command=='context clear':
            clear_channel(self.store,message.guild.id,message.channel.id,actor=message.author.id,now=time.time())
            self.recent_bot.pop(message.channel.id,None)
            answer='channel context cleared; long-term memories are unchanged.'
        elif command.startswith('feature '):
            parts=command.split()
            if len(parts)==3 and parts[1] in ('unsolicited','reactions') and parts[2] in ('on','off'):
                self.flags.set(message.guild.id,message.channel.id,parts[1],parts[2]=='on',message.author.id)
                answer=f'{parts[1]} {parts[2]} in this channel.'
            else:answer='use `!vin admin feature unsolicited on|off` or `feature reactions on|off`.';outcome='invalid'
        elif command=='memories':
            rows=self.memory.shared(message.guild.id,limit=20)
            answer='\n'.join(f"{r['id']} ({r['scope']}): {r['content']}" for r in rows)[:1800] or 'no shared memories.'
        elif command.startswith('remember server '):
            try:self.memory.put_shared(scope='server',guild=message.guild.id,content=action[len('remember server '):],source_ids=[message.id]);answer='server memory saved.'
            except ValueError:answer='memory must contain 3–300 characters.';outcome='invalid'
        elif command.startswith('remember global '):
            if not self.settings.owner_id or message.author.id!=self.settings.owner_id:answer='only the configured owner can change global memory.';outcome='permission_denied'
            else:
                try:self.memory.put_shared(scope='global',content=action[len('remember global '):],source_ids=[message.id]);answer='global memory saved.'
                except ValueError:answer='memory must contain 3–300 characters.';outcome='invalid'
        elif command.startswith('memory delete '):
            try:
                mid=int(command.split()[2]);owner=bool(self.settings.owner_id and message.author.id==self.settings.owner_id)
                answer='memory deleted.' if self.memory.remove_shared(mid,guild=message.guild.id,allow_global=owner) else 'memory not found or not permitted.'
                if answer!='memory deleted.':outcome='not_found_or_denied'
            except (ValueError,IndexError):answer='use `!vin admin memory delete ID`.';outcome='invalid'
        elif command=='proposals':
            with self.store.lock:rows=self.store.db.execute("SELECT id,text FROM prompt_versions WHERE status='proposed' ORDER BY id DESC LIMIT 5").fetchall()
            answer='\n'.join(f"{r['id']}: {r['text']}" for r in rows)[:1800] or 'no proposals.'
        elif command.startswith('activate '):
            if not self.settings.owner_id or message.author.id!=self.settings.owner_id:
                answer='only the configured owner can change global guidance.'
                outcome='permission_denied'
            else:
                try:self.adaptation.activate(int(command.split()[1]),approved=True);answer='guidance version activated; review results carefully.'
                except (ValueError,IndexError):answer='invalid guidance version.';outcome='invalid'
        else:answer='use `!vin admin status`, `memories`, `remember server TEXT`, `remember global TEXT`, `memory delete ID`, `proposals`, or `activate ID`.';outcome='invalid'
        audit(outcome)
        await message.reply(answer,mention_author=False,allowed_mentions=discord.AllowedMentions.none())

    async def memory_command(self,message,action):
        guild,user=message.guild.id,message.author.id
        command=action.lower()
        if command=='auto off':self.memory.set_auto(guild,user,False);answer='automatic memory off.'
        elif command=='auto on':self.memory.set_auto(guild,user,True);answer='automatic memory on.'
        elif command=='off':self.memory.set_enabled(guild,user,False);answer='memory off.'
        elif command=='on':self.memory.set_enabled(guild,user,True);answer='memory on.'
        elif command=='forget':self.memory.forget(guild,user);answer='your stored profile memories were deleted.'
        elif command=='list':
            with self.store.lock:rows=self.store.db.execute("SELECT id,content FROM memories WHERE guild_id=? AND user_id=? AND scope='user' AND status='active' ORDER BY updated_at DESC LIMIT 30",(str(guild),str(user))).fetchall()
            try:await message.author.send(('your saved memories:\n'+'\n'.join(f"- {r['id']}: {r['content']}" for r in rows) if rows else 'no saved memories.')[:1900],allowed_mentions=discord.AllowedMentions.none());answer='sent you a DM.'
            except discord.Forbidden:answer='could not DM you; enable server DMs and try again.'
        elif command.startswith('delete ') or command.startswith('correct '):
            parts=action.split(maxsplit=2)
            try:
                memory_id=int(parts[1]);replacement=parts[2] if command.startswith('correct ') and len(parts)>2 else None
                if command.startswith('correct ') and replacement is None:raise ValueError('Missing correction')
                ok=self.memory.change_record(guild,user,memory_id,replacement=replacement,source_id=message.id)
                answer='memory updated.' if ok else 'memory not found in your profile.'
            except (ValueError,IndexError):answer='use `!vin memory delete ID` or `!vin memory correct ID new text`.'
        elif command.startswith('add '):
            text=action[4:].strip();enabled,generation=self.memory.generation(guild,user)
            if not enabled:answer='memory is off for you.'
            elif self.memory.apply_user_proposals(guild,user,generation,[{'content':text,'kind':'fact','source_ids':[str(message.id)],'source_type':'self_report','confidence':1.0}]):answer='saved to your memory.'
            else:answer='could not save that memory; use 3–300 characters.'
        else:answer='use `!vin memory on`, `off`, `auto on`, `auto off`, `list`, `add text`, `correct ID text`, `delete ID`, or `forget`.'
        await message.reply(answer,mention_author=False,allowed_mentions=discord.AllowedMentions.none())

    @tasks.loop(seconds=2)
    async def worker(self):
        if len(self.inflight)>=self.settings.worker_concurrency:return
        item=self.queue.claim(settle_seconds=5)
        if not item and self.flags.any_unsolicited() and not self.opportunities.quiet(self.settings.quiet_timezone,self.settings.quiet_start,self.settings.quiet_end):
            opportunity=self.opportunities.next()
            if opportunity and self.flags.enabled(opportunity['guild_id'],opportunity['channel_id'],'unsolicited'):
                self.queue.enqueue(guild_id=opportunity['guild_id'],channel_id=opportunity['channel_id'],author_id=opportunity['author_id'],branch_id=opportunity['branch_id'],message_id=opportunity['message_id'],priority=1,ttl=30,reason='observe')
                item=self.queue.claim(settle_seconds=0)
        if not item:return
        with self.store.lock:experiment=self.store.db.execute("SELECT 1 FROM prompt_experiments WHERE state='running' LIMIT 1").fetchone()
        others=self.queue.claim_compatible(item,settle_seconds=2) if self.settings.group_direct and not experiment else []
        task=asyncio.create_task(self.process_group([item]+others) if others else self.process_item(item),name=f"queue:{item['id']}")
        self.inflight.add(task)
        task.add_done_callback(self._work_done)

    def _work_done(self,task):
        self.inflight.discard(task)
        if not task.cancelled() and task.exception():
            error=type(task.exception()).__name__
            log.error('Worker task failed: %s',error)
            self.store.log(f'worker_error:{uuid.uuid4().hex}','worker_error',payload={'task':task.get_name(),'error':error})

    async def process_item(self,item):
        if item['reason']=='observe' and (not self.flags.enabled(item['guild_id'],item['channel_id'],'unsolicited') or self.opportunities.quiet(self.settings.quiet_timezone,self.settings.quiet_start,self.settings.quiet_end)):
            self.queue.finish(item['id'],'cancelled',lease_token=item['lease_token'],reason='unsolicited disabled or quiet hours');return
        channel=self.get_channel(int(item['channel_id']))
        if not channel:self.queue.finish(item['id'],'failed',lease_token=item['lease_token'],reason='channel unavailable');return
        context=await asyncio.to_thread(build_context,self.store,self.memory,item,memory_enabled=self.settings.memory_enabled)
        if context is None:
            self.queue.finish(item['id'],'cancelled',lease_token=item['lease_token'],reason='source messages unavailable');return
        versions=context.pop('_input_versions');memory_generation=context.pop('_memory_generation');memory_versions=context.pop('_memory_versions')
        try:
            assignment=self.experiments.assign(item['id'])
            kwargs={'kind':'observe' if item['reason'] in ('observe','welcome') else 'direct','queue_id':item['id']}
            if assignment is not None:kwargs['prompt_version']=assignment['prompt_version']
            async with typing_indicator(channel):
                result=await self.responder.decide(context,**kwargs)
                if item['priority']>=20 and result['decision']=='silence' and self.queue.fresh(item['id'],lease_token=item['lease_token'],expected_versions=versions,memory_generation=memory_generation,memory_versions=memory_versions):
                    # A definite direct request should not be silently discarded. One
                    # budgeted retry asks for a response without losing the queue item.
                    context['retry_note']='This is a direct request. Reply to the requester rather than staying silent.'
                    result=await self.responder.decide(context,**kwargs)
            if item['reason']=='observe' and (not self.flags.enabled(item['guild_id'],item['channel_id'],'unsolicited') or self.opportunities.quiet(self.settings.quiet_timezone,self.settings.quiet_start,self.settings.quiet_end)):
                self.queue.finish(item['id'],'cancelled',lease_token=item['lease_token'],reason='unsolicited disabled or quiet hours');return
            if not self.queue.fresh(item['id'],lease_token=item['lease_token'],expected_versions=versions,memory_generation=memory_generation,memory_versions=memory_versions):
                if item['reason']=='observe':self.queue.finish(item['id'],'expired',lease_token=item['lease_token'],reason='stale opportunity')
                else:self.queue.retry_stale(item['id'],lease_token=item['lease_token'])
                return
            if result['decision']=='silence':
                status='failed' if item['priority']>=20 else 'cancelled'
                self.queue.finish(item['id'],status,lease_token=item['lease_token'],reason='model chose silence');return
            await self.deliver(item,channel,context,result)
        except (BudgetExceeded,ProviderRateLimited) as exc:
            self.queue.defer(item['id'],lease_token=item['lease_token'],delay=exc.retry_after,reason=str(exc));log.info('Response deferred: %s',exc)
        except (ModelError,ValueError) as exc:
            self.queue.finish(item['id'],'failed',lease_token=item['lease_token'],reason=type(exc).__name__);log.warning('Response failed: %s',type(exc).__name__)
        except discord.HTTPException as exc:
            self.store.log(f'send_error:{uuid.uuid4().hex}','send_error',guild_id=item['guild_id'],channel_id=item['channel_id'],payload={'queue_id':item['id'],'error':type(exc).__name__})
            log.warning('Discord send uncertain: %s',type(exc).__name__)

    async def deliver(self,item,channel,context,result):
        # Mark uncertain BEFORE send; a crash during Discord I/O will not cause an automatic duplicate.
        text,allowed_mentions=prepare_user_mentions(result['text'],context)
        nonce=self.queue.begin_send(item['id'],text,lease_token=item['lease_token'])
        if not nonce:return
        send_kwargs={'nonce':nonce,'allowed_mentions':allowed_mentions}
        source=context['source_message_ids'][-1]
        if item['priority']>=20 and str(source).isdigit() and hasattr(channel,'get_partial_message'):
            send_kwargs['reference']=channel.get_partial_message(int(source))
            send_kwargs['mention_author']=False
        sent=await channel.send(text,**send_kwargs)
        resolved=self.queue.reconcile_sent(item['id'],sent.id,sent_at=sent.created_at.timestamp())
        if not resolved:
            # The gateway event may have reconciled first; erasure may also have cancelled it.
            with self.store.lock:
                current=self.store.db.execute('SELECT status,response_message_id FROM queue WHERE id=?',(item['id'],)).fetchone()
            if not current or current['status']!='sent' or current['response_message_id']!=str(sent.id):return
        self.store.message_event(kind='create',message_id=sent.id,guild_id=item['guild_id'],channel_id=item['channel_id'],author_id=self.user.id,content=text,created_at=sent.created_at.timestamp(),changed_at=sent.created_at.timestamp(),metadata={'author_bot':True})
        if item['reason'] not in ('observe','welcome') and self.settings.memory_enabled:self.profiles.outgoing(item['guild_id'],item['author_id'],sent.created_at.timestamp(),message_id=sent.id)
        self.recent_bot[channel.id]={'message_id':str(sent.id),'target_user_id':item['author_id'],'created_at':sent.created_at.timestamp(),'branch_id':item['branch_id'],'content':text}
        if self.flags.enabled(item['guild_id'],item['channel_id'],'reactions'):
            for emoji in (self.settings.positive_emoji,self.settings.negative_emoji):
                try:await sent.add_reaction(emoji)
                except discord.HTTPException as exc:
                    self.store.log(f'reaction_seed_error:{sent.id}:{emoji}','reaction_seed_error',guild_id=item['guild_id'],channel_id=item['channel_id'],message_id=str(sent.id),payload={'emoji':emoji,'error':type(exc).__name__})
                    log.warning('Unable to add feedback reaction')


    def _link_group_request(self,request_id,entries):
        if not request_id:return
        with self.store.transaction() as db:
            if not db.execute('SELECT 1 FROM request_attempts WHERE request_id=?',(request_id,)).fetchone():return
            db.executemany('INSERT OR IGNORE INTO group_request_members(request_id,queue_id) VALUES(?,?)',[(request_id,item['id']) for item,_,_ in entries])

    async def process_group(self,items):
        # Canary assignments are per response; never mix experimental variants.
        with self.store.lock:running=self.store.db.execute("SELECT 1 FROM prompt_experiments WHERE state='running' LIMIT 1").fetchone()
        if running:
            for item in items:self.queue.retry_individually(item['id'],lease_token=item['lease_token'],reason='experiment active')
            return
        entries=[]
        for item in items:
            context=await asyncio.to_thread(build_context,self.store,self.memory,item,memory_enabled=self.settings.memory_enabled)
            if context is None:
                self.queue.finish(item['id'],'cancelled',lease_token=item['lease_token'],reason='source messages unavailable');continue
            if context['private_memories']:
                # A grouped call exposes every included context to every output.
                # Process these separately so relevant personal memories can be used.
                self.queue.retry_individually(item['id'],lease_token=item['lease_token'],reason='personal memory available')
                continue
            versions=context.pop('_input_versions');context.pop('_memory_generation');context.pop('_memory_versions')
            # Never put multiple users' private profiles into the same model call.
            context['private_memories']=[]
            context['private_profile']=None
            memory_versions={m['id']:m['updated_at'] for m in context['shared_memories']}
            entries.append((item,context,{'expected_versions':versions,'memory_versions':memory_versions}))
        if len(entries)<2:
            for item,_,_ in entries:self.queue.retry_individually(item['id'],lease_token=item['lease_token'],reason='group became too small')
            return
        # The model receives all grouped contexts in one request. Any output
        # can depend on any input, even when a source is outside another
        # member's individual recent-message window.
        group_versions={};group_memories={}
        for _,_,checks in entries:
            group_versions.update(checks['expected_versions']);group_memories.update(checks['memory_versions'])
        for _,_,checks in entries:
            checks['expected_versions']=group_versions;checks['memory_versions']=group_memories
        contexts=[{'queue_id':str(item['id']),'conversation':context} for item,context,_ in entries]
        try:
            channel=self.get_channel(int(entries[0][0]['channel_id']))
            async with typing_indicator(channel):
                results,request_id=await self.responder.decide_group(contexts)
            self._link_group_request(request_id,entries)
        except GroupFormatError as exc:
            self._link_group_request(exc.request_id,entries)
            for item,_,_ in entries:self.queue.retry_individually(item['id'],lease_token=item['lease_token'],reason='invalid group output')
            return
        except (BudgetExceeded,ProviderRateLimited) as exc:
            for item,_,_ in entries:self.queue.defer(item['id'],lease_token=item['lease_token'],delay=exc.retry_after,reason=str(exc))
            return
        except (ModelError,ValueError) as exc:
            for item,_,_ in entries:self.queue.retry_individually(item['id'],lease_token=item['lease_token'],reason=type(exc).__name__)
            return
        for item,context,checks in entries:
            if not self.queue.fresh(item['id'],lease_token=item['lease_token'],**checks):
                self.queue.retry_stale(item['id'],lease_token=item['lease_token']);continue
            result=results.get(str(item['id']))
            if not result or result['decision']!='reply':
                self.queue.retry_individually(item['id'],lease_token=item['lease_token'],reason='group omitted direct reply');continue
            channel=self.get_channel(int(item['channel_id']))
            if not channel:
                self.queue.finish(item['id'],'failed',lease_token=item['lease_token'],reason='channel unavailable');continue
            try:await self.deliver(item,channel,context,result)
            except discord.HTTPException as exc:
                self.store.log(f'send_error:{uuid.uuid4().hex}','send_error',guild_id=item['guild_id'],channel_id=item['channel_id'],payload={'queue_id':item['id'],'error':type(exc).__name__})
                log.warning('Grouped send uncertain: %s',type(exc).__name__)

    async def recover_unrouted(self,*,age=10,limit=50):
        """Rebuild routing work after a crash between message logging and enqueue."""
        now=time.time()
        with self.store.lock:
            rows=self.store.db.execute("SELECT * FROM messages WHERE routing_state='pending' AND deleted=0 AND created_at<=? ORDER BY created_at LIMIT ?",(now-age,limit)).fetchall()
        # A missing reference can take seconds to fetch. Bound concurrency so
        # one batch cannot wait serially for every unavailable Discord message.
        references={}
        with self.store.lock:
            for row in rows:
                ref=row['reply_to'];content=(row['content'] or '').strip().lower()
                if not ref or int(row['channel_id']) not in self.settings.channels or now-row['created_at']>=120 or not content:continue
                if content.startswith(('!vin memory ','!vin admin ','!vin data ')) or content in ('!vin help','!vin quota'):continue
                if not self.store.db.execute('SELECT 1 FROM messages WHERE message_id=?',(ref,)).fetchone():references[(row['channel_id'],ref)]=None
        semaphore=asyncio.Semaphore(10)
        async def fetch_reference(key):
            async with semaphore:
                channel=self.get_channel(int(key[0]))
                try:return await asyncio.wait_for(channel.fetch_message(int(key[1])),timeout=3) if channel else None
                except (discord.HTTPException,asyncio.TimeoutError):return None
        if references:
            keys=list(references);values=await asyncio.gather(*(fetch_reference(key) for key in keys))
            references=dict(zip(keys,values))
        recovered=0
        for row in rows:
            now=time.time()
            if not self.source_still_pending(row['message_id'],row['changed_at']):continue
            if int(row['channel_id']) not in self.settings.channels:
                self.store.mark_routed(row['message_id']);continue
            content=row['content'] or '';metadata=json.loads(row['metadata'])
            # Avoid replaying state-changing controls after a crash: their prior
            # side effects may have completed before acknowledgement was stored.
            if content.lower().startswith(('!vin memory ','!vin admin ','!vin data ')) or content.strip().lower() in ('!vin help','!vin quota'):
                self.store.log(f"recovery_control_skipped:{row['message_id']}",'recovery_control_skipped',guild_id=row['guild_id'],channel_id=row['channel_id'],message_id=row['message_id'],author_id=row['author_id'])
                self.store.mark_routed(row['message_id']);continue
            if not content or now-row['created_at']>=120:
                self.store.log(f"recovery_expired:{row['message_id']}",'recovery_expired',guild_id=row['guild_id'],channel_id=row['channel_id'],message_id=row['message_id'],author_id=row['author_id'])
                self.store.mark_routed(row['message_id']);continue
            ref=row['reply_to']
            with self.store.lock:
                parent=self.store.db.execute('SELECT author_id FROM messages WHERE message_id=? AND deleted=0',(ref,)).fetchone() if ref else None
                parent_deleted=self.store.db.execute('SELECT deleted FROM messages WHERE message_id=?',(ref,)).fetchone() if ref and not parent else None
                previous=self.store.db.execute("SELECT q.response_message_id AS message_id,q.author_id AS target_user_id,q.updated_at AS created_at,q.branch_id,m.content FROM queue q LEFT JOIN messages m ON m.message_id=q.response_message_id AND m.deleted=0 WHERE q.channel_id=? AND q.author_id=? AND q.status='sent' AND q.updated_at>=COALESCE((SELECT reset_at FROM channel_context WHERE guild_id=? AND channel_id=?),0) ORDER BY q.updated_at DESC LIMIT 1",(row['channel_id'],row['author_id'],row['guild_id'],row['channel_id'])).fetchone()
            if ref and not parent and not parent_deleted:
                original=references.get((row['channel_id'],ref))
                # The user can edit, delete, or erase the source while the
                # network lookup is in flight. Re-read before using it.
                if not self.source_still_pending(row['message_id'],row['changed_at']):continue
                now=time.time()
                if now-row['created_at']>=120:
                    self.store.log(f"recovery_expired:{row['message_id']}",'recovery_expired',guild_id=row['guild_id'],channel_id=row['channel_id'],message_id=row['message_id'],author_id=row['author_id'])
                    self.store.mark_routed(row['message_id']);continue
                if original is None:
                    self.store.log(f"recovery_reference_unavailable:{row['message_id']}",'recovery_reference_unavailable',guild_id=row['guild_id'],channel_id=row['channel_id'],message_id=row['message_id'],author_id=row['author_id'])
                    continue
                created=original.created_at.timestamp()
                original_ref=original.reference.message_id if original.reference else None
                self.store.message_event(kind='create',message_id=original.id,guild_id=row['guild_id'],channel_id=row['channel_id'],author_id=original.author.id,content=original.content,created_at=created,changed_at=created,reply_to=original_ref,metadata=message_metadata(original))
                with self.store.lock:
                    parent=self.store.db.execute('SELECT author_id FROM messages WHERE message_id=? AND deleted=0',(ref,)).fetchone()
            ancestor=bot_ancestor(self.store,parent_id=ref,channel_id=row['channel_id'],guild_id=row['guild_id'],bot_id=self.user.id,now=row['created_at']) if ref else None
            channel_prompt=self.recent_channel_prompt(row['guild_id'],row['channel_id'],row['message_id'],row['created_at']) if not ref else None
            route=Router(self.user.id).route({'message_id':row['message_id'],'author_id':row['author_id'],'created_at':row['created_at'],'content':content,'reply_to':ref,'mentions':metadata.get('mentions',[])},referenced=dict(parent) if parent else None,recent_bot=dict(previous) if previous else None,bot_ancestor=ancestor,recent_channel_bot=channel_prompt)
            self.store.log(f"route_recovered:{row['message_id']}",'route',guild_id=row['guild_id'],channel_id=row['channel_id'],message_id=row['message_id'],author_id=row['author_id'],occurred_at=row['created_at'],payload={'action':route.action,'score':route.score,'reason':route.reason,'recovered':True})
            if self.settings.memory_enabled and not content.lstrip().startswith(('!','/')):
                self.extractor.collect(row['guild_id'],row['author_id'],row['message_id'],interaction=route.action in ('direct','candidate'))
            if route.action in ('direct','candidate'):
                if self.settings.memory_enabled:self.profiles.incoming(row['guild_id'],row['author_id'],row['created_at'],message_id=row['message_id'])
                branch=route.branch_id if ref or route.reason in ('channel_prompt_answer','channel_question_answer') else f"user:{row['author_id']}"
                self.queue.enqueue(guild_id=row['guild_id'],channel_id=row['channel_id'],author_id=row['author_id'],branch_id=branch,message_id=row['message_id'],priority=20 if route.action=='direct' else 10,ttl=max(1,120-(now-row['created_at'])),reason=route.reason)
                recovered+=1
            self.store.mark_routed(row['message_id'])
        return recovered

    @tasks.loop(seconds=10)
    async def routing_recovery_worker(self):await self.recover_unrouted()

    @routing_recovery_worker.before_loop
    async def before_routing_recovery_worker(self):await self.wait_until_ready()

    async def recover_uncertain(self):
        with self.store.lock:
            rows=self.store.db.execute("SELECT id,guild_id,channel_id,send_nonce,author_id,branch_id,reason FROM queue WHERE status='uncertain' AND send_nonce IS NOT NULL ORDER BY updated_at DESC LIMIT 100").fetchall()
        by_channel={}
        for row in rows:by_channel.setdefault(row['channel_id'],{})[row['send_nonce']]=dict(row)
        for channel_id,nonces in by_channel.items():
            channel=self.get_channel(int(channel_id))
            if not channel:
                self.store.log(f'uncertain_recovery_unavailable:{uuid.uuid4().hex}','uncertain_recovery_unavailable',channel_id=channel_id,payload={'error':'channel_unavailable','count':len(nonces)})
                continue
            try:
                async for msg in channel.history(limit=100):
                    nonce=str(msg.nonce) if msg.nonce is not None else None
                    if msg.author.id==self.user.id and nonce in nonces:
                        item=nonces[nonce]
                        if self.queue.reconcile_sent(item['id'],msg.id,sent_at=msg.created_at.timestamp()):
                            created=msg.created_at.timestamp()
                            self.store.message_event(kind='create',message_id=msg.id,guild_id=item['guild_id'],channel_id=item['channel_id'],author_id=self.user.id,content=msg.content,created_at=created,changed_at=created,metadata={'author_bot':True})
                            self.recent_bot[int(channel_id)]={'message_id':str(msg.id),'target_user_id':item['author_id'],'created_at':created,'branch_id':item['branch_id'],'content':msg.content}
                            if item['reason'] not in ('observe','welcome') and self.settings.memory_enabled:self.profiles.outgoing(item['guild_id'],item['author_id'],created,message_id=msg.id)
                            if self.flags.enabled(item['guild_id'],item['channel_id'],'reactions'):
                                for emoji in (self.settings.positive_emoji,self.settings.negative_emoji):
                                    try:await msg.add_reaction(emoji)
                                    except discord.HTTPException as exc:
                                        self.store.log(f'reaction_seed_error:{uuid.uuid4().hex}','reaction_seed_error',guild_id=item['guild_id'],channel_id=channel_id,message_id=str(msg.id),payload={'emoji':emoji,'error':type(exc).__name__})
                                        log.warning('Unable to restore feedback reaction')
            except discord.HTTPException as exc:
                self.store.log(f'uncertain_recovery_unavailable:{uuid.uuid4().hex}','uncertain_recovery_unavailable',channel_id=channel_id,payload={'error':type(exc).__name__,'count':len(nonces)})
                log.warning('Uncertain-send recovery unavailable: %s',type(exc).__name__)

    @tasks.loop(minutes=30)
    async def recovery_worker(self):await self.recover_uncertain()

    @recovery_worker.before_loop
    async def before_recovery_worker(self):await self.wait_until_ready()

    @tasks.loop(minutes=10)
    async def quota_worker(self):
        try:await self.gateway.refresh_quota()
        except ModelError as exc:log.warning('%s',exc)

    @quota_worker.before_loop
    async def before_quota_worker(self):await self.wait_until_ready()

    @tasks.loop(hours=24)
    async def adaptation_worker(self):
        try:
            with self.store.lock:running=self.store.db.execute("SELECT 1 FROM prompt_experiments WHERE state='running'").fetchone()
            if running:return
            version=await self.adaptation.draft_from_feedback(self.gateway,positive=self.settings.positive_emoji,negative=self.settings.negative_emoji)
            log.info('Learned guidance proposal %s created for review',version)
        except (BudgetExceeded,ModelError,ValueError,TypeError,json.JSONDecodeError) as exc:
            log.info('No guidance proposal: %s',exc)

    @adaptation_worker.before_loop
    async def before_adaptation_worker(self):await self.wait_until_ready()

    @tasks.loop(minutes=30)
    async def experiment_worker(self):
        with self.store.lock:row=self.store.db.execute("SELECT id FROM prompt_experiments WHERE state='running'").fetchone()
        if row:
            try:
                outcome=self.experiments.assess(row['id'],auto_promote=self.settings.auto_promote,positive=self.settings.positive_emoji,negative=self.settings.negative_emoji)
                log.info('Guidance experiment %s: %s',row['id'],outcome)
            except ValueError as exc:log.warning('Guidance experiment not assessed: %s',type(exc).__name__)

    @experiment_worker.before_loop
    async def before_experiment_worker(self):await self.wait_until_ready()

    @tasks.loop(minutes=30)
    async def feedback_worker(self):
        with self.store.lock:
            cursor=self.store.db.execute("SELECT value FROM background_cursors WHERE name='feedback'").fetchone()
            last=cursor['value'] if cursor else 0
            rows=self.store.db.execute("SELECT id,channel_id,response_message_id FROM queue WHERE status='sent' AND response_message_id IS NOT NULL AND id>? ORDER BY id LIMIT 40",(last,)).fetchall()
        if not rows:
            with self.store.transaction() as db:db.execute("INSERT INTO background_cursors(name,value) VALUES('feedback',0) ON CONFLICT(name) DO UPDATE SET value=0")
            return
        ledger=Feedback(self.store,self.user.id,self.settings.positive_emoji,self.settings.negative_emoji)
        for row in rows:
            channel=self.get_channel(int(row['channel_id']))
            if not channel:
                self.store.log(f'feedback_reconcile_error:{uuid.uuid4().hex}','feedback_reconcile_error',channel_id=row['channel_id'],message_id=row['response_message_id'],payload={'error':'channel_unavailable'})
                continue
            started=time.time();snapshot=[]
            try:
                message=await channel.fetch_message(int(row['response_message_id']))
                seeded=set()
                for reaction in message.reactions:
                    emoji=str(reaction.emoji)
                    if emoji not in (ledger.positive,ledger.negative):continue
                    if reaction.me:seeded.add(emoji)
                    async for user in reaction.users():
                        if not user.bot:snapshot.append((str(user.id),emoji))
                ledger.reconcile(row['response_message_id'],snapshot,snapshot_started=started)
                if self.flags.enabled(message.guild.id,row['channel_id'],'reactions'):
                    for emoji in (ledger.positive,ledger.negative):
                        if emoji not in seeded:
                            try:await message.add_reaction(emoji)
                            except discord.HTTPException as exc:
                                self.store.log(f'reaction_seed_error:{uuid.uuid4().hex}','reaction_seed_error',guild_id=str(message.guild.id),channel_id=row['channel_id'],message_id=row['response_message_id'],payload={'emoji':emoji,'error':type(exc).__name__})
                                log.warning('Unable to restore feedback reaction')
            except discord.HTTPException as exc:
                self.store.log(f'feedback_reconcile_error:{uuid.uuid4().hex}','feedback_reconcile_error',channel_id=row['channel_id'],message_id=row['response_message_id'],payload={'error':type(exc).__name__})
                log.warning('Feedback reconciliation unavailable: %s',type(exc).__name__)
        with self.store.transaction() as db:
            db.execute("INSERT INTO background_cursors(name,value) VALUES('feedback',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(rows[-1]['id'],))

    @feedback_worker.before_loop
    async def before_feedback_worker(self):await self.wait_until_ready()

    @tasks.loop(minutes=30)
    async def memory_worker(self):
        try:await self.extractor.run_batch(max_wait=self.settings.memory_batch_max_wait)
        except (BudgetExceeded,ModelError,ValueError,TypeError,json.JSONDecodeError) as exc:log.warning('Memory batch failed: %s',exc)

    @memory_worker.before_loop
    async def before_memory_worker(self):await self.wait_until_ready()

    @worker.before_loop
    async def before_worker(self):await self.wait_until_ready()

    async def on_raw_reaction_add(self,payload):await self._reaction(payload,True)
    async def on_raw_reaction_remove(self,payload):await self._reaction(payload,False)
    async def on_raw_reaction_clear(self,payload):
        await self._clear_reactions(payload,None)

    async def on_raw_reaction_clear_emoji(self,payload):
        await self._clear_reactions(payload,str(payload.emoji))

    async def _clear_reactions(self,payload,emoji):
        if not self.user or payload.channel_id not in self.settings.channels:return
        with self.store.lock:row=self.store.db.execute("SELECT 1 FROM queue WHERE response_message_id=? AND status='sent'",(str(payload.message_id),)).fetchone()
        if row:Feedback(self.store,self.user.id,self.settings.positive_emoji,self.settings.negative_emoji).clear(payload.message_id,emoji)

    async def _reaction(self,payload,added):
        if not self.user or payload.channel_id not in self.settings.channels:return
        if str(payload.user_id)==str(self.user.id) or str(payload.emoji) not in (self.settings.positive_emoji,self.settings.negative_emoji):return
        with self.store.lock:row=self.store.db.execute("SELECT 1 FROM queue WHERE response_message_id=? AND status='sent'",(str(payload.message_id),)).fetchone()
        if not row:return
        guild=self.get_guild(payload.guild_id) if payload.guild_id else None
        member=getattr(payload,'member',None) or (guild.get_member(payload.user_id) if guild else None)
        if member is not None and member.bot:return
        if added and member is None:
            # Do not guess whether an uncached reactor is a person. A complete
            # reaction snapshot can verify and import it later.
            Feedback(self.store,self.user.id,self.settings.positive_emoji,self.settings.negative_emoji).mark_unverified(payload.message_id,payload.user_id,str(payload.emoji))
            self.store.log(f'feedback_voter_unverified:{uuid.uuid4().hex}','feedback_voter_unverified',guild_id=str(payload.guild_id) if payload.guild_id else None,channel_id=str(payload.channel_id),message_id=str(payload.message_id),author_id=str(payload.user_id),payload={'emoji':str(payload.emoji)})
            return
        Feedback(self.store,self.user.id,self.settings.positive_emoji,self.settings.negative_emoji).update(payload.message_id,payload.user_id,str(payload.emoji),added)


def main():
    from dotenv import load_dotenv
    load_dotenv(os.getenv('VINCENT_ENV_FILE','.env'));logging.basicConfig(level=logging.INFO)
    settings=Settings.from_env();settings.validate_runtime()
    Vincent(settings).run(settings.token,log_handler=None)

if __name__=='__main__':main()
