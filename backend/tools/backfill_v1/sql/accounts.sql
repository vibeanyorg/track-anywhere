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
ORDER BY account_id COLLATE "C"
