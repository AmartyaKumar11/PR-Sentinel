import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getReviewDetail } from '../api/client';
import { AgentTrace } from '../components/AgentTrace';
import { BlastRadiusGraph } from '../components/BlastRadiusGraph';
import { ComposerPromptView } from '../components/ComposerPromptView';
import { IntentReport } from '../components/IntentReport';
import { RequirementHeatmap } from '../components/RequirementHeatmap';
import { RiskBadge } from '../components/RiskBadge';
import { TaskLifecycle } from '../components/TaskLifecycle';

export function ReviewDetail() {
  const { taskId } = useParams();
  const [task, setTask] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getReviewDetail(taskId)
      .then(setTask)
      .catch((e) => setError(e.message));
  }, [taskId]);

  if (error) return <div className="text-red-600">{error}</div>;
  if (!task) return <div className="text-gray-400">Loading…</div>;

  const diagnosis = task.diagnosis_json || {};
  const intent = diagnosis.intent_alignment || {};
  const blast =
    (task.blast_radius_json &&
      ((task.blast_radius_json.directly_changed || []).length > 0 ||
        (task.blast_radius_json.depth_1_impacted || []).length > 0))
      ? task.blast_radius_json
      : diagnosis.blast_radius || {};
  const initialSteps = (task.trace || []).map((t) => ({
    step: t.step_number,
    phase: t.phase,
    type: t.type,
    content: t.content,
    tool: t.tool_name,
    args: t.tool_args,
    elapsed_ms: t.elapsed_ms,
  }));

  return (
    <div className="space-y-4">
      <Link to="/" className="text-sm text-blue-600 hover:underline">
        ← Dashboard
      </Link>

      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="text-xl font-bold">PR #{task.pr_number}</h1>
        <RiskBadge severity={task.severity} />
        <span className="text-sm text-gray-500">{task.repo}</span>
        <span className="text-xs px-2 py-1 rounded bg-gray-100">{task.status}</span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-3 space-y-4">
          <section className="border rounded-lg bg-white p-4">
            <h2 className="text-sm font-semibold mb-3">Agent trace</h2>
            <AgentTrace taskId={taskId} initialSteps={initialSteps} />
          </section>
          <ComposerPromptView prompt={task.composer_prompt} />
        </div>
        <div className="lg:col-span-2 space-y-4">
          <IntentReport intent={intent} />
          <RequirementHeatmap scores={task.requirement_scores} />
          <div className="border rounded-lg bg-white p-3">
            <h3 className="text-sm font-semibold mb-2">Blast radius</h3>
            <BlastRadiusGraph blastRadius={blast} />
          </div>
          <TaskLifecycle status={task.status} />
        </div>
      </div>

      {task.github_comment_url && (
        <a
          href={task.github_comment_url}
          target="_blank"
          rel="noreferrer"
          className="text-sm text-blue-600 hover:underline"
        >
          View GitHub comment →
        </a>
      )}
    </div>
  );
}
