SELECT
    classification_event_id,
    book_id,
    event_type,
    source_category_id,
    target_category_id,
    affected_line_count,
    before,
    after,
    rollback,
    created_by,
    created_at,
    version
FROM public.classification_events
WHERE book_id = :source_book_id
ORDER BY classification_event_id COLLATE "C"
