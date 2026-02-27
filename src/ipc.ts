import { ChildProcess, execSync, spawn, spawnSync } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';

import { CronExpressionParser } from 'cron-parser';

import {
  DATA_DIR,
  IPC_POLL_INTERVAL,
  MAIN_GROUP_FOLDER,
  TIMEZONE,
} from './config.js';
import { AvailableGroup } from './container-runner.js';
import { createTask, deleteTask, getTaskById, updateTask } from './db.js';
import { isValidGroupFolder } from './group-folder.js';
import { logger } from './logger.js';
import { RegisteredGroup } from './types.js';

export interface IpcDeps {
  sendMessage: (jid: string, text: string) => Promise<void>;
  sendImage: (jid: string, imagePath: string, caption?: string) => Promise<void>;
  registeredGroups: () => Record<string, RegisteredGroup>;
  registerGroup: (jid: string, group: RegisteredGroup) => void;
  syncGroupMetadata: (force: boolean) => Promise<void>;
  getAvailableGroups: () => AvailableGroup[];
  writeGroupsSnapshot: (
    groupFolder: string,
    isMain: boolean,
    availableGroups: AvailableGroup[],
    registeredJids: Set<string>,
  ) => void;
}

let ipcWatcherRunning = false;
let monitorProcess: ChildProcess | null = null;
const MONITOR_CONFIG = '/tmp/nanoclaw-monitor.json';
const MONITOR_SCRIPT = path.join(process.cwd(), 'scripts', 'monitor.py');

export function startIpcWatcher(deps: IpcDeps): void {
  if (ipcWatcherRunning) {
    logger.debug('IPC watcher already running, skipping duplicate start');
    return;
  }
  ipcWatcherRunning = true;

  const ipcBaseDir = path.join(DATA_DIR, 'ipc');
  fs.mkdirSync(ipcBaseDir, { recursive: true });

  const processIpcFiles = async () => {
    // Scan all group IPC directories (identity determined by directory)
    let groupFolders: string[];
    try {
      groupFolders = fs.readdirSync(ipcBaseDir).filter((f) => {
        const stat = fs.statSync(path.join(ipcBaseDir, f));
        return stat.isDirectory() && f !== 'errors';
      });
    } catch (err) {
      logger.error({ err }, 'Error reading IPC base directory');
      setTimeout(processIpcFiles, IPC_POLL_INTERVAL);
      return;
    }

    const registeredGroups = deps.registeredGroups();

    for (const sourceGroup of groupFolders) {
      const isMain = sourceGroup === MAIN_GROUP_FOLDER;
      const messagesDir = path.join(ipcBaseDir, sourceGroup, 'messages');
      const tasksDir = path.join(ipcBaseDir, sourceGroup, 'tasks');

      // Process messages from this group's IPC directory
      try {
        if (fs.existsSync(messagesDir)) {
          const messageFiles = fs
            .readdirSync(messagesDir)
            .filter((f) => f.endsWith('.json'));
          for (const file of messageFiles) {
            const filePath = path.join(messagesDir, file);
            try {
              const data = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
              if (data.type === 'message' && data.chatJid && data.text) {
                // Authorization: verify this group can send to this chatJid
                const targetGroup = registeredGroups[data.chatJid];
                if (
                  isMain ||
                  (targetGroup && targetGroup.folder === sourceGroup)
                ) {
                  await deps.sendMessage(data.chatJid, data.text);
                  logger.info(
                    { chatJid: data.chatJid, sourceGroup },
                    'IPC message sent',
                  );
                } else {
                  logger.warn(
                    { chatJid: data.chatJid, sourceGroup },
                    'Unauthorized IPC message attempt blocked',
                  );
                }
              }
              fs.unlinkSync(filePath);
            } catch (err) {
              logger.error(
                { file, sourceGroup, err },
                'Error processing IPC message',
              );
              const errorDir = path.join(ipcBaseDir, 'errors');
              fs.mkdirSync(errorDir, { recursive: true });
              fs.renameSync(
                filePath,
                path.join(errorDir, `${sourceGroup}-${file}`),
              );
            }
          }
        }
      } catch (err) {
        logger.error(
          { err, sourceGroup },
          'Error reading IPC messages directory',
        );
      }

      // Process tasks from this group's IPC directory
      try {
        if (fs.existsSync(tasksDir)) {
          const taskFiles = fs
            .readdirSync(tasksDir)
            .filter((f) => f.endsWith('.json'));
          for (const file of taskFiles) {
            const filePath = path.join(tasksDir, file);
            try {
              const data = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
              // Pass source group identity to processTaskIpc for authorization
              await processTaskIpc(data, sourceGroup, isMain, deps);
              fs.unlinkSync(filePath);
            } catch (err) {
              logger.error(
                { file, sourceGroup, err },
                'Error processing IPC task',
              );
              const errorDir = path.join(ipcBaseDir, 'errors');
              fs.mkdirSync(errorDir, { recursive: true });
              fs.renameSync(
                filePath,
                path.join(errorDir, `${sourceGroup}-${file}`),
              );
            }
          }
        }
      } catch (err) {
        logger.error({ err, sourceGroup }, 'Error reading IPC tasks directory');
      }
    }

    setTimeout(processIpcFiles, IPC_POLL_INTERVAL);
  };

  processIpcFiles();
  logger.info('IPC watcher started (per-group namespaces)');
}

