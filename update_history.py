#!/usr/bin/env python3
"""Incrementally archive NBA box scores without altering data/nba.json.

Run in the existing NBA Fantasy Dashboard repository. Reuses the site's ESPN
box-score parser and TaipeI-date conversions from update_data.py.
The ESPN site API is unofficial and may be partial or unavailable.
"""
import argparse
import concurrent.futures
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from update_data import BASE, get_json, parse_event, boxscore_players, ET, TZ, fantasy_day

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
HISTORY = DATA / 'history'
UTC = dt.timezone.utc


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def json_write_if_changed(path, data):
    new_text = json.dumps(data, ensure_ascii=False, separators=(',', ':'), sort_keys=False) + '\n'
    if path.exists() and path.read_text(encoding='utf-8') == new_text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(new_text, encoding='utf-8')
    temp.replace(path)
    return True


def season_type(event):
    # Only label season phase when ESPN has explicit season information.
    season = event.get('season') or {}
    if isinstance(season, dict):
        kind = season.get('type')
        if isinstance(kind, dict):
            kind = kind.get('id') or kind.get('type')
        if str(kind) == '1':
            return 'preseason'
        if str(kind) == '2':
            return 'regular'
        if str(kind) == '3':
            return 'playoffs'
    return 'unknown'


def season_file(season):
    if len(season) != 7 or season[4] != '-' or not season[:4].isdigit() or not season[-2:].isdigit():
        raise ValueError('Invalid season format, expected 2026-27')
    year = int(season[:4])
    if int(season[-2:]) != (year + 1) % 100:
        raise ValueError('Season end year does not match start year')
    return HISTORY / (season + '.json')


