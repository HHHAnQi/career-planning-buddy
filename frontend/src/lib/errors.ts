import { ApiError } from "@/api/client";

export interface UserFacingError {
  title: string;
  message: string;
  action: "retry" | "refresh" | "view-progress";
  requestId: string | null;
}

export function toUserFacingError(error: unknown): UserFacingError {
  if (!(error instanceof ApiError)) {
    return {
      title: "暂时没能完成这一步",
      message: "请稍后再试，你已经保存的内容不会丢失。",
      action: "retry",
      requestId: null,
    };
  }

  if (error.code === "NETWORK_UNREACHABLE") {
    return {
      title: "网络似乎断开了",
      message: "正在等待重新连接，你已经完成的进度不会丢失。",
      action: "retry",
      requestId: error.requestId,
    };
  }
  if (error.code === "STATE_RUN_ALREADY_ACTIVE") {
    return {
      title: "已有一份路线正在生成",
      message: "返回今天页面即可继续查看生成进度。",
      action: "view-progress",
      requestId: error.requestId,
    };
  }
  if (error.code === "SAFETY_HIGH_RISK_INPUT") {
    return {
      title: "请先确保你现在是安全的",
      message: "请尽快联系身边可信任的人或当地紧急服务。本服务不提供医疗诊断或紧急救援。",
      action: "retry",
      requestId: error.requestId,
    };
  }
  if (error.code === "STATE_TASK_EXECUTION_INCOMPLETE") {
    return {
      title: "执行步骤还没有完成",
      message: "请先完成全部执行步骤，再进行验收。",
      action: "retry",
      requestId: error.requestId,
    };
  }
  if (error.code === "STATE_PLAN_NOT_MUTABLE") {
    return {
      title: "当前计划不能继续修改",
      message: "这不是刷新问题，请返回路线页确认当前正在执行的计划。",
      action: "retry",
      requestId: error.requestId,
    };
  }
  if (error.code?.includes("VERSION") === true || error.status === 409) {
    return {
      title: "内容已经更新",
      message: "请刷新后再继续操作。",
      action: "refresh",
      requestId: error.requestId,
    };
  }
  if (error.status === 429 || error.code?.includes("RATE") === true) {
    return {
      title: "规划服务有点忙",
      message: "稍等片刻后重试即可。",
      action: "retry",
      requestId: error.requestId,
    };
  }
  return {
    title: "暂时没能完成这一步",
    message: "请稍后重试；如果问题持续，可把请求编号提供给开发者。",
    action: "retry",
    requestId: error.requestId,
  };
}
