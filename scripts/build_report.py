#!/usr/bin/env python3
"""Build the Korean planning report from the reviewed Markdown documents.

Requires Python with python-docx and pandoc. --pdf also requires soffice.
No application dependencies or source datasets are installed or changed.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit

from docx import Document
from docx.text.run import Run
from docx.text.hyperlink import Hyperlink
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports"
STEM = "jeju-personal-tour-agent-design"
FONT = "Noto Sans CJK KR"
CHAPTERS = [
    ("05-existing-service-review.md", "1. 기존 서비스와 소스 분석"),
    ("01-product-and-architecture.md", "2. 제품 목표와 시스템 설계"),
    ("04-questionnaire-and-scoring.md", "3. 실제 문항·페르소나·추천 점수"),
    ("08-persona-clustering.md", "4. 페르소나 유형·군집 설계와 시각화"),
    ("02-data-plan.md", "5. 제주 데이터 확보와 검증"),
    ("09-ollama-lag-feasibility.md", "6. Ollama·LAG 가능성과 Vercel 배포 설계"),
    ("10-mcp-server-and-vercel-integration.md", "7. MCP 서버 구축과 기존 앱 통합"),
    ("11-multi-provider-tunnels-database-and-cost.md", "8. Claude·OpenAI·다중 터널·DB와 비용"),
    ("12-domain-tunnel-and-ollama-runbook.md", "9. 도메인·관리 MCP·Windows와 macOS 연결"),
    ("13-next-steps-and-mcp-implementation.md", "10. 네임서버 저장 후 단계와 MCP 구현 결과"),
    ("07-implementation-process.md", "11. 구현 절차와 API 계약"),
    ("03-evaluation-and-roadmap.md", "12. 평가와 개발 로드맵"),
    ("06-evidence-and-open-items.md", "13. 참고 근거와 남은 확인"),
    ("14-confirmed-data-catalog.md", "14. CSV 55개 확정 목록과 품질·권리 검토"),
    ("15-huggingface-mcp-setup.md", "15. Hugging Face MCP 연결과 모델 검증"),
    ("16-huggingface-organization-and-ci.md", "16. Hugging Face 조직·계정·CI 운영"),
    ("17-ai-agent-model-registry.md", "17. AI AGENT MODEL 전체 목록과 선정 기준"),
    ("18-claude-code-handoff.md", "18. Claude Code 작업 인수인계와 재개 절차"),
    ("19-agent-step2-data-foundation.md", "19. AI Agent 1·2단계: 진단과 근거 등급 데이터 기반"),
    ("20-langflow-agent-builder.md", "20. Langflow로 Agent 흐름 만들기"),
    ("21-linux-docker-production.md", "21. Linux Docker 운영 배치와 검증 절차"),
    ("22-jailbreak-and-prompt-injection-defense.md", "22. 탈옥·프롬프트 주입 방어와 평가 설계"),
    ("23-agent-step3-evaluation-set.md", "23. AI Agent 3단계: 평가셋 생성기와 정답 규칙"),
]
PAGE_BREAK = '\n```{=openxml}\n<w:p><w:r><w:br w:type="page"/></w:r></w:p>\n```\n'


def set_font(style, size=None, bold=None):
    style.font.name = FONT
    if size is not None:
        style.font.size = Pt(size)
    if bold is not None:
        style.font.bold = bold
    fonts = style.element.get_or_add_rPr().find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        style.element.get_or_add_rPr().append(fonts)
    for key in ("ascii", "hAnsi", "eastAsia", "cs"):
        fonts.set(qn("w:" + key), FONT)


def make_reference(path):
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin, sec.bottom_margin = Cm(2), Cm(1.8)
    sec.left_margin, sec.right_margin = Cm(1.7), Cm(1.7)
    sec.header_distance = sec.footer_distance = Cm(0.8)
    for name in ("Normal", "Body Text", "List Paragraph"):
        style = doc.styles[name]
        set_font(style, 9.5)
        style.paragraph_format.space_after = Pt(5)
        style.paragraph_format.line_spacing = 1.13
    for name, size in (("Title", 26), ("Subtitle", 14), ("Heading 1", 18),
                       ("Heading 2", 13), ("Heading 3", 11), ("Heading 4", 10)):
        style = doc.styles[name]
        set_font(style, size)
        style.font.color.rgb = RGBColor.from_string("163B54")
        style.paragraph_format.keep_with_next = True
        style.paragraph_format.space_before = Pt(14)
        style.paragraph_format.space_after = Pt(7)
    doc.styles["Heading 1"].paragraph_format.page_break_before = True
    header = sec.header.paragraphs[0]
    header.text = "OREUM IT  |  제주 개인화 관광 AI Agent 설계 보고서"
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    set_font(header.style, 8)
    footer = sec.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run("기획 초안 · 2026-11-18  |  ")
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    for run in footer.runs:
        run.font.size = Pt(8)
    doc.save(path)


def source_body(name):
    text = (ROOT / "docs" / name).read_text(encoding="utf-8")
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    out = []
    mermaid = False
    for line in lines:
        if line.strip() == "```mermaid":
            mermaid = True
            out.extend([
                "**공통 처리 흐름**",
                "",
                "설문·대화 → 프로필 검증 → 후보 생성 → 필수 제약 검사 → 선호 랭킹·다양성 → 이동·일정 계산 → 출처·제약 최종 검증 → 설명·수정",
                "",
                "데이터 인덱스는 후보 생성에 근거를 공급하고, 동의 기반 피드백은 다음 추천 개선에 사용한다. LAG 모듈의 연결 위치는 원문 검토 후 확정한다.",
                "",
            ])
            continue
        if mermaid:
            if line.strip() == "```":
                mermaid = False
            continue
        out.append(line)
    def rebase_link(match):
        target = urlsplit(match.group(1))
        if target.scheme or target.netloc or not target.path:
            return match.group(0)
        source = ROOT / "docs" / target.path
        relative = os.path.relpath(source.resolve(), OUT)
        return "](" + urlunsplit(("", "", relative, target.query, target.fragment)) + ")"

    # The compiled Markdown lives in reports/, while its sources live in docs/.
    return re.sub(r"\]\(([^)]+)\)", rebase_link, "\n".join(out).strip())


def polish(path):
    doc = Document(path)
    for section in doc.sections:
        section.page_width, section.page_height = Cm(21), Cm(29.7)
        section.top_margin, section.bottom_margin = Cm(2), Cm(1.8)
        section.left_margin, section.right_margin = Cm(1.7), Cm(1.7)
    for style in doc.styles:
        if style.type == 1:
            set_font(style)
    # Normalize Pandoc tables to complete python-docx cell/width markup.
    # This also avoids blank-grid rendering in the bundled Writer importer.
    for old in list(doc.tables):
        count = len(old.columns)
        ratios = {
            2: [0.30, 0.70], 3: [0.23, 0.37, 0.40],
            4: [0.17, 0.25, 0.28, 0.30],
            5: [0.07, 0.18, 0.27, 0.30, 0.18],
            7: [0.20, 0.20, 0.08, 0.06, 0.06, 0.06, 0.34],
        }.get(count, [1 / count] * count)
        fresh = doc.add_table(rows=len(old.rows), cols=count)
        fresh.autofit = False
        for ci, ratio in enumerate(ratios):
            fresh.columns[ci].width = Cm(17.6 * ratio)
        for source_row, target_row in zip(old.rows, fresh.rows):
            for ci, (source, target) in enumerate(zip(source_row.cells, target_row.cells)):
                target.width = Cm(17.6 * ratios[ci])
                target.text = ""
                for pi, paragraph in enumerate(source.paragraphs):
                    new_p = target.paragraphs[0] if pi == 0 else target.add_paragraph()
                    for item in paragraph.iter_inner_content():
                        if isinstance(item, Run):
                            run = new_p.add_run(item.text)
                            run.bold, run.italic = item.bold, item.italic
                        elif isinstance(item, Hyperlink):
                            link = OxmlElement("w:hyperlink")
                            if item.address:
                                rel = doc.part.relate_to(item.url,
                                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                                    is_external=True)
                                link.set(qn("r:id"), rel)
                            elif item.fragment:
                                link.set(qn("w:anchor"), item.fragment)
                            run = OxmlElement("w:r")
                            props = OxmlElement("w:rPr")
                            color = OxmlElement("w:color")
                            color.set(qn("w:val"), "145D86")
                            props.append(color)
                            run.append(props)
                            text = OxmlElement("w:t")
                            text.text = item.text
                            run.append(text)
                            link.append(run)
                            new_p._p.append(link)
        old._tbl.addprevious(fresh._tbl)
        old._tbl.getparent().remove(old._tbl)
    for table in doc.tables:
        table.style = "Table Grid"
        table.autofit = False
        for ri, row in enumerate(table.rows):
            props = row._tr.get_or_add_trPr()
            if ri == 0:
                repeat = OxmlElement("w:tblHeader")
                props.append(repeat)
            no_split = OxmlElement("w:cantSplit")
            props.append(no_split)
            for cell in row.cells:
                if ri == 0:
                    shade = OxmlElement("w:shd")
                    shade.set(qn("w:fill"), "E4EDF3")
                    cell._tc.get_or_add_tcPr().append(shade)
                for p in cell.paragraphs:
                    p.paragraph_format.space_after = Pt(3)
                    p.paragraph_format.line_spacing = 1.05
                    for run in p.runs:
                        run.font.size = Pt(8.3)
                        if ri == 0:
                            run.bold = True
    for p in doc.paragraphs:
        # Pandoc emits these style IDs without definitions in the reference.
        # The bundled Writer then adds X after links and drops list numbers.
        # Read the raw ID: python-docx reports Normal for undefined styles.
        raw_style = p._p.pPr.pStyle if p._p.pPr is not None else None
        if raw_style is not None and raw_style.val in ("Compact", "FirstParagraph"):
            p.style = doc.styles["Body Text"]
            p.paragraph_format.space_after = Pt(3)
        if p.style.name in ("Source Code", "Verbatim Char"):
            p.paragraph_format.line_spacing = 1.0
            for run in p.runs:
                run.font.size = Pt(8)
    doc.core_properties.title = "제주도 맞춤 개인 관광 페르소나(테마) 추천 AI Agent"
    doc.core_properties.subject = "문항 설계, 기존 서비스 분석, LAG 검토 계획, 추천 구조와 개발 절차"
    doc.core_properties.author = "OREUM IT 프로젝트 기획"
    doc.core_properties.keywords = "제주, 관광, 페르소나, LAG, 추천, 설문, 설계"
    doc.save(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", action="store_true")
    args = parser.parse_args()
    if not shutil.which("pandoc"):
        raise SystemExit("pandoc is required to build this report")
    OUT.mkdir(exist_ok=True)
    cover = """---
