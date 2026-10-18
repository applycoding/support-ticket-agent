"""Runtime settings. The eval harness pins the demo clock and the refund limit."""

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from support_ops.models import Provider

DEMO_TODAY = date(2026, 4, 10)
AUTO_APPROVE_LIMIT_CENTS = 7_500
COURTESY_CENTS = 1_500


def repo_root() -> Path:
    """Find the repository root by walking up to pyproject.toml."""
    override = os.environ.get("SUPPORT_OPS_ROOT", "").strip()
    if override:
        return Path(override)
    start = Path(__file__).resolve()
    for candidate in (start.parent, *start.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "data" / "policies").is_dir():
            return candidate
    return Path.cwd()


def _blank(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


@dataclass(frozen=True, slots=True)
class Settings:
    """Paths, provider selection, and the refund policy constants."""

    root: Path
    db_path: Path
    policy_dir: Path
    llm_provider: str
    openai_api_key: str | None
    anthropic_api_key: str | None
    openai_model: str
    anthropic_model: str
    auto_approve_limit_cents: int
    courtesy_cents: int
    refund_window_days: int
    defect_window_days: int
    shipping_courtesy_after_days: int
    shipping_lost_after_days: int
    today: date

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from the environment. The default provider is mock."""
        root = repo_root()
        provider_name = os.environ.get("SUPPORT_OPS_LLM_PROVIDER", "mock").strip().lower() or "mock"
        Provider(provider_name)
        today_raw = os.environ.get("SUPPORT_OPS_TODAY", "").strip()
        today = date.fromisoformat(today_raw) if today_raw else DEMO_TODAY
        db_raw = os.environ.get("SUPPORT_OPS_DB", "").strip()
        db_path = Path(db_raw) if db_raw else root / "data" / "support_ops.db"
        openai_model = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
        anthropic_model = (
            os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-3-5-sonnet-latest"
        )
        return cls(
            root=root,
            db_path=db_path,
            policy_dir=root / "data" / "policies",
            llm_provider=provider_name,
            openai_api_key=_blank("OPENAI_API_KEY"),
            anthropic_api_key=_blank("ANTHROPIC_API_KEY"),
            openai_model=openai_model,
            anthropic_model=anthropic_model,
            auto_approve_limit_cents=AUTO_APPROVE_LIMIT_CENTS,
            courtesy_cents=COURTESY_CENTS,
            refund_window_days=30,
            defect_window_days=90,
            shipping_courtesy_after_days=7,
            shipping_lost_after_days=14,
            today=today,
        )


def make_settings(db_path: Path) -> Settings:
    """Settings for tests and the eval harness. The clock stays on the demo date."""
    root = repo_root()
    return Settings(
        root=root,
        db_path=db_path,
        policy_dir=root / "data" / "policies",
        llm_provider=Provider.MOCK.value,
        openai_api_key=None,
        anthropic_api_key=None,
        openai_model="gpt-4o-mini",
        anthropic_model="claude-3-5-sonnet-latest",
        auto_approve_limit_cents=AUTO_APPROVE_LIMIT_CENTS,
        courtesy_cents=COURTESY_CENTS,
        refund_window_days=30,
        defect_window_days=90,
        shipping_courtesy_after_days=7,
        shipping_lost_after_days=14,
        today=DEMO_TODAY,
    )
