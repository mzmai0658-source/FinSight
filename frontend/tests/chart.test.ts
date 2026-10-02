// @vitest-environment node
import { expect, it } from "vitest";
import * as echarts from "echarts";
it("renders a financial trend with the patched ECharts runtime", () => {
  const chart = echarts.init(null, undefined, { renderer: "svg", ssr: true, width: 500, height: 300 });
  try {
    chart.setOption({ xAxis: { type: "category", data: ["2022", "2023", "2024"] }, yAxis: { type: "value" }, series: [{ type: "line", data: [101, 202, 303] }] });
    const svg = chart.renderToSVGString();
    expect(svg).toContain("<svg");
    expect(svg).toContain("2024");
    expect(svg).toContain("path");
  } finally { chart.dispose(); }
});
