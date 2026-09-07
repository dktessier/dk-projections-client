"""Standalone client-facing DK Projections app (Google SSO gated).

Deployed as its OWN Streamlit Community Cloud app from this same repo (main
file path = client_app.py), so it inherits every push automatically while
staying isolated from the internal tool. It reads the same saved projections
Google Sheet as the internal tool and renders the same tables via the shared
`dk_view.render_dk_projections`, but locked to a single output (plain equal
average of all projectors) with no Projector control.

Access is gated by Google sign-in (Streamlit native OIDC auth) plus an email
allow-list, so the app can be a *public* Community Cloud app (the workspace's
one private-app slot is used by the internal tool) while still limiting who
can actually see the projections.

Required secrets in THIS app's Secrets store (Community Cloud dashboard):

    [gcp_service_account]      # same block the internal app uses (Sheets access)
    ...

    allowed_emails = ["alice@theirteam.com", "bob@theirteam.com"]

    [auth]
    redirect_uri = "https://<this-app-url>/oauth2callback"
    cookie_secret = "<a long random string>"
    client_id = "<Google OAuth client id>"
    client_secret = "<Google OAuth client secret>"
    server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"

The Google OAuth client's Authorized redirect URI must exactly match
`redirect_uri` above. `allowed_emails` can be edited anytime in the dashboard
to add/remove viewers without a code change.
"""
from pathlib import Path

import pandas as pd
import streamlit as st

from dk_view import render_dk_projections

st.set_page_config(page_title="DK Projections", layout="wide")


def _parse_allowed_emails():
    """Allowed viewers from secrets. Accepts a TOML list or a comma-separated
    string. Returns a lowercased set (empty if unset)."""
    raw = st.secrets.get("allowed_emails", [])
    if isinstance(raw, str):
        raw = [p for p in raw.replace(";", ",").split(",")]
    return {str(e).strip().lower() for e in raw if str(e).strip()}


@st.cache_data(ttl=3600)
def _compute_next_week():
    """Default week for the selector, mirroring streamlit_app.py's logic.

    Reads only the season/week columns of the enriched parquet so the client
    app doesn't pull in the full internal data-load path. Falls back to week 1
    if the file is missing."""
    try:
        _p = Path(__file__).parent / "data" / "player_game_enriched.parquet"
        _df = pd.read_parquet(_p, columns=["season", "week"])
        if _df.empty:
            return 1
        _latest = int(_df["season"].max())
        _weeks = sorted(_df[_df.season == _latest]["week"].dropna().unique())
        if not _weeks:
            return 1
        return 1 if max(_weeks) >= 22 else int(max(_weeks)) + 1
    except Exception:
        return 1


# ── Auth gate ────────────────────────────────────────────────────────────────
if not st.user.is_logged_in:
    st.title("DK Projections")
    st.write("Please sign in with your Google account to continue.")
    st.button("Sign in with Google", type="primary", on_click=st.login)
    st.stop()

_email = (st.user.email or "").lower()
_allowed = _parse_allowed_emails()

# Fail closed: if no allow-list is configured, nobody gets in (prevents a
# misconfigured public app from exposing projections to any Google account).
if not _allowed or _email not in _allowed:
    st.title("DK Projections")
    st.error("Your account isn't authorized to view this app. "
             "Contact the projections team if you believe this is a mistake.")
    st.caption(f"Debug — detected email: {st.user.email!r} · "
               f"allow-list entries loaded: {len(_allowed)}")
    st.button("Sign out", on_click=st.logout)
    st.stop()

# ── Authorized ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.caption(f"Signed in as {st.user.email}")
    st.button("Sign out", on_click=st.logout)

st.title("DK Projections")
render_dk_projections(_compute_next_week(), show_projector=False)
