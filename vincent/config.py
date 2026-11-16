"""Validated configuration for the new Vincent process."""
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo
import os


def _integer(name, default, minimum=0):
    value = int(os.environ.get(name, default))
    if value < minimum:
        raise ValueError(f'{name} must be at least {minimum}')
    return value


def _boolean(name, default=False):
    value = os.environ.get(name, str(default)).lower().strip()
    if value not in {'true', 'false', 'yes', 'no', '1', '0', 'on', 'off'}:
        raise ValueError(f'{name} must be a boolean')
    return value in {'true', 'yes', '1', 'on'}


@dataclass(frozen=True)
class Settings:
    database: Path = Path('data/vincent-v2.sqlite3')
    token: str = field(default='', repr=False)
    api_key: str = field(default='', repr=False)
    model: str = 'openrouter/free'
    channels: frozenset[int] = frozenset()
    owner_id: int = 0
    welcome_channel_id: int = 0
    max_daily: int = 1000
    max_per_minute: int = 18
    max_output_tokens: int = 16000
    background_max_output_tokens: int = 32000
    worker_concurrency: int = 3
    max_backlog: int = 200
    group_direct: bool = True
    background_ceiling: int = 250
    observe_limit: int = 120
    memory_limit: int = 80
    memory_batch_max_wait: int = 21600
    memory_interval_minutes: int = 10
    adaptation_limit: int = 50
    reset_timezone: str = 'UTC'
    reset_hour: int = 0
    unsolicited_enabled: bool = False
    quiet_timezone: str = 'Europe/Prague'
    quiet_start: int = 23
    quiet_end: int = 9
    reactions_enabled: bool = True
    memory_enabled: bool = True
    adaptation_enabled: bool = True
    adaptation_min_votes: int = 10
    adaptation_min_raters: int = 3
    auto_promote: bool = False
    positive_emoji: str = '👍'
    negative_emoji: str = '👎'

    def __post_init__(self):
        if self.max_daily < 1 or self.max_per_minute < 1 or self.max_backlog < 1 or self.max_output_tokens < 1 or self.background_max_output_tokens < 1 or not 1 <= self.worker_concurrency <= 10:
            raise ValueError('Request limits must be positive')
        if not 0 <= self.background_ceiling <= self.max_daily:
            raise ValueError('Background ceiling must fit within daily limit')
        if min(self.observe_limit, self.memory_limit, self.adaptation_limit, self.memory_batch_max_wait) < 0 or self.memory_interval_minutes < 1:
            raise ValueError('Category limits cannot be negative')
        if not 10<=self.adaptation_min_votes<=1000 or not 3<=self.adaptation_min_raters<=self.adaptation_min_votes:
            raise ValueError('Adaptation thresholds require 10–1000 votes and at least 3 raters')
        if not 0 <= self.reset_hour <= 23:
            raise ValueError('Reset hour must be 0–23')
        if self.welcome_channel_id and self.welcome_channel_id not in self.channels:
            raise ValueError('WELCOME_CHANNEL_ID must be included in CHANNEL_IDS')
        ZoneInfo(self.reset_timezone)
        ZoneInfo(self.quiet_timezone)
        if not 0 <= self.quiet_start <= 23 or not 0 <= self.quiet_end <= 23:
            raise ValueError('Quiet hours must be between 0 and 23')
        if not self.positive_emoji or not self.negative_emoji:
            raise ValueError('Feedback emojis cannot be empty')
        if self.positive_emoji == self.negative_emoji:
            raise ValueError('Feedback emojis must differ')

    @classmethod
    def from_env(cls):
        channels = frozenset(int(x.strip()) for x in os.getenv('CHANNEL_IDS', '').split(',') if x.strip())
        cap = _integer('VINCENT_MAX_DAILY', 1000, 1)
        return cls(database=Path(os.getenv('VINCENT_DATABASE', 'data/vincent-v2.sqlite3')),
                   token=os.getenv('DISCORD_TOKEN', ''), api_key=os.getenv('OPENROUTER_API_KEY', ''),
                   model=os.getenv('OPENROUTER_MODEL', 'openrouter/free'), channels=channels,
                   owner_id=_integer('VINCENT_OWNER_ID', 0), welcome_channel_id=_integer('WELCOME_CHANNEL_ID',0), max_daily=cap,
                   max_per_minute=_integer('VINCENT_MAX_PER_MINUTE', 18, 1),
                   max_output_tokens=_integer('VINCENT_MAX_OUTPUT_TOKENS',16000,1),
                   background_max_output_tokens=_integer('VINCENT_BACKGROUND_MAX_OUTPUT_TOKENS',32000,1),
                   worker_concurrency=_integer('VINCENT_WORKER_CONCURRENCY', 3, 1),
                   max_backlog=_integer('VINCENT_MAX_BACKLOG', 200, 1),
                   group_direct=_boolean('VINCENT_GROUP_DIRECT', True),
                   background_ceiling=_integer('VINCENT_BACKGROUND_CEILING', min(250, cap)),
                   observe_limit=_integer('VINCENT_OBSERVE_LIMIT', 120),
                   memory_limit=_integer('VINCENT_MEMORY_LIMIT', 80),
                   memory_batch_max_wait=_integer('VINCENT_MEMORY_BATCH_MAX_WAIT', 21600),
                   memory_interval_minutes=_integer('VINCENT_MEMORY_INTERVAL_MINUTES',10,1),
                   adaptation_limit=_integer('VINCENT_ADAPTATION_LIMIT', 50),
                   reset_timezone=os.getenv('VINCENT_RESET_TIMEZONE', 'UTC'),
                   reset_hour=_integer('VINCENT_RESET_HOUR', 0),
                   unsolicited_enabled=_boolean('VINCENT_UNSOLICITED_ENABLED'),
                   quiet_timezone=os.getenv('VINCENT_QUIET_TIMEZONE','Europe/Prague'),
                   quiet_start=_integer('VINCENT_QUIET_START',23),
                   quiet_end=_integer('VINCENT_QUIET_END',9),
                   reactions_enabled=_boolean('VINCENT_REACTIONS_ENABLED', True),
                   memory_enabled=_boolean('VINCENT_MEMORY_ENABLED', True),
                   adaptation_enabled=_boolean('VINCENT_ADAPTATION_ENABLED', True),
                   adaptation_min_votes=_integer('VINCENT_ADAPTATION_MIN_VOTES', 10, 10),
                   adaptation_min_raters=_integer('VINCENT_ADAPTATION_MIN_RATERS', 3, 3),
                   auto_promote=_boolean('VINCENT_AUTO_PROMOTE', False),
                   positive_emoji=os.getenv('VINCENT_POSITIVE_EMOJI', '👍'),
                   negative_emoji=os.getenv('VINCENT_NEGATIVE_EMOJI', '👎'))

    def validate_runtime(self):
        if not self.token or not self.api_key or not self.channels:
            raise ValueError('DISCORD_TOKEN, OPENROUTER_API_KEY and CHANNEL_IDS are required')
