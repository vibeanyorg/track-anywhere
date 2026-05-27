from __future__ import annotations

import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def _assert_router_uses_service_boundary(filename: str, alias: str, protocol: str, port_module: str) -> None:
    source = (BACKEND / f"api_routers/{filename}").read_text()
    ports = (BACKEND / f"api_ports/{port_module}.py").read_text()

    assert "from ..api_runtime import service" not in source
    assert "from ..api_service_ports import" not in source
    assert f"from ..api_ports.{port_module} import {alias}" in source
    assert f"class {protocol}(AuditRecorder, Protocol)" in ports
    assert f"{alias} = Annotated[{protocol}, ServiceDependency]" in ports
    assert "recorder=service" in source


def test_storage_write_methods_do_not_accept_service_object():
    offenders: list[str] = []
    for path in BACKEND.rglob("storage*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not (node.name.startswith("save_") or node.name.startswith("_save_")):
                continue
            arg_names = [arg.arg for arg in node.args.args]
            if "service" in arg_names:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {node.name}")

    assert offenders == []


def test_storage_write_modules_do_not_read_service_dirty_state():
    write_files = [
        BACKEND / "storage_partial.py",
        BACKEND / "storage_annotation_writers.py",
        BACKEND / "storage_payment_instruments.py",
        BACKEND / "storage_payment_profiles.py",
        BACKEND / "storage_uow.py",
    ]
    offenders = []
    pattern = re.compile(r"\bservice\.")
    for path in write_files:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_api_routers_do_not_access_storage_directly():
    offenders = []
    pattern = re.compile(r"\bservice\.storage\b")
    for path in (BACKEND / "api_routers").glob("*.py"):
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_system_router_does_not_own_storage_or_migration_details():
    source = (BACKEND / "api_routers/system.py").read_text()

    assert "from ..api_runtime import" not in source
    assert "from ..api_auth_runtime import auth_cookie_secure" in source
    assert "from ..api_browser_sessions import browser_sessions" in source
    assert "service.storage" not in source
    assert "service.config" not in source
    assert "service.owner_token" not in source
    assert "sqlalchemy" not in source
    assert "alembic.config" not in source
    assert "ScriptDirectory" not in source
    assert "service.system_readiness()" in source
    assert "service.system_status(token" in source


def test_system_router_uses_service_dependency_boundary():
    source = (BACKEND / "api_routers/system.py").read_text()
    ports = (BACKEND / "api_ports/system.py").read_text()

    assert "from ..api_ports.system import SystemService" in source
    assert "class SystemRouteService(Protocol)" in ports
    assert "SystemService = Annotated[SystemRouteService, ServiceDependency]" in ports


def test_non_system_api_routers_do_not_import_runtime_service():
    offenders = []
    allowed_files = {BACKEND / "api_routers/system.py"}
    for path in (BACKEND / "api_routers").glob("*.py"):
        if path in allowed_files:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if (node.level == 2 and node.module == "api_runtime") or node.module == "track_anywhere.api_runtime":
                imported_names = {alias.name for alias in node.names}
                if "service" in imported_names:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert offenders == []


def test_api_route_service_ports_are_router_scoped():
    assert not (BACKEND / "api_service_ports.py").exists()


def test_high_churn_ledger_router_uses_service_dependency_boundary():
    _assert_router_uses_service_boundary("ledger.py", "LedgerService", "LedgerRouteService", "ledger")


def test_high_churn_catalog_router_uses_service_dependency_boundary():
    _assert_router_uses_service_boundary("catalog.py", "CatalogService", "CatalogRouteService", "catalog")


def test_catalog_adjacent_write_routers_use_service_dependency_boundaries():
    expectations = [
        ("counterparties.py", "CounterpartyService", "CounterpartyRouteService", "counterparties"),
        ("payment_instruments.py", "PaymentInstrumentService", "PaymentInstrumentRouteService", "payment_instruments"),
        ("payment_profiles.py", "PaymentProfileService", "PaymentProfileRouteService", "payment_profiles"),
    ]

    for filename, alias, protocol, port_module in expectations:
        _assert_router_uses_service_boundary(filename, alias, protocol, port_module)


def test_operational_write_routers_use_service_dependency_boundaries():
    expectations = [
        ("credentials.py", "CredentialService", "CredentialRouteService", "credentials"),
        ("recurring.py", "RecurringService", "RecurringRouteService", "recurring"),
    ]

    for filename, alias, protocol, port_module in expectations:
        _assert_router_uses_service_boundary(filename, alias, protocol, port_module)


def test_remaining_business_routers_use_service_dependency_boundaries():
    expectations = [
        ("finance.py", "FinanceService", "FinanceRouteService", "finance"),
        ("books.py", "BookService", "BookRouteService", "books"),
        ("backoffice.py", "BackofficeService", "BackofficeRouteService", "backoffice"),
    ]

    for filename, alias, protocol, port_module in expectations:
        _assert_router_uses_service_boundary(filename, alias, protocol, port_module)


def test_backoffice_router_does_not_reach_into_service_registries():
    source = (BACKEND / "api_routers/backoffice.py").read_text()
    offenders = []
    forbidden = [
        re.compile(r"\bservice\.(books|users|auth_identities|categories|recurring|ledger)\b"),
    ]
    for line_number, line in enumerate(source.splitlines(), start=1):
        if any(pattern.search(line) for pattern in forbidden):
            offenders.append(f"api_routers/backoffice.py:{line_number}: {line.strip()}")

    assert offenders == []


def test_api_error_auditing_has_no_runtime_singleton_fallback():
    errors_source = (BACKEND / "api_errors.py").read_text()
    assert "api_runtime import service" not in errors_source
    assert "recorder=service" not in errors_source
    assert "def raise_command_error(error: Exception, operation: str, *, recorder:" in errors_source

    offenders = []
    for path in (BACKEND / "api_routers").glob("*.py"):
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if "raise_command_error(" in line and "recorder=" not in line:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_platform_auth_does_not_accept_whole_service_object():
    path = BACKEND / "platform_auth.py"
    tree = ast.parse(path.read_text())
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        arg_names = [arg.arg for arg in node.args.args]
        if "service" in arg_names:
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {node.name}")

    assert offenders == []


def test_storage_loaders_return_snapshots_instead_of_mutating_service():
    offenders = []
    checked_files = [
        BACKEND / "storage.py",
        BACKEND / "storage_snapshot_loader.py",
        BACKEND / "storage_payment_instruments.py",
        BACKEND / "storage_payment_profiles.py",
    ]
    forbidden = [
        re.compile(r"\bdef load_into\b"),
        re.compile(r"\b_hydrate_"),
        re.compile(r"\bservice\."),
    ]
    for path in checked_files:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if any(pattern.search(line) for pattern in forbidden):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_storage_write_boundaries_use_explicit_change_sets():
    offenders = []
    checked_files = [
        BACKEND / "storage_changes.py",
        BACKEND / "storage.py",
        BACKEND / "storage_partial.py",
        BACKEND / "storage_uow.py",
        BACKEND / "domain_storage_writers.py",
    ]
    forbidden = [
        re.compile(r"\bcategory_book\b"),
        re.compile(r"\bbudget_book\b"),
        re.compile(r"\bbook_directory\b"),
        re.compile(r"\bif aliases is None\b"),
        re.compile(r"\bif versions is None\b"),
        re.compile(r"\bif events is None\b"),
    ]
    for path in checked_files:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if any(pattern.search(line) for pattern in forbidden):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_persistence_helpers_are_grouped_by_bounded_context():
    grouped_modules = {
        "storage_repositories": {"catalog.py", "categories.py", "finance.py", "ledger.py", "payments.py", "security.py", "workflow.py"},
        "service_persistence": {"catalog.py", "collectors.py", "directory.py", "finance.py", "ledger.py", "metadata.py", "startup.py", "workflow.py"},
    }
    for folder, modules in grouped_modules.items():
        assert not (BACKEND / f"{folder}.py").exists()
        for module in modules:
            assert (BACKEND / f"{folder}/{module}").exists()


def test_service_write_helpers_use_commit_vocabulary():
    offenders = []
    forbidden = re.compile(r"\b(def|self)\._persist_")
    for path in BACKEND.glob("service*.py"):
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if forbidden.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_known_god_class_names_do_not_return():
    offenders = []
    forbidden = [
        re.compile(r"\bclass ServiceBootstrapMixin\b"),
        re.compile(r"\bclass CatalogRepository\b"),
        re.compile(r"\buow\.catalog\b"),
    ]
    for path in BACKEND.rglob("*.py"):
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if any(pattern.search(line) for pattern in forbidden):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert offenders == []


def test_compatibility_facades_stay_empty():
    checked = {
        BACKEND / "service_persistence/__init__.py": {"ServicePersistenceMixin"},
        BACKEND / "storage_partial.py": {"PartialStorageWriters"},
    }
    offenders = []
    for path, class_names in checked.items():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in class_names:
                if len(node.body) != 1 or not isinstance(node.body[0], ast.Pass):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {node.name}")

    assert offenders == []
