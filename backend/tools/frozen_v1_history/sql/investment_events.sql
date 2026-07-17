SELECT
    event_id,
    book_id,
    account_id,
    event_type,
    amount,
    currency,
    occurred_at,
    memo,
    units,
    nav,
    transaction_id,
    version
FROM public.investment_events
WHERE book_id = :source_book_id
ORDER BY event_id COLLATE "C"
