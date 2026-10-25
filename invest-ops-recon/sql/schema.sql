-- Relational model for the reconciliation pipeline (SQLite).
-- Reference data, the two position sources, the trade blotter, and pipeline outputs.

DROP TABLE IF EXISTS securities;
DROP TABLE IF EXISTS accounts;
DROP TABLE IF EXISTS vendor_prices;
DROP TABLE IF EXISTS fx_rates;
DROP TABLE IF EXISTS ibor_trades;
DROP TABLE IF EXISTS ibor_positions;
DROP TABLE IF EXISTS custodian_positions;
DROP TABLE IF EXISTS dq_issues;
DROP TABLE IF EXISTS recon_results;
DROP TABLE IF EXISTS breaks;

CREATE TABLE securities (
    security_id   TEXT PRIMARY KEY,
    isin          TEXT NOT NULL UNIQUE,
    ticker        TEXT NOT NULL,
    name          TEXT NOT NULL,
    asset_class   TEXT NOT NULL CHECK (asset_class IN ('Equity', 'Bond', 'ETF')),
    currency      TEXT NOT NULL,
    multiplier    REAL NOT NULL      -- 0.01 for bonds priced per 100 of face value
);

CREATE TABLE accounts (
    portfolio_id      TEXT PRIMARY KEY,
    portfolio_name    TEXT NOT NULL,
    custodian_account TEXT NOT NULL UNIQUE
);

CREATE TABLE vendor_prices (
    price_date  TEXT NOT NULL,
    security_id TEXT NOT NULL REFERENCES securities(security_id),
    price       REAL NOT NULL,
    PRIMARY KEY (price_date, security_id)
);

CREATE TABLE fx_rates (
    rate_date    TEXT NOT NULL,
    currency     TEXT NOT NULL,
    cad_per_unit REAL NOT NULL,
    PRIMARY KEY (rate_date, currency)
);

CREATE TABLE ibor_trades (
    trade_id     TEXT PRIMARY KEY,
    portfolio_id TEXT NOT NULL REFERENCES accounts(portfolio_id),
    security_id  TEXT NOT NULL REFERENCES securities(security_id),
    side         TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity     REAL NOT NULL CHECK (quantity > 0),
    price        REAL NOT NULL,
    trade_date   TEXT NOT NULL,
    settle_date  TEXT NOT NULL
);
CREATE INDEX ix_trades_pos ON ibor_trades (portfolio_id, security_id, trade_date);

CREATE TABLE ibor_positions (
    as_of_date         TEXT NOT NULL,
    portfolio_id       TEXT NOT NULL REFERENCES accounts(portfolio_id),
    security_id        TEXT NOT NULL REFERENCES securities(security_id),
    quantity           REAL NOT NULL,
    price              REAL NOT NULL,
    fx_rate            REAL NOT NULL,
    market_value_local REAL NOT NULL,
    market_value_cad   REAL NOT NULL,
    PRIMARY KEY (as_of_date, portfolio_id, security_id)
);

CREATE TABLE custodian_positions (
    as_of_date         TEXT NOT NULL,
    portfolio_id       TEXT NOT NULL REFERENCES accounts(portfolio_id),
    security_id        TEXT NOT NULL REFERENCES securities(security_id),
    quantity           REAL NOT NULL,
    price              REAL NOT NULL,
    currency           TEXT NOT NULL,
    fx_rate            REAL NOT NULL,
    market_value_local REAL NOT NULL,
    market_value_cad   REAL NOT NULL,
    source_file        TEXT NOT NULL,
    source_row         INTEGER NOT NULL,
    PRIMARY KEY (as_of_date, portfolio_id, security_id)
);

CREATE TABLE dq_issues (
    issue_id    INTEGER PRIMARY KEY,
    as_of_date  TEXT NOT NULL,
    source      TEXT NOT NULL,
    rule        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    record_key  TEXT NOT NULL,
    security_id TEXT,
    detail      TEXT NOT NULL
);

CREATE TABLE recon_results (
    as_of_date       TEXT NOT NULL,
    portfolio_id     TEXT NOT NULL,
    security_id      TEXT NOT NULL,
    ibor_qty         REAL,
    cust_qty         REAL,
    ibor_price       REAL,
    cust_price       REAL,
    ibor_mv_local    REAL,
    cust_mv_local    REAL,
    ibor_fx          REAL,
    cust_fx          REAL,
    ibor_mv_cad      REAL,
    cust_mv_cad      REAL,
    qty_diff         REAL,
    mv_cad_diff      REAL,
    status           TEXT NOT NULL,
    PRIMARY KEY (as_of_date, portfolio_id, security_id)
);

CREATE TABLE breaks (
    as_of_date      TEXT NOT NULL,
    portfolio_id    TEXT NOT NULL,
    security_id     TEXT NOT NULL,
    status          TEXT NOT NULL,
    category        TEXT NOT NULL,
    needs_review    INTEGER NOT NULL,
    severity        TEXT NOT NULL,
    impact_cad      REAL NOT NULL,
    age_days        INTEGER NOT NULL,
    evidence        TEXT NOT NULL,     -- JSON facts used by the explanation step
    explanation     TEXT NOT NULL,
    explanation_src TEXT NOT NULL,     -- TEMPLATE | LLM_VERIFIED | LLM_REJECTED
    suggested_action TEXT NOT NULL,
    PRIMARY KEY (as_of_date, portfolio_id, security_id)
);
