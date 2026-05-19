import { Suspense } from "react";
import { CliCallback } from "./cli-callback";

export default function AuthCallbackPage() {
  return (
    <Suspense>
      <CliCallback />
    </Suspense>
  );
}
