import { useState } from 'react';

export function ComposerPromptView({ prompt }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    await navigator.clipboard.writeText(prompt || '');
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="border rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 bg-gray-50 border-b">
        <span className="text-sm font-medium">Cursor Composer prompt</span>
        <button
          onClick={copy}
          className="text-xs px-2 py-1 rounded border hover:bg-gray-100"
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>
      <pre className="p-3 text-xs overflow-x-auto whitespace-pre-wrap font-mono bg-white max-h-64">
        {prompt || '(empty)'}
      </pre>
    </div>
  );
}
