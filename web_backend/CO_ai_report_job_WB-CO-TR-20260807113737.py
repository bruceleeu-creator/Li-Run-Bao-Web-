"""利润宝 · 完整财报后台任务薄 runner。

runner 只管理持久化生命周期、进度和线程；业务流水线消费 SQLite 冻结快照，
报告与 completed 终态由同一个数据库事务提交。
"""

from __future__ import annotations

import importlib
import fcntl
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

_db = importlib.import_module("web_backend.CO_db_WB-CO-TR-20260805160732")

_lock = threading.Lock()
_active_by_version: dict[str, str] = {}
_threads_by_job: dict[str, threading.Thread] = {}
_OWNER_TOKEN = f"process-{uuid.uuid4().hex}"
logger = logging.getLogger(__name__)
_lease_lock = threading.Lock()
_lease_handle = None
_lease_path: Path | None = None
_lease_refcount = 0
_pending_lease_releases = 0
_admission_lock = threading.Lock()
_accepting_jobs = True
_SHUTDOWN_WAIT_SECONDS = 2.0


class ProcessLeaseError(RuntimeError):
    pass


def get_process_lock_path() -> Path:
    return Path(str(_db.get_db_path()) + ".process.lock")


def acquire_process_lease() -> bool:
    """获取单实例 OS file lock；锁由打开的 fd 持有，进程退出自动释放。"""
    global _lease_handle, _lease_path, _lease_refcount, _accepting_jobs
    target = get_process_lock_path().resolve()
    with _lease_lock:
        if _lease_handle is not None and _lease_path == target:
            _lease_refcount += 1
            return True
        if _lease_handle is not None:
            if not _release_process_lease_locked(force=True):
                return False
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = target.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        os.chmod(target, 0o600)
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        _lease_handle = handle
        _lease_path = target
        _lease_refcount = 1
    with _admission_lock:
        _accepting_jobs = True
    return True


def _live_worker_threads() -> list[threading.Thread]:
    with _lock:
        return [
            thread
            for thread in _threads_by_job.values()
            if thread.is_alive()
        ]


def _release_process_lease_locked(*, force: bool) -> bool:
    """已持有 ``_lease_lock`` 时释放一个租约引用。

    运行中 worker 是跨进程租约的生存证明；即使 ``force=True`` 也只能
    跳过引用计数，不能跳过 worker 安全门禁。
    """
    global _lease_handle, _lease_path, _lease_refcount
    if _lease_handle is None:
        return True
    if _live_worker_threads():
        return False
    if not force and _lease_refcount > 1:
        _lease_refcount -= 1
        return True
    try:
        fcntl.flock(_lease_handle.fileno(), fcntl.LOCK_UN)
    finally:
        _lease_handle.close()
        _lease_handle = None
        _lease_path = None
        _lease_refcount = 0
    return True


def release_process_lease(
    *, force: bool = False, _already_locked: bool = False
) -> bool:
    if _already_locked:
        return _release_process_lease_locked(force=force)
    with _lease_lock:
        return _release_process_lease_locked(force=force)


def owns_process_lease() -> bool:
    with _lease_lock:
        return _lease_handle is not None and _lease_path == get_process_lock_path().resolve()


def shutdown_workers(timeout: float | None = None) -> bool:
    """停止接收新 job，并在明确上限内等待已启动 worker。

    返回 ``True`` 表示本进程所有 worker 已停止，调用者才可释放
    process lease。超时不会强杀 daemon thread，而是 fail-closed 继续持锁。
    """
    global _accepting_jobs
    wait_seconds = _SHUTDOWN_WAIT_SECONDS if timeout is None else max(0.0, timeout)
    with _admission_lock:
        _accepting_jobs = False
    deadline = time.monotonic() + wait_seconds
    while True:
        current = threading.current_thread()
        workers = [
            worker for worker in _live_worker_threads() if worker is not current
        ]
        if not workers:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "后台报告任务在关停等待上限内未结束，继续持有进程租约"
            )
            return False
        workers[0].join(timeout=remaining)


def _release_deferred_lease_if_idle() -> None:
    """最后一个 worker 退出后完成 lifespan 超时时延后的租约释放。"""
    global _pending_lease_releases
    if _live_worker_threads():
        return
    with _lease_lock:
        if _live_worker_threads():
            return
        pending = _pending_lease_releases
        _pending_lease_releases = 0
        for _ in range(pending):
            if not _release_process_lease_locked(force=False):
                _pending_lease_releases += 1
                break


