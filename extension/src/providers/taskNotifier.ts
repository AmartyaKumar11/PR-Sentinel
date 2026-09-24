import * as vscode from 'vscode';
import { SentinelClient, Task } from '../api/sentinelClient';
import { getBackendUrl } from '../utils/config';
import { openTaskContext } from './fileNavigator';
import { StatusReporter } from './statusReporter';

export async function notifyNewTask(
  task: Task,
  context: vscode.ExtensionContext,
  onAccepted?: (task: Task) => void,
): Promise<void> {
  const pick = await vscode.window.showInformationMessage(
    `PR Sentinel: ${task.severity} on PR #${task.pr_number} — ${task.suggested_fix || 'Review needed'}`,
    'Accept',
    'Dismiss',
    'Dashboard',
  );

  if (pick === 'Accept') {
    await acceptTask(task, context, onAccepted);
  } else if (pick === 'Dismiss') {
    await dismissTask(task);
  } else if (pick === 'Dashboard') {
    const url = `${getBackendUrl().replace(/\/$/, '')}/`;
    await vscode.env.openExternal(vscode.Uri.parse(`http://127.0.0.1:5173/review/${task.id}`));
    void url;
  }
}

export async function acceptTask(
  task: Task,
  context: vscode.ExtensionContext,
  onAccepted?: (task: Task) => void,
): Promise<void> {
  const client = new SentinelClient(getBackendUrl());
  await client.updateStatus(task.id, 'accepted');
  await openTaskContext(task);
  StatusReporter.watch(task, client);
  onAccepted?.(task);
  context.workspaceState.update('prSentinel.activeTask', task.id);
}

export async function dismissTask(task: Task): Promise<void> {
  const client = new SentinelClient(getBackendUrl());
  await client.updateStatus(task.id, 'dismissed');
}
