from __future__ import annotations

from ..storage_changes import AuthorizationGrantChanges, DeviceGrantChanges


class PlatformAuthGrantStorageWriters:
    def save_authorization_grant_change(self, changes: AuthorizationGrantChanges) -> None:
        with self.unit_of_work() as uow:
            uow.platform_grants.save_authorization_grants(changes.grants)

    def save_device_grant_change(self, changes: DeviceGrantChanges) -> None:
        with self.unit_of_work() as uow:
            uow.platform_grants.save_device_grants(changes.grants)
