#!/usr/bin/env python3
"""Best-effort NBA public scoreboard, boxscores, and roster injury snapshot.
No login keys. ESPN public endpoints are unofficial and may change.
"""
import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
OUT = HERE / 'data/nba.json'
BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; FantasyDataDashboard/1.0)', 'Accept': 'application/json'}

def get_json(url, retries=2):
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            if attempt == retries:
                raise RuntimeError(f'{url.split("?")[0]}: {type(exc).__name__}: {exc}') from exc
            time.sleep(1.2 * (attempt + 1))

def team_entry(team):
    return {'id': str(team.get('id', '')), 'abbr': team.get('abbreviation') or team.get('shortDisplayName') or '', 'name': team.get('displayName') or team.get('name') or '', 'logo': (team.get('logos') or [{}])[0].get('href', '')}

TZ = ZoneInfo('Asia/Taipei')
ET = ZoneInfo('America/New_York')

def taiwan_day(value):
    """Return Asia/Taipei calendar date from the event's UTC tipoff."""
    if not value:
        return ''
    try:
        instant = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if instant.tzinfo is None:
            # An unqualified date is not enough to safely convert time zones.
            return value[:10]
        return instant.astimezone(TZ).date().isoformat()
    except (ValueError, TypeError, OverflowError):
        return value[:10]

def parse_event(event):
    c = (event.get('competitions') or [{}])[0]
    competitors = c.get('competitors') or []
    home = next((e for e in competitors if e.get('homeAway') == 'home'), {})
    away = next((e for e in competitors if e.get('homeAway') == 'away'), {})
    st = c.get('status') or event.get('status') or {}
    ty = st.get('type') or {}
    tipoff = event.get('date') or c.get('date') or ''
    return {'id': str(event.get('id', '')), 'date': taiwan_day(tipoff), 'start_time': tipoff,
            'home': (home.get('team') or {}).get('abbreviation', ''), 'away': (away.get('team') or {}).get('abbreviation', ''),
            'home_score': home.get('score'), 'away_score': away.get('score'), 'status': ty.get('name', ''),
            'status_text': ty.get('shortDetail') or st.get('displayClock') or ty.get('description') or '',
            'completed': bool(ty.get('completed'))}

def boxscore_players(summary, fallback_date):
    rows = []
    box = (summary.get('boxscore') or {}).get('players') or []
    event_id = str((summary.get('header') or {}).get('id', ''))
    for team_blob in box:
        t = team_blob.get('team') or {}
        abbr = t.get('abbreviation', '')
        for section in team_blob.get('statistics') or []:
            keys = [str(k).lower() for k in section.get('keys') or []]
            labels = [str(k).lower() for k in section.get('labels') or []]
            # ESPN uses both abbreviated and long-form keys across versions.
            # Normalize punctuation and support either keys or labels.
            def normalized(key):
                return re.sub(r'[^a-z0-9]', '', str(key).lower())
            for item in section.get('athletes') or []:
                athlete = item.get('athlete') or {}
                vals = item.get('stats') or []
                stats = {}
                for headings in (labels, keys):
                    for heading, value in zip(headings, vals):
                        stats[normalized(heading)] = value
                # ESPN box scores typically expose min, fg, 3pt, ft, reb, ast, stl, blk, to, pf, pts
                def as_number(*candidates):
                    for k in candidates:
                        v = stats.get(normalized(k))
                        if v is None: continue
                        try: return float(str(v).replace(',', ''))
                        except (ValueError, TypeError): pass
                    return None
                def split_shots(*candidates):
                    for k in candidates:
                        v = stats.get(normalized(k))
                        if isinstance(v, str) and '-' in v:
                            try:
                                a,b = v.split('-', 1)
                                return [int(a), int(b)]
                            except (ValueError, TypeError): pass
                    return [None, None]
                def rebound_total():
                    total = as_number('reb', 'rebounds', 'totalrebounds', 'totalrebs', 'trb')
                    if total is not None:
                        return total
                    offense = as_number('oreb', 'offensiverebounds', 'offrebounds')
                    defense = as_number('dreb', 'defensiverebounds', 'defrebounds')
                    if offense is not None and defense is not None:
                        return offense + defense
                    return None
                fg = split_shots('fg', 'fieldgoalsmade-fieldgoalsattempted')
                ft = split_shots('ft', 'freethrowsmade-freethrowsattempted')
                th = split_shots('3pt', 'threepointfieldgoalsmade-threepointfieldgoalsattempted')
                if not vals or item.get('didNotPlay') or item.get('ejected') and not any(str(v).strip() for v in vals):
                    continue
                pts = as_number('pts', 'points')
                if pts is None: continue
                rows.append({'id': str(athlete.get('id','')), 'game_id': event_id, 'date': fallback_date,
                    'name': athlete.get('displayName') or athlete.get('fullName') or '', 'team': abbr,
                    'minutes': stats.get('min') or stats.get('minutes') or '',
                    'pts': pts, 'reb': rebound_total(), 'ast': as_number('ast', 'assists'),
                    'stl': as_number('stl','steals'), 'blk': as_number('blk','blocks'), 'tov': as_number('to','turnovers'),
                    'fgm': fg[0], 'fga': fg[1], 'ftm': ft[0], 'fta': ft[1], 'tpm': th[0]})
    return rows

