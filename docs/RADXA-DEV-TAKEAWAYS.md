# Radxa ROCK 4D — NanoClaw 开发备忘录

> 首次部署全程 takeaway（2026-02-27）
> 目的：下次开发时快速回忆架构、避免踩坑

---

## 一、开发工作流

### 1. 改完代码必须重新 build + 重启

NanoClaw 用 TypeScript 编写，运行时读的是 `dist/` 里的编译产物，不是 `src/`。
**每次修改 `src/*.ts` 后：**

```bash
cd /home/radxa/nanoclaw
npm run build                                # tsc 编译
systemctl --user restart nanoclaw.service     # 重启服务
journalctl --user -u nanoclaw -f             # 查看日志确认启动正常
```

如果只改了 Python 脚本（`scripts/*.py`）或 `CLAUDE.md`，不需要 build，但仍需重启服务让 IPC watcher 重载。

### 2. 改 CLAUDE.md 后要让对话生效

`CLAUDE.md` 在 Docker 容器启动时挂载。改了以后：
- 新对话会自动读取最新内容
- 已有的活跃容器**不会**自动刷新，需要等当前会话超时（`IDLE_TIMEOUT`）后容器销毁，下次对话才会用新内容
- 急需生效时可手动 kill 容器：`docker kill $(docker ps -q --filter name=nanoclaw)`

### 3. Git 提交流程

```bash
cd /home/radxa/nanoclaw
git add -A
git commit -m "描述信息"
git push origin main    # SSH remote: git@github.com:yang-hong/nanoclaw.git
```

SSH key 已配置在板子上（`~/.ssh/id_ed25519`），public key 已添加到 GitHub。

---

## 二、架构要点

### 4. Host ↔ Container 通信 = IPC 文件

Docker 容器内的 Claude **无法** 直接操作硬件（摄像头、NPU、GPIO）。
所有硬件操作走 IPC：

```
Claude (容器内)  →  写 JSON 到 /workspace/ipc/tasks/  →  ipc.ts (host)  →  硬件
```

IPC 轮询间隔由 `.env` 中的 `IPC_POLL_INTERVAL` 控制（默认 1000ms）。

### 5. IPC 任务类型一览

| 任务类型 | 文件 | 功能 |
|---------|------|------|
| `capture_photo` | `src/ipc.ts` | USB 摄像头拍照（fswebcam） |
| `capture_and_detect` | `src/ipc.ts` | 拍照 + YOLOv5s NPU 推理 |
| `start_monitor` | `src/ipc.ts` | 启动持续监控守护进程 |
| `stop_monitor` | `src/ipc.ts` | 停止监控守护进程 |
| `send_image` | `src/ipc.ts` | 直接发送图片到 WhatsApp |
| `send_message` | `src/ipc.ts` | 直接发送文本消息 |
| `schedule_task` | `src/ipc.ts` | 创建定时任务 |
| `register_group` | `src/ipc.ts` | 注册新的聊天组 |

### 6. 高频任务绕过 Claude

NanoClaw 的定时任务每次启动一个完整 Docker 容器 + Claude 推理（~40s）。
需要 **10 秒级** 响应的任务，用 `monitor.py` 模式：

```
Claude 发 start_monitor IPC → ipc.ts 启动 Python 守护进程 → 直接操作硬件 + WhatsApp
```

`monitor.py` 独立运行在 host，循环：拍照 → YOLO 推理 → 有目标就 send_image → 等待间隔。
单次循环 ~3s，远快于经过 Claude 的 ~40s。

---

## 三、踩坑记录

### 7. USB 摄像头过曝

**现象：** `fswebcam` 拍出来全白  
**原因：** USB 摄像头刚启动时 auto-exposure 没稳定就抓帧了  
**修复：** 加 `-S 20`（跳过 20 帧让曝光稳定）

```bash
fswebcam -r 1920x1080 --no-banner -S 20 /tmp/photo.jpg
```

### 8. RKNN 运行时污染 stdout

**现象：** `yolo-detect.py` 输出的 JSON 被 RKNN C 库的日志行破坏，`JSON.parse` 失败  
**修复（双层防护）：**

Python 端——RKNN 初始化期间把 fd 1 重定向到 /dev/null：
```python
devnull_fd = os.open(os.devnull, os.O_WRONLY)
old_stdout_fd = os.dup(1)
os.dup2(devnull_fd, 1)
# ... RKNN load / inference ...
os.dup2(old_stdout_fd, 1)  # 恢复
```

TypeScript 端——只解析以 `{` 开头的那一行：
```typescript
const jsonLine = result.stdout.split('\n').find(l => l.trimStart().startsWith('{'));
```

### 9. 子进程要有可见日志

**现象：** `monitor.py` 以 `stdio: 'ignore'` 启动，失败了毫无痕迹  
**修复：** 写日志文件 + Python 无缓冲

```typescript
const logFd = fs.openSync('logs/monitor.log', 'a');
spawn('python3', ['-u', 'scripts/monitor.py'], {
  detached: true,
  stdio: ['ignore', logFd, logFd],
});
```

### 10. Session 损坏导致容器无限重启

**现象：** Claude Code 容器反复 exit code 1，尝试恢复一个已损坏的 session  
**修复步骤：**

