from pathlib import Path
from datetime import datetime
import pendulum
import requests
import pandas as pd
from dateutil import parser as dateparser

from airflow.decorators import dag, task
from airflow.providers.postgres.hooks.postgres import PostgresHook

DATA_DIR = Path("/opt/airflow/data/anime")
RAW_DIR = DATA_DIR / "raw"
PROC_DIR = DATA_DIR / "processed"

DEFAULT_ARGS = {"owner": "takeshy", "retries": 1}

@dag(
    dag_id="anime_top_etl_jikan_daily",
    schedule="@daily",
    start_date=pendulum.datetime(2025, 10, 1, tz="UTC"),
    catchup=False,
    default_args=DEFAULT_ARGS,
    tags=["anime", "jikan", "etl", "celery"],
    max_active_runs=1,
)
def anime_top_etl_jikan_daily():

    @task
    def extract_top_anime(pages: int = 2) -> str:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        rows = []
        for page in range(1, pages + 1):
            url = f"https://api.jikan.moe/v4/top/anime?page={page}"
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            for item in r.json().get("data", []):
                aired = item.get("aired") or {}
                a_from, a_to = aired.get("from"), aired.get("to")
                try:
                    airing_start = dateparser.parse(a_from) if a_from else None
                except Exception:
                    airing_start = None
                try:
                    airing_end = dateparser.parse(a_to) if a_to else None
                except Exception:
                    airing_end = None

                rows.append({
                    "mal_id": item.get("mal_id"),
                    "title": item.get("title") or "Unknown",
                    "episodes": item.get("episodes"),
                    "score": item.get("score"),
                    "popularity": item.get("popularity"),
                    "rank": item.get("rank"),
                    "type": item.get("type"),
                    "year": item.get("year"),
                    "airing_start": airing_start.isoformat() if airing_start else None,
                    "airing_end": airing_end.isoformat() if airing_end else None,
                    "fetched_at": datetime.utcnow().isoformat(),
                })

        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        raw_path = RAW_DIR / f"jikan_top_anime_raw_{ts}.csv"
        pd.DataFrame(rows).to_csv(raw_path, index=False)
        return str(raw_path)

    @task
    def transform_top_anime(raw_csv_path: str) -> str:
        PROC_DIR.mkdir(parents=True, exist_ok=True)
        df = pd.read_csv(raw_csv_path)
        cols = ["mal_id","title","episodes","score","popularity","rank","type","year","airing_start","airing_end","fetched_at"]
        df = df[cols].copy()
        for c in ["airing_start","airing_end","fetched_at"]:
            df[c] = pd.to_datetime(df[c], errors="coerce")
        df["episodes"] = df["episodes"].fillna(0).astype("Int64")
        df["year"] = df["year"].astype("Int64")
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        proc_path = PROC_DIR / f"jikan_top_anime_{ts}.csv"
        df.to_csv(proc_path, index=False)
        return str(proc_path)

    @task
    def load_to_postgres(proc_csv_path: str, pg_conn_id: str = "postgres_anime") -> int:
        hook = PostgresHook(postgres_conn_id=pg_conn_id)
        sql = Path("/opt/airflow/include/sql/upsert_anime.sql").read_text(encoding="utf-8")
        hook.run(sql)

        df = pd.read_csv(proc_csv_path, parse_dates=["airing_start","airing_end","fetched_at"])
        if df.empty:
            return 0

        rows = []
        for _, r in df.iterrows():
            rows.append((
                int(r["mal_id"]) if pd.notna(r["mal_id"]) else None,
                r["title"],
                int(r["episodes"]) if pd.notna(r["episodes"]) else None,
                float(r["score"]) if pd.notna(r["score"]) else None,
                int(r["popularity"]) if pd.notna(r["popularity"]) else None,
                int(r["rank"]) if pd.notna(r["rank"]) else None,
                r["type"] if pd.notna(r["type"]) else None,
                int(r["year"]) if pd.notna(r["year"]) else None,
                r["airing_start"].to_pydatetime() if pd.notna(r["airing_start"]) else None,
                r["airing_end"].to_pydatetime() if pd.notna(r["airing_end"]) else None,
                r["fetched_at"].to_pydatetime() if pd.notna(r["fetched_at"]) else datetime.utcnow(),
            ))

        from psycopg2.extras import execute_values
        insert_sql = """
        INSERT INTO public.anime_top
            (mal_id, title, episodes, score, popularity, "rank", "type", "year", airing_start, airing_end, fetched_at)
        VALUES %s
        ON CONFLICT (mal_id) DO UPDATE SET
            title=EXCLUDED.title,
            episodes=EXCLUDED.episodes,
            score=EXCLUDED.score,
            popularity=EXCLUDED.popularity,
            "rank"=EXCLUDED."rank",
            "type"=EXCLUDED."type",
            "year"=EXCLUDED."year",
            airing_start=EXCLUDED.airing_start,
            airing_end=EXCLUDED.airing_end,
            fetched_at=EXCLUDED.fetched_at;
        """
        conn = hook.get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            execute_values(cur, insert_sql, rows, page_size=500)
        return len(rows)

    raw = extract_top_anime(pages=2)
    proc = transform_top_anime(raw)
    load_to_postgres(proc)

dag = anime_top_etl_jikan_daily()