def find_injuries(obj):
    """List only explicit injury data, never infer from absence or player status."""
    athletes = obj.get('athletes') or []
    if isinstance(athletes, dict): athletes = athletes.get('items') or []
    rows = []
    for p in athletes:
        if not isinstance(p, dict): continue
        info = p.get('athlete') if isinstance(p.get('athlete'), dict) else p
        for inj in (info.get('injuries') or p.get('injuries') or []):
            if not isinstance(inj, dict): continue
            status = inj.get('status') or inj.get('type') or ''
            if isinstance(status, dict): status = status.get('name') or status.get('description') or ''
            desc = inj.get('details') or inj.get('description') or inj.get('shortComment') or ''
            if isinstance(desc, dict): desc = desc.get('detail') or desc.get('type') or ''
            rows.append({'id': str(info.get('id', '')), 'name': info.get('displayName') or info.get('fullName') or '',
                         'status': str(status), 'detail': str(desc), 'date': inj.get('date') or ''})
    return rows

def normalize_existing(old):
    """Use actual tipoff time; mark old untimed US calendar dates as inferred.

    Existing historical records lacking timestamps are provisionally shifted
    to the next Taiwan date, matching most US evening NBA games. Early US
    games can fall on the same Taiwan date. Later scoreboard refreshes fix
    those entries when the true UTC start time becomes available.
    """
    legacy_us_dates = not old.get('date_basis')
    by_id = {}
    for g in old.get('games') or []:
        if not isinstance(g, dict):
            continue
        if g.get('start_time'):
            g['date'] = taiwan_day(g['start_time'])
            g.pop('date_inferred', None)
        elif legacy_us_dates and g.get('date'):
            try:
                g['date'] = (dt.date.fromisoformat(g['date']) + dt.timedelta(days=1)).isoformat()
                g['date_inferred'] = True
            except ValueError:
                pass
        if g.get('id'):
            by_id[str(g['id'])] = g
    for p in old.get('player_games') or []:
        if not isinstance(p, dict):
            continue
        g = by_id.get(str(p.get('game_id', '')))
        if p.get('start_time'):
            p['date'] = taiwan_day(p['start_time'])
            p.pop('date_inferred', None)
        elif g:
            p['date'] = g['date']
            if g.get('start_time'):
                p['start_time'] = g['start_time']
                p.pop('date_inferred', None)
            elif g.get('date_inferred'):
                p['date_inferred'] = True
        elif legacy_us_dates and p.get('date'):
            try:
                p['date'] = (dt.date.fromisoformat(p['date']) + dt.timedelta(days=1)).isoformat()
                p['date_inferred'] = True
            except ValueError:
                pass
    return old


