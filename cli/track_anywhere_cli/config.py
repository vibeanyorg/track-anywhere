from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal
from urllib.parse import SplitResult, urlsplit, urlunsplit
from uuid import uuid4

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX runtime
    msvcrt = None  # type: ignore[assignment]

from .output import CliDiagnostic


CredentialKind = Literal["oauth", "api_key"]


@dataclass(frozen=True)
class RequestCredential:
    kind: CredentialKind
    secret: str


@dataclass
class CliConfig:
    base_url: str
    token: str | None = None
    insecure_automation: bool = False
    api_key: str | None = None
    resource: str | None = None
    oauth_endpoint: str | None = None

    def __post_init__(self) -> None:
        self.base_url = canonical_base_url(self.base_url)
        if self.resource is not None:
            self.resource = canonical_resource(self.resource)
        if self.oauth_endpoint is not None:
            self.oauth_endpoint = validate_transport_url(self.oauth_endpoint)
        if self.token and self.api_key:
            raise ValueError("OAuth bearer token and API key are mutually exclusive")

    @property
    def credential(self) -> RequestCredential | None:
        if self.api_key:
            return RequestCredential(kind="api_key", secret=self.api_key)
        if self.token:
            return RequestCredential(kind="oauth", secret=self.token)
        return None


@dataclass(frozen=True)
class AuthProfile:
    base_url: str
    resource: str
    issuer: str
    client_id: str
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None
    scope: str | None = None
    token_endpoint: str | None = None
    revocation_endpoint: str | None = None
    auth_kind: str = "pkce"

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", canonical_base_url(self.base_url))
        object.__setattr__(self, "resource", canonical_resource(self.resource))
        object.__setattr__(self, "issuer", canonical_base_url(self.issuer))
        if self.token_endpoint is not None:
            object.__setattr__(
                self, "token_endpoint", canonical_endpoint(self.token_endpoint)
            )
        if self.revocation_endpoint is not None:
            object.__setattr__(
                self,
                "revocation_endpoint",
                canonical_endpoint(self.revocation_endpoint),
            )
        if not self.client_id or not self.access_token:
            raise ValueError("OAuth profile requires client_id and access_token")

    def to_dict(self) -> dict[str, object]:
        return {
            "base_url": self.base_url,
            "resource": self.resource,
            "issuer": self.issuer,
            "client_id": self.client_id,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scope": self.scope,
            "token_endpoint": self.token_endpoint,
            "revocation_endpoint": self.revocation_endpoint,
            "auth_kind": self.auth_kind,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> AuthProfile:
        return cls(
            base_url=_required_text(value, "base_url"),
            resource=_required_text(value, "resource"),
            issuer=_required_text(value, "issuer"),
            client_id=_required_text(value, "client_id"),
            access_token=_required_text(value, "access_token"),
            refresh_token=_optional_text(value.get("refresh_token")),
            expires_at=_optional_float(value.get("expires_at")),
            scope=_optional_text(value.get("scope")),
            token_endpoint=_optional_text(value.get("token_endpoint")),
            revocation_endpoint=_optional_text(value.get("revocation_endpoint")),
            auth_kind=_optional_text(value.get("auth_kind")) or "pkce",
        )


@dataclass(frozen=True)
class StoredToken:
    token: str
    source: str


@dataclass(frozen=True)
class StoredProfile:
    profile: AuthProfile
    source: str


@dataclass(frozen=True)
class ProfileDeletion:
    deleted: bool
    diagnostics: list[CliDiagnostic]


class TokenStore:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        resource: str | None = None,
    ) -> None:
        self.explicit_token_file = "TRACK_ANYWHERE_TOKEN_FILE" in os.environ
        self.token_file = Path(
            os.getenv(
                "TRACK_ANYWHERE_TOKEN_FILE",
                str(Path.home() / ".config" / "track-anywhere" / "token"),
            )
        )
        self.profile_file = (
            self.token_file
            if self.explicit_token_file
            else Path.home() / ".config" / "track-anywhere" / "auth-profiles.json"
        )
        self.base_url = canonical_base_url(base_url) if base_url is not None else None
        if resource is not None:
            self.resource = canonical_resource(resource)
        elif self.base_url is not None:
            self.resource = canonical_resource(f"{self.base_url}/api/v2")
        else:
            self.resource = None

    def load(self) -> str | None:
        stored = self.load_with_source()
        return stored.token if stored is not None else None

    def load_with_source(self) -> StoredToken | None:
        if self.explicit_token_file:
            return self._load_file()
        keyring = _keyring_module()
        if keyring is not None:
            try:
                token = keyring.get_password("track-anywhere", "cli-token")
            except Exception:
                token = None
            if token:
                return StoredToken(token=token, source="keyring")
        return self._load_file()

    def save(self, token: str) -> list[CliDiagnostic]:
        if self.explicit_token_file:
            self._save_file(token)
            return []
        keyring = _keyring_module()
        if keyring is not None:
            try:
                keyring.set_password("track-anywhere", "cli-token", token)
                return []
            except Exception:
                pass
        self._save_file(token)
        return [_token_file_warning(self.token_file)]

    def load_profile_with_source(self) -> StoredProfile | None:
        base_url, resource = self._profile_identity()
        profile_key = _profile_key(base_url, resource)
        self._assert_profile_not_blocked()

        if not self.explicit_token_file:
            keyring = _keyring_module()
            if keyring is not None:
                try:
                    encoded = keyring.get_password(
                        "track-anywhere", f"cli-profile:{profile_key}"
                    )
                except Exception as exc:
                    raise RuntimeError(
                        "could not read OAuth profile from keyring"
                    ) from exc
                if encoded:
                    profile = _decode_profile(encoded)
                    if profile is None:
                        raise RuntimeError("invalid OAuth profile stored in keyring")
                    self._validate_profile_identity(profile, base_url, resource)
                    return StoredProfile(profile=profile, source="keyring")

        document, legacy_raw = self._read_profile_document()
        encoded_profile = document["profiles"].get(profile_key)
        if isinstance(encoded_profile, dict):
            try:
                profile = AuthProfile.from_dict(encoded_profile)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "invalid OAuth profile stored in profile file"
                ) from exc
            self._validate_profile_identity(profile, base_url, resource)
            return StoredProfile(
                profile=profile,
                source="token_file" if self.explicit_token_file else "profile_file",
            )
        if legacy_raw:
            if self.explicit_token_file:
                return StoredProfile(
                    profile=_legacy_profile(base_url, resource, legacy_raw),
                    source="legacy_token_file",
                )
            raise RuntimeError(
                f"invalid OAuth profile document: {self.profile_file}; "
                "remove it or run `ta auth login` after moving it aside"
            )

        return None

    def save_profile(self, profile: AuthProfile) -> list[CliDiagnostic]:
        with self.profile_lock():
            return self._save_profile_locked(profile)

    def _save_profile_locked(self, profile: AuthProfile) -> list[CliDiagnostic]:
        base_url, resource = self._profile_identity()
        if profile.base_url != base_url or profile.resource != resource:
            raise ValueError("OAuth profile does not match this base URL and resource")
        profile_key = _profile_key(base_url, resource)
        encoded = json.dumps(profile.to_dict(), separators=(",", ":"))
        self._begin_profile_transition_locked()

        if not self.explicit_token_file:
            keyring = _keyring_module()
            if keyring is not None:
                account = f"cli-profile:{profile_key}"
                try:
                    keyring.set_password("track-anywhere", account, encoded)
                except Exception as _write_error:
                    try:
                        if keyring.get_password("track-anywhere", account) is not None:
                            keyring.delete_password("track-anywhere", account)
                        if keyring.get_password("track-anywhere", account) is not None:
                            raise RuntimeError("stale keyring profile remains")
                    except Exception as cleanup_error:
                        self._mark_profile_blocked()
                        raise RuntimeError(
                            "OAuth profile persistence failed and stale keyring data "
                            "could not be removed; run `ta auth login` again"
                        ) from cleanup_error
                    return self._save_file_authority(profile_key, profile)
                try:
                    self._remove_profile_file(profile_key)
                    self._clear_profile_blocked()
                except OSError as exc:
                    self._mark_profile_blocked()
                    raise RuntimeError(
                        "OAuth profile was saved to keyring but stale file storage "
                        "could not be removed; run `ta auth login` again"
                    ) from exc
                return []

        return self._save_file_authority(profile_key, profile)

    def _begin_profile_transition_locked(self) -> None:
        try:
            self._mark_profile_blocked()
        except OSError as exc:
            raise RuntimeError(
                "OAuth profile persistence could not be started safely"
            ) from exc

    def delete_profile(self) -> ProfileDeletion:
        with self.profile_lock():
            return self._delete_profile_locked()

    def _delete_profile_locked(self) -> ProfileDeletion:
        base_url, resource = self._profile_identity()
        profile_key = _profile_key(base_url, resource)
        diagnostics: list[CliDiagnostic] = []
        try:
            self._mark_profile_blocked()
        except OSError as exc:
            return ProfileDeletion(
                deleted=False,
                diagnostics=[_profile_delete_error("block marker", exc)],
            )

        if not self.explicit_token_file:
            keyring = _keyring_module()
            if keyring is not None:
                try:
                    account = f"cli-profile:{profile_key}"
                    if keyring.get_password("track-anywhere", account) is not None:
                        keyring.delete_password("track-anywhere", account)
                    if keyring.get_password("track-anywhere", account) is not None:
                        raise RuntimeError("keyring still contains the OAuth profile")
                except Exception as exc:
                    diagnostics.append(_profile_delete_error("keyring", exc))

        try:
            with _exclusive_file_lock(self._document_lock_path()):
                document, legacy_raw = self._read_profile_document()
                if legacy_raw is not None and self.explicit_token_file:
                    try:
                        self.profile_file.unlink()
                    except FileNotFoundError:
                        pass
                elif profile_key in document["profiles"]:
                    del document["profiles"][profile_key]
                    if document["profiles"]:
                        _atomic_write_text(
                            self.profile_file,
                            json.dumps(document, indent=2, sort_keys=True) + "\n",
                        )
                    else:
                        try:
                            self.profile_file.unlink()
                        except FileNotFoundError:
                            pass
                verified_document, verified_legacy = self._read_profile_document()
                if profile_key in verified_document["profiles"] or (
                    self.explicit_token_file and verified_legacy is not None
                ):
                    raise RuntimeError("profile file still contains the OAuth profile")
        except (OSError, RuntimeError) as exc:
            diagnostics.append(_profile_delete_error("profile file", exc))
        if not diagnostics:
            try:
                self._clear_profile_blocked()
            except OSError as exc:
                diagnostics.append(_profile_delete_error("block marker", exc))
        return ProfileDeletion(deleted=not diagnostics, diagnostics=diagnostics)

    @contextmanager
    def profile_lock(self) -> Iterator[None]:
        base_url, resource = self._profile_identity()
        profile_key = _profile_key(base_url, resource)
        lock_path = self.profile_file.parent / f".auth-profile-{profile_key}.lock"
        with _exclusive_file_lock(lock_path):
            yield

    def _document_lock_path(self) -> Path:
        return self.profile_file.with_name(f".{self.profile_file.name}.lock")

    def _blocked_profile_path(self) -> Path:
        base_url, resource = self._profile_identity()
        profile_key = _profile_key(base_url, resource)
        return self.profile_file.parent / f".auth-profile-{profile_key}.blocked"

    def _assert_profile_not_blocked(self) -> None:
        if self._blocked_profile_path().exists():
            raise RuntimeError(
                "OAuth profile storage is in an inconsistent state; "
                "run `ta auth login` again"
            )

    def _mark_profile_blocked(self) -> None:
        _atomic_write_text(
            self._blocked_profile_path(),
            "OAuth profile storage requires a fresh login.\n",
        )

    def _clear_profile_blocked(self) -> None:
        try:
            self._blocked_profile_path().unlink()
        except FileNotFoundError:
            pass

    def _save_file_authority(
        self, profile_key: str, profile: AuthProfile
    ) -> list[CliDiagnostic]:
        try:
            with _exclusive_file_lock(self._document_lock_path()):
                document, _legacy_raw = self._read_profile_document()
                document["profiles"][profile_key] = profile.to_dict()
                _atomic_write_text(
                    self.profile_file,
                    json.dumps(document, indent=2, sort_keys=True) + "\n",
                )
            self._clear_profile_blocked()
        except OSError as exc:
            self._mark_profile_blocked()
            raise RuntimeError(
                "OAuth profile could not be persisted safely; run `ta auth login` again"
            ) from exc
        return (
            [_token_file_warning(self.profile_file)]
            if not self.explicit_token_file
            else []
        )

    def _remove_profile_file(self, profile_key: str) -> None:
        with _exclusive_file_lock(self._document_lock_path()):
            document, legacy_raw = self._read_profile_document()
            if legacy_raw is not None:
                try:
                    self.profile_file.unlink()
                except FileNotFoundError:
                    pass
                return
            if profile_key not in document["profiles"]:
                return
            del document["profiles"][profile_key]
            if document["profiles"]:
                _atomic_write_text(
                    self.profile_file,
                    json.dumps(document, indent=2, sort_keys=True) + "\n",
                )
            else:
                try:
                    self.profile_file.unlink()
                except FileNotFoundError:
                    pass

    @staticmethod
    def _validate_profile_identity(
        profile: AuthProfile, base_url: str, resource: str
    ) -> None:
        if profile.base_url != base_url or profile.resource != resource:
            raise RuntimeError("OAuth profile storage identity mismatch")

    def _profile_identity(self) -> tuple[str, str]:
        if self.base_url is None or self.resource is None:
            raise ValueError("profile operations require base_url and resource")
        return self.base_url, self.resource

    def _read_profile_document(self) -> tuple[dict[str, object], str | None]:
        empty: dict[str, object] = {"version": 1, "profiles": {}}
        if not self.profile_file.exists():
            return empty, None
        text = self.profile_file.read_text(encoding="utf-8").strip()
        if not text:
            return empty, None
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return empty, text
        if not isinstance(decoded, dict) or decoded.get("version") != 1:
            return empty, text
        profiles = decoded.get("profiles")
        if not isinstance(profiles, dict):
            return empty, None
        return {"version": 1, "profiles": dict(profiles)}, None

    def _load_file(self) -> StoredToken | None:
        if self.token_file.exists():
            token = self.token_file.read_text(encoding="utf-8").strip()
            if token and not token.startswith("{"):
                return StoredToken(token=token, source="token_file")
        return None

    def _save_file(self, token: str) -> None:
        _atomic_write_text(self.token_file, token + "\n")


