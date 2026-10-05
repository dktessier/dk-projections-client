"""Shared DK Projections rendering.

Used by both the internal tool tab (streamlit_app.py) and the standalone
client app (client_app.py) so both surfaces render from one code path and
stay in sync automatically. `show_projector=False` produces the locked
client view: no Projector dropdown, output is the plain equal average of
all projectors for each game.
"""
import base64 as _b64
import re as _re
import pandas as pd
import streamlit as st
import streamlit.components.v1 as _stc

from team_meta import get_team_color, get_team_name, get_team_logo_path
from sheets import load_projections as _sheets_read

# Odds conversions, duplicated from core.math on purpose: this module is synced
# into the PUBLIC client repo (deploy/sync-from-private.yml), which ships only
# client_app/dk_view/sheets/team_meta. Importing core.math here would either
# break that app on import or publish the desk's pricing module. Two lines of
# arithmetic is the cheaper copy — tests/test_dk_view_td.py pins them against
# core.math so they cannot drift.
def prob_to_american(p):
    """Fair American price for probability ``p``, or None if unpriceable."""
    if p <= 0.0 or p >= 1.0:
        return None
    if p >= 0.5:
        return int(round(-100.0 * p / (1.0 - p)))
    return int(round(100.0 / p - 100.0))


def american_to_prob(price):
    """Implied probability of an American price. Inverse of prob_to_american."""
    price = float(price)
    if price == 0.0:
        return 0.0
    if price < 0.0:
        return -price / (-price + 100.0)
    return 100.0 / (price + 100.0)


# American prices cannot be averaged arithmetically (+184 and -119 average to
# +33, which is not the consensus of anything). Each priced column is carried
# through the aggregation as a probability under the shadow name below and
# converted back afterwards by _restore_td_prices.
_TD_PRICE_COLS = {"td_true": "_p_td_true"}

# The carried probabilities must NOT be rounded to 2dp before they are converted
# back. A 1% TD share is p=0.0237 (+4116); at 0.02 it prints +4900, and anything
# under 0.005 rounds to zero and prints blank. Those are exactly the deep-bench
# TD rows this board exists to price.
_NO_ROUND = set(_TD_PRICE_COLS.values())


def _round_disp(df):
    """Round display columns to 2dp, leaving the carried probabilities alone."""
    cols = [c for c in df.columns
            if c not in _NO_ROUND and pd.api.types.is_numeric_dtype(df[c])]
    if cols:
        df[cols] = df[cols].round(2)
    return df


def _fmt_td_price(v):
    """Display form of an American price: signed, or blank when unpriceable."""
    if v is None or pd.isna(v):
        return ""
    return f"{int(v):+d}"


def _restore_td_prices(df):
    """Turn the averaged probabilities back into American prices."""
    for _pc, _pp in _TD_PRICE_COLS.items():
        if _pp not in df.columns:
            continue
        df[_pc] = df[_pp].apply(
            lambda v: prob_to_american(v) if pd.notna(v) and 0 < v < 1 else None
        )
        df = df.drop(columns=[_pp])
    return df

# Generational suffix (Jr./Sr./II…V). The saved sheet stores only player_name
# (no id), so if the roster source changed a name between saves — e.g. started
# appending "Jr." — the same player is stored under two strings and splits into
# two rows in the averages. We collapse those variants on the suffix-stripped
# name and display the fuller form.
_NAME_SUFFIX_RE = _re.compile(r"\s+(Jr\.?|Sr\.?|II|III|IV|V)$", _re.IGNORECASE)

# Display-only name overrides for downstream mapping: force these players to
# show with a suffix regardless of how the roster source spelled them when the
# projection was saved. Keyed on the suffix-stripped, lowercased name. Does not
# change saved data or the internal Save flow — only the DK Projections labels.
_NAME_DISPLAY_OVERRIDES = {
    "brian robinson": "Brian Robinson Jr.",
    "travis etienne": "Travis Etienne Jr.",
    "harold fannin": "Harold Fannin Jr.",
    "brian thomas": "Brian Thomas Jr.",
    "oronde gadsden": "Oronde Gadsden II",
    "luther burden": "Luther Burden III",
    "aj barner": "A.J. Barner",
    "dj moore": "D.J. Moore",
    # Ourlads spells him "Tre' Harris"; the model's data and the board say "Tre
    # Harris". Without this the longest-name tie-break picks the apostrophe.
    "tre harris": "Tre Harris",
}


def _canon_player_name(n) -> str:
    # Match ignoring generational suffix, periods (AJ vs A.J.), case, and
    # extra whitespace, so spelling variants of one player collapse together.
    s = _NAME_SUFFIX_RE.sub("", str(n).strip())
    # Apostrophes too: "Tre' Harris" and "Tre Harris" are one player, saved under
    # two spellings by boards built off different roster sources.
    s = s.replace(".", "").replace("'", "").replace("\u2019", "")
    return _re.sub(r"\s+", " ", s).strip().lower()


def _preferred_display_name(names) -> str:
    """Pick the display name for a set of variants: prefer the suffixed form
    (e.g. 'Marvin Harrison Jr.'), else the longest; deterministic tie-break."""
    uniq = list(dict.fromkeys(str(n).strip() for n in names))
    suffixed = [n for n in uniq if _NAME_SUFFIX_RE.search(n)]
    pool = suffixed if suffixed else uniq
    return sorted(pool, key=lambda s: (-len(s), s))[0]


