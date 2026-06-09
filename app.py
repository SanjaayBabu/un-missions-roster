"""
UN Permanent Missions — Attendee Roster Builder
Streamlit web app. Fetches live data from the UN e-Blue Book API.

Local:   streamlit run app.py
Deploy:  push to GitHub, connect at share.streamlit.io, main file = app.py
"""

import base64

import pandas as pd
import streamlit as st

from photos import fetch_wikipedia_photo
from scraper import fetch_bluebook

st.set_page_config(
    page_title="UN Missions — Attendee Roster",
    page_icon="🇺🇳",
    layout="wide",
)

# ── Data ──────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner="Fetching Blue Book data…")
def load_df() -> pd.DataFrame:
    missions = fetch_bluebook()
    rows = []
    for m in missions:
        for s in m.get("staff") or []:
            h = (s.get("honorific") or "").strip()
            rows.append({
                "country":       m["country"],
                "mission":       m["mission_name"] or "",
                "honorific":     h,
                "name":          s["full_name"],
                "display_name":  f"{h} {s['full_name']}".strip() if h else s["full_name"],
                "position":      ((s.get("title") or "").strip() or (s.get("rank") or "").strip()),
                "rank":          (s.get("rank") or "").strip(),
                "hom":           bool(s.get("is_head_of_mission")),
                "accreditation": (s.get("accreditation_date") or "")[:10],
                "phone":         m.get("phone") or "",
                "email":         m.get("email") or "",
                "address":       (m.get("address") or "").replace("\n", ", "),
            })
    return pd.DataFrame(rows)


@st.cache_data(ttl=86400, show_spinner=False)
def auto_photo(full_name: str, country: str) -> str | None:
    """Look up a Wikipedia headshot. Cached 24 h; returns URL or None."""
    return fetch_wikipedia_photo(full_name, country)


def titles_for_country(df: pd.DataFrame, country: str) -> list[str]:
    """Unique position titles for a country, HoM-held titles first then alphabetical."""
    cdf = df[df["country"] == country]
    hom_titles = list(cdf[cdf["hom"]]["position"].unique())
    other_titles = sorted(t for t in cdf["position"].unique() if t not in hom_titles)
    return hom_titles + other_titles


def staff_for_title(df: pd.DataFrame, country: str, title: str) -> pd.DataFrame:
    """All staff for a country + position title, sorted alphabetically."""
    return df[(df["country"] == country) & (df["position"] == title)].sort_values("name")


def resolve_by_name(df: pd.DataFrame, country: str, full_name: str) -> pd.Series | None:
    matches = df[(df["country"] == country) & (df["name"] == full_name)]
    return matches.iloc[0] if not matches.empty else None


# ── Diplomatic protocol ordering ──────────────────────────────────────────────

def _protocol_rank(position: str, rank: str) -> tuple[int, int]:
    """
    Returns (major, minor) sort key per UN diplomatic protocol:
    PR > Acting PR > CDA a.i. > DPR(H.E.) > DPR > PO > DPO > other ambassadors
    > Minister Counsellor > Counsellor > First Sec > Second Sec > Third Sec > Attaché
    Within DPR: Ambassador rank (H.E.) before non-Ambassador.
    Ties broken by country name in the caller.
    """
    pos = position.lower().strip()
    rnk = rank.lower().strip()
    is_ambassador = "ambassador" in rnk

    if "permanent representative" in pos and "deputy" not in pos and "acting" not in pos:
        return (1, 0)
    # US uses bare "Representative" for the same top role as PR
    if pos == "representative":
        return (1, 0)
    if "acting permanent representative" in pos:
        return (2, 0)
    if "chargé" in pos or "charge" in pos:
        return (3, 0)
    if "deputy permanent representative" in pos:
        return (4, 0 if is_ambassador else 1)
    # US equivalent of DPR
    if pos.startswith("deputy representative"):
        return (4, 0 if is_ambassador else 1)
    if "permanent observer" in pos and "deputy" not in pos:
        return (5, 0)
    if "deputy permanent observer" in pos:
        return (6, 0)
    if is_ambassador:
        return (7, 0)
    if "minister counsellor" in rnk or "minister plenipotentiary" in rnk:
        return (8, 0)
    if "counsellor" in rnk or "counsellor" in pos:
        return (9, 0)
    if "first secretary" in rnk:
        return (10, 0)
    if "second secretary" in rnk:
        return (11, 0)
    if "third secretary" in rnk:
        return (12, 0)
    if "attaché" in rnk or "attache" in rnk:
        return (13, 0)
    return (14, 0)


def sort_by_protocol(roster: list, df: pd.DataFrame) -> list:
    def key(entry):
        person = resolve_by_name(df, entry["country"], entry["full_name"])
        rnk = person["rank"] if person is not None else ""
        major, minor = _protocol_rank(entry["position"], rnk)
        return (major, minor, entry["country"])
    return sorted(roster, key=key)


