import { cn, Spinner, ViewTabs } from "@career-brain/ui";
import type { UseQueryResult } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronLeft } from "lucide-react";
import type { ReactNode } from "react";
import { ApiError } from "../lib/api";
import { ERROR_TEXT } from "../lib/labels";

export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "토큰이 없거나 맞지 않습니다. 설정에서 토큰을 확인하세요.";
    if (error.code && ERROR_TEXT[error.code]) return ERROR_TEXT[error.code]!;
    if (error.status === 422 && Array.isArray(error.detail)) return "입력값을 확인하세요.";
    if (error.code) return `${error.code} (${error.status})`;
    return `요청 실패 (${error.status})`;
  }
  if (error instanceof TypeError) return "API에 연결할 수 없습니다. 서버가 켜져 있는지 확인하세요.";
  return error instanceof Error ? error.message : String(error);
}

export function ErrorText({ error, className }: { error: unknown; className?: string }) {
  if (!error) return null;
  return (
    <p role="alert" className={cn("text-[13px] text-danger", className)}>
      {errorText(error)}
    </p>
  );
}

/** 쿼리 로딩·오류 처리 후 데이터가 있을 때만 children 을 그린다. */
export function Loaded<T>({
  query,
  children,
}: {
  query: UseQueryResult<T>;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending)
    return (
      <div className="flex items-center gap-2 py-6 text-muted">
        <Spinner /> 불러오는 중
      </div>
    );
  if (query.isError) return <ErrorText error={query.error} className="py-4" />;
  return <>{children(query.data)}</>;
}

export function PageHeader({
  title,
  subtitle,
  back,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  back?: { to: string; label: string; search?: Record<string, string> };
  actions?: ReactNode;
}) {
  return (
    <header className="mb-8 space-y-1">
      {back ? (
        <Link
          to={back.to}
          search={back.search}
          className="-ml-1 inline-flex min-h-9 items-center gap-0.5 rounded-md pr-2 text-[13px] text-muted hover:text-fg sm:min-h-7"
        >
          <ChevronLeft size={16} />
          {back.label}
        </Link>
      ) : null}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl leading-8 font-semibold">{title}</h1>
          {subtitle ? <div className="text-[13px] text-muted">{subtitle}</div> : null}
        </div>
        {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
      </div>
    </header>
  );
}

/** 보기 바꾸기 밑줄 탭. 모양은 packages/ui 의 ViewTabs 하나로 맞춘다. */
export function Tabs<T extends string>({
  value,
  onChange,
  items,
  label,
}: {
  value: T;
  onChange: (v: T) => void;
  items: { value: T; label: ReactNode }[];
  label?: string;
}) {
  return <ViewTabs value={value} onChange={onChange} items={items} label={label} className="mb-4" />;
}

/** 목록: 상자 없이 위 선과 행 사이 얇은 선으로만 나눈다. */
export function List({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <ul className={cn("divide-y divide-line border-y border-line", className)}>
      {children}
    </ul>
  );
}
