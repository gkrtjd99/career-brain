import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// @types/node 없이 쓰는 최소 선언 (설정 파일은 Node에서 돈다)
declare const process: { env: Record<string, string | undefined>; cwd(): string };

// 개발 서버는 /api 를 personal-api 로 넘긴다. 다른 주소면 CAREER_API_PROXY 로 바꾼다.
//   CAREER_API_PROXY=http://127.0.0.1:8765 npm run dev
export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), ""), ...process.env };
  const target = env.CAREER_API_PROXY || "http://127.0.0.1:8000";
  return {
    plugins: [react(), tailwindcss()],
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
