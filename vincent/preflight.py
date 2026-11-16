"""Offline startup checks. No network calls or credential values are printed."""
import argparse,importlib.metadata,json,os,sqlite3,tempfile
from pathlib import Path
from dotenv import load_dotenv
from .config import Settings
from .store import Store


def check(dotenv_path='.env'):
    if dotenv_path:load_dotenv(dotenv_path)
    warnings=[];errors=[]
    try:settings=Settings.from_env()
    except (ValueError,TypeError) as exc:return {'ready':False,'errors':['Invalid configuration: '+type(exc).__name__],'warnings':[]}
    if not settings.token:errors.append('DISCORD_TOKEN is missing')
    if not settings.api_key:errors.append('OPENROUTER_API_KEY is missing')
    if not settings.channels:errors.append('CHANNEL_IDS is empty')
    if not settings.owner_id:warnings.append('VINCENT_OWNER_ID is unset; global memory administration is unavailable')
    if settings.unsolicited_enabled:warnings.append('Unsolicited participation is enabled')
    if settings.auto_promote:warnings.append('Automatic canary promotion is enabled')
    for package in ('discord.py','httpx','python-dotenv'):
        try:importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:errors.append(f'{package} is not installed')
    # Validate migration syntax against a disposable database, not the target database.
    try:
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'test.sqlite');count=store.db.execute('SELECT count(*) FROM schema_migrations').fetchone()[0];store.close()
    except (OSError,sqlite3.Error,ValueError) as exc:
        errors.append('Disposable migration check failed: '+type(exc).__name__);count=0
    target=Path(settings.database)
    if target.exists():
        try:
            db=sqlite3.connect(target.resolve().as_uri()+'?mode=ro',uri=True)
            try:integrity=db.execute('PRAGMA quick_check').fetchone()[0]
            finally:db.close()
            if integrity!='ok':errors.append('Target database integrity check failed')
        except sqlite3.Error as exc:errors.append('Target database check failed: '+type(exc).__name__)
    else:warnings.append('Target database does not yet exist; it will be created on startup')
    return {'ready':not errors,'errors':errors,'warnings':warnings,'model':settings.model,'channel_count':len(settings.channels),'daily_limit':settings.max_daily,'minute_limit':settings.max_per_minute,'provider_reset_config':{'timezone':settings.reset_timezone,'hour':settings.reset_hour},'worker_concurrency':settings.worker_concurrency,'unsolicited_enabled':settings.unsolicited_enabled,'auto_promotion_enabled':settings.auto_promote,'migration_count':count,'network_checks_performed':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--dotenv',default='.env');args=p.parse_args();result=check(args.dotenv);print(json.dumps(result,indent=2));return 0 if result['ready'] else 1
if __name__=='__main__':raise SystemExit(main())
