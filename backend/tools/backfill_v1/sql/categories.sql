SELECT
    category_id,
    book_id,
    kind,
    parent_id,
    name,
    normalized_name,
    level,
    path_cache,
    icon,
    color,
    sort_order,
    status,
    version
FROM public.categories
ORDER BY category_id COLLATE "C"
