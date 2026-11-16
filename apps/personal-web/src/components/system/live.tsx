import { ApiError, type LiveEvent } from "@career-brain/client";
import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../../lib/api";

// 실행·승인 실시간 갱신 (GET /core/live, SSE). 알림에는 ID가 없으므로 받으면 해당 목록을 다시 읽는다.
// 연결돼 있는 동안 실행 목록의 몇 초 간격 다시 읽기를 끈다. 끊기면(LISTEN 불가·프록시·오류) 예전처럼
// 다시 읽기로 돌아가고, 뒤에서 점점 긴 간격으로 다시 붙어 본다.
// 시스템 화면 전체, 일괄 실행 진행 카드, 오늘 화면 승인 요청 카드가 쓴다. 안쪽 LiveProvider는 바깥 연결을
// 그대로 쓴다 (화면 하나에 연결 하나).

// null: 바깥에 LiveProvider가 없다
const LiveContext = createContext<boolean | null>(null);

/** 실시간 알림이 연결돼 있으면 true. false면 각 화면이 몇 초 간격으로 다시 읽는다. */
export function useLiveConnected() {
  return useContext(LiveContext) === true;
}

const RUN_KEYS = [["runs"], ["run"], ["run-events"]];
const APPROVAL_KEYS = [["approvals"], ["approval-diff"]];

export function LiveProvider({ children }: { children: ReactNode }) {
  const outer = useContext(LiveContext);
  if (outer !== null) return <>{children}</>;
  return <LiveConnection>{children}</LiveConnection>;
}

function LiveConnection({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const ctl = new AbortController();
    let delay = 1000;
    let stopped = false;
    const refresh = (keys: string[][]) => keys.forEach((queryKey) => void qc.invalidateQueries({ queryKey }));
    const onEvent = (e: LiveEvent) => {
      if (e.type === "ready") {
        setConnected(true);
        delay = 1000;
        // 끊겨 있던 동안 놓친 변경 대신 한 번 다시 읽는다
        refresh([...RUN_KEYS, ...APPROVAL_KEYS]);
      } else if (e.type === "runs") refresh(RUN_KEYS);
      else if (e.type === "approvals") refresh(APPROVAL_KEYS);
      else if (e.type === "unauthorized") stopped = true;
    };
    void (async () => {
      while (!ctl.signal.aborted && !stopped) {
        try {
          await api.liveStream(onEvent, ctl.signal);
        } catch (error) {
          if (ctl.signal.aborted) break;
          // 인증·권한 오류는 다시 붙어도 같다: 다시 읽기로만 간다
          if (error instanceof ApiError && error.status >= 400 && error.status < 500) stopped = true;
        }
        setConnected(false);
        if (ctl.signal.aborted || stopped) break;
        await new Promise((r) => setTimeout(r, delay));
        delay = Math.min(delay * 2, 30_000);
      }
    })();
    return () => {
      ctl.abort();
      setConnected(false);
    };
  }, [qc]);

  return <LiveContext.Provider value={connected}>{children}</LiveContext.Provider>;
}
