-- =============================================================================
-- hubobench.db
-- Contains instance definitions, solver configurations, run metadata, solver results, and raw samples.
--
-- objective_json in the instances table stores the full canonical problem schema JSON as a blob. This is the sole on-disk copy of the polynomial —
-- no separate JSON files are required. The runner reads this column when encoding a problem for a solver. Analytical query columns (num_variables,
-- density, etc.) are stored typed alongside it for indexed querying.
--
-- =============================================================================


-- ---------------------------------------------------------------------------
-- instances
-- One row per unique HUBO problem instance.
-- Written by the instance generator on instance creation.
-- Primary key is the full 64-char content-derived SHA-256 hash.
--
-- Generator provenance (name, version, seed, notes) is not stored here.
-- It lives in the objective JSON blob and is sufficient for audit purposes.
-- problem_class is retained as a classifier feature.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS instances (

    problem_hash            TEXT    PRIMARY KEY,    -- full 64-char SHA-256

    -- Schema provenance
    problem_schema_version  TEXT    NOT NULL,       -- problem_schema.md version in effect
    created_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),

    -- Classifier feature columns
    -- Typed and indexed for analytical queries. All derived from objective JSON.
    num_variables           INTEGER NOT NULL,
    max_degree              INTEGER NOT NULL,
    density                 REAL    NOT NULL,       -- at max_degree
    dynamic_range_ratio     REAL    NOT NULL,       -- max|c| / min|c|
    coeff_dist              TEXT    NOT NULL,       -- empirical | synthetic | gaussian
    num_terms               INTEGER NOT NULL,       -- total non-zero terms
    problem_class           TEXT    NOT NULL,       -- synthetic_random | max_cut | graph_coloring | model_dependent
    constraint_ratio        REAL    NOT NULL,       -- k/N; 0.0 for unconstrained

    -- Full canonical problem schema JSON. Sole copy of the polynomial.
    -- Fetched by the runner at encode time: SELECT objective_json WHERE problem_hash = ?
    -- Never queried analytically. Stored as blob for memory-mapped OS caching.
    objective_json          BLOB    NOT NULL

);


-- ---------------------------------------------------------------------------
-- solver_configs
-- One row per unique solver configuration + environment. Surrogate INTEGER PK
-- for cheap joins. Identity is the natural key (solver_name, config_json,
-- environment_digest): the same solver at the same config on the same device
-- reuses one row and its results are never re-run; a different device or a
-- different config forks a new row and a fresh pending set.
-- solver_version is nullable — not currently populated by run wrappers but
-- retained for manual annotation or future use.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS solver_configs (

    solver_config_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    solver_name             TEXT    NOT NULL,   -- dirac3 | gurobi_miqp | SA_OpenJij | gurobi_nlfunc
    solver_version          TEXT,               -- nullable; e.g. "1.2.3" if known
    limits_dossier_version  TEXT    NOT NULL,   -- dossier governing feasibility thresholds
    config_json             TEXT    NOT NULL,   -- full parameter dict; normalised key order
    environment_digest      TEXT    NOT NULL,   -- container image digest, else host fingerprint

    UNIQUE (solver_name, config_json, environment_digest)

);


-- ---------------------------------------------------------------------------
-- runs
-- One row per benchmark batch.
-- solution_schema_version records which solution schema was in effect.
-- Mixing solution schema versions within one training corpus is not permitted.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (

    run_id                      TEXT    PRIMARY KEY,
    solution_schema_version     TEXT    NOT NULL,
    created_at                  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    notes                       TEXT

);