def _equal_average(game_df, num_cols):
    return _round_disp(
        game_df.groupby(["player_name", "position", "_team"])[num_cols].mean().reset_index()
    )


# ── Per-team scenario toggles ────────────────────────────────────────────────
# Save keys encode a scenario in their middle segment ("" = Base/full health,
# e.g. "Hall_Out" = a named injury scenario). The Saved Projections view lets a
# trader pick a scenario PER TEAM, PER MATCHUP from the team header panel; the
# slate-wide control just seeds the default and reads back "Custom" the moment
# any one team diverges. These helpers are shared by both the trader and client
# apps (render_dk_projections is the single entry point for both).
_SCN_ALL = "All"
_SCN_FULL = "Full Health"


def _scn_options(scenarios):
    """Toggle options for one team given the Scenario values it actually has.

    "" is Base (full health); non-empty strings are named injury scenarios.
      - only Base saves      -> ["Full Health"]            (nothing to toggle)
      - Base + named          -> ["All", "Full Health", <named…>]
      - named but no Base     -> ["All", <named…>]
    """
    named = sorted(s for s in scenarios if s)
    if not named:
        return [_SCN_FULL]
    opts = [_SCN_ALL]
    if "" in scenarios:
        opts.append(_SCN_FULL)
    return opts + named


def _scn_fallback(opts, base):
    """The selection a team lands on when it can't honor the slate default
    (e.g. slate=All but a team only has Base saves). A forced fallback, so it
    deliberately does NOT count as a user override for the "Custom" indicator."""
    if base in opts:
        return base
    if _SCN_FULL in opts:
        return _SCN_FULL
    return opts[0]


def _scn_filter(df, selected):
    """Rows for the selected scenario: All = everything, Full Health = Base only,
    otherwise the single named scenario."""
    if selected == _SCN_ALL:
        return df
    if selected == _SCN_FULL:
        return df[df["Scenario"] == ""]
    return df[df["Scenario"] == selected]


def _weighted_display_df(game_df, num_cols, wt_df):
    """Per-player display frame using a projector-weight matrix (team x projector,
    0-1). Falls back to an equal average per team wherever weights are empty or
    sum to zero, and overall if the matrix is empty. Used by the trader
    'Average' (trader weights) and the client view (client weights)."""
    if wt_df is None or wt_df.empty or "Projector" not in game_df.columns or "_team" not in game_df.columns:
        return _equal_average(game_df, num_cols)
    parts = []
    for _team_key in game_df["_team"].unique():
        _team_rows = game_df[game_df["_team"] == _team_key]
        _team_wts = wt_df.loc[_team_key] if _team_key in wt_df.index else pd.Series(dtype=float)
        if _team_wts.empty or _team_wts.sum() == 0:
            parts.append(_equal_average(_team_rows, num_cols))
            continue
        _wt_rows = []
        for (_pname, _pos, _tm), _pg in _team_rows.groupby(["player_name", "position", "_team"]):
            _total_w = 0.0
            _accum = {c: 0.0 for c in num_cols if c in _pg.columns}
            # Weight is tracked PER COLUMN, not once for the player: a projector
            # who left a cell blank must not count in that column's denominator.
            # A save made before TD pricing existed has a blank td_true, and
            # charging it to the divisor as if it were p=0 halved the consensus
            # probability — +800 rendered +1700. groupby().mean() skips NaN, so
            # this also keeps the weighted and equal-average paths agreeing.
            _colw = {c: 0.0 for c in _accum}
            for _, _r in _pg.iterrows():
                _w = float(_team_wts.get(_r.get("Projector", ""), 0))
                if _w <= 0:
                    continue
                _total_w += _w
                for _c in _accum:
                    _v = pd.to_numeric(_r.get(_c), errors="coerce")
                    if pd.notna(_v):
                        _accum[_c] += _w * _v
                        _colw[_c] += _w
            if _total_w > 0:
                _row_out = {"player_name": _pname, "position": _pos, "_team": _tm}
                for _c in _accum:
                    _row_out[_c] = (_accum[_c] / _colw[_c]
                                    if _colw[_c] > 0 else float("nan"))
                _wt_rows.append(_row_out)
        parts.append(_round_disp(pd.DataFrame(_wt_rows)) if _wt_rows
                     else _equal_average(_team_rows, num_cols))
    return pd.concat(parts, ignore_index=True) if parts else _equal_average(game_df, num_cols)


def _logo_b64(abbr: str) -> str:
    p = get_team_logo_path(abbr)
    if not p:
        return ""
    with open(p, "rb") as f:
        return _b64.b64encode(f.read()).decode()


@st.cache_data(ttl=60)
def _load_dk_projections(week):
    return _sheets_read(week)


# The weights matrices were read from Google on EVERY rerun with a game open —
# two round trips each (open + read), paid again on every scenario toggle,
# projector change and slate-chip click. They are edited by hand a few times a
# season, so five minutes of staleness is invisible and the reruns stop waiting
# on the network.
#
# Only a GOOD read is cached. sheets._load_weights_matrix turns any failure (a
# 429, a timeout, a token refresh) into an empty frame, and cache_data would keep
# that for the full five minutes — silently pricing the Average and client views
# as a plain equal average. So an empty result raises inside the cached function,
# which cache_data does not store, and the caller falls back to equal weights for
# that one rerun only; the next rerun asks Google again, exactly as before.
class _NoWeights(Exception):
    pass


