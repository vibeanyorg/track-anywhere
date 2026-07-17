SELECT
    counterparty_id,
    book_id,
    slug,
    name,
    kind,
    status,
    version
FROM public.counterparties
WHERE book_id = :source_book_id
ORDER BY counterparty_id COLLATE "C"
