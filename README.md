# Spotify API Personal Statistics

This repository contains a beginner-friendly Python script that connects to the Spotify Web API,
retrieves your personal listening history, and prints simple analytics about how you listen to
music and podcasts. The script focuses on readability over clever abstractions so you can easily
modify it for your own experiments.

## What the script does

- **OAuth authentication** – Guides you through Spotify's OAuth flow, then stores encrypted tokens
  locally in `tokens.json` (ignored by git) so you only need to log in once.
- **Data retrieval** – Downloads your recently played tracks and podcast episodes and filters them by
  a configurable time range (last 24 hours, 1 week, 1 month, 3 months, or 6 months).
- **Analytics** – Calculates total listening time per artist and per podcast, plus simple patterns such
  as which days of the week or hours of the day you tend to listen most.
- **Output** – Prints the results to the console in plain English and can optionally export the full
  play history to a CSV file for spreadsheet tinkering.

> ⚠️ Spotify only exposes a limited history of “recently played” items (roughly the last 50 plays).
> Choosing a long time range still works—the script simply filters what Spotify returns—so results
> will vary depending on how much you have listened recently.

## Requirements

- Python 3.10+
- A Spotify account (Free or Premium) with access to the [Spotify for Developers](https://developer.spotify.com/) portal
- `requests` and `python-dotenv` Python packages (installed through `pip`)

## 1. Create a Spotify application

1. Visit <https://developer.spotify.com/dashboard> and create a new application.
2. Note the **Client ID** and **Client Secret** shown on the app page.
3. Add a redirect URI, e.g. `http://localhost:8080/callback`, to the app's settings. This must match
   the value you use in your local configuration.

## 2. Configure environment variables

1. Copy the provided template: `cp .env.example .env`.
2. Fill in your Spotify credentials in `.env`:

   ```env
   SPOTIFY_CLIENT_ID=your_client_id_here
   SPOTIFY_CLIENT_SECRET=your_client_secret_here
   SPOTIFY_REDIRECT_URI=http://localhost:8080/callback
   ```

3. The script automatically loads `.env`, so there is no need to export variables manually.

## 3. Install Python dependencies

Inside the project directory run:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows users: .venv\Scripts\activate
pip install -r requirements.txt
```

## 4. Run the analytics script

```bash
python spotify_analytics.py --time-range 1month --export play_history.csv
```

Arguments:

- `--time-range` (optional): `24h`, `1week`, `1month` (default), `3months`, or `6months`.
- `--export` (optional): Path to save a CSV of every play returned by Spotify.
- `--max-batches` (optional): Advanced users can tune how many Spotify API pages to fetch (default 20).

### First run authorization

On the first run, the script prints a Spotify authorization URL and tries to open it in your
browser. After you log in and approve access, copy the full redirect URL from your browser's address
bar and paste it back into the terminal. Tokens get stored in `tokens.json` so future runs reuse
them automatically.

### Example console output

```
================ Spotify Listening Summary ================
Time range requested : Last 1week (since 2023-11-19)
Total plays analyzed : 32
Total listening time  : 1h 56m

Top artists by listening time
-----------------------------
01. Maggie Rogers — 0h 42m
02. Vulfpeck — 0h 31m
03. Ology Podcast — 0h 27m

Top podcasts by listening time
------------------------------
01. Ology Podcast — 0h 27m

Listening pattern by day of week:
- Monday    0h 48m
- Wednesday 0h 41m

Listening pattern by hour of day:
- 07:00  0h 21m
- 12:00  0h 32m
- 18:00  0h 53m
===========================================================
```

The exact numbers will change based on your personal listening history.

### CSV export

When you supply `--export`, the resulting file includes the following columns:

| Column             | Description                                           |
|--------------------|-------------------------------------------------------|
| `type`             | `track` or `episode`                                  |
| `name`             | Track title or podcast episode title                  |
| `artists_or_hosts` | Comma-separated list of artists or podcast hosts      |
| `show_name`        | Podcast show name (blank for songs)                   |
| `duration_minutes` | Track length in minutes (decimal)                     |
| `played_at_iso`    | Local timestamp, ISO 8601 format                      |

## Project structure

```
.
├── README.md              # This document
├── requirements.txt       # Python dependencies (requests, python-dotenv)
├── spotify_analytics.py   # Main script
├── .env.example           # Template for local credentials
└── tokens.json (created at runtime, ignored by git)
```

## Troubleshooting

- **"Missing Spotify credential(s)"** – Double-check your `.env` file or export the variables in your
  shell before running the script.
- **No data returned** – Spotify only returns a short play history. Listen to something new, then rerun
  the script with a shorter time range such as `24h` or `1week`.
- **Authorization keeps opening** – Delete `tokens.json` to force a fresh OAuth flow if tokens become
  invalid.

Happy listening! 🎧