```bash
# 1. 杀掉容器
docker kill $(docker ps -q --filter name=nanoclaw-friend)

# 2. 清数据库里的 session 记录
sqlite3 store/messages.db "DELETE FROM sessions WHERE group_folder = 'friend'"

# 3. 清 session 文件
rm -rf data/sessions/friend/.claude

# 4. 复制 agent runner 源码（否则 TS18003: No inputs）
cp data/sessions/main/agent-runner-src/*.ts data/sessions/friend/agent-runner-src/

# 5. 重启
systemctl --user restart nanoclaw.service
```

### 11. 新用户/新组注册 checklist

1. 注册组（IPC `register_group` 或直接写数据库）
2. 创建 `groups/<folder>/CLAUDE.md`（从 `groups/main/CLAUDE.md` 复制，**替换 chatJid**）
3. 确保 `data/sessions/<folder>/` 存在且权限 `777`
4. `agent-runner-src/*.ts` 需要从 main 复制过来
5. 在 `ipc.ts` 中检查权限逻辑允许新组的 IPC 调用

### 12. 响应计时器实现

在 `src/index.ts` 使用 `lastInputTime: Record<string, number>` 记录每个 chat 的最后输入时间。
在 `onOutput` 回调中计算 `Date.now() - lastInputTime[chatJid]`。

**坑：** 不能用一个单一的 `t0` 变量，因为暖容器会跨多条消息复用，导致时间一直累加。
必须以 chatJid 为 key，并在 `startMessageLoop` 中 pipe 新消息时也更新时间戳。

---

## 四、关键文件速查

```
nanoclaw/
├── .env                        # 主配置（API token、model、poll intervals）
├── start.sh                    # systemd 启动脚本（sg docker 包裹）
├── src/
│   ├── index.ts                # 主入口（消息循环、响应计时器）
│   ├── ipc.ts                  # IPC 任务处理（所有硬件操作入口）
│   └── channels/whatsapp.ts    # WhatsApp 通道（sendMessage、sendImage、transcribeAudio）
├── scripts/
│   ├── yolo-detect.py          # 单帧 YOLO 检测（NPU）
│   └── monitor.py              # 持续监控守护进程
├── groups/
│   ├── main/CLAUDE.md          # 主用户的 agent 记忆
│   ├── friend/CLAUDE.md        # 朋友的 agent 记忆
│   └── global/CLAUDE.md        # 共享基础配置
├── .claude/skills/
│   ├── add-camera/             # Skill: USB 拍照
│   ├── add-yolo-detect/        # Skill: NPU YOLO 检测
│   └── add-monitor/            # Skill: 持续监控
├── store/messages.db           # SQLite 数据库（sessions、groups）
├── data/sessions/              # 各组 Docker 容器的持久化数据
└── logs/
    └── monitor.log             # 监控守护进程日志
```

---

## 五、常用命令

```bash
# 查看 nanoclaw 运行状态和日志
systemctl --user status nanoclaw.service
journalctl --user -u nanoclaw -f
journalctl --user -u nanoclaw --since "5 min ago"

# 重新编译并重启
cd /home/radxa/nanoclaw && npm run build && systemctl --user restart nanoclaw.service

# 查看活跃的 Docker 容器
docker ps --filter name=nanoclaw

# 手动强制清理所有 nanoclaw 容器
docker kill $(docker ps -q --filter name=nanoclaw) 2>/dev/null

# 监控守护进程日志
tail -f /home/radxa/nanoclaw/logs/monitor.log

# 手动拍照测试
fswebcam -r 1920x1080 --no-banner -S 20 /tmp/test.jpg

# 手动 YOLO 测试
python3 scripts/yolo-detect.py --camera /dev/video0

# 查看 NPU 状态
cat /sys/kernel/debug/rknpu/load

# 查看温度
cat /sys/class/thermal/thermal_zone0/temp  # 除以 1000 得摄氏度

# 数据库查询
sqlite3 store/messages.db "SELECT * FROM registered_groups"
sqlite3 store/messages.db "SELECT group_folder, session_id FROM sessions"
```

---

## 六、性能参考

| 操作 | 耗时 | 瓶颈 |
|------|------|------|
| 纯文本回复 | ~12-22s | Claude API 推理 |
| fswebcam 拍照（-S 20） | ~1.4s | 帧跳过 warmup |
| YOLO NPU 推理 | ~1.5s | RKNN 模型首次加载 |
| Monitor 单循环（稳态） | ~3s | 拍照 + 推理 |
| Docker 容器冷启动 | ~5-8s | 镜像初始化 |

**最大瓶颈：Claude API 延迟（~10-15s）。** 重复性任务用 monitor.py 绕过 Claude 是最有效的优化。

---

## 七、下次开发建议

1. **改完 `src/*.ts` 后永远记得 `npm run build && systemctl --user restart nanoclaw.service`**
2. 新增 IPC 任务类型时，同步更新 `CLAUDE.md` 让 agent 知道怎么调用
3. 写 Skill 而不是直接改源码——方便回溯和复用
4. 大模型文件（`.rknn`、`.so`）已放在 GitHub repo 里，不超过 ~25MB 没问题
5. 多用户权限问题排查顺序：`CLAUDE.md` 指令 → `ipc.ts` 权限检查 → 数据库 registered_groups → 文件权限
