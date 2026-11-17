// 연결·관리 탭들이 같이 쓰는 작은 조각. 상자 없이 얇은 선과 여백으로만 나눈다.
import { Button, toast } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { type ReactNode } from "react";
import { ApiError, api } from "../../lib/api";
import { errorText } from "../common";

export function Rows({ children }: { children: ReactNode }) {
  return <ul className="divide-y divide-line border-y border-line">{children}</ul>;
}

/** 비었을 때 한 줄: 무엇을 하면 채워지는지 알려 준다 */
export function Quiet({ children }: { children: ReactNode }) {
  return <p className="border-t border-line pt-3 text-sm text-muted">{children}</p>;
}

/** 모델 연결 API 오류. 옛 서버(경로 없음)는 404로 온다 */
export function modelsErrorText(error: unknown) {
  if (error instanceof ApiError && error.status === 404 && !error.code?.startsWith("connection"))
    return "이 서버에는 아직 모델 연결 API가 없습니다. 서버를 새 판으로 올리세요 (그동안은 models.toml로 설정합니다).";
  return errorText(error);
}

/** 연결 시험 결과 캐시. 시험(과금 없는 GET /models)을 누를 때만 부르고, 용도별 모델 고르기가 같은 값을 쓴다 */
export function useConnectionTest(name: string | null) {
  return useQuery({
    queryKey: ["model-test", name],
    queryFn: () => api.testModelConnection(name!),
    enabled: false,
    retry: false,
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

/** 글을 복사하는 작은 버튼 */
export function CopyButton({ text, label = "복사" }: { text: string; label?: string }) {
  return (
    <Button
      size="sm"
      variant="ghost"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => toast("복사했습니다."));
      }}
    >
      {label}
    </Button>
  );
}