title: '제주도 맞춤 개인 관광 페르소나(테마) 추천 AI Agent'
subtitle: '문항 설계부터 추천·일정 검증까지 — 기존 서비스 확장 기획 보고서'
author: 'OREUM IT 프로젝트 기획'
date: '2026년 11월 18일 · 한국 시간 · 검증 전 설계 초안'
lang: ko-KR
---

**작성 목적**

기존 제주 관광 서비스의 실제 소스와 데이터 자산을 출발점으로, 사용자의 문항 응답이 여행 프로필·테마·장소·일정으로 이어지는 구조와 구현·검증 절차를 제안한다.

**핵심 권고**

기존 Vercel 앱의 4문항·코스빌더·Gemini 비서를 재사용하되 공통 여행 프로필, 필수 제약 검사, 근거 추적과 추천 평가를 추가한다. 외부 Agent Worker·사설 Ollama·HTTP MCP 서버로 모델 실행과 도구를 연결한다. 처음 지정된 논문 2504.09277은 SynthTRIPs 평가셋 생성 틀로 확인되어 평가에 쓰고, LAG는 2508.05509 Logic-Augmented Generation으로서 Agent 추론 비교 실험으로 검토한다(2026-11-18 사용자 결정). Llama는 모델 후보, Ollama는 실행 계층, MCP는 도구 연결 규약이다. LAG와 RAG를 동일시하지 않는다.

