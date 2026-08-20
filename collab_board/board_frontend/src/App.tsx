import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  apiBoard,
  apiCreateRoom,
  apiCreateTask,
  apiDeleteTask,
  apiJoinByCode,
  apiLogin,
  apiMe,
  apiMembers,
  apiPatchMe,
  apiPatchTask,
  apiRefreshInvite,
  apiRegister,
  apiRemoveMember,
  apiRooms,
  clearSession,
  loadToken,
  loadUser,
} from "./api";
import type { BoardPayload, Member, Me, RoomCard, Task } from "./types";

type View = "login" | "rooms" | "board";
const STATUS_CN: Record<string, string> = { todo: "待办", doing: "进行中", done: "已完成" };
const PRIO_CN: Record<string, string> = { high: "高", mid: "中", low: "低" };
const COLOR_OPTIONS = ["#3D5A80", "#8C5A3C", "#3F6B4F", "#6B4A6E", "#8F8A3D", "#6E5238", "#38566E", "#2F4B6B"];

function fmtDue(due: string | null): string {
  return due ? due.slice(5) : "";
}

function isOverdue(t: Task): boolean {
  if (!t.due_date || t.status === "done") return false;
  return t.due_date < new Date().toISOString().slice(0, 10);
}

export default function App() {
  const [view, setView] = useState<View>(() => (loadToken() ? "rooms" : "login"));
  const [me, setMe] = useState<Me | null>(() => loadUser());
  const [roomId, setRoomId] = useState(0);
  const [error, setError] = useState("");

  useEffect(() => {
    if (loadToken() && !me) {
      apiMe()
        .then(setMe)
        .catch(() => {
          clearSession();
          setView("login");
        });
    }
  }, []);

  const logout = () => {
    clearSession();
    setMe(null);
    setView("login");
  };

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">利润宝 · 协同看板</span>
        {me ? (
          <span className="me">
            <span className="color-dot" style={{ background: me.color }} />
            {me.display_name || me.username}
            <button className="btn btn--ghost btn--sm" onClick={logout}>
              退出
            </button>
          </span>
        ) : null}
      </header>
      {error ? <div className="alert alert--error" role="alert">{error}</div> : null}
      {view === "login" ? (
        <LoginPage
          onDone={(user) => {
            setMe(user);
            setView("rooms");
          }}
          onError={setError}
        />
      ) : view === "rooms" ? (
        <RoomsPage
          me={me}
          onMe={setMe}
          onOpen={(id) => {
            setRoomId(id);
            setView("board");
          }}
          onError={setError}
        />
      ) : (
        <BoardPage
          me={me}
          roomId={roomId}
          onBack={() => setView("rooms")}
          onError={setError}
        />
      )}
      <footer className="footnote">请勿在任务中粘贴财报原始数字 · 云端只存任务与进度</footer>
    </div>
  );
}

/* ── 登录/注册 ─────────────────────────────────────────────── */

function LoginPage({ onDone, onError }: { onDone: (u: Me) => void; onError: (s: string) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    onError("");
    try {
      const out = mode === "login" ? await apiLogin(username, password) : await apiRegister(username, password);
      onDone(out.user);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth card">
      <h2>{mode === "login" ? "登录协同看板" : "注册新账户"}</h2>
      <label className="field">
        <span>用户名（3~32 位字母/数字）</span>
        <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
      </label>
      <label className="field">
        <span>密码（≥8 位）</span>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
      </label>
      <div className="row">
        <button className="btn btn--primary" disabled={busy || !username || !password} onClick={() => void submit()}>
          {busy ? "请稍候…" : mode === "login" ? "登录" : "注册并进入"}
        </button>
        <button className="btn btn--ghost" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "没有账户？注册" : "已有账户？登录"}
        </button>
      </div>
    </div>
  );
}

/* ── 房间列表 ──────────────────────────────────────────────── */