def merge_boxscores(old_rows, new_rows, refreshed_games):
    """Replace complete game snapshots to avoid retaining stale in-game stats."""
    updated = set(refreshed_games)
    combined = [p for p in old_rows if str(p.get('game_id','')) not in updated]
    previous_by_id = {(str(p.get('game_id','')), str(p.get('id',''))): p
                      for p in old_rows if p.get('game_id') and p.get('id')}
    for p in new_rows:
        old = previous_by_id.get((str(p.get('game_id','')), str(p.get('id',''))))
        if p.get('reb') is None and old is not None and old.get('reb') is not None:
            p['reb'] = old['reb']
        combined.append(p)
    return combined


def write_if_changed(payload, previous):
    # Keep the timestamp stable when the payload did not actually change.
    comparable = dict(payload)
    comparable.pop('generated_at', None)
    old_comparable = dict(previous)
    old_comparable.pop('generated_at', None)
    if previous and comparable == old_comparable:
        print('Data unchanged; no commit required.', flush=True)
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUT.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, OUT)


def live_update(old):
    """Fetch only recent Eastern calendar days; includes live games.

    ESPN's public endpoints are unofficial and can fail or return partial stats.
    """
    now = dt.datetime.now(dt.timezone.utc)
    eastern = now.astimezone(ET).date()
    previous = json.loads(json.dumps(old))
    old = normalize_existing(old)
    days = [eastern - dt.timedelta(days=1), eastern, eastern + dt.timedelta(days=1)]
    seen = {}
    warnings = []
    successful_days = 0
    for day in days:
        url = BASE + '/scoreboard?' + urllib.parse.urlencode({
            'dates': day.strftime('%Y%m%d'), 'limit': 300
        })
        try:
            result = get_json(url, retries=1)
            successful_days += 1
            for event in result.get('events') or []:
                item = parse_event(event)
                if item.get('id') and item.get('start_time'):
                    seen[item['id']] = item
        except Exception as exc:
            warnings.append(f'{day} 賽程讀取失敗：{exc}')

    if not successful_days:
        print('WARNING: Live source unavailable; preserving last good data.', flush=True)
        return

    previous_games = {str(g.get('id')):g for g in old.get('games') or [] if g.get('id')}
    previous_games.update(seen)
    games = sorted(previous_games.values(), key=lambda g:(g.get('date',''),g.get('id','')))
    # Live games and newly completed games may have provisional player stats.
    eligible = [g for g in seen.values() if (g.get('status') not in ('STATUS_SCHEDULED','STATUS_POSTPONED','STATUS_CANCELED'))]
    fresh_rows = []
    successful_ids = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(get_json, BASE + '/summary?' + urllib.parse.urlencode({'event':g['id']}), 1):g for g in eligible}
        for future in concurrent.futures.as_completed(futures):
            g = futures[future]
            try:
                obj = future.result()
                new_rows = boxscore_players(obj, g['date'])
                if new_rows:
                    for p in new_rows: p['start_time'] = g['start_time']
                    fresh_rows.extend(new_rows)
                    successful_ids.add(g['id'])
            except Exception as exc:
                warnings.append(f"{g['id']} 逐場 Box Score 讀取失敗：{exc}")

    rows = merge_boxscores(old.get('player_games') or [], fresh_rows, successful_ids)
    if fresh_rows and not any(p.get('reb') is not None for p in fresh_rows):
        warnings.append('本次即時 Box Score 未取得任何籃板值，REB 可能缺漏；請檢查 ESPN 欄位格式。')
    # Any preserved record with start_time should always use its genuine Taiwan date.
    for p in rows:
        if p.get('start_time'):
            p['date'] = taiwan_day(p['start_time'])
    payload = {
        **old,
        'source': 'ESPN public site API (unofficial / not guaranteed)',
        'calendar_timezone': 'Asia/Taipei',
        'date_basis': 'tipoff_utc_to_Asia/Taipei',
        'generated_at': now.isoformat(timespec='seconds').replace('+00:00','Z'),
        'games': games,
        'player_games': rows,
        'warnings': (warnings + [w for w in old.get('warnings') or [] if '即時' not in w])[:45]
    }
    write_if_changed(payload, previous)
    print(f'Live checked: games={len(seen)}, refreshed={len(successful_ids)}, boxscore_rows={len(rows)}', flush=True)