export async function processTaskIpc(
  data: {
    type: string;
    taskId?: string;
    prompt?: string;
    schedule_type?: string;
    schedule_value?: string;
    context_mode?: string;
    groupFolder?: string;
    chatJid?: string;
    targetJid?: string;
    // For register_group
    jid?: string;
    name?: string;
    folder?: string;
    trigger?: string;
    requiresTrigger?: boolean;
    containerConfig?: RegisteredGroup['containerConfig'];
  },
  sourceGroup: string, // Verified identity from IPC directory
  isMain: boolean, // Verified from directory path
  deps: IpcDeps,
): Promise<void> {
  const registeredGroups = deps.registeredGroups();

  switch (data.type) {
    case 'schedule_task':
      if (
        data.prompt &&
        data.schedule_type &&
        data.schedule_value &&
        data.targetJid
      ) {
        // Resolve the target group from JID
        const targetJid = data.targetJid as string;
        const targetGroupEntry = registeredGroups[targetJid];

        if (!targetGroupEntry) {
          logger.warn(
            { targetJid },
            'Cannot schedule task: target group not registered',
          );
          break;
        }

        const targetFolder = targetGroupEntry.folder;

        // Authorization: non-main groups can only schedule for themselves
        if (!isMain && targetFolder !== sourceGroup) {
          logger.warn(
            { sourceGroup, targetFolder },
            'Unauthorized schedule_task attempt blocked',
          );
          break;
        }

        const scheduleType = data.schedule_type as 'cron' | 'interval' | 'once';

        let nextRun: string | null = null;
        if (scheduleType === 'cron') {
          try {
            const interval = CronExpressionParser.parse(data.schedule_value, {
              tz: TIMEZONE,
            });
            nextRun = interval.next().toISOString();
          } catch {
            logger.warn(
              { scheduleValue: data.schedule_value },
              'Invalid cron expression',
            );
            break;
          }
        } else if (scheduleType === 'interval') {
          const ms = parseInt(data.schedule_value, 10);
          if (isNaN(ms) || ms <= 0) {
            logger.warn(
              { scheduleValue: data.schedule_value },
              'Invalid interval',
            );
            break;
          }
          nextRun = new Date(Date.now() + ms).toISOString();
        } else if (scheduleType === 'once') {
          const scheduled = new Date(data.schedule_value);
          if (isNaN(scheduled.getTime())) {
            logger.warn(
              { scheduleValue: data.schedule_value },
              'Invalid timestamp',
            );
            break;
          }
          nextRun = scheduled.toISOString();
        }

        const taskId = `task-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        const contextMode =
          data.context_mode === 'group' || data.context_mode === 'isolated'
            ? data.context_mode
            : 'isolated';
        createTask({
          id: taskId,
          group_folder: targetFolder,
          chat_jid: targetJid,
          prompt: data.prompt,
          schedule_type: scheduleType,
          schedule_value: data.schedule_value,
          context_mode: contextMode,
          next_run: nextRun,
          status: 'active',
          created_at: new Date().toISOString(),
        });
        logger.info(
          { taskId, sourceGroup, targetFolder, contextMode },
          'Task created via IPC',
        );
      }
      break;

    case 'pause_task':
      if (data.taskId) {
        const task = getTaskById(data.taskId);
        if (task && (isMain || task.group_folder === sourceGroup)) {
          updateTask(data.taskId, { status: 'paused' });
          logger.info(
            { taskId: data.taskId, sourceGroup },
            'Task paused via IPC',
          );
        } else {
          logger.warn(
            { taskId: data.taskId, sourceGroup },
            'Unauthorized task pause attempt',
          );
        }
      }
      break;

    case 'resume_task':
      if (data.taskId) {
        const task = getTaskById(data.taskId);
        if (task && (isMain || task.group_folder === sourceGroup)) {
          updateTask(data.taskId, { status: 'active' });
          logger.info(
            { taskId: data.taskId, sourceGroup },
            'Task resumed via IPC',
          );
        } else {
          logger.warn(
            { taskId: data.taskId, sourceGroup },
            'Unauthorized task resume attempt',
          );
        }
      }
      break;

    case 'cancel_task':
      if (data.taskId) {
        const task = getTaskById(data.taskId);
        if (task && (isMain || task.group_folder === sourceGroup)) {
          deleteTask(data.taskId);
          logger.info(
            { taskId: data.taskId, sourceGroup },
            'Task cancelled via IPC',
          );
        } else {
          logger.warn(
            { taskId: data.taskId, sourceGroup },
            'Unauthorized task cancel attempt',
          );
        }
      }
      break;

    case 'refresh_groups':
      // Only main group can request a refresh
      if (isMain) {
        logger.info(
          { sourceGroup },
          'Group metadata refresh requested via IPC',
        );
        await deps.syncGroupMetadata(true);
        // Write updated snapshot immediately
        const availableGroups = deps.getAvailableGroups();
        deps.writeGroupsSnapshot(
          sourceGroup,
          true,
          availableGroups,
          new Set(Object.keys(registeredGroups)),
        );
      } else {
        logger.warn(
          { sourceGroup },
          'Unauthorized refresh_groups attempt blocked',
        );
      }
      break;

    case 'register_group':
      // Only main group can register new groups
      if (!isMain) {
        logger.warn(
          { sourceGroup },
          'Unauthorized register_group attempt blocked',
        );
        break;
      }
      if (data.jid && data.name && data.folder && data.trigger) {
        if (!isValidGroupFolder(data.folder)) {
          logger.warn(
            { sourceGroup, folder: data.folder },
            'Invalid register_group request - unsafe folder name',
          );
          break;
        }
        deps.registerGroup(data.jid, {
          name: data.name,
          folder: data.folder,
          trigger: data.trigger,
          added_at: new Date().toISOString(),
          containerConfig: data.containerConfig,
          requiresTrigger: data.requiresTrigger,
        });
      } else {
        logger.warn(
          { data },
          'Invalid register_group request - missing required fields',
        );
      }
      break;

    case 'capture_photo': {
      // Capture photo from USB camera and send to WhatsApp
      const targetJid = data.chatJid;
      if (!targetJid) {
        logger.warn({ data }, 'capture_photo: missing chatJid');
        break;
      }
      const targetGroup = registeredGroups[targetJid];
      if (!isMain && (!targetGroup || targetGroup.folder !== sourceGroup)) {
        logger.warn({ sourceGroup, targetJid }, 'Unauthorized capture_photo blocked');
        break;
      }

      const device = '/dev/video0';
      const tmpPath = path.join(os.tmpdir(), `nanoclaw-photo-${Date.now()}.jpg`);
      const caption = (data as { caption?: string }).caption;

      try {
        logger.info({ device, tmpPath }, 'Capturing photo from camera');
        execSync(
          `fswebcam -d ${device} -r 1280x720 -S 20 --jpeg 95 --no-banner "${tmpPath}"`,
          { timeout: 30000 },
        );
        await deps.sendImage(targetJid, tmpPath, caption ?? '📷');
        logger.info({ targetJid }, 'Photo sent via WhatsApp');
      } catch (err) {
        logger.error({ err }, 'Failed to capture or send photo');
        await deps.sendMessage(targetJid, '📷 拍照失败，请确认摄像头已连接。').catch(() => {});
      } finally {
        try { fs.unlinkSync(tmpPath); } catch { /* ignore */ }
      }
      break;
    }

    case 'capture_and_detect': {
      // Capture a photo then run YOLO detection via NPU, send annotated result to WhatsApp
      const targetJid = data.chatJid;
      if (!targetJid) {
        logger.warn({ data }, 'capture_and_detect: missing chatJid');
        break;
      }
      const targetGroup = registeredGroups[targetJid];
      if (!isMain && (!targetGroup || targetGroup.folder !== sourceGroup)) {
        logger.warn({ sourceGroup, targetJid }, 'Unauthorized capture_and_detect blocked');
        break;
      }

      const device = '/dev/video0';
      const ts = Date.now();
      const tmpPhoto      = path.join(os.tmpdir(), `nanoclaw-photo-${ts}.jpg`);
      const tmpAnnotated  = path.join(os.tmpdir(), `nanoclaw-annotated-${ts}.jpg`);
      const userCaption   = (data as { caption?: string }).caption;

      try {
        // Step 1: capture frame
        logger.info({ device, tmpPhoto }, 'Capturing photo for YOLO detection');
        execSync(
          `fswebcam -d ${device} -r 1280x720 -S 20 --jpeg 95 --no-banner "${tmpPhoto}"`,
          { timeout: 30000 },
        );

        // Step 2: run YOLO detection script (blocking, up to 30 s for NPU load + inference)
        logger.info({ tmpPhoto }, 'Running YOLO detection');
        const yoloResult = spawnSync(
          'python3',
          [
            'scripts/yolo-detect.py',
            '--image',    tmpPhoto,
            '--annotate', tmpAnnotated,
            '--conf',     '0.5',
          ],
          { cwd: process.cwd(), timeout: 30000, encoding: 'utf-8' },
        );

        let caption = userCaption ?? '🔍 YOLO detection';
        let imageToSend = tmpPhoto;   // fall back to raw photo if YOLO fails

        if (yoloResult.status === 0 && yoloResult.stdout) {
          try {
            // RKNN runtime may print log lines to stdout; extract the JSON line only
            const jsonLine = yoloResult.stdout.split('\n').find((l: string) => l.trimStart().startsWith('{'));
            const parsed = JSON.parse(jsonLine || '{}') as {
              success: boolean;
              count: number;
              detections: Array<{ label: string; confidence: number }>;
              annotated_image: string | null;
            };

            if (parsed.success) {
              imageToSend = parsed.annotated_image ?? tmpPhoto;

              // Build a compact label summary (cap at 10 entries)
              const summary = parsed.detections
                .slice(0, 10)
                .map((d) => `• ${d.label} (${(d.confidence * 100).toFixed(0)}%)`)
                .join('\n');

              caption =
                parsed.count === 0
                  ? `${userCaption ? userCaption + '\n' : ''}🔍 No objects detected.`
                  : `${userCaption ? userCaption + '\n' : ''}🔍 Detected ${parsed.count} object(s):\n${summary}`;
            }
          } catch {
            logger.warn({ stdout: yoloResult.stdout }, 'YOLO script output is not valid JSON');
          }
        } else {
          logger.error({ stderr: yoloResult.stderr, status: yoloResult.status }, 'YOLO script failed');
          caption = `${userCaption ? userCaption + '\n' : ''}⚠️ Detection failed — sending raw photo.`;
        }

        await deps.sendImage(targetJid, imageToSend, caption);
        logger.info({ targetJid, count: caption }, 'YOLO result sent via WhatsApp');
      } catch (err) {
        logger.error({ err }, 'capture_and_detect: fatal error');
        await deps.sendMessage(targetJid, '📷 Capture/detection failed. Is the camera plugged in?').catch(() => {});
      } finally {
        try { fs.unlinkSync(tmpPhoto);     } catch { /* ignore */ }
        try { fs.unlinkSync(tmpAnnotated); } catch { /* ignore */ }
      }
      break;
    }

    case 'start_monitor': {
      const targetJid = data.chatJid;
      if (!targetJid) {
        logger.warn({ data }, 'start_monitor: missing chatJid');
        break;
      }
      // Non-main groups can only monitor their own chat
      const targetGroup = registeredGroups[targetJid];
      if (!isMain && (!targetGroup || targetGroup.folder !== sourceGroup)) {
        logger.warn({ sourceGroup, targetJid }, 'Unauthorized start_monitor blocked');
        break;
      }

      const monitorConfig = {
        chatJid: targetJid,
        interval: (data as { interval?: number }).interval ?? 10,
        detectLabels: (data as { detectLabels?: string[] }).detectLabels ?? ['person'],
        confidenceThreshold: (data as { confidenceThreshold?: number }).confidenceThreshold ?? 0.5,
        sendAnnotated: true,
        groupFolder: sourceGroup,
      };

      fs.writeFileSync(MONITOR_CONFIG, JSON.stringify(monitorConfig, null, 2));

      // Start monitor process if not already running
      if (!monitorProcess || monitorProcess.exitCode !== null) {
        const logFd = fs.openSync(path.join(process.cwd(), 'logs', 'monitor.log'), 'a');
        monitorProcess = spawn('python3', ['-u', MONITOR_SCRIPT], {
          stdio: ['ignore', logFd, logFd],
          detached: true,
        });
        monitorProcess.unref();
        fs.closeSync(logFd);
        logger.info({ pid: monitorProcess.pid, config: monitorConfig }, 'Monitor process started');
      } else {
        logger.info('Monitor config updated (process already running)');
      }

      await deps.sendMessage(targetJid,
        `👁️ 监控已启动\n` +
        `• 间隔: ${monitorConfig.interval}s\n` +
        `• 目标: ${monitorConfig.detectLabels.join(', ')}\n` +
        `• 置信度: ${monitorConfig.confidenceThreshold}\n\n` +
        `说"停止监控"即可关闭。`
      );
      break;
    }

    case 'stop_monitor': {
      const targetJid = data.chatJid;

      // Write stop signal
      fs.writeFileSync(MONITOR_CONFIG, JSON.stringify({ stop: true }));

      // Kill process if still running
      if (monitorProcess && monitorProcess.exitCode === null) {
        monitorProcess.kill('SIGTERM');
        monitorProcess = null;
        logger.info('Monitor process stopped');
      }

      // Clean up config
      try { fs.unlinkSync(MONITOR_CONFIG); } catch { /* ignore */ }

      if (targetJid) {
        await deps.sendMessage(targetJid, '👁️ 监控已停止。');
      }
      break;
    }

    case 'send_image': {
      const targetJid = data.chatJid;
      let imagePath = (data as { imagePath?: string }).imagePath;
      const caption = (data as { caption?: string }).caption;
      if (!targetJid || !imagePath) {
        logger.warn({ data }, 'send_image: missing chatJid or imagePath');
        break;
      }
      // Resolve relative paths against the group's IPC directory
      // (container agents save to /workspace/ipc/ which maps to data/ipc/<group>/)
      if (!path.isAbsolute(imagePath)) {
        imagePath = path.join(DATA_DIR, 'ipc', sourceGroup, imagePath);
      }
      if (!fs.existsSync(imagePath)) {
        logger.warn({ imagePath }, 'send_image: image file not found');
        break;
      }

      try {
        await deps.sendImage(targetJid, imagePath, caption ?? '');
        logger.info({ targetJid, imagePath }, 'Monitor image sent');
      } catch (err) {
        logger.error({ err, targetJid }, 'Failed to send monitor image');
      } finally {
        try { fs.unlinkSync(imagePath); } catch { /* ignore */ }
      }
      break;
    }

    default:
      logger.warn({ type: data.type }, 'Unknown IPC task type');
  }
}
