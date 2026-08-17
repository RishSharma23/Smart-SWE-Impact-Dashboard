"""Configuration loading and window resolution.

Everything the pipeline treats as tunable lives in ``config/*.yaml``.  This
module resolves those files plus environment overrides into one frozen
``Settings`` object, which is serialised verbatim into the run manifest so a
reader can reconstruct exactly what a run was told to do.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"

UTC = dt.timezone.utc


def _read_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"missing config file: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def parse_ts(value: str | dt.datetime | None) -> dt.datetime | None:
    """Parse an ISO-8601 timestamp into a timezone-aware UTC datetime.

    GitHub emits a trailing ``Z``; ``fromisoformat`` before 3.11 rejects it and
    we normalise regardless so every timestamp in the pipeline is UTC-aware.
    Naive input is *assumed* UTC rather than silently localised.
    """
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(text)
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Window:
    start: dt.datetime
    end: dt.datetime
    lookback_days: int
    primary_cohort: str
    also_ingest: tuple[str, ...]
    slice_days: int
    fetch_context_outside_window: bool
    max_context_artifacts: int

    def contains(self, when: dt.datetime | None) -> bool:
        if when is None:
            return False
        return self.start <= when < self.end

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": iso(self.start),
            "end": iso(self.end),
            "lookback_days": self.lookback_days,
            "primary_cohort": self.primary_cohort,
            "also_ingest": list(self.also_ingest),
            "slice_days": self.slice_days,
        }


@dataclass(frozen=True)
class Settings:
    repository: dict[str, Any]
    clone: dict[str, Any]
    snapshot_files: tuple[str, ...]
    license_areas: tuple[dict[str, Any], ...]
    window: Window
    components: dict[str, Any]
    bots: dict[str, Any]
    generated: dict[str, Any]
    features: dict[str, Any]
    data_dir: Path
    project_root: Path = field(default=PROJECT_ROOT)

    def param(self, family: str, key: str, default: Any = None) -> Any:
        """Read a tunable from config/feature_versions.yaml::parameters.

        These values change derived feature *values*, so the owning feature
        version must be bumped alongside any edit -- a quality gate asserts the
        YAML and versions.py agree.
        """
        return ((self.features.get("parameters") or {}).get(family) or {}).get(
            key, default
        )

    # -- convenience accessors ------------------------------------------
    @property
    def owner(self) -> str:
        return self.repository["owner"]

    @property
    def name(self) -> str:
        return self.repository["name"]

    @property
    def qualifier(self) -> str:
        return self.repository["qualifier"]

    @property
    def default_branch(self) -> str:
        return self.repository["default_branch"]

    @property
    def clone_path(self) -> Path:
        return (self.project_root / self.clone["path"]).resolve()

    def path(self, *parts: str) -> Path:
        return self.data_dir.joinpath(*parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "clone": self.clone,
            "snapshot_files": list(self.snapshot_files),
            "license_areas": [dict(a) for a in self.license_areas],
            "window": self.window.as_dict(),
            "data_dir": str(self.data_dir.relative_to(self.project_root)),
        }


def resolve_window(
    cfg: dict[str, Any],
    *,
    start_override: str | None = None,
    end_override: str | None = None,
    now: dt.datetime | None = None,
) -> Window:
    """Resolve the analysis window.

    Default end is the extraction-start instant; default start is
    ``lookback_days`` before the most recent *complete* UTC day.  Anchoring the
    start on a UTC midnight (rather than on "now minus 90*24h") is what makes
    the boundary tests meaningful and keeps two runs on the same day
    comparable.
    """
    w = cfg.get("window", {})
    d = cfg.get("discovery", {})
    c = cfg.get("context", {})

    now = now or dt.datetime.now(UTC)
    lookback = int(w.get("lookback_days", 90))

    end_raw = end_override or os.getenv("IMPACT_WINDOW_END") or w.get("end")
    start_raw = start_override or os.getenv("IMPACT_WINDOW_START") or w.get("start")

    end = parse_ts(end_raw) or now
    if start_raw:
        start = parse_ts(start_raw)
    else:
        last_complete_midnight = now.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start = last_complete_midnight - dt.timedelta(days=lookback)

    assert start is not None
    if start >= end:
        raise ValueError(f"window start {iso(start)} must precede end {iso(end)}")

    return Window(
        start=start,
        end=end,
        lookback_days=lookback,
        primary_cohort=str(w.get("primary_cohort", "merged")),
        also_ingest=tuple(w.get("also_ingest", ["created", "updated"])),
        slice_days=int(d.get("slice_days", 3)),
        fetch_context_outside_window=bool(
            c.get("fetch_referenced_outside_window", True)
        ),
        max_context_artifacts=int(c.get("max_context_artifacts", 4000)),
    )


def load_settings(
    *,
    window_start: str | None = None,
    window_end: str | None = None,
    now: dt.datetime | None = None,
) -> Settings:
    repo_cfg = _read_yaml("repository.yaml")
    window_cfg = _read_yaml("window.yaml")

    data_dir = Path(os.getenv("IMPACT_DATA_DIR", PROJECT_ROOT / "data"))
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir

    return Settings(
        repository=repo_cfg["repository"],
        clone=repo_cfg.get("clone", {}),
        snapshot_files=tuple(repo_cfg.get("snapshot_files", [])),
        license_areas=tuple(repo_cfg.get("license_areas", [])),
        window=resolve_window(
            window_cfg, start_override=window_start, end_override=window_end, now=now
        ),
        components=_read_yaml("components.yaml"),
        bots=_read_yaml("bots.yaml"),
        generated=_read_yaml("generated_files.yaml"),
        features=_read_yaml("feature_versions.yaml"),
        data_dir=data_dir,
    )


def github_token() -> str:
    """Resolve the GitHub token without ever printing it.

    Order: ``GITHUB_TOKEN`` env -> ``.env`` file -> authenticated ``gh`` CLI.
    A read-only fine-grained token is sufficient; no write scope is used.
    """
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        return token

    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GITHUB_TOKEN=") and not line.startswith("#"):
                token = line.split("=", 1)[1].strip().strip("'\"")
                if token:
                    return token

    import subprocess

    try:
        out = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=15
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass

    raise RuntimeError(
        "No GitHub token available. Set GITHUB_TOKEN in .env (fine-grained token, "
        "public repositories, read-only) or run `gh auth login`. "
        "See .env.example."
    )
