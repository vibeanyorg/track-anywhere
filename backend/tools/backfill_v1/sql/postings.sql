SELECT
    id,
    transaction_id,
    book_id,
    position,
    account_id,
    side,
    amount_semantics,
    amount,
    currency
FROM public.postings
ORDER BY transaction_id COLLATE "C", position, id
