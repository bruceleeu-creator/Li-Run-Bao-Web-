"""通用案例包（CaseManifest）注册与解析。

任意企业：在 demo_output/cases/<id>/ 下放 manifest.json + 文件（或指向外部目录），
无需改 Python 业务代码。见架构 Spec Phase B。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# 项目根（core/ 的上一级）
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CASES_ROOT = _PROJECT_ROOT / "demo_output" / "cases"
# 额外搜索根：案例文件可在这些目录中按文件名解析
_DEFAULT_FILE_SEARCH_ROOTS = [
    _PROJECT_ROOT / "demo_output" / "cases",
    _PROJECT_ROOT.parent / "测试文件",
]


class CaseManifestError(Exception):
    """案例包配置或文件缺失。"""


@dataclass
class CaseFileRef:
    path: str  # 文件名或相对路径
    report_year: Optional[int] = None

    @classmethod
    def from_raw(cls, raw: Any) -> "CaseFileRef":
        if isinstance(raw, str):
            return cls(path=raw)
        if isinstance(raw, dict):
            yr = raw.get("report_year")
            return cls(
                path=str(raw.get("path") or raw.get("name") or ""),
                report_year=int(yr) if yr is not None else None,
            )
        raise CaseManifestError(f"非法 files 项：{raw!r}")


@dataclass
class CaseManifest:
    id: str
    label: str = ""
    description: str = ""
    company_name: str = ""
    industry: str = "制造业"
    files: List[CaseFileRef] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    defaults: Dict[str, Any] = field(default_factory=dict)
    gold: Optional[str] = None  # 相对 case 目录
    tags: List[str] = field(default_factory=list)
    # 解析后填充
    case_dir: Optional[Path] = None
    manifest_path: Optional[Path] = None

    @property
    def income_tax_nominal_rate(self) -> Optional[float]:
        v = self.defaults.get("income_tax_nominal_rate")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def all_ids(self) -> List[str]:
        ids = [self.id]
        for a in self.aliases:
            if a and a not in ids:
                ids.append(a)
        return ids

    def to_public_dict(self, *, available: bool, location: str, resolved_files: Sequence[str]) -> dict:
        return {
            "id": self.id,
            "aliases": list(self.aliases),
            "label": self.label or self.id,
            "description": self.description,
            "files": [f.path for f in self.files],
            "resolved_files": list(resolved_files),
            "company_name": self.company_name,
            "industry": self.industry,
            "defaults": dict(self.defaults),
            "gold": self.gold,
            "tags": list(self.tags),
            "available": available,
            "location": location,
        }


def _load_manifest_file(path: Path) -> CaseManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise CaseManifestError(f"无法读取 {path}: {e}") from e
    if not isinstance(raw, dict):
        raise CaseManifestError(f"manifest 必须是 JSON 对象：{path}")
    cid = str(raw.get("id") or path.parent.name).strip()
    if not cid:
        raise CaseManifestError(f"manifest 缺少 id：{path}")
    files_raw = raw.get("files") or []
    files = [CaseFileRef.from_raw(x) for x in files_raw]
    if not files:
        raise CaseManifestError(f"manifest 缺少 files：{path}")
    aliases = [str(a) for a in (raw.get("aliases") or []) if a]
    m = CaseManifest(
        id=cid,
        label=str(raw.get("label") or cid),
        description=str(raw.get("description") or ""),
        company_name=str(raw.get("company_name") or ""),
        industry=str(raw.get("industry") or "制造业"),
        files=files,
        aliases=aliases,
        defaults=dict(raw.get("defaults") or {}),
        gold=str(raw["gold"]) if raw.get("gold") else None,
        tags=[str(t) for t in (raw.get("tags") or [])],
        case_dir=path.parent.resolve(),
        manifest_path=path.resolve(),
    )
    return m


def discover_case_dirs(cases_root: Optional[Path] = None) -> List[Path]:
    root = Path(cases_root or _DEFAULT_CASES_ROOT)
    if not root.is_dir():
        return []
    out: List[Path] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "manifest.json").is_file():
            out.append(child)
    return out


def load_all_manifests(cases_root: Optional[Path] = None) -> List[CaseManifest]:
    manifests: List[CaseManifest] = []
    for d in discover_case_dirs(cases_root):
        try:
            manifests.append(_load_manifest_file(d / "manifest.json"))
        except CaseManifestError:
            continue
    return manifests


def get_manifest(case_id: str, cases_root: Optional[Path] = None) -> CaseManifest:
    """按 id 或 alias 查找；找不到抛 CaseManifestError。"""
    cid = (case_id or "").strip()
    if not cid:
        raise CaseManifestError("case_id 为空")
    for m in load_all_manifests(cases_root):
        if cid in m.all_ids():
            return m
    raise CaseManifestError(f"未知案例：{cid}")


def resolve_case_files(
    manifest: CaseManifest,
    *,
    extra_search_roots: Optional[Sequence[Path]] = None,
) -> List[Path]:
    """解析 manifest.files 为绝对路径列表（顺序与 manifest 一致）。

    搜索顺序：
    1. case_dir / path
    2. case_dir 下仅文件名
    3. 全局 search roots / case_id / path
    4. 全局 search roots / path（文件名）
    """
    case_dir = manifest.case_dir or Path(".")
    roots: List[Path] = []
    if extra_search_roots:
        roots.extend(Path(r) for r in extra_search_roots)
    roots.extend(_DEFAULT_FILE_SEARCH_ROOTS)

    found: List[Path] = []
    missing: List[str] = []
    for ref in manifest.files:
        name = ref.path.strip()
        if not name:
            missing.append("(空路径)")
            continue
        candidates: List[Path] = [
            case_dir / name,
            case_dir / Path(name).name,
        ]
        for root in roots:
            candidates.append(root / manifest.id / name)
            candidates.append(root / manifest.id / Path(name).name)
            # 兼容旧目录名 audit_3years
            for alias in manifest.aliases:
                candidates.append(root / alias / name)
                candidates.append(root / alias / Path(name).name)
            candidates.append(root / name)
            candidates.append(root / Path(name).name)
            # 测试文件目录直接放 PDF
            candidates.append(root / Path(name).name)

        hit: Optional[Path] = None
        seen = set()
        for c in candidates:
            try:
                key = str(c.resolve()) if c.exists() else str(c)
            except OSError:
                key = str(c)
            if key in seen:
                continue
            seen.add(key)
            if c.is_file():
                hit = c.resolve()
                break
        if hit is None:
            missing.append(name)
        else:
            found.append(hit)

    if missing:
        raise CaseManifestError(
            f"案例 {manifest.id} 缺少文件：{', '.join(missing)}。"
            f"请放到 {case_dir} 或配置的搜索目录（如 测试文件/）。"
        )
    return found


def gold_path(manifest: CaseManifest) -> Optional[Path]:
    if not manifest.gold or not manifest.case_dir:
        return None
    p = manifest.case_dir / manifest.gold
    return p if p.is_file() else None


def list_cases_public(
    cases_root: Optional[Path] = None,
    *,
    extra_search_roots: Optional[Sequence[Path]] = None,
) -> List[dict]:
    """供 GET /import/cases。"""
    out: List[dict] = []
    for m in load_all_manifests(cases_root):
        try:
            paths = resolve_case_files(m, extra_search_roots=extra_search_roots)
            available = True
            location = str(paths[0].parent) if paths else str(m.case_dir or "")
            resolved = [p.name for p in paths]
        except CaseManifestError:
            available = False
            location = str(m.case_dir or "")
            resolved = []
        out.append(
            m.to_public_dict(
                available=available, location=location, resolved_files=resolved
            )
        )
    return out
