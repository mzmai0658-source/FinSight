import { describe, expect, it } from "vitest";
import { renderMarkdown } from "../src/utils/markdown";

describe("Markdown safety", () => {
  it.each([
    "[source](javascript:alert(1))",
    "[source](jav&#x61;script:alert(1))",
    "[source](data:text/html;base64,PHNjcmlwdD4=)",
    "[source](file:///C:/secret.txt)",
    "[source](//untrusted.example/track)",
    "![track](https://untrusted.example/track)",
    '<img src=x onerror="alert(1)"><script>alert(1)</script>',
  ])("removes executable links and remote images: %s", (input) => {
    const element = document.createElement("div");
    element.innerHTML = renderMarkdown(input);
    expect(element.querySelector("script,img,iframe,svg,[onerror]")).toBeNull();
    for (const anchor of element.querySelectorAll("a")) expect(anchor.hasAttribute("href")).toBe(false);
  });
  it("retains ordinary Markdown and secure external links", () => {
    const element = document.createElement("div");
    element.innerHTML = renderMarkdown("**财报** [公司公告](https://example.org/report.pdf)\n\n|年份|收入|\n|--|--|\n|2024|10|");
    expect(element.querySelector("strong")?.textContent).toBe("财报");
    expect(element.querySelector("table")).not.toBeNull();
    expect(element.querySelector("a")?.rel).toBe("noopener noreferrer");
    expect(element.querySelector("a")?.href).toBe("https://example.org/report.pdf");
  });
});
