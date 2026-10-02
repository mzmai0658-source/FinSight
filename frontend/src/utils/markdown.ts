import { marked } from "marked";
import DOMPurify from "dompurify";

marked.setOptions({
  gfm: true,
  breaks: true,
});

/** 作品说明：转义原始 HTML，防止 LLM 输出注入标签；随后再交给 marked 解析 Markdown。 */
function escapeHtml(raw: string): string {
  return raw.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function renderMarkdown(content: string): string {
  const escaped = escapeHtml(String(content ?? ""));
  const html = marked.parse(escaped, { async: false }) as string;
  return sanitizeRenderedHtml(html);
}

/** 作品说明：OCR HTML 表格保留结构，同时移除可执行内容。 */
export function renderOcrMarkdown(content: string): string {
  return sanitizeRenderedHtml(marked.parse(String(content ?? ''), { async: false }) as string);
}

function sanitizeRenderedHtml(html: string): string {
  const sanitized = DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["img", "video", "audio", "iframe", "form", "input", "button", "style"],
    FORBID_ATTR: ["style", "srcset"],
  });
  const template = document.createElement("template");
  template.innerHTML = sanitized;
  for (const anchor of template.content.querySelectorAll("a")) {
    const href = safeLinkUrl(anchor.getAttribute("href") ?? "");
    if (href) {
      anchor.setAttribute("href", href);
      anchor.setAttribute("target", "_blank");
      anchor.setAttribute("rel", "noopener noreferrer");
    } else {
      anchor.removeAttribute("href");
      anchor.removeAttribute("target");
    }
  }
  return template.innerHTML;
}

/** 作品说明：模型生成链接仅允许普通网页或邮件协议，禁止本机或可执行地址。 */
export function safeLinkUrl(raw: string): string {
  if (!raw || /[\u0000-\u0020\u007f]/.test(raw)) return "";
  try {
    const url = new URL(raw);
    return ["https:", "http:", "mailto:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}
