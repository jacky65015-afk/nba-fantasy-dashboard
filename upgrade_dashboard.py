#!/usr/bin/env python3
'''One-file upgrade for NBA Fantasy 六隊作戰中心.

Upload this file to the repository root as ``upgrade_dashboard.py`` (replace the
existing file), then run the existing GitHub Actions workflow:

    NBA Fantasy - Player Opponent History

The script is intentionally non-destructive. It patches the current site in
place and refuses unsafe replacements when expected anchors are missing.

Features installed:
1. Player box-score rows get a direct "📈 對戰" link, and scheduled future games get a pregame matchup panel.
2. The opponent page accepts ?player=...&opponent=...&league=... deep links.
3. Opponent analysis adds recent minutes, DD rate and a conservative START/BN
   helper. Shark Roto uses an 82-game-cap-aware heuristic: recent role/minutes
   are more important than historical matchup.
4. The script stages opponents.html during the current Actions run so the existing
   commit step includes it without modifying any workflow YAML file.
5. Existing ESPN daily scoreboard safety patch and README notes are preserved.

No Yahoo login, private roster API, or invented historical records are added.
'''
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def replace_once(text: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if new in text:
        return text, False
    if old not in text:
        raise RuntimeError(f"{label}: expected anchor missing; refusing unsafe replacement")
    return text.replace(old, new, 1), True


def patch_index() -> bool:
    path = ROOT / "index.html"
    html = path.read_text(encoding="utf-8")
    changed = False

    if 'href="./opponents.html"' not in html:
        needle = '<button class="tab" data-tab="players">🏀 球員數據</button>'
        link = (
            '<a class="tab" href="./opponents.html" '
            'title="每位球員對各隊的歷史 9CAT 平均">📈 對手數據</a>'
        )
        html, did = replace_once(
            html, needle, needle + link, "index.html opponent navigation"
        )
        changed |= did

    if ".tabs a.tab" not in html:
        html = html.replace(
            "</style>",
            "\n.tabs a.tab{text-decoration:none;display:inline-flex;"
            "align-items:center;justify-content:center}\n</style>",
            1,
        )
        changed = True

    if "function matchupHref(p)" not in html:
        anchor = (
            "const playerColumns=[['rank','RANK'],['name','球員'],['team','隊'],"
            "['minutes','MIN'],['pts','PTS'],['reb','REB'],['ast','AST'],"
            "['tpm','3PM'],['stl','STL'],['blk','BLK'],['tov','TO'],"
            "['fg','FG%'],['fgshots','FG'],['ft','FT%'],['ftshots','FT']];"
        )
        helper = r'''
function playerOpponent(p){
  const g=data.games.find(g=>String(g.id)===String(p.game_id));
  if(!g)return '';
  return p.team===g.home?g.away:p.team===g.away?g.home:'';
}
function matchupHref(p){
  const q=new URLSearchParams();
  q.set('player',p.name||'');
  q.set('league',leagueId);
  const opp=playerOpponent(p);if(opp)q.set('opponent',opp);
  return './opponents.html?'+q.toString();
}
'''
        if anchor not in html:
            raise RuntimeError(
                "index.html playerColumns anchor missing; refusing to patch player links"
            )
        html = html.replace(anchor, anchor + helper, 1)
        changed = True

    old_cell = (
        '<td><strong>${esc(p.name)}</strong><div class="small">台灣 '
        '${esc(tipoffTaiwan(p.start_time))}</div></td>'
    )
    new_cell = (
        '<td><strong>${esc(p.name)}</strong><div class="small">台灣 '
        '${esc(tipoffTaiwan(p.start_time))} · '
        '<a href="${esc(matchupHref(p))}" '
        'title="查看歷史對戰、近期分鐘、DD 與 START/BN 輔助">📈 對戰</a>'
        '</div></td>'
    )
    if new_cell not in html:
        if old_cell not in html:
            raise RuntimeError(
                "index.html player row anchor missing; refusing to rewrite player table"
            )
        html = html.replace(old_cell, new_cell, 1)
        changed = True

    if changed:
        path.write_text(html, encoding="utf-8")
        print("index.html: installed player → matchup deep links")
    else:
        print("index.html: matchup links already installed")
    return changed



def patch_pregame_index() -> bool:
    # Allow matchup lookup before tipoff using schedule + last known player team.
    path = ROOT / "index.html"
    html = path.read_text(encoding="utf-8")
    changed = False

    old_dates = '''function gameDateOptions(){
  // US/Eastern today is selectable even before the games have started.
  // Never relabel yesterday's statistics as today's.
  return [...new Set([fantasyToday(),...data.player_games.map(p=>p.date).filter(Boolean)])].sort().reverse();
}'''
    new_dates = '''function gameDateOptions(){
  // Include scheduled dates as well as completed Box Score dates so pregame
  // matchup lookup works before tipoff.
  return [...new Set([
    fantasyToday(),
    ...data.games.map(g=>g.date).filter(Boolean),
    ...data.player_games.map(p=>p.date).filter(Boolean)
  ])].sort().reverse();
}'''
    if old_dates in html:
        html = html.replace(old_dates, new_dates, 1)
        changed = True
    elif new_dates not in html:
        raise RuntimeError("index.html gameDateOptions anchor missing; refusing unsafe pregame patch")

    helper_anchor = "function pctDecimal(m,a){"
    helper = r'''function latestKnownPlayers(){
  const ordered=[...data.player_games].sort((a,b)=>
    String(a.date||'').localeCompare(String(b.date||''))||
    String(a.start_time||'').localeCompare(String(b.start_time||''))
  );
  const byId=new Map();
  for(const p of ordered){
    const key=String(p.id||p.name||'').trim();
    if(key)byId.set(key,p);
  }
  return [...byId.values()];
}
function pregameRowsForDate(date){
  const scheduled=data.games.filter(g=>
    g.date===date &&
    !g.completed &&
    !['STATUS_IN_PROGRESS','STATUS_HALFTIME'].includes(String(g.status||''))
  );
  if(!scheduled.length)return [];
  const teamGame=new Map();
  for(const g of scheduled){
    if(g.home&&g.away){
      teamGame.set(g.home,{opp:g.away,game:g});
      teamGame.set(g.away,{opp:g.home,game:g});
    }
  }
  const already=new Set(
    data.player_games.filter(p=>p.date===date).map(p=>String(p.id||p.name||''))
  );
  let rows=latestKnownPlayers()
    .filter(p=>teamGame.has(p.team)&&!already.has(String(p.id||p.name||'')))
    .map(p=>{
      const m=teamGame.get(p.team);
      return {...p,_pregame:true,_opp:m.opp,_game:m.game};
    });

  const rosterText=String(getSetting().roster||'').toLowerCase();
  if(rosterText.trim()){
    const rosterOnly=rows.filter(p=>
      p.name && rosterText.includes(String(p.name).toLowerCase())
    );
    if(rosterOnly.length)rows=rosterOnly;
  }
  return rows;
}
function pregameMatchupHref(p){
  const q=new URLSearchParams();
  q.set('player',p.name||'');
  q.set('league',leagueId);
  if(p._opp)q.set('opponent',p._opp);
  return './opponents.html?'+q.toString();
}
function pregamePanelHtml(rows,date){
  if(!rows.length)return '';
  return `<div class="info" style="margin:14px 0">
    <h4>⏰ 賽前對戰查詢 · ${esc(date)}</h4>
    <p class="small">尚未產生 Box Score；球員隊別使用最近一次已取得紀錄，對手來自已抓取賽程。</p>
    <div class="table-wrap" style="margin-top:10px"><table>
      <thead><tr><th>球員</th><th>隊</th><th>對手</th><th>台灣開賽</th><th>分析</th></tr></thead>
      <tbody>${rows.map(p=>`<tr>
        <td><strong>${esc(p.name)}</strong></td>
        <td>${esc(p.team)}</td>
        <td><strong>${esc(p._opp)}</strong></td>
        <td>${esc(tipoffTaiwan(p._game?.start_time))}</td>
        <td><a href="${esc(pregameMatchupHref(p))}">📈 賽前對戰</a></td>
      </tr>`).join('')}</tbody>
    </table></div>
  </div>`;
}
'''
    if "function pregameRowsForDate(date)" not in html:
        if helper_anchor not in html:
            raise RuntimeError("index.html pctDecimal anchor missing; refusing pregame helper insertion")
        html = html.replace(helper_anchor, helper + "\n" + helper_anchor, 1)
        changed = True

    old_rows = '''  let rows=dayRows.filter(p=>(playerTeamFilter==='ALL'||p.team===playerTeamFilter)&&(`${p.name} ${p.team}`).toLowerCase().includes(search.toLowerCase()));
  rows.sort((a,b)=>comparePlayer(a,b,playerSort,playerSortDir));'''
    new_rows = '''  let rows=dayRows.filter(p=>(playerTeamFilter==='ALL'||p.team===playerTeamFilter)&&(`${p.name} ${p.team}`).toLowerCase().includes(search.toLowerCase()));
  let preRows=pregameRowsForDate(playerDate).filter(p=>(playerTeamFilter==='ALL'||p.team===playerTeamFilter)&&(`${p.name} ${p.team} ${p._opp}`).toLowerCase().includes(search.toLowerCase()));
  preRows.sort((a,b)=>String(a.team).localeCompare(String(b.team))||String(a.name).localeCompare(String(b.name)));
  rows.sort((a,b)=>comparePlayer(a,b,playerSort,playerSortDir));'''
    if "let preRows=pregameRowsForDate(playerDate)" not in html:
        if old_rows not in html:
            raise RuntimeError("index.html renderPlayers row anchor missing; refusing pregame row wiring")
        html = html.replace(old_rows, new_rows, 1)
        changed = True

    old_insert = '''</div><p class="small">${data.legacy_dates_estimated||dayRows.some(p=>p.date_inferred)?'''
    new_insert = '''</div>${pregamePanelHtml(preRows,playerDate)}<p class="small">${data.legacy_dates_estimated||dayRows.some(p=>p.date_inferred)?'''
    if "pregamePanelHtml(preRows,playerDate)" not in html:
        if old_insert not in html:
            raise RuntimeError("index.html renderPlayers template anchor missing; refusing pregame panel insertion")
        html = html.replace(old_insert, new_insert, 1)
        changed = True

    if changed:
        path.write_text(html, encoding="utf-8")
        print("index.html: enabled pregame matchup lookup for scheduled future games")
    else:
        print("index.html: pregame matchup lookup already installed")
    return changed

def patch_opponents() -> bool:
    path = ROOT / "opponents.html"
    if not path.exists():
        raise RuntimeError(
            "opponents.html is missing. Run the existing opponent-history installer first."
        )
    html = path.read_text(encoding="utf-8")
    changed = False

    if ".decision-grid{" not in html:
        css = r'''
.decision-grid{display:grid;grid-template-columns:1.2fr repeat(4,minmax(0,1fr));gap:10px;margin-top:12px}
.decision-main{border:1px solid #355271;background:#0e1e33;border-radius:12px;padding:14px}
.decision-main strong{display:block;font-size:24px;margin:3px 0 5px}
.decision-item{border:1px solid var(--edge);background:#122139;border-radius:12px;padding:12px}
.decision-item small{display:block;color:#9eb7d6;margin-bottom:5px}
.decision-item b{font-size:17px}
.decision-note{margin-top:10px;color:#a9c0dc;font-size:12px;line-height:1.55}
.start{color:#7af2c7}.lean{color:#8fc6ff}.bench{color:#ffd27f}.danger{color:#ff9e9e}
@media(max-width:900px){.decision-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.decision-main{grid-column:span 2}}
'''
        html = html.replace("</style>", css + "\n</style>", 1)
        changed = True

    panel_anchor = (
        '  <section class="stats" id="summary" aria-label="球員統計摘要"></section>'
    )
    decision_panel = r'''  <section class="panel" id="decisionPanel">
    <div class="heading"><div><h2>🎯 START / BN 輔助</h2><div class="small" id="decisionSubtitle">以近期角色、分鐘、DD 與所選對手歷史輔助判斷</div></div><span class="pill" id="leagueBadge">通用模式</span></div>
    <div id="decisionBody" class="decision-grid"></div>
    <div class="decision-note">這是 Fantasy 輔助，不是傷病或先發名單的即時確認。Shark Roto 會特別考慮 82 場上限：近期分鐘過低時，即使歷史對位漂亮也會偏向 BN。</div>
  </section>'''
    if 'id="decisionPanel"' not in html:
        if panel_anchor not in html:
            raise RuntimeError(
                "opponents.html summary anchor missing; refusing to insert decision panel"
            )
        html = html.replace(panel_anchor, panel_anchor + "\n" + decision_panel, 1)
        changed = True

    base_state = (
        "let manifest=[],archive=null,available=[],chosenId='',joined=[],visibleGames=[],"
        "sortKey='gp',sortDesc=true,sourceMode='history';"
    )
    enhanced_state = base_state + r'''
const query=new URLSearchParams(location.search);
const requestedPlayer=(query.get('player')||'').trim();
const requestedOpponent=abbr((query.get('opponent')||'').trim());
const requestedLeague=(query.get('league')||'').trim().toLowerCase();
'''
    if "const requestedPlayer=" not in html:
        if base_state not in html:
            raise RuntimeError(
                "opponents.html state anchor missing; refusing to install deep-link parsing"
            )
        html = html.replace(base_state, enhanced_state, 1)
        changed = True

    if "function renderDecision()" not in html:
        anchor = "function aggregate(rows){"
        helper = r'''
function minuteValue(value){
 const s=String(value??'').trim();
 if(!s)return null;
 if(/^\d+:\d+$/.test(s)){const [m,sec]=s.split(':').map(Number);return m+sec/60}
 const n=Number(s);return Number.isFinite(n)?n:null;
}
function isDoubleDouble(r){
 const cats=['pts','reb','ast','stl','blk'].map(k=>num(r[k])??0);
 return cats.filter(v=>v>=10).length>=2;
}
function avg(values){
 const xs=values.filter(v=>v!==null&&Number.isFinite(v));
 return xs.length?xs.reduce((a,b)=>a+b,0)/xs.length:null;
}
function recentRowsForPlayer(limit=5){
 return joined
  .filter(p=>String(p.id||p.name)===chosenId)
  .sort((a,b)=>String(b.date).localeCompare(String(a.date))||String(b.game_id).localeCompare(String(a.game_id)))
  .slice(0,limit);
}
function selectedOpponentRows(){
 const opp=$('#opponent').value;
 if(!opp||opp==='ALL')return [];
 return joined
  .filter(p=>String(p.id||p.name)===chosenId&&p.opponent===opp)
  .sort((a,b)=>String(b.date).localeCompare(String(a.date))||String(b.game_id).localeCompare(String(a.game_id)));
}
function decisionLabel(recent,oppRows){
 const mins=recent.map(r=>minuteValue(r.minutes));
 const avgMin=avg(mins);
 const latest=mins[0];
 const recentDD=recent.filter(isDoubleDouble).length;
 const oppAgg=oppRows.length?aggregate(oppRows):null;
 let score=0;
 if(avgMin!==null){
   if(avgMin>=28)score+=4;
   else if(avgMin>=25)score+=3;
   else if(avgMin>=22)score+=2;
   else if(avgMin>=19)score+=0;
   else score-=3;
 }
 if(latest!==null){
   if(latest>=28)score+=1;
   else if(latest<18)score-=2;
 }
 if(recentDD>=2)score+=2;else if(recentDD===1)score+=1;
 if(oppAgg&&oppAgg.gp>=2){
   if(oppAgg.min>=26)score+=1;
   if(oppAgg.fg!==null&&oppAgg.fg>=.50)score+=0.5;
 }
 if(requestedLeague==='shark'&&avgMin!==null&&avgMin<20)score=Math.min(score,-1);
 if(score>=5)return {label:'START',cls:'start',why:'近期角色與分鐘足以優先吃場次'};
 if(score>=2.5)return {label:'偏 START',cls:'lean',why:'可以上，但仍要看賽前角色與傷病消息'};
 if(score>=0)return {label:'BN 觀察',cls:'bench',why:'角色或分鐘尚未穩定，82 場上限下先保留場次'};
 return {label:'BN',cls:'danger',why:'近期分鐘偏低，歷史對位不足以抵銷場次成本'};
}
function renderDecision(){
 const body=$('#decisionBody');if(!body)return;
 const recent=recentRowsForPlayer(5),oppRows=selectedOpponentRows();
 const mins=recent.map(r=>minuteValue(r.minutes));
 const avgMin=avg(mins);
 const trend=mins.slice().reverse().map(v=>v===null?'—':Math.round(v)).join(' → ');
 const recentDD=recent.filter(isDoubleDouble).length;
 const oppDD=oppRows.filter(isDoubleDouble).length;
 const oppAgg=oppRows.length?aggregate(oppRows):null;
 const d=decisionLabel(recent,oppRows);
 const leagueName=requestedLeague==='shark'?'Shark Roto · 82場上限':'通用 Fantasy';
 $('#leagueBadge').textContent=leagueName;
 const opp=$('#opponent').value;
 $('#decisionSubtitle').textContent=opp&&opp!=='ALL'
   ?`近期角色 + 對 ${opp} 的所選賽季歷史`
   :'近期角色為主；選擇對手後會加入 matchup 歷史';
 const opponentText=oppAgg
   ?`${oppAgg.gp} 場｜${fmt(oppAgg.pts)}分 ${fmt(oppAgg.reb)}板 ${fmt(oppAgg.blk)}鍋`
   :'尚未選擇對手';
 const ddText=oppRows.length?`${oppDD}/${oppRows.length}`:'—';
 body.innerHTML=`
   <div class="decision-main"><small>${requestedLeague==='shark'?'Shark Roto 建議':'Fantasy 輔助建議'}</small><strong class="${d.cls}">${d.label}</strong><div>${esc(d.why)}</div></div>
   <div class="decision-item"><small>近5場平均 MIN</small><b>${avgMin===null?'—':avgMin.toFixed(1)}</b><div class="small">${esc(trend||'—')}</div></div>
   <div class="decision-item"><small>近5場 DD</small><b>${recentDD}/${recent.length||0}</b><div class="small">DD 為獨立加權訊號</div></div>
   <div class="decision-item"><small>對手歷史</small><b>${esc(opponentText)}</b><div class="small">只用已取得紀錄</div></div>
   <div class="decision-item"><small>對手 DD</small><b>${esc(ddText)}</b><div class="small">${oppRows.length?'所選賽季交手':'選對手後顯示'}</div></div>`;
}
'''
        if anchor not in html:
            raise RuntimeError(
                "opponents.html aggregate anchor missing; refusing to install decision helpers"
            )
        html = html.replace(anchor, helper + "\n" + anchor, 1)
        changed = True

    old_set_players = r''' const exact=available.find(p=>p.id===chosenId);
 if(!exact){const previous=$('#playerInput').value.trim().toLowerCase();const match=available.find(p=>p.name.toLowerCase()===previous)||available.find(p=>p.name==='Tyrese Maxey')||available[0];chosenId=match?.id||'';}
 $('#playerInput').value=available.find(p=>p.id===chosenId)?.name||'';
 const teams=[...new Set(joined.map(p=>p.opponent).filter(Boolean))].sort();
 const old=$('#opponent').value||'ALL';$('#opponent').innerHTML='<option value="ALL">全部 30 隊</option>'+teams.map(t=>`<option value="${esc(t)}">${esc(t)}</option>`).join('');$('#opponent').value=teams.includes(old)?old:'ALL';'''
    new_set_players = r''' const exact=available.find(p=>p.id===chosenId);
 if(!exact){
   const previous=$('#playerInput').value.trim().toLowerCase();
   const deep=requestedPlayer?available.find(p=>p.name.toLowerCase()===requestedPlayer.toLowerCase())||available.find(p=>p.name.toLowerCase().includes(requestedPlayer.toLowerCase())):null;
   const match=deep||available.find(p=>p.name.toLowerCase()===previous)||available.find(p=>p.name==='Tyrese Maxey')||available[0];
   chosenId=match?.id||'';
 }
 $('#playerInput').value=available.find(p=>p.id===chosenId)?.name||'';
 const teams=[...new Set(joined.map(p=>p.opponent).filter(Boolean))].sort();
 const old=$('#opponent').value||'ALL';
 $('#opponent').innerHTML='<option value="ALL">全部 30 隊</option>'+teams.map(t=>`<option value="${esc(t)}">${esc(t)}</option>`).join('');
 $('#opponent').value=teams.includes(requestedOpponent)?requestedOpponent:(teams.includes(old)?old:'ALL');'''
    if "const deep=requestedPlayer?" not in html:
        if old_set_players not in html:
            raise RuntimeError(
                "opponents.html setPlayers anchor missing; refusing to install deep-link selection"
            )
        html = html.replace(old_set_players, new_set_players, 1)
        changed = True

    render_anchor = (
        " const availablePhases=[...new Set(joined.filter(p=>String(p.id||p.name)===chosenId)"
        ".map(p=>p.phase))];"
    )
    if " renderDecision();\n const availablePhases=" not in html:
        if render_anchor not in html:
            raise RuntimeError(
                "opponents.html render anchor missing; refusing to wire decision panel"
            )
        html = html.replace(
            render_anchor, " renderDecision();\n" + render_anchor, 1
        )
        changed = True

    if changed:
        path.write_text(html, encoding="utf-8")
        print("opponents.html: installed deep links, DD/minutes panel and START/BN helper")
    else:
        print("opponents.html: matchup decision upgrade already installed")
    return changed


def patch_updater() -> bool:
    path = ROOT / "update_data.py"
    src = path.read_text(encoding="utf-8")
    if "failed_days = set()" in src and "fetch_range(" not in src:
        print("update_data.py: daily scoreboard fix already installed")
        return False

    begin = src.find("    ranges = [(start + dt.timedelta(days=i)")
    end = src.find(
        "    games = list({g['id']: g for g in games if g['id']}.values())", begin
    )
    if begin < 0 or end < begin:
        print("update_data.py: legacy multi-day block not found; left unchanged")
        return False

    replacement = r'''    # ESPN sometimes rejects YYYYMMDD-YYYYMMDD date ranges (HTTP 400),
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
    path.write_text(src, encoding="utf-8")
    print("update_data.py: installed single-day scoreboard fallback")
    return True


def patch_readme() -> bool:
    path = ROOT / "README.md"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    marker = "### START / BN 對戰輔助"
    if marker in text:
        return False
    addition = r'''

### START / BN 對戰輔助

- 球員 Box Score 名稱旁的「📈 對戰」可直接帶入球員、當場對手與目前 Fantasy 聯盟。
- 對手頁會顯示近 5 場平均分鐘、分鐘趨勢、DD 次數、對手歷史與 DD 次數。
- `Shark Roto` 使用 82 場上限的保守邏輯：近期分鐘／角色優先於歷史 matchup；低分鐘不會因歷史對位漂亮就自動判為 START。
- START / BN 僅是資料型輔助，不會取代賽前傷病、先發與分鐘限制消息。
'''
    path.write_text(text.rstrip() + addition + "\n", encoding="utf-8")
    print("README.md: added START/BN matchup notes")
    return True


def stage_for_current_workflow() -> None:
    """Stage site files now; do not modify or stage workflow YAML files."""
    candidates = [
        "index.html",
        "opponents.html",
        "update_data.py",
        "README.md",
    ]
    existing = [x for x in candidates if (ROOT / x).exists()]
    try:
        subprocess.run(
            ["git", "add", "--", *existing],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        print("git: staged upgraded dashboard files for this workflow run")
    except Exception as exc:
        print(f"git: staging skipped ({exc})")


def main() -> None:
    changed = []
    for name, func in [
        ("index", patch_index),
        ("pregame", patch_pregame_index),
        ("opponents", patch_opponents),
        ("updater", patch_updater),
        ("readme", patch_readme),
    ]:
        if func():
            changed.append(name)
    stage_for_current_workflow()
    print(
        "Upgrade complete. Changed: "
        + (", ".join(changed) if changed else "nothing (already installed)")
    )


if __name__ == "__main__":
    main()
