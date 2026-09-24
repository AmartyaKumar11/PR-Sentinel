import * as vscode from 'vscode';
import { SentinelClient, Task } from '../api/sentinelClient';
import { getBackendUrl, getPollIntervalMs } from '../utils/config';
import { getRepoIdentifier } from '../utils/git';
import { notifyNewTask } from './taskNotifier';

export class TaskPoller implements vscode.Disposable {
  private timer: NodeJS.Timeout | undefined;
  private seen = new Set<string>();
  private client: SentinelClient;
  private activeTask: Task | undefined;

  constructor(private context: vscode.ExtensionContext) {
    this.client = new SentinelClient(getBackendUrl());
  }

  start(): void {
    void this.pollNow();
    this.timer = setInterval(() => void this.pollNow(), getPollIntervalMs());
  }

  async pollNow(): Promise<void> {
    const repo = getRepoIdentifier();
    if (!repo) {
      vscode.window.setStatusBarMessage('PR Sentinel: no git remote', 3000);
      return;
    }
    this.client = new SentinelClient(getBackendUrl());
    const tasks = await this.client.getTasks(repo, ['dispatched', 'pending']);
    for (const task of tasks) {
      if (this.seen.has(task.id)) continue;
      this.seen.add(task.id);
      if (task.status === 'dispatched') {
        await notifyNewTask(task, this.context, (t) => {
          this.activeTask = t;
        });
      }
    }
    if (!tasks.length) {
      vscode.window.setStatusBarMessage(`PR Sentinel: polled ${repo} (0 tasks)`, 2000);
    }
  }

  getActiveTask(): Task | undefined {
    return this.activeTask;
  }

  dispose(): void {
    if (this.timer) clearInterval(this.timer);
  }
}
