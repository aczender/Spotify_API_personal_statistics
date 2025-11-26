#!/usr/bin/env python3
"""Simple Spotify analytics for personal listening habits.

This script performs the following high-level steps:
1. Load Spotify API credentials from environment variables (use a .env file for convenience).
2. Walk the user through Spotify's OAuth flow and cache the resulting tokens locally.
3. Download the user's recently played tracks and podcast episodes for a chosen time range.
4. Calculate easy-to-read statistics such as total listening time per artist/podcast and
   the times of day when the user listens the most.
5. Print the insights to the console and optionally export the raw play history to a CSV file.

The code is intentionally written in a step-by-step, beginner-friendly style so it is easy to
modify or extend.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import secrets
import sys
import time
import webbrowser
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

import requests

try:
    # Loading environment variables from a .env file keeps secrets out of the codebase.
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency handling
    load_dotenv = None

# --- Configuration constants -------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
TOKENS_FILE = BASE_DIR / "tokens.json"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
RECENTLY_PLAYED_URL = "https://api.spotify.com/v1/me/player/recently-played"
DEFAULT_SCOPE = "user-read-recently-played"

# Human-friendly names for time ranges; values are expressed in days.
TIME_RANGES = {
    "24h": 1,
    "1week": 7,
    "1month": 30,
    "3months": 90,
    "6months": 180,
}

DAY_ORDER = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


# --- Environment and credential helpers --------------------------------------
def prepare_environment() -> None:
    """Load environment variables from a .env file when python-dotenv is available."""

    if load_dotenv is not None:
        # Load .env in the project directory (silently ignored when missing).
        load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)
        # Load default locations as well so users can keep a global secrets file.
        load_dotenv(override=False)


def get_spotify_config() -> Dict[str, str]:
    """Fetch Spotify credentials from the environment and validate them."""

    prepare_environment()

    config = {
        "client_id": os.getenv("SPOTIFY_CLIENT_ID"),
        "client_secret": os.getenv("SPOTIFY_CLIENT_SECRET"),
        "redirect_uri": os.getenv("SPOTIFY_REDIRECT_URI"),
    }

    missing = [key for key, value in config.items() if not value]
    if missing:
        missing_keys = ", ".join(missing)
        message = (
            f"Missing Spotify credential(s): {missing_keys}.\n"
            "Create a .env file (see README) or export the variables in your shell and try again."
        )
        raise SystemExit(message)

    return config


# --- Token storage and OAuth helpers ----------------------------------------
def read_tokens() -> Dict[str, Any] | None:
    """Load cached OAuth tokens from disk if they exist."""

    if not TOKENS_FILE.exists():
        return None

    try:
        with TOKENS_FILE.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError:
        # If the file is corrupt we simply ignore it and start a fresh auth flow.
        return None


def save_tokens(tokens: Dict[str, Any]) -> None:
    """Persist OAuth tokens to disk and protect the file permissions when possible."""

    with TOKENS_FILE.open("w", encoding="utf-8") as handle:
        json.dump(tokens, handle, indent=2)

    try:
        # 0o600 means read/write for the current user only (best effort on Unix-like systems).
        os.chmod(TOKENS_FILE, 0o600)
    except OSError:
        pass


def tokens_expired(tokens: Dict[str, Any]) -> bool:
    """Return True when the saved access token is about to expire."""

    expires_at = tokens.get("expires_at")
    if expires_at is None:
        return True
    # Refresh a little earlier than the actual expiry to avoid race conditions.
    return time.time() > (expires_at - 30)


def ensure_tokens(
    config: Dict[str, str], tokens: Dict[str, Any] | None = None, force_refresh: bool = False
) -> Dict[str, Any]:
    """Return a valid access token, refreshing or re-authorizing when needed."""

    tokens = tokens or read_tokens()

    if tokens and not force_refresh and not tokens_expired(tokens):
        return tokens

    if tokens and tokens.get("refresh_token"):
        return refresh_access_token(config, tokens["refresh_token"])

    return request_user_authorization(config)


def request_user_authorization(config: Dict[str, str]) -> Dict[str, Any]:
    """Guide the user through the Spotify OAuth flow and save the resulting tokens."""

    state = secrets.token_urlsafe(16)
    params = {
        "client_id": config["client_id"],
        "response_type": "code",
        "redirect_uri": config["redirect_uri"],
        "scope": DEFAULT_SCOPE,
        "state": state,
        "show_dialog": "false",
    }

    auth_url = f"{AUTH_URL}?{urlencode(params)}"
    print("\n=== Spotify authorization required ===")
    print("1. A browser window will open. Log in and approve the requested permissions.")
    print("2. Spotify will redirect to your redirect URI. Copy the FULL URL from the browser.")
    print("3. Paste that URL below so the script can capture the authorization code.\n")

    try:
        webbrowser.open(auth_url, new=1, autoraise=True)
    except webbrowser.Error:
        # Opening the browser is a convenience. When it fails the user can still visit manually.
        print(f"If your browser did not open automatically, visit this URL manually:\n{auth_url}\n")
    else:
        print(f"If the browser did not open, copy this URL manually:\n{auth_url}\n")

    authorization_code = prompt_for_authorization_code(state)
    token_response = request_token(
        {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": config["redirect_uri"],
        },
        config,
    )

    tokens = normalize_token_payload(token_response)
    save_tokens(tokens)
    print("Saved new Spotify tokens to tokens.json.\n")
    return tokens


def prompt_for_authorization_code(expected_state: str) -> str:
    """Ask the user to paste the redirect URL and extract the authorization code."""

    while True:
        redirect_url = input("Paste the full redirect URL here: \n").strip()
        parsed = urlparse(redirect_url)
        params = parse_qs(parsed.query)

        state = params.get("state", [""])[0]
        if expected_state and state != expected_state:
            print("The state parameter did not match. Please try pasting the URL again.\n")
            continue

        code_values = params.get("code")
        if not code_values:
            print("Could not find a 'code' parameter in the URL. Please try again.\n")
            continue

        return code_values[0]


def request_token(payload: Dict[str, str], config: Dict[str, str]) -> Dict[str, Any]:
    """Send a POST request to Spotify's token endpoint and return the JSON payload."""

    try:
        response = requests.post(
            TOKEN_URL,
            data=payload,
            auth=(config["client_id"], config["client_secret"]),
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as error:  # pragma: no cover - network side effects
        raise SystemExit(f"Spotify token request failed: {error}") from error

    return response.json()


def normalize_token_payload(token_payload: Dict[str, Any], existing_refresh: str | None = None) -> Dict[str, Any]:
    """Convert Spotify's token response into a structure we store on disk."""

    refresh_token = token_payload.get("refresh_token") or existing_refresh
    if not refresh_token:
        raise SystemExit("Spotify did not return a refresh token. Please re-run the script to authorize again.")

    expires_in = int(token_payload.get("expires_in", 3600))
    expires_at = time.time() + expires_in

    return {
        "access_token": token_payload["access_token"],
        "refresh_token": refresh_token,
        "scope": token_payload.get("scope", DEFAULT_SCOPE),
        "expires_at": expires_at,
    }


def refresh_access_token(config: Dict[str, str], refresh_token: str) -> Dict[str, Any]:
    """Refresh the access token using the stored refresh token."""

    token_response = request_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        config,
    )

    tokens = normalize_token_payload(token_response, existing_refresh=refresh_token)
    save_tokens(tokens)
    print("Refreshed Spotify access token.\n")
    return tokens