# ── Photo helpers ─────────────────────────────────────────────────────────────

def _to_data_url(uploaded_file) -> str:
    data = uploaded_file.read()
    b64 = base64.b64encode(data).decode()
    mime = uploaded_file.type or "image/jpeg"
    return f"data:{mime};base64,{b64}"


@st.dialog("Set photo")
def photo_dialog(i: int) -> None:
    entry = st.session_state.roster[i]
    person = resolve_by_name(df, entry["country"], entry["full_name"])
    label = person["display_name"] if person is not None else entry["full_name"]
    st.markdown(f"**{label}**  ·  {entry['country']}")

    current = entry.get("photo")
    if current:
        st.image(current, width=140)
        if st.button("Remove photo", type="secondary"):
            st.session_state.roster[i]["photo"] = None
            st.rerun()
        st.divider()

    uploaded = st.file_uploader("Upload an image", type=["jpg", "jpeg", "png", "webp"])
    if uploaded is not None:
        st.session_state.roster[i]["photo"] = _to_data_url(uploaded)
        st.rerun()

    url = st.text_input("Or paste a photo URL", placeholder="https://…")
    if url and st.button("Use URL", type="primary"):
        st.session_state.roster[i]["photo"] = url
        st.rerun()


# ── Session state ─────────────────────────────────────────────────────────────

if "roster" not in st.session_state:
    # Each entry: {country, position, full_name, photo?}
    st.session_state.roster = []

if "view_mode" not in st.session_state:
    st.session_state.view_mode = "table"


def add_entry(country: str, position: str, full_name: str) -> None:
    st.session_state.roster.append({"country": country, "position": position, "full_name": full_name, "photo": None})


def remove_entry(i: int) -> None:
    st.session_state.roster.pop(i)


def on_roster_position_change(i: int) -> None:
    new_pos = st.session_state[f"rpos_{i}"]
    country = st.session_state.roster[i]["country"]
    st.session_state.roster[i]["position"] = new_pos
    staff = staff_for_title(df, country, new_pos)
    st.session_state.roster[i]["full_name"] = staff.iloc[0]["name"] if not staff.empty else ""
    st.session_state.roster[i]["photo"] = None  # person changed; clear photo


def on_roster_person_change(i: int) -> None:
    new_display = st.session_state[f"rperson_{i}"]
    country = st.session_state.roster[i]["country"]
    position = st.session_state.roster[i]["position"]
    staff = staff_for_title(df, country, position)
    match = staff[staff["display_name"] == new_display]
    if not match.empty:
        st.session_state.roster[i]["full_name"] = match.iloc[0]["name"]
        st.session_state.roster[i]["photo"] = None  # person changed; clear photo


def move_entry(i: int, direction: int) -> None:
    j = i + direction
    roster = st.session_state.roster
    if 0 <= j < len(roster):
        roster[i], roster[j] = roster[j], roster[i]


# ── Load data ─────────────────────────────────────────────────────────────────

df = load_df()
all_countries = sorted(df["country"].unique())

# ── Page header ───────────────────────────────────────────────────────────────

st.title("🇺🇳 UN Permanent Missions — Attendee Roster")

st.caption(
    f"Source: UN e-Blue Book (Protocol and Liaison Service) · "
    f"data for {df['country'].nunique()} missions · refreshes hourly"
)

st.divider()

# ── Add attendee ──────────────────────────────────────────────────────────────

st.subheader("Add attendee")

add_col1, add_col2, add_col3, add_col4 = st.columns([3, 3, 4, 2])

with add_col1:
    selected_country = st.selectbox(
        "Country",
        options=[""] + all_countries,
        format_func=lambda x: "Select a country…" if x == "" else x,
        key="add_country",
        label_visibility="collapsed",
    )

titles = titles_for_country(df, selected_country) if selected_country else []

with add_col2:
    if selected_country and titles:
        selected_title = st.selectbox(
            "Position",
            options=titles,
            key="add_title",
            label_visibility="collapsed",
        )
    elif selected_country:
        st.warning("No staff found for this country.")
        selected_title = None
    else:
        st.selectbox(
            "Position",
            options=["Select a country first"],
            disabled=True,
            label_visibility="collapsed",
        )
        selected_title = None

title_staff = staff_for_title(df, selected_country, selected_title) if (selected_country and selected_title) else pd.DataFrame()

