SELECT
    book_id,
    name,
    kind,
    base_currency,
    timezone,
    status,
    template_key,
    settings,
    created_by,
    version
FROM public.ledger_books
ORDER BY book_id COLLATE "C"
