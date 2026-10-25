-- Position reconciliation: IBOR (trade-date book of record) vs custodian (settled).
-- SQLite has no portable FULL OUTER JOIN in older versions, so the full join is
-- built as LEFT JOIN UNION ALL anti-joined LEFT JOIN.
-- The CASE order mirrors recon/tolerance.py::line_status (tested for equality).

WITH joined AS (
    SELECT i.as_of_date, i.portfolio_id, i.security_id,
           i.quantity AS ibor_qty, c.quantity AS cust_qty,
           i.price AS ibor_price, c.price AS cust_price,
           i.market_value_local AS ibor_mv_local, c.market_value_local AS cust_mv_local,
           i.fx_rate AS ibor_fx, c.fx_rate AS cust_fx,
           i.market_value_cad AS ibor_mv_cad, c.market_value_cad AS cust_mv_cad
    FROM ibor_positions i
    LEFT JOIN custodian_positions c
           ON c.as_of_date = i.as_of_date
          AND c.portfolio_id = i.portfolio_id
          AND c.security_id = i.security_id
    UNION ALL
    SELECT c.as_of_date, c.portfolio_id, c.security_id,
           NULL, c.quantity, NULL, c.price, NULL, c.market_value_local,
           NULL, c.fx_rate, NULL, c.market_value_cad
    FROM custodian_positions c
    LEFT JOIN ibor_positions i
           ON i.as_of_date = c.as_of_date
          AND i.portfolio_id = c.portfolio_id
          AND i.security_id = c.security_id
    WHERE i.security_id IS NULL
)
SELECT j.*,
       COALESCE(ibor_qty, 0) - COALESCE(cust_qty, 0)       AS qty_diff,
       COALESCE(ibor_mv_cad, 0) - COALESCE(cust_mv_cad, 0) AS mv_cad_diff,
       CASE
           WHEN cust_qty IS NULL THEN 'MISSING_AT_CUSTODIAN'
           WHEN ibor_qty IS NULL THEN 'MISSING_IN_IBOR'
           WHEN ABS(ibor_qty - cust_qty) > :qty_tol THEN 'QTY_BREAK'
           WHEN cust_price <> 0
                AND ABS(ibor_price - cust_price) / ABS(cust_price) > :price_rel_tol THEN 'PRICE_BREAK'
           WHEN ABS(ibor_mv_cad - cust_mv_cad) >
                MAX(:mv_abs_tol, :mv_rel_tol * ABS(cust_mv_cad)) THEN 'MV_BREAK'
           ELSE 'MATCHED'
       END AS status
FROM joined j
ORDER BY as_of_date, portfolio_id, security_id;
