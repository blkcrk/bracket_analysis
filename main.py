# main.py
import time
import requests
import pandas as pd
from collections import defaultdict
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── App setup ──────────────────────────────────────────────────────────────────

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Constants ──────────────────────────────────────────────────────────────────

TEAM_CATEGORIES = {
    145: 'Scoring Offense', 146: 'Scoring Defense', 147: 'Scoring Margin',
    148: 'Field Goal Percentage', 149: 'Field Goal Percentage Defense',
    150: 'Free Throw Percentage', 151: 'Rebound Margin', 152: 'Three Point Percentage',
    153: 'Three Pointers Per Game', 168: 'Winning Percentage', 214: 'Blocks Per Game',
    215: 'Steals Per Game', 216: 'Assists Per Game', 217: 'Turnovers Per Game',
    286: 'Fouls Per Game'
}

NAME_MAP = {
    'Prairie View A&M': 'Prairie View', 'Northern Iowa': 'UNI',
    'Cal Baptist': 'California Baptist', 'South Florida': 'South Fla.',
    'Queens (N.C.)': 'Queens (NC)', "St. John's": "St. John's (NY)",
    'Miami (Ohio)': 'Miami (OH)', 'Long Island': 'LIU',
    "Saint Mary's": "Saint Mary's (CA)"
}

WEIGHTS = {
    'SCR MAR': 1.0, 'OPP PPG': 2.0, 'FG%': 1.0,
    'TOPG': 1.5, '3PG': 1.0, 'FT%': 0.5, 'REB MAR': 1.0
}

# ── Injury adjustments ─────────────────────────────────────────────────────────
# Negative SCR MAR = scoring loss, positive OPP PPG = weaker defense without player

INJURY_ADJUSTMENTS = {
    'Gonzaga': {'SCR MAR': -17.8, 'OPP PPG': 2.0},
    'BYU': {'SCR MAR': -18.0, 'OPP PPG': 2.0},
    'Alabama': {'SCR MAR': -16.8, 'OPP PPG': 1.5},
    'North Carolina': {'SCR MAR': -19.8, 'OPP PPG': 2.5},
    'Texas Tech': {'SCR MAR': -21.8, 'OPP PPG': 2.5},
}


# ── Data loading ───────────────────────────────────────────────────────────────

def fetch_pages(cat_id, stat_type):
    url = f"https://ncaa-api.henrygd.me/stats/basketball-men/d1/current/{stat_type}/{cat_id}"
    r = requests.get(url).json()
    rows = r['data']
    for p in range(2, r['pages'] + 1):
        rows += requests.get(url, params={'page': p}).json()['data']
        time.sleep(0.25)
    return rows


def load_team_stats():
    dfs = []
    for cat_id in TEAM_CATEGORIES:
        df = pd.DataFrame(fetch_pages(cat_id, 'team'))
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


# ── Prediction logic ───────────────────────────────────────────────────────────

def get_adjusted_stats(name, row):
    adj = INJURY_ADJUSTMENTS.get(name, {})
    return {
        'SCR MAR': float(row['SCR MAR']) + adj.get('SCR MAR', 0),
        'OPP PPG': float(row['OPP PPG']) + adj.get('OPP PPG', 0),
        'FG%': float(row['FG%']),
        'TOPG': float(row['TOPG']),
        '3PG': float(row['3PG']),
        'FT%': float(row['FT%']),
        'REB MAR': float(row['REB MAR']),
    }