def main(live=False):
    old = {}
    if OUT.exists():
        try: old = json.loads(OUT.read_text(encoding='utf-8'))
        except (ValueError, OSError): pass
    if live:
        return live_update(old)
    previous = json.loads(json.dumps(old))
    old = normalize_existing(old)
    now = dt.datetime.now(dt.timezone.utc)
    # Query ESPN by its US/Eastern scoreboard calendar, but OUTPUT all game dates in Asia/Taipei.
    try:
        today = now.astimezone(ET).date()
    except Exception:
        today = now.date()
    warnings = []
    games = []
    teams_by_abbr = {}
    # League schedule for 14 days ago through 28 days ahead.
    start, end = today - dt.timedelta(days=14), today + dt.timedelta(days=28)
    ranges = [(start + dt.timedelta(days=i), min(start + dt.timedelta(days=i+6), end)) for i in range(0, (end-start).days+1, 7)]
    def fetch_range(pair):
        a, b = pair
        url = BASE + '/scoreboard?' + urllib.parse.urlencode({
            'dates': f'{a:%Y%m%d}-{b:%Y%m%d}', 'limit': 300
        })
        return get_json(url)

    def fetch_day(day):
        # ESPN's single-day query is often more compatible than a date range.
        url = BASE + '/scoreboard?' + urllib.parse.urlencode({
            'dates': day.strftime('%Y%m%d'), 'limit': 300
        })
        return get_json(url)

    def add_events(payload):
        for evt in payload.get('events') or []:
            games.append(parse_event(evt))
            for co in ((evt.get('competitions') or [{}])[0].get('competitors') or []):
                t = team_entry(co.get('team') or {})
                if t['abbr']:
                    teams_by_abbr[t['abbr']] = t

    failed_ranges = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {pool.submit(fetch_range, pair): pair for pair in ranges}
        for future in concurrent.futures.as_completed(tasks):
            a, b = tasks[future]
            try:
                add_events(future.result())
            except Exception as exc:
                failed_ranges.append((a, b))
                warnings.append(f'{a} 至 {b} 區間賽程抓取失敗：{exc}')

    # Fallback for weeks rejected by ESPN's date-range endpoint.
    # Check one day first; if blocked too, avoid dozens of useless requests.
    if failed_ranges:
        probe = failed_ranges[0][0]
        try:
            add_events(fetch_day(probe))
            print(f'Daily scoreboard probe succeeded: {probe}', flush=True)
            days = sorted({a + dt.timedelta(days=i)
                           for a, b in failed_ranges
                           for i in range((b-a).days + 1)
                           if a + dt.timedelta(days=i) != probe})
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                tasks = {pool.submit(fetch_day, day): day for day in days}
                for future in concurrent.futures.as_completed(tasks):
                    day = tasks[future]
                    try:
                        add_events(future.result())
                    except Exception as exc:
                        warnings.append(f'{day} 單日賽程抓取失敗：{exc}')
        except Exception as exc:
            warnings.append(f'單日賽程備援也失敗（{probe}）：{exc}')

    games = list({g['id']: g for g in games if g['id']}.values())
    games.sort(key=lambda r: (r['date'], r['id']))
    if not games and old.get('games'):
        warnings.append('賽程來源沒有返回資料，保留上次成功快取。')
        games = old['games']
    elif not games:
        warnings.append('賽程尚無可用資料，請檢查 ESPN 來源或網路。')
    # Select recent Taiwan dates, not Eastern dates, and include active games.
    today_taiwan = now.astimezone(TZ).date()
    recent = [g for g in games if (g.get('completed') or g.get('status') in ('STATUS_IN_PROGRESS','STATUS_HALFTIME'))
              and str(today_taiwan-dt.timedelta(days=7)) <= g['date'] <= str(today_taiwan)]
    player_games = []
    def fetch_summary(game):
        obj = get_json(BASE + '/summary?' + urllib.parse.urlencode({'event': game['id']}), retries=1)
        rows = boxscore_players(obj, game['date'])
        for p in rows: p['start_time'] = game.get('start_time','')
        return rows
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        tasks = {pool.submit(fetch_summary, g): g for g in recent}
        for future in concurrent.futures.as_completed(tasks):
            game = tasks[future]
            try: player_games.extend(future.result())
            except Exception as exc: warnings.append(f"{game['date']} {game['id']} 球員數據抓取失敗：{exc}")
    if not player_games and old.get('player_games'):
        warnings.append('球員數據來源無可用結果，暫存上次擷取數據（可能過期）。')
        player_games = old['player_games']
    else:
        new_game_ids = {str(p.get('game_id')) for p in player_games if p.get('game_id')}
        old_keep = [p for p in old.get('player_games') or [] if str(p.get('game_id')) not in new_game_ids
                    and p.get('date','') >= str(today_taiwan - dt.timedelta(days=8))]
        player_games = old_keep + player_games
    # Preserve the last known rebound when ESPN sends partial in-game values.
    older_by_id = {(str(p.get('game_id','')), str(p.get('id',''))): p
                   for p in old.get('player_games') or [] if p.get('game_id') and p.get('id')}
    for p in player_games:
        previous_row = older_by_id.get((str(p.get('game_id','')), str(p.get('id',''))))
        if p.get('reb') is None and previous_row and previous_row.get('reb') is not None:
            p['reb'] = previous_row['reb']
    if player_games and not any(p.get('reb') is not None for p in player_games):
        warnings.append('本次 Box Score 未取得任何籃板值，REB 可能缺漏；請檢查 ESPN 欄位格式。')
    # Fetch public team rosters. Injury details frequently unavailable here; omit if missing.
    injury_rows = []
    team_list = list(teams_by_abbr.values())
    if team_list:
        def fetch_roster(team):
            obj = get_json(BASE + '/teams/' + urllib.parse.quote(team['id']) + '/roster', retries=1)
            return team['abbr'], find_injuries(obj)
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            tasks = {pool.submit(fetch_roster, t): t for t in team_list if t['id']}
            for future in concurrent.futures.as_completed(tasks):
                try:
                    abbr, items = future.result()
                    injury_rows.extend([dict(x, team=abbr) for x in items])
                except Exception as exc: warnings.append(f"{tasks[future]['abbr']} 傷病來源失敗：{exc}")
    if not injury_rows:
        warnings.append('球隊 roster API 未提供可確認的傷病清單；傷病頁顯示「未取得」，不表示全部健康。')
    data = {
        'generated_at': now.isoformat(timespec='seconds').replace('+00:00','Z'),
        'source': 'ESPN public site API (unofficial / not guaranteed)',
        'season': '2026-27', 'calendar_timezone': 'Asia/Taipei', 'date_basis': 'tipoff_utc_to_Asia/Taipei', 'warnings': warnings[:45],
        'games': games, 'player_games': player_games,
        'injuries': injury_rows, 'teams': sorted(teams_by_abbr.values(), key=lambda t: t['abbr'])
    }
    write_if_changed(data, previous)
    print(f"Updated {OUT}: games={len(games)}, player_games={len(player_games)}, injuries={len(injury_rows)}, warnings={len(warnings)}", flush=True)
    for message in warnings[:45]:
        print('WARNING:', message, flush=True)
    if not games and not player_games:
        raise SystemExit('No scoreboard / player results returned. Review WARNING lines above; do not treat empty data as current.')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='Only refresh recent game and live boxscore data')
    args = parser.parse_args()
    main(live=args.live)
