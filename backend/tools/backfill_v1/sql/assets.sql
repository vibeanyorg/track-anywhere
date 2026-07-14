SELECT
    asset_code,
    kind,
    scale,
    display_scale,
    name,
    status,
    version
FROM public.assets
ORDER BY asset_code COLLATE "C"
