from datetime import datetime, timezone
from pathlib import Path

import pytest

from satyasutra.cache import TTLCache
from satyasutra.config import ThresholdEntry, ThresholdsFile, load_config
from satyasutra.fastclock import Clusterer, FastClock, normalise, shingles, text_key
from satyasutra.fastclock.minhash import MinHasher, estimate_jaccard, exact_jaccard
from satyasutra.schemas import Post

CONFIG = Path(__file__).resolve().parents[2] / "config"
BASE = "the new bridge in the capital collapsed this morning and officials say hundreds of people were injured in the crash"
ONE_WORD_CHANGED = BASE.replace("hundreds", "dozens")
UNRELATED = "scientists announced a new battery design that charges a phone in two minutes according to a university lab report"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def settings(**over):
    return load_config(CONFIG).runtime.fastclock.model_copy(update=over)


def post(text, n=1):
    now = datetime.now(timezone.utc)
    return Post(id=f"at://did:plc:x/app.bsky.feed.post/{n}", source_id="s", url="https://x.org", author_hash="h" * 64, text=text, posted_at=now, fetched_at=now)


def fastclock(tmp_path, clock, **over):
    cfg = load_config(CONFIG)
    cfg = cfg.model_copy(update={
        "thresholds": ThresholdsFile(thresholds={"cluster_jaccard": ThresholdEntry(value=0.5)}),
        "runtime": cfg.runtime.model_copy(update={"fastclock": settings(**over)}),
    })
    return FastClock(cfg, TTLCache(tmp_path / "c.sqlite3", clock=clock), clock=clock)


# normalise --------------------------------------------------------------------------------------------------------

def test_normalise_folds_case_links_mentions_punctuation_and_emoji():
    a = normalise("BREAKING!!! Bridge collapsed 🚨 https://t.co/abc @newsdesk, see #bridge")
    assert a == "breaking bridge collapsed see bridge"


def test_normalise_uses_nfkc_and_keeps_other_scripts():
    assert normalise("ＢＲＩＤＧＥ １２") == "bridge 12"
    assert normalise("पुल गिर गया।") == "पुल गिर गया"


def test_normalise_can_leave_nothing():
    assert normalise("🚨🚨 https://x.org/a @someone !!!") == ""


def test_shingles_short_text_is_one_shingle_and_empty_is_none():
    assert shingles("two words", 3) == {"two words"}
    assert shingles("", 3) == set()
    assert shingles("a b c d", 3) == {"a b c", "b c d"}


def test_text_key_is_stable_for_equivalent_text():
    assert text_key(normalise("Hello,  World!")) == text_key(normalise("hello world"))


# minhash ----------------------------------------------------------------------------------------------------------

def test_signatures_are_deterministic_and_estimate_jaccard():
    h = MinHasher(128)
    a = {f"s{i}" for i in range(60)}
    b = {f"s{i}" for i in range(30, 90)}                 # overlap 30 of union 90
    assert h.signature(a) == MinHasher(128).signature(a)
    assert abs(estimate_jaccard(h.signature(a), h.signature(b)) - exact_jaccard(a, b)) < 0.15
    with pytest.raises(ValueError):
        h.signature(set())


# clusterer --------------------------------------------------------------------------------------------------------

def test_identical_and_lightly_edited_posts_share_a_cluster_and_unrelated_does_not():
    c = Clusterer(settings(), 0.5)
    first, created = c.add("1", BASE, 0.0)
    assert created
    same, created_same = c.add("2", BASE.upper() + " https://t.co/x", 1.0)
    edited, created_edit = c.add("3", ONE_WORD_CHANGED, 2.0)
    other, created_other = c.add("4", UNRELATED, 3.0)
    assert same.id == edited.id == first.id and not created_same and not created_edit
    assert other.id != first.id and created_other and first.size == 3


def test_text_with_nothing_left_is_not_clustered():
    assert Clusterer(settings(), 0.5).add("1", "🚨🚨 https://x.org", 0.0) is None


def test_velocity_counts_posts_per_minute_inside_the_window():
    c = Clusterer(settings(window_s=60), 0.5)
    for i in range(6):
        cluster, _ = c.add(str(i), BASE, float(i * 10))      # six posts in 50 s
    assert cluster.velocity(50.0, 60) == 6.0
    assert cluster.velocity(200.0, 60) == 0.0                # all older than the window


