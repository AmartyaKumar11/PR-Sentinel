import { useState, useEffect } from 'react';

export function useSSE(taskId) {
  const [steps, setSteps] = useState([]);
  const [status, setStatus] = useState('idle');

  useEffect(() => {
    if (!taskId) return;

    setStatus('connecting');
    setSteps([]);
    const source = new EventSource(`/api/stream/${taskId}`);

    source.onopen = () => setStatus('running');

    source.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setStatus('running');
      setSteps((prev) => [...prev, data]);

      if (data.type === 'answer' && data.phase === 'dispatch') {
        setStatus('complete');
        source.close();
      }
      if (data.type === 'error') {
        setStatus('error');
        source.close();
      }
    };

    source.onerror = () => {
      setStatus('error');
      source.close();
    };

    return () => source.close();
  }, [taskId]);

  return { steps, status };
}
