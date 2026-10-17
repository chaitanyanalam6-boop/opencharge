import { useEffect, useState } from "react";

import { ApiError } from "../api/client";

export type Loadable<T> =
  | { state: "loading" }
  | { state: "error"; error: ApiError }
  | { state: "ready"; data: T };

/** Run `load` whenever `key` changes; ignore responses that arrive after a newer request. */
export function useApi<T>(load: (() => Promise<T>) | null, key: string): Loadable<T> {
  const [result, setResult] = useState<Loadable<T>>({ state: "loading" });

  useEffect(() => {
    if (!load) return;
    let current = true;
    setResult({ state: "loading" });
    load()
      .then((data) => current && setResult({ state: "ready", data }))
      .catch((error: unknown) => {
        if (!current) return;
        const apiError = error instanceof ApiError ? error : new ApiError(0, "Something went wrong.");
        setResult({ state: "error", error: apiError });
      });
    return () => {
      current = false;
    };
    // `key` captures everything `load` depends on.
  }, [key]);

  return result;
}
