import { Button } from "@career-brain/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api } from "../../lib/api";
import { ErrorText } from "../common";
import { OAuthTab } from "../system/OAuthTab";
import { CopyButton } from "./parts";

const API_BASE: string = import.meta.env.VITE_API_BASE ?? "/api";
const TOKEN_PLACEHOLDER = "<토큰>";

/** 지금 화면이 붙은 API 주소 + /core/mcp. VITE_API_BASE가 절대 주소면 그대로 쓴다 */
export function mcpUrl() {
  const base = /^https?:\/\//.test(API_BASE) ? API_BASE : `${window.location.origin}${API_BASE}`;
  return `${base.replace(/\/$/, "")}/core/mcp`;
}

/** 할 수 있는 일을 묶음으로. 도구 이름 대신 사람이 읽는 말로 */
const SCOPES: { name: string; text: string }[] = [
  { name: "읽기", text: "브레인 검색, 경험·확인한 사실·근거, 공고, 지원 서류 검사, 학습 충족도, 작업·승인 상태" },
  { name: "쓰기", text: "인터뷰 시작·답변, 공고 링크 저장, 학습 기록, 작업 시작·중단, 승인 요청" },
  { name: "외부 모델 사용", text: "문서 수정안 제안. 모델 연결에서 허용한 자료만 보냅니다" },
  { name: "할 수 없는 것", text: "승인 결정, 사실 확정, 제출, 모델 연결·키 보기. 에이전트가 만든 사실은 확인 대기로 갑니다" },
];