with add_col3:
    if not title_staff.empty and len(title_staff) > 1:
        person_options = title_staff["display_name"].tolist()
        name_map = dict(zip(person_options, title_staff["name"].tolist()))
        selected_display = st.selectbox(
            "Person",
            options=person_options,
            key="add_person",
            label_visibility="collapsed",
        )
        selected_full_name = name_map[selected_display]
    elif not title_staff.empty:
        # Only one person — auto-select, no dropdown needed
        selected_full_name = title_staff.iloc[0]["name"]
        st.write("")  # keep layout aligned
    else:
        st.selectbox(
            "Person",
            options=["—"],
            disabled=True,
            label_visibility="collapsed",
        )
        selected_full_name = None

with add_col4:
    st.write("")  # vertical alignment
    can_add = bool(selected_country and selected_full_name)
    already_added = any(
        e["country"] == selected_country and e["full_name"] == selected_full_name
        for e in st.session_state.roster
    )
    if already_added:
        st.caption("Already on roster")
    elif st.button("＋ Add to roster", disabled=not can_add, use_container_width=True):
        add_entry(selected_country, selected_title, selected_full_name)
        st.rerun()

# Preview resolved person before adding
if selected_country and selected_full_name:
    person = resolve_by_name(df, selected_country, selected_full_name)
    if person is not None:
        parts = [p for p in [person["rank"], person["phone"]] if p]
        st.caption(f"→ {person['display_name']}  ·  {person['position']}" +
                   (f"  ·  {' · '.join(parts)}" if parts else ""))

st.divider()

# ── Roster ────────────────────────────────────────────────────────────────────

rh1, rh2, rh3 = st.columns([5, 2, 2])
with rh1:
    st.subheader(f"Roster  ({len(st.session_state.roster)} attendees)")
with rh2:
    if st.session_state.roster:
        st.write("")
        if st.button("↕ Sort by protocol", use_container_width=True):
            st.session_state.roster = sort_by_protocol(st.session_state.roster, df)
            st.rerun()
with rh3:
    if st.session_state.roster:
        st.write("")
        label = "🪪 Card view" if st.session_state.view_mode == "table" else "☰ Table view"
        if st.button(label, use_container_width=True):
            st.session_state.view_mode = "cards" if st.session_state.view_mode == "table" else "table"
            st.rerun()

if not st.session_state.roster:
    st.info("Your roster is empty. Add attendees above.")

elif st.session_state.view_mode == "cards":
    # ── Card view ─────────────────────────────────────────────────────────────
    CARDS_PER_ROW = 4
    for row_start in range(0, len(st.session_state.roster), CARDS_PER_ROW):
        cols = st.columns(CARDS_PER_ROW)
        for col_idx, ei in enumerate(range(row_start, min(row_start + CARDS_PER_ROW, len(st.session_state.roster)))):
            entry = st.session_state.roster[ei]
            person = resolve_by_name(df, entry["country"], entry["full_name"])
            display = person["display_name"] if person is not None else entry["full_name"]
            rank = (person["rank"] if person is not None else "") or ""
            manual_photo = entry.get("photo")
            wp_photo = None if manual_photo else auto_photo(entry["full_name"], entry["country"])
            effective_photo = manual_photo or wp_photo
            with cols[col_idx]:
                with st.container(border=True):
                    if effective_photo:
                        st.image(effective_photo, use_container_width=True)
                        if wp_photo:
                            st.caption("📖 Wikipedia")
                    else:
                        st.markdown(
                            "<div style='height:120px;display:flex;align-items:center;"
                            "justify-content:center;font-size:3rem;background:#f0f2f6;"
                            "border-radius:4px'>🧑‍💼</div>",
                            unsafe_allow_html=True,
                        )
                    st.markdown(f"**{display}**")
                    st.caption(f"{entry['country']}")
                    st.caption(f"{entry['position']}" + (f" · {rank}" if rank else ""))
                    if st.button("📷 Photo", key=f"card_photo_{ei}", use_container_width=True):
                        photo_dialog(ei)

