import { Section } from "@career-brain/ui";
import { Send } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { bookmarkletHref } from "../../lib/capture";
import { CopyButton } from "./parts";

/**
 * 보고 있는 페이지 보내기 (c0043): 북마크 막대로 끌어다 두는 "브레인으로 보내기".
 * 주소는 이 앱의 출처로 만든다. React는 `javascript:` 주소를 막으므로 그려진 뒤에 붙인다.
 */
export function CaptureSection() {
  const href = useMemo(() => bookmarkletHref(window.location.origin), []);
  const link = useRef<HTMLAnchorElement>(null);
  const [hint, setHint] = useState(false);
  useEffect(() => {
    link.current?.setAttribute("href", href);
  }, [href]);

  return (
    <Section title="브라우저에서 보내기">
      <div className="space-y-4 border-t border-line pt-4">
        <p className="text-[13px] leading-6 text-muted">
          로그인해야 보이는 공고를 이 브레인으로 보냅니다. 아래 버튼을 북마크 막대로 끌어다 두고, 보낼 페이지에서
          누릅니다.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <a
            ref={link}
            draggable
            onClick={(e) => {
              // 여기서 누르면 이 화면을 보내게 된다: 끌어다 두라고만 알린다
              e.preventDefault();
              setHint(true);
            }}
            className="inline-flex h-9 cursor-grab items-center gap-1.5 rounded-md border border-line-strong bg-surface px-3 text-sm font-medium active:cursor-grabbing"
          >
            <Send size={15} aria-hidden />
            브레인으로 보내기
          </a>
          <CopyButton text={href} label="북마크 주소 복사" />
        </div>
        {hint ? (
          <p role="status" className="text-[13px]">
            누르지 말고 북마크 막대로 끌어다 놓습니다. 끌 수 없으면 주소를 복사해 새 북마크의 주소 칸에 붙여넣습니다.
          </p>
        ) : null}
        <ul className="space-y-1 text-[13px] leading-6 text-muted">
          <li>새 창에서 제목·본문을 미리 보고, 공고나 참고 자료·내 경험 자료로 고른 뒤 저장할 때만 넣습니다.</li>
          <li>그 사이트에는 토큰을 보내지 않습니다. 이 서버도 그 주소에 따로 접속하지 않습니다.</li>
          <li>휴대폰(안드로이드 Chrome)은 이 앱을 홈 화면에 추가한 뒤 공유 메뉴에서 Career Brain을 고릅니다.</li>
        </ul>
      </div>
    </Section>
  );
}
