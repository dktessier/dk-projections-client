"""
NFL team metadata — full names, primary/secondary colors, logo paths.
"""
from pathlib import Path

_LOGO_DIR = Path(__file__).parent / "assets" / "logos"

TEAM_META = {
    "ARI": {"name": "Arizona Cardinals", "primary": "#97233F", "secondary": "#000000"},
    "ATL": {"name": "Atlanta Falcons", "primary": "#A71930", "secondary": "#000000"},
    "BAL": {"name": "Baltimore Ravens", "primary": "#241773", "secondary": "#9E7C0C"},
    "BUF": {"name": "Buffalo Bills", "primary": "#00338D", "secondary": "#C60C30"},
    "CAR": {"name": "Carolina Panthers", "primary": "#0085CA", "secondary": "#101820"},
    "CHI": {"name": "Chicago Bears", "primary": "#0B162A", "secondary": "#C83803"},
    "CIN": {"name": "Cincinnati Bengals", "primary": "#FB4F14", "secondary": "#000000"},
    "CLE": {"name": "Cleveland Browns", "primary": "#311D00", "secondary": "#FF3C00"},
    "DAL": {"name": "Dallas Cowboys", "primary": "#003594", "secondary": "#869397"},
    "DEN": {"name": "Denver Broncos", "primary": "#FB4F14", "secondary": "#002244"},
    "DET": {"name": "Detroit Lions", "primary": "#0076B6", "secondary": "#B0B7BC"},
    "GB":  {"name": "Green Bay Packers", "primary": "#203731", "secondary": "#FFB612"},
    "HOU": {"name": "Houston Texans", "primary": "#03202F", "secondary": "#A71930"},
    "IND": {"name": "Indianapolis Colts", "primary": "#002C5F", "secondary": "#A2AAAD"},
    "JAX": {"name": "Jacksonville Jaguars", "primary": "#006778", "secondary": "#9F792C"},
    "KC":  {"name": "Kansas City Chiefs", "primary": "#E31837", "secondary": "#FFB81C"},
    "LAC": {"name": "Los Angeles Chargers", "primary": "#0080C6", "secondary": "#FFC20E"},
    "LA":  {"name": "Los Angeles Rams", "primary": "#003594", "secondary": "#FFA300"},
    "LAR": {"name": "Los Angeles Rams", "primary": "#003594", "secondary": "#FFA300"},
    "LV":  {"name": "Las Vegas Raiders", "primary": "#000000", "secondary": "#A5ACAF"},
    "MIA": {"name": "Miami Dolphins", "primary": "#008E97", "secondary": "#FC4C02"},
    "MIN": {"name": "Minnesota Vikings", "primary": "#4F2683", "secondary": "#FFC62F"},
    "NE":  {"name": "New England Patriots", "primary": "#002244", "secondary": "#C60C30"},
    "NO":  {"name": "New Orleans Saints", "primary": "#D3BC8D", "secondary": "#101820"},
    "NYG": {"name": "New York Giants", "primary": "#0B2265", "secondary": "#A71930"},
    "NYJ": {"name": "New York Jets", "primary": "#125740", "secondary": "#000000"},
    "PHI": {"name": "Philadelphia Eagles", "primary": "#004C54", "secondary": "#A5ACAF"},
    "PIT": {"name": "Pittsburgh Steelers", "primary": "#FFB612", "secondary": "#101820"},
    "SEA": {"name": "Seattle Seahawks", "primary": "#002244", "secondary": "#69BE28"},
    "SF":  {"name": "San Francisco 49ers", "primary": "#AA0000", "secondary": "#B3995D"},
    "TB":  {"name": "Tampa Bay Buccaneers", "primary": "#D50A0A", "secondary": "#34302B"},
    "TEN": {"name": "Tennessee Titans", "primary": "#0C2340", "secondary": "#4B92DB"},
    "WAS": {"name": "Washington Commanders", "primary": "#5A1414", "secondary": "#FFB612"},
}

def get_team_logo_path(abbr: str) -> str:
    """Return local path to team logo PNG, or empty string if not found."""
    path = _LOGO_DIR / f"{abbr}.png"
    return str(path) if path.exists() else ""

def get_team_color(abbr: str) -> str:
    return TEAM_META.get(abbr, {}).get("primary", "#4e9af1")

def get_team_name(abbr: str) -> str:
    return TEAM_META.get(abbr, {}).get("name", abbr)
