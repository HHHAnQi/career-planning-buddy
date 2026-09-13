import { Link, useNavigate } from "react-router-dom";
import { useState } from "react";
import { useMe } from "@/api/auth";
import { useCreateReview, useDeleteReview, useReviews, useStartNextPlan, useUpdateReview } from "@/api/reviews";
import { useActivePlan } from "@/api/plans";
import type { ActivePlanResponse, ReviewResponse } from "@/api/types";
import { localDateIso } from "@/components/WeeklyTaskSchedule";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { toUserFacingError } from "@/lib/errors";

const MOOD_EMOJI = ["😞", "😕", "😐", "🙂", "😄"];

function moodLabel(mood: number): string {
  return MOOD_EMOJI[mood - 1] ?? "—";
}

function ReviewForm({ planId, review, onClose }: { planId: string; review?: ReviewResponse; onClose: () => void }) {
  const today = new Date().toISOString().slice(0, 10);
  const createReview = useCreateReview();
  const updateReview = useUpdateReview();
  const [mood, setMood] = useState(review?.mood ?? 3);
  const [blockers, setBlockers] = useState(review?.blockers ?? "");
  const [adjustmentRequest, setAdjustmentRequest] = useState(review?.adjustment_request ?? "");
  const [freeText, setFreeText] = useState(review?.free_text ?? "");
  const pending = createReview.isPending || updateReview.isPending;
  const mutationError = createReview.error ?? updateReview.error;
  const displayError = mutationError === null ? null : toUserFacingError(mutationError);

  function submit(): void {
    if (pending) return;
    if (review !== undefined) {
      updateReview.mutate(
        {
          reviewId: review.review_id,
          payload: {
            version: review.version,
            mood,
            blockers: blockers.trim() || null,
            adjustment_request: adjustmentRequest.trim() || null,
            free_text: freeText.trim() || null,
          },
        },
        { onSuccess: () => onClose() },
      );
      return;
    }
    createReview.mutate(
      {
        payload: {
          plan_id: planId,
          review_date: today,
          mood,
          blockers: blockers.trim() || null,
          adjustment_request: adjustmentRequest.trim() || null,
          free_text: freeText.trim() || null,
        },
        idempotencyKey: `review-${Date.now()}`,
      },
      { onSuccess: () => onClose() }
    );
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{review === undefined ? "今日复盘" : "修改复盘"}</DialogTitle>
          <DialogDescription>记录今天的状态；周中用于调整待办，周期结束后用于本周结算。</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label>今天的整体感受</Label>
            <div className="flex gap-2">
              {MOOD_EMOJI.map((e, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => setMood(i + 1)}
                  className={`rounded-md p-2 text-xl transition-colors ${
                    mood === i + 1 ? "bg-accent" : "hover:bg-secondary"
                  }`}
                >
                  {e}
                </button>
              ))}
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="blockers">遇到的阻碍（可空）</Label>
            <Textarea
              id="blockers"
              rows={2}
              value={blockers}
              onChange={(e) => setBlockers(e.target.value)}
              placeholder="比如：知识点太散、时间不够…"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="adjust">想做的调整（可空）</Label>
            <Textarea
              id="adjust"
              rows={2}
              value={adjustmentRequest}
              onChange={(e) => setAdjustmentRequest(e.target.value)}
              placeholder="比如：减少每日任务量、换重点方向…"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="free">自由记录（可空）</Label>
            <Textarea
              id="free"
              rows={2}
              value={freeText}
              onChange={(e) => setFreeText(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          {displayError !== null && <p className="mr-auto text-sm text-destructive">{displayError.message}</p>}
          <Button onClick={submit} disabled={pending}>
            {pending ? "保存中…" : review === undefined ? "提交复盘" : "保存修改"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ReviewItem({
  review,
  canStartNext,
  onStartNext,
  onEdit,
  onDelete,
}: {
  review: ReviewResponse;
  canStartNext: boolean;
  onStartNext: (id: string) => void;
  onEdit: (review: ReviewResponse) => void;
  onDelete: (review: ReviewResponse) => void;
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="text-xl">{moodLabel(review.mood)}</span>
            <CardTitle className="text-base">{review.review_date}</CardTitle>
          </div>
          {review.suggested_replan && <Badge variant="warning">建议调整</Badge>}
        </div>
        <CardDescription>
          完成 {review.completed_count} · 放弃 {review.abandoned_count}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {review.blockers !== null && (
          <p>
            <span className="text-muted-foreground">阻碍：</span>
            {review.blockers}
          </p>
        )}
        {review.adjustment_request !== null && (
          <p>
            <span className="text-muted-foreground">调整：</span>
            {review.adjustment_request}
          </p>
        )}
        {review.free_text !== null && (
          <p>
            <span className="text-muted-foreground">记录：</span>
            {review.free_text}
          </p>
        )}
        <p className="text-muted-foreground">{review.companion_message}</p>
        {review.next_plan_run_id === null && (
          <div className="flex gap-2 pt-1">
            <Button variant="ghost" size="sm" onClick={() => onEdit(review)}>修改</Button>
            <Button variant="ghost" size="sm" className="text-destructive" onClick={() => onDelete(review)}>删除</Button>
          </div>
        )}
        {review.next_plan_run_id !== null && <p className="text-xs text-muted-foreground">该复盘已用于生成后续计划，现作为执行依据保留，不能再修改或删除。</p>}
        {review.next_plan_run_id === null && canStartNext && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => onStartNext(review.review_id)}
            className="mt-2"
          >
            完成本周结算并生成下一周 →
          </Button>
        )}
        {review.next_plan_run_id === null && !canStartNext && (
          <p className="rounded-lg bg-muted/60 px-3 py-2 text-xs leading-5 text-muted-foreground">
            当前固定周周期仍在进行，不会提前替换整周计划。需要调整时，请进入对应日期修改待办。
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export function ReviewsPage(): JSX.Element {
  const me = useMe();
  const reviews = useReviews();
  const activePlan = useActivePlan();
  const startNext = useStartNextPlan();
  const deleteReview = useDeleteReview();
  const [showForm, setShowForm] = useState(false);
  const [editingReview, setEditingReview] = useState<ReviewResponse | null>(null);
  const [deletingReview, setDeletingReview] = useState<ReviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const resolvedPlan = me.data?.active_plan ?? activePlan.data;
  const hasPlan =
    me.data?.profile_complete === true &&
    me.data?.planning_window_valid !== false &&
    resolvedPlan !== null &&
    resolvedPlan !== undefined;
  const canSettlePlan = resolvedPlan === null || resolvedPlan === undefined
    ? false
    : isFixedWeekClosed(resolvedPlan);

  function onStartNext(reviewId: string): void {
    if (startNext.isPending) return;
    setError(null);
    startNext.mutate(
      { reviewId, idempotencyKey: `next-${Date.now()}` },
      {
        onSuccess: () => {
          navigate("/today");
        },
        onError: (err: unknown) => {
          setError(toUserFacingError(err).message);
        },
      }
    );
  }

  if (reviews.isLoading) {
    return <div className="text-muted-foreground">正在加载复盘…</div>;
  }

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div className="flex items-end justify-between gap-4">
        <div><p className="text-sm font-medium text-primary">看见真实节奏</p><h1 className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">复盘</h1><p className="mt-2 text-sm text-muted-foreground">不是评判完成多少，而是让下一步更合适。</p></div>
        {hasPlan && (
          <Button size="sm" onClick={() => setShowForm(true)}>
            新建复盘
          </Button>
        )}
      </div>

      {!hasPlan && me.data?.profile_complete === true && (
        <Card>
          <CardContent className="py-6 text-sm text-muted-foreground">
            {me.data?.planning_window_valid === false ? "规划周期已经结束，请先更新日期。" : "需要先生成至少一份计划才能开始复盘。"}
            <Link to={me.data?.planning_window_valid === false ? "/settings/profile" : "/today"} className="ml-2 text-primary hover:underline">去处理 →</Link>
          </CardContent>
        </Card>
      )}

      {error !== null && <p className="text-sm text-destructive">{error}</p>}

      {showForm && resolvedPlan !== undefined && resolvedPlan !== null && (
        <ReviewForm
          planId={resolvedPlan.plan_id}
          onClose={() => setShowForm(false)}
        />
      )}
      {editingReview !== null && <ReviewForm planId={editingReview.plan_id} review={editingReview} onClose={() => setEditingReview(null)} />}
      {deletingReview !== null && (
        <Dialog open onOpenChange={(open) => !open && setDeletingReview(null)}>
          <DialogContent><DialogHeader><DialogTitle>删除这条复盘？</DialogTitle><DialogDescription>删除后无法恢复，也不会再用于后续计划判断。</DialogDescription></DialogHeader>{deleteReview.error !== null && <p className="text-sm text-destructive">{toUserFacingError(deleteReview.error).message}</p>}<DialogFooter><Button variant="outline" onClick={() => setDeletingReview(null)}>取消</Button><Button variant="destructive" disabled={deleteReview.isPending} onClick={() => deleteReview.mutate(deletingReview.review_id, { onSuccess: () => setDeletingReview(null) })}>{deleteReview.isPending ? "删除中…" : "确认删除"}</Button></DialogFooter></DialogContent>
        </Dialog>
      )}

      {!reviews.isLoading && (reviews.data?.items.length ?? 0) === 0 ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            还没有复盘记录。
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {(reviews.data?.items ?? []).map((r) => (
            <ReviewItem
              key={r.review_id}
              review={r}
              canStartNext={
                canSettlePlan
                && resolvedPlan !== null
                && resolvedPlan !== undefined
                && r.plan_id === resolvedPlan.plan_id
              }
              onStartNext={onStartNext}
              onEdit={setEditingReview}
              onDelete={setDeletingReview}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function isFixedWeekClosed(plan: ActivePlanResponse): boolean {
  if (plan.tasks.length > 0 && plan.tasks.every((task) => ["completed", "abandoned", "expired"].includes(task.state))) {
    return true;
  }
  const cycleEnd = new Date(`${plan.plan_date}T00:00:00`);
  cycleEnd.setDate(cycleEnd.getDate() + 6);
  const horizonEnd = new Date(`${plan.horizon_end}T00:00:00`);
  if (horizonEnd < cycleEnd) cycleEnd.setTime(horizonEnd.getTime());
  return localDateIso() > localDateIso(cycleEnd);
}
