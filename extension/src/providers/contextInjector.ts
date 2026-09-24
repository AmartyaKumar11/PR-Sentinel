import * as vscode from 'vscode';
import { Task } from '../api/sentinelClient';

export async function injectCursorContext(task: Task): Promise<void> {
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
  if (!workspaceRoot) return;

  const rulesDir = vscode.Uri.joinPath(workspaceRoot, '.cursor', 'rules');
  const ruleFile = vscode.Uri.joinPath(rulesDir, 'sentinel-context.mdc');

  const files = Array.isArray(task.affected_files) ? task.affected_files : [];
  const globs = files.map((f) => f.path).join(', ');
  const content = `---
description: PR Sentinel diagnosis for PR #${task.pr_number}
globs: ${globs}
---

${task.composer_prompt || ''}
`;

  await vscode.workspace.fs.createDirectory(rulesDir);
  await vscode.workspace.fs.writeFile(ruleFile, Buffer.from(content, 'utf-8'));
  await ensureGitignore(workspaceRoot, '.cursor/rules/sentinel-context.mdc');
}

async function ensureGitignore(root: vscode.Uri, pattern: string): Promise<void> {
  const gitignorePath = vscode.Uri.joinPath(root, '.gitignore');
  try {
    const existing = Buffer.from(await vscode.workspace.fs.readFile(gitignorePath)).toString(
      'utf-8',
    );
    if (!existing.includes(pattern)) {
      const updated = existing.trimEnd() + '\n' + pattern + '\n';
      await vscode.workspace.fs.writeFile(gitignorePath, Buffer.from(updated, 'utf-8'));
    }
  } catch {
    await vscode.workspace.fs.writeFile(gitignorePath, Buffer.from(pattern + '\n', 'utf-8'));
  }
}