**확인 범위**

원본 웹·Flutter와 공식 Ollama·MCP 고정 소스 문서를 읽었다. 기존 저장소의 CSV 55개를 실제 검사해 카탈로그·컬럼 사전·로컬 묶음을 만들었다. 원본 ZIP은 재배포 조건 검토 전이므로 공개 Git에서 제외한다. 배포 사이트는 네트워크 차단으로 직접 확인하지 못했다. 읽기 전용 MCP 3개 도구와 기존 자료 7,561건을 이용한 로컬 연결을 검증했다. 서비스·HF 클라이언트·Agent 카탈로그·평가셋 62개와 Gateway 10개 검사가 로컬에서 통과했고, 기반 커밋 ad3c363의 main GitHub Actions 성공을 확인했다. 원본 앱과 같은 장소 981곳의 근거 등급을 실측했으며 verified 값과 비용 값은 0건이다. 3단계 평가셋 96문항의 정답을 규칙으로 계산했고 필수 조건 판정은 모두 unknown이다. SynthTRIPs 저자 공식 코드(커밋 9562f53)를 읽어 생성 순서와 질의 문장 규칙·채점 지표를 대조했으며 코드는 실행하지 않았다. 기준선 채점과 모델 공급자 호출은 하지 않았다. HF 실연결·조직 생성은 미완료다. 도메인·네임서버 저장은 사용자 보고이며 DNS 활성화·공개 연결은 미검증이다. 두 논문의 본문 세부와 별도 PDF·Excel 원본은 미확인이다(논문 자체는 초록 수준으로만 확인). Ollama 모델·LAG·운영 앱 통합은 아직 실행하지 않았다. 신규 문항·점수·목표는 검증 전 제안이다.

