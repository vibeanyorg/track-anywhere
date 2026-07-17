SELECT
    account_id,
    book_id,
    name,
    type,
    currency,
    institution_type,
    subtype,
    institution,
    version
FROM public.accounts
WHERE book_id = :source_book_id
ORDER BY account_id COLLATE "C"
