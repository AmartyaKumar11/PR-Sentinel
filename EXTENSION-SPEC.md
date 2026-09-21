# PR Sentinel — Extension Specification

> **Target:** VS Code ≥1.85, Cursor (VS Code fork)  
> **Language:** TypeScript  
> **Distribution:** VSIX sideload (primary), Open VSX (optional)

---

## The Extension Does 5 Things

1. **Poll** for tasks from the backend every 30s
2. **Notify** the developer when a new task arrives
3. **Open** affected files at the correct lines on Accept
4. **Inject** `.cursor/rules/sentinel-context.mdc` with the Composer prompt
5. **Report** status back when the developer saves affected files

It does NOT write code, run tests, duplicate the dashboard, or maintain state beyond the session.

---

## File Structure

```
extension/
├── src/
│   ├── extension.ts              # activate(), deactivate(), command registration
│   ├── api/
│   │   └── sentinelClient.ts     # HTTP client → backend /api/tasks
│   ├── providers/
│   │   ├── taskPoller.ts         # Poll every 30s, detect new tasks
│   │   ├── taskNotifier.ts       # VS Code notification with Accept/Dismiss/Dashboard
│   │   ├── fileNavigator.ts      # Open files, navigate to lines
│   │   ├── contextInjector.ts    # Write .cursor/rules/sentinel-context.mdc
│   │   ├── blastRadiusPanel.ts   # Webview sidebar (P1)
│   │   └── statusReporter.ts     # Watch file saves → PATCH status
│   ├── views/
│   │   └── blastRadius.html      # Webview template (P1)
│   └── utils/
│       ├── git.ts                # Read repo owner/name from git remote
│       └── config.ts             # Extension settings reader
├── package.json                  # Extension manifest
├── tsconfig.json
├── webpack.config.js
└── .vsixignore
```

---

## package.json (Extension Manifest)

```json
{
  "name": "pr-sentinel",
  "displayName": "PR Sentinel",
  "description": "AI-powered PR remediation — diagnostics delivered to your editor",
  "version": "0.1.0",
  "publisher": "amartya-kumar",
  "engines": { "vscode": "^1.85.0" },
  "categories": ["Other"],
  "activationEvents": ["onStartupFinished"],
  "main": "./dist/extension.js",
  "contributes": {
    "commands": [
      { "command": "prSentinel.showTasks", "title": "PR Sentinel: Show Tasks" },
      { "command": "prSentinel.acceptTask", "title": "PR Sentinel: Accept Task" },
      { "command": "prSentinel.dismissTask", "title": "PR Sentinel: Dismiss Task" },
      { "command": "prSentinel.showBlastRadius", "title": "PR Sentinel: Blast Radius" }
    ],
    "viewsContainers": {
      "activitybar": [{
        "id": "pr-sentinel",
        "title": "PR Sentinel",
        "icon": "$(shield)"
      }]
    },
    "views": {
      "pr-sentinel": [{
        "type": "webview",
        "id": "prSentinel.blastRadius",
        "name": "Blast Radius"
      }]
    },
    "configuration": {
      "title": "PR Sentinel",
      "properties": {
        "prSentinel.backendUrl": {
          "type": "string",
          "default": "https://pr-sentinel-backend.up.railway.app",
          "description": "Backend API URL"
        },
        "prSentinel.pollIntervalSeconds": {
          "type": "number",
          "default": 30,
          "description": "Task polling interval"
        }
      }
    }
  }
}
```

---

## Implementation Files

### extension.ts

```typescript
import * as vscode from 'vscode';
import { TaskPoller } from './providers/taskPoller';
import { acceptTask, dismissTask } from './providers/taskNotifier';
import { BlastRadiusPanel } from './providers/blastRadiusPanel';

let poller: TaskPoller;

export function activate(context: vscode.ExtensionContext) {
    poller = new TaskPoller(context);
    poller.start();

    context.subscriptions.push(
        vscode.commands.registerCommand('prSentinel.showTasks', () => poller.pollNow()),
        vscode.commands.registerCommand('prSentinel.acceptTask', (task) => acceptTask(task, context)),
        vscode.commands.registerCommand('prSentinel.dismissTask', (task) => dismissTask(task)),
        vscode.commands.registerCommand('prSentinel.showBlastRadius', (task) => BlastRadiusPanel.show(context, task)),
        poller
    );

    // Status bar
    const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBar.text = "$(shield) Sentinel";
    statusBar.command = "prSentinel.showTasks";
    statusBar.show();
    context.subscriptions.push(statusBar);
}

export function deactivate() {
    poller?.dispose();
}
```

### sentinelClient.ts

