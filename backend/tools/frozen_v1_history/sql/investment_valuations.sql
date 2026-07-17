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
WHERE book_id = :source_book_id
ORDER BY valuation_id COLLATE "C"
