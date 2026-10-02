import { expect, it } from "vitest";
import { parseEtlSteps } from "../src/utils/etl";
it("keeps legacy ETL steps and exposes partial retryable stages", () => {
  const steps = [{ name: "rag", status: "failed" }];
  expect(parseEtlSteps(JSON.stringify(steps))).toEqual({ steps, retryable: [] });
  expect(parseEtlSteps(JSON.stringify({ steps, retryable_steps: ["rag"] }))).toEqual({ steps, retryable: ["rag"] });
  expect(parseEtlSteps("bad json")).toEqual({ steps: [], retryable: [] });
});
