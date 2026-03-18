# main.py
import requests
import pandas as pd
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from collections import defaultdict
from functools import reduce
import time

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Data fetching ──────────────────────────────────────────────────────────────

def fetch_all_pages(cat_id, stat_type='individual'):
    url = f"https://ncaa-api.henrygd.me/stats/basketball-men/d1/current/{stat_type}/{cat_id}"
    r = requests.get(url).json()
    rows = r['data']
    for p in range(2, r['pages'] + 1):
        rows += requests.get(url, params={'page': p}).json()['data']
        time.sleep(0.25)
    return rows, r['title']

def load_team_stats():
    team_categories = {
        145: 'Scoring Offense', 146: 'Scoring Defense', 147: 'Scoring Margin',
        148: 'Field Goal Percentage', 149: 'Field Goal Percentage Defense',
        150: 'Free Throw Percentage', 151: 'Rebound Margin', 152: 'Three Point Percentage',
        153: 'Three Pointers Per Game', 168: 'Winning Percentage', 214: 'Blocks Per Game',
        215: 'Steals Per Game', 216: 'Assists Per Game', 217: 'Turnovers Per Game',
        286: 'Fouls Per Game'
    }
    dfs = []
    for cat_id in team_categories:
        rows, _ = fetch_all_pages(cat_id, stat_type='team')
        df = pd.DataFrame(rows)
        stat_cols = [c for c in df.columns if c not in ['Rank', 'Name', 'Team', 'G']]
        dfs.append(df[['Team'] + stat_cols])
        time.sleep(0.25)
    combined = dfs[0]
    for df in dfs[1:]:
        df = df.drop(columns=[c for c in df.columns if c in combined.columns and c != 'Team'])
        combined = pd.merge(combined, df, on='Team', how='outer')
    return combined

def load_bracket():
    r = requests.get("https://ncaa-api.henrygd.me/brackets/basketball-men/d1/2026")
    return r.json()['championships'][0]['games']

name_map = {
    'Prairie View A&M': 'Prairie View', 'Northern Iowa': 'UNI',
    'Cal Baptist': 'California Baptist', 'South Florida': 'South Fla.',
    'Queens (N.C.)': 'Queens (NC)', "St. John's": "St. John's (NY)",
    'Miami (Ohio)': 'Miami (OH)', 'Long Island': 'LIU',
    "Saint Mary's": "Saint Mary's (CA)"
}

def compare_teams(t1_name, t2_name, team_stats):
    try:
        t1 = team_stats[team_stats['Team'] == t1_name].iloc[0]
        t2 = team_stats[team_stats['Team'] == t2_name].iloc[0]
        score = (
            (float(t1['SCR MAR']) - float(t2['SCR MAR'])) * 1.0 +
            (float(t2['OPP PPG']) - float(t1['OPP PPG'])) * 2.0 +
            (float(t1['FG%'])     - float(t2['FG%']))      * 1.0 +
            (float(t2['TOPG'])    - float(t1['TOPG']))      * 1.5 +
            (float(t1['3PG'])     - float(t2['3PG']))       * 1.0 +
            (float(t1['FT%'])     - float(t2['FT%']))       * 0.5 +
            (float(t1['REB MAR']) - float(t2['REB MAR']))   * 1.0
        )
        return score
    except:
        return 0

def build_feeders(games):
    feeders = defaultdict(list)
    for g in games:
        if g['victorBracketPositionId']:
            feeders[g['victorBracketPositionId']].append(g['bracketPositionId'])
    return feeders

def simulate_bracket(games, team_stats, overrides={}):
    feeders = build_feeders(games)
    pos_to_game = {g['bracketPositionId']: g for g in games}

    def get_winner(pos):
        if pos in overrides:
            return overrides[pos]
        g = pos_to_game.get(pos)
        if not g:
            return None
        teams = g['teams']
        feed = feeders.get(pos, [])
        if len(feed) == 2:
            t1 = get_winner(feed[0])
            t2 = get_winner(feed[1])
        elif len(teams) == 2:
            t1 = name_map.get(teams[0]['nameShort'], teams[0]['nameShort']) if teams[0]['nameShort'] else None
            t2 = name_map.get(teams[1]['nameShort'], teams[1]['nameShort']) if teams[1]['nameShort'] else None
        else:
            return None
        if t1 and t2:
            score = compare_teams(t1, t2, team_stats)
            return t1 if score > 0 else t2
        return t1 or t2

    result = {}
    for g in games:
        pos = g['bracketPositionId']
        teams = g['teams']
        t1 = name_map.get(teams[0]['nameShort'], teams[0]['nameShort']) if teams and teams[0]['nameShort'] else None
        t2 = name_map.get(teams[1]['nameShort'], teams[1]['nameShort']) if len(teams) > 1 and teams[1]['nameShort'] else None
        result[pos] = {
            'team1': teams[0]['nameShort'] if teams else None,
            'team2': teams[1]['nameShort'] if len(teams) > 1 else None,
            'winner': get_winner(pos),
            'date': g['startDate']
        }
    return result

# ── Startup ────────────────────────────────────────────────────────────────────

print("Loading team stats...")
team_stats = load_team_stats()
print("Loading bracket...")
games = load_bracket()
bracket_overrides = {}

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/bracket")
def get_bracket():
    return simulate_bracket(games, team_stats, bracket_overrides)

class Override(BaseModel):
    position_id: int
    winner: str

@app.post("/override")
def set_override(override: Override):
    bracket_overrides[override.position_id] = override.winner
    return {"status": "ok"}

@app.get("/reset")
def reset():
    bracket_overrides.clear()
    return {"status": "reset"}

@app.get("/", response_class=HTMLResponse)
def index():
    return open('bracket.html').read()
