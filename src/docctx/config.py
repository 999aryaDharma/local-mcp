"""
Configuration loading and defaults for docctx.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from docctx.paths import get_docctx_home


@dataclass
class RetrievalBoostsConfig:
    heading_exact: float = 1.5
    code_match: float = 1.3
    trust_tier_official: float = 1.2
    heading_title_exact: float = 1.1


@dataclass
class RetrievalConfig:
    mode: str = "hybrid" # "keyword", "semantic", "hybrid"
    rrf_k: int = 60
    floor_score: float = 3.0
    confidence_cutoff: float = 6.0
    default_limit: int = 5
    max_limit: int = 10
    default_response_mode: str = "standard"
    boosts: RetrievalBoostsConfig = field(default_factory=RetrievalBoostsConfig)
    cache_enabled: bool = True
    cache_max_size: int = 100
    cache_ttl_seconds: float = 300.0


@dataclass
class EmbeddingsConfig:
    model: str = "all-MiniLM-L6-v2"
    provider: str = "local" # "local", "openai"
    dimension: int = 384


@dataclass
class ChunkingConfig:
    target_tokens: int = 400
    max_tokens: int = 800
    min_tokens: int = 80


@dataclass
class IngestionConfig:
    rate_limit_rps: float = 1.0
    max_pages: int = 50
    max_depth: int = 2
    respect_robots: bool = True
    request_timeout_sec: int = 30
    user_agent: str = "docctx/1.0 (+https://github.com/you/docctx)"
    llm_summarize: bool = True
    llm_provider: str = "gemini"
    llm_model: str = "gemini-1.5-flash"
    api_key_env: str = "GEMINI_API_KEY"


@dataclass
class GraphConfig:
    extract_entities: bool = True


@dataclass
class FreshnessConfig:
    fresh_days: int = 7
    stale_days: int = 30


@dataclass
class StorageConfig:
    home: str = "~/.docctx"
    cache_enabled: bool = True


@dataclass
class DocctxConfig:
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    ingestion: IngestionConfig = field(default_factory=IngestionConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    freshness: FreshnessConfig = field(default_factory=FreshnessConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)


def _load_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        raise ValueError(f"Invalid config file at {path}: {e}") from e


def load_config() -> DocctxConfig:
    """Load configuration from config.toml, with defaults for missing keys."""
    home = get_docctx_home()
    config_path = home / "config.toml"
    raw = _load_toml(config_path)

    cfg = DocctxConfig()

    r = raw.get("retrieval", {})
    if r:
        boosts_raw = r.get("boosts", {})
        cfg.retrieval = RetrievalConfig(
            mode=r.get("mode", cfg.retrieval.mode),
            rrf_k=r.get("rrf_k", cfg.retrieval.rrf_k),
            floor_score=r.get("floor_score", cfg.retrieval.floor_score),
            confidence_cutoff=r.get(
                "confidence_cutoff", cfg.retrieval.confidence_cutoff
            ),
            default_limit=r.get("default_limit", cfg.retrieval.default_limit),
            max_limit=r.get("max_limit", cfg.retrieval.max_limit),
            default_response_mode=r.get(
                "default_response_mode", cfg.retrieval.default_response_mode
            ),
            boosts=RetrievalBoostsConfig(
                heading_exact=boosts_raw.get(
                    "heading_exact", cfg.retrieval.boosts.heading_exact
                ),
                code_match=boosts_raw.get("code_match", cfg.retrieval.boosts.code_match),
                trust_tier_official=boosts_raw.get(
                    "trust_tier_official", cfg.retrieval.boosts.trust_tier_official
                ),
                heading_title_exact=boosts_raw.get(
                    "heading_title_exact", cfg.retrieval.boosts.heading_title_exact
                ),
            ),
        )

    c = raw.get("chunking", {})
    if c:
        cfg.chunking = ChunkingConfig(
            target_tokens=c.get("target_tokens", cfg.chunking.target_tokens),
            max_tokens=c.get("max_tokens", cfg.chunking.max_tokens),
            min_tokens=c.get("min_tokens", cfg.chunking.min_tokens),
        )

    i = raw.get("ingestion", {})
    if i:
        cfg.ingestion = IngestionConfig(
            rate_limit_rps=i.get("rate_limit_rps", cfg.ingestion.rate_limit_rps),
            max_pages=i.get("max_pages", cfg.ingestion.max_pages),
            max_depth=i.get("max_depth", cfg.ingestion.max_depth),
            respect_robots=i.get("respect_robots", cfg.ingestion.respect_robots),
            request_timeout_sec=i.get(
                "request_timeout_sec", cfg.ingestion.request_timeout_sec
            ),
            user_agent=i.get("user_agent", cfg.ingestion.user_agent),
            llm_summarize=i.get("llm_summarize", cfg.ingestion.llm_summarize),
            llm_provider=i.get("llm_provider", cfg.ingestion.llm_provider),
            llm_model=i.get("llm_model", cfg.ingestion.llm_model),
            api_key_env=i.get("api_key_env", cfg.ingestion.api_key_env),
        )

    e = raw.get("embeddings", {})
    if e:
        cfg.embeddings = EmbeddingsConfig(
            model=e.get("model", cfg.embeddings.model),
            provider=e.get("provider", cfg.embeddings.provider),
            dimension=e.get("dimension", cfg.embeddings.dimension),
        )

    g = raw.get("graph", {})
    if g:
        cfg.graph = GraphConfig(
            extract_entities=g.get("extract_entities", cfg.graph.extract_entities),
        )

    fr = raw.get("freshness", {})
    if fr:
        cfg.freshness = FreshnessConfig(
            fresh_days=fr.get("fresh_days", cfg.freshness.fresh_days),
            stale_days=fr.get("stale_days", cfg.freshness.stale_days),
        )

    st = raw.get("storage", {})
    if st:
        cfg.storage = StorageConfig(
            home=st.get("home", cfg.storage.home),
            cache_enabled=st.get("cache_enabled", cfg.storage.cache_enabled),
        )

    return cfg


def write_default_config(config_path: Path) -> None:
    """Write a default config.toml if it doesn't exist."""
    if config_path.exists():
        return

    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = """\
[retrieval]
floor_score = 3.0
confidence_cutoff = 6.0
default_limit = 5
max_limit = 10
default_response_mode = "standard"

[retrieval.boosts]
heading_exact = 1.5
code_match = 1.3
trust_tier_official = 1.2
heading_title_exact = 1.1

[chunking]
target_tokens = 400
max_tokens = 800
min_tokens = 80

[ingestion]
rate_limit_rps = 1.0
max_pages = 50
max_depth = 2
respect_robots = true
request_timeout_sec = 30
user_agent = "docctx/1.0 (+https://github.com/you/docctx)"

[freshness]
fresh_days = 7
stale_days = 30

[storage]
home = "~/.docctx"
cache_enabled = true
"""
    config_path.write_text(content, encoding="utf-8")
