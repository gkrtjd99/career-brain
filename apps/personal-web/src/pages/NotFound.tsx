import { Link } from "@tanstack/react-router";

export function NotFoundPage() {
  return (
    <div className="pt-6">
      <h1 className="text-xl leading-8 font-semibold">없는 화면입니다</h1>
      <p className="mt-1 text-sm text-muted">
        주소를 확인하거나{" "}
        <Link to="/" className="text-fg underline underline-offset-2 hover:no-underline">
          오늘 화면으로 돌아가세요
        </Link>
        .
      </p>
    </div>
  );
}