def test_top_ranks_by_velocity_then_size():
    c = Clusterer(settings(window_s=60), 0.5)
    for i in range(2):
        c.add(f"a{i}", UNRELATED, 10.0 + i, author=f"a{i}")
    for i in range(5):
        c.add(f"b{i}", BASE, 10.0 + i, author=f"b{i}")
    ranked = c.top(20.0)
    assert [cl.rep_text for cl, _ in ranked] == [BASE, UNRELATED] and ranked[0][1] > ranked[1][1]


def test_idle_clusters_are_evicted_and_their_buckets_cleaned():
    c = Clusterer(settings(cluster_idle_s=100), 0.5)
    c.add("1", BASE, 0.0)
    c.add("2", UNRELATED, 500.0)                              # evicts the first on arrival
    assert len(c.clusters) == 1 and all(len(v) == 1 for v in c._buckets.values())
    _, created = c.add("3", BASE, 501.0)
    assert created                                            # the old cluster is gone, so this starts a new one


def test_max_clusters_drops_the_least_recent():
    c = Clusterer(settings(max_clusters=2), 0.5)
    c.add("1", BASE, 1.0)
    c.add("2", UNRELATED, 2.0)
    c.add("3", "completely different words about a cricket match played yesterday in the evening under floodlights", 3.0)
    assert len(c.clusters) == 2 and BASE not in [x.rep_text for x in c.clusters.values()]


# fast clock -------------------------------------------------------------------------------------------------------

def test_same_text_twice_calls_the_resolver_once_and_returns_the_cached_result(tmp_path):
    fc = fastclock(tmp_path, Clock())
    calls = []

    def resolve(p):
        calls.append(p.id)
        return {"verdict": "UNVERIFIABLE_YET", "n": len(calls)}

    first = fc.process(post(BASE, 1), resolve)
    again = fc.process(post(BASE.upper() + " https://t.co/zz", 2), resolve)      # same text after normalising
    assert len(calls) == 1 and not first.cached and again.cached
    assert again.result == first.result == {"verdict": "UNVERIFIABLE_YET", "n": 1}
    assert again.cluster.size == 2                                               # the repost still counts toward velocity


def test_a_different_text_is_resolved_separately(tmp_path):
    fc = fastclock(tmp_path, Clock())
    calls = []
    fc.process(post(BASE, 1), lambda p: calls.append(1) or {})
    fc.process(post(UNRELATED, 2), lambda p: calls.append(2) or {})
    assert calls == [1, 2]


def test_cached_result_expires_after_the_ttl(tmp_path):
    clock = Clock()
    fc = fastclock(tmp_path, clock, result_ttl_s=100)
    calls = []
    fc.process(post(BASE, 1), lambda p: calls.append(1) or {})
    clock.t += 101
    fc.process(post(BASE, 2), lambda p: calls.append(2) or {})
    assert calls == [1, 2]


def test_without_a_resolver_nothing_is_cached_but_clusters_still_form(tmp_path):
    fc = fastclock(tmp_path, Clock())
    out = fc.process(post(BASE, 1))
    assert out.result is None and not out.cached and out.created_cluster and len(fc.top()) == 1


def test_a_post_with_nothing_to_check_never_reaches_the_resolver(tmp_path):
    fc = fastclock(tmp_path, Clock())
    out = fc.process(post("🚨🚨🚨 https://x.org/a", 1), lambda p: pytest.fail("resolver must not run"))
    assert out.cluster is None


def test_fast_clock_refuses_to_start_without_a_calibrated_threshold(tmp_path):
    cfg = load_config(CONFIG).model_copy(update={"thresholds": ThresholdsFile(thresholds={})})
    with pytest.raises(ValueError, match="calibrate"):
        FastClock(cfg, TTLCache(tmp_path / "c.sqlite3"))


def test_lsh_bands_must_divide_num_perm():
    with pytest.raises(ValueError):
        type(settings())(**{**settings().model_dump(), "lsh_bands": 7})


def test_one_account_repeating_a_template_ranks_below_a_claim_many_accounts_repeat():
    c = Clusterer(settings(window_s=60), 0.5)
    for i in range(10):
        c.add(f"bot{i}", UNRELATED, 10.0 + i, author="one-bot")
    for i in range(3):
        c.add(f"h{i}", BASE, 10.0 + i, author=f"human{i}")
    ranked = c.top(20.0)
    assert ranked[0][0].rep_text == BASE and ranked[1][1] > ranked[0][1]      # the bot has more posts per minute but fewer authors
