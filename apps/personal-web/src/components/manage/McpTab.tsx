import { cn, Section } from "@career-brain/ui";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { OAuthTab } from "../system/OAuthTab";
import { NewTokenForm } from "../TokensSection";
import { AuditSection } from "./RunsTab";
import { CopyButton } from "./parts";

const API_BASE: string = import.meta.env.VITE_API_BASE ?? "/api";
const DOCS = "https://github.com/gkrtjd99/career-brain/blob/main/docs/architecture/mcp.md";

/** 지금 화면이 붙은 API 주소 + /core/mcp. VITE_API_BASE가 절대 주소면 그대로 쓴다 */
export function mcpUrl() {
  const base = /^https?:\/\//.test(API_BASE) ? API_BASE : `${window.location.origin}${API_BASE}`;
  return `${base.replace(/\/$/, "")}/core/mcp`;
}

type Client = "claude-code" | "codex" | "claude-desktop" | "cursor";

const CLIENTS: { value: Client; label: string }[] = [
  { value: "claude-code", label: "Claude Code" },
  { value: "codex", label: "Codex" },
  { value: "claude-desktop", label: "Claude Desktop" },
  { value: "cursor", label: "Cursor" },
];

/** 클라이언트별 설정 조각. 토큰 자리는 언제나 <토큰> (실제 값을 넣어 보이지 않는다) */
function snippet(client: Client, url: string): { where: string; code: string; note?: string } {
  switch (client) {
    case "claude-code":
      return {
        where: "터미널에서 한 번",
        code: `claude mcp add --transport http career-brain ${url} \\\n  --header "Authorization: Bearer <토큰>"`,
        note: "프로젝트에만 두려면 --scope project. 설정 파일을 커밋한다면 토큰 대신 ${CAREER_API_TOKEN} 같은 환경변수를 쓰세요.",
      };
    case "codex":
      return {
        where: "~/.codex/config.toml",
        code: `[mcp_servers.career-brain]\nurl = "${url}"\nbearer_token_env_var = "CAREER_API_TOKEN"`,
        note: "Codex를 띄우는 셸에서 export CAREER_API_TOKEN=<토큰>. 토큰을 파일에 적지 않습니다.",
      };
    case "claude-desktop":
      return {
        where: "claude_desktop_config.json (stdio)",
        code: JSON.stringify(
          {
            mcpServers: {
              "career-brain": {
                command: "uv",
                args: ["run", "--directory", "/path/to/career-brain", "personal-api", "mcp"],
                env: { CAREER_API_TOKEN: "<토큰>" },
              },
            },
          },
          null,
          2,
        ),
        note: "stdio는 API 서버를 거치지 않고 DB에 직접 붙습니다 (저장소 폴더의 .env를 읽음). /path/to/career-brain을 이 저장소 경로로 바꾸세요. 백그라운드 작업은 personal-api serve 또는 worker가 돌아야 진행됩니다.",
      };
    case "cursor":
      return {
        where: "~/.cursor/mcp.json (또는 프로젝트의 .cursor/mcp.json)",
        code: JSON.stringify(
          { mcpServers: { "career-brain": { url, headers: { Authorization: "Bearer <토큰>" } } } },
          null,
          2,
        ),
      };
  }
}

