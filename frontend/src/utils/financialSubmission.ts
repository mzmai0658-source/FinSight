export const processingLabels: Record<string, string> = {
  PENDING: "排队中", RUNNING: "处理中", SUCCESS: "处理完成", PARTIAL: "部分处理完成", FAILED: "处理失败",
};
export const reviewLabels: Record<string, string> = {
  PROCESSING: "尚在处理", WAITING: "待审核", APPROVED: "已批准，待发布", REJECTED: "已拒绝", PUBLISHED: "已发布",
};
export function reviewVariant(status: string): "default" | "primary" | "success" | "warning" | "danger" {
  return status === "PUBLISHED" ? "success" : status === "APPROVED" ? "primary" : status === "WAITING" ? "warning" : status === "REJECTED" ? "danger" : "default";
}
export function displayEvidence(value: unknown): string {
  if (value == null || value === "") return "未登记";
  return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
}