# --- Spotify API helpers -----------------------------------------------------
def spotify_get(
    url: str, params: Dict[str, Any], config: Dict[str, str], tokens: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Perform a GET request with automatic token refresh and rate-limit handling."""

    while True:
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
        except requests.RequestException as error:  # pragma: no cover - network side effects
            raise SystemExit(f"Spotify API request failed: {error}") from error

        if response.status_code == 401:
            # Token expired or revoked; refresh and retry the request.
            tokens = ensure_tokens(config, tokens, force_refresh=True)
            continue

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "1"))
            print(f"Spotify asked us to slow down. Waiting {retry_after} second(s)...")
            time.sleep(retry_after)
            continue

        response.raise_for_status()
        return response.json(), tokens


def parse_spotify_timestamp(timestamp: str) -> datetime:
    """Convert Spotify's ISO 8601 timestamp into an aware datetime object."""

    # Spotify uses UTC timestamps with a trailing 'Z'. Python understands this format
    # when we replace 'Z' with '+00:00'.
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc)


def simplify_play_item(play: Dict[str, Any], played_at: datetime) -> Dict[str, Any] | None:
    """Extract the fields we care about from a single play history item."""

    track = play.get("track") or play.get("episode")
    if track is None:
        return None

    entry_type = track.get("type", "track")

    if entry_type == "track":
        return {
            "type": "track",
            "name": track.get("name", "Unknown track"),
            "artists": [artist.get("name", "Unknown artist") for artist in track.get("artists", [])],
            "album": track.get("album", {}).get("name"),
            "duration_ms": track.get("duration_ms", 0),
            "played_at": played_at,
        }

    # Podcast episodes live under the same key but include show information instead of artists.
    show_info = track.get("show", {})
    host = show_info.get("publisher") or "Unknown host"
    return {
        "type": "episode",
        "name": track.get("name", "Unknown episode"),
        "artists": [host],
        "show_name": show_info.get("name", "Unknown podcast"),
        "duration_ms": track.get("duration_ms", 0),
        "played_at": played_at,
    }