/** MCP·에이전트 탭: 붙는 방법, 에이전트용 토큰, 도구 권한, 호출 기록, 원격 연결(OAuth) */
export function McpTab({ isAdmin }: { isAdmin: boolean }) {
  const [client, setClient] = useState<Client>("claude-code");
  const url = mcpUrl();
  const s = snippet(client, url);
  return (
    <div className="max-w-3xl space-y-10">
      <Section title="이 서버에 붙이기">
        <dl className="divide-y divide-line border-y border-line text-[13px]">
          <div className="flex flex-col gap-1 py-2.5 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
            <dt className="shrink-0 text-muted">MCP 주소 (HTTP)</dt>
            <dd className="flex min-w-0 items-center gap-1">
              <code className="min-w-0 font-mono text-xs break-all">{url}</code>
              <CopyButton text={url} />
            </dd>
          </div>
        </dl>
        {import.meta.env.DEV ? (
          <p className="text-[13px] text-muted">
            개발 서버로 열었습니다. 개발 서버를 끄면 이 주소도 닫히니, 에이전트에는 API 서버 주소(기본{" "}
            <code>http://127.0.0.1:8000/api/core/mcp</code>)를 쓰세요.
          </p>
        ) : null}
        <div
          role="radiogroup"
          aria-label="클라이언트"
          className="flex gap-0.5 overflow-x-auto rounded-md border border-line p-0.5 [scrollbar-width:none] sm:inline-flex"
        >
          {CLIENTS.map((c) => (
            <button
              key={c.value}
              type="button"
              role="radio"
              aria-checked={client === c.value}
              onClick={() => setClient(c.value)}
              className={cn(
                "h-9 shrink-0 rounded px-3 text-[13px] whitespace-nowrap transition-colors duration-150",
                client === c.value ? "bg-accent font-medium text-accent-fg" : "text-muted hover:text-fg",
              )}
            >
              {c.label}
            </button>
          ))}
        </div>
        <div className="min-w-0 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <p className="min-w-0 text-[13px] text-muted">{s.where}</p>
            <CopyButton text={s.code} />
          </div>
          <pre className="max-w-full overflow-x-auto rounded-md bg-subtle px-3 py-2.5 font-mono text-xs leading-5">
            {s.code}
          </pre>
          {s.note ? <p className="text-[13px] text-muted">{s.note}</p> : null}
        </div>
      </Section>

      <Section title="에이전트용 토큰">
        <p className="text-[13px] text-muted">
          위 조각의 <code>&lt;토큰&gt;</code> 자리에 넣을 API 토큰입니다. 에이전트마다 따로 만들고 만료를 두면, 그 에이전트만
          끊을 수 있습니다. 목록·연장·폐기는{" "}
          <Link to="/manage" search={{ tab: "tokens" }} className="underline underline-offset-2 hover:text-fg">
            토큰 탭
          </Link>
          에서 합니다.
        </p>
        {isAdmin ? (
          <NewTokenForm defaultLabel="MCP" defaultExpires="90" heading="에이전트용 토큰 만들기" />
        ) : (
          <p className="border-t border-line pt-3 text-sm text-muted">토큰은 공간 소유자가 만듭니다. 소유자에게 부탁하세요.</p>
        )}
      </Section>

      <Section title="도구와 권한">
        <ul className="space-y-1.5 border-y border-line py-3 text-[13px]">
          <li>도구는 업무 단위로만 열고, 웹과 같은 권한 정책·같은 작업 공간을 씁니다. 토큰이 공간을 정합니다.</li>
          <li className="font-medium">
            에이전트는 승인하지 못합니다. 승인 결정·주장 확정·승인 없는 제출 도구가 없고, 에이전트가 만든 주장은 확인
            대기로 들어갑니다.
          </li>
          <li className="text-muted">
            모델 연결·키는 MCP 도구로 열지 않습니다. 공고·문서 본문은 자료로만 다루고, 그 안의 지시를 따르지 않습니다.
          </li>
        </ul>
        <p className="text-[13px] text-muted">
          도구별로 필요한 권한은{" "}
          <a href={`${DOCS}#도구`} target="_blank" rel="noreferrer" className="underline underline-offset-2 hover:text-fg">
            MCP 문서의 도구 표
          </a>
          에 있습니다.
        </p>
      </Section>

      <Section title="MCP 호출 기록">
        <AuditSection />
      </Section>

      <div className="space-y-6 border-t border-line pt-8">
        <h2 className="text-base font-semibold">원격 연결 (OAuth)</h2>
        <OAuthTab />
      </div>
    </div>
  );
}