def main():
    parser = argparse.ArgumentParser(description='Keep a cumulative NBA player-vs-opponent history database')
    parser.add_argument('--season', default='2026-27', help='NBA season YYYY-YY')
    parser.add_argument('--start', default=None, help='US/Eastern start date YYYY-MM-DD')
    parser.add_argument('--days-per-run', type=int, default=45, help='Max new/refresh scoreboard dates per run')
    parser.add_argument('--max-game-summaries', type=int, default=120, help='Max boxscore API requests per run')
    args = parser.parse_args()
    path = season_file(args.season)
    season_year = int(args.season[:4])
    start = dt.date.fromisoformat(args.start) if args.start else dt.date(season_year, 10, 20)
    # 2019-20 NBA playoffs concluded in October 2020 due to the pandemic.
    season_end = dt.date(2020, 10, 15) if season_year == 2019 else dt.date(season_year + 1, 8, 1)
    now = dt.datetime.now(UTC)
    et_today = now.astimezone(ET).date()
    final_day = min(et_today, season_end)
    current = load_json(DATA / 'nba.json', {})
    old = load_json(path, {})

    games = {str(g['id']): dict(g) for g in (old.get('games') or []) if g.get('id')}
    players = {(str(p['game_id']), str(p['id'])): dict(p)
               for p in (old.get('player_games') or []) if p.get('game_id') and p.get('id')}
    done_days = set(old.get('fetched_days') or [])
    done_boxes = set(str(x) for x in old.get('boxscore_fetched') or [])
    warnings = []
    previous_game_count = len(games)
    previous_player_count = len(players)

    def eastern_game_day(g):
        try:
            instant = dt.datetime.fromisoformat(str(g['start_time']).replace('Z', '+00:00'))
            return instant.astimezone(ET).date()
        except (ValueError, KeyError, TypeError, OverflowError):
            return None

    def in_season(g):
        day = eastern_game_day(g)
        return day is not None and start <= day <= season_end

    # Seed with existing live data so no player rows are lost during migration.
    for g in current.get('games') or []:
        if g.get('id') and in_season(g):
            gid = str(g['id'])
            previous = games.get(gid, {})
            games[gid] = {**previous, **g, 'phase': g.get('phase') or previous.get('phase') or 'unknown'}
    for p in current.get('player_games') or []:
        gid = str(p.get('game_id', ''))
        pid = str(p.get('id', ''))
        if gid in games and pid and p.get('pts') is not None:
            previous = players.get((gid, pid), {})
            players[(gid, pid)] = {**previous, **p}

    def fetch_day(day):
        stamp = day.strftime('%Y%m%d')
        return get_json(BASE + f'/scoreboard?dates={stamp}&limit=300', retries=1)

    # Always refresh recent days so live and newly completed scores are finalized.
    recent = [d for d in (et_today - dt.timedelta(days=2), et_today - dt.timedelta(days=1), et_today)
              if start <= d <= final_day]
    missing = []
    if start <= final_day:
        missing = [start + dt.timedelta(days=i) for i in range((final_day - start).days + 1)
                   if (start + dt.timedelta(days=i)).isoformat() not in done_days]
    day_budget = max(1, min(args.days_per_run, 180))
    days = list(dict.fromkeys(recent + missing))[:day_budget]
    updated = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(fetch_day, day): day for day in days}
        for job in concurrent.futures.as_completed(jobs):
            day = jobs[job]
            try:
                response = job.result()
                if not isinstance(response.get('events'), list):
                    raise ValueError('Missing events list; date not marked complete')
                for event in response['events']:
                    phase = season_type(event)
                    if phase == 'preseason':
                        continue
                    g = parse_event(event)
                    gid = str(g.get('id') or '')
                    if not gid:
                        continue
                    previous = games.get(gid, {})
                    games[gid] = {**previous, **g, 'phase': phase if phase != 'unknown'
                                  else previous.get('phase', 'unknown')}
                    updated.add(gid)
                done_days.add(day.isoformat())
            except Exception as e:
                warnings.append(f'{day.isoformat()} scoreboard unavailable: {e}')

    # Retry non-final boxscores and games with missing player data. Completed
    # games older than 3 days with successful snapshots are immutable in practice.
    def is_scored_game(g):
        return bool(g.get('completed')) or g.get('status') in ('STATUS_IN_PROGRESS', 'STATUS_HALFTIME')

    needs = []
    for g in games.values():
        if not is_scored_game(g) or not in_season(g):
            continue
        gid = str(g['id'])
        day = eastern_game_day(g)
        if gid not in done_boxes or (day and day >= et_today - dt.timedelta(days=2)):
            needs.append(g)
    needs.sort(key=lambda g: (str(g.get('id')) not in updated,
                              0 if eastern_game_day(g) and eastern_game_day(g) >= et_today - dt.timedelta(days=2) else 1,
                              g.get('date', ''), g.get('id', '')))
    needs = needs[:max(0, min(args.max_game_summaries, 300))]

    def fetch_box(g):
        content = get_json(BASE + '/summary?event=' + str(g['id']), retries=1)
        return boxscore_players(content, str(g.get('date') or ''))

    filled = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        jobs = {pool.submit(fetch_box, g): g for g in needs}
        for job in concurrent.futures.as_completed(jobs):
            game = jobs[job]
            gid = str(game['id'])
            try:
                rows = job.result()
                if not rows:
                    raise ValueError('Box score returned no player rows')
                for p in rows:
                    if not p.get('id'):
                        continue
                    p['game_id'] = gid
                    p['date'] = game.get('date') or p.get('date', '')
                    p['start_time'] = game.get('start_time') or p.get('start_time', '')
                    key = (gid, str(p['id']))
                    previous = players.get(key, {})
                    if p.get('reb') is None and previous.get('reb') is not None:
                        p['reb'] = previous['reb']
                    players[key] = {**previous, **p}
                    filled += 1
                if game.get('completed') and len(rows) >= 10:
                    done_boxes.add(gid)
            except Exception as e:
                warnings.append(f"Game {gid} box score unavailable: {e}")

    # Some historical snapshots lack a complete season or game identity.
    # Never invent an opponent when the game id cannot be joined.
    # Align all stored history dates with Yahoo Fantasy matchup dates.
    for g in games.values():
        if g.get('start_time'):
            g['date'] = fantasy_day(g['start_time'])
    for (gid, _), p in players.items():
        g = games.get(gid)
        when = p.get('start_time') or (g or {}).get('start_time')
        if when:
            p['date'] = fantasy_day(when)
        elif g:
            p['date'] = g.get('date', p.get('date', ''))

    valid_players = [p for (gid, _), p in players.items() if gid in games]
    valid_players.sort(key=lambda p: (p.get('date', ''), str(p.get('game_id', '')), p.get('name', '')))
    valid_games = sorted(games.values(), key=lambda g: (g.get('date', ''), str(g.get('id', ''))))
    new_doc = {
        'schema_version': 1, 'season': args.season,
        'source': 'ESPN public site API (unofficial; completeness not guaranteed)',
        'calendar_timezone': 'America/New_York', 'display_timezone': 'Asia/Taipei',
        'date_basis': 'tipoff_utc_to_America/New_York',
        'generated_at': old.get('generated_at', ''),
        'fetched_days': sorted(done_days), 'boxscore_fetched': sorted(done_boxes),
        'warnings': warnings[:30], 'games': valid_games, 'player_games': valid_players,
    }
    old_compare = dict(old)
    old_compare['generated_at'] = ''
    new_compare = dict(new_doc)
    new_compare['generated_at'] = ''
    if new_compare != old_compare:
        new_doc['generated_at'] = now.isoformat(timespec='seconds').replace('+00:00', 'Z')
    changed = json_write_if_changed(path, new_doc)

    # Manifest supports multiple seasons without changing the site UI.
    index_path = HISTORY / 'index.json'
    index_old = load_json(index_path, {})
    catalog = {s['id']: dict(s) for s in index_old.get('seasons', []) if s.get('id')}
    catalog[args.season] = {
        'id': args.season, 'file': 'data/history/' + args.season + '.json',
        'games': sum(bool(g.get('completed')) for g in valid_games),
        'player_games': len(valid_players),
        'updated_at': new_doc['generated_at'],
        'fetched_days': len(done_days),
    }
    json_write_if_changed(index_path, {'schema_version': 1, 'seasons': sorted(catalog.values(), key=lambda s: s['id'], reverse=True)})
    print(f'History {args.season}: {len(valid_games)} games (+{len(games)-previous_game_count}), '
          f'{len(valid_players)} player rows (+{len(players)-previous_player_count}), '
          f'{len(done_days)} days fetched; {len(needs)} game requests; '
          f'{filled} newly fetched rows; changed={changed}')
    for warning in warnings[:10]:
        print('WARNING:', warning)
    if not valid_games and not valid_players:
        raise SystemExit('No usable history records. Check ESPN API and data/nba.json.')


if __name__ == '__main__':
    main()
