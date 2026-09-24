import * as vscode from 'vscode';

export function getBackendUrl(): string {
  return (
    vscode.workspace.getConfiguration('prSentinel').get<string>('backendUrl') ||
    'http://127.0.0.1:8000'
  );
}

export function getPollIntervalMs(): number {
  const secs =
    vscode.workspace.getConfiguration('prSentinel').get<number>('pollIntervalSeconds') || 30;
  return secs * 1000;
}
