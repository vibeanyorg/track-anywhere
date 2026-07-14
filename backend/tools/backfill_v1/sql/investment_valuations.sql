SELECT
    valuation_id,
    book_id,
    account_id,
    value,
    currency,
    observed_at,
    source,
    memo,
    version
FROM public.investment_valuations
ORDER BY valuation_id COLLATE "C"
