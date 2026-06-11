"""Local dashboard over the grocery price database.

Run:  uv run --extra dashboard streamlit run dashboard/app.py
Pull a fresh DB first with:  uv run gdb pull
"""

import sqlite3
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

DB_PATH = Path("data/grocery.db")

CHAIN_COLOURS = {"coles": "#e01a22", "woolies": "#178841", "aldi": "#00a8e1"}

st.set_page_config(page_title="Grocery Prices AU", page_icon="🛒", layout="wide")


@st.cache_resource
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


@st.cache_data(ttl=300)
def query(sql: str, params=()) -> pd.DataFrame:
    return pd.read_sql_query(sql, get_conn(), params=params)


def dollars(cents) -> str:
    return f"${cents / 100:.2f}"


def price_history(product_id: int) -> pd.DataFrame:
    return query(
        """SELECT date, price_c / 100.0 AS price, was_price_c / 100.0 AS was_price, source
           FROM price_events WHERE product_id = ? ORDER BY date""",
        (product_id,),
    )


def attribute_history(product_id: int) -> pd.DataFrame:
    return query(
        "SELECT date, field, old_value, new_value FROM attribute_events WHERE product_id = ? ORDER BY date",
        (product_id,),
    )


def history_figure(traces: list[tuple[str, pd.DataFrame]], attr_events: pd.DataFrame | None = None):
    """Step chart of price histories; vertical markers for attribute changes."""
    fig = go.Figure()
    for label, hist in traces:
        chain = label.split(":")[0]
        fig.add_trace(
            go.Scatter(
                x=hist["date"],
                y=hist["price"],
                name=label,
                mode="lines+markers",
                line_shape="hv",
                line_color=CHAIN_COLOURS.get(chain),
            )
        )
    if attr_events is not None:
        for _, ev in attr_events.iterrows():
            fig.add_vline(x=ev["date"], line_dash="dot", line_color="orange")
            fig.add_annotation(
                x=ev["date"], y=1, yref="paper", showarrow=False, textangle=-90,
                text=f"{ev['field']}: {ev['old_value']}→{ev['new_value']}",
                font=dict(size=10, color="orange"),
            )
    fig.update_layout(yaxis_title="$", xaxis_title=None, height=450, hovermode="x unified")
    return fig


if not DB_PATH.exists():
    st.error("No database at data/grocery.db — run `uv run gdb pull` (or backfill/scrape) first.")
    st.stop()

tab_search, tab_compare, tab_health = st.tabs(["🔍 Search & history", "⚖️ Cross-chain compare", "📊 Data health"])

with tab_search:
    col1, col2 = st.columns([3, 1])
    term = col1.text_input("Search products", placeholder="e.g. full cream milk 2l")
    chains = col2.multiselect("Chains", ["coles", "woolies", "aldi"], default=["coles", "woolies", "aldi"])

    if term:
        like = f"%{'%'.join(term.split())}%"
        results = query(
            f"""SELECT p.id, p.chain, p.brand, p.name, p.quantity, p.unit, p.category,
                       p.last_seen, pe.price_c
                FROM products p
                JOIN price_events pe ON pe.id =
                    (SELECT id FROM price_events WHERE product_id = p.id ORDER BY date DESC LIMIT 1)
                WHERE (p.brand || ' ' || p.name) LIKE ? COLLATE NOCASE
                  AND p.chain IN ({','.join('?' * len(chains))})
                ORDER BY p.last_seen DESC, pe.price_c
                LIMIT 200""",
            (like, *chains),
        )
        if results.empty:
            st.info("No matches.")
        else:
            results["price"] = results["price_c"].map(dollars)
            results["size"] = results.apply(
                lambda r: f"{r['quantity']:g}{r['unit']}" if pd.notna(r["quantity"]) else "", axis=1
            )
            picked = st.dataframe(
                results[["chain", "brand", "name", "size", "price", "category", "last_seen"]],
                on_select="rerun",
                selection_mode="single-row",
                hide_index=True,
                use_container_width=True,
            )
            rows = picked.selection.rows if picked else []
            if rows:
                product = results.iloc[rows[0]]
                st.subheader(f"{product['brand'] or ''} {product['name']} — {product['chain']}")
                hist = price_history(int(product["id"]))
                attrs = attribute_history(int(product["id"]))
                st.plotly_chart(
                    history_figure([(f"{product['chain']}: {product['name']}", hist)], attrs),
                    use_container_width=True,
                )
                if not attrs.empty:
                    st.caption("Attribute changes (orange lines) — pack size changes are shrinkflation suspects.")
    else:
        st.caption("Type to search across all chains.")