def generated_idempotency_key(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def command_idempotency_key(args: argparse.Namespace, prefix: str) -> str:
    return getattr(args, "idempotency_key", None) or generated_idempotency_key(prefix)


@dataclass(frozen=True)
class TokenResolution:
    token: str | None
    diagnostics: list[CliDiagnostic]
    source: str | None = None
    credential: RequestCredential | None = None
    profile: AuthProfile | None = None
    store: TokenStore | None = None


def resolve_token_with_diagnostics(args: argparse.Namespace) -> TokenResolution:
    configured_token = getattr(args, "token", None)
    api_key_file = getattr(args, "api_key_file", None)
    env_api_key = os.getenv("TRACK_ANYWHERE_API_KEY")
    env_token = os.getenv("TRACK_ANYWHERE_TOKEN")
    insecure_automation = bool(getattr(args, "insecure_automation", False))

    selected = sum(
        bool(item) for item in (configured_token, api_key_file, env_api_key, env_token)
    )
    if selected > 1:
        raise RuntimeError("configure exactly one OAuth token or API key source")
    if configured_token:
        credential = RequestCredential(kind="oauth", secret=configured_token)
        return TokenResolution(
            token=configured_token,
            credential=credential,
            diagnostics=[],
            source="configured",
        )
    if api_key_file:
        secret = _read_secret_file(Path(api_key_file), label="API key")
        return TokenResolution(
            token=None,
            credential=RequestCredential(kind="api_key", secret=secret),
            diagnostics=[],
            source="api_key_file",
        )
    if env_api_key:
        if not insecure_automation:
            raise RuntimeError(
                "TRACK_ANYWHERE_API_KEY requires --insecure-automation; prefer an API key file"
            )
        return TokenResolution(
            token=None,
            credential=RequestCredential(kind="api_key", secret=env_api_key),
            diagnostics=[
                CliDiagnostic(
                    level="warning",
                    code="insecure_env_api_key",
                    message="Using TRACK_ANYWHERE_API_KEY with --insecure-automation.",
                )
            ],
            source="environment_api_key",
        )
    if env_token:
        if not insecure_automation:
            raise RuntimeError(
                "TRACK_ANYWHERE_TOKEN requires --insecure-automation; prefer OS keyring"
            )
        return TokenResolution(
            token=env_token,
            credential=RequestCredential(kind="oauth", secret=env_token),
            diagnostics=[
                CliDiagnostic(
                    level="warning",
                    code="insecure_env_token",
                    message="Using TRACK_ANYWHERE_TOKEN with --insecure-automation.",
                )
            ],
            source="environment",
        )

    base_url = getattr(args, "base_url", None)
    resource = getattr(args, "resource", None)
    if base_url:
        store = TokenStore(base_url=base_url, resource=resource)
        stored_profile = store.load_profile_with_source()
        if stored_profile is None:
            return TokenResolution(
                token=None,
                credential=None,
                diagnostics=[],
                source=None,
                store=store,
            )
        profile = stored_profile.profile
        return TokenResolution(
            token=profile.access_token,
            credential=RequestCredential(kind="oauth", secret=profile.access_token),
            diagnostics=[],
            source=stored_profile.source,
            profile=profile,
            store=store,
        )

    stored = TokenStore().load_with_source()
    if stored is None:
        return TokenResolution(token=None, diagnostics=[], source=None)
    return TokenResolution(
        token=stored.token,
        credential=RequestCredential(kind="oauth", secret=stored.token),
        diagnostics=[],
        source=stored.source,
    )


def resolve_token(args: argparse.Namespace) -> str | None:
    resolution = resolve_token_with_diagnostics(args)
    return resolution.credential.secret if resolution.credential is not None else None


def canonical_base_url(value: str) -> str:
    return _canonical_absolute_url(value, allow_query=False)


def canonical_resource(value: str) -> str:
    return _canonical_absolute_url(value, allow_query=True)


def canonical_endpoint(value: str) -> str:
    return _canonical_absolute_url(value, allow_query=True)


def validate_transport_url(value: str) -> str:
    canonical = canonical_endpoint(value)
    parsed = urlsplit(canonical)
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
        raise ValueError("plain HTTP is only allowed for loopback hosts")
    return canonical


def _canonical_absolute_url(value: str, *, allow_query: bool) -> str:
    text = value.strip()
    parsed = urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("URL must not contain credentials or a fragment")
    if not allow_query and parsed.query:
        raise ValueError("base URL must not contain a query")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL contains an invalid port") from exc
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        host = f"{host}:{port}"
    path = parsed.path.rstrip("/")
    normalized = SplitResult(
        scheme=scheme,
        netloc=host,
        path=path,
        query=parsed.query if allow_query else "",
        fragment="",
    )
    return urlunsplit(normalized)


def _is_loopback_host(hostname: str | None) -> bool:
    if hostname is None:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _profile_key(base_url: str, resource: str) -> str:
    return hashlib.sha256(f"{base_url}\n{resource}".encode("utf-8")).hexdigest()


def _legacy_profile(base_url: str, resource: str, token: str) -> AuthProfile:
    return AuthProfile(
        base_url=base_url,
        resource=resource,
        issuer=base_url,
        client_id="legacy-unscoped-token",
        access_token=token,
        auth_kind="legacy",
    )


def _decode_profile(encoded: object) -> AuthProfile | None:
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        decoded = json.loads(encoded)
        if not isinstance(decoded, dict):
            return None
        return AuthProfile.from_dict(decoded)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _required_text(value: dict[str, object], key: str) -> str:
    text = _optional_text(value.get(key))
    if text is None:
        raise ValueError(f"OAuth profile is missing {key}")
    return text


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("OAuth profile expiry must be numeric")
    return float(value)


def _read_secret_file(path: Path, *, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"{label} file must be a regular non-symlink file")
    secret = path.read_text(encoding="utf-8").strip()
    if not secret:
        raise RuntimeError(f"{label} file is empty")
    return secret


def _atomic_write_text(path: Path, value: str) -> None:
    try:
        path.parent.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    try:
        path.parent.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    locked = False
    try:
        os.fchmod(descriptor, 0o600)
        _lock_descriptor(descriptor)
        locked = True
        yield
    finally:
        if locked:
            _unlock_descriptor(descriptor)
        os.close(descriptor)


def _lock_descriptor(descriptor: int) -> None:
    if fcntl is not None:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return
    if msvcrt is None:  # pragma: no cover - unsupported platform
        raise RuntimeError("cross-process file locking is unavailable")
    if os.fstat(descriptor).st_size == 0:  # pragma: no cover - Windows fallback
        os.write(descriptor, b"\0")
        os.fsync(descriptor)
    os.lseek(descriptor, 0, os.SEEK_SET)
    msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)


def _unlock_descriptor(descriptor: int) -> None:
    if fcntl is not None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return
    if msvcrt is not None:  # pragma: no cover - Windows fallback
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)


def _profile_delete_error(storage: str, exc: Exception) -> CliDiagnostic:
    return CliDiagnostic(
        level="error",
        code="profile_delete_failed",
        category="security",
        message=f"Could not delete OAuth profile from {storage}: {exc}",
        retryable=True,
        detail={"storage": storage, "exception_type": type(exc).__name__},
    )


def _keyring_module():
    try:
        import keyring  # type: ignore
    except Exception:
        return None
    return keyring


def _token_file_warning(path: Path) -> CliDiagnostic:
    return CliDiagnostic(
        level="warning",
        code="token_file_fallback",
        message=f"OS keyring unavailable; saved token to {path}.",
    )
