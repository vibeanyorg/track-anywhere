from __future__ import annotations

from .service_category_commands import CategoryCommandUseCases
from .service_category_lines import CategoryLineUseCases
from .service_category_queries import CategoryQueryUseCases
from .service_category_reporting import CategoryReportingUseCases


class CategoryUseCases(
    CategoryQueryUseCases,
    CategoryCommandUseCases,
    CategoryReportingUseCases,
    CategoryLineUseCases,
):
    pass