```typescript
import * as vscode from 'vscode';

export interface Task {
    id: string;
    pr_number: number;
    severity: string;
    status: string;
    suggested_fix: string;
    affected_files: Array<{ path: string; lines: number[]; change_type: string }>;
    composer_prompt: string;
    blast_radius_summary: string;
    intent_gaps: string[];
    created_at: string;
}

export class SentinelClient {
    constructor(private baseUrl: string) {}

    async getTasks(repo: string, statuses: string[]): Promise<Task[]> {
        const url = `${this.baseUrl}/api/tasks?repo=${encodeURIComponent(repo)}&status=${statuses.join(',')}`;
        try {
            const res = await fetch(url);
            if (!res.ok) return [];
            const data = await res.json();
            return data.tasks || [];
        } catch {
            return [];
        }
    }

    async updateStatus(taskId: string, status: string): Promise<void> {
        const url = `${this.baseUrl}/api/tasks/${taskId}`;
        try {
            await fetch(url, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    status,
                    extension_version: '0.1.0',
                    editor: vscode.env.appName.toLowerCase().includes('cursor') ? 'cursor' : 'vscode'
                })
            });
        } catch { /* silent fail — backend is best-effort */ }
    }
}
```

### git.ts

```typescript
import * as vscode from 'vscode';
import { execSync } from 'child_process';

export function getRepoIdentifier(): string | null {
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!workspaceRoot) return null;

    try {
        const remote = execSync('git remote get-url origin', { cwd: workspaceRoot })
            .toString().trim();
        // Parse: git@github.com:owner/repo.git or https://github.com/owner/repo.git
        const match = remote.match(/github\.com[:/](.+?)(?:\.git)?$/);
        return match ? match[1] : null;
    } catch {
        return null;
    }
}
```

### contextInjector.ts

```typescript
import * as vscode from 'vscode';
import { Task } from '../api/sentinelClient';

export async function injectCursorContext(task: Task): Promise<void> {
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
    if (!workspaceRoot) return;

    const rulesDir = vscode.Uri.joinPath(workspaceRoot, '.cursor', 'rules');
    const ruleFile = vscode.Uri.joinPath(rulesDir, 'sentinel-context.mdc');

    // Build the .cursor/rules file content
    const globs = task.affected_files.map(f => f.path).join(', ');
    const content = `---
description: PR Sentinel diagnosis for PR #${task.pr_number}
globs: ${globs}
---

${task.composer_prompt}
`;

    // Write file
    await vscode.workspace.fs.createDirectory(rulesDir);
    await vscode.workspace.fs.writeFile(ruleFile, Buffer.from(content, 'utf-8'));

    // Add to .gitignore if not already there
    await ensureGitignore(workspaceRoot, '.cursor/rules/sentinel-context.mdc');
}

async function ensureGitignore(root: vscode.Uri, pattern: string): Promise<void> {
    const gitignorePath = vscode.Uri.joinPath(root, '.gitignore');
    try {
        const existing = Buffer.from(
            await vscode.workspace.fs.readFile(gitignorePath)
        ).toString('utf-8');
        if (!existing.includes(pattern)) {
            const updated = existing.trimEnd() + '\n' + pattern + '\n';
            await vscode.workspace.fs.writeFile(gitignorePath, Buffer.from(updated, 'utf-8'));
        }
    } catch {
        // No .gitignore — create one
        await vscode.workspace.fs.writeFile(
            gitignorePath,
            Buffer.from(pattern + '\n', 'utf-8')
        );
    }
}
```

### fileNavigator.ts

```typescript
import * as vscode from 'vscode';
import { Task } from '../api/sentinelClient';
import { injectCursorContext } from './contextInjector';

export async function openTaskContext(task: Task): Promise<void> {
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
    if (!workspaceRoot) return;

    // 1. Open each affected file at the right line
    for (const file of task.affected_files) {
        const uri = vscode.Uri.joinPath(workspaceRoot, file.path);
        try {
            const doc = await vscode.workspace.openTextDocument(uri);
            const editor = await vscode.window.showTextDocument(doc, {
                preview: false,
                viewColumn: vscode.ViewColumn.One,
            });

            if (file.lines?.length > 0) {
                const line = file.lines[0] - 1; // VS Code is 0-indexed
                const position = new vscode.Position(Math.max(0, line), 0);
                editor.selection = new vscode.Selection(position, position);
                editor.revealRange(
                    new vscode.Range(position, position),
                    vscode.TextEditorRevealType.InCenter
                );
            }
        } catch {
            // File might not exist locally — skip
        }
    }

    // 2. Inject .cursor/rules/ context
    await injectCursorContext(task);

    // 3. Copy Composer prompt to clipboard for easy paste
    await vscode.env.clipboard.writeText(task.composer_prompt);
    vscode.window.showInformationMessage(
        'PR Sentinel: Context injected. Composer prompt copied to clipboard.'
    );
}
```

---

## Build & Package

```bash
cd extension
npm run build          # webpack → dist/extension.js
npx @vscode/vsce package  # → pr-sentinel-0.1.0.vsix
```

### Install in Cursor

```bash
cursor --install-extension pr-sentinel-0.1.0.vsix
# Or drag the .vsix into Extensions panel
```

### Cursor vs VS Code Differences

| Feature | Cursor | VS Code |
|---|---|---|
| `.cursor/rules/` injection | Works — Cursor reads these as AI context | Ignored — falls back to sidebar webview only |
| Extension API | Identical (Cursor is a VS Code fork) | Native |
| Open VSX | Cursor reads Open VSX | VS Code reads VS Code Marketplace |
| Background Agent integration | Available (Pro subscription) | Not available |
