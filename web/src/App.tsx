import { useEffect } from "react";
import { Route, Routes, useLocation } from "react-router-dom";

import { Layout } from "./components/Layout";
import { PricePage } from "./pages/PricePage";
import { ResultsPage } from "./pages/ResultsPage";
import { SearchPage } from "./pages/SearchPage";
import { StatusPage } from "./pages/StatusPage";

const TITLES: [RegExp, string][] = [
  [/^\/results/, "Results · OpenCharge"],
  [/^\/hospital\//, "Price details · OpenCharge"],
  [/^\/status/, "Data status · OpenCharge"],
];

function usePageTitle() {
  const { pathname } = useLocation();
  useEffect(() => {
    document.title = TITLES.find(([pattern]) => pattern.test(pathname))?.[1] ?? "OpenCharge";
    window.scrollTo(0, 0);
  }, [pathname]);
}

export function App() {
  usePageTitle();
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<SearchPage />} />
        <Route path="/results" element={<ResultsPage />} />
        <Route path="/hospital/:hospitalId/price/:code" element={<PricePage />} />
        <Route path="/status" element={<StatusPage />} />
        <Route
          path="*"
          element={
            <div className="container" style={{ padding: "48px 16px" }}>
              <h1>Page not found</h1>
            </div>
          }
        />
      </Routes>
    </Layout>
  );
}
