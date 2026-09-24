import { useEffect, useRef } from 'react';
import { useSSE } from '../hooks/useSSE';
import { TraceStep } from './TraceStep';

function StatusDot({ status }) {
  const color =
    status === 'running'
      ? 'bg-blue-500 animate-pulse'
      : status === 'complete'
        ? 'bg-green-500'
        : status === 'error'
          ? 'bg-red-500'
          : 'bg-gray-300';
  return <span className={`inline-block w-2 h-2 rounded-full ${color}`} />;
}

export function AgentTrace({ taskId, initialSteps = [] }) {
  const { steps: liveSteps, status } = useSSE(taskId);
  const steps = liveSteps.length ? liveSteps : initialSteps;
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [steps]);

  return (
    <div className="space-y-3 max-h-[480px] overflow-y-auto">
      <div className="flex items-center gap-2 mb-4 sticky top-0 bg-white py-1">
        <StatusDot status={liveSteps.length ? status : steps.length ? 'complete' : 'idle'} />
        <span className="text-sm text-gray-500">
          {status === 'running'
            ? 'Agent is reasoning...'
            : status === 'complete' || (!liveSteps.length && steps.length)
              ? 'Analysis complete'
              : status === 'error'
                ? 'Error occurred'
                : 'Waiting...'}
        </span>
      </div>

      {steps.map((step, i) => (
        <TraceStep key={i} step={step} />
      ))}

      {status === 'running' && (
        <div className="animate-pulse text-sm text-gray-400">Thinking...</div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