with tab_compare:
    groups = query(
        """SELECT g.id, g.name, g.match_type, COUNT(m.product_id) AS members
           FROM match_groups g JOIN match_members m ON m.group_id = g.id
           GROUP BY g.id ORDER BY g.match_type, g.name"""
    )
    if groups.empty:
        st.info("No match groups yet — run `uv run gdb match` to build identical matches.")
    else:
        st.caption(f"{len(groups)} match groups ({(groups.match_type == 'identical').sum()} identical, "
                   f"{(groups.match_type == 'equivalent').sum()} equivalent)")
        group_label = st.selectbox(
            "Product group",
            groups.apply(lambda g: f"[{g['match_type']}] {g['name']} ({g['members']} chains)", axis=1),
        )
        group = groups.iloc[
            groups.apply(lambda g: f"[{g['match_type']}] {g['name']} ({g['members']} chains)", axis=1)
            .tolist().index(group_label)
        ]
        members = query(
            """SELECT p.id, p.chain, p.brand, p.name, p.quantity, p.unit
               FROM match_members m JOIN products p ON p.id = m.product_id
               WHERE m.group_id = ?""",
            (int(group["id"]),),
        )
        traces = [
            (f"{m['chain']}: {m['brand'] or ''} {m['name']}".strip(), price_history(int(m["id"])))
            for _, m in members.iterrows()
        ]
        st.plotly_chart(history_figure(traces), use_container_width=True)
        latest = query(
            f"""SELECT p.chain, p.brand || ' ' || p.name AS product,
                       p.quantity, p.unit, pe.price_c
                FROM match_members m
                JOIN products p ON p.id = m.product_id
                JOIN price_events pe ON pe.id =
                    (SELECT id FROM price_events WHERE product_id = p.id ORDER BY date DESC LIMIT 1)
                WHERE m.group_id = ?""",
            (int(group["id"]),),
        )
        latest["price"] = latest["price_c"].map(dollars)
        latest["$/unit"] = latest.apply(
            lambda r: f"${r['price_c'] / r['quantity'] / 100 * (100 if r['unit'] in ('g', 'ml') else 1):.2f}"
                      f" per {'100' + r['unit'] if r['unit'] in ('g', 'ml') else r['unit']}"
            if pd.notna(r["quantity"]) and r["quantity"] else "",
            axis=1,
        )
        st.dataframe(latest[["chain", "product", "price", "$/unit"]], hide_index=True, use_container_width=True)

with tab_health:
    runs = query(
        "SELECT date, chain, product_count, status FROM scrape_runs ORDER BY date DESC, chain LIMIT 30"
    )
    counts = query(
        """SELECT chain, COUNT(*) AS products, MAX(last_seen) AS latest_observation
           FROM products GROUP BY chain"""
    )
    events = query(
        """SELECT source, COUNT(*) AS events, MIN(date) AS oldest, MAX(date) AS newest
           FROM price_events GROUP BY source"""
    )
    col1, col2 = st.columns(2)
    col1.subheader("Catalogue coverage")
    col1.dataframe(counts, hide_index=True, use_container_width=True)
    col2.subheader("Price events")
    col2.dataframe(events, hide_index=True, use_container_width=True)
    st.subheader("Recent scrape runs")
    st.dataframe(runs, hide_index=True, use_container_width=True)
