import * as vscode from 'vscode';
import { Task } from '../api/sentinelClient';
import { injectCursorContext } from './contextInjector';

export async function openTaskContext(task: Task): Promise<void> {
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
  if (!workspaceRoot) return;

  const files = Array.isArray(task.affected_files) ? task.affected_files : [];
  for (const file of files) {
    const uri = vscode.Uri.joinPath(workspaceRoot, file.path);
    try {
      const doc = await vscode.workspace.openTextDocument(uri);
      const editor = await vscode.window.showTextDocument(doc, {
        preview: false,
        viewColumn: vscode.ViewColumn.One,
      });

      if (file.lines?.length > 0) {
        const line = file.lines[0] - 1;
        const position = new vscode.Position(Math.max(0, line), 0);
        editor.selection = new vscode.Selection(position, position);
        editor.revealRange(
          new vscode.Range(position, position),
          vscode.TextEditorRevealType.InCenter,
        );
      }
    } catch {
      /* skip missing local files */
    }
  }

  await injectCursorContext(task);

  if (task.composer_prompt) {
    await vscode.env.clipboard.writeText(task.composer_prompt);
  }
  vscode.window.showInformationMessage(
    'PR Sentinel: Context injected. Composer prompt copied to clipboard.',
  );
}
