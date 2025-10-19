CREATE TABLE IF NOT EXISTS public.anime_top (
    mal_id       INTEGER PRIMARY KEY,
    title        TEXT,
    episodes     INTEGER,
    score        NUMERIC,
    popularity   INTEGER,
    "rank"       INTEGER,
    "type"       TEXT,
    "year"       INTEGER,
    airing_start TIMESTAMP NULL,
    airing_end   TIMESTAMP NULL,
    fetched_at   TIMESTAMP NOT NULL
);
