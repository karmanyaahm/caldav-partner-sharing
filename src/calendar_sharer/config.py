"""Configuration and on-disk locations.

Everything lives outside the repo: the checkout sits on a LUKS volume that is
not mounted at boot, and secrets should not be one `git add -A` away from a
commit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP = "calendar-sharer"


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP


def tokens_path() -> Path:
    return data_dir() / "tokens.json"


def state_path() -> Path:
    return data_dir() / "state.json"


def lock_path() -> Path:
    return data_dir() / "generate.lock"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    fm_user: str
    fm_apppw: str
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket: str
    public_base_url: str
    feed_name: str
    past_days: int
    future_days: int | None

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"

    def url_for(self, token: str) -> str:
        return f"{self.public_base_url.rstrip('/')}/f/{token}.ics"


_FASTMAIL = ("FM_USER", "FM_APPPW")
_R2 = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "PUBLIC_BASE_URL")


def load(env: dict | None = None, need_r2: bool = True) -> Config:
    """Load config. `need_r2=False` for commands that never touch the bucket."""
    env = dict(os.environ if env is None else env)

    # Convenience for running from the checkout; systemd uses EnvironmentFile.
    dotenv = Path(".env")
    if dotenv.is_file():
        for line in dotenv.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key.strip(), value.strip().strip("'\""))

    required = _FASTMAIL + (_R2 if need_r2 else ())
    missing = [k for k in required if not env.get(k)]
    if missing:
        raise ConfigError(
            f"missing config: {', '.join(missing)}\n"
            f"set them in {config_dir() / 'env'} (mode 0600) or the environment"
        )

    return Config(
        fm_user=env["FM_USER"],
        fm_apppw=env["FM_APPPW"],
        r2_account_id=env.get("R2_ACCOUNT_ID", ""),
        r2_access_key_id=env.get("R2_ACCESS_KEY_ID", ""),
        r2_secret_access_key=env.get("R2_SECRET_ACCESS_KEY", ""),
        r2_bucket=env.get("R2_BUCKET", ""),
        public_base_url=env.get("PUBLIC_BASE_URL", ""),
        feed_name=env.get("FEED_NAME") or "Calendar",
        past_days=int(env.get("FEED_PAST_DAYS") or 7),
        # Empty or 0 means no future cutoff; one-time events are then only
        # dropped once they are past.
        future_days=(int(env["FEED_FUTURE_DAYS"]) or None) if env.get("FEED_FUTURE_DAYS") else 28,
    )
