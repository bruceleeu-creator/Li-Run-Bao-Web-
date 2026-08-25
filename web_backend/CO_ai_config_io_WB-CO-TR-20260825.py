"""利润宝 · AI 配置文件 IO（参数路径 + 函数内白名单校验）。

职责边界：本模块只做「给定 Path → 读/写/删 JSON」，路径来源
（LIRUNBAO_AI_CONFIG_PATH 或项目根默认值）由调用方 CO_ai 提供。

安全设计：每个文件操作函数在使用路径前先做同函数内的白名单校验——
resolve() 规范化后必须位于项目根目录或系统临时目录之内，且创建路径
本身不得包含 ``..`` 跳转段。写入内容只允许 base_url / model，
API Key 永不落盘（2026-08-25 安全加固）。写入采用「临时文件 + 原子改名」
避免半写状态。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

_ALLOWED_KEYS = ("base_url", "model")
# 白名单根目录：项目根（本文件位于 web_backend/ 下，parents[1] 即项目根）
# + 系统临时目录（测试隔离用）
_ROOT = Path(__file__).resolve().parents[1]


def read_config_file(path: Path) -> dict:
    """读取 JSON 配置；文件不存在或损坏时返回空 dict。

    路径校验：拒绝 ``..`` 跳转；resolve() 后必须位于白名单目录内。
    """
    if ".." in Path(path).parts:
        raise ValueError("AI 配置路径不允许包含 ..")
    real = Path(path).resolve()
    allowed = (_ROOT, Path(tempfile.gettempdir()).resolve())
    if not any(real == root or root in real.parents for root in allowed):
        raise ValueError("AI 配置路径必须位于项目目录或系统临时目录内")
    if not real.exists():
        return {}
    try:
        cfg = json.loads(real.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return cfg if isinstance(cfg, dict) else {}


def write_config_file(path: Path, payload: dict) -> None:
    """把 base_url/model 原子写入配置文件（多余键一律剔除，防止 api_key 混入）。

    路径校验：拒绝 ``..`` 跳转；resolve() 后必须位于白名单目录内。
    """
    if ".." in Path(path).parts:
        raise ValueError("AI 配置路径不允许包含 ..")
    real = Path(path).resolve()
    allowed = (_ROOT, Path(tempfile.gettempdir()).resolve())
    if not any(real == root or root in real.parents for root in allowed):
        raise ValueError("AI 配置路径必须位于项目目录或系统临时目录内")
    safe_payload = {key: payload.get(key, "") for key in _ALLOWED_KEYS}
    real.parent.mkdir(parents=True, exist_ok=True)
    tmp = real.with_name(real.name + ".tmp")
    tmp.write_text(
        json.dumps(safe_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(real)


def delete_config_file(path: Path) -> None:
    """删除配置文件；不存在时静默成功。

    路径校验：拒绝 ``..`` 跳转；resolve() 后必须位于白名单目录内。
    """
    if ".." in Path(path).parts:
        raise ValueError("AI 配置路径不允许包含 ..")
    real = Path(path).resolve()
    allowed = (_ROOT, Path(tempfile.gettempdir()).resolve())
    if not any(real == root or root in real.parents for root in allowed):
        raise ValueError("AI 配置路径必须位于项目目录或系统临时目录内")
    if real.exists():
        real.unlink()