/** MCP 탭: 주소 → 토큰 → 클라이언트별 설정 → 권한. working.cv/mcp처럼 한 줄로 읽히게 */
export function McpTab({ isAdmin }: { isAdmin: boolean }) {
  const url = mcpUrl();
  const [token, setToken] = useState<string | null>(null);
  const t = token ?? TOKEN_PLACEHOLDER;

  return (
    <div className="max-w-2xl space-y-10">
      <p className="text-[13px] text-muted">
        Claude Code·Codex 같은 MCP 클라이언트를 연결하면 에이전트가 이 브레인을 조회하고 초안을 만들 수 있습니다.
      </p>

      <Block title="MCP 서버">
        <Code text={url} />
        {import.meta.env.DEV ? (
          <p className="text-[13px] text-muted">
            개발 서버 주소입니다. 개발 서버를 끄면 닫히니, 에이전트에는 API 서버 주소(기본{" "}
            <code>http://127.0.0.1:8000/api/core/mcp</code>)를 쓰세요.
          </p>
        ) : null}
      </Block>

      <Block title="토큰">
        {isAdmin ? <TokenStep token={token} onIssued={setToken} /> : (
          <p className="text-[13px] text-muted">토큰은 공간 소유자가 만듭니다.</p>
        )}
      </Block>

      <Block title="설정 예시">
        <Example name="Claude Code" where="터미널에서 한 번">
          <Code
            text={`claude mcp add --transport http career-brain ${url} \\\n  --header "Authorization: Bearer ${t}"`}
          />
        </Example>
        <Example name="Codex" where="~/.codex/config.toml">
          <Code text={`[mcp_servers.career-brain]\nurl = "${url}"\nbearer_token_env_var = "CAREER_API_TOKEN"`} />
          <Code text={`export CAREER_API_TOKEN=${t}`} />
        </Example>
        <details className="group">
          <Summary>다른 클라이언트 (Cursor, Claude Desktop)</Summary>
          <div className="mt-4 space-y-6">
            <Example name="Cursor" where="~/.cursor/mcp.json">
              <Code
                text={JSON.stringify(
                  { mcpServers: { "career-brain": { url, headers: { Authorization: `Bearer ${t}` } } } },
                  null,
                  2,
                )}
              />
            </Example>
            <Example name="Claude Desktop" where="claude_desktop_config.json · 서버 없이 DB에 직접 연결">
              <Code
                text={JSON.stringify(
                  {
                    mcpServers: {
                      "career-brain": {
                        command: "uv",
                        args: ["run", "--directory", "/path/to/career-brain", "personal-api", "mcp"],
                        env: { CAREER_API_TOKEN: t },
                      },
                    },
                  },
                  null,
                  2,
                )}
              />
              <p className="text-[13px] text-muted">/path/to/career-brain을 이 저장소 경로로 바꾸세요.</p>
            </Example>
          </div>
        </details>
      </Block>

      <Block title="권한">
        <dl className="divide-y divide-line border-y border-line text-[13px]">
          {SCOPES.map((s) => (
            <div key={s.name} className="grid gap-0.5 py-2.5 sm:grid-cols-[8rem_minmax(0,1fr)] sm:gap-4">
              <dt className="font-medium">{s.name}</dt>
              <dd className="text-muted">{s.text}</dd>
            </div>
          ))}
        </dl>
        <p className="text-[13px] text-muted">
          에이전트는 토큰이 속한 공간만 봅니다. 토큰을 폐기하면 바로 연결이 해제되고, 호출 기록은{" "}
          <Link to="/manage" search={{ tab: "runs" }} className="underline underline-offset-2 hover:text-fg">
            작업·승인
          </Link>
          에서 봅니다.
        </p>
      </Block>

      {isAdmin ? (
        <details className="group border-t border-line pt-6">
          <Summary>원격 연결 (OAuth) — 토큰 대신 로그인으로 붙는 클라이언트용</Summary>
          <div className="mt-6">
            <OAuthTab />
          </div>
        </details>
      ) : null}
    </div>
  );
}

function Summary({ children }: { children: ReactNode }) {
  return (
    <summary className="inline-flex min-h-9 cursor-pointer list-none items-center gap-1 text-[13px] text-muted hover:text-fg sm:min-h-6 [&::-webkit-details-marker]:hidden">
      <ChevronRight size={14} aria-hidden className="transition-transform duration-150 group-open:rotate-90" />
      {children}
    </summary>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Example({ name, where, children }: { name: string; where: string; children: ReactNode }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-2 text-[13px]">
        <span className="font-medium">{name}</span>
        <span className="text-muted">{where}</span>
      </div>
      {children}
    </div>
  );
}

/** 회색 바탕 코드 한 덩이 + 오른쪽 위 복사 */
function Code({ text }: { text: string }) {
  return (
    <div className="relative">
      <pre className="max-w-full overflow-x-auto rounded-md bg-subtle py-2.5 pr-16 pl-3 font-mono text-xs leading-5 whitespace-pre">
        {text}
      </pre>
      <div className="absolute top-1 right-1 rounded-md bg-subtle">
        <CopyButton text={text} />
      </div>
    </div>
  );
}

/** MCP용 토큰을 만들면 아래 예시에 바로 채운다. 값은 이 화면 메모리에만, 한 번만 보인다 */
function TokenStep({ token, onIssued }: { token: string | null; onIssued: (t: string) => void }) {
  const qc = useQueryClient();
  const issue = useMutation({
    mutationFn: () => api.issueApiToken({ label: "MCP", expires_days: 90 }),
    onSuccess: (res) => {
      onIssued(res.secret);
      void qc.invalidateQueries({ queryKey: ["api-tokens"] });
    },
  });
  if (token)
    return (
      <p className="text-[13px] font-medium">
        토큰을 만들어 아래 예시에 넣었습니다. 이 화면을 떠나면 다시 볼 수 없으니 지금 복사하세요.
      </p>
    );
  return (
    <div className="space-y-2">
      <p className="text-[13px] text-muted">
        에이전트용 토큰을 만들면 아래 예시의 {TOKEN_PLACEHOLDER} 자리에 채워집니다. 90일 뒤 만료되고, 목록·폐기는{" "}
        <Link to="/manage" search={{ tab: "tokens" }} className="underline underline-offset-2 hover:text-fg">
          토큰
        </Link>
        에서 합니다.
      </p>
      <Button size="sm" variant="primary" disabled={issue.isPending} onClick={() => issue.mutate()}>
        {issue.isPending ? "만드는 중" : "MCP용 토큰 만들기"}
      </Button>
      <ErrorText error={issue.error} />
    </div>
  );
}
