#!/usr/bin/env python3
"""Correct Yahoo Fantasy week/day boundaries while displaying tipoffs in Taipei.

Non-destructive patch for jacky65015-afk/nba-fantasy-dashboard.
Dates for games, player boxscores and history use America/New_York;
start_time remains UTC, and the website displays start time in Asia/Taipei.
Idempotent; refuses source files with unfamiliar anchors.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
EASTERN = ZoneInfo('America/New_York')
DATE_BASIS = 'tipoff_utc_to_America/New_York'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count == 0 and new in text:
        return text
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one anchor, found {count}: {old[:65]!r}')
    return text.replace(old, new, 1)


def save_if_changed(path: Path, new: str) -> bool:
    old = path.read_text('utf-8')
    if new == old:
        print(f'Unchanged: {path.relative_to(ROOT)}')
        return False
    path.write_text(new, encoding='utf-8')
    print(f'Updated: {path.relative_to(ROOT)}')
    return True


def fix_index() -> None:
    path = ROOT / 'index.html'
    html = path.read_text('utf-8')
    if "function normalizeFantasyData(obj)" in html:
        print('index.html: already uses Fantasy calendar')
        return

    # Keep the existing six-team dashboard, localStorage, custom lineups and tabs.
    html = replace_once(
        html,
        'const taiwanNow=()=>dayOf(taiwanToday());',
        """const fantasyToday=()=>{
  const parts=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
  const get=k=>parts.find(p=>p.type===k)?.value||'';
  return `${get('year')}-${get('month')}-${get('day')}`;
};
const fantasyNow=()=>dayOf(fantasyToday());""",
        'US current day helper',
    )
    # Source code still defines taiwanToday() for local use, but weekday grouping
    # and 'today' selections must use the US date matching Yahoo Fantasy.
    html = html.replace('taiwanNow()', 'fantasyNow()')
    html = html.replace('taiwanToday()', 'fantasyToday()')

    begin = html.find('// Normalize every cached date to Taiwan time.')
    end = html.find('let fetchInProgress=false;', begin)
    if begin == -1 or end == -1 or end <= begin:
        raise RuntimeError('index.html: cannot locate original date-normalization block')
    replacement = r'''// Yahoo Fantasy assigns NBA games to the US/Eastern NBA schedule date.
// The tipoff clock shown to Taiwanese managers remains Asia/Taipei.
function fantasyDateFromUTC(value){
  if(!value)return '';
  const t=new Date(value);
  if(Number.isNaN(t.getTime()))return '';
  const parts=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(t);
  const get=k=>parts.find(p=>p.type===k)?.value||'';
  return `${get('year')}-${get('month')}-${get('day')}`;
}
function tipoffTaiwan(value){
  if(!value)return '開賽時間未取得';
  const t=new Date(value);
  if(Number.isNaN(t.getTime()))return '開賽時間未取得';
  const parts=new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Taipei',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(t);
  const get=k=>parts.find(p=>p.type===k)?.value||'';
  return `${get('month')}/${get('day')} ${get('hour')}:${get('minute')}`;
}
function normalizeFantasyData(obj){
  const byId=new Map();let unknown=0;
  for(const g of obj.games||[]){
    const accurate=fantasyDateFromUTC(g.start_time||g.startTime||'');
    if(accurate){g.date=accurate;delete g.date_inferred;}
    else{g.date_inferred=true;unknown++;}
    if(g.id)byId.set(String(g.id),g);
  }
  for(const p of obj.player_games||[]){
    const g=byId.get(String(p.game_id||''));
    const accurate=fantasyDateFromUTC(p.start_time||g?.start_time||'');
    if(accurate){p.date=accurate;delete p.date_inferred;}
    else if(g){p.date=g.date;p.date_inferred=!!g.date_inferred;}
    else{p.date_inferred=true;unknown++;}
  }
  obj.date_basis='tipoff_utc_to_America/New_York';
  obj.calendar_timezone='America/New_York';
  obj.display_timezone='Asia/Taipei';
  obj.legacy_dates_estimated=unknown>0;
  return obj;
}
'''
    html = html[:begin] + replacement + html[end:]
    html = replace_once(html, 'data=normalizeTaiwanData(obj);', 'data=normalizeFantasyData(obj);', 'website data load')

    # Actual dates remain US days; present the clock beneath the opponent.
    html = replace_once(
        html,
        '<span class="accent" title="${esc(o.game.status_text)}">${esc(o.opponent)}</span>',
        '<span class="accent" title="${esc(o.game.status_text)}">${esc(o.opponent)}</span><div class="small">台灣 ${esc(tipoffTaiwan(o.game.start_time))}</div>',
        'schedule start-time cell',
    )
    html = replace_once(
        html,
        'let b2b=days.some((d,i)=>i>0&&cells[t][d]?.length&&cells[t][days[i-1]]?.length);',
        "let b2b=days.some(d=>{if(!cells[t][d]?.length)return false;const prev=iso(shift(dayOf(d),-1)),next=iso(shift(dayOf(d),1));return data.games.some(g=>(g.home===t||g.away===t)&&(g.date===prev||g.date===next))});",
        'cross-week B2B detection',
    )
    # The same markup exists in Injury Tracking; restrict the change to
    # renderPlayers() so injury rows remain untouched.
    player_begin = html.index('function renderPlayers(){')
    player_end = html.index('// Rank injuries', player_begin)
    player_block = replace_once(
        html[player_begin:player_end],
        '<td><strong>${esc(p.name)}</strong></td>',
        '<td><strong>${esc(p.name)}</strong><div class="small">台灣 ${esc(tipoffTaiwan(p.start_time))}</div></td>',
        'player box-score start time',
    )
    html = html[:player_begin] + player_block + html[player_end:]
    html = html.replace('依台灣日期（UTC+8）顯示；✔ 表示當日有賽事', '依 Yahoo Fantasy 美國比賽日分週；對戰格內的開賽時間為台灣時間')
    html = html.replace('依台灣日期（UTC+8）歸類', '依 Yahoo Fantasy 美國比賽日歸類；球員名稱下方顯示台灣開賽時間')
    html = html.replace('（台灣今天）', '（美國今天）')
    html = html.replace('這個台灣日期尚無可用的球員數據', '這個美國比賽日尚無可用的球員數據')
    html = html.replace('日期已使用台灣時間（UTC+8）；', '日期已依美國比賽日計算；')
    html = html.replace('⚠ 舊資料缺少開賽時間者，日期暫依美國晚間比賽＋1 天推定；待更新程式取得開賽時間後核實。', '⚠ 少數舊資料缺少原始開賽時間，無法確認正確美國比賽日。')
    html = html.replace('※ 每週場次與 B2B 由資料中的每日比賽推算。', '※ 每週場次與 B2B 均按美國東部比賽日計算，包含跨週的背靠背。')
    html = html.replace('// All displayed calendar days use Taiwan local time (UTC+08:00).', '// NBA matchups use US game dates; actual tipoff times display in Taipei.')
    html = html.replace('  // Taiwan today must remain selectable even before today\'s box scores arrive.', '  // US/Eastern today is selectable even before the games have started.')
    save_if_changed(path, html)


def fix_updater() -> None:
    path = ROOT / 'update_data.py'
    src = path.read_text('utf-8')
    if 'def fantasy_day(value):' in src and DATE_BASIS in src:
        print('update_data.py: already uses Fantasy calendar')
        return
    src = replace_once(src, 'def taiwan_day(value):', 'def fantasy_day(value):', 'date helper name')
    src = src.replace('taiwan_day(', 'fantasy_day(')
    start = src.index('def fantasy_day(value):')
    end = src.index('\ndef parse_event(', start)
    segment = src[start:end]
    if 'instant.astimezone(TZ).date().isoformat()' not in segment:
        raise RuntimeError('update_data.py: original Asia/Taipei date conversion not found')
    segment = segment.replace('instant.astimezone(TZ).date().isoformat()', 'instant.astimezone(ET).date().isoformat()')
    segment = segment.replace('Return Asia/Taipei calendar date from the event\'s UTC tipoff.', 'Return the Yahoo/NBA US Eastern game date from the event UTC tipoff.')
    src = src[:start] + segment + src[end:]
    src = src.replace('legacy_us_dates = not old.get(\'date_basis\')', 'legacy_us_dates = False  # Never guess an untimed date; wait for an accurate tipoff.')
    src = src.replace('today_taiwan', 'today_fantasy')
    src = src.replace('today_fantasy = now.astimezone(TZ).date()', 'today_fantasy = now.astimezone(ET).date()')
    src = src.replace("'calendar_timezone': 'Asia/Taipei'", "'calendar_timezone': 'America/New_York', 'display_timezone': 'Asia/Taipei'")
    src = src.replace("'date_basis': 'tipoff_utc_to_Asia/Taipei'", "'date_basis': 'tipoff_utc_to_America/New_York'")
    src = src.replace('OUTPUT all game dates in Asia/Taipei', 'OUTPUT US/Eastern NBA matchup dates')
    src = src.replace('recent Taiwan dates, not Eastern dates', 'recent US/Eastern NBA game dates')
    src = src.replace('always use its genuine Taiwan date', 'always use its genuine US/Eastern game date')
    src = src.replace('to the next Taiwan date, matching most US evening NBA games. Early US', 'to the next Taiwan date. We do not apply this legacy guess after migration. Early US')
    # Remaining legacy explanatory text has no bearing on actual conversion.
    if src.count(DATE_BASIS) < 2:
        raise RuntimeError('update_data.py: expected season and live date-basis declarations')
    save_if_changed(path, src)


def fix_history_updater() -> None:
    path = ROOT / 'update_history.py'
    src = path.read_text('utf-8')
    if "from update_data import BASE, get_json, parse_event, boxscore_players, ET, TZ, fantasy_day" not in src:
        src = replace_once(
            src,
            'from update_data import BASE, get_json, parse_event, boxscore_players, ET, TZ',
            'from update_data import BASE, get_json, parse_event, boxscore_players, ET, TZ, fantasy_day',
            'import Fantasy date converter',
        )
    if '# Align all stored history dates with Yahoo Fantasy matchup dates.' not in src:
        src = replace_once(
            src,
            '    valid_players = [p for (gid, _), p in players.items() if gid in games]',
            '''    # Align all stored history dates with Yahoo Fantasy matchup dates.
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

    valid_players = [p for (gid, _), p in players.items() if gid in games]''',
            'history backfill date normalization',
        )
    src = src.replace("'calendar_timezone': 'Asia/Taipei'", "'calendar_timezone': 'America/New_York', 'display_timezone': 'Asia/Taipei'")
    src = src.replace("'date_basis': 'tipoff_utc_to_Asia/Taipei'", "'date_basis': 'tipoff_utc_to_America/New_York'")
    save_if_changed(path, src)


def fix_opponent_page() -> None:
    path = ROOT / 'opponents.html'
    html = path.read_text('utf-8')
    if "function fantasyDay(value)" not in html:
        html = replace_once(
            html,
            "let manifest=[],archive=null,available=[],chosenId='',joined=[],visibleGames=[],sortKey='gp',sortDesc=true,sourceMode='history';",
            """function fantasyDay(value){
  if(!value)return '';
  const t=new Date(value);if(Number.isNaN(t.getTime()))return '';
  const parts=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(t);
  const get=k=>parts.find(p=>p.type===k)?.value||'';
  return `${get('year')}-${get('month')}-${get('day')}`;
}
function twStart(value){
  if(!value)return '—';const t=new Date(value);if(Number.isNaN(t.getTime()))return '—';
  return t.toLocaleString('zh-TW',{timeZone:'Asia/Taipei',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
}
let manifest=[],archive=null,available=[],chosenId='',joined=[],visibleGames=[],sortKey='gp',sortDesc=true,sourceMode='history';""",
            'opponent-page timezone helpers',
        )
        html = replace_once(
            html,
            "found.push({...p,opponent:opp,team,phase:g.phase||'unknown',isComplete:!!g.completed});",
            "found.push({...p,date:fantasyDay(g.start_time||p.start_time)||p.date,start_time:p.start_time||g.start_time,opponent:opp,team,phase:g.phase||'unknown',isComplete:!!g.completed});",
            'opponent historical game-day normalization',
        )
        html = replace_once(
            html,
            "key==='date'?esc(r.date):key==='opponent'",
            "key==='date'?esc(r.date)+'<div class=\"muted\" style=\"font-size:11px\">台灣 '+esc(twStart(r.start_time))+'</div>':key==='opponent'",
            'history game log Taiwan tipoff',
        )
        # Distinguish game days and local tipoff times in the caption.
        html = html.replace('對手數據分析 · 六隊作戰中心', '對手數據分析 · 六隊作戰中心')
        save_if_changed(path, html)
    else:
        print('opponents.html: already uses US game dates and Taiwan tipoff')


def us_date(iso_utc: str) -> str | None:
    if not iso_utc:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(iso_utc).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(EASTERN).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def migrate_json_file(path: Path) -> None:
    old = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(old, dict) or 'games' not in old or 'player_games' not in old:
        print(f'Skipping unrelated JSON: {path.relative_to(ROOT)}')
        return
    if old.get('date_basis') == DATE_BASIS and old.get('calendar_timezone') == 'America/New_York':
        print(f'Already migrated: {path.relative_to(ROOT)}')
        return
    game_map = {}
    n_games = 0
    n_players = 0
    for g in old.get('games') or []:
        date = us_date(g.get('start_time') or g.get('startTime'))
        if date:
            if g.get('date') != date:
                n_games += 1
            g['date'] = date
            g.pop('date_inferred', None)
        if g.get('id'):
            game_map[str(g['id'])] = g
    for p in old.get('player_games') or []:
        game = game_map.get(str(p.get('game_id', '')), {})
        when = p.get('start_time') or game.get('start_time')
        date = us_date(when)
        if date:
            if p.get('date') != date:
                n_players += 1
            p['date'] = date
            p.pop('date_inferred', None)
            if not p.get('start_time') and when:
                p['start_time'] = when
        elif game.get('date'):
            p['date'] = game['date']
    old['date_basis'] = DATE_BASIS
    old['calendar_timezone'] = 'America/New_York'
    old['display_timezone'] = 'Asia/Taipei'
    if path.name == 'nba.json':
        txt = json.dumps(old, ensure_ascii=False, indent=2) + '\n'
    else:
        txt = json.dumps(old, ensure_ascii=False, separators=(',', ':')) + '\n'
    path.write_text(txt, encoding='utf-8')
    print(f'Migrated {path.relative_to(ROOT)}: corrected game dates={n_games}, player dates={n_players}')


def main() -> None:
    # Patch code first. If an anchor is missing, fail without rewriting an unrelated UI.
    fix_index()
    fix_updater()
    fix_history_updater()
    fix_opponent_page()
    for p in [ROOT / 'data/nba.json', *sorted((ROOT / 'data/history').glob('20??-??.json'))]:
        if p.exists():
            migrate_json_file(p)
    print('Fantasy calendar migration completed. US dates, Taiwan tipoff times, US-week B2B.')


if __name__ == '__main__':
    main()
