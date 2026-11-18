/**
 * 폴더째 넣기: 끌어다 놓은 폴더(또는 폴더 고르기)를 끝까지 내려가며 파일을 모은다.
 *
 * 모아 둔 자료는 보통 폴더에 있다 ("자소서/2024 상반기/…"). 시스템·임시 파일(.DS_Store, ~$로 시작하는 오피스
 * 잠금 파일, ._ 리소스 포크, 숨김 파일)과 읽지 못하는 형식은 넣지 않고 세기만 한다. 같은 파일은 서버가 내용
 * 해시로 한 번만 받는다. 한 번에 ``MAX_FOLDER_FILES``개까지.
 */

export const MAX_FOLDER_FILES = 300;

const READABLE = new Set(["pdf", "docx", "pptx", "xlsx", "hwpx", "hwp", "png", "jpg", "jpeg", "txt", "md", "html", "htm"]);
const JUNK = /^(?:\.|~\$|\._|thumbs\.db$|desktop\.ini$)/i;

export interface Collected {
  files: File[];
  /** 읽지 못하는 형식이라 뺀 파일 수 (시스템·임시 파일은 세지 않는다) */
  skipped: number;
  /** ``MAX_FOLDER_FILES``를 넘어 뺀 파일 수 */
  overflow: number;
}

export function isReadable(name: string) {
  const ext = name.toLowerCase().split(".").pop() ?? "";
  return name.includes(".") && READABLE.has(ext);
}

export function isJunk(name: string) {
  return JUNK.test(name);
}

/** 고른 파일 목록(폴더 고르기 포함)을 거른다 */
export function collect(files: Iterable<File>): Collected {
  const out: File[] = [];
  let skipped = 0;
  let overflow = 0;
  for (const f of files) {
    const path = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
    // 경로 중 숨김 폴더(.git 등) 아래는 통째로 뺀다
    if (path.split("/").some(isJunk)) continue;
    if (!isReadable(f.name)) {
      skipped += 1;
      continue;
    }
    if (out.length >= MAX_FOLDER_FILES) {
      overflow += 1;
      continue;
    }
    out.push(f);
  }
  return { files: out, skipped, overflow };
}

type Entry = FileSystemEntry;

function readAll(dir: FileSystemDirectoryEntry): Promise<Entry[]> {
  const reader = dir.createReader();
  const all: Entry[] = [];
  return new Promise((resolve) => {
    // readEntries는 한 번에 100개 안팎만 준다: 빈 배열이 올 때까지 다시 부른다
    const next = () =>
      reader.readEntries(
        (batch) => {
          if (!batch.length) return resolve(all);
          all.push(...batch);
          next();
        },
        () => resolve(all),
      );
    next();
  });
}

function fileOf(entry: FileSystemFileEntry): Promise<File | null> {
  return new Promise((resolve) => entry.file(resolve, () => resolve(null)));
}

/** 끌어다 놓은 것에서 파일을 모은다. 폴더가 없으면 ``dataTransfer.files`` 그대로 (거르기만). */
export async function collectDropped(dt: DataTransfer): Promise<Collected> {
  const entries = Array.from(dt.items ?? [])
    .filter((it) => it.kind === "file")
    .map((it) => it.webkitGetAsEntry?.() ?? null);
  if (!entries.some((e) => e?.isDirectory)) return collect(Array.from(dt.files));
  const found: File[] = [];
  const walk = async (entry: Entry | null, depth: number): Promise<void> => {
    if (!entry || isJunk(entry.name) || found.length > MAX_FOLDER_FILES * 2 || depth > 12) return;
    if (entry.isFile) {
      const f = await fileOf(entry as FileSystemFileEntry);
      if (f) found.push(f);
    } else if (entry.isDirectory) {
      for (const child of await readAll(entry as FileSystemDirectoryEntry)) await walk(child, depth + 1);
    }
  };
  for (const e of entries) await walk(e, 0);
  return collect(found);
}
