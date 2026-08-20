// 协同看板 · 类型定义
export interface Me {
  id: number;
  username: string;
  display_name: string;
  color: string;
}

export interface Member {
  id: number;
  username: string;
  display_name: string;
  color: string;
  role: "owner" | "member" | string;
}

export interface Task {
  id: number;
  title: string;
  detail: string;
  status: "todo" | "doing" | "done" | string;
  priority: "high" | "mid" | "low" | string;
  due_date: string | null;
  month_tag: string | null;
  creator_id: number;
  creator_name: string;
  creator_color: string;
  assignee_id: number | null;
  assignee_name: string;
  assignee_color: string | null;
  updated_at: string | null;
  completed_at: string | null;
  completed_by: number | null;
}

export interface BoardStats {
  total: number;
  done: number;
  doing: number;
  overdue: number;
  due_today: number;
  completion: number;
}

export interface BoardPayload {
  unchanged: boolean;
  version: number;
  tasks: Task[];
  members: Member[];
  stats: BoardStats;
}

export interface RoomCard {
  id: number;
  name: string;
  role: string;
  version: number;
  member_count: number;
  stats: { total: number; done: number; completion: number };
}
