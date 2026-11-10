#!/usr/bin/env python3
"""Non-destructive installer for Fantasy opponent history features.

Run in a checkout of jacky65015-afk/nba-fantasy-dashboard.
Only inserts a link and fixes rejected ESPN multi-day scoreboards;
never replaces existing dashboard with an outdated index.html.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def patch_index():
    path = ROOT / 'index.html'
    html = path.read_text(encoding='utf-8')
    if 'href="./opponents.html"' in html:
        print('index.html: history navigation already installed')
        return False
    needle = '<button class="tab" data-tab="players">🏀 球員數據</button>'
    if needle not in html:
        raise RuntimeError('index.html: player tab anchor missing, refusing to overwrite existing UI')
    link = '<a class="tab" href="./opponents.html" title="每位球員對各隊的歷史 9CAT 平均">📈 對手數據</a>'
    html = html.replace(needle, needle + link, 1)
    style = '\n.tabs a.tab{text-decoration:none;display:inline-flex;align-items:center;justify-content:center}\n'
    if '.tabs a.tab' not in html:
        html = html.replace('</style>', style + '</style>', 1)
    path.write_text(html, encoding='utf-8')
    print('index.html: added Opponent Analysis tab, preserving all existing tabs and league settings')
    return True


def patch_updater():
    path = ROOT / 'update_data.py'
    src = path.read_text(encoding='utf-8')
    if 'failed_days = set()' in src and 'fetch_range(' not in src:
        print('update_data.py: daily scoreboard fix already installed')
        return False
    begin = src.find('    ranges = [(start + dt.timedelta(days=i)')
    end = src.find("    games = list({g['id']: g for g in games if g['id']}.values())", begin)
    if begin < 0 or end < begin:
        raise RuntimeError('update_data.py: expected scoreboard block not found; refusing unsafe replacement')
    # Keep the rest of the data updater unchanged, including its rebound parser,
    # Taiwan time conversion, live update logic and historical cache merging.
    replacement = '''    # ESPN sometimes rejects YYYYMMDD-YYYYMMDD date ranges (HTTP 400),
    # even when every single-day scoreboard request works.
    days = [start + dt.timedelta(days=i) for i in range((end-start).days + 1)]

    def fetch_day(day):
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

    failed_days = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {pool.submit(fetch_day, day): day for day in days}
        for future in concurrent.futures.as_completed(tasks):
            day = tasks[future]
            try:
                response = future.result()
                if not isinstance(response.get('events'), list):
                    raise ValueError('Missing events list')
                add_events(response)
            except Exception as exc:
                failed_days.add(day)
                warnings.append(f'{day} 單日賽程抓取失敗：{exc}')

    # Preserve previously fetched games for dates with temporary API failures.
    if failed_days:
        for saved in old.get('games') or []:
            instant = saved.get('start_time')
            if not instant:
                continue
            try:
                date_et = dt.datetime.fromisoformat(instant.replace('Z', '+00:00')).astimezone(ET).date()
            except (ValueError, TypeError, OverflowError):
                continue
            if date_et in failed_days:
                games.append(saved)

'''
    src = src[:begin] + replacement + src[end:]
    # Existing current warnings are replaced on the next full update.
    path.write_text(src, encoding='utf-8')
    print('update_data.py: removed failing multi-day calls; kept daily fetch and per-day cached fallback')
    return True


def patch_readme():
    path = ROOT / 'README.md'
    if not path.exists():
        return False
    old = path.read_text(encoding='utf-8')
    if '## 球員對手歷史數據' in old:
        return False
    new = old.rstrip() + '''

## 球員對手歷史數據

- 首頁「📈 對手數據」連到 `opponents.html`，可查球員對各 NBA 球隊的已取得 9CAT 場均、逐場明細與 CSV。
- `.github/workflows/opponent_history.yml` 以 GitHub Actions 排程執行 `update_history.py`，累積檔案在 `data/history/<season>.json`。
- `data/nba.json` **仍保留原本近期資料格式與功能**；新歷史檔獨立保存完整逐場紀錄，避免每次更新被近期 7–8 天資料覆寫。
- 預設開始累積 2026–27；可用 `python update_history.py --season 2016-17 --start 2016-10-01` 逐步回補其他賽季，但不代表已回補或驗證 2016 年至今所有資料。
- 每位球員的對手由 `game_id` 連結比賽主客隊，球員與球隊別以當場資料為準。各隊別名會統一顯示。
- FG%、FT% 使用總命中／總出手加權；場均統計只由已取得、可確認的紀錄計算。已標示的例行賽／季後賽可以篩選，舊紀錄缺少類型時不會擅自判定。
- ESPN 公開端點不保證穩定，資料缺漏、傷病、Yahoo 私人名單仍需另外查核。GitHub 定時排程可能延遲或停用。
'''
    path.write_text(new, encoding='utf-8')
    print('README.md: appended opponent-history setup notes')
    return True


if __name__ == '__main__':
    patch_index()
    patch_updater()
    patch_readme()
    print('Installation complete. Existing pages, settings and team profiles were preserved.')
