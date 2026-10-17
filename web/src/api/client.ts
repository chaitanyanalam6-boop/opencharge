import type {
  HospitalPriceDetail,
  Interpretation,
  Payer,
  SearchResponse,
  StatusResponse,
} from "./types";

// Empty in development (Vite proxies /api); the deployed API's origin in production.
const BASE = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "Can't reach the OpenCharge API. Is the server running?");
  }
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try {
      const body = await response.json();
      if (typeof body.error === "string") message = body.error;
      else if (Array.isArray(body.detail)) message = "That search couldn't be run as written.";
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export const api = {
  search: (q: string) => request<SearchResponse>(`/api/search?q=${encodeURIComponent(q)}`),
  run: (interpretation: Interpretation) =>
    request<SearchResponse>("/api/search/run", {
      method: "POST",
      body: JSON.stringify(interpretation),
    }),
  hospitalPrice: (hospitalId: string, code: string, codeType: string) =>
    request<HospitalPriceDetail>(
      `/api/hospital/${encodeURIComponent(hospitalId)}/price/${encodeURIComponent(code)}?code_type=${codeType}`,
    ),
  status: () => request<StatusResponse>("/api/status"),
  payers: () => request<Payer[]>("/api/payers"),
};
