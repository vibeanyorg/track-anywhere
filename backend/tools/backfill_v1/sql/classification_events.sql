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
ORDER BY classification_event_id COLLATE "C"
