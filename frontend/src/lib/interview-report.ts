const SEEN_REPORT_PREFIX = "cpb_seen_interview_report:";

export function markInterviewReportSeen(interviewId: string): void {
  localStorage.setItem(`${SEEN_REPORT_PREFIX}${interviewId}`, "1");
}

export function isInterviewReportSeen(interviewId: string): boolean {
  return localStorage.getItem(`${SEEN_REPORT_PREFIX}${interviewId}`) === "1";
}