**이번 산출물**

기존 서비스 분석, 문항·8개 페르소나 설계 후보·13개 취향 축·시각화, 추천·데이터 계획, Ollama 기능·Cloud 차이, MCP 버전·도구·인증·작업 큐, Claude·OpenAI 동시 지원·다중 터널·DB 위치·계정/비용, 도메인·관리 MCP·장비 연결 안내, oreumit.com 다음 단계·배치도·MCP 초기 구현, 기존 Vercel 앱의 호환·단계별 전환·롤백 설계, LAG 재현·모델 비교·평가 계획, 확정 CSV 목록, Hugging Face MCP·조직·CI 안내, 전체 AI AGENT MODEL 레지스트리와 Claude Code 인수인계, Agent 구축 가능성 진단과 근거 등급 장소 카탈로그·테마 매핑, Langflow 1.13.3과 jeju-mcp 연결·시작 흐름, 3단계 평가셋 생성기·정답 규칙·채점 함수와 집계. 페르소나 시각화에는 실제 관광객 표본이나 학습된 군집을 사용하지 않았다.
"""
    contents = "# 보고서 구성\n\n" + "\n".join(f"- {title}" for _, title in CHAPTERS)
    contents += "\n\n문항과 추천 계산은 3장, 페르소나 설계·시각화는 4장, Ollama·Vercel 구성은 6장, MCP 서버 구축은 7장에 정리했다. Claude·OpenAI·터널·DB·계정/비용은 8장, 도메인·관리 MCP·장비 연결은 9장, 네임서버 저장 후 단계·MCP 구현 결과는 10장, 구현 공통 절차는 11장, 연구·데이터 미확인 항목은 13장을 기준으로 관리한다.\n"
    contents += "\n확정 CSV의 출처·품질·재생성은 14장, HF MCP와 계정·CI는 15~16장, 전체 모델의 역할·검증 단계는 17장, 다른 Claude Code 세션의 시작 순서와 다음 구현은 18장, Agent 단계 진행과 근거 등급 카탈로그는 19장, Langflow 연결과 보안 기준은 20장, Docker 운영 배치는 21장, 탈옥·프롬프트 주입 방어는 22장, 평가셋 생성기와 정답 규칙은 23장을 따른다.\n"
    parts = [cover, contents]
    for filename, title in CHAPTERS:
        parts.extend(["# " + title, source_body(filename)])
    md = OUT / (STEM + ".md")
    md.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    docx_path = OUT / (STEM + ".docx")
    with tempfile.TemporaryDirectory(prefix="jeju-report-") as tmp:
        reference = Path(tmp) / "reference.docx"
        make_reference(reference)
        subprocess.run(["pandoc", str(md), "--from=markdown", "--to=docx",
                        "--resource-path=" + str(OUT) + os.pathsep + str(ROOT),
                        "--reference-doc=" + str(reference), "--standalone",
                        "--output=" + str(docx_path)], cwd=ROOT, check=True)
        polish(docx_path)
        if args.pdf:
            soffice = shutil.which("soffice")
            if not soffice:
                raise SystemExit("soffice is required for --pdf")
            profile = Path(tmp) / "lo-profile"
            cache = Path(tmp) / "cache"
            cache.mkdir()
            env = dict(os.environ, XDG_CACHE_HOME=str(cache))
            subprocess.run([soffice, "-env:UserInstallation=" + profile.as_uri(),
                            "--headless", "--convert-to", "pdf", "--outdir", str(OUT),
                            str(docx_path)], cwd=ROOT, env=env, check=True)
            pdf = OUT / (STEM + ".pdf")
            if not pdf.is_file() or pdf.stat().st_size == 0:
                raise SystemExit("PDF export did not produce a nonempty file")
    print(docx_path)
    if args.pdf:
        print(OUT / (STEM + ".pdf"))


if __name__ == "__main__":
    main()
