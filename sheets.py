"""
NFL Projections Google Sheets store.

Structure:
    One spreadsheet, one tab per week (Week_01, Week_02, ...).
    Each row = one player projection under a specific save key.

    Save key format: {team}_{players_out}_{signature}
        CIN_Corey               — Corey's full health CIN projection
        CIN_Chase_OUT_Corey     — Corey's CIN projection with Chase out
        CIN_Chase_OUT_Dave      — Dave's version
        TB_Corey                — Corey's TB side

Columns:
    saved_at | season | week | game | save_key |
    player_name | position | pass_snap_pct | tgt_rate | catch_pct | y_catch |
    rush_snap_pct | carry_rate | ypc |
    proj_tgts | proj_rec | proj_rec_yds | proj_carries | proj_rush_yds

Public API:
    save_projection(week, game, save_key, players) -> (success, msg)
    load_projections(week) -> pd.DataFrame
    list_saves(week, game) -> list[dict]
    build_save_key(team, outs, signature) -> str
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logger = logging.getLogger("nfl.sheets")

_SHEET_ID = "1Z2kVeOPHg1LLS6fCAiAHFFW4MlS7Q-99S6Qnwnikg3A"
_WEIGHTS_SHEET_ID = "1P6wGZ4E3dXNxgS1xB7ErDKgOzgvEzReCkyq0Gy3V-M8"
_CREDS_FILE = str(Path(__file__).parent.parent / "wnba_props" / "wnba-props-0a911726df2c.json")
_USE_STREAMLIT_SECRETS = True

HEADERS = [
    "saved_at", "season", "week", "game", "save_key",
    "player_name", "position",
    "pass_snap_pct", "tgt_rate", "catch_pct", "y_catch",
    "rush_snap_pct", "carry_rate", "ypc",
    "proj_tgts", "proj_rec", "proj_rec_yds",
    "proj_carries", "proj_rush_yds",
    "qb_td_att_pct", "qb_int_att_pct",
    "proj_pass_att", "proj_comp", "proj_pass_yds", "proj_pass_tds",
    "proj_ints", "proj_scrambles", "proj_scramble_yds", "proj_total_rush_yds",
    "team_plays", "team_dropback_pct", "team_sack_pct", "team_throwaway_pct",
    "team_scramble_pct", "tgt_share", "carry_share",
]


def build_save_key(team: str, outs: list[str], signature: str) -> str:
    """Build save key from team, players out, and user signature.

    Examples:
        build_save_key("CIN", [], "Corey") -> "CIN_Corey"
        build_save_key("CIN", ["Chase"], "Corey") -> "CIN_Chase_OUT_Corey"
        build_save_key("CIN", ["Chase", "Higgins"], "Corey") -> "CIN_Chase_Higgins_OUT_Corey"
    """
    if outs:
        return f"{team}_{'_'.join(outs)}_OUT_{signature}"
    return f"{team}_{signature}"

_gc = None


def _get_gc():
    global _gc
    if _gc is not None:
        return _gc
    import gspread
    if _USE_STREAMLIT_SECRETS:
        try:
            import streamlit as st
            creds_dict = dict(st.secrets["gcp_service_account"])
            _gc = gspread.service_account_from_dict(creds_dict)
            return _gc
        except Exception:
            pass
    _gc = gspread.service_account(filename=_CREDS_FILE)
    return _gc


def _get_sheet():
    gc = _get_gc()
    return gc.open_by_key(_SHEET_ID)


def _get_or_create_tab(week: int):
    tab_name = f"Week {week}"
    sh = _get_sheet()
    import gspread
    try:
        ws = sh.worksheet(tab_name)
        # Sync header row if columns were added
        existing_hdr = ws.row_values(1)
        if len(existing_hdr) < len(HEADERS):
            ws.update(values=[HEADERS], range_name="A1")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(tab_name, rows=500, cols=len(HEADERS))
        ws.update(values=[HEADERS], range_name="A1")
    return ws


def save_projection(
    season: int,
    week: int,
    game: str,
    save_key: str,
    players: list[dict],
) -> tuple[bool, str]:
    """
    Save a full game projection to Sheets.

    players: list of dicts with keys matching HEADERS[5:] (player_name onward).
    Overwrites any existing rows with the same save_key for this game.
    """
    if not _SHEET_ID:
        return False, "Sheet ID not configured"

    try:
        ws = _get_or_create_tab(week)

        # Delete existing rows with same save_key + game (overwrite)
        existing = ws.get_all_values()
        if len(existing) > 1:
            # Find header indices
            hdr = existing[0]
            game_idx = hdr.index("game") if "game" in hdr else -1
            key_idx = hdr.index("save_key") if "save_key" in hdr else -1
            if game_idx >= 0 and key_idx >= 0:
                rows_to_delete = []
                for row_num, row in enumerate(existing[1:], start=2):
                    if row[game_idx] == game and row[key_idx] == save_key:
                        rows_to_delete.append(row_num)
                # Delete from bottom up to preserve indices
                for rn in reversed(rows_to_delete):
                    ws.delete_rows(rn)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        rows = []
        for p in players:
            row = [now, season, week, game, save_key]
            for h in HEADERS[5:]:
                row.append(p.get(h, ""))
            rows.append(row)
        ws.append_rows(rows, value_input_option="RAW")
        return True, f"Saved {len(rows)} players → {save_key}"
    except Exception as e:
        logger.error(f"save_projection failed: {e}")
        return False, str(e)


def load_projections(week: int) -> pd.DataFrame:
    """Load all projections for a given week."""
    if not _SHEET_ID:
        return pd.DataFrame()
    try:
        ws = _get_or_create_tab(week)
        # Use get_all_values to avoid duplicate header issues
        all_vals = ws.get_all_values()
        if not all_vals or len(all_vals) < 2:
            return pd.DataFrame(columns=HEADERS)
        headers = all_vals[0]
        # Clean empty headers (extra columns without names)
        headers = [h if h else f"_col_{i}" for i, h in enumerate(headers)]
        rows = all_vals[1:]
        df = pd.DataFrame(rows, columns=headers)
        # Drop empty-header columns and empty rows
        df = df[[c for c in df.columns if not c.startswith("_col_")]]
        df = df[df.iloc[:, 0] != ""]  # drop rows where first col is empty
        return df
    except Exception as e:
        logger.error(f"load_projections failed: {e}")
        return pd.DataFrame()


def list_saves(week: int, game: str | None = None) -> list[dict]:
    """List unique save keys for a week (optionally filtered by game)."""
    df = load_projections(week)
    if df.empty:
        return []
    if game:
        df = df[df["game"] == game]
    return (
        df[["game", "save_key", "saved_at"]]
        .drop_duplicates(subset=["game", "save_key"])
        .sort_values("saved_at", ascending=False)
        .to_dict("records")
    )


def load_projector_weights() -> pd.DataFrame:
    """
    Load projector weights from the private weights sheet.

    Layout: A1 empty (or "Team"), A2:A33 = team abbreviations,
    B1:XX1 = projector Save Key IDs, B2:XX33 = weights (0-1).

    Returns DataFrame with team abbrev as index and projector names as columns.
    Values are 0-1 weights. Missing/0 = projector not active for that team.
    """
    try:
        gc = _get_gc()
        sh = gc.open_by_key(_WEIGHTS_SHEET_ID)
        ws = sh.sheet1
        all_vals = ws.get_all_values()
        if not all_vals or len(all_vals) < 2:
            return pd.DataFrame()
        headers = all_vals[0]
        rows = all_vals[1:]
        # First column header may be empty — use "Team" as label
        headers[0] = headers[0] or "Team"
        df = pd.DataFrame(rows, columns=headers)
        df = df.set_index("Team")
        # Drop any empty-name columns
        df = df[[c for c in df.columns if c.strip()]]
        # Convert to numeric
        for col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        return df
    except Exception as e:
        logger.error(f"load_projector_weights failed: {e}")
        return pd.DataFrame()