def compare_teams(t1_name, t2_name, stats):
    try:
        t1 = get_adjusted_stats(t1_name, stats[stats['Team'] == t1_name].iloc[0])
        t2 = get_adjusted_stats(t2_name, stats[stats['Team'] == t2_name].iloc[0])
        return (
                (t1['SCR MAR'] - t2['SCR MAR']) * WEIGHTS['SCR MAR'] +
                (t2['OPP PPG'] - t1['OPP PPG']) * WEIGHTS['OPP PPG'] +
                (t1['FG%'] - t2['FG%']) * WEIGHTS['FG%'] +
                (t2['TOPG'] - t1['TOPG']) * WEIGHTS['TOPG'] +
                (t1['3PG'] - t2['3PG']) * WEIGHTS['3PG'] +
                (t1['FT%'] - t2['FT%']) * WEIGHTS['FT%'] +
                (t1['REB MAR'] - t2['REB MAR']) * WEIGHTS['REB MAR']
        )
    except:
        return 0


def get_analysis(t1, t2, score):
    if not t1 or not t2 or score is None:
        return ""
    fav = t1 if score > 0 else t2
    dog = t2 if score > 0 else t1
    inj_note = ""
    if fav in INJURY_ADJUSTMENTS:
        inj_note = f" ⚠️ {fav} has injury concerns."
    elif dog in INJURY_ADJUSTMENTS:
        inj_note = f" ⚠️ {dog} has injury concerns."
    margin = abs(score)
    conf = ("dominant favourite" if margin > 30 else
            "strong favourite" if margin > 15 else
            "moderate favourite" if margin > 5 else
            "slight favourite")
    return f"{fav} is a {conf} over {dog} (score: {score:+.1f}).{inj_note}"


def build_feeders(games):
    feeders = defaultdict(list)
    for g in games:
        if g['victorBracketPositionId']:
            feeders[g['victorBracketPositionId']].append(g['bracketPositionId'])
    return feeders


def simulate_bracket(games, stats, overrides={}):
    feeders = build_feeders(games)
    pos_to_game = {g['bracketPositionId']: g for g in games}
    winner_cache, score_cache = {}, {}

    def get_winner(pos):
        if pos in winner_cache:
            return winner_cache[pos]
        if pos in overrides:
            winner_cache[pos] = overrides[pos]
            score_cache[pos] = None
            return overrides[pos]
        g = pos_to_game.get(pos)
        if not g:
            return None
        teams = g['teams']
        feed = feeders.get(pos, [])
        if len(feed) == 2:
            t1, t2 = get_winner(feed[0]), get_winner(feed[1])
        elif len(teams) == 2:
            t1 = NAME_MAP.get(teams[0]['nameShort'], teams[0]['nameShort']) if teams[0]['nameShort'] else None
            t2 = NAME_MAP.get(teams[1]['nameShort'], teams[1]['nameShort']) if teams[1]['nameShort'] else None
        else:
            return None
        if t1 and t2:
            score = compare_teams(t1, t2, stats)
            score_cache[pos] = score
            w = t1 if score > 0 else t2
        else:
            w = t1 or t2
            score_cache[pos] = 0
        winner_cache[pos] = w
        return w

    result = {}
    for g in games:
        pos = g['bracketPositionId']
        feed = feeders.get(pos, [])
        teams = g['teams']
        if len(feed) == 2:
            t1, t2 = get_winner(feed[0]), get_winner(feed[1])
        else:
            t1 = teams[0]['nameShort'] if teams and teams[0]['nameShort'] else None
            t2 = teams[1]['nameShort'] if len(teams) > 1 and teams[1]['nameShort'] else None
            ff_feeds = feeders.get(pos, [])
            if ff_feeds:
                ff_winner = get_winner(ff_feeds[0])
                if t1 is None:
                    t1 = ff_winner
                elif t2 is None:
                    t2 = ff_winner
        get_winner(pos)
        score = score_cache.get(pos, 0) or 0
        t1_prob = round(50 + (score / (abs(score) + 10)) * 50) if t1 and t2 else 50
        result[pos] = {
            'team1': t1, 'team2': t2,
            'winner': winner_cache.get(pos),
            'date': g['startDate'],
            'score': round(score, 1),
            'team1_prob': t1_prob,
            'team2_prob': 100 - t1_prob,
            'analysis': get_analysis(t1, t2, score)
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
