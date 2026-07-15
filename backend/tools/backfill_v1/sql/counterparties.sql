SELECT
    counterparty_id,
    book_id,
    slug,
    name,
    kind,
    status,
    version
FROM public.counterparties
ORDER BY counterparty_id COLLATE "C"
