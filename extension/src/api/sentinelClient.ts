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
      const data = (await res.json()) as { tasks?: Task[] };
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
          editor: vscode.env.appName.toLowerCase().includes('cursor') ? 'cursor' : 'vscode',
        }),
      });
    } catch {
      /* silent */
    }
  }
}
