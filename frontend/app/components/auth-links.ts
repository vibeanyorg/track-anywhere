export function accountUrl(path: "login" | "signup") {
  return `/auth/${path}?next=${encodeURIComponent("/#auth")}`;
}