def _reap_worker_after_exit(job_id: str, worker: threading.Thread) -> None:
    """在 worker target 真正返回后移出跟踪表并尝试延后解锁。"""
    worker.join()
    with _lock:
        if _threads_by_job.get(job_id) is worker:
            _threads_by_job.pop(job_id, None)
    _release_deferred_lease_if_idle()


def _schedule_worker_reap(job_id: str) -> None:
    worker = threading.current_thread()
    reaper = threading.Thread(
        target=_reap_worker_after_exit,
        args=(job_id, worker),
        name=f"ai-report-reaper-{job_id}",
        daemon=True,
    )
    reaper.start()


def defer_process_lease_release() -> None:
    """登记一次延后释放；若 worker 已在竞态窗口退出则立即释放。"""
    global _pending_lease_releases
    with _lease_lock:
        _pending_lease_releases += 1
    _release_deferred_lease_if_idle()


def shutdown_process_lifecycle(timeout: float | None = None) -> bool:
    """完成一次应用关停；超时时由最后一个 worker 延后解锁。"""
    if shutdown_workers(timeout):
        return release_process_lease()
    defer_process_lease_release()
    return False


def _pipeline_mod() -> Any:
    return importlib.import_module(
        "web_backend.CO_ai_report_pipeline_WB-CO-TR-20260807113737"
    )


def _new_job_id() -> str:
    return f"job-{uuid.uuid4().hex[:12]}"


def _workspace_lifecycle_mod() -> Any:
    return importlib.import_module("web_backend.CO_import_WB-CO-TR-20260805160732")


def start_job(session_version: str | None) -> str:
    """在工作区门禁内原子冻结 SQLite 当前会话并启动或复用任务。"""
    if not owns_process_lease() and not acquire_process_lease():
        raise ProcessLeaseError("另一个利润宝进程正在运行报告任务")
    lifecycle = _workspace_lifecycle_mod()
    with _admission_lock:
        if not _accepting_jobs:
            raise ProcessLeaseError("利润宝正在关停，暂停启动新报告任务")
        with lifecycle.workspace_lifecycle_guard():
            return _start_job_locked(session_version)


def _start_job_locked(session_version: str | None) -> str:
    candidate_id = _new_job_id()
    job_id, created = _db.capture_session_and_create_job(
        candidate_id, session_version or None, _OWNER_TOKEN
    )
    stored = _db.get_job(job_id)
    if stored is None:
        raise RuntimeError("任务创建后不可读取")
    actual_version = stored["session_version"]
    with _lock:
        _active_by_version[actual_version] = job_id
    if not created:
        return job_id

    def update(
        progress=None,
        *,
        stage=None,
        current=None,
        total=None,
        message=None,
    ) -> bool:
        continue_status = _db.job_continue_status(job_id, actual_version)
        if continue_status == "session_changed":
            raise _pipeline_mod().PipelineError(
                "SESSION_CHANGED",
                "会话已变化，任务已取消，未保存报告",
                stage=str(stage or getattr(progress, "stage", "progress")),
            )
        if continue_status != "ok":
            raise _pipeline_mod().PipelineError(
                "JOB_STATE_CHANGED",
                "任务状态已变化，未保存报告",
                stage=str(stage or getattr(progress, "stage", "progress")),
            )
        if progress is not None:
            stage = progress.stage
            current = progress.current
            total = progress.total
            message = progress.message
        changed = _db.update_job(
            job_id,
            stage=stage,
            current=current,
            total=total,
            message=message,
        )
        if not changed:
            raise _pipeline_mod().PipelineError(
                "PROGRESS_INVALID",
                "报告任务进度无效，未保存报告",
                stage=str(stage or "progress"),
            )
        return True

    def worker() -> None:
        try:
            if not _db.start_job(job_id):
                return
            snapshot = _db.load_job_input_snapshot(job_id)
            result = _run_pipeline(job_id, snapshot, update)
            result = _coerce_pipeline_result(result)
            outcome = _db.commit_report_and_complete_job(
                job_id,
                snapshot.session_version,
                _report_title(snapshot),
                result,
            )
            if outcome.status == "session_changed":
                _db.fail_job_safe(
                    job_id,
                    "SESSION_CHANGED",
                    "会话已变化，任务已取消，未保存报告",
                )
            elif outcome.status != "completed":
                # 任务可能已被外部安全终态化；条件更新会阻止二次写入。
                _db.fail_job_safe(
                    job_id,
                    "JOB_STATE_CHANGED",
                    "任务状态已变化，未保存报告",
                )
        except _pipeline_mod().PipelineError as exc:
            _db.fail_job_safe(job_id, exc.code, exc.public_message)
        except Exception:
            # Do not attach exc_info: SDK/OCR exceptions can contain request
            # payloads, local paths, or credentials.
            logger.error("完整财报后台流水线发生内部异常 job_id=%s", job_id)
            _db.fail_job_safe(
                job_id,
                "INTERNAL_PIPELINE_ERROR",
                "报告任务发生内部错误，请重试",
            )
        finally:
            with _lock:
                if _active_by_version.get(actual_version) == job_id:
                    _active_by_version.pop(actual_version, None)
            try:
                _workspace_lifecycle_mod().retry_workspace_cleanup()
            except Exception:
                logger.error("后台任务终态后的工作区退休扫描失败")
            # 当前 thread 在 finally 中仍然 is_alive()，不能由它自己证明
            # worker 已退出并释放跨进程租约。交由 reaper join 后收尾。
            _schedule_worker_reap(job_id)

    thread = threading.Thread(
        target=worker,
        name=f"ai-report-job-{job_id}",
        daemon=True,
    )
    with _lock:
        _threads_by_job[job_id] = thread
    try:
        thread.start()
    except Exception:
        with _lock:
            _active_by_version.pop(actual_version, None)
            _threads_by_job.pop(job_id, None)
        _db.fail_job_safe(job_id, "WORKER_START_FAILED", "报告任务启动失败，请重试")
        try:
            _workspace_lifecycle_mod().retry_workspace_cleanup()
        except Exception:
            logger.error("后台任务启动失败后的工作区退休扫描失败")
        raise
    return job_id


