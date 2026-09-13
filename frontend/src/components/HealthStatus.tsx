import type { HealthResponse } from "../api/health";

interface HealthStatusProps {
  health?: HealthResponse;
  isError: boolean;
  isPending: boolean;
}

export function HealthStatus({
  health,
  isError,
  isPending,
}: HealthStatusProps): JSX.Element {
  if (isPending) {
    return <p className="status status--loading">正在检查后端状态…</p>;
  }

  if (isError || health === undefined) {
    return (
      <p className="status status--error" role="alert">
        后端请求失败
      </p>
    );
  }

  return (
    <p className="status status--healthy">
      后端正常
      <span className="status__detail">{health.service}</span>
    </p>
  );
}
