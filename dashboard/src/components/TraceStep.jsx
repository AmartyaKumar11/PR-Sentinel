import { useState } from 'react';

const STEP_CONFIG = {
  thought: { icon: '💭', label: 'Thought', bg: 'bg-gray-50', border: 'border-gray-200' },
  action: { icon: '🔧', label: 'Action', bg: 'bg-amber-50', border: 'border-amber-200' },
  observation: { icon: '👁', label: 'Observation', bg: 'bg-teal-50', border: 'border-teal-200' },
  answer: { icon: '✅', label: 'Answer', bg: 'bg-purple-50', border: 'border-purple-200' },
  error: { icon: '❌', label: 'Error', bg: 'bg-red-50', border: 'border-red-200' },
};

export function TraceStep({ step }) {
  const [expanded, setExpanded] = useState(step.type !== 'observation');
  const config = STEP_CONFIG[step.type] || STEP_CONFIG.thought;

  const content =
    step.type === 'action'
      ? `${step.tool || step.tool_name || ''}(${JSON.stringify(step.args || step.tool_args || {})})`
      : step.content;

  const isLong = (content || '').length > 300;

  let answerBody = content;
  if (step.type === 'answer') {
    try {
      answerBody = JSON.stringify(JSON.parse(content), null, 2);
    } catch {
      answerBody = content;
    }
  }

  return (
    <div className={`rounded-lg border ${config.border} ${config.bg} p-3`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-medium">
          <span>{config.icon}</span>
          <span>{config.label}</span>
          <span className="text-gray-400">Step {step.step ?? step.step_number}</span>
          <span className="text-xs px-2 py-0.5 rounded-full bg-gray-200 text-gray-600">
            {step.phase}
          </span>
        </div>
        {step.elapsed_ms != null && (
          <span className="text-xs text-gray-400">{step.elapsed_ms}ms</span>
        )}
      </div>
      <div
        className={`mt-2 text-sm whitespace-pre-wrap ${
          !expanded && isLong ? 'max-h-20 overflow-hidden' : ''
        }`}
      >
        {step.type === 'answer' ? (
          <pre className="text-xs bg-white/50 p-2 rounded overflow-x-auto">{answerBody}</pre>
        ) : (
          content
        )}
      </div>
      {isLong && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-1 text-xs text-blue-600 hover:underline"
        >
          {expanded ? 'Collapse' : 'Expand'}
        </button>
      )}
    </div>
  );
}
