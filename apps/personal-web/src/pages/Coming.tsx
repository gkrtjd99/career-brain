import { Badge, Card } from "@career-brain/ui";
import { PageHeader } from "../components/common";

export function ComingPage({
  title,
  milestone,
  lines,
}: {
  title: string;
  milestone: string;
  lines: string[];
}) {
  return (
    <div className="max-w-xl">
      <PageHeader title={title} subtitle={<Badge tone="none">준비 중 · {milestone}</Badge>} />
      <Card className="p-4">
        <p className="mb-2 text-sm">이 화면은 다음 단계에서 들어옵니다.</p>
        <ul className="list-disc space-y-1 pl-5 text-[13px] text-muted">
          {lines.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
