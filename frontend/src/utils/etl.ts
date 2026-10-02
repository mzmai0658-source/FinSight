export interface EtlStep { name: string; status: string; detail?: string }
/** 作品说明：兼容旧步骤数组与当前包含可重试阶段的结果封装。 */
export function parseEtlSteps(raw?: string | null): { steps: EtlStep[]; retryable: string[] } {
  try {
    const value = JSON.parse(raw || "[]");
    return {
      steps: Array.isArray(value) ? value : Array.isArray(value?.steps) ? value.steps : [],
      retryable: !Array.isArray(value) && Array.isArray(value?.retryable_steps) ? value.retryable_steps.map(String) : [],
    };
  } catch { return { steps: [], retryable: [] }; }
}
