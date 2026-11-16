"""경험 위키: 확정된 주장만 모아 읽는 페이지.

위키는 저장소가 아니라 보기다. 주장·근거 테이블에서 매번 만들고, 따로 쓰지 않는다.
후보(extracted)·이의(disputed)·폐기(retired) 주장은 본문에 넣지 않고 "확인 대기" 수만 보여준다.
"""

from __future__ import annotations

from dataclasses import dataclass

from career_core.db import Database
from career_core.domain.brain import CONFIRMED_STATUSES, ClaimKind, ClaimStatus
from career_core.ports import ActorContext
from career_core.usecases.brain import (
    ClaimView,
    ExperienceSummary,
    get_experience,
    list_experiences,
)

KIND_TITLES: dict[str, str] = {
    ClaimKind.RESPONSIBILITY: "맡은 일",
    ClaimKind.ACTION: "한 일",
    ClaimKind.RESULT: "결과",
    ClaimKind.DECISION: "판단",
    ClaimKind.DIFFICULTY: "어려움과 대응",
    ClaimKind.SKILL: "기술",
    ClaimKind.OTHER: "기타",
}
CONTRIBUTION_LABELS = {"personal": "본인", "team": "팀"}


@dataclass(frozen=True, slots=True)
class WikiSection:
    kind: str
    title: str
    claims: list[ClaimView]


@dataclass(frozen=True, slots=True)
class WikiPage:
    experience_id: object
    title: str
    organization: str | None
    role: str | None
    period_text: str | None
    sections: list[WikiSection]
    confirmed: int
    pending: int


async def wiki_index(db: Database, actor: ActorContext) -> list[ExperienceSummary]:
    """폐기되지 않은 경험 목록. 확정 주장 수·확인 대기 수는 summary의 claim_counts로 본다."""
    return await list_experiences(db, actor)


async def wiki_page(db: Database, actor: ActorContext, experience_id) -> WikiPage:
    view = await get_experience(db, actor, experience_id)
    confirmed = [c for c in view.claims if c.status in CONFIRMED_STATUSES]
    pending = sum(1 for c in view.claims if c.status == ClaimStatus.EXTRACTED)
    sections = []
    for kind, title in KIND_TITLES.items():
        items = [c for c in confirmed if c.kind == kind]
        if items:
            sections.append(WikiSection(kind, title, items))
    return WikiPage(
        view.id,
        view.title,
        view.organization,
        view.role,
        view.period_text,
        sections,
        len(confirmed),
        pending,
    )


def _md_escape(text: str) -> str:
    return text.replace("\n", " ").strip()


def render_markdown(page: WikiPage) -> str:
    """사람이 읽거나 내보내기 위한 Markdown. 근거는 각주 대신 주장 바로 아래에 인용으로 둔다."""
    head = " · ".join(p for p in (page.organization, page.role, page.period_text) if p)
    lines = [f"# {_md_escape(page.title)}", ""]
    if head:
        lines += [_md_escape(head), ""]
    if page.pending:
        lines += [f"> 확인 대기 주장 {page.pending}건은 이 페이지에 포함되지 않았다.", ""]
    if not page.sections:
        lines += ["확정된 주장이 아직 없다.", ""]
    for section in page.sections:
        lines += [f"## {section.title}", ""]
        for c in section.claims:
            badge = "원문 근거" if c.status == ClaimStatus.EVIDENCE_SUPPORTED else "본인 확인"
            who = CONTRIBUTION_LABELS.get(c.contribution, c.contribution)
            lines.append(f"- {_md_escape(c.statement)} _({who} · {badge})_")
            for ev in c.evidence:
                if ev.kind == "source_span":
                    where = ev.source_title or "원문"
                    if ev.page:
                        where += f" p.{ev.page}"
                    lines.append(f"  - > {_md_escape(ev.quote or '')} — {where}")
                else:
                    lines.append(
                        f"  - 인터뷰: {_md_escape(ev.question or '')} → {_md_escape(ev.answer or '')}"
                    )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
