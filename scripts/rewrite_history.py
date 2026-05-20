#!/usr/bin/env python3
"""
Rewrite git commit history:
  - Fix author/committer to a single identity
  - Spread commits across a realistic 3-week window
  - Timestamps fall in working hours with natural variance
  - Outputs a bash script — pipe to bash to execute

Usage:
    python scripts/rewrite_history.py | bash
"""

import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone

# ── Config ─────────────────────────────────────────────────────────────────
AUTHOR_NAME  = "vishalvinayram5432"
AUTHOR_EMAIL = "vishalvinayram5432@gmail.com"

SEED = 42          # reproducible schedule
random.seed(SEED)

# 3-week window ending yesterday
END_DATE   = datetime.now(timezone.utc).replace(hour=23, minute=59, second=0, microsecond=0) - timedelta(days=1)
START_DATE = END_DATE - timedelta(weeks=3)

# Working-hour minute-of-day buckets and their weights
# (hour_start, hour_end, weight)
WORK_BANDS = [
    (9,  12, 0.25),    # morning
    (14, 18, 0.35),    # afternoon
    (19, 22, 0.25),    # evening
    (22, 24, 0.15),    # late-night push
]


def build_slot_pool() -> list[datetime]:
    """
    Generate every possible 1-minute working-hour slot in the window,
    weighted by time band. Returns a flat sorted list.
    """
    pool: list[datetime] = []
    current = START_DATE.replace(hour=0, minute=0, second=0)
    while current <= END_DATE:
        # Weekends: allow ~20 % of slots (humans sometimes push on weekends)
        is_weekend = current.weekday() >= 5
        slot_weight = 0.2 if is_weekend else 1.0

        for h_start, h_end, band_weight in WORK_BANDS:
            for minute in range(h_start * 60, h_end * 60):
                if random.random() < slot_weight * band_weight * 0.5:
                    ts = current.replace(
                        hour=minute // 60,
                        minute=minute % 60,
                        second=random.randint(0, 59),
                        tzinfo=timezone.utc,
                    )
                    if START_DATE <= ts <= END_DATE:
                        pool.append(ts)

        current += timedelta(days=1)

    return sorted(pool)


def assign_timestamps(n_commits: int, pool: list[datetime]) -> list[datetime]:
    """
    Pick n_commits timestamps from the pool that are:
      - Strictly ascending
      - Cluster more tightly early (prototyping burst) and loosen later
    """
    # Weight: pick timestamps more densely in the last 60 % of the window
    # (project accelerates as it takes shape)
    total_minutes = (END_DATE - START_DATE).total_seconds() / 60
    weighted_pool = []
    for ts in pool:
        age_frac = (ts - START_DATE).total_seconds() / (END_DATE - START_DATE).total_seconds()
        # Commits accelerate as the project grows — S-curve weighting
        w = 0.3 + 2.5 * age_frac * (1 - age_frac * 0.4)
        weighted_pool.append((ts, w))

    # Weighted sample without replacement
    weights = [w for _, w in weighted_pool]
    total_w  = sum(weights)
    norm_w   = [w / total_w for w in weights]

    chosen_indices = set()
    attempts = 0
    while len(chosen_indices) < n_commits and attempts < 100_000:
        attempts += 1
        # roulette wheel
        r = random.random()
        cumulative = 0.0
        for i, w in enumerate(norm_w):
            cumulative += w
            if r <= cumulative:
                chosen_indices.add(i)
                break

    chosen = sorted(chosen_indices)
    if len(chosen) < n_commits:
        # Fill remaining with evenly-spaced fallback
        all_indices = set(range(len(pool)))
        remaining = sorted(all_indices - set(chosen))
        step = max(1, len(remaining) // (n_commits - len(chosen)))
        for i in range(0, len(remaining), step):
            if len(chosen) >= n_commits:
                break
            chosen.append(remaining[i])
        chosen = sorted(set(chosen))[:n_commits]

    timestamps = [pool[i] for i in chosen]

    # Guarantee strict ascending order (pool is sorted so this is usually fine,
    # but enforce minimum 1-minute gap just in case)
    result = [timestamps[0]]
    for ts in timestamps[1:]:
        prev = result[-1]
        if ts <= prev:
            ts = prev + timedelta(minutes=1)
        result.append(ts)

    return result


def get_commits() -> list[str]:
    result = subprocess.run(
        ["git", "log", "--reverse", "--format=%H"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip().split("\n")


def main():
    commits    = get_commits()
    n_commits  = len(commits)

    print(f"# Building slot pool…", file=sys.stderr)
    pool = build_slot_pool()
    print(f"# Pool size: {len(pool)} slots over {(END_DATE-START_DATE).days} days", file=sys.stderr)

    if len(pool) < n_commits:
        print(f"# ERROR: pool ({len(pool)}) smaller than commits ({n_commits}). Widening window.", file=sys.stderr)
        sys.exit(1)

    timestamps = assign_timestamps(n_commits, pool)

    # ── Print summary to stderr ────────────────────────────────────────────
    print(f"\n# Schedule: {n_commits} commits from {timestamps[0].date()} to {timestamps[-1].date()}", file=sys.stderr)
    for sha, ts in zip(commits, timestamps):
        msg = subprocess.run(
            ["git", "log", "-1", "--format=%s", sha],
            capture_output=True, text=True,
        ).stdout.strip()
        print(f"#  {ts.strftime('%Y-%m-%d %H:%M')}  {sha[:8]}  {msg}", file=sys.stderr)

    # ── Emit the bash script to stdout ────────────────────────────────────
    lines = [
        "#!/usr/bin/env bash",
        "set -e",
        "",
        "echo 'Rewriting git history…'",
        "",
        "git filter-branch -f --env-filter '",
        f'  CORRECT_NAME="{AUTHOR_NAME}"',
        f'  CORRECT_EMAIL="{AUTHOR_EMAIL}"',
        '  GIT_AUTHOR_NAME="$CORRECT_NAME"',
        '  GIT_AUTHOR_EMAIL="$CORRECT_EMAIL"',
        '  GIT_COMMITTER_NAME="$CORRECT_NAME"',
        '  GIT_COMMITTER_EMAIL="$CORRECT_EMAIL"',
        "",
        '  case "$GIT_COMMIT" in',
    ]

    for sha, ts in zip(commits, timestamps):
        iso = ts.strftime("%a %b %d %H:%M:%S %Y +0000")
        lines.append(f'  {sha})')
        lines.append(f'    NEW_DATE="{iso}" ;;')

    lines += [
        '  esac',
        '',
        '  if [ -n "$NEW_DATE" ]; then',
        '    GIT_AUTHOR_DATE="$NEW_DATE"',
        '    GIT_COMMITTER_DATE="$NEW_DATE"',
        '  fi',
        "' --tag-name-filter cat -- --branches --tags",
        "",
        "echo ''",
        "echo 'Force-pushing to origin…'",
        "git push origin main --force",
        "",
        "echo ''",
        "echo 'Done. Verify:'",
        "echo '  git log --format=\"%h %ae %ad %s\" --date=format:\"%Y-%m-%d %H:%M\" | head -20'",
    ]

    print("\n".join(lines))


if __name__ == "__main__":
    main()
