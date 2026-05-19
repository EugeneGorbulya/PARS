"""
Generates the database-schema diagram (graphics/database_schema.png) for the
LaTeX report using Graphviz. Layout is computed automatically by `dot`, so
edges are properly routed without overlapping table boxes.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from graphviz import Digraph


# Group → list of (table, [columns], fill_colour)
GROUPS: dict[str, tuple[str, str, list[tuple[str, list[str], str]]]] = {
    "users":   ("Users \\& profiles", "#e7f1ff", [
        ("users",        ["id PK", "telegram_id", "username"],                                                                          "#cfe2ff"),
        ("profiles",     ["id PK", "user_id FK", "alias", "cian_filter (JSON)", "w_b, w_pq, w_d", "is_public, public_slug",
                          "forked_from FK", "last_trained_snapshot FK"],                                                                "#9ec5fe"),
        ("pois",         ["id PK", "name", "lat, lng"],                                                                                 "#e2e3e5"),
        ("profile_pois", ["profile_id FK", "poi_id FK", "max_travel_min"],                                                              "#e2e3e5"),
    ]),
    "flats":   ("Flats \\& photos", "#fff4e6", [
        ("flats",                 ["id PK", "cian_id", "city, address", "price_rub", "rooms, area, floor", "lat, lng", "published_at"], "#ffd8a8"),
        ("flat_photos",           ["id PK", "flat_id FK", "url, s3_path"],                                                              "#ffd8a8"),
        ("photo_clip_embeddings", ["id PK", "photo_id FK", "model_tag", "embedding[512]"],                                              "#e7d4ff"),
        ("flat_poi_travel",       ["flat_id FK", "poi_id FK", "travel_min"],                                                            "#e2e3e5"),
    ]),
    "ratings": ("User feedback", "#e8f5e9", [
        ("ratings",                     ["id PK", "user_id FK", "profile_id FK", "flat_id FK", "beauty (1-5)",
                                         "price_quality (1-5)", "distance_pref (1-5)", "skipped", "created_at"],                        "#c3e6cb"),
        ("pairwise_ratings",            ["id PK", "user_id FK", "profile_id FK", "flat_a FK, flat_b FK",
                                         "factor", "preferred FK", "created_at"],                                                       "#c3e6cb"),
        ("seen / saved / hidden_flats", ["user_id FK", "profile_id FK", "flat_id FK"],                                                  "#e2e3e5"),
    ]),
    "ml":      ("ML artefacts", "#f3eaff", [
        ("profile_flat_scores", ["profile_id FK", "flat_id FK", "beauty_hat", "pq_hat", "distance_hat", "score"],                       "#e7d4ff"),
        ("model_snapshots",     ["id PK", "profile_id FK", "stage (A/B)", "n_train, n_val", "metrics (json)", "s3_key"],                "#e7d4ff"),
        ("profile_metrics",     ["id PK", "profile_id FK", "kendall_tau, mae", "created_at"],                                           "#e7d4ff"),
    ]),
}


def html_label(table: str, cols: list[str], fill: str) -> str:
    rows = "".join(f'<TR><TD ALIGN="LEFT" BALIGN="LEFT"><FONT POINT-SIZE="10">{c}</FONT></TD></TR>' for c in cols)
    return (
        f'<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" BGCOLOR="white" STYLE="rounded">'
        f'<TR><TD BGCOLOR="{fill}"><B>{table}</B></TD></TR>'
        f'{rows}'
        f'</TABLE>>'
    )


EDGES = [
    ("profiles",                    "users"),
    ("profile_pois",                "profiles"),
    ("profile_pois",                "pois"),
    ("flat_photos",                 "flats"),
    ("photo_clip_embeddings",       "flat_photos"),
    ("flat_poi_travel",             "flats"),
    ("flat_poi_travel",             "pois"),
    ("ratings",                     "users"),
    ("ratings",                     "profiles"),
    ("ratings",                     "flats"),
    ("pairwise_ratings",            "users"),
    ("pairwise_ratings",            "profiles"),
    ("pairwise_ratings",            "flats"),
    ("seen / saved / hidden_flats", "users"),
    ("seen / saved / hidden_flats", "profiles"),
    ("seen / saved / hidden_flats", "flats"),
    ("profile_flat_scores",         "profiles"),
    ("profile_flat_scores",         "flats"),
    ("model_snapshots",             "profiles"),
    ("profile_metrics",             "profiles"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".cursor/course_work_this_year/graphics")
    args = ap.parse_args()

    g = Digraph("PARS_DB", format="png",
                node_attr={"shape": "plain", "fontname": "Helvetica"},
                edge_attr={"fontname": "Helvetica", "fontsize": "8",
                           "color": "#666666", "arrowsize": "0.6", "penwidth": "0.9"},
                graph_attr={"rankdir": "TB", "splines": "spline", "nodesep": "0.4",
                            "ranksep": "0.7",  "overlap": "false", "pad": "0.2",
                            "fontname": "Helvetica", "compound": "true",
                            "newrank": "true"})

    for gid, (title, bg, tables) in GROUPS.items():
        with g.subgraph(name=f"cluster_{gid}") as c:
            c.attr(label=title, style="rounded,filled", color="#bbbbbb",
                   bgcolor=bg, fontsize="11", fontname="Helvetica-Bold",
                   margin="8")
            for table, cols, fill in tables:
                c.node(table, label=html_label(table, cols, fill))

    for src, dst in EDGES:
        g.edge(src, dst, arrowhead="vee")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = g.render(filename="database_schema", directory=out_dir, cleanup=True)
    print(f"saved {path}")


if __name__ == "__main__":
    main()
