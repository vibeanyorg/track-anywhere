SELECT
    transaction_id,
    book_id,
    memo,
    occurred_at,
    purpose,
    reversed_by,
    reverses_transaction_id,
    version
FROM public.transactions
ORDER BY transaction_id COLLATE "C"
