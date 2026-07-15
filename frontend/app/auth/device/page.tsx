import { Suspense } from "react";
import { DeviceAuthorization } from "./device-authorization";

export default function DeviceAuthorizationPage() {
  return (
    <Suspense>
      <DeviceAuthorization />
    </Suspense>
  );
}
