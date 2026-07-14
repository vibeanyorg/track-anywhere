SELECT
    line_id,
    transaction_id,
    position,
    line_type,
    amount,
    currency,
    book_id,
    category_id,
    category_version_id,
    category_path_snapshot,
    counterparty_id,
    project_id,
    necessity,
    reimbursement_status,
    memo,
    version
FROM public.transaction_lines
ORDER BY transaction_id COLLATE "C", position, line_id COLLATE "C"
