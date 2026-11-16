// 저장한 테마를 첫 그리기 전에 적용한다 (깜빡임 방지). 외부 파일이라 CSP script-src 'self'와 맞는다.
try {
  var t = localStorage.getItem("career-brain:theme");
  var dark = t === "dark" || (t !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
} catch (e) {}
