import * as vscode from 'vscode';
import { execSync } from 'child_process';

export function getRepoIdentifier(): string | null {
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!workspaceRoot) return null;

  try {
    const remote = execSync('git remote get-url origin', { cwd: workspaceRoot })
      .toString()
      .trim();
    const match = remote.match(/github\.com[:/](.+?)(?:\.git)?$/);
    return match ? match[1] : null;
  } catch {
    return null;
  }
}
