SELECT
    category_version_id,
    category_id,
    book_id,
    name,
    parent_id,
    path,
    icon,
    color,
    valid_from,
    valid_to,
    change_reason,
    version
FROM public.category_versions
WHERE book_id = :source_book_id
ORDER BY category_version_id COLLATE "C"
