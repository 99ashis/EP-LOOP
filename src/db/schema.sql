-- Run this once in the Supabase SQL editor (Project -> SQL Editor -> New query)
-- before setting EP_STATE_BACKEND=supabase.

-- One row per symbol with a currently active anchor generation (NEW_EP or
-- the most recent RETRO_NEW_EP in its chain). Absence == no active episode.
create table if not exists ep_anchors_current (
    symbol                          text primary key,
    anchor_type                     text not null,   -- 'NEW_EP' or 'RETRO_NEW_EP'
    generation                      integer not null, -- 1 = original NEW_EP, 2+ = each RETRO_NEW_EP promotion
    origin_anchor_date              date not null,    -- the very first NEW_EP date in this chain, preserved for lineage
    anchor_date                     date not null,
    anchor_close                    numeric not null,
    anchor_prev_close               numeric not null,
    persistent_count                integer not null default 0,  -- resets to 0 each new generation
    sustained_count                 integer not null default 0,  -- resets to 0 each new generation
    promotion_candidate_date        date,             -- first Persistent of THIS generation; null until one fires
    promotion_candidate_close       numeric,
    promotion_candidate_prev_close  numeric,
    last_label                      text,             -- for research-trigger "did this change" comparisons only
    last_label_date                 date
);

-- Permanent, append-only log: one row per (symbol, day) that actually got
-- LABELED (gap-zone days are not logged here, same as the daily CSV).
create table if not exists ep_output_history (
    id                      bigserial primary key,
    as_of_date              date not null,
    symbol                  text not null,
    label                   text not null,   -- NEW_EP / PERSISTENT_EP / RETRO_NEW_EP / SUSTAINED_EP / FIZZLE_OUT_EP
    label_changed           boolean,
    generation               integer,
    anchor_date             date,
    anchor_close            numeric,
    anchor_prev_close       numeric,
    sessions_since_anchor   integer,
    close                   numeric,
    prev_close              numeric,
    volume                  numeric,
    avg_volume_50           numeric,
    volume_multiple         numeric,
    pct_move_vs_prev        numeric,
    persistent_count        integer,
    sustained_count         integer,
    unique (as_of_date, symbol)
);
create index if not exists idx_ep_output_history_symbol on ep_output_history (symbol);
create index if not exists idx_ep_output_history_as_of on ep_output_history (as_of_date);
create index if not exists idx_ep_output_history_label on ep_output_history (label);

-- One row per (symbol, day) that got flagged for research (see research/trigger.py).
create table if not exists research_results (
    id                              bigserial primary key,
    as_of                           date not null,
    symbol                          text not null,
    trigger_reason_for_research     text,
    ep_label                        text,
    fundamental_material_change     boolean,
    fundamental_confidence          text,
    fundamental_summary             text,
    news_catalyst_identified        boolean,
    news_headline                   text,
    news_summary                    text,
    raw_json                        jsonb,
    unique (as_of, symbol)
);
create index if not exists idx_research_results_symbol on research_results (symbol);
create index if not exists idx_research_results_as_of on research_results (as_of);
