import * as vscode from 'vscode';
import { TaskPoller } from './providers/taskPoller';
import { acceptTask, dismissTask } from './providers/taskNotifier';
import { BlastRadiusPanel } from './providers/blastRadiusPanel';
import { StatusReporter } from './providers/statusReporter';
import { Task } from './api/sentinelClient';

let poller: TaskPoller;

export function activate(context: vscode.ExtensionContext) {
  poller = new TaskPoller(context);
  poller.start();

  const panel = new BlastRadiusPanel();
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(BlastRadiusPanel.viewType, panel),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('prSentinel.showTasks', () => poller.pollNow()),
    vscode.commands.registerCommand('prSentinel.acceptTask', (task: Task) =>
      acceptTask(task, context, (t) => panel.setTask(t)),
    ),
    vscode.commands.registerCommand('prSentinel.dismissTask', (task: Task) => dismissTask(task)),
    vscode.commands.registerCommand('prSentinel.showBlastRadius', (task?: Task) =>
      BlastRadiusPanel.show(context, task || poller.getActiveTask()),
    ),
    poller,
  );

  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.text = '$(shield) Sentinel';
  statusBar.command = 'prSentinel.showTasks';
  statusBar.show();
  context.subscriptions.push(statusBar);
}

export function deactivate() {
  StatusReporter.dispose();
  poller?.dispose();
}
