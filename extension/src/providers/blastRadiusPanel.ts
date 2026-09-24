import * as vscode from 'vscode';
import { Task } from '../api/sentinelClient';

export class BlastRadiusPanel implements vscode.WebviewViewProvider {
  public static readonly viewType = 'prSentinel.blastRadius';
  private view?: vscode.WebviewView;
  private task?: Task;

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = { enableScripts: true };
    this.render();
  }

  setTask(task: Task): void {
    this.task = task;
    this.render();
  }

  static show(_context: vscode.ExtensionContext, task?: Task): void {
    void vscode.commands.executeCommand('prSentinel.blastRadius.focus');
    if (task) {
      vscode.window.showInformationMessage(
        `Blast radius: ${task.blast_radius_summary || 'n/a'}\nGaps: ${(task.intent_gaps || []).join(', ') || 'none'}`,
      );
    }
  }

  private render(): void {
    if (!this.view) return;
    const t = this.task;
    const gaps = (t?.intent_gaps || []).map((g) => `<li>${escapeHtml(g)}</li>`).join('');
    this.view.webview.html = `<!DOCTYPE html>
<html><body style="font-family:sans-serif;padding:12px;color:var(--vscode-foreground)">
  <h3>PR Sentinel</h3>
  ${
    t
      ? `<p><b>PR #${t.pr_number}</b> · ${escapeHtml(t.severity)}</p>
         <p>${escapeHtml(t.blast_radius_summary || '')}</p>
         <p><b>Intent gaps</b></p><ul>${gaps || '<li>None</li>'}</ul>
         <pre style="white-space:pre-wrap;font-size:11px">${escapeHtml(t.suggested_fix || '')}</pre>`
      : '<p>No active task</p>'
  }
</body></html>`;
  }
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
