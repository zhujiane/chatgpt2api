"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ComponentProps } from "react";
import {
  CheckCircle2,
  CircleAlert,
  CircleOff,
  Copy,
  LoaderCircle,
  Pencil,
  Play,
  RefreshCw,
  Star,
  Terminal,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  deleteCodexUser,
  fetchCodexUsers,
  refreshCodexUser,
  runCodexExec,
  setDefaultCodexUser,
  startCodexLogin,
  updateCodexUser,
  type CodexExecResult,
  type CodexUser,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";

const statusMeta: Record<
  string,
  {
    label: string;
    icon: typeof CheckCircle2;
    badge: ComponentProps<typeof Badge>["variant"];
  }
> = {
  normal: { label: "正常", icon: CheckCircle2, badge: "success" },
  login_pending: { label: "登录中", icon: CircleAlert, badge: "warning" },
  error: { label: "异常", icon: CircleOff, badge: "danger" },
  unknown: { label: "未知", icon: CircleAlert, badge: "secondary" },
};

function metaForStatus(status: string) {
  return statusMeta[status] ?? statusMeta.unknown;
}

function formatUsage(value?: string | number | null) {
  if (value === null || value === undefined || value === "") {
    return "未知";
  }
  return String(value);
}

function formatTime(value?: string | null) {
  return value || "—";
}

function CodexPageContent() {
  const didLoadRef = useRef(false);
  const [users, setUsers] = useState<CodexUser[]>([]);
  const [defaultUserId, setDefaultUserId] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isStartingLogin, setIsStartingLogin] = useState(false);
  const [refreshingId, setRefreshingId] = useState("");
  const [deletingId, setDeletingId] = useState("");
  const [defaultingId, setDefaultingId] = useState("");
  const [editingUser, setEditingUser] = useState<CodexUser | null>(null);
  const [editName, setEditName] = useState("");
  const [editEnabled, setEditEnabled] = useState(true);
  const [isUpdating, setIsUpdating] = useState(false);
  const [execUser, setExecUser] = useState<CodexUser | null>(null);
  const [execPrompt, setExecPrompt] = useState("");
  const [execCwd, setExecCwd] = useState("");
  const [execModel, setExecModel] = useState("");
  const [execSandbox, setExecSandbox] = useState<"read-only" | "workspace-write" | "danger-full-access">(
    "workspace-write",
  );
  const [isExecuting, setIsExecuting] = useState(false);
  const [execResult, setExecResult] = useState<CodexExecResult | null>(null);

  const loadUsers = async (silent = false) => {
    if (!silent) {
      setIsLoading(true);
    }
    try {
      const data = await fetchCodexUsers();
      setUsers(data.items);
      setDefaultUserId(data.default_user_id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "加载 Codex 账号失败";
      toast.error(message);
    } finally {
      if (!silent) {
        setIsLoading(false);
      }
    }
  };

  useEffect(() => {
    if (didLoadRef.current) {
      return;
    }
    didLoadRef.current = true;
    void loadUsers();
  }, []);

  useEffect(() => {
    if (!users.some((user) => user.status === "login_pending")) {
      return;
    }
    const timer = window.setInterval(() => void loadUsers(true), 3000);
    return () => window.clearInterval(timer);
  }, [users]);

  const summary = useMemo(() => {
    const total = users.length;
    const active = users.filter((user) => user.status === "normal" && user.enabled).length;
    const pending = users.filter((user) => user.status === "login_pending").length;
    const errors = users.filter((user) => user.status === "error").length;
    return { total, active, pending, errors };
  }, [users]);

  const defaultUser = users.find((user) => user.id === defaultUserId) ?? null;

  const handleStartLogin = async () => {
    setIsStartingLogin(true);
    try {
      const data = await startCodexLogin({ mode: "browser" });
      setUsers(data.items);
      toast.success(`已唤起 Codex 登录：${data.item.name}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "唤起 Codex 登录失败";
      toast.error(message);
    } finally {
      setIsStartingLogin(false);
    }
  };

  const handleRefresh = async (user: CodexUser) => {
    setRefreshingId(user.id);
    try {
      const data = await refreshCodexUser(user.id);
      setUsers(data.items);
      toast.success("Codex 状态已刷新");
    } catch (error) {
      const message = error instanceof Error ? error.message : "刷新 Codex 状态失败";
      toast.error(message);
    } finally {
      setRefreshingId("");
    }
  };

  const handleSetDefault = async (user: CodexUser) => {
    setDefaultingId(user.id);
    try {
      const data = await setDefaultCodexUser(user.id);
      setUsers(data.items);
      setDefaultUserId(data.default_user_id);
      toast.success("默认 Codex 账号已切换");
    } catch (error) {
      const message = error instanceof Error ? error.message : "切换默认账号失败";
      toast.error(message);
    } finally {
      setDefaultingId("");
    }
  };

  const handleDelete = async (user: CodexUser) => {
    setDeletingId(user.id);
    try {
      const data = await deleteCodexUser(user.id);
      setUsers(data.items);
      setDefaultUserId(data.default_user_id);
      toast.success("Codex 账号已删除");
    } catch (error) {
      const message = error instanceof Error ? error.message : "删除 Codex 账号失败";
      toast.error(message);
    } finally {
      setDeletingId("");
    }
  };

  const openEdit = (user: CodexUser) => {
    setEditingUser(user);
    setEditName(user.name || "");
    setEditEnabled(user.enabled !== false);
  };

  const handleUpdate = async () => {
    if (!editingUser) {
      return;
    }
    setIsUpdating(true);
    try {
      const data = await updateCodexUser(editingUser.id, {
        name: editName,
        enabled: editEnabled,
      });
      setUsers(data.items);
      setEditingUser(null);
      toast.success("Codex 账号已更新");
    } catch (error) {
      const message = error instanceof Error ? error.message : "更新 Codex 账号失败";
      toast.error(message);
    } finally {
      setIsUpdating(false);
    }
  };

  const openExec = (user: CodexUser) => {
    setExecUser(user);
    setExecPrompt("");
    setExecCwd("");
    setExecModel("");
    setExecSandbox("workspace-write");
    setExecResult(null);
  };

  const handleExec = async () => {
    if (!execUser) {
      return;
    }
    if (!execPrompt.trim()) {
      toast.error("请输入要交给 Codex CLI 的任务");
      return;
    }
    setIsExecuting(true);
    setExecResult(null);
    try {
      const data = await runCodexExec({
        user_id: execUser.id,
        prompt: execPrompt,
        cwd: execCwd.trim() || undefined,
        model: execModel.trim() || undefined,
        sandbox: execSandbox,
      });
      setExecResult(data.result);
      toast.success(data.result.return_code === 0 ? "Codex CLI 执行完成" : "Codex CLI 执行结束但返回错误");
      void loadUsers(true);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Codex CLI 执行失败";
      toast.error(message);
    } finally {
      setIsExecuting(false);
    }
  };

  return (
    <>
      <section className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-1">
          <div className="text-xs font-semibold tracking-[0.18em] text-stone-500 uppercase">
            Codex Accounts
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">Codex 管理</h1>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            className="h-10 rounded-xl border-stone-200 bg-white/80 px-4 text-stone-700 hover:bg-white"
            onClick={() => void loadUsers()}
            disabled={isLoading}
          >
            <RefreshCw className={cn("size-4", isLoading ? "animate-spin" : "")} />
            刷新
          </Button>
          <Button
            className="h-10 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800"
            onClick={() => void handleStartLogin()}
            disabled={isStartingLogin}
          >
            {isStartingLogin ? <LoaderCircle className="size-4 animate-spin" /> : <Terminal className="size-4" />}
            Codex login
          </Button>
        </div>
      </section>

      <section className="grid gap-3 md:grid-cols-4">
        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
          <CardContent className="p-4">
            <div className="mb-4 flex items-start justify-between">
              <span className="text-xs font-medium text-stone-400">账号总数</span>
              <Terminal className="size-4 text-stone-400" />
            </div>
            <div className="text-[1.75rem] font-semibold tracking-tight text-stone-900">{summary.total}</div>
          </CardContent>
        </Card>
        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
          <CardContent className="p-4">
            <div className="mb-4 flex items-start justify-between">
              <span className="text-xs font-medium text-stone-400">可用账号</span>
              <CheckCircle2 className="size-4 text-stone-400" />
            </div>
            <div className="text-[1.75rem] font-semibold tracking-tight text-emerald-600">{summary.active}</div>
          </CardContent>
        </Card>
        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
          <CardContent className="p-4">
            <div className="mb-4 flex items-start justify-between">
              <span className="text-xs font-medium text-stone-400">登录中</span>
              <CircleAlert className="size-4 text-stone-400" />
            </div>
            <div className="text-[1.75rem] font-semibold tracking-tight text-orange-500">{summary.pending}</div>
          </CardContent>
        </Card>
        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
          <CardContent className="p-4">
            <div className="mb-4 flex items-start justify-between">
              <span className="text-xs font-medium text-stone-400">默认账号</span>
              <Star className="size-4 text-stone-400" />
            </div>
            <div className="truncate text-[1.1rem] font-semibold tracking-tight text-blue-500">
              {defaultUser?.name || "未设置"}
            </div>
          </CardContent>
        </Card>
      </section>

      <Card className="overflow-hidden rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-0">
          <div className="flex items-center justify-between border-b border-stone-100 px-4 py-3">
            <div className="flex items-center gap-3">
              <h2 className="text-lg font-semibold tracking-tight">Codex 账号列表</h2>
              <Badge variant="secondary" className="rounded-lg bg-stone-200 px-2 py-0.5 text-stone-700">
                {users.length}
              </Badge>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-left">
              <thead className="border-b border-stone-100 text-[11px] text-stone-400 uppercase tracking-[0.18em]">
                <tr>
                  <th className="w-56 px-4 py-3">账号</th>
                  <th className="w-24 px-4 py-3">状态</th>
                  <th className="w-24 px-4 py-3">默认</th>
                  <th className="w-24 px-4 py-3">5h 剩余</th>
                  <th className="w-24 px-4 py-3">7d 剩余</th>
                  <th className="w-44 px-4 py-3">最后检查</th>
                  <th className="w-44 px-4 py-3">最后使用</th>
                  <th className="w-48 px-4 py-3">操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => {
                  const status = metaForStatus(user.status);
                  const StatusIcon = status.icon;
                  const isDefault = user.id === defaultUserId || user.is_default;
                  return (
                    <tr
                      key={user.id}
                      className="border-b border-stone-100/80 text-sm text-stone-600 transition-colors hover:bg-stone-50/70"
                    >
                      <td className="px-4 py-3">
                        <div className="space-y-1">
                          <div className="font-medium text-stone-800">{user.name || user.id}</div>
                          <div className="flex items-center gap-2 text-xs text-stone-400">
                            <span>{user.id}</span>
                            <button
                              type="button"
                              className="rounded-lg p-1 transition hover:bg-stone-100 hover:text-stone-700"
                              onClick={() => {
                                void navigator.clipboard.writeText(user.id);
                                toast.success("账号 ID 已复制");
                              }}
                              aria-label="复制账号 ID"
                              title="复制账号 ID"
                            >
                              <Copy className="size-3.5" />
                            </button>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant={status.badge} className="inline-flex items-center gap-1 rounded-md px-2 py-1">
                          <StatusIcon className="size-3.5" />
                          {status.label}
                        </Badge>
                      </td>
                      <td className="px-4 py-3">
                        {isDefault ? (
                          <Badge variant="info" className="inline-flex items-center gap-1 rounded-md">
                            <Star className="size-3.5" />
                            默认
                          </Badge>
                        ) : (
                          <span className="text-stone-400">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant="secondary" className="rounded-md bg-stone-100 text-stone-700">
                          {formatUsage(user.usage?.five_hour_remaining)}
                        </Badge>
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant="secondary" className="rounded-md bg-stone-100 text-stone-700">
                          {formatUsage(user.usage?.seven_day_remaining)}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-xs leading-5 text-stone-500">
                        {formatTime(user.last_status_checked_at)}
                      </td>
                      <td className="px-4 py-3 text-xs leading-5 text-stone-500">{formatTime(user.last_used_at)}</td>
                      <td className="px-4 py-3">
                        <div className="flex min-w-44 flex-nowrap items-center gap-1 text-stone-400">
                          <button
                            type="button"
                            className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg transition hover:bg-stone-100 hover:text-stone-700 disabled:cursor-not-allowed disabled:opacity-40"
                            onClick={() => void handleSetDefault(user)}
                            disabled={isDefault || defaultingId === user.id || !user.auth_file_exists}
                            title="设为默认"
                            aria-label="设为默认"
                          >
                            {defaultingId === user.id ? <LoaderCircle className="size-4 animate-spin" /> : <Star className="size-4" />}
                          </button>
                          <button
                            type="button"
                            className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg transition hover:bg-stone-100 hover:text-stone-700"
                            onClick={() => void handleRefresh(user)}
                            disabled={refreshingId === user.id || !user.auth_file_exists}
                            title="刷新状态"
                            aria-label="刷新状态"
                          >
                            <RefreshCw className={cn("size-4", refreshingId === user.id ? "animate-spin" : "")} />
                          </button>
                          <button
                            type="button"
                            className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg transition hover:bg-stone-100 hover:text-stone-700 disabled:cursor-not-allowed disabled:opacity-40"
                            onClick={() => openExec(user)}
                            disabled={!user.auth_file_exists || user.enabled === false}
                            title="用此账号运行 Codex CLI"
                            aria-label="用此账号运行 Codex CLI"
                          >
                            <Play className="size-4" />
                          </button>
                          <button
                            type="button"
                            className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg transition hover:bg-stone-100 hover:text-stone-700"
                            onClick={() => openEdit(user)}
                            title="编辑"
                            aria-label="编辑"
                          >
                            <Pencil className="size-4" />
                          </button>
                          <button
                            type="button"
                            className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg transition hover:bg-rose-50 hover:text-rose-500"
                            onClick={() => void handleDelete(user)}
                            disabled={deletingId === user.id}
                            title="删除"
                            aria-label="删除"
                          >
                            {deletingId === user.id ? <LoaderCircle className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {!isLoading && users.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
                <div className="rounded-xl bg-stone-100 p-3 text-stone-500">
                  <Terminal className="size-5" />
                </div>
                <div className="space-y-1">
                  <p className="text-sm font-medium text-stone-700">还没有 Codex 账号</p>
                  <p className="text-sm text-stone-500">点击 Codex login 后，在弹出的 OpenAI 授权页面完成登录。</p>
                </div>
              </div>
            ) : null}
          </div>
        </CardContent>
      </Card>

      <Dialog open={Boolean(editingUser)} onOpenChange={(open) => (!open ? setEditingUser(null) : null)}>
        <DialogContent showCloseButton={false} className="rounded-2xl p-6">
          <DialogHeader className="gap-2">
            <DialogTitle>编辑 Codex 账号</DialogTitle>
            <DialogDescription className="text-sm leading-6">修改账号名称和启用状态。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium text-stone-700">名称</label>
              <Input
                value={editName}
                onChange={(event) => setEditName(event.target.value)}
                className="h-11 rounded-xl border-stone-200 bg-white"
              />
            </div>
            <label className="flex items-center gap-2 text-sm font-medium text-stone-700">
              <Checkbox checked={editEnabled} onCheckedChange={(checked) => setEditEnabled(Boolean(checked))} />
              启用此账号
            </label>
          </div>
          <DialogFooter className="pt-2">
            <Button
              variant="secondary"
              className="h-10 rounded-xl bg-stone-100 px-5 text-stone-700 hover:bg-stone-200"
              onClick={() => setEditingUser(null)}
              disabled={isUpdating}
            >
              取消
            </Button>
            <Button
              className="h-10 rounded-xl bg-stone-950 px-5 text-white hover:bg-stone-800"
              onClick={() => void handleUpdate()}
              disabled={isUpdating}
            >
              {isUpdating ? <LoaderCircle className="size-4 animate-spin" /> : null}
              保存修改
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(execUser)} onOpenChange={(open) => (!open ? setExecUser(null) : null)}>
        <DialogContent showCloseButton={false} className="max-w-3xl rounded-2xl p-6">
          <DialogHeader className="gap-2">
            <DialogTitle>运行 Codex CLI</DialogTitle>
            <DialogDescription className="text-sm leading-6">
              当前账号：{execUser?.name || execUser?.id || "—"}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium text-stone-700">任务</label>
              <Textarea
                value={execPrompt}
                onChange={(event) => setExecPrompt(event.target.value)}
                className="min-h-28 rounded-xl border-stone-200 bg-white"
                placeholder="输入要交给 codex exec 的任务"
              />
            </div>
            <div className="grid gap-3 md:grid-cols-[1fr_160px_160px]">
              <div className="space-y-2">
                <label className="text-sm font-medium text-stone-700">工作目录</label>
                <Input
                  value={execCwd}
                  onChange={(event) => setExecCwd(event.target.value)}
                  className="h-11 rounded-xl border-stone-200 bg-white"
                  placeholder="留空使用当前项目目录"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-stone-700">模型</label>
                <Input
                  value={execModel}
                  onChange={(event) => setExecModel(event.target.value)}
                  className="h-11 rounded-xl border-stone-200 bg-white"
                  placeholder="默认"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-stone-700">沙箱</label>
                <Select value={execSandbox} onValueChange={(value) => setExecSandbox(value as typeof execSandbox)}>
                  <SelectTrigger className="h-11 rounded-xl border-stone-200 bg-white">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="workspace-write">workspace-write</SelectItem>
                    <SelectItem value="read-only">read-only</SelectItem>
                    <SelectItem value="danger-full-access">danger-full-access</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            {execResult ? (
              <div className="space-y-2 rounded-xl border border-stone-100 bg-stone-50 p-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-stone-500">
                  <Badge variant={execResult.return_code === 0 ? "success" : "danger"} className="rounded-md">
                    exit {execResult.return_code}
                  </Badge>
                  <span>{execResult.started_at} - {execResult.finished_at}</span>
                </div>
                <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-white p-3 text-xs leading-5 text-stone-700">
                  {[execResult.stdout, execResult.stderr].filter(Boolean).join("\n")}
                </pre>
              </div>
            ) : null}
          </div>
          <DialogFooter className="pt-2">
            <Button
              variant="secondary"
              className="h-10 rounded-xl bg-stone-100 px-5 text-stone-700 hover:bg-stone-200"
              onClick={() => setExecUser(null)}
              disabled={isExecuting}
            >
              关闭
            </Button>
            <Button
              className="h-10 rounded-xl bg-stone-950 px-5 text-white hover:bg-stone-800"
              onClick={() => void handleExec()}
              disabled={isExecuting}
            >
              {isExecuting ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}
              运行
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

export default function CodexPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);

  if (isCheckingAuth || !session || session.role !== "admin") {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return <CodexPageContent />;
}
