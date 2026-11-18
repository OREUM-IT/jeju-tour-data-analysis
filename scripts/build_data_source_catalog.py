#!/usr/bin/env python3
"""Join the measured CSV inventory with pinned repository evidence and package it.

No network requests, credentials, original-file edits or production ingestion.
Run build_raw_data_inventory.py first. --bundle creates a local-only source ZIP;
redistribution terms must be checked before that archive is published.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REPORTS = ROOT / "reports"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(content):
    return hashlib.sha256(content).hexdigest()


def source_map(source):
    base = source / "prototype/data"
    mapping = {}
    for item in read_json(base / "public-csv/sources.json")["files"]:
        mapping["public-csv/" + item["file"]] = dict(
            title=item["title"], provider=item.get("provider", ""),
            source_urls=item["page"], dataset_ids=item["pk"],
            evidence_file="prototype/data/public-csv/sources.json",
            source_date_recorded=item.get("baseDate", ""),
            upstream_collected_at_recorded=item.get("collected", ""),
            license_recorded=item.get("license", ""),
            transformation_recorded=item.get("method", ""),
            data_kind="jeju_filtered_snapshot" if "전국 → 제주" in item.get("method", "") else "source_file_snapshot",
            scope_recorded=item.get("scope", ""),
            caveats_recorded=" | ".join(item.get("caveats", [])),
            metadata_sha256=item.get("sha256", ""), metadata_rows=item.get("rows"),
            update_cycle_recorded=item.get("cycle", ""),
        )
    hub_periods = {
        "lodging": "2025-12-31", "tourist-lodging": "2024-12-31",
        "hygiene": "2025-05-23", "jeju-festivals": "마지막 종료일 2025-04-13",
        "visit-jeju-places": "게시기간 2024-10-01 ~ 2024-12-31; 행별 수정일 별도",
    }
    for item in read_json(base / "hub-extra/sources.json"):
        mapping["hub-extra/" + item["id"] + ".csv"] = dict(
            title=item["title"], source_urls=item["url"],
            evidence_file="prototype/data/hub-extra/sources.json | prototype/data/hub-extra/README.md",
            source_date_recorded=hub_periods[item["id"]],
            upstream_collected_at_recorded=item.get("retrieved", ""),
            license_recorded="README 기록: 이용허락범위 제한 없음" if "data.go.kr" in item["url"] else "개별 이용조건 재확인 필요",
            data_kind="source_file_snapshot", transformation_recorded="원본 CSV 스냅샷(원본 README 기록)",
            metadata_sha256=item["sha256"], metadata_rows=item["rows"],
            caveats_recorded="VISIT JEJU 여행장소의 주소·좌표 불일치/제주 외 행, 숙박 등록기록 중복, 과거 행사 확인 필요",
        )
    for filename, ident, title, date, rows in [
        ("markets", 985, "서귀포시 전통시장 정보", "2021-11-24", 8),
        ("beaches", 784, "제주특별자치도 해수욕장 정보", "2016~2023; 최신 2023-12-31", 88),
        ("info", 1022, "제주 관광 안내소 현황", "2021-02-18", 14),
    ]:
        mapping[f"hub-source/{filename}.csv"] = dict(
            title=title, dataset_ids=str(ident), source_urls=f"https://jejudatahub.net/data/view/data/{ident}",
            evidence_file="prototype/data/hub-source/README.md", source_date_recorded=date,
            upstream_collected_at_recorded="2026-11-16", data_kind="source_file_snapshot",
            transformation_recorded="원본 CSV 스냅샷(원본 README 기록)", metadata_rows=rows,
            caveats_recorded="과거 시점 자료. 실제 개장·영업·시설 이용 가능 상태는 별도 확인",
        )
    airlines = read_json(base / "airlines/sources.json")
    for item in airlines["sources"]:
        mapping[f"airlines/raw/{item['id']}.csv"] = dict(
            title="항공사별 관광객 통계 · " + item["name"], dataset_ids=str(item["id"]),
            source_urls=item["page"], download_url_recorded=item.get("download", ""),
            evidence_file="prototype/data/airlines/sources.json",
            upstream_collected_at_recorded=airlines.get("retrievedAt", ""),
            license_recorded=item.get("license", ""), metadata_sha256=item.get("sha256", ""),
            data_kind="source_file_snapshot", transformation_recorded="원본 CSV 스냅샷",
            caveats_recorded="집계 통계. 개별 관광객 선호·현행 운항·실시간 혼잡도를 뜻하지 않음",
        )
    bike = [
        ("route", "국토종주 자전거길 제주 노선좌표", "", "2024-02-29"),
        ("facilities", "국토종주 자전거길 제주 주변시설 좌표", "", "2024-02-29"),
        ("codebook", "국토종주 자전거길 제주 노선 코드북", "", "2024-02-29"),
        ("rental", "전국자전거대여소표준데이터 제주 부분", "15017319", "2023-11-29"),
        ("storage", "전국자전거보관소표준데이터 제주 부분", "15017318", "2020-06-30"),
    ]
    for name, title, ident, date in bike:
        mapping[f"bike/{name}-jeju.csv"] = dict(
            title=title, dataset_ids=ident,
            source_urls=f"https://www.data.go.kr/data/{ident}/standard.do" if ident else "",
            source_date_recorded=date, data_kind="jeju_filtered_snapshot",
            transformation_recorded="제주 노선·시설 추출본; build_bike.py와 jeju_bike.json의 출처 기록",
            evidence_file="prototype/build_bike.py | prototype/data/jeju_bike.json",
            caveats_recorded="원본 전체 ZIP은 미확보. 파일 기준 과거 노선·시설이며 현행 안전·운영 보장 아님",
        )
    temporal = read_json(base / "public-csv/temporal-demand/sources.json")
    mapping["public-csv/temporal-demand/profiles.csv"] = dict(
        title="계절·요일·시간별 상대 수요 프로필(파생 집계)",
        dataset_ids="512 | 490 | 1205 | 1206 | 1207 | 1208",
        source_urls=" | ".join(f"https://jejudatahub.net/data/view/data/{n}" for n in [512, 490, 1205, 1206, 1207, 1208]),
        evidence_file="prototype/data/public-csv/temporal-demand/sources.json | docs/crowd-csv-and-operating-account.md",
        source_date_recorded="버스 2023-12-25~2025-10-26; 외식 2019~2021; 렌터카 2020~2021",
        license_recorded=temporal.get("license", ""), data_kind="derived_aggregate",
        transformation_recorded="원자료의 계절·요일·시간대 상대지수 집계. 26개 원본 CSV는 저장소에 없음",
        caveats_recorded="실시간 혼잡도·대기시간·관광객 페르소나 아님. 외식 요일 없음, 렌터카 토요일 표본",
    )
    return mapping


def usage(relative_path):
    name = Path(relative_path).stem
    if "temporal-demand" in relative_path or "airlines" in relative_path or name in {"hallasan-visitors-2024", "hallasan-reservations", "ferry-operations-jeju", "ferry-usage-jeju", "flights-cju"}:
        return "P2", "과거 수요·교통 집계와 비교 평가", "현재 운항·잔여석·혼잡 상태와 분리"
    if name.startswith("visitjeju-") and name != "visitjeju-festivals":
        return "P0", "장소 후보·상세 속성·테마 근거", "상세 3종에는 공통 ID가 없어 이름 매칭의 유일성·제주 좌표·검증 근거 필요"
    if name in {"tourist-spots-jeju", "visit-jeju-places", "oreum-list", "oreum-summary-2021"}:
        return "P0", "장소 후보·위치·자연 테마", "중복·좌표·실제 장소 연결·현재 운영 확인"
    if any(word in name for word in ["accessible", "nursing", "parking", "hygiene", "good-price"]):
        return "P1", "접근성·편의·예산 조건 근거", "근거의 대상 장소·의미·유효기간 검증 후 필수 조건에 사용"
    if "festival" in name or "performance" in name or "opening" in name:
        return "P1", "여행일별 행사·계절 운영 후보", "여행 날짜와 일치·취소/변경·날짜 누락/역전 확인"
    if "bus" in name or "bike" in relative_path:
        return "P1", "이동·자전거·지역 동선 보조", "현행 시간표·실시간 교통·시설 운영과 구분"
    return "P1", "테마·지역별 후보와 상세 근거", "출처·최신성·중복·장소 연결 확인 후 적용"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/workspace/scratch/reference-repos/jeju-tour"))
    parser.add_argument("--bundle", action="store_true", help="Create a local-only source ZIP; does not establish redistribution rights")
    args = parser.parse_args()
    source = args.source.resolve()
    inventory = read_json(DATA / "raw-data-inventory.json")
    if Path(inventory["source_repository_path"]).resolve() != source:
        raise SystemExit("Inventory source repository differs; rebuild inventory first.")
    files = inventory["files"]
    commit = inventory["source_commit"]
    recorded = source_map(source)
    rows, originals = [], {}
    for item in files:
        relative = item["relative_path"]
        short = str(Path(relative).relative_to("prototype/data"))
        content = (source / relative).read_bytes()
        if digest(content) != item["sha256"] or item["git_blob"] != item["worktree_git_blob"]:
            raise SystemExit("Source changed or differs from Git: " + relative)
        if short not in recorded:
            raise SystemExit("Missing explicit source mapping: " + short)
        meta = dict(recorded[short])
        expected_hash = meta.pop("metadata_sha256", "")
        expected_rows = meta.pop("metadata_rows", None)
        priority, use, next_action = usage(short)
        meta.update(
            relative_path=relative, archive_path="csv/" + short, data_rows=item["data_rows"],
            column_count=item["column_count"], encoding=item["encoding"], byte_size=item["byte_size"],
            sha256=item["sha256"], source_commit=commit,
            repository_file_url=f"https://github.com/OREUM-IT/jeju-tour/blob/{commit}/{quote(relative)}",
            snapshot_download_url=f"https://raw.githubusercontent.com/OREUM-IT/jeju-tour/{commit}/{quote(relative)}",
            availability_status="verified_repository_snapshot",
            metadata_hash_check="match" if expected_hash == item["sha256"] else "mismatch" if expected_hash else "not_recorded",
            metadata_rows_check="match" if expected_rows == item["data_rows"] else "mismatch" if expected_rows is not None else "not_recorded",
            license_review_status="recorded_source_only; current_terms_not_rechecked",
            live_source_status="not_refreshed_network_proxy_403",
            priority=priority, feature_uses=use, next_action=next_action,
            inspected_at=inventory["generated_at"], excel_mapping_status="original_workbook_not_available",
        )
        if not meta.get("dataset_ids"):
            ids = re.findall(r"/(?:data/view/data|data)/(\d+)", meta.get("source_urls", ""))
            meta["dataset_ids"] = " | ".join(ids)
        rows.append(meta)
        originals["csv/" + short] = content
    columns = ["relative_path", "title", "data_kind", "data_rows", "column_count", "encoding", "priority", "feature_uses", "source_urls", "dataset_ids", "source_date_recorded", "upstream_collected_at_recorded", "availability_status", "metadata_hash_check", "metadata_rows_check", "license_recorded", "license_review_status", "caveats_recorded", "next_action", "evidence_file", "archive_path", "snapshot_download_url", "repository_file_url", "source_commit", "sha256", "byte_size", "inspected_at", "live_source_status", "excel_mapping_status"]
    columns += sorted({key for row in rows for key in row} - set(columns))
    target = DATA / "confirmed-data-catalog.csv"
    with target.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    notes = f"""# 제주 관광 데이터 CSV 스냅샷 묶음

