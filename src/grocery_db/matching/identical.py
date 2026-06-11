"""Automated identical-product matching across chains.

Conservative by design: two products match only when their normalised
brand+name string AND pack size agree exactly. This mainly pairs branded
items between Coles and Woolies — Aldi's own-brand catalogue needs
equivalence matching (curated/LLM, method='llm'|'manual'), not this.

Rebuildable: wipes previous method='auto' identical groups and recreates
them. Never touches manual/llm matches or price data.
"""

import re
import sqlite3

_NOISE_WORDS = {"the", "of", "with", "and"}


def normalise_name(brand: str | None, name: str | None) -> str | None:
    if not name:
        return None
    text = f"{brand or ''} {name}".lower()
    # Pack sizes live in the quantity/unit columns; strip them from the name
    # so "Milk 2L" and "Milk 2l" and "Milk" can agree.
    text = re.sub(r"\b[0-9]+(\.[0-9]+)?\s*(g|kg|ml|l|litre|pack|pk|each|ea|x)\b", " ", text)
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    words = [w for w in text.split() if w not in _NOISE_WORDS]
    # Dedupe words: brand often repeats in the name ("Coca-Cola Coca Cola...")
    seen, out = set(), []
    for word in words:
        if word not in seen:
            seen.add(word)
            out.append(word)
    return " ".join(out) or None


def rebuild_auto_matches(conn: sqlite3.Connection) -> dict:
    conn.execute(
        """DELETE FROM match_members WHERE method = 'auto' AND group_id IN
           (SELECT id FROM match_groups WHERE match_type = 'identical')"""
    )
    conn.execute(
        """DELETE FROM match_groups WHERE match_type = 'identical'
           AND id NOT IN (SELECT group_id FROM match_members)"""
    )

    buckets: dict[tuple, list[tuple[int, str]]] = {}
    for row in conn.execute(
        "SELECT id, chain, brand, name, quantity, unit FROM products WHERE quantity IS NOT NULL"
    ):
        norm = normalise_name(row["brand"], row["name"])
        if not norm:
            continue
        key = (norm, round(row["quantity"], 1), row["unit"])
        buckets.setdefault(key, []).append((row["id"], row["chain"]))

    stats = {"groups": 0, "members": 0}
    for (norm, _qty, _unit), members in buckets.items():
        chains = {chain for _, chain in members}
        if len(chains) < 2:
            continue
        # Ambiguous buckets (two different SKUs from one chain) are skipped
        # rather than guessed at.
        if len(members) != len(chains):
            continue
        cur = conn.execute(
            "INSERT INTO match_groups (name, match_type) VALUES (?, 'identical')",
            (norm,),
        )
        for product_id, _ in members:
            conn.execute(
                """INSERT INTO match_members (group_id, product_id, method, confidence)
                   VALUES (?, ?, 'auto', 0.9)""",
                (cur.lastrowid, product_id),
            )
            stats["members"] += 1
        stats["groups"] += 1
    conn.commit()
    return stats
