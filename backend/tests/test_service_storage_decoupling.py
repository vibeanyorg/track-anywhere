from __future__ import annotations

import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


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
    allowed_files = {BACKEND / "api_routers/system.py"}
    pattern = re.compile(r"\bservice\.storage\b")
    for path in (BACKEND / "api_routers").glob("*.py"):
        if path in allowed_files:
            continue
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern.search(line):
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
        BACKEND / "service_persistence.py": {"ServicePersistenceMixin"},
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