검사 시각: {inventory['generated_at']}
출처: https://github.com/OREUM-IT/jeju-tour/tree/{commit}/prototype/data

CSV {len(rows)}개를 위 Git 커밋의 실제 파일과 대조했습니다. csv/ 아래 파일은
원래 바이트·인코딩을 보존합니다. CP949 파일도 있으므로 encoding 컬럼을 확인하세요.
catalog/ 아래 목록·컬럼사전은 UTF-8 BOM CSV입니다.

원본 스냅샷, 제주 부분 추출본, 파생 집계를 data_kind로 구분합니다.
특히 temporal-demand/profiles.csv는 원천 관측이 아닌 파생 상대지수입니다.
서로 겹치는 장소·시점·집계 단위가 있으므로 행 수 합계를 관광지 수로 쓰면 안 됩니다.

이번 작업은 이미 저장된 파일의 확보·검증·정리입니다. 제공 사이트의 최신 파일을
새로 내려받은 것은 아닙니다. 수집일·기간·라이선스 문구는 원본 저장소 기록이며
현재 원문과 재대조한 것으로 표시하지 않습니다. evidence/에 관련 기록을 동봉했습니다.
명시된 이용조건과 이미지별 권리는 서비스 적용 및 공개 재배포 전에 확인해야 합니다.
55개 중 라이선스 기록 공란 8개와 개별 조건 재확인 2개가 있습니다. 이 ZIP은 로컬
검토용이며 공개 저장소에서 제외됩니다. Git 접근 가능 상태는 재배포 허가가 아닙니다.
현재 운영 상태도 별도로 확인해야 합니다.