@st.cache_data(ttl=300, show_spinner=False)
def _cached_trader_weights():
    from sheets import load_projector_weights
    _w = load_projector_weights()
    if _w is None or _w.empty:
        raise _NoWeights()
    return _w


@st.cache_data(ttl=300, show_spinner=False)
def _cached_client_weights():
    from sheets import load_client_projector_weights
    _w = load_client_projector_weights()
    if _w is None or _w.empty:
        raise _NoWeights()
    return _w


def _load_trader_weights():
    try:
        return _cached_trader_weights()
    except _NoWeights:
        return pd.DataFrame()


def _load_client_weights():
    try:
        return _cached_client_weights()
    except _NoWeights:
        return pd.DataFrame()


def _slate_counts(dk_df, week, slate):
    """[(home, away, home_count, away_count)] for every game in `week`.

    Starts from the SCHEDULE rather than from the saved rows, which is the whole
    point: a matchup nobody has projected has no saved rows to be found in, so
    counting up from the sheet can only ever list the games already done. The
    zeroes are the signal here.

    A count is distinct PROJECTORS, not distinct save_keys. A projector who also
    saved a "Chase_Out" scenario has still only given one opinion on the game, and
    this number is read as "how many people have looked at this".
    """
    if slate is None or getattr(slate, "empty", True):
        return []
    if not {"week", "home", "away"} <= set(slate.columns):
        return []
    _g = slate[slate["week"] == int(week)]
    if _g.empty:
        return []

    # save_key is {team}_{scenario}_{projector} with the projector always last.
    # Team -> game is 1:1 within a week, so a per-team tally is already per-game.
    _by_team = {}
    if dk_df is not None and not dk_df.empty and "save_key" in dk_df.columns:
        _parts = dk_df["save_key"].dropna().astype(str).str.strip().str.split("_")
        _by_team = (pd.DataFrame({"team": _parts.str[0], "projector": _parts.str[-1]})
                    .drop_duplicates()
                    .groupby("team")["projector"].nunique().to_dict())

    _rows = [(str(_r.home), str(_r.away),
              int(_by_team.get(str(_r.home), 0)), int(_by_team.get(str(_r.away), 0)))
             for _r in _g.itertuples(index=False)]
    # Fewest first — the list exists to say what still needs doing.
    _rows.sort(key=lambda t: (t[2] + t[3], t[0]))
    return _rows


def _open_game(home, away):
    """Chip callback: queue a game for the Game dropdown below.

    Queued rather than written straight into dk_game_filter because the label has
    to match one of that selectbox's options exactly, and those are only known
    once render_dk_projections has read the week's saves.
    """
    st.session_state["_dk_pending_game"] = (home, away)


def _render_slate_summary(week, dk_df, slate):
    """One compact block of per-team saved-projection counts for the week's slate.

    Deliberately small and above the filters: it is a coverage check read on the
    way past. Red 0 / amber 1 / green 2+ per team, games sorted fewest-first, so
    the ones to pick up next are the ones read first. Each chip is a button that
    opens that game in the board below.
    """
    _rows = _slate_counts(dk_df, week, slate)
    if not _rows:
        return
    _none = sum(1 for _r in _rows if _r[2] + _r[3] == 0)

    def _pill(n):
        _c = "red" if n == 0 else "orange" if n == 1 else "green"
        return f":{_c}-background[**{n}**]"

    _hdr = (f'<span style="font-size:12px;font-weight:700;color:#475569;">'
            f'WEEK {week} COVERAGE</span>'
            f'<span style="font-size:12px;color:#94a3b8;"> &nbsp;'
            f'{len(_rows) - _none} of {len(_rows)} games projected'
            + (f' &middot; <b style="color:#b91c1c;">{_none} with none yet</b>'
               if _none else ' &middot; all covered')
            + '</span>')
    st.markdown(_hdr, unsafe_allow_html=True)

    # Eight to a row, so a 16-game slate reads as 8 and 8. Real st.buttons rather
    # than HTML so a click can reach Python; sixteen buttons are a few KB of state,
    # nothing like the cost of the tables they open.
    _PER_ROW = 8
    for _i in range(0, len(_rows), _PER_ROW):
        _cols = st.columns(_PER_ROW)
        for _col, (_h, _a, _hc, _ac) in zip(_cols, _rows[_i:_i + _PER_ROW]):
            _col.button(f"{_pill(_hc)} {_h} / {_a} {_pill(_ac)}",
                        key=f"dk_slate_{week}_{_h}_{_a}",
                        on_click=_open_game, args=(_h, _a))


