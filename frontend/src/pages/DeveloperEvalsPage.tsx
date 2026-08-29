import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import {
  cancelEvalRun,
  createEvalRun,
  fetchEvalProgress,
  fetchEvalReport,
  fetchEvalRuns,
  fetchEvalStatus,
  fetchLatestCalibration,
} from "@/api/evals";

const TERMINAL = new Set(["completed", "failed", "cancelled"]);

export function DeveloperEvalsPage(): JSX.Element {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dataset, setDataset] = useState<"runtime-smoke" | "stage5">("runtime-smoke");
  const [providerMode, setProviderMode] = useState<"mock" | "fixture">("mock");
  const runs = useQuery({ queryKey: ["dev-evals"], queryFn: fetchEvalRuns, retry: false });
  const selected = runs.data?.items.find((item) => item.experiment_id === selectedId) ?? null;
  const status = useQuery({
    queryKey: ["dev-eval-status", selectedId],
    queryFn: () => fetchEvalStatus(selectedId ?? ""),
    enabled: selectedId !== null,
    retry: false,
    refetchInterval: (query) => TERMINAL.has(query.state.data?.status ?? "") ? false : 1000,
  });
  const progress = useQuery({
    queryKey: ["dev-eval-progress", selectedId],
    queryFn: () => fetchEvalProgress(selectedId ?? ""),
    enabled: selectedId !== null,
    retry: false,
    refetchInterval: (query) => TERMINAL.has(query.state.data?.status ?? "") ? false : 1000,
  });
  const report = useQuery({
    queryKey: ["dev-eval-report", selectedId],
    queryFn: () => fetchEvalReport(selectedId ?? ""),
    enabled: selectedId !== null && TERMINAL.has(status.data?.status ?? selected?.status ?? ""),
    retry: false,
  });
  const calibration = useQuery({
    queryKey: ["dev-eval-calibration", selected?.dataset_id, selected?.dataset_version],
    queryFn: () => fetchLatestCalibration(selected?.dataset_id ?? "", selected?.dataset_version ?? ""),
    enabled: selected !== null,
    retry: false,
  });
  const create = useMutation({
    mutationFn: createEvalRun,
    onSuccess: async (created) => {
      setSelectedId(created.experiment_id);
      await queryClient.invalidateQueries({ queryKey: ["dev-evals"] });
    },
  });
  const cancel = useMutation({
    mutationFn: cancelEvalRun,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["dev-evals"] }),
        queryClient.invalidateQueries({ queryKey: ["dev-eval-status", selectedId] }),
        queryClient.invalidateQueries({ queryKey: ["dev-eval-progress", selectedId] }),
      ]);
    },
  });

  const calibrationMode = calibration.data?.usage_mode ?? "diagnostic_only";

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-primary">Developer Console</p>
          <h1 className="mt-1 text-2xl font-semibold">Eval Harness V2</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Case → Experiment → Trial → Grade → Report。浏览器仅创建免费 mock/fixture 小实验。
          </p>
        </div>
        <div className="flex gap-3 text-sm">
          <Link className="text-primary underline" to="/dev/runs">Run Trace</Link>
          <Link className="text-primary underline" to="/me">返回我的</Link>
        </div>
      </header>

      <section className="rounded-xl border bg-card p-4" aria-label="Create Eval experiment">
        <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <label className="grid gap-1 text-sm">
            Dataset
            <select value={dataset} onChange={(event) => setDataset(event.target.value as typeof dataset)}>
              <option value="runtime-smoke">runtime-smoke（1 case）</option>
              <option value="stage5">stage5（30 cases）</option>
            </select>
          </label>
          <label className="grid gap-1 text-sm">
            Provider
            <select value={providerMode} onChange={(event) => setProviderMode(event.target.value as typeof providerMode)}>
              <option value="mock">mock</option>
              <option value="fixture">fixture record/replay</option>
            </select>
          </label>
          <button
            type="button"
            disabled={create.isPending}
            onClick={() => create.mutate({ dataset, providerMode })}
          >
            创建确定性实验
          </button>
        </div>
        {create.isError ? <p role="alert">创建实验失败。</p> : null}
      </section>

      {runs.isError ? <p role="alert">无法读取 Eval Experiments。</p> : null}
      <div className="grid gap-4 lg:grid-cols-[minmax(260px,0.8fr)_minmax(0,1.5fr)]">
        <section className="rounded-xl border bg-card p-4" aria-label="Eval experiment list">
          <h2 className="font-semibold">Experiments</h2>
          <ol className="mt-3 space-y-2">
            {runs.data?.items.map((item) => (
              <li key={item.experiment_id}>
                <button className="w-full rounded-lg border p-3 text-left" type="button" onClick={() => setSelectedId(item.experiment_id)}>
                  <strong>{item.status}</strong> · {item.execution_mode}
                  <span className="block text-xs text-muted-foreground">{item.dataset_id}@{item.dataset_version}</span>
                  <span className="block text-xs">{item.variant_role} / {item.agent_variant ?? "full_agent_v1"}</span>
                </button>
              </li>
            ))}
          </ol>
        </section>

        <section className="rounded-xl border bg-card p-4" aria-label="Eval experiment detail">
          <h2 className="font-semibold">Experiment detail</h2>
          {selectedId === null ? <p className="mt-3 text-sm text-muted-foreground">选择一个 Experiment。</p> : null}
          {status.data ? (
            <div className="mt-3 space-y-4 text-sm">
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                <dt>Status</dt><dd>{status.data.status}</dd>
                <dt>Progress</dt><dd>{Math.round((progress.data?.estimated_progress ?? 0) * 100)}%</dd>
                <dt>Runtime</dt><dd>Stage {status.data.feature_stage} · {status.data.graph_version}</dd>
                <dt>Source</dt><dd>{status.data.git_commit.slice(0, 12)} · {status.data.prompt_version}</dd>
                <dt>Memory/Search</dt><dd>{status.data.memory_version} / {status.data.search_version}</dd>
                <dt>Pairwise gate</dt><dd><strong>{calibrationMode}</strong> ({calibration.data?.calibration_status ?? "insufficient"})</dd>
              </dl>
              {!TERMINAL.has(status.data.status) ? (
                <button type="button" disabled={cancel.isPending} onClick={() => cancel.mutate(status.data.experiment_id)}>
                  取消实验
                </button>
              ) : null}
              <div>
                <h3 className="font-medium">Trials / failures</h3>
                <ol className="mt-1 space-y-1">
                  {status.data.trials.map((trial) => (
                    <li key={trial.trial_id}>{trial.case_id}: {trial.status}{trial.error_code ? ` · ${trial.error_code}` : ""}</li>
                  ))}
                </ol>
              </div>
            </div>
          ) : null}
          {report.data ? (
            <div className="mt-5 space-y-2 text-sm">
              <h3 className="font-medium">Report summary</h3>
              <p>{report.data.scored_trial_count}/{report.data.trial_count} scored · hard gate {Math.round(report.data.hard_gate_pass_fraction * 100)}%</p>
              <p>Token: {report.data.trials.reduce((sum, trial) => sum + Number(trial.tokens_in ?? 0) + Number(trial.tokens_out ?? 0), 0)}</p>
              <ul>{Object.entries(report.data.failure_counts).map(([kind, count]) => <li key={kind}>{kind}: {count}</li>)}</ul>
            </div>
          ) : null}
          {status.isError || progress.isError || report.isError || calibration.isError ? <p role="alert">部分 Eval 详情暂时不可用。</p> : null}
        </section>
      </div>
    </div>
  );
}
