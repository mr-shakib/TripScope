import { Navigate, Outlet, useLocation } from "react-router-dom";

import { ApiError } from "../api/client";
import { useMe } from "../api/hooks";
import { ErrorState, LoadingState } from "./StateViews";

export function RequireAuth() {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) return <LoadingState label="Checking your session…" />;
  if (me.error instanceof ApiError && me.error.status === 401) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  if (me.isError) return <ErrorState error={me.error} onRetry={() => void me.refetch()} />;
  return <Outlet />;
}
