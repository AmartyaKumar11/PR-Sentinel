import * as vscode from 'vscode';
import { SentinelClient, Task } from '../api/sentinelClient';

export class StatusReporter {
  private static disposable: vscode.Disposable | undefined;

  static watch(task: Task, client: SentinelClient): void {
    this.disposable?.dispose();
    const paths = new Set(
      (Array.isArray(task.affected_files) ? task.affected_files : []).map((f) =>
        f.path.replace(/\\/g, '/'),
      ),
    );
    let reported = false;

    this.disposable = vscode.workspace.onDidSaveTextDocument(async (doc) => {
      if (reported) return;
      const rel = vscode.workspace.asRelativePath(doc.uri).replace(/\\/g, '/');
      if (!paths.has(rel) && ![...paths].some((p) => rel.endsWith(p))) return;
      reported = true;
      await client.updateStatus(task.id, 'in_progress');
      vscode.window.setStatusBarMessage('PR Sentinel: marked in_progress', 3000);
    });
  }

  static dispose(): void {
    this.disposable?.dispose();
    this.disposable = undefined;
  }
}
