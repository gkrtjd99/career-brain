import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type Plugin } from "vite";

// @types/node 없이 쓰는 최소 선언 (설정 파일은 Node에서 돈다)
declare const process: { env: Record<string, string | undefined>; cwd(): string };

/**
 * 이 기기에서 글자 읽기(OCR, src/lib/ocr.ts)에 쓰는 파일을 이 앱이 직접 내준다 — 실행 중에 CDN에서 받지 않는다.
 * node_modules에서 그대로 가져와 빌드 결과의 `ocr/`에 넣고, 개발 서버에서는 같은 주소로 내준다.
 * 레포에는 넣지 않는다 (npm 패키지: tesseract.js·tesseract.js-core Apache-2.0, @tesseract.js-data MIT).
 * 처음 사진을 읽을 때만 받는다: 엔진 하나(~3.9MB, gzip ~1.5MB) + 한국어(1.6MB)·영어(3.0MB) 학습 데이터.
 */
const OCR_FILES: Record<string, string> = {
  "ocr/worker.min.js": "tesseract.js/dist/worker.min.js",
  "ocr/core/tesseract-core-simd-lstm.wasm.js": "tesseract.js-core/tesseract-core-simd-lstm.wasm.js",
  "ocr/core/tesseract-core-lstm.wasm.js": "tesseract.js-core/tesseract-core-lstm.wasm.js",
  "ocr/lang/kor.traineddata.gz": "@tesseract.js-data/kor/4.0.0_best_int/kor.traineddata.gz",
  "ocr/lang/eng.traineddata.gz": "@tesseract.js-data/eng/4.0.0_best_int/eng.traineddata.gz",
};

type Fs = {
  readFile(path: string): Promise<Uint8Array>;
  access(path: string): Promise<void>;
};

async function fs(): Promise<Fs> {
  const name = "node:fs/promises";
  return (await import(/* @vite-ignore */ name)) as Fs;
}

/** 작업 공간(npm workspaces)이라 node_modules가 위 폴더에 있을 수 있다 */
async function locate(spec: string): Promise<string> {
  const f = await fs();
  let dir = process.cwd();
  for (let i = 0; i < 6; i++) {
    const path = `${dir}/node_modules/${spec}`;
    try {
      await f.access(path);
      return path;
    } catch {
      dir = `${dir}/..`;
    }
  }
  throw new Error(`OCR asset not found: ${spec} (npm install)`);
}

function ocrAssets(): Plugin {
  return {
    name: "career-ocr-assets",
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const path = ((req as unknown as { url?: string }).url ?? "").split("?")[0]!.replace(/^\//, "");
        const spec = OCR_FILES[path];
        if (!spec) return next();
        try {
          const data = await (await fs()).readFile(await locate(spec));
          // .gz는 압축된 그대로 (tesseract.js가 푼다). Content-Encoding을 붙이지 않는다
          res.setHeader("Content-Type", path.endsWith(".js") ? "text/javascript" : "application/octet-stream");
          res.end(data);
        } catch (err) {
          next(err);
        }
      });
    },
    async generateBundle() {
      const f = await fs();
      for (const [fileName, spec] of Object.entries(OCR_FILES)) {
        this.emitFile({ type: "asset", fileName, source: await f.readFile(await locate(spec)) });
      }
    },
  };
}

// 개발 서버는 /api 를 personal-api 로 넘긴다. 다른 주소면 CAREER_API_PROXY 로 바꾼다.
//   CAREER_API_PROXY=http://127.0.0.1:8765 npm run dev
export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), ""), ...process.env };
  const target = env.CAREER_API_PROXY || "http://127.0.0.1:8000";
  return {
    plugins: [react(), tailwindcss(), ocrAssets()],
    server: {
      proxy: { "/api": { target, changeOrigin: false } },
    },
    build: {
      outDir: "dist",
      // Pretendard 동적 subset(woff2 92개)은 브라우저가 필요한 글자 범위만 받는다.
      assetsInlineLimit: 0,
    },
  };
});
