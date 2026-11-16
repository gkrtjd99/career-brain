import { EmptyState } from "@career-brain/ui";
import { Link } from "@tanstack/react-router";

export function NotFoundPage() {
  return (
    <EmptyState className="mt-10">
      없는 화면입니다.{" "}
      <Link to="/" className="text-accent underline">
        오늘로
      </Link>
    </EmptyState>
  );
}