def fetch_recently_played(
    config: Dict[str, str],
    tokens: Dict[str, Any],
    start_time: datetime,
    max_batches: int = 20,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Collect recently played tracks/episodes, filtering by the given start time."""

    collected: List[Dict[str, Any]] = []
    before_param: int | None = None
    batches_fetched = 0

    while batches_fetched < max_batches:
        params: Dict[str, Any] = {"limit": 50}
        if before_param is not None:
            params["before"] = before_param

        data, tokens = spotify_get(RECENTLY_PLAYED_URL, params, config, tokens)
        items = data.get("items", [])
        if not items:
            break

        earliest_timestamp: datetime | None = None

        for item in items:
            played_at = parse_spotify_timestamp(item["played_at"])
            entry = simplify_play_item(item, played_at)
            if entry and played_at >= start_time:
                collected.append(entry)

            if earliest_timestamp is None or played_at < earliest_timestamp:
                earliest_timestamp = played_at

        if earliest_timestamp is None:
            break

        # Stop looping when we've reached plays older than the range the user requested.
        if earliest_timestamp <= start_time:
            break

        before_param = int(earliest_timestamp.timestamp() * 1000) - 1
        batches_fetched += 1

    # Sort chronologically so downstream analytics read naturally.
    collected.sort(key=lambda entry: entry["played_at"])
    return collected, tokens


# --- Analytics helpers -------------------------------------------------------
def summarize_listening_time(plays: List[Dict[str, Any]]) -> Tuple[List[Tuple[str, int]], List[Tuple[str, int]]]:
    """Return total listening time grouped by artist and by podcast/show."""

    artist_totals: Dict[str, int] = defaultdict(int)
    podcast_totals: Dict[str, int] = defaultdict(int)

    for entry in plays:
        if entry["type"] == "track":
            for artist in entry["artists"]:
                artist_totals[artist] += entry["duration_ms"]
        else:
            show_name = entry.get("show_name") or entry["name"]
            podcast_totals[show_name] += entry["duration_ms"]

    sorted_artists = sorted(artist_totals.items(), key=lambda item: item[1], reverse=True)
    sorted_podcasts = sorted(podcast_totals.items(), key=lambda item: item[1], reverse=True)
    return sorted_artists, sorted_podcasts


def analyze_listening_patterns(plays: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """Show when the user listens the most, grouped by day of week and hour of day."""

    by_day: Dict[str, int] = defaultdict(int)
    by_hour: Dict[str, int] = defaultdict(int)

    for entry in plays:
        local_time = entry["played_at"].astimezone()  # Convert to the user's local timezone.
        day_name = local_time.strftime("%A")
        hour_label = f"{local_time.hour:02d}:00"

        by_day[day_name] += entry["duration_ms"]
        by_hour[hour_label] += entry["duration_ms"]

    return {"by_day": by_day, "by_hour": by_hour}


def format_duration(ms: int) -> str:
    """Convert milliseconds to an easy-to-read hours/minutes string."""

    total_seconds = int(ms // 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)

    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def print_top_listings(title: str, entries: List[Tuple[str, int]], limit: int = 5) -> None:
    """Print the top N entries in a formatted list."""

    print(f"\n{title}")
    print("-" * len(title))

    if not entries:
        print("No data available yet. Try listening to something and rerun the script!")
        return

    for rank, (name, duration_ms) in enumerate(entries[:limit], start=1):
        print(f"{rank:02d}. {name} — {format_duration(duration_ms)}")


def print_pattern_summary(patterns: Dict[str, Dict[str, int]]) -> None:
    """Print day-of-week and hour-of-day listening summaries."""

    by_day = patterns["by_day"]
    by_hour = patterns["by_hour"]

    print("\nListening pattern by day of week:")
    for day in DAY_ORDER:
        duration = by_day.get(day, 0)
        if duration:
            print(f"- {day:<9} {format_duration(duration)}")

    if not any(by_day.values()):
        print("- Not enough data yet. Spotify only returns a finite history of plays.")

    print("\nListening pattern by hour of day:")
    for hour_label in sorted(by_hour.keys()):
        duration = by_hour[hour_label]
        print(f"- {hour_label}  {format_duration(duration)}")

    if not by_hour:
        print("- Not enough data yet.")


# --- Output helpers ----------------------------------------------------------
def export_to_csv(plays: List[Dict[str, Any]], destination: Path) -> None:
    """Write the detailed play history to a CSV file for spreadsheet exploration."""

    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    with destination.open("w", newline="", encoding="utf-8") as csvfile:
        fieldnames = [
            "type",
            "name",
            "artists_or_hosts",
            "show_name",
            "duration_minutes",
            "played_at_iso",
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for entry in plays:
            writer.writerow(
                {
                    "type": entry["type"],
                    "name": entry["name"],
                    "artists_or_hosts": ", ".join(entry.get("artists", [])),
                    "show_name": entry.get("show_name", ""),
                    "duration_minutes": round(entry.get("duration_ms", 0) / 60000, 2),
                    "played_at_iso": entry["played_at"].astimezone().isoformat(),
                }
            )

    print(f"\nExported detailed play history to {destination}")


def print_summary(
    plays: List[Dict[str, Any]],
    start_time: datetime,
    time_range_key: str,
    artist_totals: List[Tuple[str, int]],
    podcast_totals: List[Tuple[str, int]],
    patterns: Dict[str, Dict[str, int]],
) -> None:
    """Display an easy-to-read overview of the collected analytics."""

    print("\n================ Spotify Listening Summary ================")
    print(f"Time range requested : Last {time_range_key} (since {start_time.date()})")
    print(f"Total plays analyzed : {len(plays)}")
    total_ms = sum(entry.get("duration_ms", 0) for entry in plays)
    print(f"Total listening time  : {format_duration(total_ms)}")

    print_top_listings("Top artists by listening time", artist_totals)
    print_top_listings("Top podcasts by listening time", podcast_totals)
    print_pattern_summary(patterns)
    print("\n===========================================================\n")


# --- Command-line interface --------------------------------------------------
def parse_arguments() -> argparse.Namespace:
    """Define and parse the command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Authenticate with the Spotify Web API and analyze your personal listening data.",
    )
    parser.add_argument(
        "--time-range",
        choices=TIME_RANGES.keys(),
        default="1month",
        help="How far back to look when filtering the play history (default: 1month).",
    )
    parser.add_argument(
        "--export",
        type=str,
        help="Optional path to export the detailed play history as CSV.",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=20,
        help="Safety valve: maximum number of Spotify API pages to fetch (default: 20).",
    )

    return parser.parse_args()


def main() -> None:
    """Entry point for the command-line tool."""

    args = parse_arguments()
    config = get_spotify_config()
    tokens = ensure_tokens(config)

    days = TIME_RANGES[args.time_range]
    start_time = datetime.now(timezone.utc) - timedelta(days=days)

    plays, tokens = fetch_recently_played(
        config=config,
        tokens=tokens,
        start_time=start_time,
        max_batches=args.max_batches,
    )

    if not plays:
        print(
            "No listening data was returned for the selected time range. Spotify only shares a limited number "
            "of recent plays, so try listening to something new and rerun the script."
        )
        return

    artist_totals, podcast_totals = summarize_listening_time(plays)
    patterns = analyze_listening_patterns(plays)

    print_summary(
        plays=plays,
        start_time=start_time,
        time_range_key=args.time_range,
        artist_totals=artist_totals,
        podcast_totals=podcast_totals,
        patterns=patterns,
    )

    if args.export:
        export_to_csv(plays, Path(args.export))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(1)
