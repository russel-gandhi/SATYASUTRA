"""Typed loader for config/*.yaml. Nothing that can change between machines or runs is written in code (Bible D4)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import model_validator, BaseModel, ConfigDict, Field, ValidationError, field_validator

from .errors import ConfigError

DEFAULT_CONFIG_DIR_ENV = "FORGE_CONFIG_DIR"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceConfig(_Strict):
    id: str
    role: str
    description: str = ""
    probe_url: str
    tier: Literal[1, 2, 3, 4]
    auth_env: str | None = None          # name of the environment variable holding the key, never the key
    auth_param: str | None = None        # query parameter that carries the key, when it goes in the URL
    rate_limit_per_min: int | None = Field(default=None, gt=0)
    cache_ttl_s: int = Field(default=0, ge=0)
    status: Literal["unverified", "reachable", "unreachable", "removed"] = "unverified"
    note: str = ""

    @field_validator("probe_url")
    @classmethod
    def _http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):  # forge: allow-literal scheme check, not a source
            raise ValueError("probe_url must be an http(s) URL")
        return v


class SourcesFile(_Strict):
    sources: list[SourceConfig]

    @field_validator("sources")
    @classmethod
    def _unique(cls, v: list[SourceConfig]) -> list[SourceConfig]:
        ids = [s.id for s in v]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate source ids: {sorted(dupes)}")
        return v


class Budgets(_Strict):
    max_steps_per_claim: int = Field(gt=0)
    max_seconds_per_claim: int = Field(gt=0)
    max_fetches_per_claim: int = Field(gt=0)
    reverse_search_per_day: int = Field(ge=0)


class GeminiPolicy(_Strict):
    api_base: str
    models: list[str] = Field(min_length=1)
    measure_max_calls: int = Field(gt=0)
    measure_timeout_s: int = Field(gt=0)
    retest_attempts: int = Field(gt=0)
    request_timeout_s: float = Field(gt=0)
    max_retries: int = Field(ge=0)               # extra attempts after a 429 that names a short retry delay
    max_retry_wait_s: float = Field(ge=0)        # a longer retry delay than this is reported, not waited out
    requests_per_minute: int | None = None      # filled from docs/gemini_limits.md after a measurement
    measured_on: str = ""


class RatingMap(_Strict):
    """Publisher rating phrases by what they mean for a claim, taken from ratings actually seen (scripts/calibrate_factcheck.py)."""
    refutes: list[str] = Field(default_factory=list)
    supports: list[str] = Field(default_factory=list)
    mixed: list[str] = Field(default_factory=list)        # true in part, or true but missing context
    unproven: list[str] = Field(default_factory=list)     # "no evidence for this": absence of support, which never refutes (D12)

    @model_validator(mode="after")
    def _no_phrase_in_two_groups(self) -> "RatingMap":
        seen: dict[str, str] = {}
        for group in ("refutes", "supports", "mixed", "unproven"):
            for phrase in getattr(self, group):
                key = " ".join(phrase.casefold().split())
                if key in seen and seen[key] != group:
                    raise ValueError(f"rating phrase {phrase!r} is in both {seen[key]} and {group}")
                seen[key] = group
        return self


class VerdictRules(_Strict):
    rating_map: RatingMap
    negations: list[str]                          # a review and a claim that differ in negation may be opposites
    min_publishers_for_high: int = Field(gt=0)
    stale_after_days: int = Field(gt=0)           # policy choice, not a measurement
    related_limit: int = Field(ge=0)


class PolicyFile(_Strict):
    allowed_domains: list[str]
    read_only: bool = True
    budgets: Budgets
    gemini: GeminiPolicy
    verdict_rules: VerdictRules


class ThresholdEntry(_Strict):
    value: float
    model_sha256: str = ""               # empty until a model exists
    calibrated_on: str = ""
    date: str = ""


class ThresholdsFile(_Strict):
    thresholds: dict[str, ThresholdEntry] = Field(default_factory=dict)


class ProbeSettings(_Strict):
    timeout_s: float = Field(gt=0)
    user_agent: str


class IngestSettings(_Strict):
    source_id: str
    ws_url: str
    collections: list[str] = Field(min_length=1)
    post_url_template: str
    langs: list[str] = Field(default_factory=list)       # empty means every language
    keywords: list[str] = Field(default_factory=list)    # empty means no keyword filter
    min_text_chars: int = Field(ge=0)
    skip_replies: bool
    open_timeout_s: float = Field(gt=0)
    idle_timeout_s: float = Field(gt=0)
    reconnect_delay_s: float = Field(gt=0)
    max_reconnect_delay_s: float = Field(gt=0)
    resume_overlap_s: float = Field(ge=0)
    recent_ids_kept: int = Field(gt=0)


class FactCheckSettings(_Strict):
    endpoint: str
    page_size: int = Field(gt=0)
    timeout_s: float = Field(gt=0)


class FastClockSettings(_Strict):
    shingle_words: int = Field(gt=0)
    num_perm: int = Field(gt=0)
    lsh_bands: int = Field(gt=0)             # must divide num_perm; rows per band = num_perm / lsh_bands
    window_s: float = Field(gt=0)            # velocity is posts per minute over this window
    cluster_idle_s: float = Field(gt=0)      # clusters silent this long are forgotten
    max_clusters: int = Field(gt=0)
    result_ttl_s: float = Field(gt=0)
    top_n: int = Field(gt=0)
    worker_enabled: bool                     # start the live worker with the API; off so the API and its tests run with no network
    queue_max: int = Field(gt=0)             # posts waiting for the clock; beyond this the oldest are dropped and counted
    latency_samples: int = Field(gt=0)       # per-post times kept for the median and p95
    repeat_keys_kept: int = Field(gt=0)      # recent text keys remembered to count exact repeats
    example_text_chars: int = Field(gt=0)    # an example post's text is cut to this length in /fastclock/top
    members_kept: int = Field(gt=0)          # earliest posts stored per cluster for the origin and spread trace; later ones are only counted
    member_text_chars: int = Field(gt=0)     # stored text per trace member (a Bluesky post is at most 300 characters)
    trace_buckets: int = Field(gt=0)         # points on the spread timeline
    variants_shown: int = Field(gt=0)        # distinct wordings listed in a trace
    match_min_posts: int = Field(gt=0)       # a pasted claim is matched only to clusters with at least this many posts

    @model_validator(mode="after")
    def _bands_divide(self) -> "FastClockSettings":
        if self.num_perm % self.lsh_bands:
            raise ValueError("lsh_bands must divide num_perm")
        return self


class ExtractSettings(_Strict):
    max_claims_per_post: int = Field(gt=0)


class EscalationSettings(_Strict):
    enabled: bool                            # audit top clusters automatically; off so the API and its tests spend no model calls
    reserve_per_minute: int = Field(ge=0)    # of the Gemini requests per minute, kept free for people calling /verify
    tick_s: float = Field(gt=0)              # how often the escalator looks at the ranking when it has nothing to do
    retry_after_s: float = Field(gt=0)       # a cluster whose audit failed waits this long before it is eligible again
    max_audits_kept: int = Field(gt=0)       # audit results remembered, oldest forgotten first


class TraceSettings(_Strict):
    summary_max_chars: int = Field(gt=0)     # tool-call summaries are cut to this length before they are streamed


class FeedRef(_Strict):
    source_id: str                           # its URL is the source's probe_url in sources.yaml
    publisher: str
    lang: str


class CommonsSettings(_Strict):
    endpoint: str
    thumb_width: int = Field(gt=0)           # images are fetched as thumbnails of this width, to keep downloads small
    sample_size: int = Field(gt=1)           # images drawn for the false-match measurement


class ProvenanceSettings(_Strict):
    db_path: str
    feeds: list[FeedRef] = Field(min_length=1)
    max_image_bytes: int = Field(gt=0)
    min_side_px: int = Field(gt=0)           # smaller images are icons and badges, not evidence
    max_images_per_item: int = Field(gt=0)
    download_timeout_s: float = Field(gt=0)
    match_limit: int = Field(gt=0)
    image_requests_per_minute: int = Field(gt=0)
    commons: CommonsSettings


class OfficialFeed(_Strict):
    id: str                                  # becomes the evidence source_id
    publisher: str
    url: str                                 # an RSS feed; its host must be in policy allowed_domains
    tier: int = Field(ge=1, le=4)
    lang: str = "en"


class OfficialSettings(_Strict):
    db_path: str                             # SQLite FTS5 index of official passages
    feeds: list[OfficialFeed]
    timeout_s: float = Field(gt=0)
    refresh_s: float = Field(gt=0)           # the feeds are fetched again when the index is older than this
    max_items_per_feed: int = Field(gt=0)
    max_passage_chars: int = Field(gt=0)     # a passage is cut to this length before indexing
    max_results: int = Field(gt=0)           # passages returned for one claim
    min_token_chars: int = Field(gt=0)       # shorter query words are ignored
    contrast_rel_tol: float = Field(ge=0)    # two numbers within this relative difference are the same (policy choice: rounding)


class VoiceSettings(_Strict):
    service_url: str                         # the GPU service (forensics/service.py); when it is down the clip is transcribed on the CPU, slowly
    service_timeout_s: float = Field(gt=0)
    model_dir: str                           # a faster-whisper model folder (git-ignored; see CLAUDE.md for the download)
    device: str                              # "cpu" or "cuda"
    compute_type: str
    beam_size: int = Field(gt=0)
    vad_filter: bool                         # skip silence before transcribing
    max_bytes: int = Field(gt=0)
    max_seconds: float = Field(gt=0)         # longer audio is refused
    decode_timeout_s: float = Field(gt=0)    # ffmpeg is given this long to decode a clip
    low_language_prob: float = Field(ge=0, le=1)   # below this the detected language is shown as uncertain (a display rule, not a calibrated cut)


class KnowledgeGraphSettings(_Strict):
    endpoint: str                            # Wikidata's MediaWiki API
    entity_url_template: str                 # a human page for an entity id, with {id}
    language: str                            # language of labels and descriptions
    timeout_s: float = Field(gt=0)
    max_entities: int = Field(gt=0)          # entities named in one claim, at most
    name_max_chars: int = Field(gt=0)        # longer names are not entity names


class ForensicsSettings(_Strict):
    url: str                                 # the local TruFor service (forensics/service.py), which runs in its own Python environment
    timeout_s: float = Field(gt=0)
    max_upload_bytes: int = Field(gt=0)      # an uploaded image larger than this is refused
    threshold: str                           # name of the calibrated cut in thresholds.yaml for the manipulation score
    ai_threshold: str                        # name of the calibrated cut for the AI-generated probability from the same service
    video_frames: int = Field(gt=0)          # frames sampled evenly across a video
    max_video_bytes: int = Field(gt=0)
    max_video_s: float = Field(gt=0)         # longer videos are refused: every sampled frame costs a model run
    ffmpeg_timeout_s: float = Field(gt=0)
    thumb_width: int = Field(gt=0)           # width of the frame thumbnails returned for display


class VerifySettings(_Strict):
    posts_endpoint: str                      # Bluesky AppView getPosts, used when /verify is given a post URI
    timeout_s: float = Field(gt=0)
    max_text_chars: int = Field(gt=0)
    text_source_id: str                      # source_id recorded for text posted straight to /verify
    uri_prefix: str                          # what a Bluesky post URI starts with
    ledger_kind: str
    cache_ttl_decided_s: float = Field(gt=0)     # how long a decided verdict (REFUTED, SUPPORTED, MISSING_CONTEXT) is served from cache
    cache_ttl_undecided_s: float = Field(gt=0)   # UNVERIFIABLE_YET and no-claim results expire sooner: new fact-checks appear
    judge_max_reviews: int = Field(gt=0)         # reviews per claim sent to the same-claim judge, in one call


class RuntimeFile(_Strict):
    ledger_db_path: str
    cache_db_path: str
    probe: ProbeSettings
    ingest: IngestSettings
    factcheck: FactCheckSettings
    fastclock: FastClockSettings
    extract: ExtractSettings
    verify: VerifySettings
    provenance: ProvenanceSettings
    trace: TraceSettings
    escalation: EscalationSettings
    forensics: ForensicsSettings
    knowledge_graph: KnowledgeGraphSettings
    official: OfficialSettings
    voice: VoiceSettings


class Config(_Strict):
    sources: SourcesFile
    policy: PolicyFile
    thresholds: ThresholdsFile
    runtime: RuntimeFile
    checksum: str

    def source(self, source_id: str) -> SourceConfig:
        for s in self.sources.sources:
            if s.id == source_id:
                return s
        raise ConfigError(f"unknown source id {source_id!r}")


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError(f"missing config file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: top level must be a mapping")
    return data


def _parse(model, path: Path):
    try:
        return model.model_validate(_read_yaml(path))
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ConfigError(f"{path.name}: {problems}") from exc


def config_dir(override: str | Path | None = None) -> Path:
    if override is not None:
        return Path(override)
    env = os.environ.get(DEFAULT_CONFIG_DIR_ENV)
    return Path(env) if env else Path(__file__).resolve().parents[2] / "config"


def load_config(directory: str | Path | None = None) -> Config:
    base = config_dir(directory)
    names = ("sources", "policy", "thresholds", "runtime")
    digest = hashlib.sha256()
    for name in names:
        digest.update((base / f"{name}.yaml").read_bytes() if (base / f"{name}.yaml").is_file() else b"")
    return Config(
        sources=_parse(SourcesFile, base / "sources.yaml"),
        policy=_parse(PolicyFile, base / "policy.yaml"),
        thresholds=_parse(ThresholdsFile, base / "thresholds.yaml"),
        runtime=_parse(RuntimeFile, base / "runtime.yaml"),
        checksum=digest.hexdigest(),
    )


def get_secret(env_name: str) -> str:
    """Secrets come only from the environment. A missing one is an error, never an empty string."""
    value = os.environ.get(env_name)
    if not value:
        raise ConfigError(f"environment variable {env_name} is not set")
    return value


def has_secret(env_name: str | None) -> bool:
    return bool(env_name and os.environ.get(env_name))