-- ---------------------------------------------------------------------------
-- solutions
-- One row per (problem_hash, solver_id).
-- run_id records the most recent run that wrote the row. Rows are updated in place
-- (never appended as new rows): only the transient failures (API_ERROR, TIMEOUT) are
-- re-run, and a retry overwrites the prior failed row. Completed outcomes
-- (OK, SUBOPTIMAL_GAP, HARD_REJECT -- the DONE statuses) are terminal; the runner
-- skips them and write_solution refuses to overwrite them unless force=True.
-- Every runner writes here directly after decode_response completes.
-- Rejected / failed runs (HARD_REJECT, TIMEOUT with no incumbent, API_ERROR) are
-- written with best_energy = NULL and best_vars_json = NULL.
-- Only rows where best_energy IS NOT NULL are eligible for benchmarking.
--
-- Derived fields intentionally excluded (compute via samples JOIN):
--     n_samples               → SUM(count)    WHERE solution_id = ?
--     n_unique_samples        → COUNT(*)       WHERE solution_id = ?
--     duplicate_concentration → MAX(count) * 1.0 / SUM(count) WHERE solution_id = ?
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS solutions (

    solution_id             INTEGER PRIMARY KEY AUTOINCREMENT,

    problem_hash            TEXT    NOT NULL REFERENCES instances (problem_hash),
    solver_config_id        INTEGER NOT NULL REFERENCES solver_configs (solver_config_id),
    run_id                  TEXT    NOT NULL REFERENCES runs (run_id),

    -- Outcome
    status                  TEXT    NOT NULL,   -- OK | SUBOPTIMAL_GAP | TIMEOUT | HARD_REJECT | API_ERROR
    best_energy             REAL,               -- NULL for failed runs
    best_vars_json          TEXT,               -- JSON array of 0/1 integers length N;
                                                -- for Gurobi: sole copy of the solution assignment;
                                                -- for Dirac-3 and SA: mirrors samples rank-0 vars;
                                                -- NULL for failed runs

    -- Timing
    wall_clock_s            REAL,               -- end-to-end from encode to decode; primary TTS metric
    algorithmic_time_s      REAL,               -- device_usage_s (Dirac-3) | Runtime (Gurobi) | wall_clock_s (SA)

    -- Flags
    flags                   TEXT DEFAULT NULL,
    UNIQUE (problem_hash, solver_config_id)

);

CREATE INDEX IF NOT EXISTS idx_solutions_problem_hash
    ON solutions (problem_hash);

CREATE INDEX IF NOT EXISTS idx_solutions_run_id
    ON solutions (run_id);

CREATE INDEX IF NOT EXISTS idx_solutions_solver_config_id
    ON solutions (solver_config_id);


-- ---------------------------------------------------------------------------
-- samples
-- One row per unique sample per stochastic solution.
-- Gurobi does not write here — its assignment lives in solutions.best_vars_json.
-- Angel's warm-start pipeline reads from this table for candidate selection
-- and per-variable frequency scoring. Rows are never deleted.
--
-- vars is stored as a raw byte blob: one byte per variable, value 0 or 1.
-- For N=99 this is 99 bytes versus ~400 bytes of JSON text.
-- Read in Python: numpy.frombuffer(row.vars, dtype=numpy.uint8)
-- Write in Python: bytes(assignment_list)
-- Domain validation (all bytes in {0, 1}) is enforced in Python before insertion.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS samples (

    sample_id               INTEGER PRIMARY KEY AUTOINCREMENT,

    solution_id             INTEGER NOT NULL REFERENCES solutions (solution_id),

    sample_rank             INTEGER NOT NULL,   -- 0 = best energy within this solution
    energy                  REAL    NOT NULL,   -- canonical f(x) + constant
    count                   INTEGER NOT NULL,   -- times this exact assignment appeared
    vars                    BLOB    NOT NULL    -- raw bytes: one byte per variable (0 or 1)

);

CREATE INDEX IF NOT EXISTS idx_samples_solution_id
    ON samples (solution_id);

CREATE INDEX IF NOT EXISTS idx_samples_solution_rank
    ON samples (solution_id, sample_rank);


-- ---------------------------------------------------------------------------
-- schema_migrations
-- Historical record of the one-time 0.3 -> 0.5 schema migration that produced the
-- current corpus. The migration runner that populated it has been removed;
-- docs/schema.sql is now the single source of truth and a fresh DB is born at the
-- current version, so this table stays empty on a fresh build. Retained here (and
-- on databases that were migrated in place) purely as a provenance record.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (

    step_id     TEXT    PRIMARY KEY,    -- e.g. m0001_v03_to_v04
    applied_at  TEXT    NOT NULL        -- ISO-8601 UTC timestamp when applied

);