else:
    # ── Table view ────────────────────────────────────────────────────────────
    h0, h1, h2, h3, h4, h5, h6 = st.columns([1.2, 3, 3, 4, 3, 3, 2])
    h0.markdown("**Photo**")
    h1.markdown("**Country**")
    h2.markdown("**Position**")
    h3.markdown("**Name**")
    h4.markdown("**Rank**")
    h5.markdown("**Phone**")
    h6.markdown("")
    st.divider()

    rows_to_delete = []

    for i, entry in enumerate(st.session_state.roster):
        country = entry["country"]
        position = entry["position"]
        full_name = entry["full_name"]
        person = resolve_by_name(df, country, full_name)
        manual_photo = entry.get("photo")
        wp_photo = None if manual_photo else auto_photo(full_name, country)
        effective_photo = manual_photo or wp_photo

        country_titles = titles_for_country(df, country)
        if position not in country_titles:
            country_titles = [position] + country_titles

        title_staff = staff_for_title(df, country, position)
        person_options = title_staff["display_name"].tolist() if not title_staff.empty else [full_name]

        c0, c1, c2, c3, c4, c5, c6 = st.columns([1.2, 3, 3, 4, 3, 3, 2])

        with c0:
            if effective_photo:
                st.image(effective_photo, width=55)
                if st.button("📷", key=f"tphoto_{i}", help="Change / remove photo"):
                    photo_dialog(i)
            else:
                if st.button("📷", key=f"tphoto_{i}", help="Add photo"):
                    photo_dialog(i)

        with c1:
            st.write(country)

        with c2:
            st.selectbox(
                "pos",
                options=country_titles,
                index=country_titles.index(position) if position in country_titles else 0,
                key=f"rpos_{i}",
                on_change=on_roster_position_change,
                args=(i,),
                label_visibility="collapsed",
            )

        with c3:
            current_display = person["display_name"] if person is not None else full_name
            if len(person_options) > 1:
                idx = person_options.index(current_display) if current_display in person_options else 0
                st.selectbox(
                    "person",
                    options=person_options,
                    index=idx,
                    key=f"rperson_{i}",
                    on_change=on_roster_person_change,
                    args=(i,),
                    label_visibility="collapsed",
                )
            else:
                st.write(current_display)

        with c4:
            st.write(person["rank"] if person is not None else "—")

        with c5:
            st.write(person["phone"] if person is not None else "—")

        with c6:
            btn1, btn2, btn3 = st.columns(3)
            with btn1:
                if i > 0 and st.button("↑", key=f"up_{i}", help="Move up"):
                    move_entry(i, -1)
                    st.rerun()
            with btn2:
                if i < len(st.session_state.roster) - 1 and st.button("↓", key=f"dn_{i}", help="Move down"):
                    move_entry(i, 1)
                    st.rerun()
            with btn3:
                if st.button("✕", key=f"del_{i}", help="Remove from roster"):
                    rows_to_delete.append(i)

    # Process deletes after rendering (reverse order to preserve indices)
    for i in sorted(rows_to_delete, reverse=True):
        remove_entry(i)
    if rows_to_delete:
        st.rerun()

    st.divider()

    # ── Export ────────────────────────────────────────────────────────────────

    export_rows = []
    for entry in st.session_state.roster:
        person = resolve_by_name(df, entry["country"], entry["full_name"])
        export_rows.append({
            "Country":            entry["country"],
            "Position":           entry["position"],
            "Honorific":          person["honorific"] if person is not None else "",
            "Name":               person["name"] if person is not None else entry["full_name"],
            "Rank":               person["rank"] if person is not None else "",
            "Phone":              person["phone"] if person is not None else "",
            "Email":              person["email"] if person is not None else "",
            "Mission":            person["mission"] if person is not None else "",
            "Address":            person["address"] if person is not None else "",
            "Accreditation Date": person["accreditation"] if person is not None else "",
        })

    export_df = pd.DataFrame(export_rows)

    col_dl, col_clear = st.columns([3, 1])
    with col_dl:
        st.download_button(
            label="⬇ Download roster (CSV)",
            data=export_df.to_csv(index=False),
            file_name="attendees.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col_clear:
        if st.button("Clear roster", use_container_width=True):
            st.session_state.roster = []
            st.rerun()

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### Save / Load roster")
    st.caption("Save your roster as a file and reload it later — useful when building lists weeks in advance.")

    # Save
    if st.session_state.roster:
        import json
        roster_json = json.dumps(st.session_state.roster, indent=2)
        st.download_button(
            label="⬇ Save roster (.json)",
            data=roster_json,
            file_name="roster.json",
            mime="application/json",
            use_container_width=True,
        )
    else:
        st.button("⬇ Save roster (.json)", disabled=True, use_container_width=True)

    # Load
    uploaded = st.file_uploader("Load a saved roster", type="json", label_visibility="collapsed")
    if uploaded is not None:
        import json
        try:
            loaded = json.load(uploaded)
            if isinstance(loaded, list) and all(
                isinstance(e, dict) and {"country", "position", "full_name"} <= e.keys()
                for e in loaded
            ):
                # Backfill photo field for entries saved before photo support
                for e in loaded:
                    e.setdefault("photo", None)
                st.session_state.roster = loaded
                st.success(f"Loaded {len(loaded)} attendees.")
                st.rerun()
            else:
                st.error("File format not recognised.")
        except Exception:
            st.error("Could not read file.")

    st.divider()
    st.markdown("### About")
    st.markdown(
        "Build your event attendee list by adding diplomatic representatives "
        "one at a time. Select the country, then pick the specific person attending. "
        "Download as CSV when done."
    )
    st.divider()
    if st.button("↺ Refresh Blue Book data"):
        st.cache_data.clear()
        st.rerun()
    st.caption("Data auto-refreshes every hour from the UN e-Blue Book.")