def _coerce_pipeline_result(result: Any):
    """runner 只接受协调器的强类型结果，禁止字符串旁路。"""
    pipeline = _pipeline_mod()
    if isinstance(result, pipeline.PipelineResult):
        return result
    raise pipeline.PipelineError(
        "PIPELINE_RESULT_INVALID", "流水线结果无效，未保存", stage="finalize"
    )


def _report_title(snapshot: Any) -> str:
    company = str(snapshot.company_name or "").strip()
    return f"{company} 跨年合并报告" if company else "跨年合并报告"


def _run_pipeline(
    job_id: str,
    snapshot: Any,
    update: Callable[..., bool],
):
    """独立协调器适配点；job_id 不进入业务事实。"""
    del job_id
    return _pipeline_mod().run_report_pipeline(
        snapshot, lambda progress: update(progress)
    )


def get_job(job_id: str) -> dict | None:
    snapshot = _db.get_job(job_id)
    if snapshot is not None and snapshot["status"] in {"completed", "failed"}:
        with _lock:
            thread = _threads_by_job.get(job_id)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
            snapshot = _db.get_job(job_id)
    return snapshot


def get_active_job(session_version: str) -> dict | None:
    if not session_version:
        return None
    return _db.get_active_job(session_version)


def has_active_jobs() -> bool:
    # SQLite is the lifecycle authority.  A worker publishes its terminal row
    # just before its finally-block removes the in-memory hint, so consulting
    # the hint here would spuriously defer retirement after completion.
    return _db.has_active_jobs()


def fail_job(job_id: str, error: str) -> bool:
    """兼容管理入口；生产 worker 使用结构化 fail_job_safe。"""
    changed = _db.fail_job(job_id, error)
    if changed:
        _workspace_lifecycle_mod().retry_workspace_cleanup()
    return changed


def complete_job(job_id: str, markdown: str) -> bool:
    """兼容状态机测试；正式报告只能走原子提交接口。"""
    changed = _db.complete_job(job_id, markdown)
    if changed:
        _workspace_lifecycle_mod().retry_workspace_cleanup()
    return changed


def recover_orphaned_jobs() -> int:
    """应用启动时终态化其他进程留下的 active job。"""
    if not owns_process_lease():
        return 0
    return _db.recover_orphaned_jobs(_OWNER_TOKEN, lease_verified=True)
