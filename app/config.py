import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationError(ValueError):
    """A message known to contain no secrets, safe to display."""


@dataclass(frozen=True)
class Config:
    executable: str
    headless: bool = False
    login_url: str = "https://login.netcoresmartech.com/"
    expected_host: str = "tprappin2r.netcoresmartech.com"
    max_actions: int = 30
    max_model_turns: int = 6
    timeout_seconds: int = 180
    max_handoffs: int = 2
    live_login_enabled: bool = False
    max_login_submissions: int = 1
    teacher_live_replay_enabled: bool = False
    evidence_file: str | None = None
    learning_runs: int = 3
    live_comparison_enabled: bool = False
    login_poll_seconds: float = .5
    login_stable_seconds: float = 1.
    login_ready_seconds: float = 30.
    login_ready_max_seconds: float = 45.

    def __post_init__(self):
        if not (0 < self.login_poll_seconds <= self.login_stable_seconds <
                self.login_ready_seconds <= self.login_ready_max_seconds <= 45):
            raise ConfigurationError('Invalid bounded Login readiness limits')
        if self.learning_runs != 3:
            raise ConfigurationError('learning_runs=3 is required for graduation')
        if self.max_login_submissions != 1:
            raise ConfigurationError("NETCORE_MAX_LOGIN_SUBMISSIONS=1 is required")
        if self.teacher_live_replay_enabled:
            raise ConfigurationError("TEACHER_LIVE_REPLAY_ENABLED must be false; evaluation is synthetic-only")

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env")
        path = os.getenv("CHROMIUM_EXECUTABLE_PATH", "")
        if not Path(path).is_file():
            raise ConfigurationError("Set CHROMIUM_EXECUTABLE_PATH to the installed Chromium executable")
        cfg = cls(path, os.getenv("HEADLESS", "false").lower() == "true",
                  os.getenv("NETCORE_LOGIN_URL", cls.login_url),
                  os.getenv("NETCORE_EXPECTED_HOST", cls.expected_host),
                  live_login_enabled=read_boolean("NETCORE_LIVE_LOGIN_ENABLED", False),
                  max_login_submissions=int(os.getenv("NETCORE_MAX_LOGIN_SUBMISSIONS", "1")),
                  teacher_live_replay_enabled=read_boolean("TEACHER_LIVE_REPLAY_ENABLED", False),
                  evidence_file=os.getenv("NETCORE_EVIDENCE_FILE") or None,
                  live_comparison_enabled=read_boolean('NETCORE_LIVE_COMPARISON_ENABLED',False))
        if cfg.login_url != cls.login_url or cfg.expected_host != cls.expected_host:
            raise ConfigurationError("Module 1 permits only the reviewed Netcore entry points")
        return cfg


class OriginPolicy:
    def __init__(self, origins: set[str]):
        self.origins = origins

    def permits(self, url: str) -> bool:
        try:
            u = urlsplit(url)
            return not u.username and not u.password and f"{u.scheme}://{u.netloc}" in self.origins
        except ValueError:
            return False


def read_boolean(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value not in {"true", "false"}:
        raise ConfigurationError(f"{name} must be true or false")
    return value == "true"