function RoomsPage({ me, onOpen, onError, onMe }: { me: Me | null; onOpen: (id: number) => void; onError: (s: string) => void; onMe: (m: Me) => void }) {
  const [rooms, setRooms] = useState<RoomCard[]>([]);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [showColor, setShowColor] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setRooms(await apiRooms());
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  }, [onError]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const out = await apiCreateRoom(name.trim());
      setName("");
      await refresh();
      onOpen(out.room.id);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const join = async () => {
    const trimmed = code.trim().toUpperCase();
    if (!trimmed) return;
    setBusy(true);
    try {
      const out = await apiJoinByCode(trimmed);
      setCode("");
      await refresh();
      onOpen(out.room.id);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <div className="card">
        <h2>我的房间</h2>
        {rooms.length === 0 ? <p className="hint">还没有房间：创建一个，把邀请码发给员工加入。</p> : null}
        <div className="room-grid">
          {rooms.map((r) => (
            <button key={r.id} className="room-card" onClick={() => onOpen(r.id)}>
              <strong>{r.name}</strong>
              <span className="hint">
                {r.role === "owner" ? "负责人" : "成员"} · {r.member_count} 人
              </span>
              <span className="room-progress">
                <span className="room-progress__bar" style={{ width: `${Math.round(r.stats.completion * 100)}%` }} />
                <span>
                  {r.stats.done}/{r.stats.total} 完成
                </span>
              </span>
            </button>
          ))}
        </div>
        <div className="row">
          <input placeholder="新房间名称" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="btn btn--primary" disabled={busy || !name.trim()} onClick={() => void create()}>
            创建房间
          </button>
        </div>
        <div className="row">
          <input placeholder="输入邀请码加入房间" value={code} onChange={(e) => setCode(e.target.value)} />
          <button className="btn btn--ghost" disabled={busy || !code.trim()} onClick={() => void join()}>
            凭邀请码加入
          </button>
        </div>
      </div>

      <div className="card">
        <h2>个人设置</h2>
        <div className="row">
          <span className="hint">我的颜色（看板中标识我创建的任务）：</span>
          <span className="color-dot color-dot--lg" style={{ background: me?.color || "#999" }} />
          <button className="btn btn--ghost" onClick={() => setShowColor(!showColor)}>
            修改颜色
          </button>
        </div>
        {showColor ? (
          <div className="row">
            {COLOR_OPTIONS.map((c) => (
              <button
                key={c}
                className={"swatch" + (me?.color === c ? " swatch--on" : "")}
                style={{ background: c }}
                title={c}
                onClick={() =>
                  void apiPatchMe({ color: c })
                    .then((u) => {
                      onMe(u);
                      setShowColor(false);
                    })
                    .catch((e) => onError(e instanceof Error ? e.message : String(e)))
                }
              />
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/* ── 看板页 ────────────────────────────────────────────────── */

function BoardPage({ me, roomId, onBack, onError }: { me: Me | null; roomId: number; onBack: () => void; onError: (s: string) => void }) {
  const [board, setBoard] = useState<BoardPayload | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [viewMode, setViewMode] = useState<"kanban" | "list">("kanban");
  const [filterAssignee, setFilterAssignee] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterMonth, setFilterMonth] = useState("");
  const [drawer, setDrawer] = useState<Task | "new" | null>(null);
  const versionRef = useRef(-1);
  const roomName = useRef("");

  // 5 秒轮询（visibilitychange 隐藏时暂停）
  useEffect(() => {
    let stopped = false;
    const tick = async () => {
      if (stopped || document.hidden) return;
      try {
        const payload = await apiBoard(roomId, versionRef.current);
        if (payload.unchanged) return;
        versionRef.current = payload.version;
        setBoard(payload);
      } catch (e) {
        onError(e instanceof Error ? e.message : String(e));
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 5000);
    const onVis = () => {
      if (!document.hidden) void tick();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [roomId, onError]);

  // 房间名 + 成员（邀请码区）
  useEffect(() => {
    void apiMembers(roomId)
      .then((ms) => setMembers(ms))
      .catch(() => undefined);
    void apiRooms().then((rs) => {
      const r = rs.find((x) => x.id === roomId);
      if (r) roomName.current = r.name;
    });
  }, [roomId]);

  const refreshNow = useCallback(async () => {
    try {
      const payload = await apiBoard(roomId, -1);
      versionRef.current = payload.version;
      setBoard(payload);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  }, [roomId, onError]);

  const myRole = members.find((m) => m.id === me?.id)?.role || "";

  const tasks = useMemo(() => {
    const list = board?.tasks || [];
    return list.filter((t) => {
      if (filterAssignee === "none") {
        if (t.assignee_id !== null) return false;
      } else if (filterAssignee && String(t.assignee_id) !== filterAssignee) {
        return false;
      }
      if (filterStatus && t.status !== filterStatus) return false;
      if (filterMonth && (t.month_tag || "") !== filterMonth) return false;
      return true;
    });
  }, [board, filterAssignee, filterStatus, filterMonth]);

  const stats = board?.stats;
  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      await refreshNow();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="page">
      <div className="board-top card">
        <div className="row row--between">
          <div className="row">
            <button className="btn btn--ghost btn--sm" onClick={onBack}>
              ← 房间
            </button>
            <h2 style={{ margin: 0 }}>{roomName.current || `房间 #${roomId}`}</h2>
          </div>
          <div className="stats">
            <span>完成率 <strong>{Math.round((stats?.completion || 0) * 100)}%</strong></span>
            <span>总计 {stats?.total ?? "—"}</span>
            <span className={stats?.overdue ? "is-warn" : ""}>逾期 {stats?.overdue ?? "—"}</span>
            <span>今日到期 {stats?.due_today ?? "—"}</span>
          </div>
        </div>
        <div className="legend">
          {members.map((m) => (
            <span key={m.id} className="legend__item">
              <span className="color-dot" style={{ background: m.color }} />
              {m.display_name || m.username}
              {m.id === me?.id ? "（我）" : ""}
            </span>
          ))}
        </div>
        {myRole === "owner" ? (
          <OwnerTools roomId={roomId} members={members} onError={onError} onMembers={setMembers} />
        ) : null}
        <div className="toolbar">
          <div className="seg">
            <button className={"seg__b" + (viewMode === "kanban" ? " is-on" : "")} onClick={() => setViewMode("kanban")}>
              看板
            </button>
            <button className={"seg__b" + (viewMode === "list" ? " is-on" : "")} onClick={() => setViewMode("list")}>
              总列表
            </button>
          </div>
          <select className="input" value={filterAssignee} onChange={(e) => setFilterAssignee(e.target.value)}>
            <option value="">全部负责人</option>
            {members.map((m) => (
              <option key={m.id} value={String(m.id)}>
                {m.display_name || m.username}
              </option>
            ))}
            <option value="none">待分配</option>
          </select>
          <select className="input" value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
            <option value="">全部状态</option>
            <option value="todo">待办</option>
            <option value="doing">进行中</option>
            <option value="done">已完成</option>
          </select>
          <input className="input input--month" type="month" value={filterMonth} onChange={(e) => setFilterMonth(e.target.value)} />
          <button className="btn btn--primary" onClick={() => setDrawer("new")}>
            新建待办
          </button>
          <a className="btn btn--ghost" href={`/api/template.xlsx`}>
            下载模板
          </a>
          <a className="btn btn--ghost" href={`/api/rooms/${roomId}/export.xlsx`}>
            导出进度表
          </a>
        </div>
      </div>

      {viewMode === "kanban" ? (
        <div className="kanban">
          {(["todo", "doing", "done"] as const).map((st) => (
            <div key={st} className="kanban__col">
              <h3>
                {STATUS_CN[st]} <span className="hint">{tasks.filter((t) => t.status === st).length}</span>
              </h3>
              <div className="kanban__cards">
                {tasks
                  .filter((t) => t.status === st)
                  .map((t) => (
                    <TaskCard key={t.id} task={t} onClick={() => setDrawer(t)} onAdvance={(s) => void act(() => apiPatchTask(roomId, t.id, { status: s }))} />
                  ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <ListView tasks={tasks} members={members} />
      )}

      {drawer ? (
        <TaskDrawer
          roomId={roomId}
          task={drawer === "new" ? null : drawer}
          members={members}
          onClose={() => setDrawer(null)}
          onSaved={() => {
            setDrawer(null);
            void refreshNow();
          }}
          onError={onError}
        />
      ) : null}
    </div>
  );
}

function OwnerTools({
  roomId,
  members,
  onError,
  onMembers,
}: {
  roomId: number;
  members: Member[];
  onError: (s: string) => void;
  onMembers: (m: Member[]) => void;
}) {
  const [invite, setInvite] = useState("");
  const [showMembers, setShowMembers] = useState(false);

  useEffect(() => {
    void apiMembers(roomId).then(onMembers).catch(() => undefined);
    // 邀请码只经创建返回与重置接口展示（不落前端常驻状态）
  }, [roomId, onMembers]);

  return (
    <div className="owner-tools">
      <button
        className="btn btn--ghost btn--sm"
        onClick={() =>
          void apiRefreshInvite(roomId)
            .then((r) => setInvite(r.invite_code))
            .catch((e) => onError(e instanceof Error ? e.message : String(e)))
        }
      >
        生成/重置邀请码
      </button>
      {invite ? (
        <span className="invite-code" title="发给员工，在房间页输入加入">
          邀请码：<strong>{invite}</strong>（旧码已失效）
        </span>
      ) : null}
      <button className="btn btn--ghost btn--sm" onClick={() => setShowMembers(!showMembers)}>
        成员管理
      </button>
      {showMembers ? (
        <ul className="member-list">
          {members.map((m) => (
            <li key={m.id}>
              <span className="color-dot" style={{ background: m.color }} />
              {m.display_name || m.username}（{m.role === "owner" ? "负责人" : "成员"}）
              {m.role !== "owner" ? (
                <button
                  className="btn btn--ghost btn--sm"
                  onClick={() =>
                    void apiRemoveMember(roomId, m.id)
                      .then(() => apiMembers(roomId))
                      .then(onMembers)
                      .catch((e) => onError(e instanceof Error ? e.message : String(e)))
                  }
                >
                  移除
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function TaskCard({ task, onClick, onAdvance }: { task: Task; onClick: () => void; onAdvance: (s: string) => void }) {
  const overdue = isOverdue(task);
  return (
    <div className={"task-card" + (overdue ? " task-card--overdue" : "")} style={{ borderLeftColor: task.creator_color }}>
      <div className="task-card__head" onClick={onClick} role="button">
        <strong>{task.title}</strong>
      </div>
      <div className="task-card__meta">
        {task.assignee_id ? (
          <span className="assignee">
            <span className="color-dot" style={{ background: task.assignee_color || "#999" }} />
            {task.assignee_name}
          </span>
        ) : (
          <span className="hint">待分配</span>
        )}
        {task.due_date ? <span className={overdue ? "is-warn" : ""}>{fmtDue(task.due_date)}</span> : null}
        <span className={"prio prio--" + task.priority}>{PRIO_CN[task.priority] || task.priority}</span>
        {task.month_tag ? <span className="tag">{task.month_tag}</span> : null}
      </div>
      <div className="task-card__acts">
        {task.status !== "doing" && task.status !== "done" ? (
          <button className="btn btn--ghost btn--xs" onClick={() => onAdvance("doing")}>
            开始
          </button>
        ) : null}
        {task.status !== "done" ? (
          <button className="btn btn--ghost btn--xs" onClick={() => onAdvance("done")}>
            完成
          </button>
        ) : null}
        {task.status === "done" ? (
          <button className="btn btn--ghost btn--xs" onClick={() => onAdvance("todo")}>
            重开
          </button>
        ) : null}
      </div>
      <span className="creator" title={`创建人 ${task.creator_name}`}>
        {task.creator_name}
      </span>
    </div>
  );
}

function ListView({ tasks, members }: { tasks: Task[]; members: Member[] }) {
  const byMemberDone = new Map<number, number>();
  for (const t of tasks) {
    if (t.status === "done" && t.assignee_id) byMemberDone.set(t.assignee_id, (byMemberDone.get(t.assignee_id) || 0) + 1);
  }
  return (
    <div className="card">
      <table className="list-table">
        <thead>
          <tr>
            <th>任务</th>
            <th>负责人</th>
            <th>创建人</th>
            <th>状态</th>
            <th>截止</th>
            <th>优先级</th>
            <th>月份</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((t) => (
            <tr key={t.id}>
              <td className="cell-title" style={{ borderLeft: `3px solid ${t.creator_color}` }}>
                {t.title}
              </td>
              <td>{t.assignee_name || "待分配"}</td>
              <td>{t.creator_name}</td>
              <td>{STATUS_CN[t.status] || t.status}</td>
              <td className={isOverdue(t) ? "is-warn" : ""}>{t.due_date || ""}</td>
              <td>{PRIO_CN[t.priority] || t.priority}</td>
              <td>{t.month_tag || ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="list-summary">
        合计 {tasks.length} 条 · 已完成 {tasks.filter((t) => t.status === "done").length} 条 ·{" "}
        {members
          .filter((m) => byMemberDone.get(m.id))
          .map((m) => `${m.display_name || m.username} ${byMemberDone.get(m.id)}`)
          .join(" · ")}
      </div>
    </div>
  );
}

function TaskDrawer({
  roomId,
  task,
  members,
  onClose,
  onSaved,
  onError,
}: {
  roomId: number;
  task: Task | null;
  members: Member[];
  onClose: () => void;
  onSaved: () => void;
  onError: (s: string) => void;
}) {
  const [title, setTitle] = useState(task?.title || "");
  const [detail, setDetail] = useState(task?.detail || "");
  const [assignee, setAssignee] = useState<string>(task?.assignee_id ? String(task.assignee_id) : "");
  const [status, setStatus] = useState(task?.status || "todo");
  const [due, setDue] = useState(task?.due_date || "");
  const [priority, setPriority] = useState(task?.priority || "mid");
  const [month, setMonth] = useState(task?.month_tag || "");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    if (!title.trim()) {
      onError("任务标题不能为空");
      return;
    }
    setBusy(true);
    const body: Record<string, unknown> = {
      title: title.trim(),
      detail,
      status,
      due_date: due || null,
      priority,
      month_tag: month || null,
      assignee_id: assignee ? Number(assignee) : null,
    };
    try {
      if (task) await apiPatchTask(roomId, task.id, body);
      else await apiCreateTask(roomId, body);
      onSaved();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="drawer-mask" onClick={onClose} role="presentation">
      <div className="drawer" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="任务编辑">
        <h3>{task ? "编辑任务" : "新建待办"}</h3>
        <label className="field">
          <span>标题 *</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
        </label>
        <label className="field">
          <span>详细说明</span>
          <textarea rows={3} value={detail} onChange={(e) => setDetail(e.target.value)} />
        </label>
        <div className="field-grid">
          <label className="field">
            <span>负责人</span>
            <select className="input" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
              <option value="">待分配</option>
              {members.map((m) => (
                <option key={m.id} value={String(m.id)}>
                  {m.display_name || m.username}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>状态</span>
            <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="todo">待办</option>
              <option value="doing">进行中</option>
              <option value="done">已完成</option>
            </select>
          </label>
          <label className="field">
            <span>截止日期</span>
            <input className="input" type="date" value={due} onChange={(e) => setDue(e.target.value)} />
          </label>
          <label className="field">
            <span>优先级</span>
            <select className="input" value={priority} onChange={(e) => setPriority(e.target.value)}>
              <option value="high">高</option>
              <option value="mid">中</option>
              <option value="low">低</option>
            </select>
          </label>
          <label className="field">
            <span>月份归属（对接月度预算）</span>
            <input className="input" type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
          </label>
        </div>
        <div className="row row--end">
          {task ? (
            <button
              className="btn btn--ghost btn--danger"
              disabled={busy}
              onClick={() =>
                void apiDeleteTask(roomId, task.id)
                  .then(onSaved)
                  .catch((e) => onError(e instanceof Error ? e.message : String(e)))
              }
            >
              删除
            </button>
          ) : null}
          <button className="btn btn--ghost" onClick={onClose}>
            取消
          </button>
          <button className="btn btn--primary" disabled={busy} onClick={() => void save()}>
            保存
          </button>
        </div>
        {task ? <p className="hint">创建人 {task.creator_name} · 更新 {task.updated_at?.slice(0, 16).replace("T", " ")}</p> : null}
      </div>
    </div>
  );
}
