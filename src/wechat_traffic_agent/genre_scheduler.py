"""Genre scheduling for the Square observation pipeline: policy table, quotas, weighted draw."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import random
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DEFAULT_POLICY = {
    "genres": {
        "quick_take":   {"weight": 3.0, "daily_max": 10, "cooldown_posts": 1, "vol_boost": 2.0},
        "data_digest":  {"weight": 2.0, "daily_max": 4,  "cooldown_posts": 2, "vol_boost": 1.2},
        "engagement":   {"weight": 1.0, "daily_max": 2,  "cooldown_posts": 4},
        "deep_take":    {"weight": 2.0, "daily_min": 1,  "daily_max": 3, "cooldown_posts": 2},
        "explainer":    {"weight": 1.5, "daily_min": 1,  "daily_max": 2, "cooldown_posts": 3},
        "story_review": {"weight": 1.5, "daily_min": 1,  "daily_max": 2, "cooldown_posts": 3},
    },
    "deep_daily": {"min": 2, "max": 4},
    "vol_threshold": 5.0,
}
DEEP_GENRES = ("deep_take", "explainer", "story_review")
DEFAULT_ZONE_NAME = "Asia/Shanghai"
STALE_VOLATILITY_SECONDS = 1800


@dataclass(frozen=True)
class GenreDecision:
    genre: str
    depth_tier: str  # "deep" | "light"
    trace: dict


def genre_pipeline_enabled() -> bool:
    return os.environ.get("CONTENT_GENRE_PIPELINE_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def policy_path(state_root) -> Path:
    override = os.environ.get("CONTENT_GENRE_POLICY_PATH", "").strip()
    if override:
        return Path(override)
    return Path(state_root) / "square_editorial" / "genre_policy.json"


def load_policy(state_root) -> dict:
    path = policy_path(state_root)
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(policy, dict) or not isinstance(policy.get("genres"), dict) or not policy["genres"]:
            raise ValueError("policy.genres missing")
    except (OSError, ValueError):
        if path.exists():
            logger.warning("GENRE_POLICY_INVALID path=%s; using built-in defaults", path)
        return json.loads(json.dumps(DEFAULT_POLICY))
    defaults = json.loads(json.dumps(DEFAULT_POLICY))
    if set(policy["genres"]) != set(defaults["genres"]):
        logger.warning("GENRE_POLICY_UNKNOWN_GENRES path=%s diff=%s",
                       path, sorted(set(policy["genres"]) ^ set(defaults["genres"])))
    for key, value in list(policy["genres"].items()):
        if not isinstance(value, dict):
            # 形状废掉的条目：已知体裁回退默认值，未知键无默认可回退、直接丢弃。
            if key in defaults["genres"]:
                logger.warning("GENRE_POLICY_INVALID_GENRE_ENTRY path=%s genre=%s using_default",
                               path, key)
                policy["genres"][key] = json.loads(json.dumps(defaults["genres"][key]))
            else:
                policy["genres"].pop(key)
    if not isinstance(policy.get("deep_daily"), dict):
        logger.warning("GENRE_POLICY_INVALID_DEEP_DAILY path=%s using_default", path)
        policy["deep_daily"] = defaults["deep_daily"]
    return policy


def _posting_zone(state_root):
    try:
        name = json.loads(
            (Path(state_root) / "content/x/posting_contract.json").read_text(encoding="utf-8")
        )["timezone"]
    except (OSError, ValueError, KeyError, TypeError):
        name = DEFAULT_ZONE_NAME
    try:
        return ZoneInfo(name)
    except Exception:
        # Windows venv may lack tzdata; Beijing is a fixed UTC+8 offset.
        return timezone(timedelta(hours=8))


def _genre_sequence(recent_published) -> list[str]:
    sequence = []
    for post in recent_published or ():
        provenance = post.get("observation_provenance") or {}
        genre = provenance.get("genre")
        if not genre:
            genre = "research" if post.get("research_provenance") else "observation_legacy"
        sequence.append(str(genre))
    return sequence


def _today_counts(state_root, now, zone) -> dict[str, int]:
    from .platforms.binance_square_editorial import SquareDraftStore

    counts = {genre: 0 for genre in DEFAULT_POLICY["genres"]}
    today = now.astimezone(zone).date()
    for row in SquareDraftStore(state_root).list():
        if row.get("status") != "SUCCESS" or not row.get("published_at"):
            continue
        try:
            stamp = datetime.fromisoformat(str(row["published_at"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.astimezone(zone).date() != today:
            continue
        genre = (row.get("observation_provenance") or {}).get("genre")
        if genre in counts:
            counts[genre] += 1
    return counts


def alt_market_volatility(state_root, now) -> float | None:
    """Mean absolute 24h price change across cached alt rows; None when cache is stale or broken."""
    from .sources.binance_alt_market import STABLE_BASES, live_cache_root

    path = live_cache_root(state_root) / "binance_alt_market" / "latest.json"
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        age = (now - datetime.fromisoformat(cached["fetched_at"])).total_seconds()
        if not 0 <= age <= STALE_VOLATILITY_SECONDS:
            return None
        changes = [abs(float(row["priceChangePercent"])) for row in cached["rows"]
                   if str(row.get("symbol", "")).endswith("USDT")
                   and str(row.get("symbol", ""))[:-4] not in STABLE_BASES]
        return sum(changes) / len(changes) if changes else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def decide_genre(state_root, now, recent_published, *, zone=None, rng=None):
    """Return a GenreDecision when the pipeline is enabled, else None (legacy single path)."""
    if not genre_pipeline_enabled():
        return None
    rng = rng or random.Random()
    zone = zone or _posting_zone(state_root)
    policy = load_policy(state_root)
    genres = policy["genres"]
    deep_cfg = policy.get("deep_daily") or DEFAULT_POLICY["deep_daily"]
    counts = _today_counts(state_root, now, zone)
    sequence = _genre_sequence(recent_published)
    deep_done = sum(counts.get(g, 0) for g in DEEP_GENRES)
    day_half_over = now.astimezone(zone).hour >= 12

    def spec(genre):
        return genres.get(genre) or {}

    # 1. quota-first: deep quota unmet after local midday forces a deep genre.
    if deep_done < int(deep_cfg.get("min", 2)) and day_half_over:
        eligible = [g for g in DEEP_GENRES if g in genres
                    and counts.get(g, 0) < int(spec(g).get("daily_max", 3))]
        if eligible:
            forced = max(eligible, key=lambda g: float(spec(g).get("weight", 1.0)))
            trace = {"reason": "quota_force", "deep_done": deep_done,
                     "day_half_over": day_half_over, "counts": counts}
            return GenreDecision(forced, "deep", trace)

    trace = {"reason": "weighted_draw", "deep_done": deep_done,
             "day_half_over": day_half_over, "counts": counts}

    # 2. cooldown filter: a genre used within its cooldown_posts window is blocked.
    blocked = set()
    for genre in genres:
        cooldown = int(spec(genre).get("cooldown_posts", 0))
        if cooldown <= 0 or genre not in sequence:
            continue
        last = len(sequence) - 1 - sequence[::-1].index(genre)
        if len(sequence) - 1 - last < cooldown:
            blocked.add(genre)
    trace["rejected_by_cooldown"] = sorted(blocked)

    # 3. volatility boost.
    volatility = alt_market_volatility(state_root, now)
    hot = volatility is not None and volatility >= float(policy.get("vol_threshold", 5.0))
    trace["volatility"] = volatility

    # 4. weighted draw.
    candidates = []
    for genre, row in genres.items():
        if genre not in DEFAULT_POLICY["genres"]:
            continue  # operator typo in the policy table must not produce an unknown genre
        if genre in blocked or counts.get(genre, 0) >= int(row.get("daily_max", 99)):
            continue
        weight = float(row.get("weight", 1.0))
        if hot:
            weight *= float(row.get("vol_boost", 1.0))
        candidates.append({"genre": genre, "effective_weight": round(weight, 4)})
    if not candidates:
        candidates = [{"genre": "quick_take", "effective_weight": 1.0}]
        trace["fallback"] = "all_genres_exhausted"
    trace["candidates"] = candidates
    total = sum(c["effective_weight"] for c in candidates)
    pick = rng.random() * total
    acc = 0.0
    chosen = candidates[-1]["genre"]
    for c in candidates:
        acc += c["effective_weight"]
        if pick <= acc:
            chosen = c["genre"]
            break
    return GenreDecision(chosen, "deep" if chosen in DEEP_GENRES else "light", trace)