원본 Excel은 포함되지 않았습니다. 문서에서 언급한 1,259개 항목 전체의 대조를
완료했다는 뜻이 아닙니다. 실제 장소 매칭·필수 조건 근거·최신성 검증 후 Agent에 연결합니다.
"""
    REPORTS.mkdir(exist_ok=True)
    if not args.bundle:
        print(json.dumps({"csv_files": len(rows), "catalog": str(target), "bundle_created": False}, ensure_ascii=False, indent=2))
        return
    archive = REPORTS / "jeju-data-snapshot-bundle.zip"
    evidence = [
        "prototype/data/public-csv/sources.json", "prototype/data/public-csv/README.md",
        "prototype/data/hub-source/README.md", "prototype/data/hub-extra/sources.json",
        "prototype/data/hub-extra/hub-sources.json", "prototype/data/hub-extra/README.md",
        "prototype/data/airlines/sources.json", "prototype/data/public-csv/temporal-demand/sources.json",
        "docs/jeju-datahub-integration.md", "docs/crowd-csv-and-operating-account.md",
        "prototype/build_bike.py", "prototype/data/jeju_bike.json",
    ]
    with ZipFile(archive, "w", ZIP_DEFLATED, compresslevel=6) as zipped:
        zipped.writestr("README.md", notes)
        for name, content in originals.items():
            zipped.writestr(name, content)
        for name in ["confirmed-data-catalog.csv", "raw-data-inventory.csv", "raw-data-columns.csv", "raw-data-inventory.json"]:
            zipped.write(DATA / name, "catalog/" + name)
        for relative in evidence:
            zipped.write(source / relative, "evidence/" + relative)
    with ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        for name, content in originals.items():
            assert digest(zipped.read(name)) == digest(content)
    print(json.dumps({"csv_files": len(rows), "data_rows_sum_not_unique_places": sum(row["data_rows"] for row in rows), "metadata_hash_matches": sum(row["metadata_hash_check"] == "match" for row in rows), "metadata_row_matches": sum(row["metadata_rows_check"] == "match" for row in rows), "metadata_mismatches": [row["relative_path"] for row in rows if "mismatch" in [row["metadata_hash_check"], row["metadata_rows_check"]]], "catalog": str(target), "bundle": str(archive), "bundle_bytes": archive.stat().st_size}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
