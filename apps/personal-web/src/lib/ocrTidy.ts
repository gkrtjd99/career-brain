// OCR 줄 정리 (이 기기에서 읽은 사진·스캔). 순수 함수라 따로 둔다.
// 증빙 제목처럼 글자 사이를 띄어 쓴 줄("자 격 증")은 글자 하나짜리 조각 줄로 갈라져 쓸모없는 블록이 된다.

export interface OcrLine {
  page?: number | null;
  text: string;
  bbox?: [number, number, number, number] | null;
}

const SYLLABLE = /^[가-힣]$/;

/** 글자마다 띄어 쓴 한글("자 격 증 명")을 붙인다. 줄 전체가 그렇거나, 한 글자 낱말이 넷 이상 이어질 때만. */
export function collapseSpacedHangul(text: string): string {
  const tokens = text.split(" ");
  if (tokens.length >= 2 && tokens.every((t) => SYLLABLE.test(t))) return tokens.join("");
  const out: string[] = [];
  let run: string[] = [];
  const flush = () => {
    if (run.length >= 4) out.push(run.join(""));
    else out.push(...run);
    run = [];
  };
  for (const t of tokens) {
    if (SYLLABLE.test(t)) run.push(t);
    else {
      flush();
      out.push(t);
    }
  }
  flush();
  return out.join(" ");
}

const isFragment = (text: string) => /^[가-힣]{1,2}$/.test(text.replace(/\s/g, ""));

function sameRow(a: OcrLine, b: OcrLine): boolean {
  if (!a.bbox || !b.bbox || a.page !== b.page) return false;
  const [ax0, ay0, ax1, ay1] = a.bbox;
  const [bx0, by0, , by1] = b.bbox;
  const overlap = Math.min(ay1, by1) - Math.max(ay0, by0);
  const h = Math.min(ay1 - ay0, by1 - by0);
  // 같은 높이에 있고, 뒤 조각이 앞 조각 오른쪽에서 글자 몇 개 너비 안에 있다
  return h > 0 && overlap >= h * 0.5 && bx0 >= ax0 && bx0 - ax1 < (ay1 - ay0) * 4;
}

/**
 * 같은 줄 높이에 놓인 한두 글자 조각을 옆 줄과 합치고, 글자마다 띄어 쓴 한글을 붙인다.
 * 표처럼 같은 높이에 놓인 긴 항목 두 개("자격번호 …", "발급일 …")는 합치지 않는다.
 */
export function tidyOcrLines<T extends OcrLine>(lines: T[]): T[] {
  const out: T[] = [];
  for (const line of lines) {
    const prev = out.at(-1);
    if (prev && sameRow(prev, line) && (isFragment(prev.text) || isFragment(line.text))) {
      const [x0, y0, x1, y1] = prev.bbox!;
      const [bx0, by0, bx1, by1] = line.bbox!;
      out[out.length - 1] = {
        ...prev,
        text: `${prev.text} ${line.text}`,
        bbox: [Math.min(x0, bx0), Math.min(y0, by0), Math.max(x1, bx1), Math.max(y1, by1)],
      };
      continue;
    }
    out.push({ ...line });
  }
  return out.map((l) => ({ ...l, text: collapseSpacedHangul(l.text) })).filter((l) => l.text.trim());
}