def render_dk_projections(next_week, *, show_projector=True, slate=None):
    # Week selector. int() and the clamp are both load-bearing, because this one
    # function is the entry point for two separate apps and neither of them owns
    # this line: a numpy scalar from a caller's parquet read fails selectbox's
    # `isinstance(index, int)` check, and a next_week outside 1-18 (an offseason
    # parquet, or a playoff week) fails its range check. Either one takes the whole
    # tab down with a redacted error on Cloud, so the boundary coerces rather than
    # trusting the caller.
    # Claimed before the week selector so the coverage line renders between the tab
    # bar and the filters, which is where it was asked for. It cannot simply be
    # written there: it needs the selected week, and that is not known until the
    # selectbox below has run. So the slot is reserved now and filled afterwards.
    #
    # Trader tool only. The client view has no business seeing which of the desk's
    # matchups are still unprojected.
    _slate_slot = st.container() if show_projector else None

    _idx = min(max(int(next_week or 1), 1), 18) - 1
    _dk_week = st.selectbox("Week", list(range(1, 19)), index=_idx, key="dk_week")

    _dk_df = _load_dk_projections(_dk_week)

    # Taken off the queue on EVERY run, before any early return: left in place on a
    # week with no saves (where the Game box below never renders), it would sit
    # there and be applied to whatever week next has saves — the wrong game.
    _pend = st.session_state.pop("_dk_pending_game", None)
    # Matchup labels on this week's slate, in both orders. A chip-opened game with
    # no saves is only kept selectable while it is still on the week being viewed.
    _slate_games = set()
    if slate is not None and not getattr(slate, "empty", True) and             {"week", "home", "away"} <= set(slate.columns):
        for _sr in slate[slate["week"] == int(_dk_week)].itertuples(index=False):
            _slate_games |= {f"{_sr.home} vs. {_sr.away}", f"{_sr.away} vs. {_sr.home}"}

    # Filled before any of the early returns below (no saves at all, or no game
    # picked yet) — an empty week is exactly when the coverage line matters most.
    if _slate_slot is not None:
        with _slate_slot:
            _render_slate_summary(_dk_week, _dk_df, slate)

    if not _dk_df.empty and "saved_at" in _dk_df.columns and "save_key" in _dk_df.columns and "player_name" in _dk_df.columns:
        _dk_df["saved_at"] = pd.to_datetime(_dk_df["saved_at"], errors="coerce")
        # De-dupe on the suffix-stripped name so a projector who re-saved a game
        # after the roster source changed the spelling ("Marvin Harrison" ->
        # "Marvin Harrison Jr.") keeps only their latest save, not both.
        _dedup_key = _dk_df["player_name"].map(_canon_player_name)
        _dk_df = (_dk_df.assign(_dedup_key=_dedup_key)
                        .sort_values("saved_at", ascending=False)
                        .drop_duplicates(subset=["save_key", "_dedup_key"], keep="first")
                        .drop(columns=["_dedup_key"])
                        .reset_index(drop=True))

    if _dk_df.empty:
        # A chip clicked on a week nobody has saved into: there is no board to
        # open, so say which game and why rather than doing nothing — silence
        # read as a broken button.
        if _pend:
            st.info(f"Nobody has saved {_pend[0]} vs. {_pend[1]} for Week {_dk_week} yet "
                    f"— no saved projections this week at all.")
        else:
            st.info(f"No projections saved for Week {_dk_week} yet.")
    else:
        # Parse save_key: {team}_{scenario}_{projector} or {team}_{projector}
        # Projector is always the last segment after final "_"
        # Scenario (players out) is everything between team and projector
        if "save_key" in _dk_df.columns:
            _dk_df["save_key"] = _dk_df["save_key"].str.strip()
            _dk_df["_team"] = _dk_df["save_key"].str.split("_").str[0]
            _dk_df["Projector"] = _dk_df["save_key"].str.split("_").str[-1]
            # Scenario: middle parts (between team and projector), empty if base projection
            def _parse_scenario(sk):
                parts = sk.split("_")
                if len(parts) <= 2:
                    return ""
                return "_".join(parts[1:-1])
            _dk_df["Scenario"] = _dk_df["save_key"].apply(_parse_scenario)
        # Clean game names (remove underscores)
        if "game" in _dk_df.columns:
            _dk_df["game_clean"] = _dk_df["game"].str.replace("_", " ").str.replace("vs", "vs.")

        # Collapse player-name variants that differ only by a generational
        # suffix (see _NAME_SUFFIX_RE note) so one player doesn't split into
        # two rows across saves. Normalize within team; display the fuller form.
        if "player_name" in _dk_df.columns and "_team" in _dk_df.columns:
            _pkey = _dk_df["player_name"].map(_canon_player_name)
            _disp = (_dk_df.assign(_pkey=_pkey)
                          .groupby(["_team", "_pkey"])["player_name"]
                          .agg(_preferred_display_name))
            _dk_df["player_name"] = [
                _NAME_DISPLAY_OVERRIDES.get(k, _disp.get((t, k), nm))
                for t, k, nm in zip(_dk_df["_team"], _pkey, _dk_df["player_name"])
            ]

        # Filters
        if show_projector:
            _dk_f1, _dk_f2 = st.columns(2)
        else:
            _dk_f1 = st.columns([1])[0]
        with _dk_f1:
            _all_games = sorted(_dk_df["game_clean"].dropna().unique().tolist()) if "game_clean" in _dk_df.columns else []
            # A slate chip was clicked. Saved labels are "HOME vs. AWAY" off the
            # odds feed, so try both orders before minting one. A game nobody has
            # saved yet is added as an option so the click still lands — the board
            # renders a chosen matchup even at zero saves, which is what shows the
            # trader it still needs doing.
            if _pend:
                _ph, _pa = _pend
                _lbl = next((g for g in (f"{_ph} vs. {_pa}", f"{_pa} vs. {_ph}")
                             if g in _all_games), f"{_ph} vs. {_pa}")
                if _lbl not in _all_games:
                    _all_games = sorted(_all_games + [_lbl])
                st.session_state["dk_game_filter"] = _lbl
            else:
                # Keep a chip-opened zero-save game selectable on later reruns —
                # but only while it is on THIS week's slate. Without that check a
                # game picked in week 3 was added to week 4's list after a week
                # switch and stayed selected, rendering a matchup not on the slate
                # (Streamlit would otherwise have reset the stale value itself).
                _cur = st.session_state.get("dk_game_filter")
                if (_cur and _cur != "All Games" and _cur not in _all_games
                        and _cur in _slate_games):
                    _all_games = sorted(_all_games + [_cur])
            _dk_game = st.selectbox("Game", ["All Games"] + _all_games, index=None,
                                    placeholder="Select a game…", key="dk_game_filter")

        # Defer the (expensive) per-game rendering until a game is chosen.
        # The week's data still loads above to populate this dropdown, but that
        # is a single cached sheet read — the cost is rendering every game's
        # tables at once, which "All Games" (opt-in) still does on demand.
        if not _dk_game:
            st.info("Select a game to view its projections.")
            return

        _dk_filt = _dk_df.copy()
        if _dk_game != "All Games":
            _dk_filt = _dk_filt[_dk_filt.game_clean == _dk_game]

        if show_projector:
            with _dk_f2:
                _all_projectors = sorted(_dk_filt["Projector"].dropna().unique().tolist()) if "Projector" in _dk_filt.columns else []
                _dk_projector = st.selectbox("Projector", ["Average"] + _all_projectors, key="dk_projector_filter")
        else:
            # Locked client view: weighted blend using the CLIENT weights sheet,
            # which defaults to an equal average wherever weights are unset.
            _dk_projector = "__CLIENT_WEIGHTED__"

        # Scenario is no longer filtered slate-wide here — it's chosen per team,
        # per matchup from each team's header panel (see the slate control and the
        # per-team toggles below). _dk_filt keeps every scenario's rows.

        # Convert numeric columns
        _num_cols = ["pass_snap_pct", "tgt_rate", "catch_pct", "y_catch",
                     "rush_snap_pct", "carry_rate", "ypc",
                     "proj_tgts", "proj_rec", "proj_rec_yds", "proj_carries", "proj_rush_yds",
                     "qb_td_att_pct", "qb_int_att_pct",
                     "proj_pass_att", "proj_comp", "proj_pass_yds", "proj_pass_tds",
                     "proj_ints", "proj_scrambles", "proj_scramble_yds", "proj_total_rush_yds",
                     "team_plays", "team_dropback_pct", "team_sack_pct", "team_throwaway_pct",
                     "team_scramble_pct", "tgt_share", "carry_share"]
        # Anytime-TD pricing is the desk's own fair number, so it renders in the
        # internal tab only. The client view (show_projector=False) never reads
        # these columns — flip _show_td to expose them there.
        _show_td = show_projector
        if _show_td:
            _num_cols += ["td_share_pct", "team_td_pts_pct", "team_dst_pct"]
        # td_true is deliberately NOT in _num_cols — see _TD_PRICE_COLS.
        _price_cols = _TD_PRICE_COLS if _show_td else {}
        for _c in list(_num_cols) + list(_price_cols):
            if _c in _dk_filt.columns:
                _dk_filt[_c] = pd.to_numeric(_dk_filt[_c], errors="coerce")
        for _pc, _pp in _price_cols.items():
            if _pc in _dk_filt.columns:
                _dk_filt[_pp] = _dk_filt[_pc].apply(
                    lambda v: american_to_prob(v) if pd.notna(v) and v != 0 else float("nan")
                )
                _num_cols.append(_pp)
        # A tab saved before a column was appended comes back without it, and the
        # groupby below indexes _num_cols directly.
        _num_cols = [_c for _c in _num_cols if _c in _dk_filt.columns]

        # Which games to render:
        #  - a specific matchup always shows (both teams, even with zero saves,
        #    so the per-team saved-projection count can render a 0)
        #  - "All Games" shows every game with >=1 save under the active filters
        if _dk_game != "All Games":
            _games_to_render = [_dk_game]
        else:
            _games_to_render = sorted(_dk_filt["game_clean"].dropna().unique().tolist())

        # Scenario toggle options per (game, team), driven by the save keys that
        # team actually has. Computed once so the slate control and the per-team
        # toggles below agree on the option set.
        def _scn_key(game_clean, team_abbr):
            return f"dk_scn_{_dk_week}_{game_clean}_{team_abbr}"

        _team_scn_opts = {}
        for _g in _games_to_render:
            _gdf = _dk_filt[_dk_filt["game_clean"] == _g]
            _gt = _g.replace("vs.", "").split()
            for _ta in [(_gt[0].strip() if _gt else ""),
                        (_gt[-1].strip() if len(_gt) > 1 else "")]:
                if not _ta:
                    continue
                _scns = (set(_gdf[_gdf["_team"] == _ta]["Scenario"].unique())
                         if "Scenario" in _gdf.columns else set())
                _team_scn_opts[(_g, _ta)] = _scn_options(_scns)

        # Slate-wide scenario default. Only surfaced when at least one team has a
        # named (non-base) scenario — otherwise every team is Full Health and
        # there is nothing to pick. Shows "Custom" when any team diverges;
        # re-picking All or Full Health resets every team to that choice.
        _has_named = any(len(_o) > 1 for _o in _team_scn_opts.values())
        if _has_named:
            _slate_base = st.session_state.get("dk_scn_base", _SCN_FULL)
            _is_custom = any(
                st.session_state.get(_scn_key(_g, _t), _scn_fallback(_o, _slate_base))
                != _scn_fallback(_o, _slate_base)
                for (_g, _t), _o in _team_scn_opts.items()
            )
            # Set before the widget is instantiated so the selectbox adopts it.
            st.session_state["dk_scn_slate"] = "Custom" if _is_custom else _slate_base

            def _on_slate_change():
                _v = st.session_state.get("dk_scn_slate")
                if _v in (_SCN_ALL, _SCN_FULL):
                    st.session_state["dk_scn_base"] = _v
                    for (_g, _t), _o in _team_scn_opts.items():
                        st.session_state[_scn_key(_g, _t)] = _scn_fallback(_o, _v)
                # A manual "Custom" pick is a no-op — the next run recomputes the
                # indicator from the per-team toggles.

            st.columns([1])[0].selectbox(
                "Scenario (slate default)",
                [_SCN_ALL, _SCN_FULL, "Custom"],
                key="dk_scn_slate",
                on_change=_on_slate_change,
                help="Default scenario applied to every team. Change a single "
                     "team's scenario on its header below and this reads "
                     "“Custom”; re-pick All or Full Health to reset every team.",
            )

        if _games_to_render:
            import streamlit.components.v1 as _stc
            _dk_css = '<style>.dk-tbl table{border-collapse:collapse;width:100%;font-family:-apple-system,sans-serif;}.dk-tbl th{padding:6px 8px;font-size:13px;font-weight:800;color:#1a1f26;border-bottom:2px solid #bbb;text-align:center;}.dk-tbl td{padding:6px 8px;border-bottom:1px solid #e8e8e8;font-size:14px;font-weight:500;color:#1a1f26;text-align:center;}.dk-tbl td:first-child,.dk-tbl th:first-child{text-align:left;}</style>'

            _market_cols = {"Carries", "Rush Yds", "Rec", "Rec Yds", "Total Rush Yds", "Total Rush Att",
                           "Pass TDs", "INTs", "Pass Att", "Comp", "Pass Yds",
                           "TD True"}

            def _dk_render_table(tbl_df):
                if tbl_df.empty:
                    return
                styled = tbl_df.style.set_properties(**{"color": "#1a1f26", "font-size": "14px", "padding": "6px 8px"}).hide(axis="index").format(precision=2, na_rep="")
                green_cols = [c for c in tbl_df.columns if c in _market_cols]
                if green_cols:
                    styled = styled.set_properties(subset=green_cols, **{"color": "#16a34a", "font-weight": "600"})
                html = styled.to_html()
                _stc.html(f'<html><head>{_dk_css}</head><body><div class="dk-tbl">{html}</div></body></html>', height=52 + len(tbl_df) * 32, scrolling=True)

            # Projector weights loaded once per render (not per team/scenario).
            _wt_df = None
            if _dk_projector == "__CLIENT_WEIGHTED__":
                # Client view: client weights sheet (equal average where unset).
                _wt_df = _load_client_weights()
            elif _dk_projector == "Average":
                # Trader view: trader weights sheet.
                _wt_df = _load_trader_weights()

            _RENAME = {
                "player_name": "Player", "position": "Pos",
                "pass_snap_pct": "Pass Snp%", "tgt_rate": "Tgt Rate",
                "catch_pct": "Catch%", "y_catch": "Y/Catch",
                "rush_snap_pct": "Rush Snp%", "carry_rate": "Carry Rate",
                "ypc": "YPC",
                "proj_tgts": "Targets", "proj_rec": "Rec",
                "proj_rec_yds": "Rec Yds", "proj_carries": "Carries",
                "proj_rush_yds": "Rush Yds",
                "proj_pass_att": "Pass Att", "proj_comp": "Comp",
                "proj_pass_yds": "Pass Yds", "proj_pass_tds": "Pass TDs",
                "proj_ints": "INTs", "proj_scrambles": "Scrambles",
                "proj_scramble_yds": "Scram Yds", "proj_total_rush_yds": "Total Rush Yds",
                "qb_td_att_pct": "TD/Att%", "qb_int_att_pct": "INT/Att%",
                "team_plays": "Plays", "team_dropback_pct": "Dropback%",
                "team_sack_pct": "Sack%", "team_throwaway_pct": "Throwaway%",
                "team_scramble_pct": "Scramble%",
                "tgt_share": "Tgt Share", "carry_share": "Carry Share",
                "td_share_pct": "TD Share", "td_true": "TD True",
            }

            def _build_disp(_src):
                """Aggregate one team's scenario-filtered rows into the display
                frame — weighted/equal (Average, client) or a single projector —
                then restore TD prices, rename, and format. Empty when there's
                nothing to show. Scenario filtering happens BEFORE this, so under
                'All' every saved row (base + injury) is blended as-is."""
                if _src is None or _src.empty:
                    return pd.DataFrame()
                if _dk_projector in ("__CLIENT_WEIGHTED__", "Average"):
                    _d = _weighted_display_df(_src, _num_cols, _wt_df)
                else:
                    _pf = _src[_src.Projector == _dk_projector]
                    if _pf.empty:
                        return pd.DataFrame()
                    _d = _round_disp(
                        _pf[["player_name", "position", "_team"]
                            + [c for c in _num_cols if c in _pf.columns]].copy()
                    ).reset_index(drop=True)
                # Probabilities -> prices. Must run after aggregation, never before.
                _d = _restore_td_prices(_d)
                _d = _d.rename(columns=_RENAME)
                # Others rows: show N/A for snap%/rate columns.
                _is_others = _d["Player"].str.contains("Others", na=False)
                if _is_others.any():
                    for _na_col in ["Pass Snp%", "Tgt Rate", "Rush Snp%", "Carry Rate"]:
                        if _na_col in _d.columns:
                            _d[_na_col] = _d[_na_col].astype(object)
                            _d.loc[_is_others, _na_col] = "N/A"
                # Signed TD-True so the column reads as odds ("+580", not "580").
                if "TD True" in _d.columns:
                    _d["TD True"] = _d["TD True"].map(_fmt_td_price)
                return _d

            for _game in _games_to_render:
                _game_df = _dk_filt[_dk_filt.game_clean == _game]
                if _game_df.empty:
                    continue

                # Get teams from game
                _game_teams = _game.replace("vs.", "").split()
                _t1 = _game_teams[0].strip() if len(_game_teams) > 0 else ""
                _t2 = _game_teams[-1].strip() if len(_game_teams) > 1 else ""

                # Each team picks its own scenario (All / Full Health / a named
                # injury scenario) from its header, so filtering + aggregation is
                # per team, not once per game.
                for _team_abbr in [_t1, _t2]:
                    if not _team_abbr:
                        continue
                    _team_all = _game_df[_game_df["_team"] == _team_abbr]
                    _opts = _team_scn_opts.get(
                        (_game, _team_abbr),
                        _scn_options(set(_team_all["Scenario"].unique())
                                     if "Scenario" in _team_all.columns else set()))
                    _slate_base = st.session_state.get("dk_scn_base", _SCN_FULL)
                    _scn_default = _scn_fallback(_opts, _slate_base)
                    _skey = _scn_key(_game, _team_abbr)
                    _selected = st.session_state.get(_skey, _scn_default)
                    if _selected not in _opts:
                        _selected = _scn_default
                    _team_src = _scn_filter(_team_all, _selected)
                    _team_df = _build_disp(_team_src)

                    # Under 'Average' we always render both teams' headers (with a
                    # 0 count if need be); otherwise skip a team with nothing to show.
                    _show_count = show_projector and _dk_projector == "Average"
                    if (_team_df is None or _team_df.empty) and not _show_count:
                        continue

                    # Team header
                    _tlogo = _logo_b64(_team_abbr)
                    _tcolor = get_team_color(_team_abbr)
                    _tname = get_team_name(_team_abbr)
                    _hdr = f'<img src="data:image/png;base64,{_tlogo}" style="width:32px;height:32px;margin-right:10px;"/>' if _tlogo else ""
                    _hdr += f'<span style="font-size:22px;font-weight:800;color:{_tcolor};">{_tname}</span>'
                    _cnt_html = ""
                    if _show_count:
                        _cnt = int(_team_src["save_key"].nunique()) if "save_key" in _team_src.columns else 0
                        _cnt_label = "saved projection" if _cnt == 1 else "saved projections"
                        _cnt_html = (
                            f'<span title="Saved projections for this team under the '
                            f'selected scenario" '
                            f'style="margin-left:auto;font-size:14px;font-weight:700;'
                            f'color:{_tcolor};background:{_tcolor}1a;'
                            f'border:1px solid {_tcolor}55;padding:2px 14px;'
                            f'border-radius:14px;">'
                            f'<b style="font-size:16px;">{_cnt}</b> {_cnt_label}</span>'
                        )
                    st.markdown(f'<div style="display:flex;align-items:center;margin:20px 0 6px 0;padding:8px 0;border-bottom:2px solid {_tcolor}40;">{_hdr}{_cnt_html}</div>', unsafe_allow_html=True)

                    # Per-team scenario toggle. Only drawn when the team has a real
                    # choice; a base-only team just notes it's Full Health (shown
                    # only when the slate has scenarios in play at all).
                    if len(_opts) > 1:
                        st.radio("Scenario", _opts, index=_opts.index(_selected),
                                 key=_skey, horizontal=True, label_visibility="collapsed")
                    elif _has_named:
                        st.caption("Scenario: Full Health")

                    # A team nobody has saved still gets its header and its "0"
                    # badge above — that is the point under Average — but there is
                    # nothing to tabulate. _build_disp on zero rows returns a frame
                    # with NO columns, so the tables below raised KeyError 'Pos' and
                    # took down every game where only one side had been projected:
                    # the common mid-week case, whether opened from the Game box or
                    # a slate chip.
                    if _team_df is None or _team_df.empty or "Pos" not in _team_df.columns:
                        st.caption("No saved projections for this team yet.")
                        continue

                    # Team Volume (show once per team)
                    _tv_cols = ["Plays", "Dropback%", "Sack%", "Throwaway%", "Scramble%"]
                    _tv_avail = [c for c in _tv_cols if c in _team_df.columns]
                    if _tv_avail:
                        _tv_row = _team_df[_tv_avail].iloc[0:1]
                        if not _tv_row.empty and _tv_row.iloc[0].notna().any():
                            st.caption("**Team Volume**")
                            _dk_render_table(_tv_row.reset_index(drop=True))

                    # QB / Passing
                    _qb_df = _team_df[_team_df["Pos"] == "QB"].copy()
                    if not _qb_df.empty:
                        st.caption("**Passing**")
                        if "Scrambles" in _qb_df.columns and "Carries" in _qb_df.columns:
                            _qb_df["Total Rush Att"] = _qb_df["Scrambles"].fillna(0) + _qb_df["Carries"].fillna(0)
                        # Rename to drop % from display
                        if "TD/Att%" in _qb_df.columns:
                            _qb_df = _qb_df.rename(columns={"TD/Att%": "TD/Att", "INT/Att%": "INT/Att"})
                        # This table has no Pos column, so "TD True" (the QB's
                        # ANYTIME-TD price, not his passing TDs) goes after Player.
                        _qb_pass_cols = ["Player", "TD True", "TD/Att", "Pass TDs", "INT/Att", "INTs", "Pass Att", "Comp", "Pass Yds", "Scrambles", "Scram Yds", "Total Rush Att", "Total Rush Yds"]
                        _qb_pass_cols = [c for c in _qb_pass_cols if c in _qb_df.columns]
                        if _qb_pass_cols:
                            _dk_render_table(_qb_df[_qb_pass_cols].reset_index(drop=True))

                    # Rushing (Others at bottom)
                    # "TD True" sits right after Pos in both player tables. It is
                    # absent from _team_df entirely when show_projector is False
                    # (the price is never carried through the aggregation), so the
                    # filter below is what gates it out of the client view.
                    _rush_cols = ["Player", "Pos", "TD True", "Rush Snp%", "Carry Rate", "Carry Share", "YPC", "Carries", "Rush Yds"]
                    _rush_cols = [c for c in _rush_cols if c in _team_df.columns]
                    # For QB rows, the Rushing line must reflect TOTAL rushing
                    # (designed + scrambles): the "Rush Yds"/"Carries" fields hold
                    # designed-only values, which drop scramble yardage for QBs.
                    _rush_src = _team_df.copy()
                    if "Pos" in _rush_src.columns:
                        _qb_mask = _rush_src["Pos"] == "QB"
                        if _qb_mask.any():
                            if "Total Rush Yds" in _rush_src.columns and "Rush Yds" in _rush_src.columns:
                                _rush_src.loc[_qb_mask, "Rush Yds"] = _rush_src.loc[_qb_mask, "Total Rush Yds"]
                            if "Scrambles" in _rush_src.columns and "Carries" in _rush_src.columns:
                                _rush_src.loc[_qb_mask, "Carries"] = (
                                    _rush_src.loc[_qb_mask, "Carries"].fillna(0)
                                    + _rush_src.loc[_qb_mask, "Scrambles"].fillna(0))
                            if {"YPC", "Carries", "Rush Yds"} <= set(_rush_src.columns):
                                _ypc_mask = _qb_mask & (_rush_src["Carries"] > 0)
                                _rush_src.loc[_ypc_mask, "YPC"] = (
                                    _rush_src.loc[_ypc_mask, "Rush Yds"] / _rush_src.loc[_ypc_mask, "Carries"]).round(1)
                    _rush_tbl = _rush_src[_rush_src["Carries"].notna() & (_rush_src["Carries"] > 0)][_rush_cols] if "Carries" in _rush_src.columns else pd.DataFrame()
                    if not _rush_tbl.empty:
                        st.caption("**Rushing**")
                        _rush_others = _rush_tbl[_rush_tbl["Player"].str.contains("Others", na=False)]
                        _rush_main = _rush_tbl[~_rush_tbl["Player"].str.contains("Others", na=False)]
                        _rush_sorted = pd.concat([_rush_main.sort_values("Rush Yds", ascending=False), _rush_others])
                        _dk_render_table(_rush_sorted.reset_index(drop=True))

                    # Receiving (Others at bottom)
                    _rec_cols = ["Player", "Pos", "TD True", "Pass Snp%", "Tgt Rate", "Tgt Share", "Catch%", "Y/Catch", "Targets", "Rec", "Rec Yds"]
                    _rec_cols = [c for c in _rec_cols if c in _team_df.columns]
                    _rec_tbl = _team_df[_team_df["Targets"].notna() & (_team_df["Targets"] > 0)][_rec_cols] if "Targets" in _team_df.columns else pd.DataFrame()
                    if not _rec_tbl.empty:
                        st.caption("**Receiving**")
                        _rec_others = _rec_tbl[_rec_tbl["Player"].str.contains("Others", na=False)]
                        _rec_main = _rec_tbl[~_rec_tbl["Player"].str.contains("Others", na=False)]
                        _rec_sorted = pd.concat([_rec_main.sort_values("Rec Yds", ascending=False), _rec_others])
                        _dk_render_table(_rec_sorted.reset_index(drop=True))

                    # Touchdowns. Its own table because the population differs:
                    # bench scorers and the D/ST row have no snaps or yards.
                    if _show_td and "TD Share" in _team_df.columns:
                        _td_cols = [c for c in ["Player", "Pos", "TD Share", "TD True"]
                                    if c in _team_df.columns]
                        _td_tbl = _team_df[_team_df["TD Share"].notna()
                                           & (_team_df["TD Share"] > 0)][_td_cols]
                        if not _td_tbl.empty:
                            st.caption("**Touchdowns**")
                            _dk_render_table(
                                _td_tbl.sort_values("TD Share", ascending=False).reset_index(drop=True)
                            )

