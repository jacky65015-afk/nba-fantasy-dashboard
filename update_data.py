#!/usr/bin/env python3
"""Best-effort NBA public scoreboard, boxscores, and roster injury snapshot.
No login keys. ESPN public endpoints are unofficial and may change.
"""
import concurrent.futures
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

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

def eastern_day(value):
    if not value: return ''
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York')).date().isoformat()
    except (ValueError, TypeError):
        return value[:10]

def parse_event(event):
    c = (event.get('competitions') or [{}])[0]
    competitors = c.get('competitors') or []
    home = next((e for e in competitors if e.get('homeAway') == 'home'), {})
    away = next((e for e in competitors if e.get('homeAway') == 'away'), {})
    st = c.get('status') or event.get('status') or {}
    ty = st.get('type') or {}
    return {'id': str(event.get('id', '')), 'date': eastern_day(event.get('date') or c.get('date') or ''),
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
            names = keys if keys else labels
            for item in section.get('athletes') or []:
                athlete = item.get('athlete') or {}
                vals = item.get('stats') or []
                stats = dict(zip(names, vals))
                # ESPN uses different keys/labels by boxscore feed/version.
                # Some feeds expose 'defensiveRebounds' + 'offensiveRebounds'
                # but not a usable 'rebounds' total. Align labels independently.
                for k, v in zip(labels, vals):
                    stats.setdefault(k, v)
                stats = {str(k).lower().replace('_','').replace(' ',''): v for k,v in stats.items()}
                # ESPN box scores typically expose min, fg, 3pt, ft, reb, ast, stl, blk, to, pf, pts
                def as_number(*candidates):
                    for k in candidates:
                        v = stats.get(k)
                        if v is None: continue
                        try: return float(str(v).replace(',', ''))
                        except (ValueError, TypeError): pass
                    return None
                def split_shots(*candidates):
                    for k in candidates:
                        v = stats.get(k)
                        if isinstance(v, str) and '-' in v:
                            try:
                                a,b = v.split('-', 1)
                                return [int(a), int(b)]
                            except (ValueError, TypeError): pass
                    return [None, None]
                fg = split_shots('fg', 'fieldgoalsmade-fieldgoalsattempted')
                ft = split_shots('ft', 'freethrowsmade-freethrowsattempted')
                th = split_shots('3pt', 'threepointfieldgoalsmade-threepointfieldgoalsattempted')
                # Fall back to offensive + defensive rebounds only when BOTH exist.
                # Never invent a zero when a field is absent.
                reb = as_number('reb', 'rebounds', 'totalrebounds', 'trb')
                ore = as_number('oreb','offensiverebounds','offreb')
                dre = as_number('dreb','defensiverebounds','defreb')
                if reb is None and ore is not None and dre is not None:
                    reb = ore + dre
                if not vals or item.get('didNotPlay') or item.get('ejected') and not any(str(v).strip() for v in vals):
                    continue
                pts = as_number('pts', 'points')
                if pts is None: continue
                rows.append({'id': str(athlete.get('id','')), 'game_id': event_id, 'date': fallback_date,
                    'name': athlete.get('displayName') or athlete.get('fullName') or '', 'team': abbr,
                    'minutes': stats.get('min') or stats.get('minutes') or '',
                    'pts': pts, 'reb': reb, 'oreb': ore, 'dreb': dre, 'ast': as_number('ast', 'assists'),
                    'stl': as_number('stl','steals'), 'blk': as_number('blk','blocks'), 'tov': as_number('to','turnovers'),
                    'fgm': fg[0], 'fga': fg[1], 'ftm': ft[0], 'fta': ft[1], 'tpm': th[0]})
    missing_reb = sum(p['reb'] is None for p in rows)
    if rows and missing_reb:
        print(f'Boxscore {event_id}: rebounds missing for {missing_reb}/{len(rows)} players; verify ESPN keys.', flush=True)
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

def main():
    old = {}
    if OUT.exists():
        try: old = json.loads(OUT.read_text(encoding='utf-8'))
        except (ValueError, OSError): pass
    now = dt.datetime.now(dt.timezone.utc)
    # ESPN scores use US/Eastern calendar dates; use America/New_York if available.
    try:
        from zoneinfo import ZoneInfo
        today = now.astimezone(ZoneInfo('America/New_York')).date()
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
    # For speed, only request individual box scores for the most recent 7 Eastern dates.
    recent = [g for g in games if g.get('completed') and str(today-dt.timedelta(days=6)) <= g['date'] <= str(today)]
    player_games = []
    def fetch_summary(game):
        obj = get_json(BASE + '/summary?' + urllib.parse.urlencode({'event': game['id']}), retries=1)
        return boxscore_players(obj, game['date'])
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        tasks = {pool.submit(fetch_summary, g): g for g in recent}
        for future in concurrent.futures.as_completed(tasks):
            game = tasks[future]
            try: player_games.extend(future.result())
            except Exception as exc: warnings.append(f"{game['date']} {game['id']} 球員數據抓取失敗：{exc}")
    if not player_games and old.get('player_games'):
        warnings.append('球員數據來源無可用結果，暫存上次擷取數據（可能過期）。')
        player_games = old['player_games']
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
        'season': '2026-27', 'warnings': warnings[:45],
        'games': games, 'player_games': player_games,
        'injuries': injury_rows, 'teams': sorted(teams_by_abbr.values(), key=lambda t: t['abbr'])
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUT.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, OUT)
    print(f"Updated {OUT}: games={len(games)}, player_games={len(player_games)}, injuries={len(injury_rows)}, warnings={len(warnings)}", flush=True)
    for message in warnings[:45]:
        print('WARNING:', message, flush=True)
    if not games and not player_games:
        raise SystemExit('No scoreboard / player results returned. Review WARNING lines above; do not treat empty data as current.')

if __name__ == '__main__':
    main()
