import type { CareerStage, GoalType, PlanStatus, SkillLevel, TaskStatus, TaskType } from "@/api/types";

export const GOAL_LABELS: Record<GoalType, string> = {
  ai_backend: "AI 后端",
  agent_app: "Agent 应用",
  backend_java: "Java 后端",
  data_engineer: "数据工程",
  fullstack: "全栈",
  other: "其他方向",
};

export const STAGE_LABELS: Record<CareerStage, string> = {
  exploring: "探索方向",
  preparing: "准备能力",
  applying: "开始投递",
  interviewing: "面试冲刺",
};

export const SKILL_LABELS: Record<SkillLevel, string> = {
  beginner: "刚入门",
  intermediate: "有一定基础",
  advanced: "较为熟练",
};

export const PLAN_STATUS_LABELS: Record<PlanStatus, string> = {
  generated: "待开始",
  active: "进行中",
  completed: "已完成",
  archived: "历史版本",
};

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  pending: "未开始",
  in_progress: "进行中",
  completed: "已完成",
  abandoned: "今天先放下",
  expired: "已过期",
};

export const TASK_TYPE_LABELS: Record<TaskType, string> = {
  learning: "学习",
  project: "项目",
  interview: "面试",
  application: "投递",
  resume: "简历",
  other: "其他",
};
